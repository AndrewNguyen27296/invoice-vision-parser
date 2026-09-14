"""
The extraction pipeline: document in, validated and math-audited data out.

THE STRATEGY LADDER
    Each rung is tried in order, and every rung above the last one is free:

        1. cache       - this exact file, at these exact settings, seen before
        2. mock        - canned response; no key, no network, no cost
        3. text layer  - the PDF's own embedded text, parsed deterministically
        4. vision      - a multimodal model, for scans and photos only

    Rungs 1-3 cost nothing. Only a document with no usable text layer reaches a
    paid model, which on real accounts-payable traffic -- mostly digital PDFs
    straight out of a vendor's ERP -- is the minority of documents.

WHAT IS SHARED, WHATEVER THE ROUTE
    Every rung produces the same dict, which goes through the same strict
    Pydantic model and the same deterministic math audit. So the green badge
    means precisely the same thing whether the numbers came from Google, from
    Anthropic, or from reading the file's own bytes. That is the point of the
    architecture: the trust anchor is ours, not the vendor's.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .cost_guard import BudgetExceeded, CostGuard, GuardConfig
from .pdf_utils import RenderedPage, file_fingerprint, page_count, render_document
from .providers import ProviderError, VisionProvider, get_provider
from .providers.mock_provider import MOCK_INVOICE
from .schemas import InvoiceData, MathAudit
from .textlayer import extract_from_text_layer

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)

#: Kept for backwards compatibility with anything importing it from here.
MOCK_RESPONSE: Dict[str, Any] = MOCK_INVOICE

# How the answer was obtained. Surfaced in the UI, the CLI and the ledger,
# because "this was free" is information the operator wants at a glance.
STRATEGY_CACHE = "cache"
STRATEGY_MOCK = "mock"
STRATEGY_TEXT_LAYER = "text-layer"
STRATEGY_VISION = "vision"

FREE_STRATEGIES = (STRATEGY_CACHE, STRATEGY_MOCK, STRATEGY_TEXT_LAYER)


class ExtractionError(RuntimeError):
    """The model replied, but not with usable JSON."""


@dataclass
class ExtractionResult:
    """Everything the UI and the CLI need, in one object."""

    invoice: InvoiceData
    audit: MathAudit
    source_path: Path
    pages_processed: int
    latency_seconds: float
    model: str
    strategy: str = STRATEGY_VISION
    provider: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    from_cache: bool = False
    mocked: bool = False
    #: True when the answer came from a canned fixture or a mock rather than a
    #: model. The UI must say so -- a fixture presented as a live extraction is
    #: the one thing a client demo must never do.
    canned: bool = False
    text_layer_coverage: Optional[float] = None
    page_images: List[RenderedPage] = field(default_factory=list)

    @property
    def within_latency_budget(self) -> bool:
        """Architectural invariant #4: under 5 seconds per page."""
        return self.latency_seconds <= 5.0 * max(1, self.pages_processed)

    @property
    def was_free(self) -> bool:
        return self.cost_usd == 0.0

    @property
    def route(self) -> str:
        """Human-readable one-liner: how this answer was obtained, and at what cost."""
        label = {
            STRATEGY_CACHE: "cache hit (no API call)",
            STRATEGY_MOCK: "mock response (no API call)",
            STRATEGY_TEXT_LAYER: "PDF text layer (no API call)",
            STRATEGY_VISION: (
                f"canned {self.provider} response (no model call)" if self.canned
                else f"vision model via {self.provider}"
            ),
        }.get(self.strategy, self.strategy)
        return f"{label} - ${self.cost_usd:.5f}"


def _strip_fences(text: str) -> str:
    return _FENCE.sub("", text).strip()


def _parse_json(text: str) -> Dict[str, Any]:
    """Recover a JSON object from a model reply, tolerating stray prose."""
    cleaned = _strip_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ExtractionError(f"Model reply was not valid JSON: {exc}") from exc
    raise ExtractionError("Model reply contained no JSON object.")


