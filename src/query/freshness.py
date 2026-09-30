"""K17 — the value behind "Last updated from sources: ".

FR-14 and G7 require the string on every answer; Q4 is what goes *inside* it.
This module keeps that decision in one place so answering Q4 later is a config
change rather than an edit to the renderer (architecture.md §8.7).

The default strategy is the ingestion timestamp, which is the only value this
corpus can actually evidence. A page-declared date would be a claim the scheme
page makes about itself, and the five registered pages do not consistently carry
one, so it is implemented but not the default.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Sequence

from src.config import Settings, load
from src.guardrails import messages as _messages

#: Rendered by the view layer, required verbatim by FR-14. The literal now
#: lives in ``guardrails.messages`` — Phase 5 made that the single home for
#: every user-facing string — and is re-exported here because the view and
#: tests have imported it from this module since Phase 4.
FRESHNESS_PREFIX = _messages.FRESHNESS_PREFIX


class FreshnessUnavailable(RuntimeError):
    """Raised when no freshness value can be derived from the corpus."""


def _parse(ts: str) -> Optional[datetime]:
    raw = (ts or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _format(moment: datetime) -> str:
    """A date a person can read. Timezone is dropped deliberately: the corpus
    timestamps are UTC fetch times, and showing '13:12 UTC' in a chat window
    implies a precision the sources do not warrant (FR-14 asks for a date)."""
    return moment.astimezone(timezone.utc).strftime("%d %b %Y")


@dataclass(frozen=True)
class FreshnessValue:
    """A resolved freshness string plus the strategy that produced it."""

    value: str
    strategy: str
    #: The raw per-source timestamps, kept for the log and for tests.
    source_dates: Dict[str, str]

    def render(self) -> str:
        return f"{FRESHNESS_PREFIX}{self.value}"


class FreshnessResolver:
    """Resolves the freshness string (K17).

    ``strategy`` is one of:

    * ``ingestion_timestamp`` — the newest ``fetched_at`` in
      ``corpus/sources.csv``. The default, and the only value the corpus
      evidences.
    * ``page_date`` — a date declared on the source page itself. Implemented so
      Q4 has a working alternative, but not the default: the registered pages do
      not carry a consistent self-declared date.

    **Q4 decision (prototype):** the default strategy is ``ingestion_timestamp``,
    the oldest ``fetched_at`` in ``corpus/sources.csv``. Rationale: it is the only
    value this corpus can evidence. A page-declared date would be the source
    asserting something about itself, and the five registered pages carry no
    consistent self-declared update date, so using one would mean trusting an
    unverified claim in the very string meant to convey trustworthiness.

    The oldest source is used rather than the newest because an answer reflects a
    corpus whose stalest member is that old; quoting the freshest timestamp would
    overstate how current the answer is (FR-14, NFR-8).

    TODO(P7): this paragraph is the authoritative record of the Q4 choice. P4 task
    3 asks for it in README known-limits, and ``README.md`` (D-3) is a P7
    deliverable, so the wording must be copied into README.md when that file is
    written.
    """

    def __init__(self, settings: Optional[Settings] = None, strategy: str = "ingestion_timestamp") -> None:
        self.settings = settings or load()
        if strategy not in ("ingestion_timestamp", "page_date"):
            raise ValueError(
                f"unknown freshness strategy {strategy!r}; expected "
                "'ingestion_timestamp' or 'page_date'"
            )
        self.strategy = strategy

    # -- strategies ---------------------------------------------------------

    def _from_ingestion(self) -> FreshnessValue:
        path = self.settings.corpus_dir / "sources.csv"
        if not path.exists():
            raise FreshnessUnavailable(
                f"{path} is missing, so no ingestion date is available. Run "
                "`python ingest.py --stage load` first (FR-14)."
            )
        dates: Dict[str, str] = {}
        newest: Optional[datetime] = None
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                moment = _parse(row.get("fetched_at", ""))
                if moment is None:
                    continue
                dates[row.get("source_id", "?")] = row.get("fetched_at", "")
                if newest is None or moment > newest:
                    newest = moment
        if newest is None:
            raise FreshnessUnavailable(
                f"no usable fetched_at in {path}; cannot state a freshness date (FR-14)"
            )
        # Oldest is the honest bound: the answer reflects a corpus whose stalest
        # source is this old, not its freshest.
        oldest = min(m for m in (_parse(v) for v in dates.values()) if m is not None)
        return FreshnessValue(
            value=_format(oldest), strategy=self.strategy, source_dates=dict(dates)
        )

    def _from_page_date(self, chunks: Optional[Sequence] = None) -> FreshnessValue:
        """Use a date the source page declares, if any chunk carries one.

        The scheme pages in this corpus do not carry a self-declared update date,
        so this normally falls through to the ingestion timestamp rather than
        inventing a page date.
        """
        declared: Optional[datetime] = None
        for chunk in chunks or ():
            raw = str(getattr(chunk, "metadata", {}).get("page_date", "") or "").strip()
            if raw:
                moment = _parse(raw)
                if moment and (declared is None or moment < declared):
                    declared = moment
        if declared is None:
            fallback = self._from_ingestion()
            return FreshnessValue(
                value=fallback.value,
                strategy=f"{self.strategy}->ingestion_timestamp",
                source_dates=fallback.source_dates,
            )
        return FreshnessValue(value=_format(declared), strategy="page_date", source_dates={})

    # -- public API ---------------------------------------------------------

    def resolve(self, chunks: Optional[Sequence] = None) -> FreshnessValue:
        """Resolve the freshness value for this answer."""
        if self.strategy == "page_date":
            return self._from_page_date(chunks)
        return self._from_ingestion()
