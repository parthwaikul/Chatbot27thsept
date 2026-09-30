"""K10 — the intent router (C-2, C-3, C-5, Q5).

Deterministic first, in a fixed order, with every keyword in one auditable table
(architecture.md §8.3). The order is the design, not an implementation detail:

    PII → ADVICE → PERFORMANCE → AMBIGUOUS → FACT → OUT_OF_SCOPE

* **PII is checked here too, as a belt-and-braces second gate.** K9 at step 0
  is the control that must run before storage; routing PII here as well means
  a caller who invokes the router directly still cannot get a PII question
  answered. It never runs before K9 in the pipeline.
* **ADVICE before PERFORMANCE**, because "Should I buy the one that gave higher
  returns?" is both, and a refusal is the safer of the two answers: it declines
  without asserting anything about returns. The reverse order would emit a
  factsheet link and imply the comparison is answerable.
* **AMBIGUOUS** is a *fact* question naming no scheme (Q5). It is detected
  after the two refusals because "Which fund is best for me?" names no scheme
  but is ADVICE, and asking "which of the five?" there would be absurd.

An LLM-assisted classifier is explicitly permitted as a fallback for genuinely
ambiguous input and is **not** implemented, because nothing in the corpus needs
it: the six refusal cases and the seven fact types are all covered by keyword
and pattern matching, so adding a model call here would add latency, cost and a
failure mode to a decision the table already makes deterministically. If a future
query is genuinely unroutable, this module should return ``OUT_OF_SCOPE`` and
let K12 decline — an LLM guess is not a safer default, it is a less
reproducible one.

The one thing this module owes the rest of the pipeline is
:attr:`IntentResult.expected_fact_type`: it is what makes fact-typed retrieval
work (see :func:`src.query.pipeline._retrieve`), and it is the mechanism by
which the riskometer source conflict is resolved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Optional, Pattern, Sequence, Tuple

from src.guardrails.pii import screen

#: The six categories of architecture.md §8.3, in evaluation order.
CATEGORIES: Tuple[str, ...] = (
    "PII",
    "ADVICE",
    "PERFORMANCE",
    "AMBIGUOUS",
    "FACT",
    "OUT_OF_SCOPE",
)

#: Categories that end the request with a message instead of retrieving.
#: ``FACT`` and ``OUT_OF_SCOPE`` continue to the relevance gate.
TERMINAL_CATEGORIES: Tuple[str, ...] = ("PII", "ADVICE", "PERFORMANCE", "AMBIGUOUS")

#: The seven FR-8 fact types this router recognises, each with its trigger
#: phrases. The values are ``FACT_TYPES`` from the chunker, so a fact type that
#: Phase 2 stops emitting would fail here too rather than silently routing to
#: OUT_OF_SCOPE.
#:
#: ``statement_guide`` is included even though the corpus does not carry it
#: (the recorded Q1 gap): routing it to FACT is correct, and K12 then declines
#: it for lack of a matching chunk, which is the behaviour the gap requires.
FACT_TYPE_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "expense_ratio": (
        "expense ratio",
        "expense ratios",
        "ter",
        "total expense ratio",
        "charges",
        "management fee",
    ),
    "exit_load": (
        "exit load",
        "exit loads",
        "redemption charge",
        "redemption fee",
        "short term",
        "if redeemed within",
    ),
    "min_sip": (
        "minimum sip",
        "min sip",
        "smallest sip",
        "sip amount",
        "minimum investment",
        "min investment",
        "first investment",
    ),
    "lock_in": (
        "lock-in",
        "lock in",
        "lockin",
        "lock-in period",
        "how long",
    ),
    "riskometer": (
        "riskometer",
        "risk-o-meter",
        "risk level",
        "risk category",
        "how risky",
    ),
    "benchmark": ("benchmark", "benchmark index", "tracked index", "nifty 500 tri"),
    "statement_guide": (
        "capital-gains statement",
        "capital gains statement",
        "tax statement",
        "download statement",
        "statement download",
        "how do i download",
        "cagr statement",
    ),
}

#: Factual fund attributes that are *not* one of the seven FR-8 fact types, so
#: they route to FACT with ``expected_fact_type=None`` and K12 checks the score
#: floor only. The corpus carries them as ``general`` chunks, and adding a fact
#: type for them would mean re-chunking and re-embedding the whole collection to
#: relabel text that is already indexed and already retrievable.
#:
#: "aum" and "fund size" were previously PERFORMANCE triggers, which sent
#: "What is the fund size of HDFC Large Cap Fund Direct Growth?" to the C-3
#: factsheet redirect. A fund's size on a date is a stated attribute of the
#: scheme, in the same class as its benchmark or its minimum SIP, and the corpus
#: answers it exactly; only returns, growth and their relatives belong in the
#: performance bucket. They are per-scheme facts, so with no scheme named they
#: take the Q5 clarify path below rather than retrieval across all five.
FACT_ATTRIBUTES: Tuple[str, ...] = (
    "fund size",
    "aum",
    "assets under management",
    "fund manager",
)

#: ADVICE triggers, architecture.md §8.3. Phrased as phrases rather than single
#: words so a bare "best" in "which benchmark is best" is not a refusal; the
#: table is matched on word boundaries and multi-word phrases are preferred.
ADVICE_KEYWORDS: Tuple[str, ...] = (
    "should i buy",
    "should i sell",
    "should i invest",
    "should i",
    "which is better",
    "which one is better",
    "which is best",
    "which one should",
    "is it right for me",
    "is it suitable for me",
    "suitable for me",
    "good for me",
    "right for me",
    "do you recommend",
    "recommend",
    "suggest",
    "suggestion",
    "advise",
    "advice",
    "worth buying",
    "worth investing",
    "good buy",
    "safe to invest",
    "can i invest",
    "best for me",
    "best for you",
    "which fund is best",
    "which is the best",
    "which one is best",
)

#: PERFORMANCE triggers, architecture.md §8.3. "returns" is listed before the
#: specific phrases; a performance question is redirected either way, so the
#: order inside this tuple is presentation, not behaviour.
#:
#: This is deliberately *not* every number a fund page publishes. Size (AUM) and
#: fund size were here and are now :data:`FACT_ATTRIBUTES`, because a size on a
#: date is a stated attribute rather than a return, and the redirect told a
#: questioner that the answer was in a PDF when the indexed page already had it.
PERFORMANCE_KEYWORDS: Tuple[str, ...] = (
    "return",
    "returns",
    "performance",
    "perform",
    "performed",
    "cagr",
    "xirr",
    "which performed better",
    "best performing",
    "top performing",
    "how much has it grown",
    "yield",
    "yields",
    "nav",
    "nav history",
    "portfolio",
    "holdings",
    "top ten holdings",
    "asset allocation",
    "sector allocation",
)

#: A question counts as a fact question if it asks something. Used only to tell
#: AMBIGUOUS (a fact question with no scheme) from OUT_OF_SCOPE (a question the
#: corpus will not support) — both continue to the gate, so this distinction
#: affects the log line and not the user's answer.
FACT_QUESTION_MARKERS: Tuple[str, ...] = (
    "what is",
    "what's",
    "what are",
    "how much",
    "how many",
    "how do i",
    "how does",
    "how long",
    "tell me",
    "explain",
    "which benchmark",
)

#: Sections of the question that, when present, mean a *specific scheme* was
#: named even if the retriever's name matching does not find it. Kept small on
#: purpose: it exists to catch "HDFC Small Cap" and "the ELSS one", not to be a
#: fuzzy NER pass, which is the retriever's job
#: (``Retriever.mentioned_source_ids``).
#:
#: A bare ``hdfc`` is deliberately **not** a hint. Every question in this domain
#: mentions the AMC, so treating the AMC as a scheme name would make
#: "What is the minimum SIP amount for HDFC?" look answered when it names no
#: fund at all — and that question would then retrieve across all five schemes
#: and mix their numbers. AMBIGUOUS is the safe read, which is exactly the
#: fallback Q5 chose.
SCHEME_HINT_PATTERNS: Tuple[str, ...] = (
    r"\belss\b",
    r"\bflexi\s*cap\b",
    r"\bhdfc\s+equity\b",
    r"\bhdfc\s+large\s+cap\b",
    r"\bhdfc\s+small\s+cap\b",
    r"\bhdfc\s+balanced\s+advantage\b",
    r"\blarge\s+cap\s+fund\b",
    r"\bsmall\s+cap\s+fund\b",
    r"\bbalanced\s+advantage\b",
    r"\bthe\s+elss\s+one\b",
)

_SCHEME_HINT_RE = re.compile("|".join(SCHEME_HINT_PATTERNS), re.IGNORECASE)


#: Fact types that cannot differ between schemes, so a question about one of
#: them is not ambiguous when it names no scheme.
#:
#: Q5 exists to stop the system answering a per-fund fact ("exit load?") with
#: another fund's number, and the clarify prompt is the safe fallback the
#: decision record chose. That risk does not exist for a how-to: there is one
#: HDFC ELSS download procedure, not five, so "How do I download my
#: capital-gains statement?" would get "which of the five?" — an answer to a
#: question the user did not ask. These route to FACT instead and are then
#: declined by K12, since the corpus carries no such chunk (the recorded Q1 gap),
#: which is the correct outcome for an unanswerable how-to.
SCHEME_INDEPENDENT_FACT_TYPES: Tuple[str, ...] = ("statement_guide",)


class IntentRouterError(ValueError):
    """Raised when the router is asked to route an empty question."""


@dataclass(frozen=True)
class Intent:
    """One routing decision and everything downstream needs from it."""

    category: str
    #: The FR-8 fact type K12 should require, or ``None`` for OUT_OF_SCOPE.
    #: This is the value that makes fact-typed retrieval work; the pipeline
    #: passes it to ``retrieve_for_fact_type``.
    expected_fact_type: Optional[str] = None
    #: Which keyword or pattern decided the category, for the log line and for
    #: debugging a misroute.
    matched: str = ""
    #: True when the question names one of the five registered schemes. Drives
    #: the AMBIGUOUS decision (Q5).
    names_scheme: bool = False
    #: The K1 source ids the question appears to name, when any.
    source_ids: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_terminal(self) -> bool:
        """True when this category answers without retrieving."""
        return self.category in TERMINAL_CATEGORIES


@dataclass(frozen=True)
class IntentResult:
    """The routing decision plus the PII hit, when K9 fired.

    Kept as a wrapper rather than returning ``Optional[Intent]`` so the caller
    reads the PII class for the refusal without re-screening, and so the screen
    result and the routing result are one atomic decision.
    """

    intent: Intent
    pii_hit: Optional[object] = None

    @property
    def category(self) -> str:
        return self.intent.category

    @property
    def expected_fact_type(self) -> Optional[str]:
        return self.intent.expected_fact_type


def _matches(haystack: str, keywords: Sequence[str]) -> Optional[str]:
    """First keyword present in ``haystack`` as a whole word/phrase."""
    for keyword in keywords:
        pattern = r"(?<!\w)" + re.escape(keyword) + r"(?!\w)"
        if re.search(pattern, haystack, re.IGNORECASE):
            return keyword
    return None


def detect_fact_type(question: str) -> Optional[str]:
    """The FR-8 fact type this question asks about, or ``None``.

    Longest keyword wins rather than first-match, so "minimum sip" is not
    classified as a bare "sip" and "exit load" is not swallowed by "load". The
    table is iterated in insertion order and every match is collected, then the
    longest is returned — the ties that matter are both present in one question
    ("what is the minimum SIP and the lock-in?" is genuinely two facts, and
    picking either is defensible, so the order of :data:`FACT_TYPE_KEYWORDS`
    breaks the tie deterministically).
    """
    best: Optional[str] = None
    best_len = 0
    for fact_type, keywords in FACT_TYPE_KEYWORDS.items():
        match = _matches(question, keywords)
        if match is None:
            continue
        length = len(match)
        if length > best_len:
            best, best_len = fact_type, length
    return best


def names_scheme(question: str) -> bool:
    """Whether the question names a scheme, using the hint patterns."""
    return bool(_SCHEME_HINT_RE.search(question or ""))


def route(
    question: str,
    registered_schemes: Optional[Dict[str, str]] = None,
    known_source_ids: Sequence[str] = (),
) -> IntentResult:
    """Route ``question`` to one of :data:`CATEGORIES`.

    ``registered_schemes`` is ``{source_id: scheme}`` from K1; it is accepted
    so the caller can pass the retriever's own view and get precise
    ``source_ids`` back, but routing does not depend on it — the hint patterns
    decide AMBIGUOUS, so the router works in a unit test with no corpus loaded.
    """
    question = (question or "").strip()
    if not question:
        raise IntentRouterError("question must not be empty")

    haystack = " ".join(question.lower().split())
    source_ids = _match_source_ids(
        haystack,
        registered_schemes or {},
        known_source_ids,
    )

    # 0. PII. K9 already ran at step 0; this is the second gate described in
    # the module docstring, and it runs before every keyword category so a PII
    # question can never be classified as something that reaches the LLM.
    hit = screen(question)
    if hit is not None:
        return IntentResult(
            Intent(category="PII", matched=hit.redacted), pii_hit=hit
        )

    # 1. ADVICE, before PERFORMANCE — see the module docstring.
    advice = _matches(haystack, ADVICE_KEYWORDS)
    if advice is not None:
        return IntentResult(
            Intent(
                category="ADVICE",
                matched=advice,
                names_scheme=names_scheme(question),
                source_ids=source_ids,
            )
        )

    # 2. PERFORMANCE. The scheme is carried through so the C-3 redirect can
    # offer that scheme's own factsheet rather than the generic link.
    performance = _matches(haystack, PERFORMANCE_KEYWORDS)
    if performance is not None:
        return IntentResult(
            Intent(
                category="PERFORMANCE",
                matched=performance,
                names_scheme=names_scheme(question),
                source_ids=source_ids,
            )
        )

    fact_type = detect_fact_type(question)
    attribute = _matches(haystack, FACT_ATTRIBUTES)
    scheme_named = names_scheme(question)

    # 3. AMBIGUOUS: a fact question that names no scheme (Q5). Answering all
    # five from one question is the failure mode Q5 exists to prevent, so this
    # asks rather than guesses. Scheme-independent how-tos are exempt: there is
    # no per-fund version of them to confuse. A :data:`FACT_ATTRIBUTES` match
    # asks too, because a size or a manager differs per scheme just as a minimum
    # SIP does — with no ``fact_type`` there is nothing else that would catch it.
    if not scheme_named and (
        attribute is not None
        or (fact_type and fact_type not in SCHEME_INDEPENDENT_FACT_TYPES)
    ):
        return IntentResult(
            Intent(
                category="AMBIGUOUS",
                expected_fact_type=fact_type,
                matched=attribute or "no scheme named",
                names_scheme=False,
                source_ids=source_ids,
            )
        )

    # 4. FACT.
    if fact_type:
        return IntentResult(
            Intent(
                category="FACT",
                expected_fact_type=fact_type,
                matched=fact_type,
                names_scheme=scheme_named,
                source_ids=source_ids,
            )
        )
    if attribute is not None:
        # No fact type to filter on, so retrieval runs unfiltered and K12
        # applies the floor alone. ``expected_fact_type=None`` is exactly the
        # OUT_OF_SCOPE contract (see src.rag.relevance_gate.evaluate), so this
        # reaches the same chunks a plain factual question always has.
        return IntentResult(
            Intent(
                category="FACT",
                expected_fact_type=None,
                matched=attribute,
                names_scheme=scheme_named,
                source_ids=source_ids,
            )
        )

    # 5. OUT_OF_SCOPE: retrieval is attempted and K12 decides.
    return IntentResult(
        Intent(
            category="OUT_OF_SCOPE",
            expected_fact_type=None,
            matched="",
            names_scheme=scheme_named,
            source_ids=source_ids,
        )
    )


def _match_source_ids(
    haystack: str, registered: Dict[str, str], known_source_ids: Sequence[str]
) -> Tuple[str, ...]:
    """Source ids whose registered scheme name appears in the question.

    Mirrors :meth:`Retriever.mentioned_source_ids` only in purpose: this feeds
    the log line and lets the pipeline log which scheme a question matched
    (FR-26). The retriever remains the authority for the Chroma filter.
    """
    found = []
    candidates = dict(registered)
    for source_id in known_source_ids:
        candidates.setdefault(source_id, source_id)
    for source_id, scheme in candidates.items():
        name = str(scheme).lower()
        base = name.split("(")[0].strip()
        variants = {base}
        for suffix in (
            "direct plan growth",
            "direct - growth",
            "direct growth",
            "direct plan - growth",
            "growth",
            "direct",
        ):
            if base.endswith(suffix):
                variants.add(base[: -len(suffix)].strip(" -"))
        # Drop the trailing "fund" as well, so "HDFC Small Cap" matches the
        # registered "HDFC Small Cap Fund - Direct Growth". Users drop the word
        # far more often than they drop the plan variant, and a name that fails
        # to match here means the C-3 redirect falls back to the generic
        # educational link instead of this scheme's own factsheet.
        for variant in list(variants):
            if variant.endswith(" fund"):
                variants.add(variant[: -len(" fund")])
        if any(len(v) >= 12 and v in haystack for v in variants):
            found.append(source_id)
    return tuple(sorted(found))
