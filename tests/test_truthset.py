"""
Tests for the accuracy-measurement machinery.

The point of this module is to stop the project quoting a number it has not
earned, so the tests are mostly about the ways a score can quietly inflate.
"""

from __future__ import annotations

import json
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

from parser.truthset import (
    TRUTH_FIELDS,
    UNKNOWN,
    TruthRecord,
    TruthSet,
    compare_field,
    fields_from_invoice,
    stub_from_extraction,
)
from parser.schemas import InvoiceData

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from reality_check import wilson  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "sample_invoices"


def invoice(**over) -> InvoiceData:
    base = {
        "vendor_name": "ACME Freight AB",
        "vendor_tax_id": "SE556677889901",
        "invoice_no": "AF-1",
        "invoice_date": "2026-01-15",
        "due_date": "2026-02-15",
        "currency": "SEK",
        "line_items": [{"description": "Haulage", "quantity": 2,
                        "unit_price": 100, "line_total": 200}],
        "subtotal": 200, "tax_rate": 25, "tax_amount": 50, "total_amount": 250,
    }
    base.update(over)
    return InvoiceData.model_validate(base)


# ------------------------------------------------------- the inflation guards
def test_a_stub_is_never_verified():
    """The single most important property here.

    A stub is pre-filled from the extractor's own output. If it counted as
    ground truth, the score would read ~100% forever no matter how wrong the
    extraction was.
    """
    stub = stub_from_extraction(SAMPLES / "1_clean_freight.pdf", invoice())
    assert stub.verified is False
    assert stub.source == "stub"
    assert "verified to true" in stub.notes


def test_unverified_records_are_excluded_from_the_verified_set(tmp_path):
    ts = TruthSet(tmp_path / "t.json")
    ts.put(TruthRecord("a" * 64, "a.pdf", {"total_amount": "1"}, verified=True))
    ts.put(TruthRecord("b" * 64, "b.pdf", {"total_amount": "1"}, verified=False))
    assert [r.file_name for r in ts.verified_records()] == ["a.pdf"]
    assert [r.file_name for r in ts.unverified_records()] == ["b.pdf"]


def test_unknown_marker_is_not_scored_as_either_pass_or_fail():
    """`?` means "not checked". Scoring it at all would be dishonest."""
    record = TruthRecord("c" * 64, "c.pdf", {
        "vendor_name": "ACME",
        "due_date": UNKNOWN,
        "tax_rate": UNKNOWN,
        "total_amount": "250.00",
    }, verified=True)
    assert set(record.scoreable_fields()) == {"vendor_name", "total_amount"}
    with pytest.raises(ValueError):
        compare_field("due_date", UNKNOWN, None)


def test_null_means_absent_and_IS_scored():
    """Distinct from `?`. A document with no due date SHOULD extract None."""
    record = TruthRecord("d" * 64, "d.pdf", {"due_date": None}, verified=True)
    assert record.scoreable_fields() == ["due_date"]
    assert compare_field("due_date", None, None)
    assert not compare_field("due_date", None, "2026-01-01")


def test_a_corrupt_truth_set_refuses_to_start_from_empty(tmp_path):
    """Silently reading a broken file as {} would report a fresh 0/0 as if
    nothing had ever been verified -- losing work and hiding the loss."""
    path = tmp_path / "t.json"
    path.write_text("{ truncated")
    with pytest.raises(RuntimeError, match="refusing to silently start from empty"):
        TruthSet(path)


# --------------------------------------------------------------- comparisons
@pytest.mark.parametrize("expected,actual,ok", [
    ("1550.00", "1550", True),
    ("1550.00", Decimal("1550.004"), True),      # within half a cent
    ("1550.00", "1550.01", False),
    ("14 993,85", "14993.85", True),             # Swedish formatting
    (None, None, True),
    (None, "1550", False),
    ("1550", None, False),
])
def test_money_comparison(expected, actual, ok):
    assert compare_field("subtotal", expected, actual) is ok


