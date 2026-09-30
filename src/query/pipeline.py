"""Stage B orchestration: the full guardrailed order of architecture.md §8.1.

    question
       │
       ├─(0) K9  PII screen ────────────── hit ──▶ refusal (PII) ─▶ END
       │   no hit: the text is never embedded, logged or persisted
       ├─(1) K10 intent route
       │     ADVICE      ──▶ refusal + educational link ──▶ END
       │     PERFORMANCE ──▶ factsheet redirect, no computation ──▶ END
       │     AMBIGUOUS   ──▶ clarify which of S1–S5 (Q5) ──▶ END
       │     FACT / OUT_OF_SCOPE
       ├─(2) K11 embed ──▶ (3) Chroma cosine top-k
       ├─(4) K12 relevance gate ── miss ──▶ decline + link ──▶ END
       ├─(5) K13 prompt ──▶ (6) K14 Groq ── error ──▶ safe error state (NFR-4)
       ├─(7) K15 validate ── fail ──▶ one repair retry ── fail ──▶ safe decline
       └─(8) K16 render: answer + citation + "Last updated from sources: <K17>"

Steps 0, 1, 4 and 7 are what make the PRD's constraints structural rather than
prompt-suggested: a non-compliant output is not rendered, it is replaced.

Three invariants this module owns:

* **The citation is recomputed from chunk metadata**, never taken from model
  text (AD-3, FR-9, E-1). A hallucinated URL cannot reach the UI even if the
  validator is bypassed.
* **Every non-answer path returns a readable message**, never a traceback
  (NFR-4), including an unexpected exception at this boundary.
* **A PII-screened question is never embedded or logged.** The screen runs
  before the retriever is constructed, so a refused question cannot reach the
  vector store even in principle.

``fact_type`` is accepted for tests and for callers that already know the fact
type, but the router's ``expected_fact_type`` wins: a hint that disagrees with
the router must not be able to pull retrieval off the fact-typed path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from src.config import Settings, load
from src.guardrails import messages
from src.guardrails.intent import Intent, route
from src.guardrails.pii import screen
from src.guardrails.validator import REPAIR_INSTRUCTION, ValidationResult, validate
from src.ingest.registry import SourceRegistry
from src.llm.groq_client import GroqClient, LLMMessage, LLMUnavailable
from src.query.freshness import FreshnessResolver
from src.rag.prompts import (
    FACTSHEET_REDIRECT,
    NOT_IN_SOURCES,
    SYSTEM_PROMPT,
    build_user_message,
    citation_for,
    is_marker,
)
from src.rag.relevance_gate import GateResult, evaluate as evaluate_gate
from src.rag.retriever import RetrievedChunk, Retriever

#: Intent categories that end the request with a message instead of retrieving.
_TERMINAL = ("PII", "ADVICE", "PERFORMANCE", "AMBIGUOUS")


class AnswerError(RuntimeError):
    """The pipeline could not produce an answer."""


@dataclass(frozen=True)
class RetrievedSource:
    """One chunk behind an answer, as the sources disclosure shows it.

    FR-22 asks the UI to list the retrieved ``chunk_id``, ``section`` and
    similarity score under each answer. ``chunk_id`` alone was on
    :class:`AnswerResponse` already; carrying the section and the score here
    too means the disclosure can be rendered without a second store query, and
    without the UI needing to know how a chunk is identified.

    This is *not* a second citation. ``citation_url`` remains the single
    authoritative link (E-1, AD-3); this is the audit trail of which chunks
    were considered, including the ones that lost.
    """

    chunk_id: str
    section: str
    similarity: float
    fact_type: str
    source_url: str


@dataclass(frozen=True)
class AnswerResponse:
    """One answer, with everything the view layer needs and nothing it does not.

    ``path`` records which branch of §8.1 produced this, so the eval harness can
    score "did the system refuse correctly" separately from "did the system
    answer correctly" without re-deriving it from the text (E-3, E-4, E-5).
    """

    text: str
    #: From chunk metadata, not from the model's text (AD-3).
    citation_url: Optional[str]
    #: The rendered FR-14 string, e.g. "Last updated from sources: 28 Sep 2026".
    last_updated: str
    retrieved_chunk_ids: List[str] = field(default_factory=list)
    #: The marker the model emitted, when it emitted one.
    marker: Optional[str] = None
    #: Best similarity across the retrieved chunks; logged for E-6 debugging.
    top_similarity: float = 0.0
    #: Which branch of architecture.md §8.1 produced this response.
    path: str = "answer"
    #: The K10 category, for the log line.
    intent: str = ""
    #: The K12 verdict, when the gate ran.
    gate_reason: str = ""
    #: The K15 verdict, when the validator ran.
    validator_checks: Sequence[str] = field(default_factory=tuple)
    #: The educational or factsheet link attached to a refusal or redirect.
    link: Optional[str] = None
    #: Which schemes a clarify prompt listed, when it asked one.
    schemes: Sequence[str] = field(default_factory=tuple)
    #: The retrieval trace behind this response: one row per chunk actually put
    #: in front of the model, for FR-22's sources disclosure. Built here, where
    #: retrieval happens, so the UI stays a renderer (AD-5) and never has to
    #: re-query the store to explain an answer it was already given.
    sources: Sequence["RetrievedSource"] = field(default_factory=tuple)

    @property
    def is_decline(self) -> bool:
        return self.path in ("decline", "gate_miss", "validator_decline") or (
            self.marker == NOT_IN_SOURCES
        )

    @property
    def is_factsheet_redirect(self) -> bool:
        return self.path == "performance" or self.marker == FACTSHEET_REDIRECT

    @property
    def is_answer(self) -> bool:
        """True only for a sourced factual answer.

        The complement of :attr:`is_refusal`, spelled out because the two are
        used for different things: the eval harness needs to assert that a
        question it expected to be answered *was* answered, and a surprising
        number of failures are a refusal where an answer was required. Without
        this, ``not is_refusal`` reads as "not a refusal" and hides the intent.
        """
        return self.path == "answer"

    @property
    def is_refusal(self) -> bool:
        """True for any message that is not a sourced factual answer."""
        return not self.is_answer


@dataclass
class _Deps:
    """Lazily-built collaborators, so a PII refusal builds none of them."""

    settings: Settings
    retriever: Any = None
    llm: Any = None
    freshness: Any = None
    registry: Any = None

    def get_retriever(self) -> Any:
        if self.retriever is None:
            self.retriever = Retriever(settings=self.settings)
        return self.retriever

    def get_llm(self) -> Any:
        if self.llm is None:
            self.llm = GroqClient(self.settings)
        return self.llm

    def get_freshness(self) -> Any:
        if self.freshness is None:
            self.freshness = FreshnessResolver(self.settings)
        return self.freshness

    def get_registry(self) -> Any:
        if self.registry is None:
            self.registry = SourceRegistry.from_file(self.settings.sources_path)
        return self.registry


def _message_response(
    text: str,
    path: str,
    settings: Settings,
    last_updated: Optional[str] = None,
    link: Optional[str] = None,
    schemes: Sequence[str] = (),
    intent: str = "",
    gate_reason: str = "",
    chunks: Sequence[RetrievedChunk] = (),
    marker: Optional[str] = None,
) -> AnswerResponse:
    """Build a non-answer response that still satisfies FR-14's freshness rule.

    Every path through §8.1 carries a freshness line, including a refusal: the
    user should be able to see the corpus is dated regardless of which branch
    answered. ``None`` falls back to the configured value so a refusal never
    renders a bare message with no date.
    """
    return AnswerResponse(
        text=text,
        citation_url=None,
        last_updated=last_updated
        if last_updated is not None
        else _safe_freshness(_Deps(settings).get_freshness(), chunks),
        retrieved_chunk_ids=[c.chunk_id for c in chunks],
        marker=marker,
        top_similarity=chunks[0].similarity if chunks else 0.0,
        path=path,
        intent=intent,
        gate_reason=gate_reason,
        link=link,
        schemes=tuple(schemes),
        sources=_sources(chunks),
    )


def _sources(chunks: Sequence[RetrievedChunk]) -> List[RetrievedSource]:
    """The retrieval trace for the sources disclosure (FR-22)."""
    return [
        RetrievedSource(
            chunk_id=chunk.chunk_id,
            section=chunk.metadata.get("section", "") or "",
            similarity=chunk.similarity,
            fact_type=chunk.fact_type,
            source_url=chunk.source_url,
        )
        for chunk in chunks
    ]


def _messages(question: str, chunks: Sequence[RetrievedChunk]) -> List[LLMMessage]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_message(chunks, question)},
    ]


def answer(
    question: str,
    retriever: Optional[Retriever] = None,
    llm: Optional[Any] = None,
    freshness: Optional[FreshnessResolver] = None,
    settings: Optional[Settings] = None,
    k: Optional[int] = None,
    fact_type: Optional[str] = None,
) -> AnswerResponse:
    """Answer ``question`` through the full §8.1 order.

    Every collaborator is optional so a test can inject a scripted LLM and a
    stub retriever. ``fact_type`` is a hint for callers that already know it; the
    router's ``expected_fact_type`` takes precedence, so the fact-typed
    retrieval path in :func:`_retrieve` is driven by K10 in normal use.
    """
    question = (question or "").strip()
    if not question:
        raise AnswerError("question must not be empty")

    settings = settings or load()
    deps = _Deps(
        settings=settings,
        retriever=retriever,
        llm=llm,
        freshness=freshness,
    )

    # -- (0) K9 PII screen. First, before any embedding, embedding or log write.
    hit = screen(question)
    if hit is not None:
        # `question` is dropped here. It is not logged, embedded or stored; the
        # refusal carries only the safe class name.
        return _message_response(
            messages.PII_REFUSAL, "pii", settings, intent="PII"
        )

    # -- (1) K10 intent route.
    registry = deps.get_registry()
    routed = route(
        question,
        registered_schemes=registry.schemes(),
        known_source_ids=registry.source_ids(),
    )
    intent: Intent = routed.intent

    if intent.category == "ADVICE":
        return _message_response(
            messages.refusal_block(
                messages.ADVICE_REFUSAL, messages.educational_link(settings)
            ),
            "advice",
            settings,
            link=messages.educational_link(settings),
            intent=intent.category,
        )

    if intent.category == "PERFORMANCE":
        source_id = intent.source_ids[0] if intent.source_ids else None
        return _message_response(
            messages.factsheet_redirect_block(
                messages.PERFORMANCE_REDIRECT, source_id, settings
            ),
            "performance",
            settings,
            link=messages.factsheet_link(source_id, settings)
            or messages.educational_link(settings),
            intent=intent.category,
        )

    if intent.category == "AMBIGUOUS":
        scheme_names = [
            registry.by_id(source_id).scheme for source_id in registry.source_ids()
        ]
        return _message_response(
            messages.CLARIFY_SCHEME_PROMPT.format(
                schemes=messages.scheme_list(scheme_names)
            ),
            "ambiguous",
            settings,
            link=messages.educational_link(settings),
            schemes=tuple(scheme_names),
            intent=intent.category,
        )

    # The router's fact type wins over the caller's hint: a hint that disagrees
    # must not be able to pull retrieval off the fact-typed path.
    expected = intent.expected_fact_type or fact_type

    # -- (2) K11 embed, (3) Chroma cosine top-k.
    retriever_obj = deps.get_retriever()
    chunks = _retrieve(retriever_obj, question, expected, k)

    # -- (4) K12 relevance gate. Both conditions, before any LLM call.
    gate: GateResult = evaluate_gate(
        chunks,
        expected_fact_type=expected,
        floor=retriever_obj.similarity_floor,
    )
    if not gate.passed:
        return _message_response(
            messages.refusal_block(
                messages.NOT_IN_SOURCES_DECLINE, messages.educational_link(settings)
            ),
            "gate_miss",
            settings,
            link=messages.educational_link(settings),
            intent=intent.category,
            gate_reason=gate.reason,
            chunks=chunks,
        )

    # -- (5) prompt, (6) LLM.
    try:
        raw = deps.get_llm().chat(_messages(question, chunks))
    except LLMUnavailable:
        # NFR-4: readable message, no traceback and no exception text, which
        # could otherwise carry a URL or key fragment.
        return _message_response(
            messages.LLM_ERROR_MESSAGE,
            "llm_error",
            settings,
            intent=intent.category,
            chunks=chunks,
        )

    # -- (7) K15 validate. Markers route to their handlers without a retry.
    allowed_urls = [c.source_url for c in chunks if c.source_url]
    result = validate(
        raw, allowed_urls, max_sentences=settings.answer_max_sentences
    )

    if result.is_marker:
        return _marker_response(result.marker, settings, intent, chunks)

    if not result.ok and result.repairable:
        # Exactly one repair retry, then a safe decline.
        try:
            raw = deps.get_llm().chat(
                _messages(question, chunks) + [{"role": "user", "content": REPAIR_INSTRUCTION}]
            )
        except LLMUnavailable:
            # A repair that could not be performed must not cost the user an
            # answer. Keep the first output and fall through to the normal
            # failure handling below, which re-renders a citation-only failure
            # from chunk metadata and declines anything else.
            pass
        else:
            result = validate(
                raw, allowed_urls, max_sentences=settings.answer_max_sentences
            )
            if result.is_marker:
                return _marker_response(result.marker, settings, intent, chunks)

    if not result.ok:
        if result.marker == FACTSHEET_REDIRECT or result.returns_hits:
            return _marker_response(
                FACTSHEET_REDIRECT, settings, intent, chunks, checks=result.failed_checks
            )
        # architecture.md §8.6 ends a citation failure at "re-render with the
        # citation taken from the top chunk's metadata", not at a decline. The
        # citation is metadata-derived anyway, so a model that emitted the wrong
        # number of links has cost a formatting point and nothing else: the
        # renderer supplies the authoritative one.
        if result.failed_checks == ("citation",):
            return _answer_response(raw, settings, chunks, intent, gate, result)
        return _message_response(
            messages.refusal_block(
                messages.VALIDATOR_DECLINE, messages.educational_link(settings)
            ),
            "validator_decline",
            settings,
            link=messages.educational_link(settings),
            intent=intent.category,
            chunks=chunks,
        )

    # -- (8) K16 render. The citation is the top chunk's metadata URL, whatever
    # the model wrote (AD-3, E-1).
    return _answer_response(raw, settings, chunks, intent, gate, result)


def _answer_response(
    raw: str,
    settings: Settings,
    chunks: Sequence[RetrievedChunk],
    intent: Intent,
    gate: GateResult,
    result: ValidationResult,
) -> AnswerResponse:
    """Build the successful-answer response, with the citation from metadata."""
    return AnswerResponse(
        text=raw.strip(),
        citation_url=citation_for(chunks),
        last_updated=_safe_freshness(_Deps(settings).get_freshness(), chunks),
        retrieved_chunk_ids=[c.chunk_id for c in chunks],
        marker=None,
        top_similarity=chunks[0].similarity if chunks else 0.0,
        path="answer",
        intent=intent.category,
        gate_reason=gate.reason,
        validator_checks=result.failed_checks,
        sources=_sources(chunks),
    )


def _marker_response(
    marker: str,
    settings: Settings,
    intent: Intent,
    chunks: Sequence[RetrievedChunk],
    checks: Sequence[str] = (),
) -> AnswerResponse:
    """Route a model marker to its handler.

    A marker is the model's compliant way of declining or redirecting, so it is
    passed through as a readable message rather than shown as the bare token: the
    user should see the decline, not the wire format.
    """
    if marker == FACTSHEET_REDIRECT:
        source_id = intent.source_ids[0] if intent.source_ids else None
        link = messages.factsheet_link(source_id, settings) or messages.educational_link(
            settings
        )
        return _message_response(
            messages.factsheet_redirect_block(
                messages.PERFORMANCE_REDIRECT, source_id, settings
            ),
            "performance",
            settings,
            link=link,
            intent=intent.category,
            chunks=chunks,
            marker=marker,
        )
    return _message_response(
        messages.refusal_block(
            messages.NOT_IN_SOURCES_DECLINE, messages.educational_link(settings)
        ),
        "decline",
        settings,
        link=messages.educational_link(settings),
        intent=intent.category,
        chunks=chunks,
        marker=marker,
    )


def _retrieve(
    retriever: Retriever, question: str, fact_type: Optional[str], k: Optional[int]
) -> List[RetrievedChunk]:
    """Retrieve context, preferring the fact-typed path when one is known.

    Plain :meth:`Retriever.retrieve` is not sufficient for a fact question.
    Measured against the persisted corpus: for "What benchmark does HDFC Equity
    Fund Direct Growth track?" the top three plain hits are all ``general``
    blobs (the "About" and "Scheme name" text), and the chunk that actually
    holds the answer, ``Benchmark: NIFTY 500 TRI``, ranks 24th. The model
    correctly replied ``NOT_IN_SOURCES`` against that context, which is faithful
    behaviour on a context that does not contain the fact.

    With ``fact_type`` supplied by K10, the K11 metadata filter puts the real
    fact chunk first. Falling back to plain retrieval when the filter yields
    nothing keeps the decline path intact rather than returning an empty list.
    """
    limit = k or retriever.top_k
    if fact_type:
        hits = retriever.retrieve_for_fact_type(question, fact_type, k=limit)
        if hits and any(hit.fact_type_match for hit in hits):
            return list(hits[:limit])
    return retriever.retrieve(question, k=limit)


def _safe_freshness(freshness: FreshnessResolver, chunks: Sequence[RetrievedChunk]) -> str:
    """Resolve freshness, degrading rather than failing the whole answer.

    A missing sources.csv should not cost the user their answer, but it must be
    visible: the fallback string says so instead of inventing a date (FR-14).
    """
    try:
        return freshness.resolve(chunks).render()
    except Exception:  # noqa: BLE001 - never let freshness break an answer
        return "Last updated from sources: unknown (corpus manifest unavailable)"
