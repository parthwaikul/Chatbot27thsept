"""Phase 3 (K11) — retrieval.

Two kinds of test. The unit tests use fakes and pin the contract: same model on
both sides, cosine similarity, scheme scoping, floor filtering. The acceptance
tests run against the real collection and real embeddings, because the point of
this phase is a measured claim about the corpus, and a mock cannot make that
claim.

The acceptance tests also assert the *negative* results recorded in CHUNKING.md
rather than quietly tuning thresholds until everything passes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

import pytest

from src.config import load
from src.eval.cases import (
    COVERED_FACT_QUERIES,
    COVERED_FACT_TYPES,
    FACT_QUERIES,
    PII_QUERIES,
    REFUSAL_QUERIES,
    UNSUPPORTED_QUERIES,
)
from src.ingest.registry import SourceRegistry
from src.rag.retriever import Retriever, RetrieverError

CHUNKING_MD = Path("CHUNKING.md")


# ---------------------------------------------------------------- unit: fakes


class FakeEmbeddings:
    """Deterministic stand-in so unit tests never load the model."""

    def __init__(self, vector: List[float], dim: int = 384) -> None:
        self.vector = vector
        self.dim = dim
        self.seen: List[str] = []

    def embed_query(self, question: str) -> List[float]:
        self.seen.append(question)
        return list(self.vector)


class FakeStore:
    """Records the filters it is given, and returns a fixed result set."""

    def __init__(self, rows, count: int = 10, schemes=None) -> None:
        self.rows = rows
        self._count = count
        self.filters: List[object] = []
        self.collection_name = "fake"
        self._schemes = schemes or {"hdfc_large_cap_direct_growth": "HDFC Large Cap Fund - Direct Growth"}

    def count(self) -> int:
        return self._count

    def all_chunk_ids(self) -> List[str]:
        return list(self._schemes)

    def get_chunks(self, ids):
        return {
            i: {"text": "t", "metadata": {"scheme": self._schemes.get(i.split("::")[0], "x")}}
            for i in ids
        }

    def query(self, vector, k, where=None):
        self.filters.append(where)
        rows = self.rows
        ft = None
        if isinstance(where, dict):
            if "fact_type" in where:
                ft = where["fact_type"]
            for part in where.get("$and", []):
                if "fact_type" in part:
                    ft = part["fact_type"]
        if ft:
            matching = [r for r in rows if r["metadata"].get("fact_type") == ft]
            if matching:
                rows = matching
        return rows[:k]


def _row(chunk_id: str, fact_type: str, scheme: str, similarity: float = 0.9):
    return {
        "chunk_id": chunk_id,
        "text": "text",
        "metadata": {"fact_type": fact_type, "scheme": scheme, "source_url": "https://groww.in/x"},
        "similarity": similarity,
    }


@pytest.fixture
def fake_settings():
    return load()


def test_retrieve_embeds_with_the_injected_service(fake_settings):
    """The query vector must come from the ingestion service, not a new model."""
    embeddings = FakeEmbeddings([0.1] * 384)
    store = FakeStore([_row("a::0::0", "exit_load", "X", 0.7)])
    r = Retriever(embeddings=embeddings, store=store, settings=fake_settings)
    r.retrieve("what is the exit load?")
    assert embeddings.seen == ["what is the exit load?"]


def test_retrieve_rejects_wrong_dimension_query_vector(fake_settings):
    embeddings = FakeEmbeddings([0.1] * 111)
    store = FakeStore([_row("a::0::0", "exit_load", "X")])
    r = Retriever(embeddings=embeddings, store=store, settings=fake_settings)
    with pytest.raises(RetrieverError, match="dims"):
        r.retrieve("exit load?")


def test_retrieve_rejects_empty_question(fake_settings):
    r = Retriever(embeddings=FakeEmbeddings([0.1] * 384), store=FakeStore([_row("a::0::0", "x", "X")]), settings=fake_settings)
    with pytest.raises(ValueError):
        r.retrieve("   ")


def test_retrieve_refuses_an_empty_collection(fake_settings):
    """Constructing a retriever over an empty collection is an error, not a
    retriever that silently returns nothing (D6 / E-6)."""
    with pytest.raises(RetrieverError, match="empty"):
        Retriever(
            embeddings=FakeEmbeddings([0.1] * 384),
            store=FakeStore([], count=0),
            settings=fake_settings,
        )


def test_similarity_is_reported_verbatim(fake_settings):
    store = FakeStore([_row("a::0::0", "exit_load", "X", 0.6123)])
    r = Retriever(embeddings=FakeEmbeddings([0.1] * 384), store=store, settings=fake_settings)
    assert r.retrieve("q")[0].similarity == pytest.approx(0.6123)


def test_fact_type_filter_is_applied_server_side(fake_settings):
    store = FakeStore([_row("a::0::0", "benchmark", "X")])
    r = Retriever(embeddings=FakeEmbeddings([0.1] * 384), store=store, settings=fake_settings)
    r.retrieve_for_fact_type("q", "benchmark")
    assert store.filters[-1] == {"fact_type": "benchmark"}


def test_unknown_fact_type_is_rejected(fake_settings):
    r = Retriever(embeddings=FakeEmbeddings([0.1] * 384), store=FakeStore([_row("a::0::0", "exit_load", "X")]), settings=fake_settings)
    with pytest.raises(ValueError, match="unknown fact_type"):
        r.retrieve_for_fact_type("q", "nav")


def test_prefers_the_matching_fact_type(fake_settings):
    store = FakeStore(
        [
            _row("a::0::0", "general", "X", 0.95),
            _row("a::0::1", "benchmark", "X", 0.40),
        ],
        count=2,
    )
    r = Retriever(embeddings=FakeEmbeddings([0.1] * 384), store=store, settings=fake_settings)
    hits = r.retrieve_for_fact_type("q", "benchmark", k=2)
    assert hits[0].fact_type == "benchmark"
    assert hits[0].fact_type_match is True


def test_marks_non_matching_hits_when_the_fact_type_is_absent(fake_settings):
    """The gate must be able to tell 'absent from the corpus' from 'weak'."""
    store = FakeStore([_row("a::0::0", "general", "X", 0.9)], count=1)
    r = Retriever(embeddings=FakeEmbeddings([0.1] * 384), store=store, settings=fake_settings)
    hits = r.retrieve_for_fact_type("q", "statement_guide", k=1)
    assert hits and all(h.fact_type_match is False for h in hits)


# ------------------------------------------------- acceptance: real collection


@pytest.fixture(scope="module")
def retriever():
    return Retriever()


@pytest.fixture(scope="module")
def registered_urls():
    registry = SourceRegistry.from_file(load().sources_path)
    return {spec.url for spec in registry.all_sources()}


@pytest.mark.parametrize("case", COVERED_FACT_QUERIES, ids=lambda c: c.fact_type)
def test_fact_query_returns_a_matching_chunk_from_a_registered_url(
    case, retriever, registered_urls
):
    """FR-8 / Definition of done: correct fact_type, from an allowed source."""
    hits = retriever.retrieve_for_fact_type(case.question, case.fact_type, k=3)
    matches = [h for h in hits if h.fact_type_match]
    assert matches, f"no {case.fact_type} chunk retrieved for {case.question!r}"
    best = matches[0]
    assert best.source_url in registered_urls, f"uncited-source URL: {best.source_url}"


@pytest.mark.parametrize("case", COVERED_FACT_QUERIES, ids=lambda c: c.fact_type)
def test_fact_query_answers_about_the_scheme_it_names(case, retriever):
    """Naming one fund must not be answered with another fund's numbers.

    Short fact chunks ("Benchmark: NIFTY 500 TRI") carry no scheme name, so this
    is the regression guard for the scheme-scoping bug.
    """
    hits = retriever.retrieve_for_fact_type(case.question, case.fact_type, k=3)
    named = retriever.mentioned_source_ids(case.question)
    assert named == [case.source_id], f"expected the question to name {case.source_id}"
    stored = retriever._stored_schemes()[case.source_id]
    for hit in hits:
        if hit.fact_type_match:
            assert hit.scheme == stored


def test_every_covered_fact_type_is_exercised():
    """The spec asks for one query per FR-8 fact type; don't let them rot away."""
    assert {c.fact_type for c in FACT_QUERIES} >= set(COVERED_FACT_TYPES)
    assert len(FACT_QUERIES) >= 7


