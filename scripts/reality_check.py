#!/usr/bin/env python3
"""
Measure accuracy on REAL invoices, and report it honestly.

    python scripts/reality_check.py stub      # pre-fill truth for new files
    python scripts/reality_check.py score     # the number you may actually quote
    python scripts/reality_check.py status    # what is verified, what is not

WHAT IS WRONG WITH THE NUMBER YOU HAVE NOW
    "100% field accuracy" came from two PDFs this project generated itself,
    with a parser developed against those exact two files. That is a fit to a
    sample of two, not a measurement. This tool replaces it.

THE HEADLINE METRIC IS TOUCH RATE, NOT FIELD ACCURACY
    Field accuracy flatters. An invoice with 10 of 11 fields right still has to
    be opened, checked and corrected by a human -- so operationally it saved
    nobody anything. The number a finance buyer cares about is:

        TOUCH RATE = share of invoices needing ZERO human correction

    That is the number that converts to hours saved, so that is the headline.
    Field accuracy is reported underneath as a diagnostic: it tells you WHICH
    field to go and fix.

AND IT COMES WITH AN ERROR BAR
    At n=20, "95%" means somewhere between roughly 76% and 99%. Quoting the
    point estimate from a small sample is how you end up promising a number you
    cannot hit. Every proportion here carries a Wilson 95% confidence interval,
    and the honest thing to say to a prospect is the LOWER bound.

    Note also that fields within one document are correlated -- a bad parse
    fails several at once -- so field-level intervals are optimistically narrow.
    Document-level touch rate has no such problem, which is another reason it
    leads.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from parser.cost_guard import BudgetExceeded, CostGuard, GuardConfig  # noqa: E402
from parser.extractor import ExtractionError, extract_invoice  # noqa: E402
from parser.pdf_utils import SUPPORTED_SUFFIXES, file_fingerprint  # noqa: E402
from parser.providers import ProviderError, get_provider  # noqa: E402
from parser.truthset import (  # noqa: E402
    TRUTH_FIELDS,
    UNKNOWN,
    TruthSet,
    compare_field,
    stub_from_extraction,
)

# Both overridable so a dry run or a test can never touch the real folder.
REAL_DIR = Path(os.getenv("PARSER_REAL_DIR") or (ROOT / "real_invoices"))
STORE = Path(os.getenv("PARSER_TRUTH_STORE") or (REAL_DIR / "truthset.json"))


# ------------------------------------------------------------------ statistics
def wilson(successes: int, trials: int, z: float = 1.959963985) -> Tuple[float, float, float]:
    """Wilson score interval for a proportion. Returns (point, low, high) as %.

    Wilson rather than the textbook normal approximation because the normal one
    is badly wrong at exactly the places this project lands: tiny samples, and
    proportions near 0 or 1. At 20/20 the normal approximation reports a
    confidence interval of [100%, 100%], which is nonsense and precisely the
    overclaim this whole exercise exists to prevent.
    """
    if trials <= 0:
        return 0.0, 0.0, 0.0
    p = successes / trials
    denom = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials))
    return 100 * p, 100 * max(0.0, centre - margin), 100 * min(1.0, centre + margin)


def bar(pct: float, width: int = 22) -> str:
    filled = int(round(width * pct / 100))
    return "#" * filled + "." * (width - filled)


# --------------------------------------------------------------------- inputs
def documents() -> List[Path]:
    if not REAL_DIR.exists():
        return []
    return sorted(
        p for p in REAL_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )


def build_guard(args) -> Tuple[CostGuard, Any]:
    config = GuardConfig()
    if args.provider:
        config.provider = args.provider
    guard = CostGuard(config)
    provider = None
    if not args.mock:
        provider = get_provider(config.provider)
        if getattr(provider, "free", False) and provider.name == "gemini":
            print()
            print("  !! You are pointing the GEMINI FREE TIER at real invoices.")
            print("     Google states free-tier content is used to improve their")
            print("     products. If any of these documents belong to a client or")
            print("     name a real supplier, stop and use a paid tier instead.")
            if not args.yes_really:
                print("     Re-run with --yes-really if that is genuinely fine.")
                sys.exit(2)
    return guard, provider


def run_extraction(path: Path, guard: CostGuard, provider, args):
    return extract_invoice(
        path, guard=guard, mock=args.mock, force_refresh=args.no_cache,
        provider=provider, use_text_layer=not args.no_text_layer,
    )


# ---------------------------------------------------------------------- stub
def cmd_stub(args) -> int:
    """Pre-fill truth records for documents that do not have one yet."""
    docs = documents()
    if not docs:
        print(f"\n  No documents in {REAL_DIR}/. Put real invoices there first.")
        print("  See real_invoices/README.md.\n")
        return 1

    truth = TruthSet(STORE)
    guard, provider = build_guard(args)
    created = skipped = 0

    print(f"\n  Bootstrapping truth stubs from {len(docs)} document(s)")
    print("  " + "=" * 74)

    for path in docs:
        fingerprint = file_fingerprint(path)
        existing = truth.records.get(fingerprint)
        if existing is not None and not args.overwrite:
            state = "verified" if existing.verified else "stub, not yet verified"
            print(f"  skip   {path.name:<44s} ({state})")
            skipped += 1
            continue
        try:
            result = run_extraction(path, guard, provider, args)
        except (BudgetExceeded, ExtractionError, ProviderError, ValueError) as exc:
            print(f"  FAIL   {path.name:<44s} {exc}")
            continue
        truth.put(stub_from_extraction(path, result.invoice, fingerprint))
        print(f"  stub   {path.name:<44s} [{result.strategy}, ${result.cost_usd:.5f}]")
        created += 1

    truth.save()
    print("  " + "=" * 74)
    print(f"\n  {created} stub(s) written, {skipped} left alone -> {STORE}")
    if created:
        print()
        print("  NOW DO THE PART THAT MATTERS. Open that file, read each invoice,")
        print("  and correct every field the extractor got wrong. Then set")
        print('  "verified": true on it. Nothing is scored until you do --')
        print("  a stub is the extractor grading its own homework.")
        print()
        print("  Faster alternative: run `streamlit run app.py`, point it at a")
        print("  file in real_invoices/, fix the table on screen, and click")
        print('  "Save as verified ground truth".')
    print(f"\n  {guard.summary()}\n")
    return 0


# -------------------------------------------------------------------- status
def cmd_status(args) -> int:
    docs = documents()
    truth = TruthSet(STORE)
    print(f"\n  {REAL_DIR}")
    print("  " + "=" * 74)
    if not docs:
        print("  (empty)\n")
        return 1

    verified = unverified = untracked = 0
    for path in docs:
        record = truth.records.get(file_fingerprint(path))
        if record is None:
            mark, detail, untracked = "untracked", "run `stub` to bootstrap", untracked + 1
        elif record.verified:
            mark = "VERIFIED"
            detail = (f"{len(record.scoreable_fields())}/{len(TRUTH_FIELDS)} fields, "
                      f"via {record.source}")
            verified += 1
        else:
            mark, detail, unverified = "stub", "needs your review", unverified + 1
        print(f"  {mark:<10s} {path.name:<44s} {detail}")

    print("  " + "=" * 74)
    print(f"\n  {len(docs)} document(s): {verified} verified, "
          f"{unverified} awaiting review, {untracked} untracked")
    if verified < 20:
        print(f"  {20 - verified} more verified document(s) for a sample worth "
              "quoting from.")
    print()
    return 0


# --------------------------------------------------------------------- score
def cmd_score(args) -> int:
    truth = TruthSet(STORE)
    verified = truth.verified_records()
    if not verified:
        print(f"\n  Nothing verified in {STORE}.")
        print("  `stub` pre-fills records; you still have to check them and set")
        print('  "verified": true. Refusing to report a number from zero '
              "documents.\n")
        return 1

    by_fingerprint = {file_fingerprint(p): p for p in documents()}
    guard, provider = build_guard(args)

    field_hits: Dict[str, int] = defaultdict(int)
    field_total: Dict[str, int] = defaultdict(int)
    route_clean: Dict[str, int] = defaultdict(int)
    route_total: Dict[str, int] = defaultdict(int)
    failures: List[Tuple[str, str, str, str]] = []
    clean_docs = 0
    scored_docs = 0
    total_cost = 0.0
    audit_states: Dict[str, int] = defaultdict(int)

    print(f"\n  Scoring {len(verified)} verified document(s)")
    print("  " + "=" * 74)

    for record in sorted(verified, key=lambda r: r.file_name):
        path = by_fingerprint.get(record.fingerprint)
        if path is None:
            print(f"  MISSING  {record.file_name} (hash not found in "
                  f"{REAL_DIR.name}/ -- file moved or edited?)")
            continue
        try:
            result = run_extraction(path, guard, provider, args)
        except (BudgetExceeded, ExtractionError, ProviderError, ValueError) as exc:
            print(f"  ERROR    {record.file_name}: {exc}")
            continue

        total_cost += result.cost_usd
        route = "mock" if result.provider == "mock" else result.strategy
        scored_docs += 1
        route_total[route] += 1

        if result.audit.passed:
            audit_states["PASS"] += 1
        elif result.audit.failures:
            audit_states["FAIL"] += 1
        else:
            audit_states["INCONCLUSIVE"] += 1

        misses: List[str] = []
        for name in record.scoreable_fields():
            expected = record.fields[name]
            actual = (len(result.invoice.line_items) if name == "line_item_count"
                      else getattr(result.invoice, name, None))
            field_total[name] += 1
            if compare_field(name, expected, actual):
                field_hits[name] += 1
            else:
                misses.append(name)
                failures.append((record.file_name, name, str(expected), str(actual)))

        if misses:
            print(f"  {len(record.scoreable_fields()) - len(misses):2d}/"
                  f"{len(record.scoreable_fields()):2d}  {record.file_name:<40s} "
                  f"[{route}] missed: {', '.join(misses)}")
        else:
            clean_docs += 1
            route_clean[route] += 1
            print(f"  {len(record.scoreable_fields()):2d}/"
                  f"{len(record.scoreable_fields()):2d}  {record.file_name:<40s} "
                  f"[{route}] clean")

    if not scored_docs:
        print("\n  Nothing could be scored.\n")
        return 1

    # ------------------------------------------------------------- the report
    print("  " + "=" * 74)
    print("\n  TOUCH RATE - invoices needing zero human correction")
    print("  (the number that converts into hours saved, so the one to quote)\n")
    point, low, high = wilson(clean_docs, scored_docs)
    print(f"      {clean_docs}/{scored_docs} documents fully correct")
    print(f"      {bar(point)}  {point:.1f}%")
    print(f"      95% confidence: {low:.1f}% - {high:.1f}%")
    print(f"\n      Say \"{low:.0f}%\" to a prospect, not \"{point:.0f}%\". "
          "That is the lower bound")
    print("      of what this sample actually supports.")

    total_fields = sum(field_total.values())
    total_hits = sum(field_hits.values())
    fpoint, flow, fhigh = wilson(total_hits, total_fields)
    print(f"\n  FIELD ACCURACY  {total_hits}/{total_fields} = {fpoint:.1f}% "
          f"(95% CI {flow:.1f}-{fhigh:.1f}%)")
    print("  Diagnostic only -- fields within a document are correlated, so this")
    print("  interval is optimistically narrow. Use it to find the weak field:\n")
    # Flag only the genuinely worst field(s). When one bad document fails ten
    # fields at once, every one of them scores the same -- calling all ten
    # "weakest" tells you nothing about where to spend your evening.
    scores = {n: 100 * field_hits[n] / field_total[n]
              for n in TRUTH_FIELDS if field_total[n]}
    worst = min(scores.values()) if scores else 0.0
    best = max(scores.values()) if scores else 0.0
    tied_at_worst = sum(1 for v in scores.values() if v == worst)

    # If most fields are tied at the bottom, the failures are whole-document
    # failures -- one document parsed as the wrong document -- and pointing at
    # ten "weakest fields" would send you off fixing the wrong thing.
    clustered = scores and tied_at_worst > len(scores) / 2

    for name in TRUTH_FIELDS:
        if name not in scores:
            continue
        p = scores[name]
        flag = ("  <-- weakest"
                if not clustered and p == worst and p < best and p < 90 else "")
        print(f"      {name:<18s} {field_hits[name]:3d}/{field_total[name]:<3d} "
              f"{bar(p, 18)} {p:5.1f}%{flag}")

    if clustered:
        print(f"\n      {tied_at_worst} of {len(scores)} fields are tied at "
              f"{worst:.1f}%, so these are WHOLE-DOCUMENT")
        print("      failures, not one weak field -- some documents parsed as the")
        print("      wrong document entirely. Read the route breakdown and the")
        print("      failure gallery below; the per-field table has nothing to say.")

    if len(route_total) > 1 or True:
        print("\n  BY ROUTE")
        for route in sorted(route_total):
            p, lo, hi = wilson(route_clean[route], route_total[route])
            note = "  [canned reply - not a measurement]" if route == "mock" else ""
            print(f"      {route:<12s} {route_clean[route]:2d}/{route_total[route]:<2d} "
                  f"clean = {p:5.1f}% (CI {lo:.0f}-{hi:.0f}%){note}")

    print("\n  MATH AUDIT VERDICTS")
    for state in ("PASS", "FAIL", "INCONCLUSIVE"):
        if audit_states[state]:
            print(f"      {state:<14s} {audit_states[state]}")
    if audit_states["FAIL"]:
        print("      Check every FAIL by hand. On real vendor invoices the")
        print("      arithmetic is usually right, so a FAIL is far more likely")
        print("      to be YOUR extraction than their bookkeeping.")

    if failures:
        print(f"\n  FAILURE GALLERY - every miss, so you can fix causes not symptoms")
        print("  " + "-" * 74)
        by_field: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
        for file_name, name, want, got in failures:
            by_field[name].append((file_name, want, got))
        for name in sorted(by_field, key=lambda n: -len(by_field[n])):
            print(f"\n    {name}  ({len(by_field[name])} miss(es))")
            for file_name, want, got in by_field[name][:8]:
                print(f"      {file_name}")
                print(f"        expected  {want}")
                print(f"        got       {got}")
            if len(by_field[name]) > 8:
                print(f"      ... and {len(by_field[name]) - 8} more")

    print()
    print("  " + "=" * 74)
    print(f"  COST  ${total_cost:.4f} across {scored_docs} document(s)"
          f"  (${total_cost / scored_docs:.5f}/doc)")
    print(f"  {guard.summary()}")
    if scored_docs < 20:
        print(f"\n  CAVEAT: {scored_docs} documents is a small sample. The interval")
        print("  above is wide for a reason. Get to 20+, from more than one vendor,")
        print("  before this number goes in a proposal.")
    print("  " + "=" * 74 + "\n")

    return 0 if clean_docs == scored_docs else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    def shared(p):
        p.add_argument("--provider", help="vision backend for documents with no text layer")
        p.add_argument("--mock", action="store_true", help="canned reply; no key, no cost")
        p.add_argument("--no-text-layer", action="store_true",
                       help="force every document through the vision model")
        p.add_argument("--no-cache", action="store_true", help="force fresh extractions")
        p.add_argument("--yes-really", action="store_true",
                       help="acknowledge sending real documents to a free tier")
        return p

    shared(sub.add_parser("stub", help="pre-fill truth records for new documents")) \
        .add_argument("--overwrite", action="store_true",
                      help="re-stub documents that already have a record")
    shared(sub.add_parser("score", help="measure accuracy on verified documents"))
    shared(sub.add_parser("status", help="what is verified and what is not"))

    args = ap.parse_args(argv)
    try:
        return {"stub": cmd_stub, "score": cmd_score, "status": cmd_status}[args.command](args)
    except ProviderError as exc:
        print(f"\n  [PROVIDER] {exc}\n", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"\n  [TRUTH SET] {exc}\n", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
