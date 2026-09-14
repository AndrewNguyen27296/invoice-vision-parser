"""
Deployment and demo-integrity invariants.

This repository is a client-facing showcase: a public GitHub repo behind a
"View Code" button and a live Streamlit Community Cloud URL behind an "Open Live
Demo" button. That makes a handful of properties load-bearing in a way ordinary
application tests do not cover, so they are pinned here:

  * the demo NEVER shows confidently wrong data
  * it boots with no API key, no secrets and no system packages
  * it survives a read-only filesystem
  * a visitor's own upload is never handed to a provider that trains on it
  * nothing internal or confidential is publishable
"""

from __future__ import annotations

import ast
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from parser import extractor as EX
from parser.cost_guard import BudgetExceeded, CostGuard, GuardConfig
from parser.pdf_utils import file_fingerprint
from parser.providers import ProviderError, available_providers, get_provider
from parser.providers.demo_provider import DemoProvider
from parser.schemas import InvoiceData

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "sample_invoices"
BUNDLED = ("1_clean_freight.pdf", "2_messy_wholesale.pdf", "3_skewed_scan.pdf")
SCAN = SAMPLES / "3_skewed_scan.pdf"


def guard(tmp_path: Path, **over) -> CostGuard:
    cfg = GuardConfig()
    cfg.cache_dir = tmp_path / ".cache"
    for k, v in over.items():
        setattr(cfg, k, v)
    return CostGuard(cfg)


# ============================================================ demo integrity
def test_every_bundled_sample_extracts_correctly_with_no_api_key(tmp_path):
    """Invariant 3: one-click end-to-end demo, no key, no upload required."""
    truth = json.loads((SAMPLES / "ground_truth.json").read_text(encoding="utf-8"))
    g = guard(tmp_path)
    for name in BUNDLED:
        result = EX.extract_invoice(SAMPLES / name, guard=g, provider=get_provider("demo"))
        expected = truth[name]
        assert result.invoice.vendor_name.upper() == expected["vendor_name"].upper(), name
        assert result.invoice.total_amount == Decimal(str(expected["total_amount"])), name
        assert len(result.invoice.line_items) == expected["line_item_count"], name
        assert result.cost_usd == 0.0, f"{name} must be free"


def test_the_scan_is_never_answered_with_another_invoice(tmp_path):
    """The regression this whole provider exists for.

    With the `mock` provider, the scanned travel invoice came back as Nordfrakt
    Logistik AB / SEK 1,937.50 under a green "Math Audit Passed" badge -- the
    wrong vendor, currency and total, presented to a prospect as a success.
    """
    result = EX.extract_invoice(SCAN, guard=guard(tmp_path), provider=get_provider("demo"))
    assert result.invoice.vendor_name == "Atlas Corporate Travel Ltd"
    assert result.invoice.currency == "GBP"
    assert result.invoice.total_amount == Decimal("1313.40")
    assert "Nordfrakt" not in (result.invoice.vendor_name or "")


def test_the_demo_provider_refuses_rather_than_inventing(tmp_path):
    """It can be unhelpful. It cannot be wrong."""
    from PIL import Image
    unknown = tmp_path / "someone_elses_scan.png"
    Image.new("RGB", (900, 1200), "white").save(unknown)

    with pytest.raises(ProviderError) as exc:
        EX.extract_invoice(unknown, guard=guard(tmp_path), provider=get_provider("demo"))
    message = str(exc.value)
    assert "will not invent" in message
    assert "PARSER_PROVIDER" in message, "the refusal must say how to fix it"


def test_a_fixture_is_labelled_as_canned_not_as_a_vision_model(tmp_path):
    result = EX.extract_invoice(SCAN, guard=guard(tmp_path), provider=get_provider("demo"))
    assert result.canned is True
    assert "canned" in result.route
    assert "vision model" not in result.route


def test_a_real_extraction_is_not_labelled_canned(tmp_path):
    result = EX.extract_invoice(SAMPLES / BUNDLED[0], guard=guard(tmp_path))
    assert result.strategy == EX.STRATEGY_TEXT_LAYER
    assert result.canned is False


