"""K11 — retrieval.

Embeds the question with the same MiniLM service used at ingestion and returns
the nearest chunks with their cosine similarity. Two properties matter more than
the ranking itself:

* **Same model on both sides.** The collection holds all-MiniLM-L6-v2 vectors, so
  a query embedded by anything else would score against the wrong geometry. The
  default service is therefore the FP32 ONNX graph of that same model, which
  agrees with the PyTorch weights to float32 rounding without importing torch.
  One instance per process (architecture.md D6) is the caller's job, not this
  class's: ``src.ui.chat.runtime_retriever`` holds it behind
  ``st.cache_resource``, so a question does not rebuild the graph.
* **The score is trustworthy.** ``similarity`` is ``1 - distance`` from the cosine
  HNSW index, so the relevance gate in P4 can compare it against a floor chosen
  from a measured distribution rather than a guess.

No LLM call happens here. This phase answers "which chunks, and how strongly",
nothing more.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.config import Settings, load
from src.ingest.models import FACT_TYPES
from src.ingest.registry import SourceRegistry
from src.rag.embeddings import EmbeddingService, build_query_embedding_service
from src.rag.vector_store import VectorStore, VectorStoreError


class RetrieverError(RuntimeError):
    """Raised when the collection is missing or empty."""


@dataclass(frozen=True)
class RetrievedChunk:
    """One search result, with everything a citation or an answer needs."""

    chunk_id: str
    text: str
    metadata: Mapping[str, Any]
    similarity: float
    #: True when ``metadata['fact_type']`` equals the fact type that was asked
    #: for. ``retrieve_for_fact_type`` uses this so the caller can tell "the
    #: corpus has no chunk of this type" from "here is a chunk of this type".
    fact_type_match: bool = True

    @property
    def source_url(self) -> str:
        return str(self.metadata.get("source_url", ""))

    @property
    def scheme(self) -> str:
        return str(self.metadata.get("scheme", ""))

    @property
    def fact_type(self) -> str:
        return str(self.metadata.get("fact_type", "general"))


class Retriever:
    """Embeds a question and returns the nearest chunks from the collection."""

    def __init__(
        self,
        embeddings: Optional[EmbeddingService] = None,
        store: Optional[VectorStore] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self.settings = settings or load()
        self.embeddings = embeddings or build_query_embedding_service(self.settings)
        self._schemes: Optional[Dict[str, str]] = None
        self._stored: Optional[Dict[str, str]] = None
        try:
            self.store = store or VectorStore(
                chroma_dir=self.settings.chroma_dir,
                collection=self.settings.chroma_collection,
            )
        except VectorStoreError as exc:
            raise RetrieverError(str(exc)) from exc
        if self.store.count() == 0:
            raise RetrieverError(
                f"the collection {self.store.collection_name!r} is empty at "
                f"{self.settings.chroma_dir}. Run `python ingest.py --stage all` first; "
                "retrieval has nothing to search until ingestion has run."
            )

    # -- helpers ------------------------------------------------------------

    @property
    def top_k(self) -> int:
        """Default k, from the Q3 decision recorded in CHUNKING.md."""
        return int(self.settings.require("retrieval_top_k"))

    @property
    def similarity_floor(self) -> float:
        """Scores at or below this are not evidence, per the Q3 decision."""
        return float(self.settings.require("similarity_floor"))

    def above_floor(
        self, question: str, k: Optional[int] = None, floor: Optional[float] = None
    ) -> List[RetrievedChunk]:
        """Only the hits scoring strictly above the floor."""
        threshold = self.similarity_floor if floor is None else floor
        return [hit for hit in self.retrieve(question, k=k) if hit.similarity > threshold]

    # -- retrieval ----------------------------------------------------------

    # -- scheme scoping -----------------------------------------------------

    def registered_schemes(self) -> Dict[str, str]:
        """``{source_id: scheme}`` for the five registered S1–S5 schemes.

        Read from config/sources.yaml (K1), the same authority the citation
        check uses, rather than from collection metadata, so retrieval and
        citations agree on the allowed set even if metadata drifts.
        """
        if self._schemes is None:
            self._schemes = SourceRegistry.from_file(self.settings.sources_path).schemes()
        return self._schemes

    def _stored_schemes(self) -> Dict[str, str]:
        """``{source_id: scheme}`` using the value actually in chunk metadata.

        Keyed by the ``source_id`` prefix of the chunk id, which is the only
        place the id is recorded. The registry's ``scheme`` string is not safe to
        filter on: the registry says "HDFC ELSS Tax Saver Fund - Direct Plan -
        Growth" while the ingested metadata says "HDFC ELSS Tax Saver Fund -
        Direct Growth", so filtering on the registry text matches nothing.
        """
        if self._stored is None:
            self._stored = {}
            for chunk_id in self.store.all_chunk_ids():
                source_id = chunk_id.split("::", 1)[0]
                if source_id in self._stored:
                    continue
                chunk = self.store.get_chunks([chunk_id])[chunk_id]
                scheme = chunk["metadata"].get("scheme")
                if scheme:
                    self._stored[source_id] = str(scheme)
        return self._stored

    def mentioned_source_ids(self, question: str) -> List[str]:
        """Source ids whose scheme the question names.

        A question about one scheme should not be answered with another's facts.
        Short fact chunks are the problem case: "Benchmark: NIFTY 500 TRI" carries
        no scheme name, so ranking benchmark chunks by embedding similarity alone
        returns HDFC Balanced Advantage's benchmark for a question about HDFC
        Equity Fund. Detecting the scheme in the *question* and using it to
        narrow the Chroma filter is what makes those answers correct.

        Returns every match, not just the first, so a comparative question can
        still be answered. Short names are ignored to avoid matching on stray
        words like "fund".
        """
        haystack = " ".join((question or "").lower().split())
        found: List[str] = []
        for source_id, scheme in self.registered_schemes().items():
            # Keep hyphens: "Direct Plan - Growth" and "Direct - Growth" are
            # both in play, and collapsing them to spaces makes one name match
            # the other's question spuriously.
            name = scheme.lower().split("(")[0].strip()
            # "HDFC ELSS Tax Saver Fund - Direct Plan - Growth" -> also try the
            # plan-less prefix, which is how users usually refer to the fund.
            candidates = {name}
            for suffix in (
                "direct growth",
                "direct plan growth",
                "direct - growth",
                "direct plan - growth",
                "growth",
                "direct",
            ):
                if name.endswith(suffix):
                    candidates.add(name[: -len(suffix)].strip(" -"))
            if any(len(c) >= 12 and c in haystack for c in candidates):
                found.append(source_id)
        return sorted(found)

    def _scheme_scope(
        self, question: str, where: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Combine an explicit ``where`` with a scheme filter when the question
        names exactly one registered scheme.

        Filters on ``scheme`` rather than ``source_id`` because ``scheme`` is what
        Phase 2 writes into chunk metadata; ``source_id`` exists only in the id
        prefix, and adding it here would mean changing the stored collection.
        """
        mentioned = self.mentioned_source_ids(question)
        if len(mentioned) != 1:
            return where
        scheme = self._stored_schemes().get(mentioned[0])
        if not scheme:
            return where
        clause: Dict[str, Any] = {"scheme": scheme}
        if where:
            return {"$and": [clause, where]}
        return clause

    def retrieve(
        self,
        question: str,
        k: Optional[int] = None,
        where: Optional[Dict[str, Any]] = None,
        scope_to_scheme: bool = True,
    ) -> List[RetrievedChunk]:
        """Return the ``k`` chunks nearest to ``question``, nearest first.

        When the question names exactly one registered scheme the search is
        narrowed to that scheme, since a fund question must not be answered with
        a different fund's numbers.
        """
        question = (question or "").strip()
        if not question:
            raise ValueError("question must not be empty")
        limit = int(k or self.top_k)
        if limit < 1:
            raise ValueError(f"k must be at least 1, got {limit}")

        vector = self.embeddings.embed_query(question)
        expected = self.settings.embedding_dim
        if len(vector) != expected:
            raise RetrieverError(
                f"query vector has {len(vector)} dims but the collection was built with "
                f"{expected}. Re-embed with the same model, or delete chroma/ and re-ingest."
            )
        scope = self._scheme_scope(question, where) if scope_to_scheme else where
        hits = self.store.query(vector, k=limit, where=scope)
        return [self._to_chunk(hit, None) for hit in hits]

    @staticmethod
    def _to_chunk(hit: Mapping[str, Any], fact_type: Optional[str]) -> RetrievedChunk:
        return RetrievedChunk(
            chunk_id=hit["chunk_id"],
            text=hit["text"],
            metadata=hit["metadata"],
            similarity=float(hit["similarity"]),
            fact_type_match=(fact_type is None or hit["metadata"].get("fact_type") == fact_type),
        )

    def retrieve_for_fact_type(
        self,
        question: str,
        fact_type: str,
        k: Optional[int] = None,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[RetrievedChunk]:
        """Return hits with ``fact_type`` ahead of everything else.

        The filter is applied in ChromaDB, not by over-fetching and re-ranking
        in Python. That is not a stylistic choice: a short fact chunk such as
        "Benchmark: NIFTY 500 TRI" loses to the long "About ..." blobs that
        repeat the scheme name, and was measured at rank 80 of 100, so no
        practical over-fetch would surface it. Filtering on the indexed
        ``fact_type`` metadata finds it regardless of how short it is.

        If the corpus holds nothing of that type, the unfiltered hits are
        returned instead, each flagged ``fact_type_match=False``, so the
        relevance gate can distinguish "absent from the corpus" from "weak
        match" instead of guessing.
        """
        if fact_type not in FACT_TYPES:
            raise ValueError(
                f"unknown fact_type {fact_type!r}; expected one of {', '.join(FACT_TYPES)}"
            )
        question = (question or "").strip()
        if not question:
            raise ValueError("question must not be empty")
        limit = int(k or self.top_k)
        if limit < 1:
            raise ValueError(f"k must be at least 1, got {limit}")

        vector = self.embeddings.embed_query(question)
        scope = self._scheme_scope(question, where)
        clauses: List[Dict[str, Any]] = [{"fact_type": fact_type}]
        if scope:
            clauses.append(scope)
        clause: Dict[str, Any] = clauses[0] if len(clauses) == 1 else {"$and": clauses}

        # A metadata-filtered HNSW search is approximate and can come back short,
        # occasionally returning no rows at all even when matches exist. Widen
        # the net before concluding the corpus has nothing, so the eval harness
        # is not at the mercy of index internals.
        hits: List[Dict[str, Any]] = []
        for width in (limit, limit * 4, limit * 12):
            hits = self.store.query(vector, k=width, where=clause)
            if hits:
                break
        if hits:
            return [self._to_chunk(hit, fact_type) for hit in hits[:limit]]
        # Nothing of this fact type for this scheme (or at all). Fall back to the
        # unfiltered search so the caller can see what *is* nearby, each hit
        # flagged as a non-match, rather than receiving a silent empty list.
        fallback = scope or where
        return [
            RetrievedChunk(
                chunk_id=hit["chunk_id"],
                text=hit["text"],
                metadata=hit["metadata"],
                similarity=float(hit["similarity"]),
                fact_type_match=False,
            )
            for hit in self.store.query(vector, k=limit, where=fallback)
        ]

    def find_chunk_ids(self, chunk_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        """Read chunks back by id, used by the eval harness to score hits."""
        return self.store.get_chunks(chunk_ids)
