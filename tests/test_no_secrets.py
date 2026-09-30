"""NFR-2 / NFR-5 — no secrets, and no PII, anywhere in the repo.

These are the checks that cannot be run against a running system, because the
damage they detect is already in the file tree. Everything here reads committed
artifacts and the working tree, so it is cheap, hermetic, and belongs in the
suite rather than in a review checklist.

Three things are asserted:

* ``.env`` is gitignored and ``.env.example`` is not — the key lives in one and
  the template is safe to publish.
* No Groq-shaped key appears in any tracked file, and none appears in an
  untracked-but-would-be-committed file either. The scan covers working-tree
  content, not just git's index, so a key written moments ago is still caught.
* No PII pattern appears under ``logs/``.

The key check is written so that it does not itself become a secret: it matches
a *shape*, ``gsk_`` plus 20 or more characters, rather than any specific key
value, and it never prints a match. Printing one would put the secret into the
test output and CI logs, which is the very failure being tested for.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Iterator, List, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Extensions a deliverable can plausibly be written in. Binary and vendored
#: trees are excluded: ``chroma/`` is a database, ``.venv/`` is third-party.
SCANNED_SUFFIXES = {".py", ".md", ".txt", ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini", ".sh"}

#: Directories never scanned: the virtualenv, the vector store, and git itself.
SKIP_DIRS = {".venv", "venv", "chroma", ".git", "logs", "corpus", "__pycache__", "node_modules"}

#: The shape of a Groq key. Deliberately a shape, not a value: a literal key in
#: a test file would be the leak this file exists to prevent.
#:
#: The character class includes ``_`` and ``-`` even though a real key is plain
#: alphanumerics, because a broader pattern is the safer default for a scan
#: whose cost is a false positive. The price of that choice is the allowlist
#: below, which has to be maintained — see :data:`SYNTHETIC_KEYS`.
KEY_SHAPE = re.compile(r"gsk_[A-Za-z0-9_\-]{20,}")

#: Deliberately fake keys used by `tests/test_prompts.py` to prove that an
#: exception message does not echo the key. They match KEY_SHAPE, and they are
#: not secrets, so the scan must not fire on them.
#:
#: Listed by exact value rather than by path or prefix, on purpose. A path-based
#: exemption ("skip tests/test_prompts.py") would quietly stop scanning a file
#: that may later contain a real key, and a prefix-based one ("skip anything
#: containing 'test'") would exempt a genuinely leaked key pasted into a test.
#: An exact-value list fails closed: a new fake key has to be added deliberately,
#: which is the moment to notice it is a real one.
SYNTHETIC_KEYS = frozenset(
    {
        "gsk_test_secret_value_1234",
        "gsk_not_a_real_key_abcdefghijklmnop",
    }
)

#: The same six classes the K9 screen detects, in a form that can be scanned for
#: over free text such as a log line.
PII_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("pan", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("aadhaar", re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")),
    ("otp", re.compile(r"\b(?:OTP|otp)\D{0,12}\d{4,8}\b")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("phone", re.compile(r"(?:\+91[\s-]?)?\b[6-9]\d{4}\s?\d{5}\b")),
    ("account_number", re.compile(r"(?i)account\s*(?:number|no\.?|#)\D{0,6}\d{8,18}")),
)


def _candidates() -> Iterator[Path]:
    """Every text file that would plausibly be committed."""
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in SCANNED_SUFFIXES:
            continue
        yield path


def _tracked() -> List[Path]:
    """Files git knows about, or the whole tree when this is not a repo."""
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return list(_candidates())
    if out.returncode != 0:
        return list(_candidates())
    return [ROOT / name for name in out.stdout.split() if (ROOT / name).is_file()]


# -- NFR-2: the key -------------------------------------------------------------


def test_env_is_gitignored():
    """.env holds the key, so it must never be committable (NFR-2, TC-4)."""
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    entries = {line.strip().rstrip("/") for line in ignore}
    assert ".env" in entries, ".gitignore must list .env"
    # A bare ".env" pattern also matches ".env.local", which is what we want.
    assert not any(line.strip() == ".env.example" for line in ignore), (
        ".env.example must stay committable — it is the committed template"
    )


def test_env_example_is_present_and_carries_no_key():
    template = ROOT / ".env.example"
    assert template.exists(), ".env.example is the committed template (NFR-2)"
    for line in template.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == "GROQ_API_KEY":
            assert not value.strip(), "GROQ_API_KEY must be empty in the template"
        assert not KEY_SHAPE.search(line), f"{name.strip()} looks like a key"


@pytest.mark.parametrize("path", _tracked(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_key_in_any_tracked_file(path):
    """The shape-based scan, over every tracked file.

    Deliberately does not include the offending text in the failure message: a
    pytest report is written to CI logs and read by people who should not see
    the key, so reporting the line would replicate the leak. The path alone is
    enough to find and fix it.
    """
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        pytest.skip(f"unreadable: {path}")
    found = {match for match in KEY_SHAPE.findall(content)} - SYNTHETIC_KEYS
    assert not found, (
        f"a Groq-shaped key appears in {path.relative_to(ROOT)}; rotate it and remove the line "
        "(the value is deliberately not shown — see this test's docstring)"
    )


def test_the_key_allowlist_holds_only_deliberate_fakes() -> None:
    """Keep the exemption list honest.

    An allowlist is a hole with a comment on it, so it needs the same scrutiny as
    the code it weakens. Each entry must actually be used, must exist, and must
    not be the configured key — that last check is the one that would catch
    somebody "fixing" a failing scan by adding the live key to the list.
    """
    from src.config import load

    settings = load()
    for fake in SYNTHETIC_KEYS:
        assert fake not in (settings.groq_api_key or ""), (
            "SYNTHETIC_KEYS contains the real key; remove it and rotate the key"
        )
        # A weak signal, but the useful direction: an exempt value has to *say*
        # it is fake. A key that reads like a real one belongs in neither the
        # allowlist nor the repo.
        assert any(
            marker in fake for marker in ("test", "fake", "dummy", "example", "not_a_real")
        ), f"SYNTHETIC_KEYS entry {fake!r} does not look like a deliberate fake"

    tree = {match for path in _candidates() for match in KEY_SHAPE.findall(
        path.read_text(encoding="utf-8", errors="ignore")
    )}
    assert tree - SYNTHETIC_KEYS == set(), (
        "a key-shaped string is present that is not on the allowlist; if it is a "
        "deliberate fake, add it to SYNTHETIC_KEYS, and if it is not, rotate the key"
    )
    unused = SYNTHETIC_KEYS - tree
    assert not unused, f"SYNTHETIC_KEYS lists keys nothing uses: {sorted(unused)}"


def test_no_assigned_key_outside_dotenv():
    """An assigned ``GROQ_API_KEY=`` is legitimate only in ``.env`` itself.

    Only whole lines that *begin* with the assignment count, so a prose mention
    in a doc — ``GROQ_API_KEY=`python -m venv ...`` inside a setup code block —
    is documentation rather than an assignment. The intent of the check is that
    no file carries a key value; a doc telling the reader which variable to fill
    in is exactly what the docs should do.
    """
    assignment = re.compile(r"^\s*(?:export\s+)?GROQ_API_KEY\s*=\s*(.*)$")
    for path in _candidates():
        if path.name == ".env":
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
        ):
            if line.lstrip().startswith("#"):
                continue
            found = assignment.match(line)
            if not found:
                continue
            value = found.group(1).strip().strip("'\"")
            if path.name == ".env.example":
                assert not value, f"{path.name}:{lineno} must leave the key empty"
                continue
            # An empty value, or a shell placeholder, is not a secret.
            assert value in ("", "${GROQ_API_KEY}", "$GROQ_API_KEY"), (
                f"{path.relative_to(ROOT)}:{lineno} assigns GROQ_API_KEY a literal value"
            )


def test_the_repo_actually_is_git_ignored_for_env():
    """``git check-ignore`` is the authoritative answer, not a pattern match."""
    try:
        out = subprocess.run(
            ["git", "check-ignore", "-q", ".env"], cwd=ROOT, capture_output=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git is unavailable in this environment")
    if out.returncode not in (0, 1):
        pytest.skip("not a git repository")
    assert out.returncode == 0, "git does not consider .env ignored"


def test_the_scan_covers_untracked_files_too() -> None:
    """``git ls-files`` answers "what will be committed", which is the right
    question for CI and the wrong one for a developer mid-work.

    A key written into a brand-new file is untracked until it is staged, so a
    tracked-only scan reports clean for exactly as long as it takes to commit.
    This asserts the whole-tree path exists and is not silently empty.
    """
    candidates = list(_candidates())
    assert candidates, "the working-tree scan found no files; the scan is broken"
    assert {p.name for p in candidates} >= {"config.py", "eval.py", "ingest.py"}


# -- NFR-2 / NFR-4: the key must not escape through ordinary Python ------------


def test_settings_repr_does_not_expose_the_key():
    """A dataclass repr is printed by pytest on any failure and by Streamlit in
    any traceback.

    Found the hard way: a frozen-dataclass assertion error in Phase 7 rendered
    the whole `Settings` object, key included, into the test output. `repr=False`
    on the field is the fix, and this test is what stops it being undone by an
    innocuous-looking refactor.
    """
    from src.config import load

    settings = load()
    key = settings.groq_api_key
    if not key:
        pytest.skip("no key configured in this environment")

    assert key not in repr(settings), "Settings repr exposes GROQ_API_KEY"
    assert key not in str(settings), "Settings str exposes GROQ_API_KEY"
    # And the value is still reachable for the code that needs it.
    assert settings.has_api_key() is True
    assert settings.groq_api_key == key


def test_a_settings_repr_is_still_useful() -> None:
    """Redaction must not cost the debugging value that made the repr worth
    having. Everything except the key should still be visible."""
    from src.config import load

    text = repr(load())
    assert "groq_model" in text
    assert "chroma_dir" in text
    assert "gsk_" not in text


def test_error_messages_name_the_missing_setting_not_its_value() -> None:
    """NFR-4: a failure must be reportable without the secret being reportable.

    Exercised on ``groq_model`` rather than the key itself, because the key is
    not a PENDING setting — it is read once at startup and its absence surfaces
    as the LLM error message. The property under test is the same one either
    way: the message says which variable to set and never carries a value.
    """
    from src.config import Settings
    from src.guardrails.messages import LLM_ERROR_MESSAGE, UNEXPECTED_ERROR_MESSAGE

    try:
        Settings(groq_model=None).require("groq_model")
    except Exception as exc:  # noqa: BLE001 - the message is what is under test
        assert "GROQ_MODEL" in str(exc), "the error must name the variable to set"
    else:
        pytest.fail("require() did not raise for an unset PENDING setting")

    for message in (LLM_ERROR_MESSAGE, UNEXPECTED_ERROR_MESSAGE):
        assert "gsk_" not in message
        assert "api key" not in message.lower()


# -- NFR-5: PII in logs ---------------------------------------------------------


def test_no_pii_under_logs():
    """Logs are a PII surface even though the app barely writes any (NFR-5).

    Absence is the passing state. If ``logs/`` does not exist there is nothing to
    scan and that is correct — architecture.md §11 has the PII screen run before
    anything is logged, so a populated log directory is itself a signal.
    """
    logs = ROOT / "logs"
    if not logs.exists():
        pytest.skip("no logs/ directory; nothing to scan")
    offenders: List[str] = []
    for path in sorted(logs.rglob("*")):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in PII_PATTERNS:
            if pattern.search(content):
                offenders.append(f"{path.relative_to(ROOT)}: {label}")
    assert not offenders, "PII found in logs: " + ", ".join(offenders)


def test_no_pii_in_the_shipped_deliverables():
    """SAMPLE_QA.md is a submission artifact (D-4, NFR-5).

    These two requirements pull against each other and the resolution matters:
    D-4 asks for a PII-block example, NFR-5 says the file contains no PII. The
    sample therefore demonstrates the screen using questions that name a
    *class* of identifier ("Send the statement to my email id") rather than one
    carrying a value. The screen does not care — it fires on the class cue — so
    nothing is weakened by not publishing a PAN.

    So: the whole file is scanned for identifier shapes, and separately the
    refusal wording must actually be present. Either test alone would pass
    vacuously — an empty sample has no PII and no refusals.
    """
    sample = ROOT / "SAMPLE_QA.md"
    if not sample.exists():
        pytest.skip("SAMPLE_QA.md not generated yet")
    content = sample.read_text(encoding="utf-8")

    offenders: List[str] = [
        f"{label} ({pattern.pattern})"
        for label, pattern in PII_PATTERNS
        if pattern.search(content)
    ]
    assert not offenders, "PII-shaped value found in SAMPLE_QA.md: " + ", ".join(offenders)

    from src.guardrails.messages import PII_REFUSAL

    assert PII_REFUSAL in content, "SAMPLE_QA.md must show the PII refusal (D-4)"


def test_the_pii_questions_shown_in_the_sample_carry_no_identifier():
    """Pin the resolution above so a later edit cannot quietly regress it.

    Someone extending the sample will naturally reach for ``PII_QUERIES[0]``,
    which reads as the obvious demo question and contains a PAN. This fails with
    a pointer to the identifier-free entries instead.
    """
    sample = ROOT / "SAMPLE_QA.md"
    if not sample.exists():
        pytest.skip("SAMPLE_QA.md not generated yet")
    content = sample.read_text(encoding="utf-8")

    from src.eval.cases import PII_QUERIES

    shown = [question for question in PII_QUERIES if question in content]
    assert shown, "no PII question from the case set is demonstrated (D-4)"
    for question in shown:
        assert not KEY_SHAPE.search(question)
        for label, pattern in PII_PATTERNS:
            assert not pattern.search(question), (
                f"SAMPLE_QA.md quotes a PII question carrying a {label} value; "
                f"use one of the identifier-free entries instead: {PII_QUERIES[-2:]}"
            )