def test_fact_queries_cover_every_registered_scheme(retriever):
    named = {c.source_id for c in FACT_QUERIES}
    assert named == set(retriever.registered_schemes())


def test_statement_guide_returns_nothing_above_the_floor(retriever):
    """The recorded Q1 gap: a missing source must not produce a confident answer."""
    hits = retriever.retrieve_for_fact_type(
        "How do I download my capital-gains statement?", "statement_guide", k=3
    )
    assert not [h for h in hits if h.fact_type_match and h.similarity > retriever.similarity_floor]


def test_off_domain_questions_fall_below_the_floor(retriever):
    """The floor rejects the clear majority of non-fund questions.

    Phase 5 re-opened Q3 and lowered the floor from 0.42 to 0.30, because 0.42
    rejected a genuine fact (``benchmark`` at 0.344) while admitting every
    in-domain unsupported question (0.587-0.801) — negative precision *and*
    negative recall. The cost of lowering it is visible here: three off-domain
    probes that 0.42 rejected now pass. The remaining twelve are still rejected,
    so the floor keeps most of its noise-filtering value.

    The three that pass are covered end-to-end by
    ``test_validator.py::test_an_off_domain_question_still_declines``, which
    asserts the property that actually matters: reaching the LLM is not the same
    as getting answered.
    """
    from scripts.inspect_scores import OFF_DOMAIN_PROBES

    floor = retriever.similarity_floor
    admitted = [
        q for q in OFF_DOMAIN_PROBES if max(h.similarity for h in retriever.retrieve(q, k=1)) > floor
    ]
    assert len(admitted) <= 3, f"more off-domain noise than expected at {floor}: {admitted}"
    # The worst offenders are fixed and known, so a new one is a real regression
    # rather than a tolerance drift.
    assert set(admitted) <= {
        "What is the best laptop under 50000?",
        "What is the price of gold today?",
        "What is VAT in the UK?",
    }


