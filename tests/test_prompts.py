"""Phase 4 (K13/K14/K16/K17) — answer generation.

Everything here runs with no API key and no network, per architecture.md §13:
the prompt contract is tested against a scripted client, and the retrieval leg
runs against the real collection so the citation is checked against real chunk
metadata rather than a mock.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.config import ConfigError, load
from src.llm.groq_client import GroqClient, LLMUnavailable, ScriptedLLMClient
from src.query.freshness import FRESHNESS_PREFIX, FreshnessResolver, FreshnessUnavailable
from src.query.pipeline import AnswerError, AnswerResponse, answer
from src.rag.prompts import (
    FACTSHEET_REDIRECT,
    HARD_RULES,
    NOT_IN_SOURCES,
    SYSTEM_PROMPT,
    build_user_message,
    citation_for,
    is_marker,
)
from src.rag.retriever import Retriever
from src.ui.answer_view import (
    DISCLAIMER,
    count_links,
    freshness_line,
    render_answer,
    render_block,
    strip_urls,
)

ENV = Path(".env")


# ------------------------------------------------- K13: the prompt contract


def test_system_prompt_contains_all_seven_hard_rules():
    """architecture.md §8.5. A dropped rule is a silent constraint loss."""
    assert len(HARD_RULES) == 7
    for index, rule in enumerate(HARD_RULES, start=1):
        assert rule in SYSTEM_PROMPT, f"hard rule {index} missing from SYSTEM_PROMPT"


def test_system_prompt_contains_both_markers():
    assert NOT_IN_SOURCES in SYSTEM_PROMPT
    assert FACTSHEET_REDIRECT in SYSTEM_PROMPT


@pytest.mark.parametrize(
    "needle",
    [
        "only facts present in CONTEXT",
        "at most 3 sentences",
        "exactly one source link",
        "Never give advice",
        "Never state, compute, compare, or rank returns",
        "Never request or repeat personal identifiers",
        "Do not use outside knowledge",
    ],
)
def test_system_prompt_states_each_constraint(needle):
    assert needle in SYSTEM_PROMPT


def test_markers_are_bare_tokens():
    """P5 routes on an exact match, so the markers must not carry punctuation."""
    assert is_marker(NOT_IN_SOURCES, NOT_IN_SOURCES)
    assert is_marker(f"  {FACTSHEET_REDIRECT}\n", FACTSHEET_REDIRECT)
    assert not is_marker(f"I cannot answer. {NOT_IN_SOURCES}", NOT_IN_SOURCES)


# --------------------------------------------- K13: context and citation


def test_build_user_message_includes_every_chunk_url():
    chunks = [
        _chunk("a::0::0", "https://groww.in/a", scheme="Fund A", section="Fees", fact_type="expense_ratio"),
        _chunk("b::0::0", "https://groww.in/b", scheme="Fund B", section="Risk", fact_type="riskometer"),
    ]
    message = build_user_message(chunks, "What is the expense ratio of Fund A?")
    for chunk in chunks:
        assert chunk.source_url in message
    assert "Fund A" in message and "Fund B" in message
    assert "expense_ratio" in message and "riskometer" in message
    assert message.rstrip().endswith("What is the expense ratio of Fund A?")


def test_build_user_message_numbers_the_chunks():
    message = build_user_message([_chunk("a::0::0", "https://groww.in/a")], "q")
    assert "[1]" in message


def test_build_user_message_refuses_a_chunk_with_no_url():
    """A context chunk with no URL could not be cited, so refuse to build."""
    chunk = _chunk("a::0::0", "")
    with pytest.raises(ValueError, match="source_url"):
        build_user_message([chunk], "q")


def test_build_user_message_refuses_empty_context_and_question():
    with pytest.raises(ValueError):
        build_user_message([], "q")
    with pytest.raises(ValueError):
        build_user_message([_chunk("a::0::0", "https://groww.in/a")], "   ")


def test_citation_comes_from_chunk_metadata_not_model_text():
    """AD-3: the citation is the top chunk's URL, verbatim."""
    chunks = [
        _chunk("a::0::0", "https://groww.in/a"),
        _chunk("b::0::0", "https://groww.in/b"),
    ]
    assert citation_for(chunks) == "https://groww.in/a"


def test_citation_falls_back_when_the_top_chunk_lacks_a_url():
    chunks = [_chunk("a::0::0", ""), _chunk("b::0::0", "https://groww.in/b")]
    assert citation_for(chunks) == "https://groww.in/b"


