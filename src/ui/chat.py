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
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

try:  # Streamlit is a P6 dependency, but this module must import without it.
    import streamlit as st
except Exception:  # pragma: no cover - only hit outside a Streamlit runtime
    st = None  # type: ignore[assignment]

from src.config import Settings, load
from src.eval.cases import COVERED_FACT_QUERIES, FactQuery
from src.guardrails.messages import DISCLAIMER
from src.query.pipeline import AnswerResponse
from src.ui.answer_view import render_answer

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


# -- renderers -----------------------------------------------------------------


def render_welcome() -> None:
    """The welcome line, the 3 example buttons, and the facts-only note."""
    if st is None:  # pragma: no cover
        return
    st.markdown(f"### {WELCOME_LINE}")
    st.caption(DISCLAIMER)
    _render_example_buttons()
    _render_stats()


def _render_example_buttons() -> None:
    """Exactly 3 example questions, chosen by measured confidence (FR-15)."""
    columns = st.columns(EXAMPLE_COUNT)
    for column, case in zip(columns, EXAMPLE_QUESTIONS):
        with column:
            if st.button(case.question, key=f"example::{case.fact_type}"):
                st.session_state[EXAMPLE_PICK_KEY] = case.question


def _render_stats() -> None:
    """The FR-25 corpus stats line."""
    st.caption(corpus_stats().render())


def render_transcript() -> None:
    """Replay the message list, newest last."""
    if st is None:  # pragma: no cover
        return
    for record in messages():
        if record["role"] == "user":
            with st.chat_message("user"):
                st.markdown(record["content"])
            continue
        response: Optional[AnswerResponse] = record.get("response")
        with st.chat_message("assistant"):
            if response is None:
                st.markdown(record["content"])
                continue
            st.markdown(render_answer(response))
            render_sources(response)


def render_sources(response: AnswerResponse) -> None:
    """The optional collapsible retrieval trace (FR-22).

    Additive and explicitly not a second citation: the citation above is the one
    link E-1 counts. This lists the chunks that were considered, including the
    ones that lost, which is what makes an E-6 failure debuggable.

    The citation link is rendered through ``st.link_button`` for a factual
    answer so the user can open the exact public page cited (FR-23).
    """
    if st is None:  # pragma: no cover
        return
    if response.citation_url:
        st.link_button("Open source page", response.citation_url)
    if not response.sources:
        return
    with st.expander(f"Sources ({len(response.sources)} chunks considered)"):
        rows = "".join(
            f"<tr><td><code>{source.chunk_id}</code></td>"
            f"<td>{source.section or '—'}</td>"
            f"<td>{source.similarity:.3f}</td>"
            f"<td><code>{source.fact_type}</code></td></tr>"
            for source in response.sources
        )
        st.markdown(
            "<table>"
            "<tr><th>chunk_id</th><th>section</th><th>score</th><th>fact_type</th></tr>"
            f"{rows}</table>"
        )


def render_refusal(response: AnswerResponse) -> None:
    """Render a non-answer path with its own link (FR-10, FR-11).

    Separate from :func:`render_sources` because a refusal has no citation and
    its link carries a different meaning: an educational resource on advice
    questions, an official factsheet on performance questions.
    """
    if st is None:  # pragma: no cover
        return
    st.markdown(render_answer(response))
    if response.link:
        st.link_button("Learn more" if response.path != "performance" else "Official factsheet", response.link)
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
    with st.chat_message("assistant"):
        st.markdown(render_answer(response))
        render_sources(response)


def take_pending_question() -> Optional[str]:
    """Consume an example-button click, returning the question once.

    Returns the value and clears it in the same call, so a rerun caused by
    something unrelated does not re-submit the same example question.
    """
    if st is None:  # pragma: no cover
        return None
    picked = st.session_state.get(EXAMPLE_PICK_KEY)
    if picked:
        st.session_state[EXAMPLE_PICK_KEY] = None
    return picked


def require_streamlit() -> None:
    """Fail loudly if the UI was imported outside a Streamlit runtime."""
    if st is None:  # pragma: no cover
        raise RuntimeError("streamlit is not importable; install requirements.txt")
