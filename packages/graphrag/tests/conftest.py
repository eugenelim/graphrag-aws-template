"""Shared test fixtures: paths into the bundled real-excerpt corpus."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
CORPUS = FIXTURES / "corpus"


@pytest.fixture
def community_root() -> Path:
    return CORPUS / "community"


@pytest.fixture
def enhancements_root() -> Path:
    return CORPUS / "enhancements"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES
