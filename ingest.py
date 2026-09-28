"""Ingestion CLI.

Phase 1 exposes load and inspect only. ``--stage all`` will grow to include
chunk, embed and store once ``CHUNKING.md`` exists (TC-2 gate).
"""

from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Optional, Sequence

from src.config import ConfigError, load
from src.ingest.inspector import build_report, render_fact_samples, render_report
from src.ingest.loader import ExtractionStats, SourceLoadError, load_all, read_records_cache
from src.ingest.registry import RegistryError, SourceRegistry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mutual fund FAQ RAG — ingestion")
    parser.add_argument(
        "--stage",
        choices=("load", "inspect", "all"),
        default="all",
        help="load = fetch and store raw text; inspect = report on loaded data; all = both",
    )
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        metavar="SOURCE_ID",
        help="limit to specific source_id (repeatable)",
    )
    parser.add_argument(
        "--registry",
        type=str,
        default=None,
        help="path to sources.yaml (defaults to SOURCES_YAML from the environment)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        settings = load()
        registry_path = args.registry or settings.sources_path
        registry = SourceRegistry.from_file(registry_path)
    except (ConfigError, RegistryError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    only: Optional[List[str]] = args.sources
    stats_by_id: Dict[str, ExtractionStats] = {}
    exit_code = 0

    if args.stage in ("load", "all"):
        print(f"registry: {registry_path} ({len(registry)} sources)")
        print("loading…")
        report, stats_rows = load_all(registry, settings, only=only)
        stats_by_id = {spec.source_id: stats for spec, stats in stats_rows}
        print("")
        if report.failures:
            print(f"failures: {len(report.failures)}")
            for source_id, message in report.failures.items():
                print(f"  {source_id}: {message}")
            exit_code = 1
        print(f"wrote {settings.sources_csv_path}")
        print(f"wrote {settings.corpus_dir / 'records.json'}")
        print(f"wrote raw text under {settings.corpus_dir / 'raw'}")

    if args.stage in ("inspect", "all"):
        cache_path = settings.corpus_dir / "records.json"
        try:
            records = read_records_cache(cache_path)
        except SourceLoadError as exc:
            print(f"{exc}", file=sys.stderr)
            return 2
        if only:
            records = [record for record in records if record.source_id in set(only)]
        if not records:
            print("no records available to inspect", file=sys.stderr)
            return 1
        report = build_report(records, {spec.source_id: spec for spec in registry}, stats_by_id)
        print(render_report(report, records))
        print(render_fact_samples(records))
        if not report.fully_ok:
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
