"""Phase 6 — chat UI tests.

The UI is hard to test by clicking, so these pin the parts that are logic
rather than pixels: which three examples appear, that the stats read from the
right places, and that the render layer cannot disagree with the pipeline about
which branch answered (AD-5).

Streamlit is exercised through a stub, so the tests run without a server and
without a browser.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest

from src.query.pipeline import AnswerResponse, RetrievedSource
from src.ui import chat


# -- a Streamlit double --------------------------------------------------------


class _Context:
    """Streamlit's ``with`` target: columns and expanders are context managers."""

    def __init__(self, stub: "_Stub") -> None:
        self._stub = stub

    def __enter__(self) -> "_Context":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def __getattr__(self, name):
        return getattr(self._stub, name)


@dataclass
class _Stub:
    """Records what the render functions asked Streamlit to draw."""

    session_state: Dict[str, Any] = field(default_factory=dict)
    buttons: List[Dict[str, Any]] = field(default_factory=list)
    links: List[Dict[str, str]] = field(default_factory=list)
    expanders: List[str] = field(default_factory=list)
    markdown: List[str] = field(default_factory=list)
    captions: List[str] = field(default_factory=list)
    chat_messages: List[str] = field(default_factory=list)
    tables: List[str] = field(default_factory=list)
    # ``pending_button`` is the id of a button that returns True on click, which
    # is how an example click is simulated without a browser.
    pending_button: Optional[str] = None
    chat_input_value: Optional[str] = None

    def button(self, label, key=None, **kwargs):
        self.buttons.append({"label": label, "key": key})
        return key == self.pending_button

    def link_button(self, label, url):
        self.links.append({"label": label, "url": url})
        return False

    def expander(self, label):
        self.expanders.append(label)
        return _Context(self)

    def markdown(self, body):  # noqa: F811 - intentionally shadows the list attr
        self.markdown_lines.append(body)

    def caption(self, body):
        self.captions.append(body)

    def chat_message(self, role):
        self.chat_messages.append(role)
        return _Context(self)

    def columns(self, count):
        return [_Context(self) for _ in range(count)]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def stub(monkeypatch):
    fake = _Stub()
    fake.markdown_lines = []  # type: ignore[attr-defined]

    def _markdown(body):
        fake.markdown_lines.append(body)

    fake.markdown = _markdown  # type: ignore[assignment]
    monkeypatch.setattr(chat, "st", fake)
    return fake


def _response(**overrides) -> AnswerResponse:
    base = dict(
        text="The expense ratio is 1.03%.",
        citation_url="https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
        last_updated="Last updated from sources: 28 Sep 2026",
        path="answer",
        intent="FACT",
    )
    base.update(overrides)
    return AnswerResponse(**base)


# -- the 3 example questions (FR-15) ------------------------------------------


def test_there_are_exactly_three_examples():
    assert len(chat.EXAMPLE_QUESTIONS) == 3
    assert chat.EXAMPLE_COUNT == 3


def test_every_example_is_a_question_the_corpus_can_answer():
    """A button that declines on click would make the first run look broken."""
    from src.eval.cases import COVERED_FACT_QUERIES

    covered = {case.fact_type for case in COVERED_FACT_QUERIES}
    for case in chat.EXAMPLE_QUESTIONS:
        assert case.fact_type in covered
        assert case.fact_type != "statement_guide", "the Q1 gap must not be an example"
        assert case.question.endswith("?")


def test_examples_are_ordered_by_measured_confidence():
    scores = [chat._example_score(case) for case in chat.EXAMPLE_QUESTIONS]
    assert scores == sorted(scores, reverse=True), scores


def test_the_three_highest_confidence_fact_types_are_chosen():
    """Pins the *selection*, not just the count.

    The three most reliable answers are expense_ratio, min_sip and lock_in by
    measured score. If a future corpus change makes benchmark reliable, this
    test should fail and the choice be re-made deliberately.
    """
    assert {case.fact_type for case in chat.EXAMPLE_QUESTIONS} == {
        "expense_ratio",
        "min_sip",
        "lock_in",
    }


