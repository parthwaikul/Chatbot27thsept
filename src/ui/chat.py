"""K18 — the chat view (architecture.md §4, FR-15, FR-20, FR-22, FR-25).

Everything Streamlit-specific is imported *inside* the functions that need it,
except the module-level ``st`` import guarded by a fallback. That is deliberate:
``chat.py`` holds the session state, the example questions, the corpus stats and
the render decisions, and all of that must be importable and testable without a
running Streamlit server. Importing the whole chat module should never require a
browser, a server, or an event loop.

AD-5 says the UI is a thin view, so two rules hold throughout:

* No retrieval, prompting, or guardrail logic. Every answer comes from
  :func:`src.query.pipeline.answer`, already routed and validated.
* No re-derivation of what the pipeline decided. ``AnswerResponse.path`` and
  ``gate_reason`` are read, not recomputed, so the UI cannot disagree with the
  eval harness about which branch answered.

The one thing this layer owns is *presentation*: which of the five FR-8 example
questions to show, how to spell the citation link, and what to say when the
corpus is empty.

**Presentation cannot add facts.** Everything drawn here is either a literal in
this file (product copy, labels, icons) or a string the pipeline produced. The one
place this file touches answer content is :func:`emphasize_numbers`, which wraps
a number *the model already wrote* in a styled span; it matches a run of
characters and returns them unchanged, so no value can be derived, rounded or
invented. That is why it is a regex over existing text rather than a formatting of
parsed fields: a parser would be able to change a number, and this cannot.
"""

from __future__ import annotations

import csv
import html
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:  # Streamlit is a P6 dependency, but this module must import without it.
    import streamlit as st
except Exception:  # pragma: no cover - only hit outside a Streamlit runtime
    st = None  # type: ignore[assignment]

from src.config import Settings, load
from src.eval.cases import COVERED_FACT_QUERIES, FactQuery
from src.guardrails.messages import DISCLAIMER
from src.query.pipeline import AnswerResponse
from src.rag.retriever import Retriever
from src.ui.answer_view import render_answer, source_label, split_block
from src.ui.icons import (
    ICON_CHAT,
    ICON_CHEVRON,
    ICON_EXTERNAL,
    ICON_INFO,
    ICON_LOGO,
    ICON_SOURCES,
    ICON_SPARK,
)
from src.ui.theme import inject_css

#: FR-15 fixes the count at exactly 3. Not a default: the docs say "exactly 3"
#: twice, and a fourth example would be a spec deviation rather than an addition.
EXAMPLE_COUNT = 3

#: The welcome line. Short, factual, and it names what the assistant does rather
#: than performing friendliness the brief does not ask for.
WELCOME_LINE = (
    "Ask a factual question about the five HDFC schemes I have indexed. "
    "Every answer comes from those pages and cites one of them."
)

#: session-state key for the message list (FR-22). Prefixed so it cannot collide
#: with anything Streamlit itself stores.
MESSAGES_KEY = "chat_messages"
EXAMPLE_PICK_KEY = "chat_example_pick"

#: The header line, with "factual" as the emphasised word. Product copy, not a
#: fact claim: it says the assistant answers factual questions, which is exactly
#: what the guardrails enforce.
HEADER_TITLE = "Ask <em>factual</em> questions about mutual funds"
HEADER_SUBTITLE = (
    "Get clear, source-backed answers from the indexed mutual fund sources."
)

#: The trust badge. Deliberately describes the product's own behaviour and claims
#: nothing about regulation: the indexed pages are Groww fund pages, not AMFI or
#: SEBI publications, so a badge naming either would be false.
TRUST_BADGE = "Facts only · No investment advice"

#: The muted line under the input, and the sidebar footer. Both restate
#: :data:`src.guardrails.messages.DISCLAIMER` in the sidebar's own words.
INPUT_NOTE = "Facts-only assistant · No investment advice"
SIDEBAR_FOOTER = "Facts-only assistant. No investment advice."

