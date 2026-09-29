"""K7 — the persisted ChromaDB collection.

Writes are always ``upsert``, never ``add`` (architecture.md 6.3). That is what
makes re-ingestion a no-op: a chunk whose id already holds identical content is
overwritten with itself, and a chunk that changed is replaced in place, so the
collection count only moves when the corpus genuinely changed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.config import Settings, load
from src.ingest.models import Chunk

COLLECTION_METADATA: Dict[str, str] = {"hnsw:space": "cosine"}


class VectorStoreError(RuntimeError):
    """Raised when the store cannot be opened or written."""


class QueryHit(dict):
    """One search result: ``chunk_id``, ``text``, ``metadata``, ``similarity``."""


class VectorStore:
    """Thin, typed wrapper over a persistent ChromaDB collection."""

    def __init__(
        self,
        chroma_dir: Optional[Path] = None,
        collection: Optional[str] = None,
    ) -> None:
        settings = load()
        self.chroma_dir = Path(chroma_dir or settings.chroma_dir)
        self.collection_name = collection or settings.chroma_collection
        self.chroma_dir.mkdir(parents=True, exist_ok=True)

        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings
        except ImportError as exc:
            raise VectorStoreError(
                "chromadb is not installed. Run `pip install -r requirements.txt`."
            ) from exc

        try:
            self._client = chromadb.PersistentClient(
                path=str(self.chroma_dir), settings=ChromaSettings(anonymized_telemetry=False)
            )
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name, metadata=COLLECTION_METADATA
            )
        except Exception as exc:
            raise VectorStoreError(
                f"could not open ChromaDB at {self.chroma_dir}: {exc}"
            ) from exc

    # -- writes -------------------------------------------------------------

    def upsert_chunks(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> None:
        """Upsert chunks and their vectors. Never uses ``add``."""
        if len(chunks) != len(vectors):
            raise VectorStoreError(
                f"got {len(chunks)} chunks but {len(vectors)} vectors; they must match"
            )
        if not chunks:
            return
        self._collection.upsert(
            ids=[chunk.chunk_id for chunk in chunks],
            documents=[chunk.text for chunk in chunks],
            metadatas=[chunk.metadata_for_store() for chunk in chunks],
            embeddings=[list(vector) for vector in vectors],
        )

    def delete_ids(self, chunk_ids: Sequence[str]) -> None:
        """Remove chunk ids, used when a source no longer yields them."""
        if chunk_ids:
            self._collection.delete(ids=list(chunk_ids))

    # -- reads --------------------------------------------------------------

    def count(self) -> int:
        """Number of chunks in the collection."""
        return int(self._collection.count())

    def query(
        self, vector: Sequence[float], k: int = 5, where: Optional[Dict[str, Any]] = None
    ) -> List[QueryHit]:
        """Return the ``k`` nearest chunks, nearest first.

        ChromaDB returns cosine *distance* for an HNSW cosine index, so
        ``similarity = 1 - distance``.
        """
        if self.count() == 0:
            return []
        response = self._collection.query(
            query_embeddings=[list(vector)],
            n_results=max(1, int(k)),
            where=where or None,
            include=["documents", "metadatas", "distances"],
        )
        ids = (response.get("ids") or [[]])[0]
        documents = (response.get("documents") or [[]])[0]
        metadatas = (response.get("metadatas") or [[]])[0]
        distances = (response.get("distances") or [[]])[0]

        hits: List[QueryHit] = []
        for index, chunk_id in enumerate(ids):
            hits.append(
                QueryHit(
                    chunk_id=chunk_id,
                    text=documents[index] if index < len(documents) else "",
                    metadata=dict(metadatas[index] or {}) if index < len(metadatas) else {},
                    similarity=1.0 - float(distances[index]),
                )
            )
        return hits

    def all_chunk_ids(self) -> List[str]:
        """Every chunk id in the collection, for reconciliation."""
        return [str(chunk_id) for chunk_id in self._collection.get(include=[])["ids"]]

    def get_chunks(self, chunk_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        """Read stored chunks back by id, for audit and idempotency checks."""
        if not chunk_ids:
            return {}
        found = self._collection.get(ids=list(chunk_ids), include=["documents", "metadatas"])
        return {
            chunk_id: {
                "text": text,
                "metadata": dict(metadata or {}),
            }
            for chunk_id, text, metadata in zip(
                found["ids"], found.get("documents") or [], found.get("metadatas") or []
            )
        }

    def vectors_for(self, chunk_ids: Sequence[str]) -> Dict[str, List[float]]:
        """Read the stored vectors back by chunk id.

        Used to write the embedding of each chunk into ``chunks.txt`` from the
        vectors that are actually persisted, so the dump and the collection can
        never disagree.
        """
        if not chunk_ids:
            return {}
        found = self._collection.get(ids=list(chunk_ids), include=["embeddings"])
        embeddings = found.get("embeddings")
        if embeddings is None:
            return {}
        return {
            chunk_id: [float(value) for value in embedding]
            for chunk_id, embedding in zip(found["ids"], embeddings)
            if embedding is not None
        }
