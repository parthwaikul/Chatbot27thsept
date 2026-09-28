"""K5 — the human-auditable chunk dump.

Writes ``chunks.txt`` (deliverable D-7): one ``=== CHUNK <chunk_id> ===`` header,
the full metadata block, then the text. A reviewer must be able to confirm by
hand that no expense-ratio row and no exit-load slab is cut in half, so the file
is plain text with no wrapping and no elision. ``chunks.jsonl`` carries the same
data for tests and for P3.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Sequence

from src.ingest.models import Chunk

METADATA_ORDER: Sequence[str] = (
    "source_url",
    "scheme",
    "category",
    "plan_variant",
    "section",
    "fact_type",
    "authority",
    "is_atomic",
    "char_len",
    "source_fetched_at",
    "content_hash",
)


def render_chunks(chunks: Sequence[Chunk]) -> str:
    """Render every chunk as the ``chunks.txt`` body."""
    blocks: List[str] = []
    for chunk in chunks:
        metadata = chunk.metadata
        lines = [f"=== CHUNK {chunk.chunk_id} ==="]
        for key in METADATA_ORDER:
            if key in metadata:
                lines.append(f"{key}: {metadata[key]}")
        for key in sorted(set(metadata) - set(METADATA_ORDER)):
            lines.append(f"{key}: {metadata[key]}")
        lines.append("---")
        lines.append(chunk.text)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def write_chunks(
    chunks: Sequence[Chunk], txt_path: Path, jsonl_path: Path
) -> dict:
    """Write ``chunks.txt`` and ``chunks.jsonl``. Both are regenerated each run."""
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text(render_chunks(chunks), encoding="utf-8")

    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(
                json.dumps(
                    {"chunk_id": chunk.chunk_id, "text": chunk.text, **dict(chunk.metadata)},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
    return {"chunks_txt": str(txt_path), "chunks_jsonl": str(jsonl_path), "count": len(chunks)}


def read_chunks_jsonl(path: Path) -> List[dict]:
    """Read ``chunks.jsonl`` back, for tests and for the P3 pipeline."""
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]