@pytest.mark.parametrize("expected,actual,ok", [
    ("04.09.2026", "2026-09-04", True),
    ("2026-09-04", "2026-09-04", True),
    ("2026-09-04", "2026-09-05", False),
])
def test_date_comparison_is_format_agnostic(expected, actual, ok):
    assert compare_field("invoice_date", expected, actual) is ok


@pytest.mark.parametrize("expected,actual,ok", [
    (" ACME  ab ", "ACME AB", True),
    ("GB 412 8876 22", "GB412887622", True),
    ("ACME AB", "ACME HB", False),
])
def test_text_comparison_ignores_case_and_spacing(expected, actual, ok):
    assert compare_field("vendor_name", expected, actual) is ok


def test_line_item_count_is_compared_as_a_number():
    assert compare_field("line_item_count", 4, 4)
    assert compare_field("line_item_count", "4", 4)
    assert not compare_field("line_item_count", 4, 3)
    assert not compare_field("line_item_count", 4, None)


# ---------------------------------------------------------------- round trip
def test_extraction_flattens_into_every_truth_field():
    fields = fields_from_invoice(invoice())
    assert set(fields) == set(TRUTH_FIELDS)
    assert fields["line_item_count"] == 1
    assert fields["total_amount"] == "250.00"


def test_store_survives_a_save_load_round_trip(tmp_path):
    path = tmp_path / "t.json"
    ts = TruthSet(path)
    ts.put(TruthRecord("e" * 64, "e.pdf", {"total_amount": "9.99"},
                       verified=True, source="app-editor", notes="hi"))
    ts.save()

    again = TruthSet(path)
    assert len(again) == 1
    record = again.records["e" * 64]
    assert record.verified and record.source == "app-editor" and record.notes == "hi"
    assert record.fields["total_amount"] == "9.99"

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "NEVER COMMIT" in payload["_comment"]


def test_records_are_keyed_by_content_so_a_rename_keeps_its_truth(tmp_path):
    original = SAMPLES / "1_clean_freight.pdf"
    renamed = tmp_path / "totally_different_name.pdf"
    renamed.write_bytes(original.read_bytes())

    ts = TruthSet(tmp_path / "t.json")
    ts.put(stub_from_extraction(original, invoice()))
    assert ts.get(renamed) is not None, "same bytes must resolve to the same truth"


def test_a_truth_set_is_never_written_into_the_project_by_default(monkeypatch):
    """Guards the demo/test path: PARSER_TRUTH_STORE must win when set."""
    target = Path(tempfile.gettempdir()) / "somewhere_else.json"
    monkeypatch.setenv("PARSER_TRUTH_STORE", str(target))
    import importlib

    import parser.truthset as module
    importlib.reload(module)
    try:
        # Path-to-Path: str() renders separators per platform, so comparing
        # strings would fail on Windows for a store that resolved correctly.
        assert module.DEFAULT_STORE == target
        assert module.DEFAULT_STORE.parent != Path("real_invoices")
    finally:
        monkeypatch.delenv("PARSER_TRUTH_STORE")
        importlib.reload(module)


# ---------------------------------------------------------------- statistics
def test_wilson_refuses_to_pretend_a_tiny_sample_is_certain():
    """The whole reason this project needs an interval.

    Two-for-two is the evidence behind the current "100%" claim. The normal
    approximation would report [100%, 100%]. Wilson reports a floor near 34%,
    which is the truth.
    """
    point, low, high = wilson(2, 2)
    assert point == 100.0
    assert 30 < low < 40
    assert high == 100.0


def test_the_interval_narrows_as_the_sample_grows():
    _, low_2, _ = wilson(2, 2)
    _, low_20, _ = wilson(20, 20)
    _, low_100, _ = wilson(100, 100)
    assert low_2 < low_20 < low_100
    assert low_20 > 80        # 20/20 supports "over 80%", not "100%"


@pytest.mark.parametrize("successes,trials", [(0, 10), (5, 10), (10, 10), (1, 1)])
def test_interval_always_brackets_the_point_estimate(successes, trials):
    point, low, high = wilson(successes, trials)
    assert 0.0 <= low <= point <= high <= 100.0


def test_zero_trials_does_not_divide_by_zero():
    assert wilson(0, 0) == (0.0, 0.0, 0.0)
