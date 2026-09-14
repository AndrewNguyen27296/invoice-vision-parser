"""
Export validated invoice data to a formatted Excel workbook (and CSV).

DESIGN POSITION
    This is the last click of the demo and the first thing the buyer's team will
    actually use, so it is built for the person who opens it in Excel, not for
    the person who wrote it:

      * Numbers are written as NUMBERS, with Excel number formats -- never as
        pre-formatted strings. An AP clerk must be able to sum a column.
      * The totals row carries live =SUM() formulas, so an edit recalculates in
        the sheet instead of silently disagreeing with it.
      * The audit verdict travels WITH the data, on the sheet. A spreadsheet
        that says "verified" without saying what was verified is worse than one
        that says nothing.
      * Three audit states get three colours. Amber for INCONCLUSIVE is the
        whole point: it is not a pass, and it is not a failure.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

if TYPE_CHECKING:  # pragma: no cover
    from parser.extractor import ExtractionResult
    from parser.schemas import InvoiceData

# One restrained palette, used consistently. Dark navy header to match the
# sample invoices; semantic fills only for the audit verdict.
NAVY = "1B3A5C"
LIGHT = "EEF2F6"
RULE = "C9D4DF"
PASS_FILL, PASS_TEXT = "E3F5E9", "13653A"
FAIL_FILL, FAIL_TEXT = "FDE7E7", "9B1C1C"
WARN_FILL, WARN_TEXT = "FEF4E3", "8A5300"

MONEY_FMT = '#,##0.00'
QTY_FMT = '#,##0.###'

_THIN = Side(style="thin", color=RULE)
_BOX = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def _num(value: Optional[Decimal]) -> Optional[float]:
    """Excel wants a float. Decimal is for auditing, not for cells."""
    return None if value is None else float(value)


def _audit_colours(audit) -> tuple[str, str, str]:
    """(fill, text, label) for the three audit states."""
    if audit is None:
        return WARN_FILL, WARN_TEXT, "NOT AUDITED"
    if audit.passed:
        return PASS_FILL, PASS_TEXT, "MATH VERIFIED"
    if audit.failures:
        return FAIL_FILL, FAIL_TEXT, "MATH AUDIT FAILED"
    return WARN_FILL, WARN_TEXT, "INCONCLUSIVE - NOT VERIFIED"


def build_workbook(result: "ExtractionResult") -> Workbook:
    """Render one extraction as a styled, formula-bearing workbook."""
    inv, audit = result.invoice, result.audit
    wb = Workbook()
    ws = wb.active
    ws.title = "Invoice"
    ws.sheet_view.showGridLines = False
    currency = inv.currency or ""

    # ---------------------------------------------------------------- banner
    ws.merge_cells("A1:E1")
    title = ws["A1"]
    title.value = inv.vendor_name or "Invoice"
    title.font = Font(size=16, bold=True, color="FFFFFF")
    title.fill = PatternFill("solid", fgColor=NAVY)
    title.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[1].height = 30

    # ------------------------------------------------------------ audit badge
    fill, text, label = _audit_colours(audit)
    ws.merge_cells("A2:E2")
    badge = ws["A2"]
    # The badge sentence already names the verdict, so do not prefix it again.
    badge.value = audit.badge if audit is not None else f"{label} - no audit run"
    badge.font = Font(size=10, bold=True, color=text)
    badge.fill = PatternFill("solid", fgColor=fill)
    badge.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[2].height = 22

    # ----------------------------------------------------------- header block
    row = 4
    for field_label, value in (
        ("Vendor", inv.vendor_name),
        ("VAT / org. number", inv.vendor_tax_id),
        ("Invoice number", inv.invoice_no),
        ("Invoice date", inv.invoice_date),
        ("Due date", inv.due_date),
        ("Currency", inv.currency),
    ):
        ws.cell(row=row, column=1, value=field_label).font = Font(bold=True, size=9)
        cell = ws.cell(row=row, column=2, value=value)
        cell.font = Font(size=9)
        if field_label.endswith("date") and value is not None:
            cell.number_format = "yyyy-mm-dd"
        row += 1

    # ------------------------------------------------------- line-item table
    row += 1
    headers = ("#", "Description", "Quantity", f"Unit price {currency}".strip(),
               f"Amount {currency}".strip())
    for col, head in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=col, value=head)
        cell.font = Font(bold=True, size=9)
        cell.fill = PatternFill("solid", fgColor=LIGHT)
        cell.border = _BOX
        cell.alignment = Alignment(
            horizontal="right" if col >= 3 else "left", vertical="center")
    header_row = row
    row += 1

    first_item = row
    for index, item in enumerate(inv.line_items, start=1):
        ws.cell(row=row, column=1, value=index).font = Font(size=9)
        ws.cell(row=row, column=2, value=item.description).font = Font(size=9)
        qty = ws.cell(row=row, column=3, value=_num(item.quantity))
        qty.number_format = QTY_FMT
        unit = ws.cell(row=row, column=4, value=_num(item.unit_price))
        unit.number_format = MONEY_FMT
        amount = ws.cell(row=row, column=5, value=_num(item.line_total))
        amount.number_format = MONEY_FMT
        for col in range(1, 6):
            ws.cell(row=row, column=col).border = _BOX
            ws.cell(row=row, column=col).font = Font(size=9)
        row += 1
    last_item = row - 1

    # ---------------------------------------------------------------- totals
    row += 1
    total_rows = (
        ("Subtotal", _num(inv.subtotal),
         f"=SUM(E{first_item}:E{last_item})" if inv.line_items else None),
        (f"Tax{f' {inv.tax_rate}%' if inv.tax_rate is not None else ''}",
         _num(inv.tax_amount), None),
        ("TOTAL", _num(inv.total_amount), None),
    )
    for offset, (field_label, value, formula) in enumerate(total_rows):
        bold = field_label == "TOTAL"
        label_cell = ws.cell(row=row, column=4, value=field_label)
        label_cell.font = Font(bold=True, size=11 if bold else 9)
        label_cell.alignment = Alignment(horizontal="right")
        value_cell = ws.cell(row=row, column=5, value=value)
        value_cell.number_format = MONEY_FMT
        value_cell.font = Font(bold=bold, size=11 if bold else 9)
        value_cell.border = _BOX
        if bold:
            value_cell.fill = PatternFill("solid", fgColor=LIGHT)
        # Live cross-check next to the subtotal: the sheet re-adds the column
        # itself, so an edit in Excel cannot quietly disagree with the export.
        if formula:
            check = ws.cell(row=row, column=7, value=formula)
            check.number_format = MONEY_FMT
            check.font = Font(size=9, italic=True, color="6B7A88")
            ws.cell(row=row, column=6, value="sum of rows:").font = Font(
                size=9, italic=True, color="6B7A88")
        row += 1

    # ------------------------------------------------------------ audit detail
    row += 2
    ws.cell(row=row, column=1, value="Audit trail").font = Font(bold=True, size=10)
    row += 1
    lines: List[tuple[str, str]] = []
    if audit is not None:
        lines += [("verified", c) for c in audit.checks]
        lines += [("FAILED", f) for f in audit.failures]
        lines += [("warning", w) for w in audit.warnings]
    lines += [("note", n) for n in inv.extraction_notes]
    lines.append(("route", result.route))
    lines.append(("latency", f"{result.latency_seconds:.2f}s for "
                             f"{result.pages_processed} page(s)"))
    for kind, message in lines:
        ws.cell(row=row, column=1, value=kind).font = Font(
            size=8, bold=kind == "FAILED",
            color=FAIL_TEXT if kind == "FAILED" else "6B7A88")
        detail = ws.cell(row=row, column=2, value=message)
        detail.font = Font(size=8, color="3C4A57")
        detail.alignment = Alignment(wrap_text=False)
        row += 1

    for col, width in ((1, 13), (2, 52), (3, 11), (4, 15), (5, 16), (6, 13), (7, 14)):
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    return wb


def to_xlsx_bytes(result: "ExtractionResult") -> bytes:
    """Workbook as bytes, for a Streamlit download button."""
    buf = io.BytesIO()
    build_workbook(result).save(buf)
    return buf.getvalue()


def write_xlsx(result: "ExtractionResult", path: Path | str) -> Path:
    path = Path(path)
    build_workbook(result).save(path)
    return path


def to_csv_text(invoice: "InvoiceData") -> str:
    """Line items as CSV, for a quick paste into an ERP import screen."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["description", "quantity", "unit_price", "line_total"])
    for item in invoice.line_items:
        writer.writerow([
            item.description,
            "" if item.quantity is None else item.quantity,
            "" if item.unit_price is None else item.unit_price,
            "" if item.line_total is None else item.line_total,
        ])
    return buf.getvalue()


