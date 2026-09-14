"""
The client-demo provider: correct canned answers for the bundled samples.

WHY THIS EXISTS -- and it is a demo-integrity fix, not a convenience

    `3_skewed_scan.pdf` is a rasterised phone scan with no text layer, so it is
    the one bundled sample that genuinely needs a vision model. With no API key
    configured and the `mock` provider selected, it used to come back as
    *Nordfrakt Logistik AB, SEK 1,937.50* -- the wrong vendor, the wrong
    currency, the wrong total -- under a confident green "Math Audit Passed"
    badge, because the mock reply is internally consistent.

    A prospect clicking "Sample 3" on the public demo would have been shown
    fabricated data presented as a successful extraction. That is the single
    worst thing a demo can do, and no amount of UI polish compensates for it.

    So this provider returns the CORRECT answer for each document it actually
    knows, keyed by the SHA-256 of the file's bytes, and REFUSES anything it
    does not know rather than inventing something. It cannot be wrong; it can
    only decline.

HONESTY CONTRACT
    These are fixtures, not extractions. `free` is True and `describe()` says
    "canned", so the UI and the CLI both label the route as a canned response
    with no model call. Never present this route as a live extraction.

WHEN A REAL KEY IS PRESENT
    Point `PARSER_PROVIDER` at `gemini` or `anthropic` and the same samples run
    through a real vision model. The bundled samples are fictional documents, so
    a free tier is appropriate for them -- which is not true of a prospect's own
    uploads (see the upload guard in app.py).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from ..pdf_utils import file_fingerprint
from .base import ProviderError, ProviderReply

FIXTURES_PATH = Path(__file__).resolve().parent.parent.parent / "sample_invoices" / "demo_fixtures.json"


class DemoProvider:
    """Fixture-backed. Correct for the bundled samples, silent on everything else."""

    name = "demo"
    free = True
    trains_on_data = False      # nothing leaves the process

    def __init__(self, fixtures_path: Optional[Path] = None) -> None:
        self.fixtures_path = Path(fixtures_path or FIXTURES_PATH)
        self._fixtures: Optional[Dict[str, Dict[str, Any]]] = None

    # ------------------------------------------------------------------ load
    @property
    def fixtures(self) -> Dict[str, Dict[str, Any]]:
        if self._fixtures is None:
            try:
                payload = json.loads(self.fixtures_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ProviderError(
                    f"Demo fixtures could not be read from {self.fixtures_path} ({exc}). "
                    "Regenerate them or choose a real provider with PARSER_PROVIDER."
                ) from exc
            self._fixtures = dict(payload.get("fixtures") or {})
        return self._fixtures

    def describe(self) -> str:
        try:
            count = len(self.fixtures)
        except ProviderError:
            count = 0
        return (f"Demo fixtures ({count} bundled samples) - canned answers, no model "
                "call, no key, no cost. Declines anything it does not know.")

    def knows(self, path: Path | str) -> bool:
        try:
            return file_fingerprint(Path(path)) in self.fixtures
        except (OSError, ProviderError):
            return False

    # ------------------------------------------------------------------ call
    def call(self, pages, cfg) -> ProviderReply:
        """Return the curated answer, or refuse. Never guess.

        The pages carry the source path so the fixture can be looked up by
        content hash -- which means renaming a sample file does not break it,
        and a document that merely *looks* similar is never matched.
        """
        source = getattr(pages[0], "source_path", None) if pages else None
        if source is None:
            raise ProviderError(
                "The demo provider needs to know which file it is looking at, and "
                "this page did not carry its source path. Choose a real provider."
            )

        fingerprint = file_fingerprint(Path(source))
        record = self.fixtures.get(fingerprint)
        if record is None:
            raise ProviderError(
                f"'{Path(source).name}' has no text layer, so it needs a vision "
                "model — and the demo provider only holds canned answers for the "
                "bundled sample invoices. It will not invent an answer for a "
                "document it has never seen.\n\n"
                "To extract this document, set PARSER_PROVIDER to 'gemini' (free "
                "tier — https://aistudio.google.com/apikey) or 'anthropic', and "
                "add the key to your .env or Streamlit secrets."
            )

        payload = {k: v for k, v in record.items() if not k.startswith("_")}
        return ProviderReply(raw_text=json.dumps(payload), model="demo-fixture")