def test_citation_of_nothing_is_none():
    assert citation_for([]) is None


def test_citation_is_always_a_registered_url():
    """C-1: the link that reaches the UI must be in the K1 allowlist."""
    from src.ingest.registry import SourceRegistry

    allowed = {spec.url for spec in SourceRegistry.from_file(load().sources_path).all_sources()}
    hits = Retriever().retrieve("What is the expense ratio of HDFC Large Cap Fund Direct Growth?", k=3)
    assert citation_for(hits) in allowed


# ------------------------------------------- K16: exactly one link (E-1)


def test_rendered_answer_has_exactly_one_link_even_when_the_model_echoes_one():
    """Rule 3 makes the model echo the URL; the renderer must not double it."""
    response = AnswerResponse(
        text="It is 1.03%. https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
        citation_url="https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
        last_updated=f"{FRESHNESS_PREFIX}28 Sep 2026",
    )
    block = render_answer(response)
    assert count_links(block) == 1


def test_a_hallucinated_url_cannot_reach_the_rendered_block():
    """AD-3: the displayed link is derived from metadata, not model text."""
    response = AnswerResponse(
        text="It is 1.03%. https://evil.example.com/not-a-real-fund",
        citation_url="https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
        last_updated=f"{FRESHNESS_PREFIX}28 Sep 2026",
    )
    block = render_answer(response)
    assert "evil.example.com" not in block
    assert count_links(block) == 1


def test_strip_urls_leaves_prose_intact():
    assert strip_urls("It is 1.03%. https://x.in/a") == "It is 1.03%."


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The AUM is INR 39,933 Cr.\nSource: https://x.in/a", "The AUM is INR 39,933 Cr."),
        ("The AUM is INR 39,933 Cr. Source: https://x.in/a", "The AUM is INR 39,933 Cr."),
        ("The AUM is INR 39,933 Cr.\n**Source:** https://x.in/a", "The AUM is INR 39,933 Cr."),
        ("**Source:** https://x.in/a", ""),
        ("The AUM is INR 39,933 Cr.\n[Source](https://x.in/a)", "The AUM is INR 39,933 Cr."),
    ],
)
def test_strip_urls_takes_the_label_with_the_link(text, expected):
    """Hard rule 3 only asks for the link, and models often label it themselves.

    Removing the URL while leaving "Source:" behind rendered as
    "Source: Source: <url>", because the renderer appends its own line from chunk
    metadata (AD-3). E-1 counts URLs rather than labels, so the duplicate passed
    every check while reading as a doubled citation to the user.
    """
    assert strip_urls(text) == expected


def test_a_rendered_answer_carries_one_label_and_one_link():
    """The regression that E-1 could not see, at the rendering boundary."""
    response = AnswerResponse(
        text="The AUM is INR 39,933 Cr.\nSource: https://groww.in/mutual-funds/x",
        citation_url="https://groww.in/mutual-funds/x",
        last_updated=f"{FRESHNESS_PREFIX}28 Sep 2026",
    )
    block = render_answer(response)
    assert block.count("Source:") == 1
    assert count_links(block) == 1


def test_render_block_carries_the_disclaimer():
    response = AnswerResponse("x", "https://groww.in/a", f"{FRESHNESS_PREFIX}d")
    assert DISCLAIMER in render_block(response)
    assert DISCLAIMER == "Facts-only. No investment advice."


def test_marker_responses_render_without_a_citation():
    for marker in (NOT_IN_SOURCES, FACTSHEET_REDIRECT):
        response = AnswerResponse(marker, None, f"{FRESHNESS_PREFIX}d", marker=marker)
        assert count_links(render_answer(response)) == 0


def test_freshness_line_prefix_is_added_once():
    assert freshness_line("28 Sep 2026") == f"{FRESHNESS_PREFIX}28 Sep 2026"
    once = freshness_line(f"{FRESHNESS_PREFIX}28 Sep 2026")
    assert freshness_line(once) == once


# --------------------------------------- K17: the FR-14 freshness string


def test_freshness_renders_the_mandated_prefix():
    value = FreshnessResolver().resolve()
    assert value.render().startswith(FRESHNESS_PREFIX)
    assert FRESHNESS_PREFIX == "Last updated from sources: "


def test_freshness_reads_the_corpus_manifest():
    """FR-14 / G7: the value must come from corpus/sources.csv fetched_at."""
    value = FreshnessResolver().resolve()
    assert value.source_dates, "expected per-source fetched_at values"
    assert re.search(r"\d{2} \w{3} \d{4}", value.value)


