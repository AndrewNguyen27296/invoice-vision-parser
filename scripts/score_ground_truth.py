#!/usr/bin/env python3
"""
Score extraction against known-correct values.

WHY THIS SCRIPT IS NOT OPTIONAL
    The README's pitch contains the number "99%+". Until something measures it,
    that is a sentence, not a fact -- and it is the one claim a technical buyer
    will test in the first five minutes. `ground_truth.json` has held the
    correct answers for the three samples since day one and nothing read it.
    Now something does.

    Run this before you quote an accuracy figure to anyone, and re-run it every
    time you touch the prompt, the resolution, the model or the text-layer
    parser. It is also the regression test for the free path: if a change to
    textlayer.py drops field accuracy, this is what tells you.

USAGE
    python scripts/score_ground_truth.py                   # free text layer
    python scripts/score_ground_truth.py --mock            # canned response
    python scripts/score_ground_truth.py --provider gemini # live vision
    python scripts/score_ground_truth.py --no-text-layer --provider anthropic

EXIT CODES
    0  every field matched
    1  at least one field mismatched
    2  a guardrail or provider error stopped the run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from parser.cost_guard import BudgetExceeded, CostGuard, GuardConfig  # noqa: E402
from parser.extractor import ExtractionError, extract_invoice  # noqa: E402
from parser.providers import ProviderError, get_provider  # noqa: E402
from parser.schemas import to_decimal  # noqa: E402

SAMPLES = ROOT / "sample_invoices"
TRUTH = SAMPLES / "ground_truth.json"
MONEY_TOLERANCE = Decimal("0.005")

TEXT_FIELDS = ("vendor_name", "vendor_tax_id", "invoice_no", "currency")
DATE_FIELDS = ("invoice_date", "due_date")
MONEY_FIELDS = ("subtotal", "tax_rate", "tax_amount", "total_amount")


def _norm_text(value: Any) -> Optional[str]:
    """Case- and whitespace-insensitive. 'GB 412 8876 22' == 'GB412887622'."""
    if value is None:
        return None
    return re.sub(r"\s+", "", str(value)).upper() or None


def _norm_date(value: Any) -> Optional[date]:
    if value in (None, "", "null"):
        return None
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    return None


def compare(expected: dict, invoice, audit) -> List[Tuple[str, bool, str, str]]:
    """(field, matched, expected, actual) for every field we have truth for."""
    rows: List[Tuple[str, bool, str, str]] = []

    for field in TEXT_FIELDS:
        if field not in expected:
            continue
        want, got = _norm_text(expected[field]), _norm_text(getattr(invoice, field))
        rows.append((field, want == got, str(expected[field]),
                     str(getattr(invoice, field))))

    for field in DATE_FIELDS:
        if field not in expected:
            continue
        want, got = _norm_date(expected[field]), getattr(invoice, field)
        rows.append((field, want == got, str(expected[field]), str(got)))

    for field in MONEY_FIELDS:
        if field not in expected:
            continue
        want, got = to_decimal(expected[field]), getattr(invoice, field)
        ok = (want is None and got is None) or (
            want is not None and got is not None and abs(want - got) <= MONEY_TOLERANCE)
        rows.append((field, ok, str(expected[field]), str(got)))

    if "line_item_count" in expected:
        want, got = int(expected["line_item_count"]), len(invoice.line_items)
        rows.append(("line_item_count", want == got, str(want), str(got)))

    if "expected_audit" in expected:
        if audit.passed:
            verdict = "PASS"
        elif audit.failures:
            verdict = "FAIL"
        else:
            verdict = "INCONCLUSIVE"
        want = str(expected["expected_audit"]).upper()
        rows.append(("audit verdict", want == verdict, want, verdict))

    return rows


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mock", action="store_true", help="score the canned response")
    ap.add_argument("--provider", help="vision backend for documents with no text layer")
    ap.add_argument("--no-text-layer", action="store_true",
                    help="force every document through the vision model")
    ap.add_argument("--no-cache", action="store_true", help="force fresh extractions")
    ap.add_argument("--min-accuracy", type=float, default=100.0,
                    help="fail the run below this field accuracy (default 100)")
    args = ap.parse_args(argv)

    if not TRUTH.exists():
        print(f"No ground truth at {TRUTH}", file=sys.stderr)
        return 2
    truth = json.loads(TRUTH.read_text(encoding="utf-8"))

    config = GuardConfig()
    if args.provider:
        config.provider = args.provider
    guard = CostGuard(config)
    provider = get_provider(config.provider) if not args.mock else None

    print()
    print(f"  Scoring against {TRUTH.name}")
    print(f"  route: {'mock' if args.mock else config.provider}"
          f"{'' if args.no_text_layer else ' (text layer tried first)'}")
    print("  " + "=" * 74)

    total = matched = 0
    per_doc: List[Tuple[str, int, int, str]] = []

    for name, expected in sorted(truth.items()):
        if name.startswith("_"):
            continue
        path = SAMPLES / name
        if not path.exists():
            print(f"\n  {name}: MISSING FILE")
            continue

        try:
            result = extract_invoice(
                path, guard=guard, mock=args.mock,
                force_refresh=args.no_cache, provider=provider,
                use_text_layer=not args.no_text_layer,
            )
        except BudgetExceeded as exc:
            print(f"\n  {name}: guardrail stopped this run -- {exc}", file=sys.stderr)
            return 2
        except (ExtractionError, ProviderError) as exc:
            print(f"\n  {name}: extraction failed -- {exc}", file=sys.stderr)
            return 2

        rows = compare(expected, result.invoice, result.audit)
        hits = sum(1 for _, ok, _, _ in rows if ok)
        total += len(rows)
        matched += hits

        # A canned reply is a canned reply whether it arrived via --mock or via
        # --provider mock. Label it so the summary can exclude it.
        route = "mock" if result.provider == "mock" else result.strategy

        print(f"\n  {name}   [{route}, ${result.cost_usd:.5f}, "
              f"{result.latency_seconds:.2f}s]")
        for field, ok, want, got in rows:
            mark = "ok  " if ok else "MISS"
            detail = f"{want}" if ok else f"expected {want!r}, got {got!r}"
            print(f"      {mark}  {field:18s} {detail}")
        print(f"      ---- {hits}/{len(rows)} fields")
        per_doc.append((name, hits, len(rows), route))

    print()
    print("  " + "=" * 74)
    for name, hits, count, strategy in per_doc:
        bar = "#" * int(round(20 * hits / count)) if count else ""
        print(f"  {name:26s} {hits:2d}/{count:2d}  {bar:<20s} {strategy}")

    # Accuracy per route, which is the number that actually means something. A
    # blended figure across a free deterministic reader and a vision model
    # describes neither of them.
    print()
    by_strategy: dict = {}
    for _, hits, count, strategy in per_doc:
        got, of = by_strategy.get(strategy, (0, 0))
        by_strategy[strategy] = (got + hits, of + count)
    for strategy, (hits, count) in sorted(by_strategy.items()):
        share = 100.0 * hits / count if count else 0.0
        print(f"  {strategy:14s} {hits:2d}/{count:2d} = {share:5.1f}%")

    # The mock provider returns one fixed invoice whatever you send it. Scoring
    # it against real documents measures nothing, and a number that means
    # nothing must not be printed as though it did.
    mocked = [n for n, _, _, st_ in per_doc if st_ == "mock"]
    scored = [(n, h, c) for n, h, c, st_ in per_doc if st_ != "mock"]
    real_matched = sum(h for _, h, _ in scored)
    real_total = sum(c for _, _, c in scored)
    accuracy = 100.0 * real_matched / real_total if real_total else 0.0

    print()
    if mocked:
        print(f"  NOT AN ACCURACY MEASUREMENT for {len(mocked)} document(s): the mock")
        print("  provider replies with one fixed invoice regardless of input. Excluded")
        print("  from the figure below. Re-run with --provider gemini or anthropic to")
        print("  score the vision route for real.")
        print()
    print(f"  FIELD ACCURACY  {real_matched}/{real_total} = {accuracy:.1f}%"
          f"  (over {len(scored)} document(s))")
    print(f"  BLENDED w/ mock {matched}/{total} = "
          f"{100.0 * matched / total if total else 0.0:.1f}%  [not meaningful]")
    print(f"  USAGE           {guard.summary()}")
    print("  " + "=" * 74)
    print()

    if not real_total:
        print("  Nothing scoreable was run.\n", file=sys.stderr)
        return 1
    if accuracy + 1e-9 < args.min_accuracy:
        print(f"  BELOW THRESHOLD: {accuracy:.1f}% < {args.min_accuracy:.1f}%\n",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