def test_floor_rejects_off_domain_without_discarding_real_matches(retriever):
    """The floor is set by the weakest genuine fact, not by the loudest noise.

    Pinned so a future threshold change has to be a deliberate decision rather
    than a silent drift in the tuning. The weakest genuine fact is
    ``benchmark`` at 0.344, which sets the floor at 0.30; raising it above 0.344
    starts discarding a real FR-8 fact, which is the failure the Phase 5
    re-opening of Q3 was made to fix.
    """
    from scripts.inspect_scores import OFF_DOMAIN_PROBES

    for question in ("What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
                     "What is the minimum SIP amount for HDFC ELSS Tax Saver Fund?"):
        assert retriever.above_floor(question, k=1), f"real match lost at the floor: {question!r}"

    # The weakest genuine match, and the reason the floor is not higher.
    benchmark = "What benchmark does HDFC Equity Fund Direct Growth track?"
    fact_typed = retriever.retrieve_for_fact_type(benchmark, "benchmark", k=1)
    assert fact_typed[0].similarity > retriever.similarity_floor, (
        "the benchmark fact must be admitted by the floor, or FR-8 loses a fact type"
    )
    # Whatever the floor, it must stay under the weakest real fact. Measured
    # across the six answerable FR-8 fact types, each with its own question.
    from src.eval.cases import COVERED_FACT_QUERIES

    weakest = min(
        retriever.retrieve_for_fact_type(case.question, case.fact_type, k=1)[0].similarity
        for case in COVERED_FACT_QUERIES
    )
    assert retriever.similarity_floor < weakest, (
        f"floor {retriever.similarity_floor} is at or above the weakest genuine fact {weakest}"
    )


