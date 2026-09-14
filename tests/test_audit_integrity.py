"""
Tests for the two claims this product is actually sold on.

  1. "The green badge is earned by Python, not asserted by the model."
  2. "Nobody can run up your bill."

Both had a hole in V0. These tests are the ones that would have caught them, so
they are written to fail loudly if either hole is ever reopened.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from parser import extractor as EX
from parser.cost_guard import CostGuard, GuardConfig
from parser.providers import ProviderReply
from parser.schemas import InvoiceData

SAMPLE = Path(__file__).resolve().parent.parent / "sample_invoices" / "1_clean_freight.pdf"


def guard(tmp_path: Path, **over) -> CostGuard:
    cfg = GuardConfig()
    cfg.cache_dir = tmp_path / ".cache"
    for k, v in over.items():
        setattr(cfg, k, v)
    return CostGuard(cfg)


# ===================================================================== claim 1
def test_a_back_calculated_subtotal_cannot_verify_itself():
    """subtotal = total - tax, then "subtotal + tax = total" -- that is an identity.

    V0 reported this as "Math Audit Passed - 1 check(s), 0.00 discrepancy" on a
    document where the model had extracted two numbers and no line items at all.
    """
    inv = InvoiceData.model_validate(
        {"line_items": [], "tax_amount": 62.50, "total_amount": 312.50}
    )
    assert inv.subtotal == Decimal("250.00")
    assert inv.derived_fields == ["subtotal"]

    audit = inv.run_audit()
    assert audit.circular
    assert not audit.passed
    assert audit.checks == []
    assert audit.total_discrepancy is None, "a fabricated 0.00 must not be displayed"
    assert any("NOT verified" in w for w in audit.warnings)
    assert "INCONCLUSIVE" in audit.badge


@pytest.mark.parametrize("tax,total", [(0, 999.99), (5000, 10.00), (-40, 20.00)])
def test_nonsense_pairs_are_never_a_pass(tax, total):
    """Any (tax, total) pair used to pass, because the third number was invented."""
    audit = InvoiceData.model_validate(
        {"line_items": [], "tax_amount": tax, "total_amount": total}
    ).run_audit()
    assert not audit.passed


def test_a_derived_subtotal_IS_verified_by_printed_line_items():
    """Deriving is not the problem; unfalsifiable checks are.

    Here the line items are genuinely printed, so summing them against the
    derived subtotal is real, independent evidence and should pass.
    """
    inv = InvoiceData.model_validate({
        "line_items": [
            {"description": "Freight", "quantity": 2, "unit_price": 100, "line_total": 200},
            {"description": "Surcharge", "quantity": 1, "unit_price": 50, "line_total": 50},
        ],
        "tax_amount": 62.50, "total_amount": 312.50,
    })
    audit = inv.run_audit()
    assert inv.derived_fields == ["subtotal"]
    assert audit.passed
    assert any("sum(line items)" in c for c in audit.checks)
    assert audit.circular          # the identity is still flagged as such


def test_line_items_still_catch_a_wrong_derived_subtotal():
    inv = InvoiceData.model_validate({
        "line_items": [{"description": "Freight", "line_total": 999}],
        "tax_amount": 62.50, "total_amount": 312.50,
    })
    assert not inv.run_audit().passed


def test_fully_printed_invoice_still_passes_normally():
    """The happy path must be untouched by the circularity fix."""
    audit = InvoiceData.model_validate({
        "line_items": [{"description": "Freight", "quantity": 2,
                        "unit_price": 100, "line_total": 200}],
        "subtotal": 200, "tax_rate": 25, "tax_amount": 50, "total_amount": 250,
    }).run_audit()
    assert audit.passed and not audit.circular
    assert audit.total_discrepancy == Decimal("0.00")


def test_hallucinated_field_names_are_reported_not_swallowed():
    """extra="ignore" silently binned every number on the page. Now it is noted."""
    inv = InvoiceData.model_validate({
        "vendor_name": "ACME", "grand_total": 5000.00,
        "net_amount": 4000.00, "vat": 1000.00, "line_items": [],
    })
    assert inv.total_amount is None
    note = " ".join(inv.extraction_notes)
    assert "unrecognised" in note
    for key in ("grand_total", "net_amount", "vat"):
        assert key in note
    assert not inv.run_audit().passed


# ===================================================================== claim 2
def test_spend_is_ledgered_even_when_the_reply_is_unparseable(tmp_path):
    """A billed call that returns junk must still move the daily budget.

    V0 recorded usage only on the success path, so a loop of malformed replies
    was real money the cap never saw.
    """
    g = guard(tmp_path)
    calls = {"n": 0}

    class JunkProvider:
        name, free = "junk", False

        def describe(self):
            return "junk"

        def call(self, pages, cfg):
            calls["n"] += 1
            return ProviderReply("I'm sorry, I can't read that invoice.", 3000, 1600, "junk")

    for _ in range(5):
        with pytest.raises(EX.ExtractionError):
            EX.extract_invoice(SAMPLE, guard=g, force_refresh=True,
                               provider=JunkProvider(), use_text_layer=False)

    assert calls["n"] == 5
    today = g.today()
    assert today["calls"] == 5
    assert today["cost_usd"] == pytest.approx(5 * g.config.price(3000, 1600), rel=1e-6)
    assert today["cost_usd"] > 0


def test_a_bad_reply_is_never_cached(tmp_path):
    g = guard(tmp_path)

    class JunkProvider:
        name, free = "junk", False
        describe = staticmethod(lambda: "junk")

        def call(self, pages, cfg):
            return ProviderReply("not json", 100, 10, "junk")

    with pytest.raises(EX.ExtractionError):
        EX.extract_invoice(SAMPLE, guard=g, force_refresh=True,
                           provider=JunkProvider(), use_text_layer=False)
    assert not list(g.config.cache_dir.glob("extract_*.json"))


def test_a_free_provider_never_moves_the_dollar_budget(tmp_path):
    """A free tier records volume but must book $0.00."""
    g = guard(tmp_path)

    class FreeProvider:
        name, free = "freebie", True
        describe = staticmethod(lambda: "free tier")

        def call(self, pages, cfg):
            import json as _json
            from parser.providers.mock_provider import MOCK_INVOICE
            return ProviderReply(_json.dumps(MOCK_INVOICE), 5000, 900, "freebie")

    result = EX.extract_invoice(SAMPLE, guard=g, force_refresh=True,
                                provider=FreeProvider(), use_text_layer=False)
    assert result.cost_usd == 0.0
    assert result.was_free
    assert g.today()["cost_usd"] == 0.0
    assert g.today()["pages"] == 1          # volume still counted
    assert g.today()["input_tokens"] == 5000
    assert result.audit.passed


def test_mock_mode_spends_nothing_and_still_audits(tmp_path):
    g = guard(tmp_path)
    result = EX.extract_invoice(SAMPLE, guard=g, mock=True)
    assert result.cost_usd == 0.0
    assert g.today()["cost_usd"] == 0.0
    assert result.audit.passed and not result.audit.circular
    assert result.within_latency_budget
