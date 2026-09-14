"""
The anti-bill-shock layer.

A public Streamlit demo is an open door to YOUR API key. Someone can drop a
500-page PDF into it, or loop uploads overnight. Every one of those pages is
money out of your pocket. This module makes that structurally impossible:

    1. PRE-FLIGHT   - page count, file size and budget are checked BEFORE any
                      network call. Refusals cost nothing.
    2. HARD CAPS    - per document, per session, per rolling day.
    3. CONTENT CACHE- the same file uploaded twice is answered from disk. A demo
                      visitor clicking "Sample Invoice #2" ten times bills once,
                      ever, for the lifetime of the cache.
    4. LEDGER       - every call's real token usage is priced and appended to a
                      JSON ledger so you always know the day's spend.

Defaults are deliberately paranoid. Loosen them per paying client, not for the
public demo.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

from .prompts import PROMPT_VERSION

# The ledger must not depend on where you happened to run the process from.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------
# Pricing table (USD per million tokens), verified Sept 2026.
# Haiku 4.5 is the cheapest vision-capable model and the correct default here.
# --------------------------------------------------------------------------
PRICING: Dict[str, Dict[str, float]] = {
    "claude-haiku-4-5":  {"input": 1.00, "output": 5.00},
    "claude-sonnet-5":   {"input": 2.00, "output": 10.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-opus-4-5":   {"input": 5.00, "output": 25.00},
    # Gemini free tier bills nothing. If you move to a paid Gemini key, set the
    # real rates with PARSER_PRICE_IN / PARSER_PRICE_OUT rather than editing this.
    "gemini-flash-latest": {"input": 0.00, "output": 0.00},
}
DEFAULT_PRICING = {"input": 1.00, "output": 5.00}


class BudgetExceeded(RuntimeError):
    """Raised before any API call when a guardrail would be breached."""


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class GuardConfig:
    """All cost limits in one place. Every field is overridable by env var."""

    model: str = field(default_factory=lambda: os.getenv("PARSER_MODEL", "claude-haiku-4-5"))
    max_image_edge: int = field(default_factory=lambda: _env_int("PARSER_MAX_IMAGE_EDGE", 1120))
    max_output_tokens: int = field(default_factory=lambda: _env_int("PARSER_MAX_OUTPUT_TOKENS", 1600))
    max_pages_per_doc: int = field(default_factory=lambda: _env_int("PARSER_MAX_PAGES_PER_DOC", 3))
    max_upload_mb: float = field(default_factory=lambda: _env_float("PARSER_MAX_UPLOAD_MB", 15.0))
    daily_usd_budget: float = field(default_factory=lambda: _env_float("PARSER_DAILY_USD_BUDGET", 2.0))
    daily_page_limit: int = field(default_factory=lambda: _env_int("PARSER_DAILY_PAGE_LIMIT", 300))
    session_page_limit: int = field(default_factory=lambda: _env_int("PARSER_SESSION_PAGE_LIMIT", 25))
    cache_enabled: bool = field(default_factory=lambda: os.getenv("PARSER_CACHE_ENABLED", "1") != "0")
    # Anchored to the project, NOT the working directory. A relative ".cache"
    # meant `cd /tmp && python cli.py ...` got a brand-new ledger and the full
    # daily budget again.
    cache_dir: Path = field(default_factory=lambda: Path(
        os.getenv("PARSER_CACHE_DIR") or (_PROJECT_ROOT / ".cache")))
    api_timeout_s: float = field(default_factory=lambda: _env_float("PARSER_API_TIMEOUT_S", 40.0))
    api_max_retries: int = field(default_factory=lambda: _env_int("PARSER_API_MAX_RETRIES", 1))

    # Which vision backend to fall back to, and whether to try the free text
    # layer before spending anything at all. See parser/textlayer.py.
    provider: str = field(default_factory=lambda: os.getenv("PARSER_PROVIDER", "anthropic"))
    use_text_layer: bool = field(
        default_factory=lambda: os.getenv("PARSER_USE_TEXT_LAYER", "1") != "0")

    # Override the pricing table without a code change, for a model whose rates
    # are not listed above (a new release, or a paid Gemini tier).
    price_in: Optional[float] = field(
        default_factory=lambda: _env_float("PARSER_PRICE_IN", -1.0) or None)
    price_out: Optional[float] = field(
        default_factory=lambda: _env_float("PARSER_PRICE_OUT", -1.0) or None)

    def price(self, input_tokens: int, output_tokens: int) -> float:
        rates = PRICING.get(self.model, DEFAULT_PRICING)
        rate_in = self.price_in if (self.price_in or -1) >= 0 else rates["input"]
        rate_out = self.price_out if (self.price_out or -1) >= 0 else rates["output"]
        return (input_tokens / 1e6) * rate_in + (output_tokens / 1e6) * rate_out

    def estimate_page_cost(self, image_tokens: int, prompt_tokens: int = 700) -> float:
        """Worst-case cost for a call carrying `image_tokens`, at full output budget."""
        return self.price(image_tokens + prompt_tokens, self.max_output_tokens)

    def worst_case_doc_cost(self, pages: int) -> float:
        """Upper bound for a document, computed WITHOUT rendering it.

        Used by the pre-flight budget gate, which has to know what a document
        might cost before it is allowed to cost anything. Assumes every page
        fills the max_image_edge square and the whole output budget is spent.
        """
        per_page_image_tokens = int((self.max_image_edge ** 2) / 750)
        return max(0, pages) * self.estimate_page_cost(per_page_image_tokens)


class CostGuard:
    """Enforces the caps and keeps the ledger."""

    def __init__(self, config: Optional[GuardConfig] = None) -> None:
        self.config = config or GuardConfig()
        self._ensure_writable_cache_dir()
        self._ledger_path = self.config.cache_dir / "usage_ledger.json"
        self._lock = threading.Lock()
        # Ledger health. Either of these being True means we cannot account for
        # today's spend, which must block spending rather than permit it.
        self._ledger_unreadable = False
        self._ledger_unwritable = False
        self.session_pages = 0
        self.session_cost = 0.0
        self.session_cache_hits = 0
        #: True when the project-local cache dir was unwritable and a temp
        #: directory was used instead. Surfaced in the UI so a deployed demo
        #: reports "spend tracking is not persistent here" rather than lying.
        self.cache_dir_fell_back = getattr(self, "cache_dir_fell_back", False)

    def _ensure_writable_cache_dir(self) -> None:
        """Make the cache directory, falling back to a temp dir if we cannot.

        Streamlit Community Cloud and most container hosts give you a writable
        app directory, but not all do -- and a read-only one used to raise
        straight out of the constructor, so the app never rendered a single
        pixel. A demo that runs on free routes has nothing to write except a
        courtesy ledger, so losing the project-local cache is a downgrade, not
        a failure.
        """
        import tempfile

        candidate = self.config.cache_dir
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            os.replace(probe, candidate / ".write_probe")
            (candidate / ".write_probe").unlink()
            return
        except OSError:
            pass

        fallback = Path(tempfile.gettempdir()) / "invoice_parser_cache"
        try:
            fallback.mkdir(parents=True, exist_ok=True)
            self.config.cache_dir = fallback
            self.cache_dir_fell_back = True
        except OSError:
            # Nothing is writable. Carry on: the free routes do not need to
            # write, and the paid routes will refuse at preflight because the
            # ledger is unwritable.
            self.cache_dir_fell_back = True

    # ------------------------------------------------------------- ledger io
    def _load_ledger(self) -> Dict[str, Any]:
        """Read the ledger, and remember if it could not be trusted.

        A truncated or corrupt file used to be swallowed and reported as {}, i.e.
        as "nothing spent today" -- which silently removed the daily budget cap
        at exactly the moment it was least safe to remove it. Now the failure is
        recorded and `preflight` refuses to spend until it is repaired.
        """
        if not self._ledger_path.exists():
            self._ledger_unreadable = False
            return {}
        try:
            data = json.loads(self._ledger_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, ValueError):
            self._ledger_unreadable = True
            return {}
        if not isinstance(data, dict):
            self._ledger_unreadable = True
            return {}
        self._ledger_unreadable = False
        return data

    def _today_key(self) -> str:
        return date.today().isoformat()

    def today(self) -> Dict[str, float]:
        entry = self._load_ledger().get(self._today_key(), {})
        return {
            "pages": float(entry.get("pages", 0)),
            "cost_usd": float(entry.get("cost_usd", 0.0)),
            "calls": float(entry.get("calls", 0)),
            "input_tokens": float(entry.get("input_tokens", 0)),
            "output_tokens": float(entry.get("output_tokens", 0)),
            # Pages answered with no spend at all: text layer, cache or free tier.
            "free_pages": float(entry.get("free_pages", 0)),
        }

    def record(self, pages: int, input_tokens: int, output_tokens: int,
               free: bool = False) -> float:
        """Price a completed call and append it to the rolling ledger.

        `free=True` (a free tier, or a local model) still records the pages and
        tokens -- you want to see the volume -- but books $0.00, so a free
        provider cannot walk the dollar budget down.
        """
        cost = 0.0 if free else self.config.price(input_tokens, output_tokens)
        with self._lock:
            ledger = self._load_ledger()
            key = self._today_key()
            entry = ledger.setdefault(key, {"pages": 0, "cost_usd": 0.0, "calls": 0,
                                            "input_tokens": 0, "output_tokens": 0,
                                            "free_pages": 0})
            entry["pages"] += pages
            if free:
                entry["free_pages"] = int(entry.get("free_pages", 0)) + pages
            entry["cost_usd"] = round(float(entry["cost_usd"]) + cost, 6)
            entry["calls"] += 1
            entry["input_tokens"] += input_tokens
            entry["output_tokens"] += output_tokens
            # Keep only the last 60 days so the file never grows unbounded.
            for stale in sorted(ledger)[:-60]:
                ledger.pop(stale, None)
            # Atomic: write a sibling temp file then rename over the target, so a
            # crash or a full disk can never leave a half-written ledger behind
            # (which the old code would have read as "$0.00 spent today").
            tmp = self._ledger_path.parent / (self._ledger_path.name + ".tmp")
            try:
                tmp.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
                os.replace(tmp, self._ledger_path)
                self._ledger_unwritable = False
            except OSError:
                # Do not fail the parse the user already paid for -- but do not
                # forget it either. The next preflight will refuse to spend.
                self._ledger_unwritable = True
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
            self.session_pages += pages
            self.session_cost += cost
        return cost

    # ---------------------------------------------------------- pre-flight
    def check_upload_size(self, file_path: Path) -> None:
        """The cheapest possible refusal. Call this BEFORE hashing or parsing."""
        size_mb = file_path.stat().st_size / (1024 * 1024)
        if size_mb > self.config.max_upload_mb:
            raise BudgetExceeded(
                f"File is {size_mb:.1f} MB; the limit is {self.config.max_upload_mb:.0f} MB. "
                "Split the document or raise PARSER_MAX_UPLOAD_MB for a paying client."
            )

    def preflight(self, file_path: Path, total_pages: int, cached: bool = False,
                  free: bool = False) -> int:
        """Validate an upload before spending anything. Returns pages to process.

        `cached=True`  -- answered from disk or the text layer: instant and free,
                          so only the size and page caps apply.
        `free=True`    -- a free-tier or local model: no dollars at risk, but the
                          page caps still apply, because a free tier has rate
                          limits of its own and abuse of it is still abuse.

        Raises BudgetExceeded with a message written for a human to read.
        """
        cfg = self.config
        self.check_upload_size(file_path)

        pages_to_process = min(total_pages, cfg.max_pages_per_doc)

        if cached:
            return pages_to_process  # a cache hit costs nothing; skip the budget gates

        if self.session_pages + pages_to_process > cfg.session_page_limit:
            raise BudgetExceeded(
                f"Session limit reached ({cfg.session_page_limit} pages). "
                "This is the per-visitor cap on the public demo."
            )

        today = self.today()  # sets the ledger health flags as a side effect

        if self._ledger_unreadable and not free:
            raise BudgetExceeded(
                f"The usage ledger at {self._ledger_path} could not be read, so today's "
                "spend cannot be verified. Refusing to spend. Repair or delete that file "
                "to resume."
            )
        # Only refuse when real money is at stake. A read-only filesystem (a
        # container, a locked-down host) must not take down a demo that is
        # running entirely on the free text-layer and fixture routes, where
        # there is no spend to account for in the first place.
        if self._ledger_unwritable and not free:
            raise BudgetExceeded(
                f"The usage ledger at {self._ledger_path} is not writable, so further "
                "spend could not be accounted for. Refusing to spend. Fix the permissions "
                "on that directory, or set PARSER_CACHE_DIR to a writable path."
            )

        if today["pages"] + pages_to_process > cfg.daily_page_limit:
            raise BudgetExceeded(
                f"Daily page limit reached ({int(today['pages'])}/{cfg.daily_page_limit} pages). "
                "Resets at midnight."
            )

        if free:
            return pages_to_process     # page caps enforced above; no dollars at risk

        # Forward-looking: price THIS document before admitting it. The old check
        # only asked whether the budget was already gone, so a run starting at
        # $1.999 of a $2.00 budget was waved through at whatever it happened to cost.
        projected = cfg.worst_case_doc_cost(pages_to_process)
        if today["cost_usd"] + projected >= cfg.daily_usd_budget:
            raise BudgetExceeded(
                f"Daily budget of ${cfg.daily_usd_budget:.2f} would be exceeded: "
                f"${today['cost_usd']:.4f} already spent and this document is worth "
                f"up to ${projected:.4f}. Resets at midnight."
            )
        return pages_to_process

    # -------------------------------------------------------------- cache
    def _cache_path(self, fingerprint: str) -> Path:
        """Key on everything that changes the answer, not just the file bytes.

        Resolution and prompt version were previously absent, so `--max-edge 400`
        or an edited prompt silently returned the answer computed under the old
        settings -- the worst kind of stale, because it looks like a fresh result.
        """
        cfg = self.config
        stamp = f"{cfg.model}_e{cfg.max_image_edge}_o{cfg.max_output_tokens}_p{PROMPT_VERSION}"
        return cfg.cache_dir / f"extract_{stamp}_{fingerprint[:32]}.json"

    def cache_get(self, fingerprint: str) -> Optional[Dict[str, Any]]:
        if not self.config.cache_enabled:
            return None
        path = self._cache_path(fingerprint)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        # A malformed entry must read as a MISS, not sail on to raise KeyError
        # deep inside the extractor where nothing is catching it.
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            return None
        self.session_cache_hits += 1
        return payload

    def cache_put(self, fingerprint: str, payload: Dict[str, Any]) -> None:
        if not self.config.cache_enabled:
            return
        try:
            self._cache_path(fingerprint).write_text(
                json.dumps(payload, indent=2, default=str), encoding="utf-8"
            )
        except OSError:
            pass

    # ------------------------------------------------------------ reporting
    def summary(self) -> str:
        today = self.today()
        cfg = self.config
        return (
            f"model={cfg.model} | session: {self.session_pages} page(s), "
            f"${self.session_cost:.4f}, {self.session_cache_hits} cache hit(s) | "
            f"today: {int(today['pages'])}/{cfg.daily_page_limit} pages "
            f"({int(today['free_pages'])} free), "
            f"${today['cost_usd']:.4f}/${cfg.daily_usd_budget:.2f}"
        )

    def today_free_pages(self) -> int:
        """Pages answered today without spending -- text layer, cache or free tier."""
        entry = self._load_ledger().get(self._today_key(), {})
        return int(entry.get("free_pages", 0))