#: Sidebar navigation. ``Chat`` is the only implemented view; the rest are drawn
#: as inert text because there is no equivalent screen to link to, and a button
#: that goes nowhere is worse than an honest label.
NAV_ACTIVE = "Chat"
NAV_ITEMS: Tuple[Tuple[str, str], ...] = (
    (ICON_CHAT, "Chat"),
    (ICON_INFO, "About"),
    (ICON_SOURCES, "Sources"),
    (ICON_SPARK, "How it works"),
)

#: The sidebar's popular questions and the header's quick chips, in the order a
#: reader meets them.
#:
#: Each is a question the corpus answers, and each is routed by the existing K10
#: rules — the UI invents no routing. ``fact_type`` names the FR-8 type the answer
#: is drawn from, and is ``None`` for the two attributes that are not FR-8 types
#: (:data:`src.guardrails.intent.FACT_ATTRIBUTES`), which the router still sends
#: down the factual path.
QUICK_QUESTIONS: Tuple[Dict[str, Any], ...] = (
    {"key": "expense_ratio", "label": "Expense ratio", "material": ":material/percent:", "fact_type": "expense_ratio"},
    {"key": "fund_size", "label": "Fund size (AUM)", "material": ":material/scale:", "fact_type": None},
    {"key": "fund_manager", "label": "Fund manager", "material": ":material/person:", "fact_type": None},
    {"key": "exit_load", "label": "Exit load", "material": ":material/logout:", "fact_type": "exit_load"},
    {"key": "min_sip", "label": "Minimum SIP", "material": ":material/savings:", "fact_type": "min_sip"},
    {"key": "riskometer", "label": "Riskometer", "material": ":material/speed:", "fact_type": "riskometer"},
    {"key": "benchmark", "label": "Benchmark", "material": ":material/bar_chart:", "fact_type": "benchmark"},
)

#: The two shortcuts whose question text has no FR-8 case to reuse, written out
#: here. Both are answerable from the indexed pages and both route to FACT through
#: :data:`src.guardrails.intent.FACT_ATTRIBUTES`; they are literal question
#: strings, not new backend knowledge, and the answer still comes from the model.
_EXTRA_QUESTIONS: Dict[str, str] = {
    "fund_size": "What is the fund size of HDFC Large Cap Fund Direct Growth?",
    "fund_manager": "Who is the fund manager for HDFC Large Cap Fund Direct Growth?",
}

#: The scheme the written-out questions name. Chosen because it is the
#: highest-confidence scheme in the corpus and needs no clarification.
#: The scheme the written-out questions name. Chosen because it is the
#: highest-confidence scheme in the corpus and needs no clarification.
_DEFAULT_SCHEME = "hdfc_large_cap_direct_growth"


def _example_score(case: FactQuery) -> float:
    """Retrieval confidence for an example, from the measured distribution.

    These are the top scores recorded in ``CHUNKING.md`` §6 against the
    persisted corpus: expense ratio 0.770, min SIP 0.668, lock-in 0.543,
    exit load 0.472, riskometer 0.522, benchmark 0.344. They are hardcoded
    because the example list must render instantly on app start — measuring it
    would mean loading the embedding model to draw three buttons, which is the
    re-ingestion-shaped slowness P6 exists to avoid.
    """
    return {
        "expense_ratio": 0.770,
        "min_sip": 0.668,
        "lock_in": 0.543,
        "riskometer": 0.522,
        "exit_load": 0.472,
        "benchmark": 0.344,
        "statement_guide": 0.000,
    }.get(case.fact_type, 0.0)


#: The example questions, highest-confidence first.
#:
#: "Highest-confidence" is decided by the measured retrieval score, not by
#: reading order, so the three buttons a new user clicks are the three most
#: reliable answers the corpus can give. ``statement_guide`` is excluded
#: deliberately: it is the recorded Q1 gap and would decline on click, which
#: would make the very first interaction look broken.
EXAMPLE_QUESTIONS: List[FactQuery] = sorted(
    COVERED_FACT_QUERIES,
    key=_example_score,
    reverse=True,
)[:EXAMPLE_COUNT]