def test_the_weakest_fact_type_is_not_an_example():
    """benchmark scores 0.344, the weakest genuine match; it is not a button."""
    assert "benchmark" not in {case.fact_type for case in chat.EXAMPLE_QUESTIONS}


def test_render_welcome_draws_three_buttons_the_note_and_the_stats(stub):
    chat.render_welcome()
    assert len(stub.buttons) == 3
    assert {b["key"] for b in stub.buttons} == {
        f"example::{case.fact_type}" for case in chat.EXAMPLE_QUESTIONS
    }
    joined = " ".join(stub.markdown_lines) + " ".join(stub.captions)
    assert "Facts-only. No investment advice." in joined
    assert "166 chunks" in joined or "chunks" in joined


# -- FR-25 corpus stats --------------------------------------------------------


def test_corpus_stats_reads_the_store_and_the_manifest():
    stats = chat.corpus_stats()
    assert stats.chunks == 166
    assert stats.schemes == 5
    assert stats.last_ingested == "2026-09-28"


def test_corpus_stats_render_reads_like_a_sentence():
    rendered = chat.CorpusStats(schemes=5, chunks=166, last_ingested="2026-09-28").render()
    assert "5 schemes" in rendered
    assert "166 chunks" in rendered
    assert "2026-09-28" in rendered


def test_an_empty_corpus_tells_the_user_what_to_run():
    """Zero chunks is a state, not a crash, and the message must be actionable."""
    rendered = chat.CorpusStats().render()
    assert "ingest.py" in rendered
    assert "never ingests" in rendered


def test_corpus_stats_degrade_when_the_manifest_is_missing(tmp_path):
    from src.config import load

    settings = load()
    broken = settings.sources_csv_path.parent / "nope.csv"
    stats = chat.corpus_stats(settings.__class__(**{**settings.__dict__, "sources_csv_path": broken}))
    assert stats.last_ingested == "unknown"
    assert stats.chunks == 166, "the store is still readable"


# -- session state (FR-22) -----------------------------------------------------


def test_init_state_is_idempotent(stub):
    chat.init_state()
    first = list(stub.session_state[chat.MESSAGES_KEY])
    chat.init_state()
    assert stub.session_state[chat.MESSAGES_KEY] == first == []


def test_an_exchange_is_recorded_as_user_then_assistant(stub):
    chat.init_state()
    response = _response()
    chat.append_exchange("What is the expense ratio?", response)
    records = chat.messages()
    assert [r["role"] for r in records] == ["user", "assistant"]
    assert records[0]["content"] == "What is the expense ratio?"
    assert records[1]["response"] is response


def test_the_full_response_is_kept_so_a_rerun_does_not_re_answer(stub):
    """Streamlit reruns the script on every interaction; re-calling the LLM
    there would be both slow and non-reproducible."""
    chat.init_state()
    chat.append_exchange("q", _response(citation_url="https://x.test/a"))
    assert chat.messages()[1]["response"].citation_url == "https://x.test/a"


def test_clear_empties_the_transcript(stub):
    chat.init_state()
    chat.append_exchange("q", _response())
    chat.clear()
    assert chat.messages() == []
    assert stub.session_state[chat.EXAMPLE_PICK_KEY] is None


# -- the example-button round trip --------------------------------------------


def test_a_clicked_example_is_returned_once_and_then_cleared(stub):
    chat.init_state()
    question = chat.EXAMPLE_QUESTIONS[0].question
    stub.session_state[chat.EXAMPLE_PICK_KEY] = question
    assert chat.take_pending_question() == question
    assert chat.take_pending_question() is None, "a rerun must not re-submit the question"


def test_take_pending_is_none_when_nothing_was_picked(stub):
    chat.init_state()
    assert chat.take_pending_question() is None


# -- rendering (FR-15, FR-22, FR-23) ------------------------------------------


def test_an_answer_renders_its_citation_as_a_link_button(stub):
    response = _response()
    chat.render_turn(response)
    assert stub.links == [
        {
            "label": "Open source page",
            "url": "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
        }
    ]


