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

import numpy as np

from src.config import Settings, load

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EXPECTED_DIM = 384

#: The FP32 graph, not one of the int8 quantisations. sentence-transformers ships
#: several prebuilt ONNX exports of this model; only the fp32 one is numerically
#: equivalent to the PyTorch weights the collection was built with. The int8
#: graphs were measured at cosine 0.9907-0.9948 against torch, which is enough to
#: move a score across the K12 floor, so they are deliberately not used.
ONNX_FILE = "onnx/model.onnx"
TOKENIZER_FILE = "tokenizer.json"
#: all-MiniLM-L6-v2's own ``sentence_bert_config.json`` pins 256. This has to match
#: sentence-transformers exactly: a longer window would let a query see more text
#: than an ingested chunk did, and the vectors would no longer be comparable.
MAX_SEQ_LENGTH = 256


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


class OnnxMiniLMEmbeddingService:
    """The same MiniLM weights, run through onnxruntime instead of PyTorch.

    This is the query-time service. It exists because loading
    ``sentence-transformers`` puts the process at ~411 MB RSS once Streamlit and
    Chroma are resident, against Render Free's 512 MB cgroup limit — the app was
    OOM-killed on its first question there. The FP32 ONNX graph of the same model
    measures ~350 MB in the same shape, never imports torch, and starts in 0.3s
    instead of downloading 87 MB inside the request. It returns the same vectors
    (cosine 1.000000, max component delta ~3e-07, identical chunk ranking and
    identical similarity scores to seven decimal places).

    Ingestion deliberately does *not* use this class, so the persisted 166 vectors
    keep coming from the PyTorch path they were always built by. Nothing is
    re-embedded: the collection is only ever *queried* from here, and the
    ``EmbeddingService`` contract (``dim``, ``embed_query``, ``embed_documents``)
    is unchanged, so the retriever, guardrails and store cannot tell the
    difference.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        expected_dim: int = EXPECTED_DIM,
        max_seq_length: int = MAX_SEQ_LENGTH,
    ) -> None:
        try:
            import onnxruntime as ort
            from huggingface_hub import hf_hub_download
            from tokenizers import Tokenizer
        except ImportError as exc:  # pragma: no cover - environment problem
            raise EmbeddingError(
                "onnxruntime, tokenizers and huggingface-hub are required for the "
                "ONNX query path. Run `pip install -r requirements.txt`."
            ) from exc

        self.model_name = model_name
        self.batch_size = 32
        try:
            self._tokenizer = Tokenizer.from_file(
                hf_hub_download(model_name, TOKENIZER_FILE)
            )
            self._tokenizer.enable_truncation(max_length=max_seq_length)
            self._tokenizer.enable_padding()
            self._session = ort.InferenceSession(
                hf_hub_download(model_name, ONNX_FILE),
                providers=["CPUExecutionProvider"],
            )
        except Exception as exc:  # pragma: no cover - network/download problem
            raise EmbeddingError(
                f"could not load the ONNX graph for {model_name!r}: {exc}. "
                "The first load downloads the weights; check network access."
            ) from exc

        # Only Bert-family graphs take token_type_ids, and the export varies by
        # revision, so the feed is built from what the graph actually declares
        # rather than assumed.
        self._needs_token_type = "token_type_ids" in {
            i.name for i in self._session.get_inputs()
        }

        # The torch path reads the dimension off the model and rejects a mismatch
        # before anything is embedded. One probe inference reproduces that guard,
        # and reuses _encode so there is only one definition of the pooling.
        self.dim = len(self._encode(["dimension probe"])[0])
        if self.dim != expected_dim:
            raise EmbeddingError(
                f"{model_name} produces {self.dim}-dim vectors but {expected_dim} were "
                "expected. Do not mix embedding models in one collection: delete "
                "chroma/ and re-ingest with a single model."
            )

    def _encode(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        encoded = self._tokenizer.encode_batch(list(texts))
        input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
        attention = np.array([e.attention_mask for e in encoded], dtype=np.int64)
        feed = {"input_ids": input_ids, "attention_mask": attention}
        if self._needs_token_type:
            feed["token_type_ids"] = np.zeros_like(input_ids)
        hidden = self._session.run(None, feed)[0]

        # Mean pooling over the attention mask, then L2 normalisation. This is
        # what 1_Pooling/config.json asks for (pooling_mode_mean_tokens) and is
        # what the stored vectors were built with; skipping either step would
        # shift every score against the relevance floor.
        mask = attention[..., None].astype(np.float32)
        pooled = (hidden * mask).sum(axis=1) / np.clip(mask.sum(axis=1), 1e-9, None)
        pooled = pooled / np.linalg.norm(pooled, axis=1, keepdims=True)
        return [[float(value) for value in row] for row in pooled]

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


def build_query_embedding_service(
    settings: Optional[Settings] = None,
) -> EmbeddingService:
    """Return the *query-time* embedding service: FP32 ONNX, no PyTorch.

    Separate from :func:`build_embedding_service` on purpose. That one is the
    ingest path and stays on sentence-transformers, so the vectors already in
    Chroma are neither re-derived nor re-written by this change; the ONNX graph is
    only ever asked to place a *question* in the same space. Since the two agree
    to float32 rounding, the similarity floor in P4 keeps its measured meaning.
    """
    settings = settings or load()
    return OnnxMiniLMEmbeddingService(
        model_name=settings.embedding_model, expected_dim=settings.embedding_dim
    )
