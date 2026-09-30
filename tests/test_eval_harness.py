"""Tests for K19, the eval harness (Phase 7).

The harness's own value depends on three things this file pins: it fails when a
check fails, it does not pass a check that never ran, and it reports a raising
check as a failure rather than aborting the run. If a broken harness can return
green, everything downstream — `eval.py` in CI, the claim in README.md that E-1
through E-9 are green — is worthless.

The live rows are exercised here against the real pipeline but with the model
call stubbed where the assertion is about the harness rather than the model.
Nothing here calls Groq, so the suite stays hermetic and fast.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

import pytest

from dataclasses import replace

from src.eval import harness
from src.eval.harness import CHECKS, CheckResult, EvalReport, run


def _result(check_id: str, passed: bool = True, **kwargs) -> CheckResult:
    return CheckResult(check_id, "a check", passed, **kwargs)


# -- the report ----------------------------------------------------------------


def test_a_failing_row_makes_the_report_fail() -> None:
    report = EvalReport([_result("E-1"), _result("E-2", passed=False, failures=["too long"])])
    assert not report.passed
    assert [result.check_id for result in report.failures()] == ["E-2"]


def test_an_empty_report_is_not_green() -> None:
    """`all([])` is True, so an empty run would otherwise report success.

    This is a live failure mode, not a theoretical one: `eval.py --only E-99`
    produces no rows, and without this the CLI would print nothing and exit 0.
    The CLI guards it separately, but the invariant belongs on the report too.
    """
    assert not EvalReport().passed
    assert not EvalReport([]).passed


def test_json_output_is_parseable_and_carries_the_verdict() -> None:
    import json

    report = EvalReport([_result("E-1", live=True), _result("E-8", live=False)])
    payload = json.loads(report.to_json())

    assert payload["passed"] is True
    assert payload["total"] == 2
    assert payload["green"] == 2
    assert [check["id"] for check in payload["checks"]] == ["E-1", "E-8"]
    assert [check["live"] for check in payload["checks"]] == [True, False]


def test_json_output_reports_failures_with_their_reasons() -> None:
    """A CI job reading --json needs to know *what* broke, not just that
    something did."""
    import json

    report = EvalReport([_result("E-1", passed=False, failures=["2 links", "no citation"])])
    payload = json.loads(report.to_json())

    assert payload["passed"] is False
    assert payload["checks"][0]["failures"] == ["2 links", "no citation"]


def test_render_labels_live_and_offline_rows() -> None:
    """A green table must not be mistaken for a hermetic test run."""
    table = EvalReport([_result("E-1", live=True), _result("E-8")]).render()
    assert "(live)" in table
    assert "(offline)" in table
    assert "2/2 checks passed" in table


# -- the check registry --------------------------------------------------------


def test_all_nine_checks_are_registered_with_distinct_ids() -> None:
    ids = [check_id for _, check_id, _ in CHECKS]
    assert ids == [f"E-{n}" for n in range(1, 10)], f"unexpected check ids: {ids}"
    assert len(set(ids)) == 9


def test_every_check_has_a_human_readable_name_and_docstring() -> None:
    """The table is the deliverable. A row with no name is unreadable."""
    for check, check_id, _ in CHECKS:
        assert check.__doc__, f"{check_id} has no docstring to name the row from"
        assert harness._row_name(check), f"{check_id} has no row name"


def test_the_offline_rows_are_e8_and_e9() -> None:
    """Pinned because `run()` decides the live flag by id membership."""
    assert harness._OFFLINE_IDS == frozenset({"E-8", "E-9"})


# -- orchestration -------------------------------------------------------------


def test_a_check_that_raises_is_reported_as_a_failed_row(monkeypatch) -> None:
    """The point of the harness is to tell you the state of all nine checks.

    Letting an exception escape would lose the other eight, which is exactly the
    information a failure investigation needs.
    """

    def boom(_settings=None):
        raise RuntimeError("store is unreachable")

    monkeypatch.setattr(harness, "CHECKS", ((boom, "E-8", True),))
    report = run()

    assert not report.passed
    assert report.results[0].check_id == "E-8"
    assert "RuntimeError" in report.results[0].detail
    assert "store is unreachable" in report.results[0].detail


def test_only_filters_by_check_id(monkeypatch) -> None:
    monkeypatch.setattr(
        harness,
        "CHECKS",
        (
            (_recorded, "E-1", False),
            (_recorded, "E-8", True),
        ),
    )
    report = run(only=["e-8"])

    assert [result.check_id for result in report.results] == ["E-8"]


def test_only_with_no_match_yields_an_empty_report(monkeypatch) -> None:
    """`eval.py` turns this into exit code 2 and a message, rather than
    reporting a vacuous pass."""
    monkeypatch.setattr(harness, "CHECKS", ((_recorded, "E-1", False),))
    assert run(only=["E-7"]).results == []


def test_run_passes_settings_only_to_the_offline_rows(monkeypatch) -> None:
    """The live rows take no arguments; handing them a Settings would raise, and
    the resulting error row would be indistinguishable from a product failure.
    """
    seen: List[str] = []

    def wants_nothing() -> CheckResult:
        seen.append("live")
        return _result("E-1")

    def wants_settings(settings) -> CheckResult:
        seen.append("offline")
        assert settings is not None
        return _result("E-8")

    monkeypatch.setattr(
        harness, "CHECKS", ((wants_nothing, "E-1", False), (wants_settings, "E-8", True))
    )
    run(settings=harness.load())

    assert seen == ["live", "offline"]


def _recorded() -> CheckResult:
    return _result("E-1")


# -- the offline rows, against the real artifacts ------------------------------


def test_e9_passes_against_the_persisted_dump() -> None:
    result = harness.check_e9_chunks_dump()
    assert result.passed, f"E-9 failed on the real chunks.txt: {list(result.failures)}"
    assert "chunks" in result.detail


def test_e8_passes_against_the_persisted_store() -> None:
    """Reads the store and the dump; it never re-embeds, so it is safe to run
    as part of the normal suite."""
    result = harness.check_e8_ingestion_discipline()
    assert result.passed, f"E-8 failed on the real store: {list(result.failures)}"


def test_e9_fails_when_a_required_field_is_missing(tmp_path) -> None:
    """Proves the row inspects content rather than merely checking the file
    exists — a dump missing `fact_type` would still look like a deliverable."""
    incomplete = tmp_path / "chunks.txt"
    incomplete.write_text(
        "=== CHUNK demo::0::0 ===\n"
        "source_url: https://groww.in/x\n"
        "scheme: Demo\n"
        "# missing fact_type and section\n",
        encoding="utf-8",
    )

    result = harness.check_e9_chunks_dump(replace(harness.load(), chunks_txt_path=incomplete))

    assert not result.passed
    assert any("fact_type" in failure for failure in result.failures)
    assert any("section" in failure for failure in result.failures)


def test_e9_fails_when_the_dump_is_absent(tmp_path) -> None:
    settings = replace(harness.load(), chunks_txt_path=tmp_path / "nope.txt")
    assert not harness.check_e9_chunks_dump(settings).passed


def test_e8_fails_when_the_counts_disagree(tmp_path) -> None:
    """A store with more chunks than the dump describes is exactly the
    double-ingest bug E-8 exists to catch."""
    dump = tmp_path / "chunks.txt"
    dump.write_text("=== CHUNK demo::0::0 ===\n", encoding="utf-8")
    settings = replace(harness.load(), chunks_txt_path=dump)

    result = harness.check_e8_ingestion_discipline(settings)

    assert not result.passed
    assert any("chunks.txt lists 1" in failure for failure in result.failures)


# -- E-5 and E-6 helpers -------------------------------------------------------


def test_acceptable_non_answers_cover_every_decline_path() -> None:
    """Guards against a new decline path being added and then reported as a
    groundedness failure, which would make E-6 fail for the wrong reason."""
    assert {"decline", "gate_miss", "performance", "validator_decline"} <= (
        harness._ACCEPTABLE_NON_ANSWERS
    )


def test_an_answer_path_is_not_acceptable_for_an_unsupported_question() -> None:
    """The whole point of the allowlist. If 'answer' ever ended up in it, E-6
    would stop testing anything at all."""
    assert "answer" not in harness._ACCEPTABLE_NON_ANSWERS
    assert "pii" not in harness._ACCEPTABLE_NON_ANSWERS


def test_lexicon_hits_reuses_the_production_matcher() -> None:
    """E-3 and E-4 must measure the matcher the pipeline enforces. If this
    function ever grows its own regex, the guarantee is gone."""
    from src.guardrails import validator

    hits = harness._lexicon_hits("You should buy this fund today.", validator.ADVICE_LEXICON)
    assert hits, "the production matcher should catch an obvious advice phrase"

    direct = validator._lexicon_hits("You should buy this fund today.", validator.ADVICE_LEXICON)
    assert hits == direct
