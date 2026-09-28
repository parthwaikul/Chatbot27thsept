"""Typed application settings loaded from the environment and `.env`.

PENDING values (the ones the milestone brief left undecided) default to `None`
and must be read through :meth:`Settings.require`, which raises
:class:`ConfigError` naming the missing variable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_EMBEDDING_DIM = 384
PENDING: Tuple[str, ...] = (
    "groq_model",
    "chunk_size",
    "chunk_overlap",
    "chunk_strategy",
    "retrieval_top_k",
    "similarity_floor",
    "educational_link",
    "factsheet_link_map",
)


class ConfigError(RuntimeError):
    """Raised when a required or PENDING setting is missing or invalid."""


def _env(name: str) -> Optional[str]:
    """Return a stripped environment value, or None when unset or blank."""
    raw = os.environ.get(name)
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def _env_str(name: str, default: str) -> str:
    value = _env(name)
    return default if value is None else value


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {value!r}") from exc


def _env_float(name: str, default: float) -> float:
    value = _env(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {value!r}") from exc


def _env_path(name: str, default: str) -> Path:
    return Path(_env_str(name, default)).expanduser()


def _env_factsheet_map() -> Optional[Dict[str, str]]:
    """Parse FACTSHEET_LINK_MAP from a `source_id=url` comma-separated value."""
    value = _env("FACTSHEET_LINK_MAP")
    if value is None:
        return None
    mapping: Dict[str, str] = {}
    for pair in value.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ConfigError(
                f"FACTSHEET_LINK_MAP entries must look like source_id=url, got {pair!r}"
            )
        source_id, url = (part.strip() for part in pair.split("=", 1))
        if not source_id or not url:
            raise ConfigError(f"FACTSHEET_LINK_MAP entry is incomplete: {pair!r}")
        mapping[source_id] = url
    if not mapping:
        raise ConfigError("FACTSHEET_LINK_MAP was set but contained no entries")
    return mapping


@dataclass(frozen=True)
class Settings:
    """Immutable view of every configuration value used by the pipeline."""

    groq_api_key: Optional[str] = None
    groq_model: Optional[str] = None
    groq_temperature: float = 0.0
    groq_timeout_s: int = 30
    groq_max_retries: int = 2

    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_dim: int = DEFAULT_EMBEDDING_DIM

    chroma_dir: Path = field(default_factory=lambda: Path("./chroma"))
    chroma_collection: str = "mf_faq"

    corpus_dir: Path = field(default_factory=lambda: Path("./corpus"))
    sources_path: Path = field(default_factory=lambda: Path("./config/sources.yaml"))
    sources_csv_path: Path = field(default_factory=lambda: Path("./corpus/sources.csv"))
    chunks_txt_path: Path = field(default_factory=lambda: Path("./chunks.txt"))
    chunks_jsonl_path: Path = field(default_factory=lambda: Path("./chunks.jsonl"))
    logs_dir: Path = field(default_factory=lambda: Path("./logs"))

    chunk_size: Optional[int] = None
    chunk_overlap: Optional[int] = None
    chunk_strategy: Optional[str] = None

    retrieval_top_k: Optional[int] = None
    similarity_floor: Optional[float] = None

    answer_max_sentences: int = 3

    educational_link: Optional[str] = None
    factsheet_link_map: Optional[Dict[str, str]] = None

    app_env: str = "local"
    http_timeout_s: int = 20
    http_user_agent: str = "mf-faq-rag/0.1 (educational prototype)"
    min_extracted_chars: int = 500

    def require(self, name: str) -> Any:
        """Return a PENDING setting, raising ConfigError if it is still unset.

        Use this for every field listed in :data:`PENDING` so that an undecided
        configuration surfaces as a named error instead of a silent ``None``.
        """
        if name not in PENDING:
            raise ConfigError(f"{name} is not a PENDING setting")
        value = getattr(self, name, None)
        if value is None:
            raise ConfigError(
                f"Setting '{name}' is not configured. Set {name.upper()} in .env "
                f"(see .env.example) and record the decision in the phase document."
            )
        return value

    def unresolved(self) -> List[str]:
        """Names of PENDING settings that are still unset, in declaration order."""
        return [name for name in PENDING if getattr(self, name, None) is None]


def load(dotenv_path: Optional[Path] = None) -> Settings:
    """Load settings from `.env` then the process environment."""
    load_dotenv(dotenv_path) if dotenv_path else load_dotenv()

    embedding_dim = _env_int("EMBEDDING_DIM", DEFAULT_EMBEDDING_DIM)
    if embedding_dim != DEFAULT_EMBEDDING_DIM:
        raise ConfigError(
            f"EMBEDDING_DIM must be {DEFAULT_EMBEDDING_DIM} for "
            f"{DEFAULT_EMBEDDING_MODEL}, got {embedding_dim}. Changing the embedding model or "
            f"dimension requires deleting the persisted ChromaDB directory and re-ingesting."
        )

    return Settings(
        groq_api_key=_env("GROQ_API_KEY"),
        groq_model=_env("GROQ_MODEL"),
        groq_temperature=_env_float("GROQ_TEMPERATURE", 0.0),
        groq_timeout_s=_env_int("GROQ_TIMEOUT_S", 30),
        groq_max_retries=_env_int("GROQ_MAX_RETRIES", 2),
        embedding_model=_env_str("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        embedding_dim=embedding_dim,
        chroma_dir=_env_path("CHROMA_DIR", "./chroma"),
        chroma_collection=_env_str("CHROMA_COLLECTION", "mf_faq"),
        corpus_dir=_env_path("CORPUS_DIR", "./corpus"),
        sources_path=_env_path("SOURCES_YAML", "./config/sources.yaml"),
        sources_csv_path=_env_path("SOURCES_CSV", "./corpus/sources.csv"),
        chunks_txt_path=_env_path("CHUNKS_TXT", "./chunks.txt"),
        chunks_jsonl_path=_env_path("CHUNKS_JSONL", "./chunks.jsonl"),
        logs_dir=_env_path("LOGS_DIR", "./logs"),
        chunk_size=None if _env("CHUNK_SIZE") is None else _env_int("CHUNK_SIZE", 0),
        chunk_overlap=None if _env("CHUNK_OVERLAP") is None else _env_int("CHUNK_OVERLAP", 0),
        chunk_strategy=_env("CHUNK_STRATEGY"),
        retrieval_top_k=None if _env("RETRIEVAL_TOP_K") is None else _env_int("RETRIEVAL_TOP_K", 0),
        similarity_floor=None if _env("SIMILARITY_FLOOR") is None else _env_float("SIMILARITY_FLOOR", 0.0),
        answer_max_sentences=_env_int("ANSWER_MAX_SENTENCES", 3),
        educational_link=_env("EDUCATIONAL_LINK"),
        factsheet_link_map=_env_factsheet_map(),
        app_env=_env_str("APP_ENV", "local"),
        http_timeout_s=_env_int("HTTP_TIMEOUT_S", 20),
        http_user_agent=_env_str(
            "HTTP_USER_AGENT", "mf-faq-rag/0.1 (educational prototype; +local)"
        ),
        min_extracted_chars=_env_int("MIN_EXTRACTED_CHARS", 500),
    )
