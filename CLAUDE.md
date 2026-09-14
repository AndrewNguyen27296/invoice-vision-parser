# Pillar 2 - AI Invoice & Vision Parser

Zero-manual-entry accounts-payable extraction with an arithmetic audit: line
items must sum to the subtotal, and subtotal plus VAT must equal the total,
before the UI shows a green verdict.

Read `README.md` for the product and `RUNBOOK.md` for operations.

## The portfolio this belongs to

Three Streamlit products, built as proof-of-work to win freelance B2B data/AI
engagements in Sweden, the Nordics and Europe. Each has one 60-second "wow
moment" it exists to deliver. They are sold through three tiers: advisory
audits, turnkey builds, and managed retainers. The rates for each live in the
private portfolio notes, never in a public repo - a prospect who can read your
floor before the first call has already won the negotiation.

| Pillar | What it is | Code | Repo | Live URL |
| :--- | :--- | :--- | :--- | :--- |
| 1. Data Pipeline Dashboard | Freight ops briefing from messy multi-schema CSVs | V1 | yes | **live** |
| 2. Invoice & Vision Parser | Zero-manual-entry AP with an arithmetic audit | V1 | not yet | not yet |
| 3. Chat with Docs RAG | Cited, hallucination-free answers over company docs | V1 | not yet | not yet |

Pillar 1 is live at https://freight-operations-intelligence-gr6t94nmogzgzh3cgy2plf.streamlit.app/.

## The plan, in order

The bottleneck is not building. All three are built. The bottleneck is that a
prospect cannot reach them, so work top-down and resist polishing.

**Phase A - ship what exists.** git init, push to GitHub, deploy to Streamlit
Community Cloud, smoke-test the live URL cold. Done for Pillar 1; Pillars 2 and
3 still need it.

**Phase B - turn demos into conversations.** A 60-second screen recording per
pillar. A one-page offer with price and timeline. A list of 25-30 Nordic
companies in the safe verticals. Then send it.

**Phase C - product work, but only what a prospect actually asks for.** Export
centres, multi-source ingestion, alerting. Do not start Phase C before Phase B
has produced a real conversation; the audit is what tells you which of these is
worth building.

The fastest first revenue is a Tier 1 audit. It needs no further product work at
all - the live demos are credibility, not the deliverable.

## Deploying to Streamlit Community Cloud

Proven on Pillar 1. The recipe:

1. `git init -b main`, then pin the identity **repo-locally** so the repo cannot
   inherit a work identity later:
   `git config user.email nguyenhuuthien27296@gmail.com`
2. Check `.gitignore` covers `.venv/`, `__pycache__/`, `.env`,
   `.streamlit/secrets.toml` and any derived data directory. Confirm with
   `git add -A && git diff --cached --name-only` before the first commit.
3. Create an **empty** repo at github.com/new under the personal account
   (`AndrewNguyen27296`) - no README, no .gitignore, no licence, or the first
   push conflicts.
4. `git remote add origin <url> && git push -u origin main`
5. share.streamlit.io -> New app -> pick the repo, branch `main`, main file
   `app.py`.

**The trap that cost Pillar 1 its first deploy:** Community Cloud builds on
**Python 3.14**. A pinned dependency with no cp314 wheel makes pip fall back to
compiling from source, and the build image has no `cmake`. `pyarrow==21.0.0`
ships wheels only to cp313, so the build died. Fixed by moving to
`pyarrow==25.0.1`, which covers 3.10-3.14.

Before deploying anything, check every pin for a 3.14 wheel against
`https://pypi.org/pypi/<pkg>/<version>/json`. Wheels tagged `py3-none-*` or
`cp39-abi3` are version-agnostic and fine; a wheel range ending at cp313 is not.

Note that `streamlit` itself depends on `pyarrow>=7.0`, so pyarrow is installed
whether or not it is listed - the only real choice is which version.

## Hard rules

- **Domain restriction.** These repositories stay entirely out of the energy and
  utility sector: no consumption data, no metering, no utility billing, no
  sustainability reporting - not in code, samples, docs or test fixtures. Safe
  verticals are logistics and freight, e-commerce, retail, corporate travel, and
  professional services. Each pillar enforces this with a test that greps every
  tracked file, and those tests ban the specific vocabulary. Do not restate the
  banned words here: this file is scanned too, and a continuation line that
  carries them without the test suite marker fails the build.
- **Never commit the parent `AI Startup/` folder.** It holds private strategy
  notes. One repository per pillar, rooted in that pillar's own directory.
- **Public repos.** These are or will be public. Do not write the author's
  employer, work email, or client names into any tracked file. Commit as the
  personal identity above, never the work one.
- **Personal equipment and personal hours only.**

## Where this pillar stands

**V1 built. Not in git, not deployed.** Phase A has not started here.

A dependency pre-flight has already been run against Streamlit Cloud's Python
3.14, and this pillar is clear - no pinned dependency will fall back to a source
build:

| Pin | Wheels |
| :--- | :--- |
| `streamlit==1.63.0` | pure python |
| `pydantic==2.13.3` | pure python |
| `pypdfium2==5.7.1` | `py3-none-*`, version-agnostic |
| `Pillow==12.2.0` | 3.10-3.14 |
| `pandas==3.0.2` | 3.11-3.14 |
| `openpyxl==3.1.5` | pure python |
| `python-dotenv==1.2.2` | pure python |

Note this pillar does not pin `pyarrow`, so pip resolves the newest one, which
has a 3.14 wheel. That is safer than Pillar 1's explicit pin was - leave it
unpinned.

**One thing to fix before the first push.**
`tests/test_deploy_readiness.py` has a good guard -
`test_the_public_readme_carries_no_commercial_or_employer_detail` asserts the
README publishes no pricing or employer name. But the guard spells those names
out as literals in the test file, and `tests/` is committed. The assertion is
worth keeping; move the forbidden strings out of the source, for example by
building them from parts or reading them from a gitignored fixture, so the guard
survives without the repo carrying the very words it is checking for. Pillar 1
hit the identical problem and it was cheaper to fix before the first push than
after, because scrubbing a public history means a force-push.

Before the first push, check whether this pillar needs an API key at runtime. If
it does, it goes in Streamlit Cloud under Settings -> Secrets, never in a
tracked file, and `.env` must be gitignored.

## Next action here

**Phase A, step 1: `git init` and push, then deploy.** Follow the recipe above.
The dependency pre-flight is already done and clean, so the failure that hit
Pillar 1 will not repeat here.

After deploying, smoke-test the live URL cold: first paint, the upload path with
a real invoice, and the page at 375 px. Note that Streamlit Cloud renders the app
inside an iframe, so DOM checks must query `iframe[title="streamlitApp"]`
.contentDocument, not the outer document.
