# 🧾 Invoice & Vision Parser

**Zero-manual-entry document automation for accounts payable.** Drop a carrier or
supplier invoice in; get structured, editable, *arithmetically audited* data out,
and a formatted Excel workbook in one click.

Built for logistics, wholesale, retail, e-commerce and corporate-travel documents.

**▶ [Open the live demo](https://invoice-vision-parser-uezswbeps38zrocbeej9hf.streamlit.app/)** — three bundled
invoices, one click, no sign-up and no API key. The green verdict on first paint
is Python's own arithmetic, not the model's word for it; pick the wholesale
invoice to watch the audit catch a real error.

Runs locally in two commands too — see [Quick start](#quick-start).

```
┌─────────────────────────┐   ┌──────────────────────────────────────┐
│                         │   │  ✅ Math verified                    │
│   the invoice, as it     │   │  sum(line items) 1 550,00 = subtotal │
│   actually looks         │   │  subtotal + VAT 25% = total 1 937,50 │
│                         │   │                                      │
│   [ page preview ]      │   │  editable vendor · dates · currency  │
│                         │   │  editable line-item table            │
│                         │   │  ⬇ formatted .xlsx   ⬇ .csv          │
└─────────────────────────┘   └──────────────────────────────────────┘
```

---

## Why it's different: the verdict is earned, not asserted

Language models are good at reading invoices and bad at arithmetic — and they are
confidently wrong in both cases. So this system never trusts the model's maths.
Every figure is re-added in Python with `Decimal` precision before anything turns
green:

```
discrepancy = | subtotal + tax − total |        green if ≤ 0.05
```

There are **three** outcomes, not two:

| Verdict | Means |
| :--- | :--- |
| 🟢 **Math verified** | Python independently re-added the numbers and they agreed |
| 🔴 **Math audit failed** | the arithmetic on the page does not hold |
| 🟠 **Inconclusive** | a missing figure had to be back-calculated, so *nothing independent could be checked* |

That third state matters. If the model returns a total and a tax but no subtotal,
deriving `subtotal = total − tax` makes `subtotal + tax = total` true *by
construction* — an identity that cannot fail and proves nothing. The audit
detects that case and refuses to call it a pass. A green badge you can't trust is
worse than no badge.

---

## It costs almost nothing to run

Extraction tries four routes in order. Everything above the last one is free:

| # | Route | When | Cost |
|---|---|---|---|
| 1 | **cache** | this exact file, at these settings, seen before | $0.00 |
| 2 | **text layer** | the PDF has embedded text — **most real invoices** | $0.00 |
| 3 | **fixtures** | the bundled demo samples | $0.00 |
| 4 | **vision model** | no text layer: a scan or a phone photo | ~$0.005/page |

Most invoices an AP team receives are digital PDFs straight out of a vendor's
ERP. Those files *already contain* the vendor name, every line item and every
figure as text. Sending a picture of them to a vision model pays money, adds
seconds and introduces transcription risk to recover data that was never lost —
so route 2 handles them, and only genuine scans reach a paid model.

**Whichever route answers, the result goes through the same schema and the same
audit.** The verdict means exactly the same thing whether the numbers came from a
model or from reading the file's own bytes.

---

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

**No API key required.** The two digital samples are read from their own text
layer; the scanned sample is served from a curated fixture. Everything works
offline, for free, out of the box.

Command line:

```bash
python cli.py sample_invoices/*.pdf        # extract and audit
python cli.py --estimate invoice.pdf       # price a job without calling anything
python cli.py --json invoice.pdf           # machine-readable output
```

Exit codes: `0` clean · `1` audit **failed** · `2` guardrail hit · `3` error ·
`4` audit **inconclusive**.

---

## Try it in one click

Three bundled sample invoices, all fictional, selectable from the sidebar:

| Sample | What it demonstrates |
| :--- | :--- |
| **Carrier invoice** | the clean path — 4 line items, audit passes |
| **Wholesale invoice** | 11 rows, Swedish decimal commas, space thousands separators — and the printed total is **3.00 too high**, which the audit catches |
| **Corporate travel** | a skewed, speckled phone scan with **no text layer**, so it needs a vision model |

The second one is the interesting demo. Reading it correctly and then *failing*
it is the product working. Correct the total by hand in the table and the verdict
turns green in front of you — deterministically, with no model involved.

---

## Extraction backends

`PARSER_PROVIDER` selects what handles documents with no text layer:

| Provider | Cost | Notes |
| :--- | :--- | :--- |
| `demo` *(default)* | free | curated answers for the bundled samples; **declines** anything else rather than inventing an answer |
| `anthropic` | paid | best accuracy; content not used for training |
| `gemini` | free tier | see the data-terms warning in `.env.example` before sending anything real |
| `mock` | free | fixed canned reply, for development and tests |

Adding a backend means implementing one method — see `parser/providers/base.py`.
Nothing else changes, because parsing, validation, auditing, caching and pricing
are shared pipeline. That is deliberate: it means the same deployment can run on
a client's preferred vendor, an EU-hosted one, or entirely on their own hardware,
and the verification still happens on their side.

---

## Cost guardrails

A public demo is an open door to your API key, so every limit is checked *before*
any network call — a refusal is free:

| Guardrail | Default | Env var |
|---|---|---|
| Pages per document | 3 | `PARSER_MAX_PAGES_PER_DOC` |
| Upload size | 15 MB | `PARSER_MAX_UPLOAD_MB` |
| Pages per visitor | 25 | `PARSER_SESSION_PAGE_LIMIT` |
| Pages per day | 300 | `PARSER_DAILY_PAGE_LIMIT` |
| Spend per day | $2.00 | `PARSER_DAILY_USD_BUDGET` |
| Image resolution | 1120 px | `PARSER_MAX_IMAGE_EDGE` |
| Output tokens | 1600 | `PARSER_MAX_OUTPUT_TOKENS` |

Plus a SHA-256 content cache, so the same file never bills twice. The daily
budget gate is forward-looking: it prices the document *before* admitting it,
rather than only checking whether the budget is already gone.

Two details worth knowing if you deploy this:

- The spend ledger is a local JSON file, so on an ephemeral host it resets on
  restart. **Set a spend limit on the API key itself** — application guardrails
  are the first line, the key limit is the backstop that survives a bug in your
  own code.
- At the default settings, a page is ~77% output tokens and only ~16% image, so
  `PARSER_MAX_OUTPUT_TOKENS` is the biggest lever on cost — not resolution.

---

## How accurate is it, honestly

**On the two bundled digital invoices: every field correct, in ~250 ms, for
$0.00.** That is a sample of two documents this repository generated itself,
parsed by a reader developed against those same two files — so it is a fit to a
tiny sample, not a measurement. The honest 95% confidence interval on 2-for-2
runs from **34% to 100%**.

Rather than quote a number it hasn't earned, the repo ships the tool that
measures one properly:

```bash
python scripts/reality_check.py stub     # pre-fill truth for your own invoices
python scripts/reality_check.py score    # touch rate, with confidence intervals
```

It reports **touch rate** — the share of invoices needing *zero* human
correction — because an invoice with 10 of 11 fields right still has to be opened
and checked, so it saved nobody anything. Field accuracy is printed underneath as
a diagnostic. Every proportion carries a Wilson interval, and the tool tells you
to quote the lower bound.

Put your own invoices in `real_invoices/` (gitignored) and find out.

---

## Architecture

```
app.py                      Streamlit UI — side-by-side, live re-audit, export
cli.py                      terminal harness
parser/
  extractor.py              the strategy ladder: cache → text layer → vision
  textlayer.py              free deterministic read of a PDF's own text
  schemas.py                strict Pydantic models + the math audit
  cost_guard.py             caps, ledger, content cache
  pdf_utils.py              render + downscale (pypdfium2)
  prompts.py                the vision prompt, versioned into the cache key
  providers/                pluggable backends behind one contract
  truthset.py               ground truth for accuracy measurement
utils/exporter.py           formatted .xlsx, .csv, batch summary sheet
scripts/
  make_samples.py           regenerates the sample invoices
  score_ground_truth.py     scores the bundled samples
  reality_check.py          touch rate on real invoices, with error bars
tests/                      111 tests
```

### One detail I'm fond of

Swedish invoices use a space as the thousands separator, which makes a table row
genuinely ambiguous as plain text:

```
010  Frakt / Freight    1 450,00    450,00
```

`1 450,00` is a valid number *and* `1 / 450,00 / 450,00` is a valid three-column
read. Tokenising cannot tell them apart. So the parser generates the candidate
readings and keeps the one where `quantity × unit_price` actually equals the row
total — **the invoice's own arithmetic disambiguates its own layout.** All 11 rows
of the wholesale sample parse with zero row-level discrepancy.

---

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q          # 134 passed
```

| File | Covers |
| :--- | :--- |
| `test_math_audit.py` | audit arithmetic, `Decimal` coercion, graceful degradation |
| `test_cost_guard.py` | every cap, ledger integrity, cache keying, pricing |
| `test_audit_integrity.py` | the claims the product is sold on |
| `test_textlayer.py` | the free path, including the ambiguity fix above |
| `test_providers_and_export.py` | provider contract, strategy ladder, Excel output |
| `test_truthset.py` | truth semantics, Wilson intervals |
| `test_deploy_readiness.py` | demo integrity, read-only filesystem, repository hygiene |

---

## Deploying to Streamlit Community Cloud

Deployed from `main` at the link above. Point it at `app.py`. No `packages.txt` is needed — every dependency ships
prebuilt wheels. Then set the caps in **Settings → Secrets** (a `.env` file is
not deployed):

```toml
PARSER_PROVIDER = "demo"
PARSER_USE_TEXT_LAYER = "1"
PARSER_DAILY_USD_BUDGET = "1.00"
PARSER_DAILY_PAGE_LIMIT = "200"
PARSER_SESSION_PAGE_LIMIT = "10"
PARSER_MAX_PAGES_PER_DOC = "2"
PARSER_MAX_UPLOAD_MB = "8"
```

With `PARSER_PROVIDER = "demo"` the deployment holds no API key at all and cannot
spend anything.

---

## Scope and limitations

- **Domain:** logistics and freight, wholesale, retail, e-commerce, corporate
  travel and professional services. Energy, electricity, utility-metering and
  carbon/ESG documents are explicitly out of scope.
- The text-layer reader is label-driven with Swedish and English vocabulary. It
  reports a coverage score and escalates to a vision model rather than guessing.
- A model that returns an unrecognised field name has that value **discarded**,
  with a note in the extraction log. Aliasing common variants is not done yet.
- The vision route has not yet been scored against a live model on real
  documents. The accuracy section above says exactly what is and isn't measured.
- Multi-page documents are capped at 3 pages by default and merged into one
  result.

---

## Licence

No licence granted. All rights reserved — portfolio demonstration code.