def test_fixtures_are_keyed_by_content_not_filename(tmp_path):
    original = SCAN
    renamed = tmp_path / "renamed.pdf"
    renamed.write_bytes(original.read_bytes())
    assert DemoProvider().knows(renamed)


def test_every_fixture_audits_the_way_its_ground_truth_says(tmp_path):
    """A fixture that failed its own audit would make the demo lie."""
    truth = json.loads((SAMPLES / "ground_truth.json").read_text(encoding="utf-8"))
    fixtures = json.loads((SAMPLES / "demo_fixtures.json").read_text(encoding="utf-8"))
    for fingerprint, record in fixtures["fixtures"].items():
        name = record["_file"]
        invoice = InvoiceData.model_validate(
            {k: v for k, v in record.items() if not k.startswith("_")})
        audit = invoice.run_audit()
        verdict = "PASS" if audit.passed else ("FAIL" if audit.failures else "INCONCLUSIVE")
        assert verdict == truth[name]["expected_audit"], name
        assert fingerprint == file_fingerprint(SAMPLES / name), f"{name} hash drifted"


# ======================================================== data protection
def test_every_provider_declares_whether_it_trains_on_submitted_content():
    for name in available_providers():
        provider = get_provider(name)
        assert isinstance(provider.trains_on_data, bool), name


def test_only_the_gemini_free_tier_is_marked_as_training_on_data(monkeypatch):
    for name in ("anthropic", "demo", "mock"):
        assert get_provider(name).trains_on_data is False, name

    monkeypatch.setenv("PARSER_GEMINI_FREE_TIER", "1")
    assert get_provider("gemini").trains_on_data is True
    monkeypatch.setenv("PARSER_GEMINI_FREE_TIER", "0")
    assert get_provider("gemini").trains_on_data is False


# ===================================================== hostile filesystem
def unwritable_dir(tmp_path: Path) -> Path:
    """A path that cannot be created, on POSIX and on Windows alike.

    `/dev/null/x` only works on POSIX; on Windows it is an ordinary relative
    path and mkdir happily succeeds. Nesting under a regular file fails
    everywhere with NotADirectoryError, which is an OSError.
    """
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("a file, so nothing can be created beneath it", encoding="utf-8")
    return blocker / "cache"


def test_an_unwritable_cache_dir_does_not_stop_the_app_booting(tmp_path):
    """A read-only host used to raise straight out of the constructor, so the
    page never rendered at all."""
    target = unwritable_dir(tmp_path)
    cfg = GuardConfig()
    cfg.cache_dir = target
    g = CostGuard(cfg)
    assert g.cache_dir_fell_back is True
    assert g.config.cache_dir != target


def test_a_free_route_survives_an_unaccountable_ledger(tmp_path):
    """No money at stake means nothing to account for, so nothing to refuse."""
    g = guard(tmp_path)
    g._ledger_unwritable = True
    g.preflight(SAMPLES / BUNDLED[0], 1, free=True)          # must not raise
    with pytest.raises(BudgetExceeded):
        g.preflight(SAMPLES / BUNDLED[0], 1, free=False)     # paid still refused


def test_the_whole_demo_runs_with_a_broken_cache_dir(tmp_path):
    cfg = GuardConfig()
    cfg.cache_dir = unwritable_dir(tmp_path)
    g = CostGuard(cfg)
    for name in BUNDLED:
        result = EX.extract_invoice(SAMPLES / name, guard=g, provider=get_provider("demo"))
        assert result.audit is not None
        assert result.cost_usd == 0.0


# ======================================================= repository hygiene
def _requirements(path: str) -> set[str]:
    out = set()
    for line in (ROOT / path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "-")):
            out.add(re.split(r"[=<>!;]", line)[0].strip().lower())
    return out


def test_requirements_are_pinned_exactly():
    """Invariant 2: reproducible, fast Cloud builds."""
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line and not line.startswith("-"):
            assert "==" in line, f"unpinned requirement: {line!r}"


