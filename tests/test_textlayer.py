"""
Tests for the free extraction path.

This path is what makes the demo cost nothing and what makes the unit economics
work on real accounts-payable traffic, so it is tested as a first-class engine,
not as a nice-to-have.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from parser.schemas import InvoiceData
from parser.textlayer import (
    _parse_row,
    extract_from_text_layer,
    has_text_layer,
    page_texts,
    parse_text_layer,
)

SAMPLES = Path(__file__).resolve().parent.parent / "sample_invoices"
DIGITAL = SAMPLES / "1_clean_freight.pdf"
MESSY = SAMPLES / "2_messy_wholesale.pdf"
SCAN = SAMPLES / "3_skewed_scan.pdf"


# ------------------------------------------------------------------ detection
def test_digital_pdfs_have_a_text_layer_and_scans_do_not():
    assert has_text_layer(DIGITAL)
    assert has_text_layer(MESSY)
    assert not has_text_layer(SCAN), "a rasterised scan must fall through to vision"


def test_a_scan_returns_none_so_the_pipeline_escalates():
    assert extract_from_text_layer(SCAN) is None


def test_non_pdf_input_has_no_text_layer(tmp_path):
    fake = tmp_path / "photo.png"
    fake.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 64)
    assert page_texts(fake) == []
    assert not has_text_layer(fake)


# ------------------------------------------------------- the ambiguity fix
def test_space_thousands_separator_is_resolved_by_arithmetic():
    """The row that cannot be tokenised correctly without doing the maths.

        010 Frakt / Freight 1 450,00 450,00

    Read left to right, "1 450,00" is a valid Swedish number (1450.00). Read as
    three columns it is qty 1 at 450,00 = 450,00. Only the arithmetic decides,
    and 1 x 450 == 450 while 1450 x 450 != 450.
    """
    row = _parse_row("010 Frakt / Freight 1 450,00 450,00")
    assert row is not None
    assert row["description"] == "Frakt / Freight"
    assert Decimal(row["quantity"]) == Decimal("1")
    assert Decimal(row["unit_price"]) == Decimal("450.00")
    assert Decimal(row["line_total"]) == Decimal("450.00")


def test_space_thousands_in_the_amount_column_is_kept_whole():
    """Here the merge is the CORRECT reading: 24 x 89,50 == 2 148,00."""
    row = _parse_row("001 Kaffebonor Arabica 1kg / Coffee beans 24 89,50 2 148,00")
    assert row is not None
    assert row["description"] == "Kaffebonor Arabica 1kg / Coffee beans"
    assert Decimal(row["quantity"]) == Decimal("24")
    assert Decimal(row["line_total"]) == Decimal("2148.00")


def test_digits_inside_a_description_are_not_mistaken_for_columns():
    row = _parse_row("Waiting time, 1.5 h 1.5 420.00 630.00")
    assert row is not None
    assert Decimal(row["quantity"]) == Decimal("1.5")
    assert Decimal(row["line_total"]) == Decimal("630.00")
    assert "Waiting time" in row["description"]


def test_a_row_whose_arithmetic_does_not_work_keeps_only_the_amount():
    """Better an honest amount than an invented quantity and unit price."""
    row = _parse_row("Odd charge 7 11.00 500.00")
    assert row is not None
    assert row["quantity"] is None and row["unit_price"] is None
    assert Decimal(row["line_total"]) == Decimal("500.00")


@pytest.mark.parametrize("line", [
    "Description Qty Unit price Amount",
    "ART BENAMNING ANTAL A-PRIS BELOPP",
    "Subtotal 1,550.00",
    "TOTAL DUE 1,937.50",
    "Moms 25% 3 748,46",
    "Payment terms: 30 days net. Bankgiro 123-4567.",
])
def test_headers_totals_and_footers_are_not_line_items(line):
    assert _parse_row(line) is None


def test_address_blocks_do_not_become_line_items():
    """"Box 1182" in a Bill-to block used to be read as a 1182.00 line item."""
    result = extract_from_text_layer(DIGITAL)
    assert result is not None
    descriptions = [r["description"] for r in result.data["line_items"]]
    assert len(descriptions) == 4
    assert not any("Box" == d for d in descriptions)


# --------------------------------------------------------------- whole files
def test_clean_freight_invoice_is_read_exactly():
    result = extract_from_text_layer(DIGITAL)
    assert result is not None and result.sufficient
    assert result.coverage == 1.0

    inv = InvoiceData.model_validate(result.data)
    assert inv.vendor_name == "NORDFRAKT LOGISTIK AB"
    assert inv.vendor_tax_id == "SE556677889901"
    assert inv.invoice_no == "NF-2026-04417"
    assert str(inv.invoice_date) == "2026-08-21"
    assert str(inv.due_date) == "2026-09-20"
    assert inv.currency == "SEK"
    assert inv.subtotal == Decimal("1550.00")
    assert inv.tax_rate == Decimal("25")
    assert inv.tax_amount == Decimal("387.50")
    assert inv.total_amount == Decimal("1937.50")
    assert len(inv.line_items) == 4

    audit = inv.run_audit()
    assert audit.passed and not audit.circular


def test_messy_swedish_invoice_reads_all_eleven_rows_and_fails_the_audit():
    """Decimal commas, space thousands, 11 rows -- and a seeded 3.00 SEK error.

    Reading it correctly and then FAILING the audit is the product working.
    """
    result = extract_from_text_layer(MESSY)
    assert result is not None and result.sufficient

    inv = InvoiceData.model_validate(result.data)
    assert inv.vendor_name == "SODRA PARTIHANDEL & GROSSIST HB"
    assert inv.invoice_no == "2026-1183-B"
    assert str(inv.invoice_date) == "2026-09-04"
    assert str(inv.due_date) == "2026-10-04"
    assert inv.subtotal == Decimal("14993.85")
    assert inv.tax_amount == Decimal("3748.46")
    assert inv.total_amount == Decimal("18745.31")
    assert len(inv.line_items) == 11

    # Every row's own arithmetic must hold, which is what proves the columns
    # were split correctly rather than merely plausibly.
    for item in inv.line_items:
        assert item.row_discrepancy == Decimal("0")

    # Line items sum to the printed subtotal ...
    assert sum(i.line_total for i in inv.line_items) == Decimal("14993.85")

    audit = inv.run_audit()
    assert not audit.passed
    assert audit.total_discrepancy == Decimal("3.00")
    assert "differs by 3.00" in audit.failures[0]


def test_subtotal_label_is_not_eaten_by_the_total_label():
    """"Subtotal" contains "total". Order of matching decides correctness."""
    data = parse_text_layer(
        "ACME Freight AB\n"
        "Description Qty Price Amount\n"
        "Haulage 2 100,00 200,00\n"
        "Subtotal 200,00\n"
        "VAT 25% 50,00\n"
        "TOTAL DUE 250,00\n"
    )
    assert data["subtotal"] == "200,00"
    assert data["tax_amount"] == "50,00"
    assert data["total_amount"] == "250,00"
    assert data["tax_rate"] == "25"


def test_english_labels_also_work():
    data = parse_text_layer(
        "Atlas Corporate Travel Ltd\n"
        "Invoice number: ATC-1\n"
        "Invoice date: 2026-01-15\n"
        "Due date: 2026-02-15\n"
        "Currency: GBP\n"
        "Description Qty Rate Amount\n"
        "Flight 2 100.00 200.00\n"
        "Net amount 200.00\n"
        "VAT 20% 40.00\n"
        "Amount due 240.00\n"
    )
    inv = InvoiceData.model_validate(data)
    assert inv.vendor_name == "Atlas Corporate Travel Ltd"
    assert inv.invoice_no == "ATC-1"
    assert str(inv.invoice_date) == "2026-01-15"
    assert str(inv.due_date) == "2026-02-15"
    assert inv.currency == "GBP"
    assert inv.total_amount == Decimal("240.00")
    assert inv.run_audit().passed


def test_a_thin_text_layer_is_treated_as_no_text_layer(tmp_path):
    """A mail-gateway stamp on a scan is not an invoice. Do not parse it."""
    assert extract_from_text_layer.__doc__          # sanity
    data = parse_text_layer("Scanned by MailGuard 3.1\n")
    assert not data["line_items"] and data["total_amount"] is None


def test_coverage_is_reported_honestly():
    """A partial read must say so, so the pipeline can escalate instead of guess."""
    from parser.textlayer import TextLayerExtraction
    partial = TextLayerExtraction(
        data={"total_amount": None, "line_items": []},
        raw_text="x" * 200, coverage=0.25, found=["vendor_name"],
        missing=["total_amount", "line_items"],
    )
    assert not partial.sufficient