def quick_question_text(item: Dict[str, Any]) -> str:
    """The full question behind a chip or sidebar shortcut.

    Prefers the question already in :data:`COVERED_FACT_QUERIES` so the shortcut
    and the eval set cannot drift apart; falls back to the written-out strings for
    the two non-FR-8 attributes.
    """
    fact_type = item.get("fact_type")
    if fact_type:
        for case in COVERED_FACT_QUERIES:
            if case.fact_type == fact_type and case.source_id == _DEFAULT_SCHEME:
                return case.question
        for case in COVERED_FACT_QUERIES:
            if case.fact_type == fact_type:
                return case.question
    return _EXTRA_QUESTIONS.get(item["key"], item["label"])


def _shortcut_key(label: str) -> str:
    """A stable session-state key for a shortcut label."""
    return re.sub(r"[^a-z0-9]+", "_", str(label).lower()).strip("_")


@dataclass
class CorpusStats:
    """The FR-25 stats line: schemes indexed, chunk count, last ingestion.

    Read from the persisted store and ``corpus/sources.csv``. Both are
    filesystem reads, so this is cheap and safe to call on every rerun.
    """

    schemes: int = 0
    chunks: int = 0
    last_ingested: str = "unknown"

    def render(self) -> str:
        if not self.chunks:
            return (
                "No chunks indexed yet. Run `python ingest.py --stage all` first; "
                "the app never ingests on start (TC-3, E-8)."
            )
        return (
            f"{self.schemes} schemes indexed · {self.chunks} chunks · "
            f"last ingested {self.last_ingested}"
        )


def _build_runtime_retriever() -> Retriever:
    """Open the store and the ONNX embedding graph once for this process."""
    return Retriever(settings=load())


#: architecture.md D6: one model instance per process.
#:
#: Streamlit reruns the whole script on every interaction and
#: :func:`src.query.pipeline.answer` builds its own ``_Deps`` per call, so without
#: this the retriever — and with it the ONNX session and the store handle — was
#: rebuilt for every single question. ``st.cache_resource`` is the one cache that
#: is per-process rather than per-session, which is exactly the lifetime D6 asks
#: for, and it also keeps the first load off the critical path of later questions.
#:
#: Falls back to the bare function when Streamlit is absent so this module keeps
#: importing outside a server, per the note at the top of the file.
runtime_retriever: Callable[[], Retriever] = (
    st.cache_resource(show_spinner=False)(_build_runtime_retriever)
    if st is not None and hasattr(st, "cache_resource")
    else _build_runtime_retriever
)


def corpus_stats(settings: Optional[Settings] = None) -> CorpusStats:
    """Read FR-25 stats from the store and the ingestion manifest.

    Degrades rather than raises: a missing manifest means the numbers are
    unknown, not that the app is broken, and refusing to start over a stats line
    would be a bad trade.
    """
    settings = settings or load()
    stats = CorpusStats()

    try:
        from src.rag.vector_store import VectorStore

        stats.chunks = VectorStore(
            chroma_dir=settings.chroma_dir, collection=settings.chroma_collection
        ).count()
    except Exception:  # noqa: BLE001 - an empty/absent index is a state, not a bug
        stats.chunks = 0

    try:
        with open(settings.sources_csv_path, newline="", encoding="utf-8") as handle:
            rows = [row for row in csv.DictReader(handle) if row.get("source_id")]
        stats.schemes = len(rows)
        stamps = sorted(str(row.get("fetched_at") or "") for row in rows)
        newest = [s for s in stamps if s][-1:] or []
        if newest:
            stats.last_ingested = newest[0][:10]
    except Exception:  # noqa: BLE001
        stats.last_ingested = "unknown"

    return stats


# -- session state -------------------------------------------------------------


