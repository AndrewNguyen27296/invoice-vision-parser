"""
Every cost guardrail, tested.

The RUNBOOK used to claim "7/7 guardrail tests pass". There were none. These are
those tests. Each one asserts that a cap raises BudgetExceeded *before* anything
is spent -- because a guardrail you have not seen fail is not a guardrail, it is
a comment.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parser.cost_guard import PRICING, BudgetExceeded, CostGuard, GuardConfig

SAMPLE = Path(__file__).resolve().parent.parent / "sample_invoices" / "1_clean_freight.pdf"


def guard(tmp_path: Path, **over) -> CostGuard:
    cfg = GuardConfig()
    cfg.cache_dir = tmp_path / ".cache"
    for key, value in over.items():
        setattr(cfg, key, value)
    return CostGuard(cfg)


# ------------------------------------------------------------------ 1. pages
def test_max_pages_per_doc_caps_a_huge_document(tmp_path):
    g = guard(tmp_path, max_pages_per_doc=3)
    assert g.preflight(SAMPLE, total_pages=400) == 3


# ------------------------------------------------------------------- 2. size
def test_oversized_upload_is_refused_before_it_is_even_opened(tmp_path):
    """The size gate must be the first thing that happens.

    This file has a .pdf suffix but is not a PDF. If the size check runs first we
    get BudgetExceeded; if anything tries to hash or parse it first we get a
    different error entirely. That distinction is the whole point.
    """
    fake = tmp_path / "huge.pdf"
    with open(fake, "wb") as fh:          # sparse 16 MB, instant to create
        fh.seek(16 * 1024 * 1024 - 1)
        fh.write(b"\0")

    g = guard(tmp_path, max_upload_mb=15.0)
    with pytest.raises(BudgetExceeded, match="16.0 MB"):
        g.check_upload_size(fake)

    from parser.extractor import extract_invoice
    with pytest.raises(BudgetExceeded):
        extract_invoice(fake, guard=g, mock=True)


# ---------------------------------------------------------------- 3. session
def test_session_page_limit_stops_one_visitor(tmp_path):
    g = guard(tmp_path, session_page_limit=4, max_pages_per_doc=3)
    g.session_pages = 2
    with pytest.raises(BudgetExceeded, match="Session limit"):
        g.preflight(SAMPLE, total_pages=3)


# ------------------------------------------------------------------ 4. daily
def test_daily_page_limit_stops_everyone(tmp_path):
    g = guard(tmp_path, daily_page_limit=10, max_pages_per_doc=3)
    g.record(pages=9, input_tokens=1000, output_tokens=100)
    with pytest.raises(BudgetExceeded, match="Daily page limit"):
        g.preflight(SAMPLE, total_pages=3)


# ----------------------------------------------------------------- 5. budget
def test_daily_budget_is_forward_looking(tmp_path):
    """A near-exhausted budget must refuse the next document, not admit it.

    The old check was `spent >= budget`, so at $1.999 of a $2.00 budget a fresh
    3-page document was waved through at whatever it turned out to cost.
    """
    g = guard(tmp_path, daily_usd_budget=2.0, max_pages_per_doc=3)
    g.record(pages=1, input_tokens=1_000_000, output_tokens=199_000)   # $1.995
    assert g.today()["cost_usd"] == pytest.approx(1.995, abs=1e-6)
    with pytest.raises(BudgetExceeded, match="would be exceeded"):
        g.preflight(SAMPLE, total_pages=3)


def test_worst_case_projection_scales_with_resolution(tmp_path):
    """Image tokens go as the square of the edge -- but they are not the big term.

    Doubling the edge quadruples the image cost and yet raises the per-page total
    by only ~48%, because at the default settings the OUTPUT token budget
    ($0.0080/page) dwarfs the image ($0.0017/page). Resolution is the biggest
    lever on the image component only; max_output_tokens is the biggest lever on
    the bill. This test pins both numbers so that stays visible.
    """
    g = guard(tmp_path, max_image_edge=1120, max_output_tokens=1600,
              model="claude-haiku-4-5")
    cheap = g.config.worst_case_doc_cost(1)
    g.config.max_image_edge = 2240
    dear = g.config.worst_case_doc_cost(1)

    image_cheap = g.config.price(int(1120 ** 2 / 750), 0)
    image_dear = g.config.price(int(2240 ** 2 / 750), 0)
    assert image_dear == pytest.approx(image_cheap * 4, rel=0.01)   # quadruples
    assert dear == pytest.approx(cheap * 1.48, rel=0.02)            # total does not

    output_share = g.config.price(0, 1600) / cheap
    assert output_share > 0.75      # output tokens are >75% of a default page


# --------------------------------------------------------- 6. ledger is trusted
def test_corrupt_ledger_fails_closed(tmp_path):
    """A truncated ledger must block spending, not reset the budget to zero.

    Reading a corrupt file as "{}" meant a single interrupted write removed the
    daily cap at exactly the wrong moment.
    """
    g = guard(tmp_path, daily_usd_budget=2.0)
    g.record(pages=1, input_tokens=2_000_000, output_tokens=400_000)   # $4.00
    with pytest.raises(BudgetExceeded, match="budget"):
        g.preflight(SAMPLE, total_pages=1)

    (g.config.cache_dir / "usage_ledger.json").write_text("{ truncated")
    fresh = CostGuard(g.config)
    with pytest.raises(BudgetExceeded, match="could not be read"):
        fresh.preflight(SAMPLE, total_pages=1)


def test_unwritable_ledger_fails_closed(tmp_path, monkeypatch):
    """If spend cannot be recorded, further spend must be refused."""
    g = guard(tmp_path)
    g.record(pages=1, input_tokens=1000, output_tokens=100)
    g.preflight(SAMPLE, total_pages=1)          # healthy: allowed

    import pathlib
    real = pathlib.Path.write_text

    def boom(self, *a, **k):
        if self.name.endswith(".tmp"):
            raise OSError("read-only file system")
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "write_text", boom)
    g.record(pages=1, input_tokens=1000, output_tokens=100)
    monkeypatch.undo()

    with pytest.raises(BudgetExceeded, match="not writable"):
        g.preflight(SAMPLE, total_pages=1)


def test_ledger_write_is_atomic_and_leaves_no_debris(tmp_path):
    g = guard(tmp_path)
    for _ in range(3):
        g.record(pages=1, input_tokens=1000, output_tokens=100)
    files = sorted(p.name for p in g.config.cache_dir.iterdir())
    assert files == ["usage_ledger.json"]
    json.loads((g.config.cache_dir / "usage_ledger.json").read_text())   # parses


def test_ledger_is_anchored_to_the_project_not_the_cwd(tmp_path, monkeypatch):
    """`cd /tmp && python cli.py ...` must not hand you a fresh daily budget."""
    monkeypatch.delenv("PARSER_CACHE_DIR", raising=False)
    from pathlib import Path as _Path

    import parser.cost_guard as cg
    project_root = _Path(cg.__file__).resolve().parent.parent

    before = GuardConfig().cache_dir
    monkeypatch.chdir(tmp_path)
    after = GuardConfig().cache_dir

    assert after.is_absolute()
    assert after == before, "the cache dir must not follow the working directory"
    # Anchored to the package, not to a hardcoded folder name -- this repo is
    # standalone and must work when cloned under any directory name.
    assert after == project_root / ".cache"


# ------------------------------------------------------------------ 7. cache
def test_cache_hit_bypasses_the_budget_gates_and_costs_nothing(tmp_path):
    g = guard(tmp_path, daily_usd_budget=0.0001)
    g.record(pages=1, input_tokens=1_000_000, output_tokens=1_000_000)
    with pytest.raises(BudgetExceeded):
        g.preflight(SAMPLE, total_pages=1, cached=False)
    assert g.preflight(SAMPLE, total_pages=1, cached=True) == 1


def test_cache_key_changes_with_anything_that_changes_the_answer(tmp_path):
    g = guard(tmp_path)
    fp = "a" * 64
    baseline = g._cache_path(fp).name
    for attr, value in (("max_image_edge", 400),
                        ("max_output_tokens", 400),
                        ("model", "claude-sonnet-5")):
        setattr(g.config, attr, value)
        assert g._cache_path(fp).name != baseline, f"{attr} must invalidate the cache"
        g = guard(tmp_path)

def test_a_malformed_cache_entry_reads_as_a_miss(tmp_path):
    """Better a fresh extraction than a KeyError nothing is catching."""
    g = guard(tmp_path)
    fp = "b" * 64
    g._cache_path(fp).write_text('{"model": "x"}')          # no "data" key
    assert g.cache_get(fp) is None
    g._cache_path(fp).write_text("[1, 2, 3]")                # not even an object
    assert g.cache_get(fp) is None
    assert g.session_cache_hits == 0


def test_cache_roundtrip(tmp_path):
    g = guard(tmp_path)
    g.cache_put("f" * 64, {"data": {"vendor_name": "X"}, "model": g.config.model})
    assert g.cache_get("f" * 64)["data"]["vendor_name"] == "X"
    assert g.cache_get("0" * 64) is None


# ---------------------------------------------------------------- 8. pricing
@pytest.mark.parametrize("model,inp,out", [
    ("claude-haiku-4-5", 1.00, 5.00),
    ("claude-sonnet-5", 2.00, 10.00),
    ("claude-sonnet-4-6", 3.00, 15.00),
    ("claude-opus-4-5", 5.00, 25.00),
])
def test_pricing_table_matches_published_rates(model, inp, out):
    assert PRICING[model] == {"input": inp, "output": out}


def test_price_arithmetic(tmp_path):
    g = guard(tmp_path, model="claude-haiku-4-5")
    assert g.config.price(1_000_000, 1_000_000) == pytest.approx(6.00)
