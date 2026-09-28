"""Tests for P0: the typed configuration contract.

P0 has no business logic, so these tests exist to pin the scaffold contract:
the spec-mandated attribute names, the PENDING/ConfigError behaviour, and the
promise that `.env.example` stays in step with `Settings`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from src.config import DEFAULT_EMBEDDING_DIM, PENDING, ConfigError, Settings, load

ROOT = Path(__file__).resolve().parent.parent
SPEC_ATTRIBUTES = (
    "chroma_dir",
    "corpus_dir",
    "sources_path",
    "chunks_txt_path",
    "chunks_jsonl_path",
    "sources_csv_path",
    "logs_dir",
    "embedding_dim",
)


def test_spec_mandated_attributes_are_exposed() -> None:
    settings = load()
    for name in SPEC_ATTRIBUTES:
        assert hasattr(settings, name), f"P0 requires Settings.{name}"
        assert getattr(settings, name) is not None


def test_embedding_dim_is_384() -> None:
    assert load().embedding_dim == DEFAULT_EMBEDDING_DIM == 384


def test_paths_are_path_objects() -> None:
    settings = load()
    for name in SPEC_ATTRIBUTES:
        if name == "embedding_dim":
            continue
        assert isinstance(getattr(settings, name), Path)


def test_pending_defaults_to_none_and_raises_named_error() -> None:
    settings = Settings()
    assert settings.unresolved() == list(PENDING)
    for name in PENDING:
        assert getattr(settings, name) is None
        with pytest.raises(ConfigError, match=name.upper()):
            settings.require(name)


def test_require_rejects_a_name_that_is_not_pending() -> None:
    with pytest.raises(ConfigError):
        Settings().require("embedding_dim")


def test_require_returns_a_value_once_set() -> None:
    settings = Settings(chunk_size=1200)
    assert settings.require("chunk_size") == 1200
    assert settings.unresolved() == [n for n in PENDING if n != "chunk_size"]


def test_groq_api_key_is_never_hardcoded() -> None:
    assert Settings().groq_api_key is None
    example = (ROOT / ".env.example").read_text()
    assert re.search(r"^GROQ_API_KEY=$", example, re.MULTILINE)


def test_env_example_covers_every_architecture_variable() -> None:
    architecture = (ROOT / "docs" / "architecture.md").read_text()
    section = architecture.split("## 9.")[1].split("## 10.")[0]
    declared = set(re.findall(r"^\| `([A-Z_]+)`", section, re.MULTILINE))
    example = set(
        re.findall(r"^([A-Z_]+)=", (ROOT / ".env.example").read_text(), re.MULTILINE)
    )
    assert declared, "architecture section 9 variable table not found"
    assert declared <= example, f"missing from .env.example: {sorted(declared - example)}"


def test_requirements_pin_the_nine_mandatory_packages() -> None:
    pins = dict(
        line.split("==", 1)
        for line in (ROOT / "requirements.txt").read_text().split()
        if "==" in line
    )
    required = {
        "sentence-transformers",
        "chromadb",
        "groq",
        "beautifulsoup4",
        "requests",
        "pyyaml",
        "python-dotenv",
        "streamlit",
        "pytest",
    }
    assert required <= set(pins), f"unpinned or missing: {sorted(required - set(pins))}"


def test_sources_yaml_holds_the_five_prd_sources() -> None:
    data = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text())
    sources = data["sources"]
    assert len(sources) == 5
    for entry in sources:
        assert set(entry) >= {"source_id", "url", "scheme", "category", "plan_variant"}
        assert entry["plan_variant"] == "direct_growth"
        assert entry["url"].startswith("https://")


def test_gitignore_protects_secrets_but_keeps_deliverables() -> None:
    ignored = (ROOT / ".gitignore").read_text().split()
    for secret in (".env", ".venv/", "chroma/", "logs/", "__pycache__/"):
        assert secret in ignored, f".gitignore must ignore {secret}"
    text = (ROOT / ".gitignore").read_text()
    for deliverable in ("chunks.txt", "chunks.jsonl", "SOURCES.md"):
        assert deliverable not in text, f".gitignore must not ignore {deliverable}"
