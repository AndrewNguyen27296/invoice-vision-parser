"""
Strict Pydantic contracts for extracted invoice data + deterministic math audit.

ARCHITECTURAL INVARIANT #2 (Strict Pydantic Type Enforcement):
    Nothing leaves this module unvalidated. If the model hallucinates a string
    where a number belongs, coercion happens here under our rules -- or the
    document is flagged, never silently accepted.

ARCHITECTURAL INVARIANT #3 (Deterministic Math Auditing):
    We NEVER trust the LLM's arithmetic. Every number it returns is re-added
    with Decimal precision in `audit()`. The green badge in the UI is earned by
    Python, not asserted by the model.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Discrepancy at or below this is considered a rounding artefact, not an error.
TOLERANCE = Decimal("0.05")

_CURRENCY_NOISE = re.compile(r"[^\d,.\-()]")


def to_decimal(value: Any) -> Optional[Decimal]:
    """Coerce whatever the model returned into a Decimal, or None.

    Handles the real-world mess found on invoices:
        "1 450,00"  "$1,450.00"  "1.450,00 SEK"  "(230.00)"  "1450"
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))

    text = str(value).strip()
    if not text:
        return None

    negative = text.startswith("(") and text.endswith(")")
    text = _CURRENCY_NOISE.sub("", text).strip("()")
    if not text:
        return None

    # Decide which separator is the decimal point.
    if "," in text and "." in text:
        # The rightmost separator wins: "1.450,00" -> comma, "1,450.00" -> dot
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        # "1450,00" -> decimal comma;  "1,450" -> thousands separator
        tail = text.split(",")[-1]
        text = text.replace(",", "." if len(tail) in (1, 2) else "")

    try:
        result = Decimal(text)
    except InvalidOperation:
        return None
    return -result if negative else result


def q2(value: Optional[Decimal]) -> Optional[Decimal]:
    """Quantise to 2 decimal places for display and comparison."""
    return None if value is None else value.quantize(Decimal("0.01"))


class LineItem(BaseModel):
    """One row of the invoice table."""

    model_config = ConfigDict(str_strip_whitespace=True)

    description: str = Field(..., description="Goods or service description as printed")
    quantity: Optional[Decimal] = Field(None, description="Units billed")
    unit_price: Optional[Decimal] = Field(None, description="Price per unit, pre-tax")
    line_total: Optional[Decimal] = Field(None, description="Extended amount for the row")

    @field_validator("quantity", "unit_price", "line_total", mode="before")
    @classmethod
    def _coerce_money(cls, v: Any) -> Any:
        return to_decimal(v)

    @field_validator("description", mode="before")
    @classmethod
    def _coerce_description(cls, v: Any) -> Any:
        return "(no description)" if v in (None, "") else str(v)

    @property
    def implied_total(self) -> Optional[Decimal]:
        """quantity x unit_price, when both are present."""
        if self.quantity is None or self.unit_price is None:
            return None
        return self.quantity * self.unit_price

    @property
    def row_discrepancy(self) -> Optional[Decimal]:
        """|qty x price - line_total|, when both sides are known."""
        implied = self.implied_total
        if implied is None or self.line_total is None:
            return None
        return abs(implied - self.line_total)


class MathAudit(BaseModel):
    """The deterministic verdict. Computed by Python, never by the model."""

    passed: bool
    summed_line_items: Optional[Decimal] = None
    reported_subtotal: Optional[Decimal] = None
    subtotal_discrepancy: Optional[Decimal] = None
    reported_tax: Optional[Decimal] = None
    reported_total: Optional[Decimal] = None
    computed_total: Optional[Decimal] = None
    total_discrepancy: Optional[Decimal] = None
    tolerance: Decimal = TOLERANCE
    circular: bool = Field(
        False,
        description="True when the only subtotal+tax=total check available was an "
                    "identity this software created, and therefore proves nothing.",
    )
    checks: List[str] = Field(default_factory=list)
    failures: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    @property
    def badge(self) -> str:
        """One-line verdict for the Streamlit banner and the CLI."""
        if self.failures:
            return f"MATH AUDIT FAILED - {self.failures[0]}"
        if not self.checks:
            if self.circular:
                return ("MATH AUDIT INCONCLUSIVE - a missing figure was back-calculated, "
                        "so no independent check was possible")
            return "MATH AUDIT INCONCLUSIVE - not enough numbers extracted"
        disc = self.total_discrepancy if self.total_discrepancy is not None else Decimal("0")
        return f"Math Audit Passed - {len(self.checks)} check(s), {disc} discrepancy"


