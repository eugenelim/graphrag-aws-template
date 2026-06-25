"""The consolidated showcase query set + a packaged-resource loader (slice-3 AC10).

One curated set is the single home for the demo's queries (``queries.yaml``,
packaged). Each query is labeled with the retrieval mode it should *win*, the gold
entity-id(s)/chunk-id(s) the trace should surface (all resolving in the fixture
corpus), and a one-line presenter highlight — so a presenter can drive the whole demo
from one place (the presenter script: ``docs/guides/tutorials/three-mode-demo.md``).

``load_showcase`` is a packaged-resource loader, like ``resolve.load_aliases`` — it
uses ``yaml`` and is **CLI/test-only**, never imported by the pure-Python query Lambda
(keep it out of that import graph; the Lambda bundle excludes pyyaml).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from typing import Literal

import yaml

ShowcaseMode = Literal["vector", "graph", "hybrid"]


@dataclass
class ShowcaseQuery:
    """One curated demo query: its winning mode, gold ids, and a presenter highlight."""

    id: str
    query: str
    wins: ShowcaseMode
    gold: list[str] = field(default_factory=list)
    highlight: str = ""


@dataclass
class PermissionShowcaseQuery:
    """A slice-4 permission-filtered demo query: the same question under a ``persona``, with
    the entity ids the persona **should see** vs. those it should find **filtered** — the
    two-persona contrast the presenter narrates (synthetic labels, a teaching stand-in for
    ACLs, never real authz)."""

    id: str
    query: str
    persona: str
    visible: list[str] = field(default_factory=list)
    filtered: list[str] = field(default_factory=list)
    highlight: str = ""


@dataclass
class GovernedShowcaseQuery:
    """An opencypher-templates governed-path demo query: the ``template`` a correct
    selector should pick, the ``param`` value that should bind from the question, and the
    ``gold`` rows the parameterized openCypher should return (all in the fixture corpus)."""

    id: str
    query: str
    template: str
    param: str
    gold: list[str] = field(default_factory=list)
    highlight: str = ""


def load_showcase() -> list[ShowcaseQuery]:
    """Load the packaged showcase query set as ``ShowcaseQuery`` objects."""
    text = resources.files("graphrag.showcase").joinpath("queries.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    raw = data.get("queries", []) if isinstance(data, dict) else []
    out: list[ShowcaseQuery] = []
    for entry in raw:
        out.append(
            ShowcaseQuery(
                id=str(entry["id"]),
                query=str(entry["query"]),
                wins=entry["wins"],
                gold=[str(g) for g in entry.get("gold", [])],
                highlight=str(entry.get("highlight", "")),
            )
        )
    return out


def load_permission_showcase() -> list[PermissionShowcaseQuery]:
    """Load the packaged slice-4 permission-filtered demo queries (the two-persona set)."""
    text = resources.files("graphrag.showcase").joinpath("queries.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    raw = data.get("permission_queries", []) if isinstance(data, dict) else []
    out: list[PermissionShowcaseQuery] = []
    for entry in raw:
        out.append(
            PermissionShowcaseQuery(
                id=str(entry["id"]),
                query=str(entry["query"]),
                persona=str(entry["persona"]),
                visible=[str(g) for g in entry.get("visible", [])],
                filtered=[str(g) for g in entry.get("filtered", [])],
                highlight=str(entry.get("highlight", "")),
            )
        )
    return out


def load_governed_showcase() -> list[GovernedShowcaseQuery]:
    """Load the packaged opencypher-templates governed-path demo queries."""
    text = resources.files("graphrag.showcase").joinpath("queries.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    raw = data.get("governed_queries", []) if isinstance(data, dict) else []
    out: list[GovernedShowcaseQuery] = []
    for entry in raw:
        out.append(
            GovernedShowcaseQuery(
                id=str(entry["id"]),
                query=str(entry["query"]),
                template=str(entry["template"]),
                param=str(entry["param"]),
                gold=[str(g) for g in entry.get("gold", [])],
                highlight=str(entry.get("highlight", "")),
            )
        )
    return out