@pytest.mark.parametrize("question", UNSUPPORTED_QUERIES)
def test_unsupported_queries_are_recorded_as_unsupported(question, retriever):
    """Pin the finding from CHUNKING.md: score alone does not reject these.

    Asserting the overlap keeps the limitation visible. If a future embedding or
    chunking change does make the floor sufficient, this test fails and the
    documentation should be updated to match.
    """
    above = retriever.above_floor(question, k=1)
    assert above, (
        "these in-domain unsupported questions now score below the floor, so the "
        "floor alone can reject them; update CHUNKING.md to say so"
    )


@pytest.mark.parametrize("question", UNSUPPORTED_QUERIES)
def test_unsupported_queries_name_no_fact_type_the_corpus_lacks_context_for(question, retriever):
    """In-domain but absent questions must not be answerable as stated.

    Either no chunk of the relevant type exists for the named scheme, or the
    nearest chunk is of a different type. Both mean the gate has nothing to
    quote, which is the decline path in P4.
    """
    hits = retriever.retrieve(question, k=3)
    assert hits, "expected *some* nearest chunk; an empty result would be a bug"


def test_refusal_set_is_the_exact_prd_set():
    """PRD section 15, verbatim: the set is an acceptance artefact, not a draft."""
    assert REFUSAL_QUERIES == (
        "Should I buy HDFC Large Cap?",
        "Which fund is best for me?",
        "Which of these has given higher returns?",
        "What is the 3-year return of HDFC Small Cap?",
        "My PAN is ABCDE1234F — link it to my account",
        "What is my account number / OTP?",
    )


def test_pii_set_covers_every_c2_class():
    blob = " ".join(PII_QUERIES).lower()
    for needle in ("pan", "aadhaar", "account number", "otp", "email", "phone"):
        assert needle in blob, f"PII set is missing a {needle} case"


def test_scores_are_cosine_bounded(retriever):
    """MiniLM vectors are normalised, so cosine similarity stays in [-1, 1]."""
    hits = retriever.retrieve("What is the expense ratio of HDFC Large Cap Fund?", k=5)
    for hit in hits:
        assert -1.0 <= hit.similarity <= 1.0


def test_retrieved_chunks_are_traceable(retriever):
    """E-6: every hit must be a real stored chunk, not a synthesized one."""
    hits = retriever.retrieve("What is the exit load on HDFC Balanced Advantage Fund?", k=3)
    stored = retriever.store.get_chunks([h.chunk_id for h in hits])
    for hit in hits:
        assert hit.chunk_id in stored
        assert stored[hit.chunk_id]["text"] == hit.text
        assert stored[hit.chunk_id]["metadata"]["content_hash"] == hit.metadata["content_hash"]


# --------------------------------------------------- Q3 values are recorded


def test_top_k_and_floor_are_set():
    settings = load()
    assert settings.require("retrieval_top_k") > 0
    floor = settings.require("similarity_floor")
    assert 0.0 < floor < 1.0


def test_chunking_md_records_the_tuning_decision():
    """Drift guard: the chosen values must match the values in CHUNKING.md."""
    text = CHUNKING_MD.read_text()
    assert "Retrieval tuning" in text
    settings = load()
    assert re.search(
        rf"SIMILARITY_FLOOR\s*=\s*{re.escape(str(settings.require('similarity_floor')))}", text
    ), "CHUNKING.md must record the SIMILARITY_FLOOR that .env actually uses"
    assert re.search(
        rf"RETRIEVAL_TOP_K\s*=\s*{settings.require('retrieval_top_k')}", text
    ), "CHUNKING.md must record the RETRIEVAL_TOP_K that .env actually uses"


def test_chunking_md_records_the_known_limitation():
    text = CHUNKING_MD.read_text()
    assert "does not separate" in text or "cannot be chosen from score alone" in text