def test_a_refusal_links_to_its_educational_page_and_cites_nothing(stub):
    response = _response(
        path="advice",
        citation_url=None,
        link="https://investor.sebi.gov.in/",
        text="I answer factual questions only.",
    )
    chat.render_turn(response)
    assert stub.links == [{"label": "Learn more", "url": "https://investor.sebi.gov.in/"}]


def test_a_performance_refusal_is_labelled_as_a_factsheet(stub):
    """The two refusal links mean different things and must not share a label."""
    response = _response(
        path="performance",
        citation_url=None,
        link="https://files.hdfcfund.com/factsheet.pdf",
        text="Returns are in the official factsheet.",
    )
    chat.render_turn(response)
    assert stub.links[0]["label"] == "Official factsheet"


def test_an_ambiguous_refusal_lists_the_schemes(stub):
    response = _response(
        path="ambiguous",
        citation_url=None,
        link="https://investor.sebi.gov.in/",
        text="Which scheme do you mean?",
        schemes=("HDFC Large Cap Fund - Direct Growth", "HDFC Small Cap Fund - Direct Growth"),
    )
    chat.render_turn(response)
    assert "Schemes I have indexed" in stub.expanders


def test_the_sources_disclosure_lists_chunk_section_and_score(stub):
    response = _response(
        sources=[
            RetrievedSource(
                chunk_id="hdfc_large_cap_direct_growth::1::0",
                section="Expense ratio",
                similarity=0.770,
                fact_type="expense_ratio",
                source_url="https://groww.in/x",
            )
        ]
    )
    chat.render_turn(response)
    assert stub.expanders == ["Sources (1 chunks considered)"]
    table = stub.tables[0] if stub.tables else " ".join(stub.markdown_lines)
    assert "hdfc_large_cap_direct_growth::1::0" in table
    assert "Expense ratio" in table
    assert "0.770" in table


def test_no_disclosure_when_nothing_was_retrieved(stub):
    chat.render_turn(_response(sources=[]))
    assert stub.expanders == []


def test_a_refusal_shows_no_retrieval_disclosure(stub):
    """A PII refusal retrieves nothing, so there is no trace to show."""
    response = _response(path="pii", citation_url=None, text="I can't help.")
    chat.render_turn(response)
    assert stub.expanders == []


def test_the_transcript_replays_every_turn(stub):
    chat.init_state()
    chat.append_exchange("q1", _response())
    chat.append_exchange("q2", _response(path="advice", citation_url=None))
    chat.render_transcript()
    assert stub.chat_messages == ["user", "assistant", "user", "assistant"]


# -- AD-5: the UI must not re-derive the pipeline's decisions -----------------


def test_render_dispatches_on_the_same_predicate_the_view_layer_uses():
    """If these two disagreed, the UI and eval.py would score answers
    differently for the same response — so they must be the same check."""
    from src.ui.answer_view import render_answer as render_text

    for path, expected_refusal in (
        ("answer", False),
        ("pii", True),
        ("advice", True),
        ("performance", True),
        ("ambiguous", True),
        ("decline", True),
        ("gate_miss", True),
        ("llm_error", True),
    ):
        response = _response(path=path, citation_url=None if path != "answer" else "https://x.test")
        assert response.is_refusal is expected_refusal, path
        # A refusal must never render a "Source:" line.
        assert ("Source:" in render_text(response)) is not expected_refusal


def test_the_ui_imports_no_ingestion_entry_point():
    """AD-5 and TC-3: the UI reads the persisted store, it never rebuilds it."""
    import inspect

    source = inspect.getsource(chat)
    for forbidden in ("embed_documents", "upsert", "chunk_document", "run_stage"):
        assert forbidden not in source, f"{forbidden} must not appear in the UI layer"


def test_app_py_contains_no_retrieval_or_prompt_logic():
    """AD-5, as a check on the entrypoint itself."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path("app.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert "src.rag.retriever" not in imported
    assert "src.rag.prompts" not in imported
    assert "src.guardrails" not in imported
    assert "src.query.pipeline" in imported, "it must call the pipeline"