def init_state() -> None:
    """Create the session keys the chat needs (FR-22). Idempotent."""
    if st is None:  # pragma: no cover - non-Streamlit import
        return
    if MESSAGES_KEY not in st.session_state:
        st.session_state[MESSAGES_KEY] = []
    if EXAMPLE_PICK_KEY not in st.session_state:
        st.session_state[EXAMPLE_PICK_KEY] = None


def messages() -> List[Dict[str, Any]]:
    """The message list, as ``{role, content, response}`` records.

    ``response`` is the full :class:`AnswerResponse` for assistant turns. It is
    kept rather than the rendered string alone so the sources disclosure and the
    citation link can be re-rendered on every rerun without re-answering —
    Streamlit reruns the whole script on each interaction, and re-running the
    LLM on every click would be both slow and non-reproducible.
    """
    if st is None:  # pragma: no cover
        return []
    init_state()
    return list(st.session_state[MESSAGES_KEY])


def append_exchange(question: str, response: AnswerResponse) -> None:
    """Record one question and its answer."""
    if st is None:  # pragma: no cover
        return
    init_state()
    st.session_state[MESSAGES_KEY].extend(
        [
            {"role": "user", "content": question, "response": None},
            {"role": "assistant", "content": response.text, "response": response},
        ]
    )


def clear() -> None:
    """Empty the transcript and the example pick."""
    if st is None:  # pragma: no cover
        return
    st.session_state[MESSAGES_KEY] = []
    st.session_state[EXAMPLE_PICK_KEY] = None


def submit_question(question: str) -> None:
    """Queue a question to be answered on the next rerun.

    The same path an example click takes (FR-15). A chip, a sidebar shortcut and a
    typed question are all "submit" events on Streamlit, and this is the one place
    they converge, so a click cannot be lost between the button and the input.
    """
    if st is None:  # pragma: no cover
        return
    init_state()
    st.session_state[EXAMPLE_PICK_KEY] = question


# -- markup helpers -----------------------------------------------------------

#: A number already present in the answer, wrapped for emphasis.
#:
#: The pattern only matches a value that already carries a marker of being a
#: figure: a currency symbol, a percent, a scale word (Cr/crore/lakh), a decimal
#: point or a thousands separator. That deliberately excludes bare integers, so a
#: date ("30 Sep 2026") and a count ("3 years") are left alone, and — the point of
#: the exercise — it never *builds* a number: ``match.group(0)`` is emitted verbatim
#: inside the span, so stripping the tags reproduces the input exactly.
_NUMBER_RE = re.compile(
    r"(?<![\w/.-])(?:"
    r"(?:₹|Rs\.?|INR)\s?\d{1,3}(?:,\d{2,3})*(?:\.\d+)?(?:\s?(?:%|Cr|crore|lakh))?"
    r"|\d{1,3}(?:,\d{2,3})+(?:\.\d+)?(?:\s?(?:%|Cr|crore|lakh))?"
    r"|\d+\.\d+\s?(?:%|Cr|crore|lakh)?"
    r"|\d+\s?(?:%|Cr|crore|lakh)"
    r")(?![\w/-])",
    re.IGNORECASE,
)


def emphasize_numbers(text: str) -> str:
    """Wrap numbers already in ``text`` in an emphasis span.

    Presentation only, and deliberately unable to alter a value: the matched
    characters are emitted unchanged between the tags, so the visible text is
    byte-identical to the input. There is no arithmetic, rounding, unit
    conversion or parsing here, which is what makes it safe to run over a factual
    answer — a formatter that reconstructed the number from its parts could round,
    and a formatter that inferred a missing unit could invent one.
    """
    if not text:
        return ""
    return _NUMBER_RE.sub(lambda m: f'<span class="ff-num">{m.group(0)}</span>', text)


def escape(text: str) -> str:
    """HTML-escape model text before embedding it in a card."""
    return html.escape(text or "", quote=True)


