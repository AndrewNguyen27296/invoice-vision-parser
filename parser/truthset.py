"""
The truth set: what these documents ACTUALLY say.

WHY THIS EXISTS
    Everything measured so far was measured against three invoices this project
    generated itself, with a parser developed against those same three files.
    That is not an accuracy measurement, it is a fit to a training set of three.
    Any number quoted from it -- "100%", "99%+" -- is a claim, and the first real
    invoice that breaks it will break it in front of a prospect.

    This module holds ground truth for REAL documents, so accuracy can be
    measured instead of asserted.

THE DESIGN PROBLEM: RECORDING TRUTH IS TEDIOUS
    Typing 12 fields for 20 invoices by hand is an hour of work nobody does, so
    it never gets done and the number stays unmeasured. Two things fix that:

      1. Bootstrap, then correct. The extractor pre-fills a stub and you only
         fix what is wrong -- usually a field or two.
      2. Truth as a byproduct. The Streamlit app already shows an editable
         table, and reviewing each invoice is work you would do anyway. One
         button turns that review into a permanent truth record.

    But bootstrapping is dangerous: pre-filling from the extractor biases you
    toward accepting its mistakes, which would flatter the score. So every
    record carries `verified`, nothing counts until a human has explicitly
    confirmed it against the document, and the provenance of each record is
    stored alongside it.

NULL VERSUS UNKNOWN -- the distinction that keeps the score honest
    `null`  the field is genuinely ABSENT from the document (no due date
            printed). Extracting nothing is correct, and this IS scored.
    `"?"`   you did not check, or it is not applicable. NOT scored at all.
            Never counted as a pass or a failure.

    Conflating those two is how an accuracy figure quietly inflates.

PRIVACY
    Real invoices carry real vendor names, bank details and prices. The folder
    and this file are gitignored, and nothing here is ever sent anywhere. Be
    deliberate about which provider you point at a real client's document --
    see the free-tier warning in .env.example.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from .pdf_utils import file_fingerprint
from .schemas import InvoiceData, q2, to_decimal

#: Sentinel meaning "not checked / not applicable". Excluded from scoring.
UNKNOWN = "?"

#: The fields a truth record can carry. Chosen as what an AP clerk would have
#: had to type by hand -- which is exactly what the product claims to replace.
TRUTH_FIELDS = (
    "vendor_name",
    "vendor_tax_id",
    "invoice_no",
    "invoice_date",
    "due_date",
    "currency",
    "subtotal",
    "tax_rate",
    "tax_amount",
    "total_amount",
    "line_item_count",
)

MONEY_FIELDS = ("subtotal", "tax_rate", "tax_amount", "total_amount")
#: Overridable so a test run or a demo can never write into the real truth set.
DEFAULT_STORE = Path(
    os.getenv("PARSER_TRUTH_STORE") or (Path("real_invoices") / "truthset.json")
)


@dataclass
class TruthRecord:
    """Ground truth for one document, keyed by the hash of its bytes."""

    fingerprint: str
    file_name: str
    fields: Dict[str, Any] = field(default_factory=dict)
    verified: bool = False
    verified_at: Optional[str] = None
    source: str = "stub"          # stub | app-editor | manual
    notes: str = ""

    def to_json(self) -> Dict[str, Any]:
        return {
            "file_name": self.file_name,
            "verified": self.verified,
            "verified_at": self.verified_at,
            "source": self.source,
            "notes": self.notes,
            "fields": self.fields,
        }

    @classmethod
    def from_json(cls, fingerprint: str, payload: Dict[str, Any]) -> "TruthRecord":
        return cls(
            fingerprint=fingerprint,
            file_name=payload.get("file_name", ""),
            fields=dict(payload.get("fields") or {}),
            verified=bool(payload.get("verified")),
            verified_at=payload.get("verified_at"),
            source=payload.get("source", "stub"),
            notes=payload.get("notes", ""),
        )

    def scoreable_fields(self) -> List[str]:
        """Fields with a real answer recorded. `UNKNOWN` is skipped entirely."""
        return [
            name for name in TRUTH_FIELDS
            if name in self.fields and self.fields[name] != UNKNOWN
        ]

    @property
    def completeness(self) -> float:
        return len(self.scoreable_fields()) / len(TRUTH_FIELDS)


class TruthSet:
    """A JSON-backed collection of truth records, keyed by file content hash.

    Keying on the hash rather than the filename means renaming or moving a
    document does not orphan its truth, and re-adding the same invoice twice
    cannot create two conflicting records.
    """

    def __init__(self, path: Path | str = DEFAULT_STORE) -> None:
        self.path = Path(path)
        self.records: Dict[str, TruthRecord] = {}
        self.load()

    # ------------------------------------------------------------------- io
    def load(self) -> "TruthSet":
        if not self.path.exists():
            self.records = {}
            return self
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(
                f"The truth set at {self.path} could not be read ({exc}). "
                "Fix or move that file -- refusing to silently start from empty, "
                "which would report a fresh 0/0 as if nothing had been verified."
            ) from exc
        self.records = {
            fingerprint: TruthRecord.from_json(fingerprint, record)
            for fingerprint, record in (payload.get("records") or {}).items()
        }
        return self

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "_comment": (
                "Ground truth for real invoices. NEVER COMMIT THIS FILE -- it "
                "describes real vendor documents. Keyed by SHA-256 of the file "
                "bytes. Set a field to \"?\" to exclude it from scoring; use "
                "null only when the field is genuinely absent from the document."
            ),
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "records": {fp: r.to_json() for fp, r in sorted(self.records.items())},
        }
        tmp = self.path.parent / (self.path.name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                       encoding="utf-8")
        os.replace(tmp, self.path)
        return self.path

    # ---------------------------------------------------------------- access
    def get(self, path: Path | str) -> Optional[TruthRecord]:
        return self.records.get(file_fingerprint(Path(path)))

    def put(self, record: TruthRecord) -> None:
        self.records[record.fingerprint] = record

    def verified_records(self) -> List[TruthRecord]:
        return [r for r in self.records.values() if r.verified]

    def unverified_records(self) -> List[TruthRecord]:
        return [r for r in self.records.values() if not r.verified]

    def __len__(self) -> int:
        return len(self.records)


# --------------------------------------------------------------- extraction
def fields_from_invoice(invoice: InvoiceData) -> Dict[str, Any]:
    """Flatten an extraction into truth-record shape, for bootstrapping a stub."""
    out: Dict[str, Any] = {}
    for name in TRUTH_FIELDS:
        if name == "line_item_count":
            out[name] = len(invoice.line_items)
            continue
        value = getattr(invoice, name, None)
        if isinstance(value, Decimal):
            # Money is written at two decimals so the JSON stays readable for
            # the human who has to correct it. Comparison is numeric either way,
            # so "250" and "250.00" are the same answer.
            out[name] = str(q2(value) if name in MONEY_FIELDS else value)
        elif value is None:
            out[name] = None
        else:
            out[name] = str(value)
    return out


def stub_from_extraction(path: Path | str, invoice: InvoiceData,
                         fingerprint: Optional[str] = None) -> TruthRecord:
    """A pre-filled, explicitly UNVERIFIED record for a human to correct.

    Deliberately `verified=False`: a stub is the extractor's opinion of itself
    and must never be scored until someone has read the document.
    """
    path = Path(path)
    return TruthRecord(
        fingerprint=fingerprint or file_fingerprint(path),
        file_name=path.name,
        fields=fields_from_invoice(invoice),
        verified=False,
        source="stub",
        notes="PRE-FILLED BY THE EXTRACTOR. Check every field against the "
              "document, then set verified to true.",
    )


# ----------------------------------------------------------------- matching
def _norm_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = "".join(str(value).split()).upper()
    return text or None


def compare_field(name: str, expected: Any, actual: Any,
                  money_tolerance: Decimal = Decimal("0.005")) -> bool:
    """Is the extraction right about this one field?

    Comparison is normalised the way a human would judge it: whitespace and
    case are irrelevant for text, money is compared numerically to half a cent,
    and dates are compared as dates.
    """
    if expected == UNKNOWN:
        raise ValueError("UNKNOWN fields must be filtered out before comparing")

    if name == "line_item_count":
        try:
            return int(expected) == int(actual)
        except (TypeError, ValueError):
            return False

    if name in MONEY_FIELDS:
        want, got = to_decimal(expected), to_decimal(actual)
        if want is None or got is None:
            return want is None and got is None
        return abs(want - got) <= money_tolerance

    if name in ("invoice_date", "due_date"):
        # Both sides pass through the schema's own date parsing, so "04.09.2026"
        # and "2026-09-04" are the same answer.
        probe = InvoiceData.model_validate({"invoice_date": expected})
        want = probe.invoice_date
        got = actual if actual is None else InvoiceData.model_validate(
            {"invoice_date": str(actual)}).invoice_date
        return want == got

    return _norm_text(expected) == _norm_text(actual)
