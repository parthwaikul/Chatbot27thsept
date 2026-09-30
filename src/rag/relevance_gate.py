"""K12 — the relevance gate (architecture.md §8.4).

Two conditions, **both** required, before any LLM call:

1. **Score floor** — best cosine similarity ≥ ``SIMILARITY_FLOOR`` (Q3, reopened in
   Phase 5 and lowered to 0.30; see ``CHUNKING.md`` §6).
2. **Fact-type agreement** — at least one retrieved chunk's ``fact_type``
   matches the intent's expected type.

The second condition is doing the real work, and the measured reason is in
``CHUNKING.md`` §"Findings from Phase 4 live verification". The floor alone does
not reject unsupported in-domain questions: questions about real fund attributes
the corpus does not carry score 0.587–0.801, comfortably above the floor.
Conversely a genuine fact can score low: ``benchmark`` is the weakest real
match at 0.344, which is why the floor sits at 0.30 rather than above it — at the
Phase 3 value of 0.42 the floor discarded a real FR-8 fact while admitting every
unsupported question, i.e. negative recall *and* negative precision. Requiring
*both* conditions is what makes the gate a correctness control rather than a
similarity heuristic.

Fact-type agreement is also how the corpus's **riskometer conflict** is resolved
without touching the Phase 2 corpus. The five scheme pages disagree with
themselves: each fund has chunks reading "Very High Risk" (inside the scheme
header blob, labelled ``fact_type: expense_ratio``) and one dedicated chunk
reading "Moderately High Riskometer". Plain retrieval surfaces the header, so the
model answers *Very High*; asking K12 for a ``riskometer`` chunk rejects the
header as a fact-type mismatch and yields the dedicated chunk, so the answer is
*Moderately High* — the reading the corpus actually files under that fact type.
Neither answer invents anything, so no citation or length check could ever catch
this; only the fact-type condition makes the choice deterministic and
explainable. The conflict is recorded rather than papered over: see Finding 8 in
``CHUNKING.md``.

The gate returns a reason rather than a bare boolean so the decline message, the
log line and the eval report can all say *which* condition failed. "I don't know"
and "you asked about the wrong kind of fact" are different user experiences and
only the first is true here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from src.rag.retriever import RetrievedChunk


class RelevanceGateError(ValueError):
    """Raised when the gate is asked to evaluate an impossible input."""


#: Why the gate said no. Reported, not just logged: E-6 debugging needs to tell
#: "nothing in the corpus resembles this" apart from "this resembles something,
#: but not the kind of thing you asked about".
BELOW_FLOOR = "below_floor"
WRONG_FACT_TYPE = "wrong_fact_type"
NO_CHUNKS = "no_chunks"


@dataclass(frozen=True)
class GateResult:
    """Whether the retrieved context may go to the LLM, and why."""

    passed: bool
    reason: str = ""
    best_similarity: float = 0.0
    floor: float = 0.0
    expected_fact_type: Optional[str] = None
    #: The fact types actually present in the retrieved context, for the log
    #: line. A miss shows what was retrieved instead, which is usually the
    #: ``general`` blobs and explains the miss at a glance.
    retrieved_fact_types: Sequence[str] = ()

    @property
    def failed_on_floor(self) -> bool:
        return self.reason == BELOW_FLOOR

    @property
    def failed_on_fact_type(self) -> bool:
        return self.reason == WRONG_FACT_TYPE


def evaluate(
    chunks: Sequence[RetrievedChunk],
    expected_fact_type: Optional[str],
    floor: float,
) -> GateResult:
    """Apply both conditions to ``chunks``.

    ``expected_fact_type`` of ``None`` — the OUT_OF_SCOPE category — means the
    intent expressed no fact expectation, so condition 2 is vacuous and only the
    score floor applies. That is the documented OUT_OF_SCOPE behaviour
    ("retrieval attempted, then declined by K12 if unsupported"), and it is why
    ``min_similarity`` is checked first: a weak retrieval should be reported as
    a weak retrieval, not as a fact-type mismatch.

    The floor comparison is ``>=`` so a chunk exactly at the floor passes; the
    retriever's ``above_floor`` helper uses ``>`` for "evidence I would rank",
    which is a different question from "is this admissible for the LLM".
    """
    materialised = list(chunks or ())
    if not materialised:
        return GateResult(
            passed=False,
            reason=NO_CHUNKS,
            floor=floor,
            expected_fact_type=expected_fact_type,
        )

    best = max(chunk.similarity for chunk in materialised)
    present = tuple(dict.fromkeys(chunk.fact_type for chunk in materialised))

    if best < floor:
        return GateResult(
            passed=False,
            reason=BELOW_FLOOR,
            best_similarity=best,
            floor=floor,
            expected_fact_type=expected_fact_type,
            retrieved_fact_types=present,
        )

    if expected_fact_type:
        # Compare ``chunk.fact_type`` to ``expected_fact_type`` directly, and
        # deliberately ignore ``chunk.fact_type_match``. That flag is relative to
        # whatever type the *retriever* was asked for, so a caller that fetched
        # for "expense_ratio" and then asks the gate about "riskometer" would see
        # ``fact_type_match=True`` on every chunk and the condition would never
        # fire — which is precisely the case that has to fail (the riskometer
        # conflict). The gate's own expectation is the only authority here.
        matched = any(chunk.fact_type == expected_fact_type for chunk in materialised)
        if not matched:
            return GateResult(
                passed=False,
                reason=WRONG_FACT_TYPE,
                best_similarity=best,
                floor=floor,
                expected_fact_type=expected_fact_type,
                retrieved_fact_types=present,
            )

    return GateResult(
        passed=True,
        best_similarity=best,
        floor=floor,
        expected_fact_type=expected_fact_type,
        retrieved_fact_types=present,
    )
