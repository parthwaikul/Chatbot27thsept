"""Every user-facing string, in one place (deliverable D-5).

Phase 5 task 1 puts the disclaimer, the freshness prefix, and each refusal /
redirect / decline here rather than inline in the router, the gate, the
validator and the view. That is the point: when the same sentence is written in
three components they drift, and the copy that reaches a user is the last place
a drift is visible. Every string below is asserted verbatim by
``tests/test_pii.py``, ``tests/test_intent.py`` and ``tests/test_validator.py``,
so a change to one is a test failure rather than a silent copy change.

Two link sets are *not* strings and are read from config, per the Q2 decision
recorded in ``CHUNKING.md``:

* ``EDUCATIONAL_LINK`` — one public, non-blog educational page, attached to
  refusals and declines (C-5, C-6).
* ``FACTSHEET_LINK_MAP`` — per-scheme official factsheet links for the
  performance redirect (C-3).

Both are read through :func:`educational_link` / :func:`factsheet_link`, which
return ``None`` rather than raising when a value is still unset, so a missing
link degrades the message instead of taking the whole answer down (NFR-4). The
value is read via ``Settings.require`` only by callers that treat it as
release-blocking.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

# -- deliverable D-5, FR-20 ----------------------------------------------------

#: Recorded as deliverable D-5 and shown by the UI (FR-20). Verbatim, including
#: the full stop: the brief specifies this exact wording.
DISCLAIMER = "Facts-only. No investment advice."

#: FR-14 / C-4. The value after the colon comes from K17
#: (:mod:`src.query.freshness`); the prefix itself is a fixed string and lives
#: here so it cannot drift between the renderer and the tests.
FRESHNESS_PREFIX = "Last updated from sources: "

# -- refusals and redirects ----------------------------------------------------

PII_REFUSAL = (
    "I can't help with that. This assistant does not accept, store, or share "
    "personal identifiers such as PAN, Aadhaar, account numbers, OTPs, email "
    "addresses, or phone numbers. Please don't share them here."
)

ADVICE_REFUSAL = (
    "I answer factual questions only, so I can't tell you whether to buy, sell, "
    "or hold a scheme, or which one is better for you. "
    "Facts such as expense ratio, exit load, minimum SIP, lock-in, riskometer, "
    "and benchmark are in the sources I use."
)

PERFORMANCE_REDIRECT = (
    "I don't state, compare, or calculate returns or performance. The official "
    "monthly factsheet publishes those figures."
)

NOT_IN_SOURCES_DECLINE = (
    "I couldn't answer that from the sources I'm limited to. I only answer "
    "questions about the five HDFC scheme pages in my sources."
)

CLARIFY_SCHEME_PROMPT = (
    "Which scheme do you mean? I have these five HDFC schemes:\n{schemes}\n"
    "Ask me about a specific one and I'll give the facts from its page."
)

#: K16 safe decline, used when the validator rejects the output twice
#: (architecture.md §8.6). Never exposes the rejected text, so a hallucinated
#: or non-compliant answer cannot reach the user even as an error message.
VALIDATOR_DECLINE = (
    "I couldn't produce an answer I could stand behind from the sources I'm "
    "limited to, so I'm not going to guess. "
)

#: NFR-4. Shown when the LLM is unreachable (rate limit, timeout, no key).
#: Deliberately names no exception text, so a key or URL cannot leak through an
#: error message.
LLM_ERROR_MESSAGE = (
    "I couldn't reach the answer service just now. Please try again in a "
    "moment."
)

#: NFR-4 for an unhandled failure, per architecture.md §10.
UNEXPECTED_ERROR_MESSAGE = (
    "Something went wrong on my side and I couldn't produce an answer. "
    "Please try rephrasing your question."
)


def scheme_list(schemes: Iterable[str]) -> str:
    """Render the registered schemes as a numbered list for a clarify prompt.

    Numbered rather than bulleted so a user can answer "2" and be understood,
    which is the point of asking (Q5).
    """
    return "\n".join(f"{i}. {name}" for i, name in enumerate(schemes, start=1))


# -- link helpers (Q2) ---------------------------------------------------------


def educational_link(settings) -> Optional[str]:
    """The configured educational link, or ``None`` when it is still unset.

    Read with a plain attribute rather than ``Settings.require`` so an unset
    value degrades a message rather than raising inside the query path; Q2
    blocks *release*, not the first run.
    """
    value = (getattr(settings, "educational_link", None) or "").strip()
    return value or None


def factsheet_link(source_id: str, settings) -> Optional[str]:
    """The configured factsheet link for ``source_id``, or ``None``.

    ``source_id`` is a K1 registry id (``hdfc_small_cap``), which is the key
    format ``FACTSHEET_LINK_MAP`` uses. Falls back to ``None`` when the map is
    unset or has no entry for this scheme, so a partial map still redirects
    correctly for the schemes it covers.
    """
    mapping: Optional[dict] = getattr(settings, "factsheet_link_map", None)
    if not mapping:
        return None
    value = (mapping.get(source_id) or "").strip()
    return value or None


def with_link(message: str, link: Optional[str], label: str = "Learn more") -> str:
    """Append ``link`` to ``message`` on its own line, when there is one.

    Kept as a function rather than an f-string at each call site so the
    "no trailing punctuation after a bare URL" rule is applied once, and so a
    missing link leaves the message clean instead of leaving a dangling label.
    """
    if not link:
        return message
    return f"{message}\n{label}: {link}"


def refusal_block(message: str, link: Optional[str], label: str = "Learn more") -> str:
    """A refusal/decline message plus its educational link, newline-joined."""
    return with_link(message, link, label)


def factsheet_redirect_block(
    message: str, source_id: Optional[str], settings
) -> str:
    """The C-3 performance redirect, with the scheme's own factsheet link.

    Falls back to the educational link when the scheme has no configured
    factsheet, so a performance question is always redirected to *something*
    official rather than silently losing its link.
    """
    link = factsheet_link(source_id, settings) if source_id else None
    label = "Official factsheet" if link else "Learn more"
    return refusal_block(message, link or educational_link(settings), label)


def all_strings() -> Sequence[str]:
    """Every user-facing constant, for the test that pins the copy."""
    return (
        DISCLAIMER,
        FRESHNESS_PREFIX,
        PII_REFUSAL,
        ADVICE_REFUSAL,
        PERFORMANCE_REDIRECT,
        NOT_IN_SOURCES_DECLINE,
        CLARIFY_SCHEME_PROMPT,
        VALIDATOR_DECLINE,
        LLM_ERROR_MESSAGE,
        UNEXPECTED_ERROR_MESSAGE,
    )
