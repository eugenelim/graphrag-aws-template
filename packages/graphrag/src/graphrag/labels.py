"""Synthetic visibility-label assignment — the INGEST side of the teaching stand-in.

Reads the packaged ``labels.yaml`` (an entity-id → tier map) and stamps the resolved
labels onto the graph and the chunks during the **same** dual-write the corpus is already
ingested through, so the two stores never diverge (charter pattern 2). Labels compose
most-restrictive-wins (``visibility.compose``): an edge is as sensitive as its
more-sensitive endpoint; a chunk as its most-sensitive owning entity.

This module uses ``yaml`` and is therefore **ingest-path only** (Fargate / CLI, where
PyYAML is available) — it must **never** be imported by the PyYAML-free query Lambda. The
read side (``visibility.py``) carries no yaml and resolves a persona's clearance at query
time; by then the labels are already baked into the stores' node/edge/chunk properties.

The labels are a stand-in for real ACLs, never production authz (charter principle 5).
``labels.yaml`` is the single, inspectable source — relabel the demo by editing it.
"""

from __future__ import annotations

from importlib import resources
from typing import TYPE_CHECKING

import yaml

from .model import Graph
from .visibility import DEFAULT_VISIBILITY, compose

if TYPE_CHECKING:
    from .chunk import Chunk


def load_labels() -> dict[str, str]:
    """Load the packaged synthetic label map as ``{entity-id: tier}``.

    Entity ids are byte-identical to the slice-1 ``normalize`` output (== graph node ids ==
    chunk owning ids). Anything not listed defaults to ``public`` at lookup time.
    """
    text = resources.files("graphrag").joinpath("labels.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    raw = data.get("labels", {}) if isinstance(data, dict) else {}
    return {str(k): str(v) for k, v in raw.items()}


def label_graph(graph: Graph, labels: dict[str, str]) -> None:
    """Stamp node + edge ``visibility`` props in place from the label map (mutates ``graph``).

    Node visibility = its label (default ``public``). Edge visibility = ``compose(src, dst)``
    — the most-restrictive of its endpoints — so "edge traversable" ≡ "both endpoints
    visible", which is what makes the during-traversal edge filter the node guarantee.
    Assigned (not ``setdefault``-merged), so a re-ingest always recomputes a fresh label.
    """
    for node in graph.nodes.values():
        node.props["visibility"] = labels.get(node.id, DEFAULT_VISIBILITY)
    for edge in graph.edges:
        src = labels.get(edge.src_id, DEFAULT_VISIBILITY)
        dst = labels.get(edge.dst_id, DEFAULT_VISIBILITY)
        edge.props["visibility"] = compose(src, dst)


def label_chunks(chunks: list[Chunk], labels: dict[str, str]) -> None:
    """Stamp each chunk's ``visibility`` in place = ``compose`` of its owning entities' tiers.

    A chunk with no owning entities composes to ``public``.
    """
    for chunk in chunks:
        chunk.visibility = compose(
            *(labels.get(entity_id, DEFAULT_VISIBILITY) for entity_id in chunk.entity_ids)
        )
