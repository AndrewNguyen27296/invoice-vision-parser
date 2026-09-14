"""
Invoice & Vision Parser — the side-by-side extraction demo.

THE 60 SECONDS THIS PAGE EXISTS FOR
    A finance lead picks a sample. Within a few seconds the document is on the
    left and a structured, editable, arithmetically audited table is on the
    right. They correct a figure by hand and watch the verdict change in real
    time — because the audit is deterministic Python, not a model opinion. Then
    one click gives them a formatted Excel workbook.

FOUR RULES THIS FILE KEEPS
    1. Three audit states, three colours. Green means Python independently
       re-added the numbers and they agreed. Amber means a figure had to be
       back-calculated, so nothing independent could be checked — that is NOT a
       pass and must never be painted green.
    2. Never show confidently wrong data. If a document cannot be extracted,
       the page says so. It does not fall back to a plausible-looking answer.
    3. Say what each answer cost and how it was obtained. Most documents are
       read from the PDF's own text layer for nothing — "we only pay the model
       for scans" is the commercial argument, so it belongs on screen.
    4. A visitor's own upload never goes to a provider that trains on it.

Run it:  streamlit run app.py
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from parser.cost_guard import BudgetExceeded, CostGuard, GuardConfig
from parser.extractor import (
    STRATEGY_TEXT_LAYER,
    ExtractionError,
    ExtractionResult,
    extract_invoice,
)
from parser.providers import ProviderError, available_providers, get_provider
from parser.schemas import InvoiceData
from parser.truthset import TruthRecord, TruthSet, fields_from_invoice
from utils.exporter import to_csv_text, to_xlsx_bytes

ROOT = Path(__file__).resolve().parent
SAMPLES = ROOT / "sample_invoices"
REAL = ROOT / "real_invoices"
UPLOAD_DIR = ROOT / ".cache" / "uploads"
TRUTH_STORE = Path(os.getenv("PARSER_TRUTH_STORE") or (REAL / "truthset.json"))

IMAGE_TYPES = ["pdf", "png", "jpg", "jpeg", "webp", "tif", "tiff"]

SAMPLE_LABELS = {
    "1_clean_freight.pdf": "Carrier invoice — clean, 4 line items",
    "2_messy_wholesale.pdf": "Wholesale invoice — 11 rows, decimal commas, has an error",
    "3_skewed_scan.pdf": "Corporate travel — skewed phone scan, no text layer",
}

st.set_page_config(
    page_title="Invoice & Vision Parser",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Palette matches .streamlit/config.toml, which pins the light theme so these
# semantic fills are always read against the ground they were designed for.
st.markdown(
    """
    <style>
      .block-container {padding-top: 2rem; padding-bottom: 4rem; max-width: 1500px;}
      h1 {font-size: 1.9rem !important; letter-spacing: -.02em; margin-bottom: .1rem;}
      .lede {color:#5A6B7B; font-size:.97rem; margin:0 0 1.1rem 0; max-width: 62ch;}

      .verdict {padding:.85rem 1.05rem; border-radius:.55rem; border-left:.35rem solid;
                margin:0 0 .5rem 0; line-height:1.35;}
      .verdict .headline {font-weight:650; font-size:1rem; display:block;}
      .verdict .detail {font-weight:400; font-size:.85rem; opacity:.92;}
      .v-pass {background:#E8F6ED; color:#0F5C34; border-color:#159A57;}
      .v-fail {background:#FDEAEA; color:#8E1A1A; border-color:#D33A3A;}
      .v-warn {background:#FEF5E5; color:#7A4A00; border-color:#E0A126;}

      .strip {display:flex; flex-wrap:wrap; gap:.45rem; margin:.1rem 0 1.1rem 0;}
      .chip {background:#F4F7FA; border:1px solid #E2E9F0; color:#43566A;
             border-radius:1rem; padding:.2rem .7rem; font-size:.78rem;
             white-space:nowrap;}
      .chip.free {background:#E8F6ED; border-color:#BFE3CE; color:#0F5C34;}
      .chip.canned {background:#FEF5E5; border-color:#F0DCB4; color:#7A4A00;}

      .panel {border:1px solid #E2E9F0; border-radius:.55rem; padding:1rem 1.15rem;
              background:#FBFDFF;}
      .muted {color:#6B7A88; font-size:.82rem;}
      section[data-testid="stSidebar"] {width: 21rem !important;}
      div[data-testid="stMetricValue"] {font-size:1.15rem;}
      /* Columns stack on narrow screens; keep the gutter honest when they do. */
      @media (max-width: 800px) { .block-container {padding-top:1.2rem;} }
    </style>
    """,
    unsafe_allow_html=True,
)


# ------------------------------------------------------------------- helpers
def get_guard() -> CostGuard:
    """One guard per browser session.

    This must live in session_state: a fresh CostGuard on every rerun would
    reset session_pages to zero and the per-visitor cap would never fire. Note
    it is still only a courtesy cap — a visitor can clear it by reloading, so
    PARSER_DAILY_* are the limits a stranger cannot clear.
    """
    if "guard" not in st.session_state:
        st.session_state.guard = CostGuard(GuardConfig())
    return st.session_state.guard


def verdict_block(audit) -> None:
    """Three states, three colours. Amber is not a pass."""
    if audit.passed:
        css, headline = "v-pass", "Math verified"
    elif audit.failures:
        css, headline = "v-fail", "Math audit failed"
    else:
        css, headline = "v-warn", "Inconclusive — not verified"
    st.markdown(
        f'<div class="verdict {css}"><span class="headline">{headline}</span>'
        f'<span class="detail">{audit.badge}</span></div>',
        unsafe_allow_html=True,
    )


def route_chips(result: ExtractionResult) -> None:
    chips: List[str] = []
    if result.strategy == STRATEGY_TEXT_LAYER:
        chips.append('<span class="chip free">PDF text layer · no model call</span>')
    elif result.canned:
        chips.append('<span class="chip canned">Canned demo fixture · no model call</span>')
    else:
        chips.append(f'<span class="chip">Vision model · {result.provider}</span>')
    chips.append(f'<span class="chip{" free" if result.was_free else ""}">'
                 f'${result.cost_usd:.5f}</span>')
    chips.append(f'<span class="chip">{result.latency_seconds:.2f}s</span>')
    chips.append(f'<span class="chip">{result.pages_processed} page'
                 f'{"s" if result.pages_processed != 1 else ""}</span>')
    if result.text_layer_coverage is not None:
        chips.append(f'<span class="chip">text-layer coverage '
                     f'{result.text_layer_coverage:.0%}</span>')
    st.markdown(f'<div class="strip">{"".join(chips)}</div>', unsafe_allow_html=True)


def rebuild(result: ExtractionResult, edited: Dict[str, Any]) -> ExtractionResult:
    """Re-validate and re-audit after a hand edit. The whole trust loop, live."""
    invoice = InvoiceData.model_validate(edited)
    invoice.extraction_notes.extend(
        n for n in result.invoice.extraction_notes if n not in invoice.extraction_notes
    )
    return dataclasses.replace(result, invoice=invoice, audit=invoice.run_audit())


# ------------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown("### Document")
    choice = st.radio(
        "Sample invoices",
        options=list(SAMPLE_LABELS),
        format_func=lambda k: SAMPLE_LABELS[k],
        label_visibility="collapsed",
    )
    upload = st.file_uploader(
        "…or drop your own invoice",
        type=IMAGE_TYPES,
        help="Freight, wholesale, travel and professional-services invoices.",
    )

    real_files = (
        sorted(p.name for p in REAL.iterdir()
               if p.is_file() and p.suffix.lower().lstrip(".") in IMAGE_TYPES)
        if REAL.exists() else []
    )
    real_choice = None
    if real_files:
        picked = st.selectbox(
            f"…or a real invoice ({len(real_files)} local)",
            ["— none —"] + real_files,
            help="For building the truth set, not for demoing. Overrides the sample.",
        )
        real_choice = None if picked == "— none —" else picked

    st.divider()
    st.markdown("### Engine")
    providers = available_providers()
    default_provider = os.getenv("PARSER_PROVIDER", "demo")
    provider_name = st.selectbox(
        "Vision fallback",
        providers,
        index=providers.index(default_provider) if default_provider in providers else 0,
        help="Only used for documents with no text layer.",
    )
    use_text_layer = st.toggle(
        "Read the PDF text layer first (free)", value=True,
        help="Digital PDFs already contain their text. Reading it costs nothing and "
             "is more accurate than photographing the page. Vision is used only for scans.",
    )
    force = st.toggle("Ignore cache", value=False)

    try:
        backend = get_provider(provider_name)
        st.caption(backend.describe())
    except ProviderError as exc:
        backend = None
        st.caption(str(exc))

    st.divider()
    st.markdown("### Today")
    guard = get_guard()
    today = guard.today()
    col_a, col_b = st.columns(2)
    col_a.metric("Pages", int(today["pages"]))
    col_b.metric("Spend", f"${today['cost_usd']:.4f}")
    if today["pages"]:
        free_pages = int(today["free_pages"])
        st.caption(f"{free_pages} of {int(today['pages'])} pages "
                   f"({free_pages / today['pages']:.0%}) cost nothing — "
                   "text layer, cache or fixtures.")
    st.progress(min(1.0, today["cost_usd"] / max(guard.config.daily_usd_budget, 1e-9)))
    st.caption(f"Hard stop at ${guard.config.daily_usd_budget:.2f}/day · "
               f"{guard.config.session_page_limit} pages per visitor")
    if guard.cache_dir_fell_back:
        st.caption("⚠️ Spend tracking is not persistent on this host — the app "
                   "directory is not writable, so the ledger resets on restart.")


# ---------------------------------------------------------------------- head
st.title("Zero-manual-entry invoice processing")
st.markdown(
    '<p class="lede">Drop a supplier or carrier invoice and get audited, '
    'structured data. Every figure is re-added in Python before anything turns '
    'green — the verdict is earned, not asserted by a model.</p>',
    unsafe_allow_html=True,
)

# Resolve the document. Upload wins, then a local real invoice, then a sample.
is_upload = upload is not None
if is_upload:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = UPLOAD_DIR / upload.name
    target.write_bytes(upload.getbuffer())
    source = target
elif real_choice:
    source = REAL / real_choice
else:
    source = SAMPLES / choice

# RULE 4: a visitor's own document never goes to a provider that trains on it.
# The bundled samples are fictional, so a free tier is fine for those; an
# upload is somebody's real supplier relationship and is not ours to donate.
if is_upload and backend is not None and getattr(backend, "trains_on_data", False):
    from parser.textlayer import has_text_layer

    if not has_text_layer(source):
        st.warning(
            f"**This upload would be sent to {backend.name}, whose free tier may use "
            "submitted content to improve their products.**\n\n"
            "It has no text layer, so it can only be read by a vision model. The "
            "bundled samples are fictional documents, which is why the free tier is "
            "fine for them — your invoice is not.\n\n"
            "Switch the vision fallback to a paid provider in the sidebar, or tick "
            "below if this particular document is genuinely not confidential."
        )
        if not st.checkbox("This document is not confidential — send it anyway"):
            st.stop()

# ------------------------------------------------------------------- extract
try:
    with st.spinner("Extracting…"):
        result = extract_invoice(
            source, guard=get_guard(), force_refresh=force,
            provider=backend, use_text_layer=use_text_layer,
        )
except BudgetExceeded as exc:
    st.error(f"**A guardrail stopped this before any spend.**\n\n{exc}")
    st.stop()
except ProviderError as exc:
    # RULE 2: no fallback to a plausible answer. Say what is needed instead.
    st.warning(f"**This document needs a vision model.**\n\n{exc}")
    st.caption("Nothing was extracted and nothing was spent. The app will not "
               "show you a guess in place of an answer.")
    st.stop()
except ExtractionError as exc:
    st.error(f"**The model replied with something unusable.**\n\n{exc}")
    st.stop()
except (FileNotFoundError, ValueError) as exc:
    st.error(f"**Cannot read that file.**\n\n{exc}")
    st.stop()

left, right = st.columns([1, 1], gap="large")

# ------------------------------------------------------------------- preview
with left:
    st.markdown("#### Source document")
    if result.page_images:
        for page in result.page_images:
            st.image(page.image, width="stretch",
                     caption=f"Page {page.page_number} · {page.width}×{page.height} px")
    else:
        st.info("No preview available for this file type.")

# --------------------------------------------------------------------- data
with right:
    st.markdown("#### Extracted data")
    verdict_block(result.audit)
    route_chips(result)

    if result.strategy == STRATEGY_TEXT_LAYER:
        st.caption("Read straight from the PDF's own text — no model, no cost, "
                   "no transcription risk. Most invoices an AP team receives "
                   "are digital PDFs like this one.")
    elif result.canned:
        st.caption("This document has no text layer, so it needs a vision model. "
                   "Shown here from a curated fixture so the demo runs without an "
                   "API key — it is a canned answer, not a live extraction.")

    inv = result.invoice
    head_left, head_right = st.columns(2)
    with head_left:
        vendor = st.text_input("Vendor", inv.vendor_name or "")
        invoice_no = st.text_input("Invoice number", inv.invoice_no or "")
        tax_id = st.text_input("VAT / org. number", inv.vendor_tax_id or "")
    with head_right:
        invoice_date = st.text_input(
            "Invoice date", "" if inv.invoice_date is None else str(inv.invoice_date))
        due_date = st.text_input(
            "Due date", "" if inv.due_date is None else str(inv.due_date))
        currency = st.text_input("Currency", inv.currency or "")

    st.markdown("##### Line items")
    frame = pd.DataFrame(
        [{
            "description": item.description,
            "quantity": None if item.quantity is None else float(item.quantity),
            "unit_price": None if item.unit_price is None else float(item.unit_price),
            "line_total": None if item.line_total is None else float(item.line_total),
        } for item in inv.line_items]
        or [{"description": "", "quantity": None, "unit_price": None, "line_total": None}]
    )
    edited_rows = st.data_editor(
        frame, width="stretch", num_rows="dynamic", hide_index=True,
        column_config={
            "description": st.column_config.TextColumn("Description", width="large"),
            "quantity": st.column_config.NumberColumn("Qty", format="%.3f"),
            "unit_price": st.column_config.NumberColumn("Unit price", format="%.2f"),
            "line_total": st.column_config.NumberColumn("Amount", format="%.2f"),
        },
        key="line_items_editor",
    )

    tot_a, tot_b, tot_c, tot_d = st.columns(4)
    with tot_a:
        subtotal = st.number_input(
            "Subtotal", value=None if inv.subtotal is None else float(inv.subtotal),
            format="%.2f", step=1.0)
    with tot_b:
        tax_rate = st.number_input(
            "Tax %", value=None if inv.tax_rate is None else float(inv.tax_rate),
            format="%.2f", step=1.0)
    with tot_c:
        tax_amount = st.number_input(
            "Tax amount", value=None if inv.tax_amount is None else float(inv.tax_amount),
            format="%.2f", step=1.0)
    with tot_d:
        total_amount = st.number_input(
            "Total", value=None if inv.total_amount is None else float(inv.total_amount),
            format="%.2f", step=1.0)

    # Re-validate and re-audit whatever is on screen right now. Correct a figure
    # by hand and the verdict below changes immediately, deterministically.
    live = rebuild(result, {
        "vendor_name": vendor or None,
        "vendor_tax_id": tax_id or None,
        "invoice_no": invoice_no or None,
        "invoice_date": invoice_date or None,
        "due_date": due_date or None,
        "currency": currency or None,
        "line_items": [
            {
                "description": row.get("description") or "(no description)",
                "quantity": row.get("quantity"),
                "unit_price": row.get("unit_price"),
                "line_total": row.get("line_total"),
            }
            for row in edited_rows.to_dict("records")
            if str(row.get("description") or "").strip() or row.get("line_total") is not None
        ],
        "subtotal": subtotal,
        "tax_rate": tax_rate,
        "tax_amount": tax_amount,
        "total_amount": total_amount,
    })

    st.markdown("##### Audit after your edits")
    verdict_block(live.audit)
    with st.expander("Audit trail", expanded=not live.audit.passed):
        for check in live.audit.checks:
            st.markdown(f"- ✅ {check}")
        for failure in live.audit.failures:
            st.markdown(f"- ❌ **{failure}**")
        for warning in live.audit.warnings:
            st.markdown(f"- ⚠️ {warning}")
        for note in live.invoice.extraction_notes:
            st.caption(note)

    st.write("")
    stem = Path(result.source_path).stem
    dl_a, dl_b = st.columns(2)
    dl_a.download_button(
        "⬇  Download formatted Excel", data=to_xlsx_bytes(live),
        file_name=f"{stem}_extracted.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch", type="primary",
    )
    dl_b.download_button(
        "⬇  Line items as CSV", data=to_csv_text(live.invoice),
        file_name=f"{stem}_line_items.csv", mime="text/csv", width="stretch",
    )

    # ---------------------------------------------------------- truth capture
    # Recording ground truth by hand is an hour of typing nobody does, so the
    # number never gets measured. But checking the table above against the
    # document on the left is work you would do anyway — so one button turns
    # that review into a permanent truth record. The confirmation checkbox is
    # not decoration: without it you would be saving the extractor's own output
    # as the answer it is graded against, and the score would read ~100% forever.
    st.divider()
    with st.expander("📌  Record this as ground truth", expanded=False):
        truth: Optional[TruthSet]
        try:
            truth = TruthSet(TRUTH_STORE)
            existing = truth.get(source)
        except RuntimeError as exc:
            truth, existing = None, None
            st.error(str(exc))

        if existing is not None and existing.verified:
            st.success(f"Already verified ({existing.verified_at}, via "
                       f"{existing.source}). Saving again replaces it.")
        elif existing is not None:
            st.info("An unverified stub exists for this file. Fix the table above, "
                    "then confirm and save.")

        st.caption(
            "Fix every field above so it matches the document on the left, then "
            "confirm. This is what turns an accuracy claim into a measurement — "
            "see `real_invoices/README.md`."
        )
        confirmed = st.checkbox(
            "I have checked every field above against the document itself",
            key="truth_confirmed")
        note = st.text_input("Note (optional)", key="truth_note",
                             placeholder="e.g. multi-page, handwritten total, credit note")

        if st.button("Save as verified ground truth",
                     disabled=not confirmed or truth is None, width="stretch"):
            from datetime import datetime

            from parser.pdf_utils import file_fingerprint
            truth.put(TruthRecord(
                fingerprint=file_fingerprint(source),
                file_name=Path(source).name,
                fields=fields_from_invoice(live.invoice),
                verified=True,
                verified_at=datetime.now().isoformat(timespec="seconds"),
                source="app-editor", notes=note,
            ))
            truth.save()
            count = len(truth.verified_records())
            st.success(f"Saved. {count} document(s) now verified — run "
                       "`python scripts/reality_check.py score` for the honest number.")
            if count < 20:
                st.caption(f"{20 - count} more before the sample is worth quoting from.")
        if not confirmed:
            st.caption("Confirm the checkbox to enable saving.")