def test_freshness_uses_the_oldest_source_not_the_newest():
    """An answer reflects the stalest source in the corpus, not the freshest."""
    resolver = FreshnessResolver()
    value = resolver.resolve()
    assert resolver.strategy == "ingestion_timestamp"
    moments = sorted(value.source_dates.values())
    assert value.value == "28 Sep 2026"
    assert moments[0].startswith("2026-09-28")


def test_freshness_page_date_falls_back_rather_than_inventing():
    """Q4: the alternative strategy must degrade, not fabricate a date."""
    value = FreshnessResolver(strategy="page_date").resolve([])
    assert "page_date" in value.strategy
    assert re.search(r"\d{2} \w{3} \d{4}", value.value)


def test_freshness_rejects_an_unknown_strategy():
    with pytest.raises(ValueError, match="unknown freshness strategy"):
        FreshnessResolver(strategy="invented")


def test_freshness_raises_when_the_manifest_is_missing(tmp_path):
    settings = load()
    from dataclasses import replace

    broken = replace(settings, corpus_dir=tmp_path)
    with pytest.raises(FreshnessUnavailable, match="sources.csv"):
        FreshnessResolver(settings=broken).resolve()


# -------------------------------------------- K14: the client contract


def test_missing_api_key_raises_a_typed_setup_error():
    """Architecture §10: clear setup message referencing .env, no traceback."""
    from dataclasses import replace

    settings = replace(load(), groq_api_key=None, groq_model="llama-3.3-70b-versatile")
    with pytest.raises(LLMUnavailable) as excinfo:
        GroqClient(settings)
    assert "GROQ_API_KEY" in str(excinfo.value)
    assert ".env" in str(excinfo.value)


def test_missing_model_raises_a_typed_setup_error():
    from dataclasses import replace

    settings = replace(load(), groq_api_key="k", groq_model=None)
    with pytest.raises(LLMUnavailable, match="GROQ_MODEL"):
        GroqClient(settings)


def test_temperature_is_the_lowest_supported():
    assert load().groq_temperature == 0.0


def test_scripted_client_returns_responses_in_order():
    client = ScriptedLLMClient(["one", "two"])
    assert client.chat([{"role": "user", "content": "a"}]) == "one"
    assert client.chat([{"role": "user", "content": "b"}]) == "two"
    assert client.call_count == 2


def test_scripted_client_raises_when_exhausted():
    with pytest.raises(LLMUnavailable):
        ScriptedLLMClient([]).chat([{"role": "user", "content": "a"}])


def test_groq_api_key_is_never_in_the_error_message():
    """TC-4 / NFR-2: a key must not reach a log or a traceback."""
    from src.llm.groq_client import _redact

    secret = "gsk_test_secret_value_1234"

    # An SDK exception can echo the key back in its own message; the client must
    # scrub it before the text reaches a log or the UI.
    assert secret not in _redact(f"auth failed for key {secret}", secret)
    assert "<redacted>" in _redact(f"auth failed for key {secret}", secret)
    assert _redact("nothing to redact", secret) == "nothing to redact"


def test_a_real_key_shaped_string_is_redacted_from_a_raised_error():
    """The end-to-end version: a failing call must not surface the key."""
    from dataclasses import replace

    from src.llm.groq_client import _redact

    secret = "gsk_not_a_real_key_abcdefghijklmnop"
    settings = replace(load(), groq_api_key=secret, groq_model="llama-3.3-70b-versatile")
    client = GroqClient.__new__(GroqClient)  # bypass __init__, which would need a live SDK client
    client.settings = settings
    client._client = _RaisingSDK(secret)
    with pytest.raises(LLMUnavailable) as excinfo:
        client.chat([{"role": "user", "content": "q"}])
    assert secret not in str(excinfo.value)
    assert "<redacted>" in str(excinfo.value)


# --------------------- integration: real retrieval, scripted LLM (no key)


@pytest.fixture(scope="module")
def retriever():
    return Retriever()


def test_pipeline_returns_a_citation_from_the_retrieved_set(retriever):
    """architecture.md §13 integration level: the URL is in the retrieved set."""
    llm = ScriptedLLMClient(["The expense ratio is 1.03%."])
    response = answer(
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
        retriever=retriever,
        llm=llm,
    )
    retrieved = retriever.store.get_chunks(response.retrieved_chunk_ids)
    urls = {row["metadata"]["source_url"] for row in retrieved.values()}
    assert response.citation_url in urls
    assert count_links(render_answer(response)) == 1