def extract_invoice(
    file_path: Path | str,
    guard: Optional[CostGuard] = None,
    mock: bool = False,
    force_refresh: bool = False,
    provider: Optional[VisionProvider] = None,
    use_text_layer: Optional[bool] = None,
) -> ExtractionResult:
    """Parse one document into validated, math-audited invoice data.

    Raises:
        BudgetExceeded:  a guardrail was hit (before any spend).
        ExtractionError: the model replied with unusable content.
        ProviderError:   the vision backend is unreachable or misconfigured.
        FileNotFoundError / ValueError: bad input file.
    """
    file_path = Path(file_path)
    guard = guard or CostGuard()
    cfg = guard.config
    started = time.perf_counter()

    if use_text_layer is None:
        use_text_layer = cfg.use_text_layer

    # Cheapest refusal first, before the file is hashed or the PDF is parsed.
    guard.check_upload_size(file_path)

    fingerprint = file_fingerprint(file_path)
    cache_key = f"{fingerprint}{'-mock' if mock else ''}"
    cached_payload = None if force_refresh else guard.cache_get(cache_key)
    total_pages = page_count(file_path)

    notes: List[str] = []
    data: Optional[Dict[str, Any]] = None
    strategy = STRATEGY_VISION
    provider_name = ""
    coverage: Optional[float] = None
    input_tokens = output_tokens = 0
    cost = 0.0
    vision_model: str = ""
    canned = False

    # ---------------------------------------------------------------- rung 1+2
    if cached_payload is not None:
        data, strategy = cached_payload["data"], STRATEGY_CACHE
        notes.append("served from local cache - no API call, no cost")
    elif mock:
        data, strategy = MOCK_INVOICE, STRATEGY_MOCK
        notes.append("MOCK MODE - canned response, no API call")

    # ------------------------------------------------------------------ rung 3
    if data is None and use_text_layer:
        try:
            parsed = extract_from_text_layer(file_path, max_pages=cfg.max_pages_per_doc)
        except Exception as exc:                      # never let a parse bug cost money
            parsed = None
            notes.append(f"text-layer read failed ({exc}); falling back to vision")
        if parsed is not None:
            coverage = parsed.coverage
            if parsed.sufficient:
                data, strategy = parsed.data, STRATEGY_TEXT_LAYER
                notes.extend(parsed.notes)
                notes.append(f"text-layer coverage {parsed.coverage:.0%}")
            else:
                notes.append(
                    f"text layer found only {parsed.coverage:.0%} of the key fields "
                    f"(missing: {', '.join(parsed.missing)}); escalating to vision"
                )
        else:
            notes.append("no usable text layer (scan or photo); using vision")

    # Pages the guard will let us touch. Free routes skip the dollar gate but
    # still obey the page caps.
    pages_to_process = guard.preflight(
        file_path, total_pages,
        cached=data is not None and strategy in FREE_STRATEGIES,
    )
    if total_pages > pages_to_process:
        notes.append(
            f"document has {total_pages} pages; only the first {pages_to_process} "
            f"were processed (PARSER_MAX_PAGES_PER_DOC)"
        )

    # Render regardless of route: the UI needs the page preview image.
    pages = render_document(
        file_path, max_pages=pages_to_process, max_edge=cfg.max_image_edge
    )

    # ------------------------------------------------------------------ rung 4
    if data is None:
        backend = provider or get_provider(cfg.provider)
        provider_name = backend.name
        guard.preflight(file_path, total_pages, free=backend.free)

        estimated = cfg.estimate_page_cost(sum(p.estimated_image_tokens for p in pages))
        reply = backend.call(pages, cfg)
        input_tokens, output_tokens = reply.input_tokens, reply.output_tokens

        # Ledger FIRST, parse second. Those tokens are billed whether or not the
        # reply turns out to be usable; recording only on the success path meant
        # a run of malformed replies was real money the daily cap never saw.
        cost = guard.record(pages_to_process, input_tokens, output_tokens,
                            free=backend.free)
        data = _parse_json(reply.raw_text)
        strategy = STRATEGY_VISION
        vision_model = reply.model or cfg.model
        canned = backend.name in ("demo", "mock")
        notes.append(
            f"{backend.describe()}"
            if backend.free else
            f"estimated ${estimated:.4f}, actual ${cost:.4f}"
        )
        guard.cache_put(cache_key, {"data": data, "model": reply.model or cfg.model})
    elif strategy in (STRATEGY_TEXT_LAYER, STRATEGY_MOCK):
        # Count the volume even though it was free, so the operator can see how
        # much of the day's traffic the free routes absorbed.
        guard.record(pages_to_process, 0, 0, free=True)

    invoice = InvoiceData.model_validate(data)
    invoice.extraction_notes.extend(notes)
    audit = invoice.run_audit()

    model_label = {
        STRATEGY_MOCK: "mock",
        STRATEGY_TEXT_LAYER: "text-layer (deterministic)",
        STRATEGY_CACHE: cached_payload.get("model", cfg.model) if cached_payload else cfg.model,
    }.get(strategy, vision_model or cfg.model)

    return ExtractionResult(
        invoice=invoice,
        audit=audit,
        source_path=file_path,
        pages_processed=pages_to_process,
        latency_seconds=time.perf_counter() - started,
        model=model_label,
        strategy=strategy,
        provider=provider_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
        from_cache=strategy == STRATEGY_CACHE,
        mocked=mock,
        canned=canned or strategy == STRATEGY_MOCK,
        text_layer_coverage=coverage,
        page_images=pages,
    )