def _svg(icon: str, extra_class: str = "") -> str:
    """Render one of the icon constants as inline SVG."""
    classes = f' class="{extra_class}"' if extra_class else ""
    return f"<svg{classes} viewBox='0 0 24 24' fill='none' aria-hidden='true'>{icon}</svg>"


# -- sidebar ------------------------------------------------------------------


def render_sidebar() -> None:
    """Brand, navigation, popular questions, and the facts-only footer.

    The shortcuts submit through :func:`submit_question`, the same mechanism the
    example buttons use, so no navigation behaviour is invented here.
    """
    if st is None:  # pragma: no cover
        return
    with st.sidebar:
        st.markdown(
            "<div class='ff-brand'>"
            f"<div class='ff-brand__mark'>{_svg(ICON_LOGO)}</div>"
            "<div>"
            "<div class='ff-brand__name'>FundFacts AI</div>"
            "<div class='ff-brand__sub'>Facts-only Mutual Fund Assistant</div>"
            "</div></div>",
            unsafe_allow_html=True,
        )
        _render_nav()
        st.markdown("<hr class='ff-divider'/>", unsafe_allow_html=True)
        st.markdown("<div class='ff-label'>Popular Questions</div>", unsafe_allow_html=True)
        _render_popular_questions()
        st.markdown("<hr class='ff-divider'/>", unsafe_allow_html=True)
        st.markdown(
            f"<div class='ff-sidebar-foot'>{escape(SIDEBAR_FOOTER)}</div>",
            unsafe_allow_html=True,
        )


def _render_nav() -> None:
    """Draw the four navigation rows, with ``Chat`` marked active."""
    rows = []
    for icon, label in NAV_ITEMS:
        active = " ff-nav__item--active" if label == NAV_ACTIVE else ""
        rows.append(
            f"<div class='ff-nav__item{active}'>{_svg(icon)}"
            f"<span>{escape(label)}</span></div>"
        )
    st.markdown(
        "<div class='ff-nav'>" + "".join(rows) + "</div>", unsafe_allow_html=True
    )


def _render_popular_questions() -> None:
    """One quiet button per popular question."""
    for item in QUICK_QUESTIONS:
        question = quick_question_text(item)
        key = f"popular::{_shortcut_key(item['label'])}"
        if st.button(
            question,
            key=key,
            help=question,
            on_click=submit_question,
            args=(question,),
        ):
            submit_question(question)


# -- header -------------------------------------------------------------------


def render_header() -> None:
    """The compact header: trust badge, title, subtitle, quick-question chips."""
    if st is None:  # pragma: no cover
        return
    st.markdown(
        f"<div class='ff-eyebrow'>{escape(TRUST_BADGE)}</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<h1 class='ff-title'>{HEADER_TITLE}</h1>", unsafe_allow_html=True
    )
    st.markdown(
        f"<p class='ff-subtitle'>{escape(HEADER_SUBTITLE)}</p>",
        unsafe_allow_html=True,
    )
    _render_chips()


def _render_chips() -> None:
    """The quick-question chips, in one wrapping row.

    Drawn as a flex row of real Streamlit buttons rather than HTML links, because
    a clickable-looking ``<div>`` is not clickable and would be a silent dead end.
    """
    with st.container(key="ff_chiprow"):
        columns = st.columns(len(QUICK_QUESTIONS))
        for column, item in zip(columns, QUICK_QUESTIONS):
            question = quick_question_text(item)
            with column:
                if st.button(
                    item["label"],
                    key=f"chip::{_shortcut_key(item['label'])}",
                    help=question,
                    icon=item["material"],
                    on_click=submit_question,
                    args=(question,),
                ):
                    submit_question(question)


# -- answer card --------------------------------------------------------------