def test_requirements_cover_every_third_party_import_the_app_makes():
    declared = _requirements("requirements.txt")
    alias = {"PIL": "pillow", "dotenv": "python-dotenv"}
    optional = {"anthropic", "google"}          # lazily imported inside providers
    stdlib = set(sys.stdlib_module_names)
    local = {"parser", "utils", "scripts", "tests"}

    files = [ROOT / "app.py"] + sorted(ROOT.glob("parser/**/*.py")) + \
            sorted(ROOT.glob("utils/**/*.py"))
    for file in files:
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name in stdlib or name in local or name in optional or name.startswith("_"):
                    continue
                assert alias.get(name, name).lower() in declared, \
                    f"{file.name} imports {name!r}, which requirements.txt does not declare"


def test_dev_only_tooling_is_not_in_the_runtime_requirements():
    runtime = _requirements("requirements.txt")
    for package in ("pytest", "reportlab"):
        assert package not in runtime, f"{package} would slow every Cloud build"
    assert "pytest" in _requirements("requirements-dev.txt")


def test_no_system_packages_are_required():
    assert not (ROOT / "packages.txt").exists(), \
        "a packages.txt means the deploy needs apt, which this one must not"


def test_the_entrypoint_is_app_py_at_the_repository_root():
    assert (ROOT / "app.py").is_file()


def _scannable_sources():
    """Repo source files worth scanning, excluding this file.

    A test that greps the tree for forbidden strings necessarily contains those
    strings itself, so scanning itself is a guaranteed false positive.
    """
    here = Path(__file__).resolve()
    for file in list(ROOT.glob("**/*.py")) + [ROOT / "requirements.txt"]:
        if any(part.startswith((".", "_")) for part in file.parts):
            continue
        if file.resolve() == here:
            continue
        yield file


def test_repository_is_standalone_with_no_cross_project_references():
    """Invariant 1: zero dependencies on sibling projects.

    Code only, deliberately. Invariant 1 is a property of what the app imports
    and reads, not of what the docs discuss: CLAUDE.md carries the portfolio
    plan on purpose, so scanning markdown here would need an exemption for the
    one file it would flag. Published markdown is checked for the thing that
    actually matters in docs -- commercial and employer detail -- by
    `test_no_published_markdown_carries_commercial_or_employer_detail`.
    """
    pattern = re.compile(r"Pillar\s*[13]|AI Startup|C:\\\\Projects|\.\./\.\./")
    for file in _scannable_sources():
        assert not pattern.search(file.read_text(encoding="utf-8")), \
            f"{file.name} references something outside this repository"


def test_gitignore_protects_secrets_internal_notes_and_real_documents():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for entry in (".env", ".streamlit/secrets.toml", "_internal/", "RUNBOOK.md",
                  "real_invoices/*", ".cache/", "tests/.private_terms"):
        assert entry in ignored, f".gitignore must exclude {entry}"
    assert "!real_invoices/README.md" in ignored, "keep the workflow doc"


def _published_sources():
    """Every markdown and Python file a visitor to the public repo can read.

    RUNBOOK.md, `_internal/` and dot-directories are gitignored, so they are
    never published and are not scanned. This module is skipped too: a guard
    that greps for a pattern necessarily contains that pattern.
    """
    here = Path(__file__).resolve()
    for pattern in ("**/*.md", "**/*.py"):
        for file in ROOT.glob(pattern):
            relative = file.relative_to(ROOT)
            # Directory parts only, so `__init__.py` is still scanned.
            if any(part.startswith((".", "_")) for part in relative.parts[:-1]):
                continue
            if relative.name.startswith("."):
                continue
            if relative.as_posix() == "RUNBOOK.md" or file.resolve() == here:
                continue
            yield file


