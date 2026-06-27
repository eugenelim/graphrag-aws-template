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


@dataclass
class Text2CypherShowcaseQuery:
    """A text2opencypher-guarded (flexible/risky-path) demo query: the ``gold`` nodes the
    model-authored openCypher should return (all resolving in the fixture corpus), and an
    optional ``shared_with_template`` naming the governed template that answers the **same**
    question — the head-to-head that lets a watcher choose between the governed and flexible
    paths."""

    id: str
    query: str
    gold: list[str] = field(default_factory=list)
    shared_with_template: str | None = None
    highlight: str = ""


@dataclass
class SelfQueryShowcaseQuery:
    """A metadata-filtering (self-query) demo query: the structured ``expected_filter`` the
    extractor should read out of the question (a ``{field: [values]}`` map over the declared
    ``source``/``entity_ids`` schema), the ``mode`` the filter applies in (``vector`` or
    ``hybrid``), and the gold chunk ids that should be ``visible`` vs. ``excluded`` once the
    filter is applied during the ANN scan (all resolving in the fixture corpus)."""

    id: str
    query: str
    mode: ShowcaseMode
    expected_filter: dict[str, list[str]] = field(default_factory=dict)
    visible: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    highlight: str = ""


@dataclass
class ParentChildShowcaseQuery:
    """A parent-child-retrieval demo query: the gold ``expected_matched_child`` (the small,
    precise chunk whose vector matches) and the ``expected_parent`` (the document whose full
    body is returned for synthesis — the ``{source}/{doc_path}`` key), plus the ``contrast``
    against flat ``vector`` mode (same question, single matched chunk vs. the whole parent
    body). All ids resolve in the fixture corpus."""

    id: str
    query: str
    expected_matched_child: str
    expected_parent: str
    contrast: str = ""
    highlight: str = ""


@dataclass
class ExtractionShowcaseQuery:
    """A schema-guided-extraction demo query answerable **only via an LLM edge** — a
    free-narrative inter-entity relationship the deterministic graph structurally cannot reach.

    ``mode`` is the retrieval mode (``graph`` / ``hybrid``) that traverses the LLM edge;
    ``llm_edge`` is the ``(src_id, kind, dst_id)`` the answer leans on (its kind is one of the
    LLM-extractable kinds); ``expected_entities`` all resolve in the fixture corpus."""

    id: str
    query: str
    mode: str
    llm_edge: tuple[str, str, str]
    expected_entities: list[str]
    highlight: str = ""


@dataclass
class GlobalShowcaseQuery:
    """A global-community-summary demo query: a **corpus-wide** question (no seed entity for
    the hybrid to expand from), the ``expected_entities`` that should appear in the contributing
    communities (all resolve in the fixture corpus after detection), and the corpus-wide
    ``theme`` the map-reduce surfaces."""

    id: str
    query: str
    expected_entities: list[str]
    theme: str = ""
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


def load_selfquery_showcase() -> list[SelfQueryShowcaseQuery]:
    """Load the packaged metadata-filtering (self-query) demo queries."""
    text = resources.files("graphrag.showcase").joinpath("queries.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    raw = data.get("selfquery_queries", []) if isinstance(data, dict) else []
    out: list[SelfQueryShowcaseQuery] = []
    for entry in raw:
        expected = entry.get("expected_filter", {}) or {}
        out.append(
            SelfQueryShowcaseQuery(
                id=str(entry["id"]),
                query=str(entry["query"]),
                mode=entry["mode"],
                expected_filter={k: [str(v) for v in vs] for k, vs in expected.items()},
                visible=[str(g) for g in entry.get("visible", [])],
                excluded=[str(g) for g in entry.get("excluded", [])],
                highlight=str(entry.get("highlight", "")),
            )
        )
    return out


def load_parentchild_showcase() -> list[ParentChildShowcaseQuery]:
    """Load the packaged parent-child-retrieval demo queries."""
    text = resources.files("graphrag.showcase").joinpath("queries.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    raw = data.get("parentchild_queries", []) if isinstance(data, dict) else []
    out: list[ParentChildShowcaseQuery] = []
    for entry in raw:
        out.append(
            ParentChildShowcaseQuery(
                id=str(entry["id"]),
                query=str(entry["query"]),
                expected_matched_child=str(entry["expected_matched_child"]),
                expected_parent=str(entry["expected_parent"]),
                contrast=str(entry.get("contrast", "")),
                highlight=str(entry.get("highlight", "")),
            )
        )
    return out


def load_global_showcase() -> list[GlobalShowcaseQuery]:
    """Load the packaged global-community-summary (corpus-wide) demo queries."""
    text = resources.files("graphrag.showcase").joinpath("queries.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    raw = data.get("global_queries", []) if isinstance(data, dict) else []
    out: list[GlobalShowcaseQuery] = []
    for entry in raw:
        out.append(
            GlobalShowcaseQuery(
                id=str(entry["id"]),
                query=str(entry["query"]),
                expected_entities=[str(e) for e in entry.get("expected_entities", [])],
                theme=str(entry.get("theme", "")),
                highlight=str(entry.get("highlight", "")),
            )
        )
    return out


def load_extraction_showcase() -> list[ExtractionShowcaseQuery]:
    """Load the packaged schema-guided-extraction demo queries (LLM-only-edge questions)."""
    text = resources.files("graphrag.showcase").joinpath("queries.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    raw = data.get("extraction_queries", []) if isinstance(data, dict) else []
    out: list[ExtractionShowcaseQuery] = []
    for entry in raw:
        edge = entry["llm_edge"]
        out.append(
            ExtractionShowcaseQuery(
                id=str(entry["id"]),
                query=str(entry["query"]),
                mode=str(entry["mode"]),
                llm_edge=(str(edge[0]), str(edge[1]), str(edge[2])),
                expected_entities=[str(e) for e in entry.get("expected_entities", [])],
                highlight=str(entry.get("highlight", "")),
            )
        )
    return out


def load_text2cypher_showcase() -> list[Text2CypherShowcaseQuery]:
    """Load the packaged text2opencypher-guarded (flexible-path) demo queries."""
    text = resources.files("graphrag.showcase").joinpath("queries.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    raw = data.get("text2cypher_queries", []) if isinstance(data, dict) else []
    out: list[Text2CypherShowcaseQuery] = []
    for entry in raw:
        shared = entry.get("shared_with_template")
        out.append(
            Text2CypherShowcaseQuery(
                id=str(entry["id"]),
                query=str(entry["query"]),
                gold=[str(g) for g in entry.get("gold", [])],
                shared_with_template=str(shared) if shared else None,
                highlight=str(entry.get("highlight", "")),
            )
        )
    return out
