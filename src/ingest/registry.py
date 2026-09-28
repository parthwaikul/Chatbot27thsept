"""K1 — the source allowlist.

Nothing may enter the corpus or be cited unless its URL is registered here.
This module is the enforcement point for constraint C-1 (public official
sources only, no third-party blogs).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import yaml

from src.config import ConfigError
from src.ingest.models import CATEGORIES

SOURCE_ID_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
PLAN_VARIANTS: Tuple[str, ...] = ("direct_growth",)
ALLOWED_HOST_SUFFIXES: Tuple[str, ...] = (
    "groww.in",
    "hdfcmutualfund.com",
    "hdfcfund.com",
    "amfiindia.com",
    "sebi.gov.in",
)


class RegistryError(ConfigError):
    """Raised when the source registry is malformed or a URL is not allowed."""


@dataclass(frozen=True)
class SourceSpec:
    """One registered public source page."""

    source_id: str
    url: str
    scheme: str
    category: str
    plan_variant: str


class SourceRegistry:
    """Immutable, validated view of ``config/sources.yaml``."""

    def __init__(self, specs: Sequence[SourceSpec]) -> None:
        self._specs: Tuple[SourceSpec, ...] = tuple(specs)
        self._by_id: Dict[str, SourceSpec] = {}
        self._by_url: Dict[str, SourceSpec] = {}
        for spec in self._specs:
            self._validate(spec)
            if spec.source_id in self._by_id:
                raise RegistryError(f"duplicate source_id in registry: {spec.source_id!r}")
            if spec.url in self._by_url:
                raise RegistryError(f"duplicate url in registry: {spec.url!r}")
            self._by_id[spec.source_id] = spec
            self._by_url[spec.url] = spec

    @staticmethod
    def _validate(spec: SourceSpec) -> None:
        if not SOURCE_ID_RE.match(spec.source_id):
            raise RegistryError(
                f"source_id {spec.source_id!r} must be lowercase snake_case"
            )
        if not spec.url.startswith("https://"):
            raise RegistryError(f"source {spec.source_id!r} must use https, got {spec.url!r}")
        if not spec.scheme.strip():
            raise RegistryError(f"source {spec.source_id!r} has no scheme name")
        if spec.category not in CATEGORIES:
            raise RegistryError(
                f"source {spec.source_id!r} has unknown category {spec.category!r}; "
                f"expected one of {', '.join(CATEGORIES)}"
            )
        if spec.plan_variant not in PLAN_VARIANTS:
            raise RegistryError(
                f"source {spec.source_id!r} has unsupported plan_variant "
                f"{spec.plan_variant!r}; the corpus is Direct Growth (see PRD Q8)"
            )

    @classmethod
    def from_file(cls, path: Path) -> "SourceRegistry":
        """Load and validate the registry from a YAML file."""
        if not path.is_file():
            raise RegistryError(f"source registry not found: {path}")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entries = data.get("sources")
        if not entries:
            raise RegistryError(f"{path} has no 'sources' entries")
        specs: List[SourceSpec] = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise RegistryError(f"{path} entry #{index} is not a mapping")
            missing = [
                key
                for key in ("source_id", "url", "scheme", "category", "plan_variant")
                if not entry.get(key)
            ]
            if missing:
                raise RegistryError(
                    f"{path} entry #{index} is missing {', '.join(missing)}"
                )
            specs.append(
                SourceSpec(
                    source_id=str(entry["source_id"]),
                    url=str(entry["url"]),
                    scheme=str(entry["scheme"]),
                    category=str(entry["category"]),
                    plan_variant=str(entry["plan_variant"]),
                )
            )
        return cls(specs)

    def all_sources(self) -> Tuple[SourceSpec, ...]:
        """Return every registered source."""
        return self._specs

    def source_ids(self) -> Tuple[str, ...]:
        return tuple(spec.source_id for spec in self._specs)

    def by_id(self, source_id: str) -> SourceSpec:
        try:
            return self._by_id[source_id]
        except KeyError as exc:
            raise RegistryError(f"unknown source_id: {source_id!r}") from exc

    def is_allowed(self, url: str) -> bool:
        """Return True when the exact URL is registered."""
        return url in self._by_url

    def require_allowed(self, url: str) -> str:
        """Return the URL when registered, otherwise raise (C-1 enforcement)."""
        if url not in self._by_url:
            raise RegistryError(
                f"url is not in the source registry and may not be fetched or cited: {url!r}"
            )
        return url

    def schemes(self) -> Dict[str, str]:
        return {spec.source_id: spec.scheme for spec in self._specs}

    def __len__(self) -> int:
        return len(self._specs)

    def __iter__(self) -> Iterable[SourceSpec]:
        return iter(self._specs)