def render_answer_card(response: AnswerResponse) -> None:
    """Draw one assistant turn: the answer, then a muted metadata row.

    The answer text is :func:`src.ui.answer_view.render_answer`'s body, unchanged.
    Only the *arrangement* differs from the plain renderer: the citation and the
    freshness date move into their own row so the answer reads cleanly, and the
    citation is labelled by host (``Source: Groww``) with the link still pointing at
    the exact URL the pipeline cited.
    """
    if st is None:  # pragma: no cover
        return
    parts = split_block(response)

    if parts.is_refusal:
        st.markdown(
            "<div class='ff-refusal'>"
            f"<p>{escape(parts.answer)}</p>"
            f"{_metadata_row('', parts.freshness, is_refusal=True)}"
            "</div>",
            unsafe_allow_html=True,
        )
        return

    body = emphasize_numbers(escape(parts.answer)) if parts.answer else ""
    st.markdown(
        "<div class='ff-answer'>"
        f"<p>{body}</p>"
        f"{_metadata_row(parts.citation_url, parts.freshness)}"
        "</div>",
        unsafe_allow_html=True,
    )


def _metadata_row(
    citation_url: str, freshness: str, is_refusal: bool = False
) -> str:
    """The muted source/freshness row under an answer.

    The anchor's ``href`` is the citation the pipeline chose, byte-for-byte; only
    its visible text is shortened. A refusal carries no citation — its link is a
    different kind of thing and is drawn by :func:`render_refusal` — so this draws
    only the freshness date for it and never a "Source:" label, which would be an
    E-1 violation the user could see.

    On the answer path a missing citation still says so rather than printing a
    bare answer, because that would be the same E-1 failure with no signal.
    """
    if citation_url:
        source = (
            "<span class='ff-meta__source'>"
            f"Source: {escape(source_label(citation_url))}{_svg(ICON_EXTERNAL)}"
            f"<a href='{escape(citation_url)}' target='_blank' "
            "rel='noopener noreferrer'></a></span>"
        )
    elif is_refusal:
        source = ""
    else:
        source = "<span class='ff-meta__source'>Source: citation unavailable</span>"
    fresh = (
        f"<span class='ff-meta__fresh'>{escape(freshness)}</span>" if freshness else ""
    )
    return f"<div class='ff-meta'>{source}{fresh}</div>"


# -- renderers -----------------------------------------------------------------


def render_welcome() -> None:
    """The empty state: onboarding copy, the 3 examples, and the facts-only note.

    FR-15 still fixes the example count at exactly 3 and the questions are still
    the measured-confidence picks, so the required behaviour is unchanged; only the
    framing around them is restyled.
    """
    if st is None:  # pragma: no cover
        return
    st.markdown(
        "<div class='ff-empty'>"
        f"<div class='ff-empty__mark'>{_svg(ICON_LOGO)}</div>"
        "<div class='ff-empty__title'>What would you like to know?</div>"
        "<div class='ff-empty__sub'>Ask factual questions about mutual funds and "
        "get concise, source-backed answers.</div>"
        "</div>",
        unsafe_allow_html=True,
    )
    _render_example_buttons()
    st.markdown(
        f"<div class='ff-stats'>{escape(DISCLAIMER)}</div>", unsafe_allow_html=True
    )
    _render_stats()


def _render_example_buttons() -> None:
    """Exactly 3 example questions, chosen by measured confidence (FR-15)."""
    st.markdown("<div class='ff-examples'></div>", unsafe_allow_html=True)
    for case in EXAMPLE_QUESTIONS:
        st.button(
            case.question,
            key=f"example::{case.fact_type}",
            on_click=submit_question,
            args=(case.question,),
        )


def _render_stats() -> None:
    """The FR-25 corpus stats line."""
    st.markdown(
        f"<div class='ff-stats'>{escape(corpus_stats().render())}</div>",
        unsafe_allow_html=True,
    )


def render_input_note() -> None:
    """The muted facts-only line that sits beside the chat input."""
    if st is None:  # pragma: no cover
        return
    st.markdown(
        f"<div class='ff-input-note'>{escape(INPUT_NOTE)}</div>",
        unsafe_allow_html=True,
    )


