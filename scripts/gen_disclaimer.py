"""Generate DISCLAIMER.txt (D-5, FR-20) from the message constants.

D-5 is "the disclaimer snippet used in the UI, recorded as a deliverable". A
file that *records* a string is a copy, and copies drift: someone rewords
ADVICE_REFUSAL, the deliverable still shows the old text, and the submission now
misrepresents the product. Reading the constants instead makes the drift
impossible — the file is the code.

Run after changing any wording in ``src/guardrails/messages.py``:

    python scripts/gen_disclaimer.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load  # noqa: E402
from src.guardrails import messages  # noqa: E402

#: (heading, requirement, attribute). Order is the order a reviewer reads it in:
#: what the product always shows, then each refusal, then the failure states.
SECTIONS = (
    ("Shown on every response — FR-20, C-4", "Disclaimer", "DISCLAIMER"),
    (
        "Shown on every response — FR-14, C-4",
        "Freshness line",
        "FRESHNESS_PREFIX",
    ),
    ("Refusals — FR-10, FR-11, C-2, C-3", "Personal identifiers (K9 screen)", "PII_REFUSAL"),
    ("Refusals — FR-10, FR-11, C-2, C-3", "Advice and opinion", "ADVICE_REFUSAL"),
    ("Refusals — FR-10, FR-11, C-2, C-3", "Returns and performance", "PERFORMANCE_REDIRECT"),
    ("Refusals — FR-10, FR-11, C-2, C-3", "Not in sources", "NOT_IN_SOURCES_DECLINE"),
    ("Refusals — FR-10, FR-11, C-2, C-3", "Ambiguous question (K10)", "CLARIFY_SCHEME_PROMPT"),
    ("Refusals — FR-10, FR-11, C-2, C-3", "Answer rejected by K15", "VALIDATOR_DECLINE"),
    ("Failure states — NFR-4", "Answer service unreachable", "LLM_ERROR_MESSAGE"),
    ("Failure states — NFR-4", "Unexpected failure", "UNEXPECTED_ERROR_MESSAGE"),
)


def _value(attribute: str) -> str:
    """The constant, with the clarify prompt's placeholder made explicit."""
    text = getattr(messages, attribute)
    if "{schemes}" in text:
        text = text.replace("{schemes}", "\n    1. HDFC Large Cap Fund - Direct Growth\n    (…five schemes listed…)")
    return text.strip()


def build() -> str:
    settings = load()
    lines = [
        "DISCLAIMER.txt — deliverable D-5 (FR-20)",
        "",
        "Every user-facing message the assistant can show, copied from",
        "src/guardrails/messages.py by `python scripts/gen_disclaimer.py`. This file is",
        "generated, not maintained by hand, so it cannot drift from what the product",
        "actually renders.",
        "",
        "Read the first section as the answer to the brief's requirement: that snippet is",
        "the disclaimer, and it appears on every single response — answers, refusals and",
        "error states alike.",
        "",
        "=" * 78,
        "",
    ]

    for requirement, heading, attribute in SECTIONS:
        lines += [
            f"## {heading}",
            "",
            f"*{requirement}*",
            "",
            "```text",
            _value(attribute),
            "```",
            "",
        ]

    educational = (settings.educational_link or "").strip()
    lines += [
        "=" * 78,
        "",
        "## Links attached to a refusal",
        "",
        "A refusal never leaves the user at a dead end: the message is followed by one link,",
        "chosen by what was refused.",
        "",
        "| Refusal | Link |",
        "|---------|------|",
        f"| Advice / opinion (FR-10) | {educational or '(not configured)'} |",
        "| Personal identifiers (C-2) | none — there is nothing to link a person to |",
        "| Returns / performance (FR-11) | the scheme's official monthly factsheet, from `FACTSHEET_LINK_MAP` |",
        "| Not in sources | " + (educational or "(not configured)") + " |",
        "",
        "## Why the wording is fixed",
        "",
        "These strings are pinned by `tests/test_validator.py` and by the eval harness, which",
        "asserts that a refusal is produced rather than an answer. A refusal is a product",
        "feature, not an error message: the assistant's value is that it says \"that figure is",
        "not in my sources, here is where to read it\" instead of guessing. Rewording one of",
        "these without updating the tests is how that guarantee quietly stops being true.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    out = ROOT / "DISCLAIMER.txt"
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out.name} ({len(SECTIONS)} messages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