def test_pipeline_passes_the_system_prompt_and_context(retriever):
    llm = ScriptedLLMClient(["ok"])
    answer("What is the minimum SIP amount for HDFC ELSS Tax Saver Fund?", retriever=retriever, llm=llm)
    messages = llm.last_messages
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == SYSTEM_PROMPT
    assert "MINIMUM SIP" in messages[1]["content"].upper()
    assert "groww.in" in messages[1]["content"]


def test_pipeline_never_cites_a_hallucinated_url(retriever):
    llm = ScriptedLLMClient(["It is 1.03%. https://totally-made-up.example.com/x"])
    response = answer(
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
        retriever=retriever,
        llm=llm,
    )
    assert response.citation_url.startswith("https://groww.in/")
    assert "made-up" not in render_answer(response)


@pytest.mark.parametrize("marker", [NOT_IN_SOURCES, FACTSHEET_REDIRECT])
def test_markers_pass_through_without_a_citation(retriever, marker):
    """A marker routes to its handler; it is never shown as a bare token.

    Phase 5 changes, both intended:

    * The question must pass K12 to reach the LLM at all. In Phase 4 an
      unanswerable question still reached the model; now the gate declines it
      first, which is the whole purpose of step 4.
    * The marker itself is no longer the response text. The pipeline converts
      it to the user-facing message from ``guardrails.messages`` (NFR-4: every
      non-answer path returns a readable message), while ``response.marker``
      still records which marker fired.
    """
    question = "What is the expense ratio of HDFC Large Cap Fund Direct Growth?"
    llm = ScriptedLLMClient([marker])
    response = answer(question, retriever=retriever, llm=llm)
    assert response.marker == marker
    assert response.text != marker, "the bare wire format must not reach the user"
    assert response.is_refusal
    # A marker means there is no grounded fact to attribute, so there is no
    # citation. A refusal may still carry the link its own requirement demands
    # (FR-11 for a performance redirect, FR-10 for a decline), which is not a
    # citation and is not rendered as "Source:".
    assert response.citation_url is None
    rendered = render_answer(response)
    assert "Source:" not in rendered
    assert count_links(rendered) == (1 if response.link else 0)


def test_the_gate_declines_before_any_llm_call(retriever):
    """K12: an unsupported question must not reach the model at all.

    This is the property the Phase 4 test above could not express. The question
    names no in-corpus attribute, so the fact-type condition fails, and a model
    that *would* have answered is never consulted.
    """
    llm = ScriptedLLMClient(["HDFC Large Cap's NAV is 412.55."])
    response = answer("What is the current NAV of HDFC Large Cap Fund?", retriever=retriever, llm=llm)
    assert response.path in ("gate_miss", "performance")
    assert llm.call_count == 0, "no LLM call may happen once the gate declines"


def test_a_fact_type_puts_the_real_fact_chunk_in_the_context(retriever):
    """Regression: plain retrieve() cannot answer a benchmark question.

    Measured on the persisted corpus: for this question the top three plain hits
    are all ``general`` blobs and the chunk holding the answer,
    ``Benchmark: NIFTY 500 TRI``, ranks 24th. Given that context the model
    replies NOT_IN_SOURCES, which is faithful but useless. The K11 fact_type
    filter is what puts the real fact first.
    """
    question = "What benchmark does HDFC Equity Fund Direct Growth track?"
    plain = retriever.retrieve(question, k=3)
    assert all(hit.fact_type == "general" for hit in plain), "premise: plain hits are not facts"
    assert all(hit.chunk_id != "hdfc_equity_flexi_cap::0::5" for hit in plain)

    llm = ScriptedLLMClient(["stub"])
    answer(question, retriever=retriever, llm=llm, fact_type="benchmark")
    context = llm.last_messages[1]["content"]
    assert "NIFTY 500" in context, "the real benchmark fact must reach the model"
    assert "fact_type: benchmark" in context


def test_fact_typed_context_produces_a_cited_answer(retriever):
    response = answer(
        "What benchmark does HDFC Equity Fund Direct Growth track?",
        retriever=retriever,
        llm=ScriptedLLMClient(["It tracks the NIFTY 500 Total Return Index."]),
        fact_type="benchmark",
    )
    assert response.citation_url == "https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth"
    assert count_links(render_answer(response)) == 1


