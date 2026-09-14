# real_invoices/

Put **real** invoices here. This folder is gitignored and nothing in it is ever
committed.

## Why this folder exists

Every accuracy number this project has produced so far came from three invoices
it generated itself, parsed by a parser written against those same three files.
That is a fit to a sample of three, not a measurement — and the honest
confidence interval on "100% of 2 documents" runs from **34%** to 100%.

So: real documents, real ground truth, real number.

## What to put in here

Aim for **20+ invoices from at least 5 different vendors.** Variety matters far
more than volume — twenty invoices from one supplier measures one layout.

Good sources that cost you nothing and involve nobody's confidential data:

- Your own purchase invoices and receipts: freight, couriers, SaaS, hosting,
  phone, office supplies, hotels, flights.
- A friend or family member's small company, with their permission.
- Vendors' own published sample invoices and templates.
- Your own outgoing invoices, if you have any.

**Stay inside the non-compete.** No energy, electricity, utility, metering,
district heating or ESG invoices — see the guardrails in the master README.

**Think before you use someone else's documents.** If an invoice belongs to a
client or names a real supplier, do not put it through a free-tier provider:
Google states free-tier content is used to improve their products. The tooling
will stop you and make you pass `--yes-really` if you try.

## The workflow

```powershell
# 1. Drop files in here, then see where you stand
python scripts\reality_check.py status

# 2. Pre-fill a truth record for each new file (free for digital PDFs)
python scripts\reality_check.py stub

# 3. THE PART THAT MATTERS: verify each record against the actual document.
#    Either edit real_invoices\truthset.json by hand and set "verified": true,
#    or — much faster — use the app:
streamlit run app.py
#    pick the file, fix the table on screen, tick the confirmation,
#    click "Save as verified ground truth"

# 4. The number you may actually quote
python scripts\reality_check.py score
```

## Why a stub is not ground truth

`stub` pre-fills each record from the extractor's own output. That saves you
most of the typing, and it is also exactly why nothing counts until you set
`verified: true` yourself: an unverified stub is the extractor grading its own
homework, and scoring against it would report ~100% forever.

## null versus "?"

| Value | Means | Scored? |
| :--- | :--- | :---: |
| `null` | the field is genuinely **absent** from the document (no due date printed) — extracting nothing is the correct answer | **yes** |
| `"?"` | you did not check it, or it does not apply | **no** |

Conflating those two is how an accuracy figure quietly inflates. If you are not
sure, write `"?"`.
