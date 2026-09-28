"""K6 — the one and only embedding service.

The brief requires that documents and queries are embedded with the *same*
model, and the architecture requires exactly one model instance in the process
(D6), because a mixed-model collection silently breaks similarity search. Both
document and query paths call :meth:`EmbeddingService.encode` with
``normalize_embeddings=True``, and the dimension is asserted at construction so a
mismatched model fails loudly here rather than as meaningless search results.
"""

from __future__ import annotations

import hashlib
import math
from typing import Iterable, List, Optional, Protocol, Sequence

from src.config import Settings, load

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EXPECTED_DIM = 384


class EmbeddingService(Protocol):
    """The interface the ingest and query pipelines both depend on."""

    dim: int
    model_name: str

    def embed_documents(self, texts: Sequence[str]) -> List[List[float]]:
        """Embed a batch of documents."""
        ...

    def embed_query(self, text: str) -> List[float]:
        """Embed a single query with the same model and normalisation."""
        ...


class EmbeddingError(RuntimeError):
    """Raised when the model or its output dimension is wrong."""


class MiniLMEmbeddingService:
    """Sentence-transformers MiniLM, normalised, dimension-checked."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        expected_dim: int = EXPECTED_DIM,
        batch_size: int = 32,
    ) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - environment problem
            raise EmbeddingError(
                "sentence-transformers is not installed. Run "
                "`pip install -r requirements.txt`."
            ) from exc

        self.model_name = model_name
        self.batch_size = batch_size
        try:
            self._model = SentenceTransformer(model_name)
        except Exception as exc:  # pragma: no cover - network/download problem
            raise EmbeddingError(
                f"could not load embedding model {model_name!r}: {exc}. "
                "The first load downloads the weights; check network access."
            ) from exc

        dim = int(self._model.get_sentence_embedding_dimension())
        if dim != expected_dim:
            raise EmbeddingError(
                f"{model_name} produces {dim}-dim vectors but {expected_dim} were "
                "expected. Do not mix embedding models in one collection: delete "
                "chroma/ and re-ingest with a single model."
            )
        self.dim = dim

    def _encode(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        vectors = self._model.encode(
            list(texts),
            batch_size=self.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [[float(value) for value in row] for row in vectors]

    def embed_documents(self, texts: Sequence[str]) -> List[List[float]]:
        return self._encode(texts)

    def embed_query(self, text: str) -> List[float]:
        return self._encode([text])[0]


class FakeEmbeddingService:
    """Deterministic offline stand-in: hash-seeded 384-dim unit vectors.

    Lets the pipeline, the store and the idempotency tests run with no model
    download and no network. Vectors are L2-normalised so a cosine query behaves
    like the real service.
    """

    def __init__(
        self, model_name: str = "fake-hash-384", dim: int = EXPECTED_DIM
    ) -> None:
        self.model_name = model_name
        self.dim = dim

    def _vector(self, text: str) -> List[float]:
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        raw: List[float] = []
        counter = 0
        while len(raw) < self.dim:
            block = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
            raw.extend(value / 255.0 for value in block)
            counter += 1
        raw = raw[: self.dim]
        norm = math.sqrt(sum(value * value for value in raw)) or 1.0
        return [value / norm for value in raw]

    def embed_documents(self, texts: Sequence[str]) -> List[List[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._vector(text)


def build_embedding_service(
    settings: Optional[Settings] = None, fake: bool = False
) -> EmbeddingService:
    """Return the shared embedding service.

    ``fake=True`` returns :class:`FakeEmbeddingService` for offline tests.
    """
    settings = settings or load()
    if fake:
        return FakeEmbeddingService(dim=settings.embedding_dim)
    return MiniLMEmbeddingService(
        model_name=settings.embedding_model, expected_dim=settings.embedding_dim
    )
