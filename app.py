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
    messages,
    render_transcript,
    render_turn,
    render_welcome,
    require_streamlit,
    take_pending_question,
)
from src.ui.answer_view import count_links, render_answer


def main() -> None:
    require_streamlit()
    init_state()
    st.set_page_config(page_title="HDFC scheme facts", page_icon="📄", layout="centered")

    if not messages():
        render_welcome()
    else:
        st.button("New question", on_click=clear)

    render_transcript()

    # An example-button click arrives as a pending question, because the button
    # and the text input are both "submit" events and the click must not be lost.
    pending = take_pending_question()
    typed = st.chat_input("Ask a factual question")

    question = pending or typed
    if not question:
        return

    with st.chat_message("user"):
        st.markdown(question)

    with st.spinner("Looking up the sources…"):
        try:
            response = answer(question)
        except AnswerError as exc:
            st.error(f"I could not answer that: {exc}")
            return

    append_exchange(question, response)
    render_turn(response)

    # FR-22's "not from sources" signal, and a self-check that E-1 still holds in
    # the running app rather than only in tests: a factual answer must render
    # exactly one link. A second link here would mean the model emitted a URL
    # that survived the strip, which is the failure AD-3 exists to prevent.
    if not response.is_refusal and count_links(render_answer(response)) != 1:
        st.warning("This answer did not render exactly one source link.")


if __name__ == "__main__":
    main()
