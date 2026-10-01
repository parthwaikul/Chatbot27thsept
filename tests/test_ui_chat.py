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
    sidebars: List[str] = field(default_factory=list)
    containers: List[Optional[str]] = field(default_factory=list)
    # ``pending_button`` is the id of a button that returns True on click, which is
    # how an example click is simulated without a browser.
    pending_button: Optional[str] = None
    chat_input_value: Optional[str] = None

    def button(
        self,
        label,
        key=None,
        help=None,
        on_click=None,
        args=(),
        kwargs=None,
        *,
        type="secondary",
        icon=None,
        disabled=False,
        use_container_width=None,
        width="content",
    ):
        # The parameter list mirrors Streamlit 1.50's ``st.button`` exactly. If the
        # UI passes a keyword this version does not support (as ``label_visibility``
        # was), the call raises here instead of only crashing in a browser.
        self.buttons.append(
            {"label": label, "key": key, "args": list(args), "help": help, "icon": icon}
        )
        if key == self.pending_button and on_click is not None:
            on_click(*(args or ()), **(kwargs or {}))
            return True
        return key == self.pending_button

    def container(
        self,
        *,
        border=None,
        key=None,
        width="stretch",
        height="content",
        horizontal=False,
        horizontal_alignment="left",
        vertical_alignment="top",
        gap=None,
    ):
        # Mirrors Streamlit 1.50's ``st.container`` (which emits ``st-key-<key>``).
        self.containers.append(key)
        return _Context(self)

    def link_button(self, label, url):
        self.links.append({"label": label, "url": url})
        return False

    def expander(self, label):
        self.expanders.append(label)
        return _Context(self)

    def markdown(self, body, **kwargs):  # noqa: F811 - shadows the list attr
        self.markdown_lines.append(body)

    def caption(self, body):
        self.captions.append(body)

    def chat_message(self, role, **kwargs):
        self.chat_messages.append(role)
        return _Context(self)

    def columns(self, count):
        return [_Context(self) for _ in range(count)]

    @property
    def sidebar(self):
        """Mirrors Streamlit, where ``st.sidebar`` is itself a context manager."""
        self.sidebars.append("open")
        return _Context(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def stub(monkeypatch):
    fake = _Stub()
    fake.markdown_lines = []  # type: ignore[attr-defined]

    def _markdown(body, **kwargs):
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
            "label": "Open source page ↗",
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
    assert stub.links == [{"label": "Learn more ↗", "url": "https://investor.sebi.gov.in/"}]


def test_a_performance_refusal_is_labelled_as_a_factsheet(stub):
    """The two refusal links mean different things and must not share a label."""
    response = _response(
        path="performance",
        citation_url=None,
        link="https://files.hdfcfund.com/factsheet.pdf",
        text="Returns are in the official factsheet.",
    )
    chat.render_turn(response)
    assert stub.links[0]["label"] == "Official factsheet ↗"


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


# -- presentation-only safety: the redesign may not add or alter a fact -------


def test_emphasize_numbers_cannot_change_the_visible_text():
    """The emphasised styling must not derive, round or invent a value.

    Stripping the span tags has to reproduce the input byte-for-byte, which is
    what makes it safe to run over a factual answer: a formatter that rebuilt the
    number from parsed parts could round it, and one that inferred a unit could
    invent one. This one only wraps what was already there.
    """
    text = (
        "The AUM is ₹39,933.36 Cr and the expense ratio is 1.03%; the minimum SIP "
        "is ₹500, with 12,345 units and a 5.5 year track record."
    )
    styled = chat.emphasize_numbers(text)
    assert chat.emphasize_numbers("") == ""
    # Remove exactly the wrapper the function adds and the text is unchanged.
    assert styled.replace('<span class="ff-num">', "").replace("</span>", "") == text


def test_emphasize_numbers_highlights_real_values():
    styled = chat.emphasize_numbers("The expense ratio is 1.03%.")
    assert '<span class="ff-num">1.03%</span>' in styled
    styled_rupees = chat.emphasize_numbers("The AUM is ₹39,933.36 Cr.")
    assert '<span class="ff-num">₹39,933.36 Cr</span>' in styled_rupees
    assert '<span class="ff-num">₹500</span>' in chat.emphasize_numbers("The minimum SIP is ₹500.")


def test_emphasize_numbers_leaves_a_date_alone():
    """Freshness and dates are not figures to shout about."""
    assert chat.emphasize_numbers("Last updated 30 Sep 2026") == "Last updated 30 Sep 2026"


def test_a_chip_or_sidebar_click_queues_its_question(stub):
    """Both shortcut surfaces submit through the same pending-question key."""
    for item in chat.QUICK_QUESTIONS:
        chat.submit_question(chat.quick_question_text(item))
    assert stub.session_state[chat.EXAMPLE_PICK_KEY] == chat.quick_question_text(
        chat.QUICK_QUESTIONS[-1]
    )


def test_every_chip_is_a_real_button_with_a_native_icon(stub):
    """Streamlit 1.50 buttons cannot take ``label_visibility`` or inline SVG.

    Each chip must therefore be a genuine ``st.button`` carrying a Material icon
    string, laid out inside the keyed row the stylesheet targets.
    """
    chat.render_header()
    chips = [b for b in stub.buttons if str(b["key"]).startswith("chip::")]
    assert len(chips) == len(chat.QUICK_QUESTIONS)
    for button in chips:
        assert button["icon"].startswith(":material/"), button
    assert "ff_chiprow" in stub.containers


def test_every_quick_question_is_one_the_corpus_answers():
    """A shortcut that declines would make the product look broken on click."""
    from src.eval.cases import COVERED_FACT_QUERIES

    covered = {case.fact_type for case in COVERED_FACT_QUERIES}
    for item in chat.QUICK_QUESTIONS:
        question = chat.quick_question_text(item)
        assert question.endswith("?"), question
        fact_type = item.get("fact_type")
        if fact_type is not None:
            assert fact_type in covered, fact_type


def test_the_fund_manager_shortcut_uses_the_agreed_question():
    item = next(q for q in chat.QUICK_QUESTIONS if q["label"] == "Fund manager")
    assert chat.quick_question_text(item) == (
        "Who is the fund manager for HDFC Large Cap Fund Direct Growth?"
    )


def test_the_redesign_does_not_add_invented_facts_to_the_markup():
    """The mockup's illustrative figures must not appear anywhere in the UI layer.

    The Stitch reference carries trend, rank and tenure numbers the backend does
    not produce. None of them may be spelled into the presentation.
    """
    import inspect

    from src.ui import chat as chat_module
    from src.ui import icons as icons_module
    from src.ui import theme as theme_module

    source = (
        inspect.getsource(chat_module)
        + inspect.getsource(theme_module)
        + inspect.getsource(icons_module)
    ).lower()
    for invented in (
        "3-month",
        "3 month",
        "percentage growth",
        "category rank",
        "direct plan share",
        "tenure",
        "total experience",
        "amfi scheme code",
    ):
        assert invented not in source, f"invented fact {invented!r} in the UI layer"


def test_the_answer_card_links_the_exact_citation_it_was_given(stub, monkeypatch):
    """The pretty label must not change the href."""
    response = _response(
        citation_url="https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"
    )
    chat.render_answer_card(response)
    joined = " ".join(stub.markdown_lines)
    assert "Source: Groww" in joined
    assert (
        "href='https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth'"
        in joined
    )


def test_the_answer_card_keeps_the_freshness_line(stub):
    response = _response(last_updated="Last updated from sources: 30 Sep 2026")
    chat.render_answer_card(response)
    assert any("Last updated from sources: 30 Sep 2026" in line for line in stub.markdown_lines)


def test_the_answer_card_carries_no_source_line_for_a_refusal(stub):
    """E-1 counts one link on the answer path; a refusal must add none here."""
    response = _response(path="pii", citation_url=None, text="I can't help with that.")
    chat.render_answer_card(response)
    joined = " ".join(stub.markdown_lines)
    assert "href=" not in joined
    assert "Source: citation unavailable" not in joined


def test_the_stylesheet_hides_no_interactive_control():
    """A styling rule that set display:none on a control could break the app.

    Streamlit's markup is not a public API, so the stylesheet is coupled to it.
    This pins the one failure mode that would remove functionality rather than
    merely look wrong: hiding something the user has to click.
    """
    from src.ui.theme import stylesheet

    css = stylesheet().lower()
    for control in (
        "stchatinput",
        "stbutton",
        "stlinkbutton",
        "stexpander",
        "stchatmessage",
    ):
        # No display:none rule may name a control. The `#MainMenu, footer` rule is
        # the one legitimate hide and names neither.
        for rule in css.split("}"):
            if "display: none" in rule or "display:none" in rule:
                assert control not in rule, f"{control} is hidden by a CSS rule"


def test_sidebar_and_header_render_their_landmarks(stub):
    chat.render_sidebar()
    chat.render_header()
    sidebar_html = " ".join(stub.markdown_lines)
    assert "FundFacts AI" in sidebar_html
    assert "Facts-only" in sidebar_html
    assert "Popular Questions" in sidebar_html
    assert "Ask <em>factual</em> questions about mutual funds" in sidebar_html
    assert chat.TRUST_BADGE in sidebar_html


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
