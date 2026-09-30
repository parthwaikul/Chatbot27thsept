"""K15 — the answer validator (architecture.md §8.6).

The last stage before rendering, and the reason the PRD's constraints are
*structural* rather than prompt-suggested. Prompt rule 2 says "at most 3
sentences"; this module is what makes it true. A model that ignores a rule does
not get its output rendered, it gets one repair attempt and then a safe decline.

Checks, in the order they run, because the order decides what the user sees when
an answer breaks several rules at once:

===========================  ==============================  ==========================
Check                        Rule                            On failure
===========================  ==============================  ==========================
Grounding marker             output is ``NOT_IN_SOURCES``    route to decline path
Performance marker           output is ``FACTSHEET_REDIRECT`` route to redirect
Length                       ≤ 3 sentences, links/freshness  one repair retry, decline
                             stripped first
Citation                     exactly 1 URL, and it is in    one repair retry, then
                             the retrieved chunks' URL set   re-cite from metadata
Advice lexicon               no advice matches               safe decline
Returns lexicon              no returns matches              factsheet redirect
Freshness                    ``Last updated from sources:``  renderer appends it
===========================  ==============================  ==========================

The two lexicons are separate because they have different *consequences*: advice
in an answer is a compliance failure (C-5) and declines, while a returns
statement is a missing feature that a user can satisfy themselves by opening the
factsheet (C-3), so it redirects. Merging them would either let advice through or
send a factual answer to a page that does not answer it.

Repair is **exactly one** retry, then a safe decline. More than one would let a
model talk itself into compliance, and the whole point is that a non-compliant
output is replaced rather than rendered. The rejected text is never included in
the decline: it may contain the invented URL or the advice the lexicon just
caught, and echoing it would defeat the check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple

from src.rag.prompts import FACTSHEET_REDIRECT, NOT_IN_SOURCES

# -- lexicons (architecture.md §8.6) -------------------------------------------

#: Advice lexicon. Rule 4 of the system prompt names "should I", "better" and
#: "best for you"; these are the surface forms of that rule. Matched
#: case-insensitively on word boundaries, so "suitable" does not fire inside
#: "suitable" written as part of a longer word and "best" does not fire inside
#: "request".
ADVICE_LEXICON: Tuple[str, ...] = (
    "should i",
    "should you",
    "you should",
    "i would suggest",
    "i would recommend",
    "i suggest",
    "i recommend",
    "we recommend",
    "recommend",
    "recommended",
    "best for you",
    "best for me",
    "better option",
    "better choice",
    "is better",
    "safer bet",
    "good buy",
    "worth buying",
    "worth investing",
    "suitable for you",
    "suitable for me",
    "ideal for you",
    "ideal for me",
    "right for you",
    "right for me",
    "your portfolio",
    "allocate",
    "invest in",
    "buy the",
    "sell the",
    "hold on to",
    "exit at",
)

#: Returns lexicon. Rule 5 forbids stating, computing, comparing or ranking
#: returns. Note that "return" also appears in "returns period"/"exit load
#: conditions"; the entries below are the noun forms a performance claim uses,
#: which is narrower and does not fire on "if redeemed within 1 year".
RETURNS_LEXICON: Tuple[str, ...] = (
    "cagr",
    "xirr",
    "annualised return",
    "annualized return",
    "average return",
    "returns of",
    "return of",
    "gave a return",
    "has returned",
    "has grown by",
    "grown by",
    "outperformed",
    "underperformed",
    "performed better",
    "performs better",
    "performed best",
    "best performing",
    "top performing",
    "higher returns",
    "better returns",
    "returns compared",
    "compared the returns",
    "p.a.",
    "year on year",
    "yoy return",
)

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

#: Lines the renderer adds, stripped before counting sentences so the model's
#: three-sentence budget is not spent on a URL and a date it was told to emit.
_STRIPPABLE_PREFIXES: Tuple[str, ...] = (
    "source:",
    "last updated from sources:",
    "see also",
    "read more",
)


def _word_pattern(phrase: str) -> re.Pattern:
    return re.compile(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", re.IGNORECASE)


def _lexicon_hits(text: str, lexicon: Sequence[str]) -> Tuple[str, ...]:
    """Every lexicon entry present in ``text``."""
    return tuple(entry for entry in lexicon if _word_pattern(entry).search(text or ""))


def count_sentences(text: str) -> int:
    """Sentences in ``text`` after removing URLs and renderer-owned lines.

    A trailing URL and the freshness line are excluded on purpose: the model was
    instructed to emit them, so charging them against FR-13's sentence budget
    would reject a compliant answer.
    """
    if not text or not text.strip():
        return 0
    body = _URL_RE.sub(" ", text)
    kept: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith(_STRIPPABLE_PREFIXES):
            continue
        kept.append(stripped)
    joined = " ".join(kept)
    return len([part for part in _SENTENCE_SPLIT.split(joined) if part.strip()])


def urls_in(text: str) -> Tuple[str, ...]:
    """Every URL in ``text``."""
    return tuple(_URL_RE.findall(text or ""))


# -- the repair prompt ---------------------------------------------------------

#: Sent once, on the single retry. Asks for the specific corrections and does
#: not restate the whole system prompt: a near-miss (a fourth sentence, a second
#: link) does not need the full contract resent, and resending it invites the
#: model to re-derive rather than fix.
REPAIR_INSTRUCTION = """Your previous reply did not meet the required format. Reply again,
using only the same CONTEXT, and fix exactly these problems:
- at most 3 sentences of content
- no advice or recommendation language
- no returns, performance, or CAGR figures
- if the CONTEXT cannot answer the question, reply exactly: NOT_IN_SOURCES
Do not add any other text."""


@dataclass(frozen=True)
class ValidationResult:
    """The verdict on one candidate answer, and which check produced it.

    ``ok`` is the only field the pipeline branches on. ``failed_checks`` is kept
    for the log and the eval report, since E-2/E-3/E-4 are *measured* by this
    module and a report that cannot say which rule fired is not much use
    (architecture.md §8.6).
    """

    ok: bool
    #: ``None`` when the answer passed, otherwise the marker to route on.
    marker: Optional[str] = None
    failed_checks: Tuple[str, ...] = field(default_factory=tuple)
    advice_hits: Tuple[str, ...] = field(default_factory=tuple)
    returns_hits: Tuple[str, ...] = field(default_factory=tuple)
    sentence_count: int = 0
    urls: Tuple[str, ...] = field(default_factory=tuple)
    #: True when the only failure was a missing freshness line, which the
    #: renderer fixes itself and therefore does not warrant a retry.
    freshness_missing: bool = False

    @property
    def is_marker(self) -> bool:
        return self.marker is not None

    @property
    def repairable(self) -> bool:
        """Whether one repair retry could plausibly fix this.

        Lexicon failures are not repairable: the validator's own documented
        consequence for advice is a safe decline, not a re-ask, so retrying would
        be second-guessing the control rather than repairing a formatting miss.
        A length or citation failure is a formatting miss and is retryable.
        """
        if self.advice_hits or self.returns_hits:
            return False
        return bool(set(self.failed_checks) & {"length", "citation"})


def validate(
    text: str,
    retrieved_urls: Sequence[str] = (),
    max_sentences: int = 3,
) -> ValidationResult:
    """Check one candidate answer against every K15 rule.

    ``retrieved_urls`` is the set of ``source_url`` values from the chunks that
    were actually retrieved. The citation check requires the model's link to be a
    member of it, which catches a plausible-looking but out-of-corpus URL
    (architecture.md §8.6). An empty ``retrieved_urls`` means the caller is
    validating without context, in which case the citation check is skipped
    rather than failed — there is no set to be a member of.
    """
    raw = (text or "").strip()
    allowed = {str(url).strip() for url in retrieved_urls if str(url).strip()}

    # Markers first and exactly: a marker is the model's compliant way of
    # declining or redirecting, and it carries no citation and no sentences.
    if raw == NOT_IN_SOURCES:
        return ValidationResult(ok=True, marker=NOT_IN_SOURCES)
    if raw == FACTSHEET_REDIRECT:
        return ValidationResult(ok=True, marker=FACTSHEET_REDIRECT)

    failed: list[str] = []
    sentences = count_sentences(raw)
    links = urls_in(raw)
    advice = _lexicon_hits(raw, ADVICE_LEXICON)
    returns = _lexicon_hits(raw, RETURNS_LEXICON)
    freshness_ok = _has_freshness(raw)

    if sentences > max_sentences:
        failed.append("length")
    if allowed:
        if len(links) != 1:
            failed.append("citation")
        elif not any(link.rstrip(".,);") in allowed for link in links):
            failed.append("citation")
    if advice:
        failed.append("advice")
    if returns:
        failed.append("returns")

    # Freshness is deliberately *not* a failure. architecture.md §8.6 gives its
    # remedy as "renderer appends it from K17", and the model was never asked to
    # emit the line, so a missing prefix is expected on every answer. It is
    # recorded on the result for the E-7 report and the renderer completes it —
    # that is the "missing freshness line repaired" case, not a repair retry.

    if not failed:
        return ValidationResult(
            ok=True,
            sentence_count=sentences,
            urls=links,
            freshness_missing=not freshness_ok,
        )

    # Advice and returns carry their own documented consequences, so they decide
    # the route even when a formatting check also failed. A sentence count of
    # four *and* a returns figure is still a returns problem: the user must not
    # see the figure at all.
    marker: Optional[str] = None
    if advice:
        marker = None  # safe decline, handled by the caller
    elif returns:
        marker = FACTSHEET_REDIRECT

    return ValidationResult(
        ok=False,
        marker=marker,
        failed_checks=tuple(failed),
        advice_hits=advice,
        returns_hits=returns,
        sentence_count=sentences,
        urls=links,
        freshness_missing=not freshness_ok,
    )


def _has_freshness(text: str) -> bool:
    """Whether the mandated FR-14 prefix is already present.

    The renderer appends the line from K17, so a missing prefix is not a model
    failure — but it *is* recorded, because an answer that arrived without it and
    an answer the renderer had to complete are different provenance and the
    E-7 report should show the difference.
    """
    from src.guardrails.messages import FRESHNESS_PREFIX

    return FRESHNESS_PREFIX.strip().lower() in (text or "").lower()


def missing_freshness_note() -> str:
    """The E-7 log line for an answer the renderer had to complete."""
    return "freshness_line_added_by_renderer"
