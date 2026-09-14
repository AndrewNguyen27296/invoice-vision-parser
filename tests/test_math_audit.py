"""The math audit is the product's trust anchor. It gets tested."""

from decimal import Decimal

import pytest

from parser.schemas import InvoiceData, to_decimal


def build(**kwargs) -> InvoiceData:
    base = {
        "line_items": [
            {"description": "Freight", "quantity": 2, "unit_price": 100, "line_total": 200},
            {"description": "Surcharge", "quantity": 1, "unit_price": 50, "line_total": 50},
        ],
        "subtotal": 250,
        "tax_rate": 25,
        "tax_amount": 62.50,
        "total_amount": 312.50,
    }
    base.update(kwargs)
    return InvoiceData.model_validate(base)


def test_clean_invoice_passes():
    audit = build().run_audit()
    assert audit.passed
    assert audit.total_discrepancy == Decimal("0.00")
    assert not audit.failures


def test_wrong_total_fails():
    audit = build(total_amount=315.50).run_audit()
    assert not audit.passed
    assert audit.total_discrepancy == Decimal("3.00")
    assert "differs by" in audit.failures[0]


def test_rounding_within_tolerance_passes():
    audit = build(total_amount=312.53).run_audit()
    assert audit.passed  # 0.03 <= 0.05 tolerance


def test_line_items_not_summing_to_subtotal_fails():
    audit = build(subtotal=260, tax_amount=65, total_amount=325).run_audit()
    assert not audit.passed


def test_missing_subtotal_is_derived():
    inv = InvoiceData.model_validate(
        {"line_items": [], "tax_amount": 62.50, "total_amount": 312.50}
    )
    assert inv.subtotal == Decimal("250.00")
    assert any("derived" in n for n in inv.extraction_notes)


def test_row_level_mismatch_warns_but_does_not_fail():
    inv = build(line_items=[
        {"description": "Freight", "quantity": 2, "unit_price": 100, "line_total": 200},
        {"description": "Odd row", "quantity": 3, "unit_price": 10, "line_total": 50},
    ], subtotal=250)
    audit = inv.run_audit()
    assert audit.passed
    assert any("Odd row" in w for w in audit.warnings)


@pytest.mark.parametrize("raw,expected", [
    ("1 450,00", "1450.00"), ("$1,450.00", "1450.00"), ("1.450,00 SEK", "1450.00"),
    ("(230.00)", "-230.00"), ("1450", "1450"), ("", None), (None, None),
    ("18 745,31", "18745.31"), ("abc", None),
])
def test_decimal_coercion(raw, expected):
    result = to_decimal(raw)
    assert result == (None if expected is None else Decimal(expected))


def test_garbage_input_degrades_gracefully():
    inv = InvoiceData.model_validate(
        {"vendor_name": None, "invoice_date": "not a date",
         "line_items": [{"description": None}], "total_amount": "n/a"}
    )
    audit = inv.run_audit()
    assert inv.invoice_date is None
    assert inv.line_items[0].description == "(no description)"
    assert not audit.passed
    assert audit.warnings
