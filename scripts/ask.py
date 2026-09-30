"""Interactive prompt for testing the live pipeline with a real Groq key.

    python scripts/ask.py
    python scripts/ask.py "What is the expense ratio of HDFC Large Cap Fund?"

This is the manual counterpart to ``scripts/verify_phase5.py``. That script runs
a fixed case list and exits non-zero, so it is a gate; this one takes whatever you
type, so it is a tool for finding the cases the fixed list did not think of.

The pipeline, guardrails and prompt are the ones the UI will call — nothing here
is a test double or a simplified path. Every collaborator is built once and
reused, so a second question is fast and does not re-embed the corpus.

The key is read from ``.env`` and never printed. Exit with ``/quit``.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import load  # noqa: E402
from src.query.pipeline import AnswerResponse, answer  # noqa: E402
from src.ui.answer_view import render_answer  # noqa: E402

#: Prefixes handled locally, so they never reach the model.
COMMANDS = {"/quit", "/exit", "/q", "/help", "/trace"}


def _show(response: AnswerResponse, elapsed: float, trace: bool) -> None:
    print()
    print(render_answer(response))
    print()
    meta = [f"path={response.path}"]
    if response.intent:
        meta.append(f"intent={response.intent}")
    if response.gate_reason:
        meta.append(f"gate={response.gate_reason}")
    if response.validator_checks:
        meta.append(f"checks={'/'.join(response.validator_checks)}")
    if response.marker:
        meta.append(f"marker={response.marker}")
    if response.retrieved_chunk_ids:
        meta.append(f"chunks={len(response.retrieved_chunk_ids)}")
    if response.top_similarity:
        meta.append(f"best_sim={response.top_similarity:.3f}")
    meta.append(f"{elapsed:.1f}s")
    print("  " + "  ".join(meta))
    if trace and response.retrieved_chunk_ids:
        print("  retrieved:")
        for chunk_id in response.retrieved_chunk_ids:
            print(f"    - {chunk_id}")


def _once(question: str, trace: bool = False) -> Optional[AnswerResponse]:
    started = time.perf_counter()
    try:
        response = answer(question)
    except Exception as exc:  # noqa: BLE001 - a crash is the thing being tested
        print(f"\n  pipeline raised {type(exc).__name__}: {exc}")
        return None
    _show(response, time.perf_counter() - started, trace)
    return response


def main(argv: Optional[list] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    settings = load()

    if not settings.groq_api_key:
        print(
            "GROQ_API_KEY is not set.\n"
            "  cp .env.example .env   then add your key to .env\n"
            "The key is read from .env and is never printed."
        )
        return 1

    print(f"model: {settings.groq_model}")
    print(f"floor: {settings.similarity_floor}   top_k: {settings.retrieval_top_k}")
    print(f"collection: {settings.chroma_collection}  (already ingested)")

    if args:
        question = " ".join(args)
        _once(question)
        return 0

    print("type a question, or /quit to exit. /trace shows retrieved chunk ids.\n")
    trace = False
    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not question:
            continue
        if question.lower() in COMMANDS:
            if question.lower() in ("/quit", "/exit", "/q"):
                return 0
            if question.lower() == "/trace":
                trace = not trace
                print(f"  retrieval trace {'on' if trace else 'off'}")
                continue
            print(__doc__)
            continue
        _once(question, trace)


if __name__ == "__main__":
    sys.exit(main())
