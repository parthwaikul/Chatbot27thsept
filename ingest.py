"""Ingestion CLI.

Phases 1 and 2. ``--stage all`` runs load -> inspect -> chunk -> embed -> store.
Use ``--stage chunk`` to regenerate ``chunks.txt`` without touching the vector
store, and ``--force`` to re-embed even when every ``content_hash`` is unchanged.
"""

from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Optional, Sequence

from src.config import ConfigError, load
from src.ingest.inspector import build_report, render_fact_samples, render_report
from src.ingest.loader import ExtractionStats, SourceLoadError, load_all, read_records_cache
from src.ingest.pipeline import run_pipeline
from src.ingest.registry import RegistryError, SourceRegistry
from src.rag.embeddings import EmbeddingError, FakeEmbeddingService
from src.rag.vector_store import VectorStoreError

STAGES = ("load", "inspect", "chunk", "store", "all")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mutual fund FAQ RAG — ingestion")
    parser.add_argument(
        "--stage",
        choices=STAGES,
        default="all",
        help=(
            "load = fetch and store raw text; inspect = report on loaded data; "
            "chunk = write chunks.txt/chunks.jsonl; store = embed and upsert into "
            "ChromaDB; all = everything"
        ),
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
    parser.add_argument(
        "--fake-embeddings",
        action="store_true",
        help="use the deterministic offline embedder instead of MiniLM (testing)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-chunk and re-embed every source even if content_hash is unchanged",
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
    records: List = []

    if args.stage in ("load", "all"):
        print(f"registry: {registry_path} ({len(registry)} sources)")
        print("loading…")
        report, stats_rows = load_all(registry, settings, only=only)
        stats_by_id = {spec.source_id: stats for spec, stats in stats_rows}
        records = list(report.records)
        print("")
        if report.failures:
            print(f"failures: {len(report.failures)}")
            for source_id, message in report.failures.items():
                print(f"  {source_id}: {message}")
            exit_code = 1
        print(f"wrote {settings.sources_csv_path}")
        print(f"wrote {settings.corpus_dir / 'records.json'}")
        print(f"wrote raw text under {settings.corpus_dir / 'raw'}")

    if args.stage in ("inspect", "chunk", "store", "all"):
        if not records:
            cache_path = settings.corpus_dir / "records.json"
            try:
                records = list(read_records_cache(cache_path))
            except SourceLoadError as exc:
                print(f"{exc}", file=sys.stderr)
                return 2
        if only:
            records = [r for r in records if r.source_id in set(only)]
        if not records:
            print("no records available; run --stage load first", file=sys.stderr)
            return 1

    if args.stage in ("inspect", "all"):
        report = build_report(records, {spec.source_id: spec for spec in registry}, stats_by_id)
        print(render_report(report, records))
        print(render_fact_samples(records))
        if not report.fully_ok:
            exit_code = 1

    if args.stage in ("chunk", "store", "all"):
        service = (
            FakeEmbeddingService(dim=settings.embedding_dim)
            if args.fake_embeddings
            else None
        )
        try:
            pipeline_report = run_pipeline(
                records,
                settings=settings,
                embeddings=service,
                force=args.force,
                write_files=True,
                embed=args.stage not in ("chunk",),
            )
        except (ConfigError, EmbeddingError, VectorStoreError, ValueError) as exc:
            print(f"pipeline error: {exc}", file=sys.stderr)
            return 2
        if args.stage == "chunk":
            print(f"wrote {settings.chunks_txt_path} ({pipeline_report.chunks} chunks)")
            print(f"wrote {settings.chunks_jsonl_path}")
            if pipeline_report.embedded < pipeline_report.chunks:
                print(
                    f"  note: {pipeline_report.chunks - pipeline_report.embedded} chunk(s) "
                    "have no vector because the store is empty; run --stage all for a "
                    "complete dump"
                )
            print("embed and store skipped (--stage chunk)")
        else:
            print("")
            print(pipeline_report.line())
            if pipeline_report.ingested:
                print(f"  ingested : {', '.join(pipeline_report.ingested)}")
            if pipeline_report.rewritten:
                print(f"  rewritten: {', '.join(pipeline_report.rewritten)}")
            if pipeline_report.unchanged:
                print(f"  unchanged: {', '.join(pipeline_report.unchanged)}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