def build_batch_workbook(results: List["ExtractionResult"]) -> Workbook:
    """One row per document: the master sheet a controller reconciles against.

    This is the V2 batch feature in embryo -- it already works for any list of
    results, so a ZIP upload only needs to produce that list.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.sheet_view.showGridLines = False

    headers = ("File", "Vendor", "Invoice no.", "Date", "Currency", "Subtotal",
               "Tax", "Total", "Audit", "Discrepancy", "Route", "Cost USD")
    for col, head in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col, value=head)
        cell.font = Font(bold=True, size=9, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.border = _BOX

    for row_index, result in enumerate(results, start=2):
        inv, audit = result.invoice, result.audit
        _, text, label = _audit_colours(audit)
        values: List[Any] = [
            result.source_path.name, inv.vendor_name, inv.invoice_no,
            inv.invoice_date, inv.currency, _num(inv.subtotal),
            _num(inv.tax_amount), _num(inv.total_amount), label,
            _num(audit.total_discrepancy) if audit else None,
            result.strategy, round(result.cost_usd, 6),
        ]
        for col, value in enumerate(values, start=1):
            cell = ws.cell(row=row_index, column=col, value=value)
            cell.font = Font(size=9, color=text if col == 9 else "000000",
                             bold=col == 9)
            cell.border = _BOX
            if col in (6, 7, 8, 10):
                cell.number_format = MONEY_FMT
            if col == 4 and value is not None:
                cell.number_format = "yyyy-mm-dd"
            if col == 12:
                cell.number_format = "0.00000"

    for col, width in ((1, 26), (2, 30), (3, 16), (4, 12), (5, 10), (6, 14),
                       (7, 14), (8, 14), (9, 28), (10, 13), (11, 13), (12, 11)):
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.freeze_panes = "A2"
    return wb