class InvoiceData(BaseModel):
    """The full validated extraction result."""

    model_config = ConfigDict(str_strip_whitespace=True)

    vendor_name: Optional[str] = None
    vendor_tax_id: Optional[str] = None
    invoice_no: Optional[str] = None
    invoice_date: Optional[date] = None
    due_date: Optional[date] = None
    currency: Optional[str] = Field(None, description="ISO 4217 code, e.g. SEK, EUR, USD")
    line_items: List[LineItem] = Field(default_factory=list)
    subtotal: Optional[Decimal] = None
    tax_rate: Optional[Decimal] = Field(None, description="Percentage, e.g. 25 for 25%")
    tax_amount: Optional[Decimal] = None
    total_amount: Optional[Decimal] = None

    # Populated by the pipeline, not by the model.
    audit: Optional[MathAudit] = None
    extraction_notes: List[str] = Field(default_factory=list)
    derived_fields: List[str] = Field(
        default_factory=list,
        description="Fields this software back-calculated because the model did not "
                    "return them. Any audit check that consumes one of these is an "
                    "identity, not evidence.",
    )

    @model_validator(mode="before")
    @classmethod
    def _note_unknown_keys(cls, data: Any) -> Any:
        """Record any field name the model invented instead of dropping it silently.

        Pydantic's default is extra="ignore". For an extraction product that is the
        worst possible default: a model that answers {"grand_total": 5000} produces a
        perfectly valid all-null InvoiceData and nobody is told a number was thrown
        away. We cannot use the value safely, but we can refuse to hide the loss.
        """
        if not isinstance(data, dict):
            return data
        unknown = sorted(k for k in data if k not in cls.model_fields)
        if not unknown:
            return data
        data = dict(data)
        notes = list(data.get("extraction_notes") or [])
        shown = ", ".join(unknown[:8]) + (" ..." if len(unknown) > 8 else "")
        notes.append(f"model returned {len(unknown)} unrecognised field(s), discarded: {shown}")
        data["extraction_notes"] = notes
        return data

    @field_validator("subtotal", "tax_rate", "tax_amount", "total_amount", mode="before")
    @classmethod
    def _coerce_money(cls, v: Any) -> Any:
        return to_decimal(v)

    @field_validator("currency", mode="before")
    @classmethod
    def _normalise_currency(cls, v: Any) -> Any:
        if not v:
            return None
        symbols = {"$": "USD", "€": "EUR", "£": "GBP", "kr": "SEK", "¥": "JPY"}
        text = str(v).strip()
        return symbols.get(text, text.upper()[:3])

    @field_validator("invoice_date", "due_date", mode="before")
    @classmethod
    def _parse_date(cls, v: Any) -> Any:
        """Accept the handful of formats vendors actually print."""
        if v in (None, "", "null"):
            return None
        if isinstance(v, date):
            return v
        text = str(v).strip()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%d %b %Y",
                    "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%Y/%m/%d", "%d-%m-%Y"):
            try:
                from datetime import datetime
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        return None  # degrade gracefully; never crash on a weird date

    @model_validator(mode="after")
    def _derive_missing(self) -> "InvoiceData":
        """Fill in what is arithmetically implied, and note that we did."""
        if self.subtotal is None and self.total_amount is not None and self.tax_amount is not None:
            object.__setattr__(self, "subtotal", self.total_amount - self.tax_amount)
            self.extraction_notes.append("subtotal derived as total - tax")
            self.derived_fields.append("subtotal")
        if self.tax_amount is None and self.total_amount is not None and self.subtotal is not None:
            object.__setattr__(self, "tax_amount", self.total_amount - self.subtotal)
            self.extraction_notes.append("tax_amount derived as total - subtotal")
            self.derived_fields.append("tax_amount")
        return self

    # ------------------------------------------------------------------ audit
    def run_audit(self, tolerance: Decimal = TOLERANCE) -> MathAudit:
        """Re-do every calculation in Decimal. This is the product's trust anchor."""
        checks: List[str] = []
        failures: List[str] = []
        warnings: List[str] = []
        circular = False

        totals = [li.line_total for li in self.line_items if li.line_total is not None]
        summed = sum(totals, Decimal("0")) if totals else None

        subtotal_disc: Optional[Decimal] = None
        if summed is not None and self.subtotal is not None:
            subtotal_disc = abs(summed - self.subtotal)
            label = f"sum(line items) {q2(summed)} vs subtotal {q2(self.subtotal)}"
            (checks if subtotal_disc <= tolerance else failures).append(
                label if subtotal_disc <= tolerance else f"{label} differs by {q2(subtotal_disc)}"
            )
        elif summed is None:
            warnings.append("no line-item amounts extracted; row-level check skipped")

        computed_total: Optional[Decimal] = None
        total_disc: Optional[Decimal] = None
        if self.subtotal is not None and self.tax_amount is not None:
            computed_total = self.subtotal + self.tax_amount
            if self.total_amount is not None:
                label = (f"subtotal {q2(self.subtotal)} + tax {q2(self.tax_amount)} "
                         f"= {q2(computed_total)} vs total {q2(self.total_amount)}")
                if self.derived_fields:
                    # We back-calculated one of these three numbers from the other two,
                    # so subtotal + tax == total by construction. Reporting that as a
                    # passing check would be this product lying about its one promise.
                    circular = True
                    warnings.append(
                        f"subtotal + tax = total NOT verified: "
                        f"{' and '.join(self.derived_fields)} was back-calculated from "
                        f"the total, so this check cannot fail. Treat these figures as "
                        f"unconfirmed."
                    )
                else:
                    total_disc = abs(computed_total - self.total_amount)
                    (checks if total_disc <= tolerance else failures).append(
                        label if total_disc <= tolerance
                        else f"{label} differs by {q2(total_disc)}"
                    )
            else:
                warnings.append("total_amount missing; subtotal+tax check skipped")
        else:
            warnings.append("subtotal or tax_amount missing; total check skipped")

        # Stated tax rate sanity check (soft: rounding on rates is common).
        if self.tax_rate is not None and self.subtotal is not None and self.tax_amount is not None:
            expected_tax = (self.subtotal * self.tax_rate / Decimal("100"))
            if abs(expected_tax - self.tax_amount) > max(tolerance, self.tax_amount.copy_abs() * Decimal("0.01")):
                warnings.append(
                    f"stated rate {self.tax_rate}% implies tax {q2(expected_tax)}, "
                    f"but {q2(self.tax_amount)} was printed"
                )
            else:
                checks.append(f"tax rate {self.tax_rate}% consistent with tax amount")

        # Per-row arithmetic.
        for idx, li in enumerate(self.line_items, start=1):
            disc = li.row_discrepancy
            if disc is not None and disc > tolerance:
                warnings.append(
                    f"row {idx} ({li.description[:32]}): qty x price = "
                    f"{q2(li.implied_total)} but row shows {q2(li.line_total)}"
                )

        audit = MathAudit(
            passed=not failures and bool(checks),
            summed_line_items=q2(summed),
            reported_subtotal=q2(self.subtotal),
            subtotal_discrepancy=q2(subtotal_disc),
            reported_tax=q2(self.tax_amount),
            reported_total=q2(self.total_amount),
            computed_total=q2(computed_total),
            total_discrepancy=q2(total_disc),
            tolerance=tolerance,
            circular=circular,
            checks=checks,
            failures=failures,
            warnings=warnings,
        )
        object.__setattr__(self, "audit", audit)
        return audit
