"""Resolution — build the single-node graph from the extracted entities/edges.

There is no separate "matching" pass: extraction assigns each mention a normalized
ID, and ``Graph.upsert_*`` merges anything that shares an ID. So resolution is
``extract`` + ``upsert``, and a "merge" is simply a node whose ``sources`` set ends
up with more than one source. The alias table feeds normalization upstream (in
``extract``), so prose-name ↔ handle merges happen the same way.
"""

from __future__ import annotations

from importlib import resources

import yaml

from .extract import extract
from .model import Graph
from .sources import ParsedDoc


def load_aliases() -> dict[str, str]:
    """Load the packaged alias table as ``{lowercased-display-name: handle}``."""
    text = resources.files("graphrag").joinpath("aliases.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    raw = data.get("aliases", {}) if isinstance(data, dict) else {}
    return {str(k).lower(): str(v) for k, v in raw.items()}


def resolve(docs: list[ParsedDoc], aliases: dict[str, str] | None = None) -> Graph:
    """Resolve the parsed corpus into a single-node graph."""
    if aliases is None:
        aliases = load_aliases()
    nodes, edges = extract(docs, aliases)
    graph = Graph()
    for node in nodes:
        graph.upsert_node(node)
    for edge in edges:
        graph.upsert_edge(edge)
    return graph


def cross_source_merges(graph: Graph) -> list[str]:
    """IDs of nodes that resolved across *both* sources — the visible merges."""
    return sorted(n.id for n in graph.nodes.values() if len(n.sources) > 1)