def render_user_bubble(text: str) -> None:
    """One user message, right-aligned on a pale-mint surface.

    Session state is untouched: :meth:`append_exchange` still records the turn and
    the chat input is still ``st.chat_input``. Only the drawing changed.
    """
    if st is None:  # pragma: no cover
        return
    with st.chat_message("user", avatar=":material/person_outline:"):
        st.markdown(escape(text))


def render_transcript() -> None:
    """Replay the message list, newest last."""
    if st is None:  # pragma: no cover
        return
    for record in messages():
        if record["role"] == "user":
            render_user_bubble(record["content"])
            continue
        response: Optional[AnswerResponse] = record.get("response")
        with st.chat_message(
            "assistant", avatar=":material/finance_mode:"
        ):
            if response is None:
                st.markdown(record["content"])
                continue
            render_answer_card(response)
            render_sources(response)


def render_sources(response: AnswerResponse) -> None:
    """The optional collapsible retrieval trace (FR-22).

    Additive and explicitly not a second citation: the citation in the answer card
    is the one link E-1 counts. This lists the chunks that were considered,
    including the ones that lost, which is what makes an E-6 failure debuggable.

    The table's contents are unchanged from the plain renderer — chunk id, section,
    score, fact type — only the markup around them is restyled.
    """
    if st is None:  # pragma: no cover
        return
    if response.citation_url:
        st.markdown(
            "<div class='ff-openlink'></div>", unsafe_allow_html=True
        )
        st.link_button("Open source page ↗", response.citation_url)
    if not response.sources:
        return
    with st.expander(f"Sources ({len(response.sources)} chunks considered)"):
        rows = "".join(
            f"<tr><td><code>{escape(source.chunk_id)}</code></td>"
            f"<td>{escape(source.section or '—')}</td>"
            f"<td>{source.similarity:.3f}</td>"
            f"<td><code>{escape(source.fact_type)}</code></td></tr>"
            for source in response.sources
        )
        st.markdown(
            "<div class='ff-sources'>"
            "<table>"
            "<tr><th>chunk_id</th><th>section</th><th>score</th><th>fact_type</th></tr>"
            f"{rows}</table></div>",
            unsafe_allow_html=True,
        )


def render_refusal(response: AnswerResponse) -> None:
    """Render a non-answer path with its own link (FR-10, FR-11).

    Separate from :func:`render_sources` because a refusal has no citation and
    its link carries a different meaning: an educational resource on advice
    questions, an official factsheet on performance questions.
    """
    if st is None:  # pragma: no cover
        return
    render_answer_card(response)
    if response.link:
        label = "Learn more" if response.path != "performance" else "Official factsheet"
        st.link_button(f"{label} ↗", response.link)
    if response.schemes:
        with st.expander("Schemes I have indexed"):
            for scheme in response.schemes:
                st.markdown(f"- {scheme}")


def render_turn(response: AnswerResponse) -> None:
    """Dispatch one response to the right renderer.

    The branch is on ``response.is_refusal``, the same predicate
    ``answer_view.render_answer`` uses, so the UI and the eval harness can never
    classify a response differently.
    """
    if st is None:  # pragma: no cover
        return
    if response.is_refusal:
        render_refusal(response)
        return
    with st.chat_message("assistant", avatar=":material/finance_mode:"):
        render_answer_card(response)
        render_sources(response)


def take_pending_question() -> Optional[str]:
    """Consume a queued question, returning it once.

    Returns the value and clears it in the same call, so a rerun caused by
    something unrelated does not re-submit the same question.
    """
    if st is None:  # pragma: no cover
        return None
    picked = st.session_state.get(EXAMPLE_PICK_KEY)
    if picked:
        st.session_state[EXAMPLE_PICK_KEY] = None
    return picked


def inject_theme() -> None:
    """Push the stylesheet. Thin wrapper so ``app.py`` need not import it."""
    inject_css(st)


def require_streamlit() -> None:
    """Fail loudly if the UI was imported outside a Streamlit runtime."""
    if st is None:  # pragma: no cover
        raise RuntimeError("streamlit is not importable; install requirements.txt")