def test_an_unknown_fact_type_falls_back_instead_of_failing(retriever):
    """Decline path intact: no fact_type match must not yield an empty context.

    HDFC Small Cap has no ``lock_in`` chunk — only the ELSS fund has a lock-in,
    so the fact-typed query falls back to plain retrieval and the retrieved
    context is the header/general text, which contains no lock-in figure.

    K12 then declines on ``wrong_fact_type`` without calling the LLM. That is the
    correct outcome: before K12 existed the fallback context went to the model,
    which would have had to say "there is no lock-in" from a chunk that never
    says so. The gate makes the corpus's silence explicit instead of asking the
    model to infer it.
    """
    llm = ScriptedLLMClient(["There is no lock-in."])
    response = answer(
        "What is the lock-in period for HDFC Small Cap Fund?",
        retriever=retriever,
        llm=llm,
        fact_type="lock_in",
    )
    assert response.retrieved_chunk_ids, "expected the fallback to return context"
    assert response.path == "gate_miss"
    assert response.gate_reason == "wrong_fact_type"
    assert llm.call_count == 0, "K12 must run before the LLM"


def test_pipeline_rejects_an_empty_question(retriever):
    with pytest.raises(AnswerError):
        answer("   ", retriever=retriever, llm=ScriptedLLMClient(["x"]))


def test_llm_failure_propagates_as_llm_unavailable(retriever):
    """Architecture §10: a model failure must not render as a partial answer.

    Phase 5 change: the pipeline no longer *propagates* the error. NFR-4 and
    architecture.md §10 require a readable message with no traceback, so
    ``LLMUnavailable`` is caught at the pipeline boundary and becomes the safe
    error state. The previous Phase 4 behaviour (letting it raise) is what the
    Phase 5 spec explicitly replaces.
    """
    response = answer(
        "What is the exit load on HDFC Balanced Advantage Fund Direct Growth?",
        retriever=retriever,
        llm=ScriptedLLMClient(error=LLMUnavailable("rate limited")),
    )
    assert response.path == "llm_error"
    assert "rate limited" not in response.text, "exception text must not reach the user"
    assert response.last_updated, "FR-14 applies to the error state too"
    assert count_links(render_answer(response)) == 0


def test_answer_response_reports_the_top_similarity(retriever):
    llm = ScriptedLLMClient(["1.03%"])
    response = answer(
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
        retriever=retriever,
        llm=llm,
    )
    assert response.top_similarity > retriever.similarity_floor


# --------------------------------------- config and secret hygiene (TC-4)


def test_max_tokens_is_configured():
    assert load().groq_max_tokens > 0


def test_env_example_ships_the_groq_keys_without_a_key():
    text = Path(".env.example").read_text()
    assert re.search(r"^GROQ_API_KEY=\s*$", text, flags=re.M), "key must ship empty (NFR-2)"
    for name in ("GROQ_MODEL", "GROQ_MAX_TOKENS", "GROQ_TEMPERATURE"):
        assert re.search(rf"^{name}=.+", text, flags=re.M), f"{name} must ship with a value"


def test_env_is_gitignored():
    import subprocess

    result = subprocess.run(
        ["git", "check-ignore", "-q", ".env"], capture_output=True
    )
    assert result.returncode == 0, ".env must be gitignored (TC-4)"


def test_no_tracked_file_contains_a_groq_key():
    """Security level, architecture.md §13: assert no key appears in tracked files.

    Matches a real key shape: ``gsk_`` followed by 20 or more characters. A bare
    ``gsk_`` is skipped so the spec's own audit command, which quotes the prefix
    as a search pattern, does not trip the check.
    """
    import re as _re

    import subprocess

    key_pattern = _re.compile(r"gsk_[A-Za-z0-9]{20,}")
    tracked = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout.split()
    for path in tracked:
        if path.endswith((".md", ".py", ".yaml", ".txt", ".example")):
            text = Path(path).read_text(errors="ignore")
            assert not key_pattern.search(text), f"possible API key committed in {path}"


class _RaisingSDK:
    """Minimal stand-in for the Groq SDK surface used by :class:`GroqClient`."""

    def __init__(self, secret: str) -> None:
        self._secret = secret
        self.chat = self
        self.completions = self

    def create(self, **_: object):
        raise RuntimeError(f"401 invalid api key {self._secret}")


# ------------------------------------------------------------- helper


def _chunk(chunk_id: str, url: str, scheme: str = "S", section: str = "Sec", fact_type: str = "general"):
    from src.rag.retriever import RetrievedChunk

    return RetrievedChunk(
        chunk_id=chunk_id,
        text="some text",
        metadata={"source_url": url, "scheme": scheme, "section": section, "fact_type": fact_type},
        similarity=0.5,
    )
