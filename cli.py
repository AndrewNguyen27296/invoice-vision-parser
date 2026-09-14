#!/usr/bin/env python3
"""
V0 terminal harness for the AI Invoice & Vision Parser.

    python cli.py sample_invoices/*.pdf              # free for digital PDFs
    python cli.py --mock sample_invoices/1_*.pdf     # canned, no key at all
    python cli.py --estimate sample_invoices/*.pdf   # price without calling
    python cli.py --provider gemini scan.pdf         # free-tier vision fallback
    python cli.py --json invoice.pdf > invoice.json

Digital PDFs are read from their own text layer for nothing. Only documents with
no text layer (scans, photos) reach a vision model. `--no-text-layer` forces
everything through the model, which is how you score the vision route.

Exit codes:  0 clean   1 math audit FAILED   2 budget/guardrail   3 error
             4 math audit INCONCLUSIVE (nothing independent left to check)
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from decimal import Decimal
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from parser.cost_guard import BudgetExceeded, CostGuard, GuardConfig
from parser.extractor import ExtractionError, ExtractionResult, extract_invoice
from parser.pdf_utils import page_count, render_document
from parser.providers import ProviderError, available_providers, get_provider

RULE = "=" * 78


def _fmt(value, dash: str = "-") -> str:
    return dash if value is None else str(value)


def render_report(result: ExtractionResult) -> None:
    inv, audit = result.invoice, result.audit

    print(f"\n{RULE}")
    print(f"  {result.source_path.name}")
    print(RULE)

    print(f"  Vendor       : {_fmt(inv.vendor_name)}")
    print(f"  Tax ID       : {_fmt(inv.vendor_tax_id)}")
    print(f"  Invoice no.  : {_fmt(inv.invoice_no)}")
    print(f"  Invoice date : {_fmt(inv.invoice_date)}")
    print(f"  Due date     : {_fmt(inv.due_date)}")
    print(f"  Currency     : {_fmt(inv.currency)}")

    if inv.line_items:
        print(f"\n  {'#':<3} {'Description':<44} {'Qty':>7} {'Unit':>11} {'Amount':>12}")
        print(f"  {'-' * 3} {'-' * 44} {'-' * 7} {'-' * 11} {'-' * 12}")
        for i, li in enumerate(inv.line_items, 1):
            print(f"  {i:<3} {li.description[:44]:<44} "
                  f"{_fmt(li.quantity):>7} {_fmt(li.unit_price):>11} {_fmt(li.line_total):>12}")
    else:
        print("\n  (no line items extracted)")

    print(f"\n  {'Subtotal':<20}{_fmt(inv.subtotal):>16}")
    rate = f" ({inv.tax_rate}%)" if inv.tax_rate is not None else ""
    print(f"  {'Tax' + rate:<20}{_fmt(inv.tax_amount):>16}")
    print(f"  {'TOTAL':<20}{_fmt(inv.total_amount):>16}")

    # Three states, not two. "We could not check this" is a different answer
    # from "this is wrong", and a finance team must be able to tell them apart.
    if audit.passed:
        verdict = "[ PASS ]"
    elif audit.failures:
        verdict = "[ FAIL ]"
    else:
        verdict = "[ ????]"
    print(f"\n  {verdict} {audit.badge}")
    for check in audit.checks:
        print(f"      ok    {check}")
    for failure in audit.failures:
        print(f"      FAIL  {failure}")
    for warning in audit.warnings:
        print(f"      warn  {warning}")

    latency_flag = "within" if result.within_latency_budget else "OVER"
    print(f"\n  Route        : {result.route}")
    print(f"  Model        : {result.model}"
          f"{'  (cache hit)' if result.from_cache else ''}")
    if result.text_layer_coverage is not None:
        print(f"  Text layer   : {result.text_layer_coverage:.0%} of key fields found")
    print(f"  Latency      : {result.latency_seconds:.2f}s for "
          f"{result.pages_processed} page(s) [{latency_flag} the <5s/page budget]")
    if result.input_tokens or result.output_tokens:
        print(f"  Tokens       : {result.input_tokens} in / {result.output_tokens} out")
    print(f"  Cost         : ${result.cost_usd:.5f}")
    for note in inv.extraction_notes:
        print(f"  Note         : {note}")


def estimate_only(path: Path, guard: CostGuard) -> None:
    cfg = guard.config
    pages = min(page_count(path), cfg.max_pages_per_doc)
    rendered = render_document(path, max_pages=pages, max_edge=cfg.max_image_edge)
    image_tokens = sum(p.estimated_image_tokens for p in rendered)
    cost = cfg.estimate_page_cost(image_tokens)
    dims = ", ".join(f"{p.width}x{p.height}" for p in rendered)
    print(f"  {path.name}: {pages} page(s) [{dims}] ~= {image_tokens} image tokens "
          f"-> worst case ${cost:.5f} on {cfg.model}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Extract and audit invoice data.")
    ap.add_argument("files", nargs="+", help="PDF or image paths (globs allowed)")
    ap.add_argument("--mock", action="store_true",
                    help="canned response; no API key, no network, no cost")
    ap.add_argument("--estimate", action="store_true",
                    help="print the projected cost and exit without calling the model")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a report")
    ap.add_argument("--no-cache", action="store_true", help="force a fresh extraction")
    ap.add_argument("--model", help="override PARSER_MODEL for this run")
    ap.add_argument("--max-edge", type=int, help="override the image downscale cap (px)")
    ap.add_argument("--provider", choices=available_providers(),
                    help="vision backend for documents with no text layer")
    ap.add_argument("--no-text-layer", action="store_true",
                    help="skip the free text-layer read; force the vision model")

    args = ap.parse_args(argv)

    paths: list[Path] = []
    for pattern in args.files:
        matched = [Path(p) for p in glob.glob(pattern)]
        paths.extend(matched or [Path(pattern)])

    config = GuardConfig()
    if args.model:
        config.model = args.model
    if args.max_edge:
        config.max_image_edge = args.max_edge
    if args.provider:
        config.provider = args.provider
    guard = CostGuard(config)

    provider = None
    if not args.mock and not args.estimate:
        try:
            provider = get_provider(config.provider)
        except ProviderError as exc:
            print(f"\n  [PROVIDER] {exc}", file=sys.stderr)
            return 3
        print(f"\n  Vision fallback: {provider.describe()}")
        if args.no_text_layer:
            print("  Text layer    : DISABLED - every page goes to the model")

    if args.estimate:
        print("\n  Cost estimate (no API calls made)\n" + "  " + "-" * 60)
        for path in paths:
            try:
                estimate_only(path, guard)
            except Exception as exc:
                print(f"  {path.name}: {exc}")
        print()
        return 0

    exit_code = 0
    payloads = []

    for path in paths:
        try:
            result = extract_invoice(
                path, guard=guard, mock=args.mock, force_refresh=args.no_cache,
                provider=provider, use_text_layer=not args.no_text_layer,
            )
        except BudgetExceeded as exc:
            print(f"\n  [BUDGET] {path.name}: {exc}", file=sys.stderr)
            exit_code = max(exit_code, 2)
            continue
        except (ExtractionError, ProviderError, FileNotFoundError, ValueError) as exc:
            print(f"\n  [ERROR] {path.name}: {exc}", file=sys.stderr)
            exit_code = max(exit_code, 3)
            continue

        if args.json:
            payloads.append(json.loads(result.invoice.model_dump_json()))
        else:
            render_report(result)

        if result.audit.failures:
            exit_code = max(exit_code, 1)
        elif not result.audit.passed:
            exit_code = max(exit_code, 4)

    if args.json:
        print(json.dumps(payloads, indent=2, default=str))
    else:
        print(f"\n{RULE}")
        print(f"  USAGE  {guard.summary()}")
        print(f"{RULE}\n")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