def _private_terms() -> list[str]:
    """Employer and client names, read from a gitignored fixture.

    A guard that greps for a forbidden word cannot spell that word in a
    committed file without publishing the very thing it exists to protect. The
    names live one per line in `tests/.private_terms`, which .gitignore
    excludes. Absent the fixture the structural checks still run and the name
    check reports itself as unarmed rather than passing silently.
    """
    fixture = Path(__file__).resolve().parent / ".private_terms"
    if not fixture.exists():
        return []
    return [line.strip() for line in fixture.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def test_no_published_file_carries_commercial_or_employer_detail():
    """The repo sits behind a public "View Code" button.

    Scans every published markdown and Python file, not only the README.
    CLAUDE.md ships too, and being the working plan it is the file most likely
    to accumulate portfolio pricing; the rates once leaked into a code comment
    in this very module, as an example of what the day-rate pattern catches.

    The commercial-prose checks are markdown-only on purpose. An invoice
    parser has dollar figures all through its code and fixtures -- schemas.py
    carries a worked example -- so a "$N,NNN" rule over Python would flag the
    domain itself. Names, local paths and day rates are checked everywhere.
    """
    private = _private_terms()

    for file in _published_sources():
        text = file.read_text(encoding="utf-8")
        name = file.relative_to(ROOT).as_posix()

        if file.suffix == ".md":
            for leak in ("Deal Size", "Upsell", "Pitch Script", "/hr",
                         "per month / client"):
                assert leak not in text, f"{name} must not publish {leak!r}"
            assert re.search(r"\$[0-9],[0-9]{3}", text) is None, \
                f"{name} publishes a price point"

        # Also catches a bare day rate or retainer, which the comma pattern
        # above misses because such a figure has no thousands separator.
        # Deliberately described, not exemplified: a real rate written here
        # would be the very leak this line exists to stop.
        assert re.search(r"\$\s?[0-9]{3}\s?(?:[-\u2013\u2014/]|per\b)", text) is None, \
            f"{name} publishes a day rate or retainer"
        # A drive letter is a single letter; the lookbehind stops "https://"
        # and other URL schemes reading as one.
        assert re.search(r"(?<![A-Za-z])[A-Za-z]:[\\/]", text) is None, \
            f"{name} publishes a local filesystem path"

        # Reported without echoing the term: a CI log is public too.
        for leak in private:
            assert leak.lower() not in text.lower(), \
                f"{name} publishes a private name listed in tests/.private_terms"


@pytest.mark.skipif(not _private_terms(),
                    reason="tests/.private_terms absent: employer-name guard not armed")
def test_the_employer_name_guard_is_armed():
    """Reports whether the name check above is actually doing anything.

    Skips on a fresh clone, where the gitignored fixture is legitimately
    absent and a visitor running pytest should still see green. On the
    author's machine -- the only place the README is edited, and so the only
    place the guard matters -- the fixture is present and this runs. A skip
    here is the signal that the fixture was lost; `pytest -rs` shows it.
    """
    assert _private_terms(), "tests/.private_terms must list the names to exclude"


def test_streamlit_config_pins_the_theme_the_verdict_colours_assume():
    config = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert 'base = "light"' in config, \
        "the audit verdict is encoded in colour; a viewer-flipped dark theme breaks it"
    assert "showErrorDetails = false" in config, "never show a prospect a traceback"


# ============================================================= non-compete
def test_no_energy_or_utility_domain_content_anywhere():
    """Invariant 4, checked mechanically rather than trusted."""
    banned = re.compile(
        r"\b(kwh|mwh|smart meter|electricity|utility bill|district heating|"
        r"carbon footprint|scope [123])\b", re.IGNORECASE)
    # A line that names the domain in order to EXCLUDE it is the invariant being
    # honoured, not broken. Allow prohibitions; flag everything else.
    exempt = re.compile(
        r"non-compete|out of scope|outside the scope|excluded|exclusively|"
        r"\bno energy\b|stay inside|guardrail|must not|never use", re.IGNORECASE)

    here = Path(__file__).resolve()
    files = list(ROOT.glob("**/*.py")) + list(ROOT.glob("**/*.json")) + \
        [ROOT / "README.md", ROOT / "real_invoices" / "README.md"]
    for file in files:
        if any(part.startswith((".", "_")) for part in file.parts):
            continue
        if file.resolve() == here or not file.exists():
            continue
        lines = file.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if not banned.search(line):
                continue
            # Prose wraps, so the word that makes this a prohibition ("...are
            # explicitly out of scope") is often on the next line. Judge the
            # sentence, not the line.
            window = " ".join(lines[max(0, index - 1):index + 2])
            if not exempt.search(window):
                pytest.fail(
                    f"{file.name}:{index + 1} mentions out-of-scope domain: {line.strip()}")
