"""UI entrypoint — the tiny chat app (Phase 6, architecture.md K18, FR-15).

    streamlit run app.py

This file is intentionally thin (AD-5). It may not contain retrieval, prompting,
or guardrail logic: every answer comes from :func:`src.query.pipeline.answer`,
already screened, routed, gated and validated, and every decision about how to
display it lives in :mod:`src.ui.chat`.

The only thing this file decides is *when* to call the pipeline. That is
irreducible: Streamlit reruns the whole script on every interaction, so the call
has to be guarded by the presence of a new question or it would re-run the LLM
on each click.

The app never ingests. It reads the persisted collection, so a restart answers
immediately with no re-embedding (TC-3, E-8).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

import streamlit as st

from src.query.pipeline import AnswerError, answer
from src.ui.chat import (
    append_exchange,
    clear,
    init_state,
    inject_theme,
    messages,
    render_header,
    render_input_note,
    render_sidebar,
    render_transcript,
    render_turn,
    render_user_bubble,
    render_welcome,
    require_streamlit,
    runtime_retriever,
    take_pending_question,
)
from src.ui.answer_view import count_links, render_answer


def main() -> None:
    require_streamlit()
    # `set_page_config` must be the first Streamlit call in the script, so the
    # theme and stylesheet are injected immediately after it and before anything
    # is drawn. The page config carries the base colours too, so the first paint is
    # already on-palette rather than flashing Streamlit's default red.
    st.set_page_config(
        page_title="FundFacts AI · Mutual Fund Facts",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    init_state()
    inject_theme()

    render_sidebar()

    if messages():
        render_header()
        render_transcript()
        st.button("New question", on_click=clear, key="new_question")
    else:
        render_welcome()

    # A chip, a sidebar shortcut and the example buttons all submit through the
    # same pending-question key, because a button and the text input are both
    # "submit" events and a click must not be lost between them.
    pending = take_pending_question()
    typed = st.chat_input("Ask a factual question about a mutual fund…")
    render_input_note()

    question = pending or typed
    if not question:
        return

    render_user_bubble(question)

    with st.spinner("Looking up the sources…"):
        try:
            # `runtime_retriever` is cached per process, so the store handle and
            # the embedding graph are opened once and reused by every later
            # question instead of rebuilt inside each `answer` call.
            response = answer(question, retriever=runtime_retriever())
        except AnswerError as exc:
            st.error(f"I could not answer that: {exc}")
            return

    append_exchange(question, response)
    render_turn(response)

    # FR-22's "not from sources" signal, and a self-check that E-1 still holds in
    # the running app rather than only in tests: a factual answer must render
    # exactly one link. A second link here would mean the model emitted a URL
    # that survived the strip, which is the failure AD-3 exists to prevent.
    #
    # The check reads `render_answer`, not the card markup, because that is the
    # canonical plain-text rendering the eval harness counts. The card is built
    # from the same citation field, so the two cannot disagree about the URL.
    if not response.is_refusal and count_links(render_answer(response)) != 1:
        st.warning("This answer did not render exactly one source link.")


if __name__ == "__main__":
    main()
