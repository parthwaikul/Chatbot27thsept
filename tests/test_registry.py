"""Tests for K1 (registry) and the C-1 / C-3 guarantees they enforce."""

from __future__ import annotations

import pytest

from src.ingest.registry import RegistryError, SourceRegistry, SourceSpec
from tests.fixtures import MINIMAL_HTML, SPECS


def test_registry_rejects_unregistered_url() -> None:
    registry = SourceRegistry(SPECS)
    assert registry.is_allowed(SPECS[0].url)
    with pytest.raises(RegistryError):
        registry.require_allowed("https://random-blog.example.com/etf-fees")


def test_registry_rejects_non_https() -> None:
    with pytest.raises(RegistryError):
        SourceRegistry([SourceSpec("x", "http://groww.in/a", "X", "elss", "direct_growth")])


def test_registry_rejects_duplicate_source_id() -> None:
    with pytest.raises(RegistryError):
        SourceRegistry([SPECS[0], SPECS[0]])


def test_registry_rejects_unknown_category() -> None:
    with pytest.raises(RegistryError):
        SourceRegistry([SourceSpec("x", "https://groww.in/a", "X", "midcap", "direct_growth")])


def test_registry_rejects_unsupported_plan_variant() -> None:
    with pytest.raises(RegistryError):
        SourceRegistry([SourceSpec("x", "https://groww.in/a", "X", "elss", "direct")])
