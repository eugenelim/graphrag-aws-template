"""Extraction — turn ParsedDocs into entities and edges.

Extraction *emits* nodes/edges (duplicate IDs across the two sources are expected
and fine); the resolution merge happens when they are upserted into a ``Graph``
(see ``resolve.py``). IDs are computed via ``normalize`` so a shared entity gets
one ID from either source — that is what makes cross-source resolution fall out of
a plain upsert instead of needing a model.
"""

from __future__ import annotations

import re

from .model import Edge, EdgeKind, EntityKind, Node
from .normalize import kep_id, person_id, sig_id, subproject_id
from .sources import COMMUNITY, ENHANCEMENTS, ParsedDoc

# Matches a prose author line in a legacy KEP body, e.g. "**Authors:** Tim Hockin".
_PROSE_AUTHORS = re.compile(r"(?im)^\**\s*authors?\**\s*:\s*(.+?)\s*$")


def _persons(raw_list: object, aliases: dict[str, str]) -> list[tuple[str, str]]:
    """Normalize a list of handle/name strings to ``(id, raw)`` pairs."""
    out: list[tuple[str, str]] = []
    if isinstance(raw_list, list):
        for item in raw_list:
            if isinstance(item, str) and item.strip():
                out.append((person_id(item, aliases), item.strip()))
    return out


def extract(docs: list[ParsedDoc], aliases: dict[str, str]) -> tuple[list[Node], list[Edge]]:
    """Extract all entities and edges from the parsed corpus.

    Each node/edge is stamped with its originating document's ``doc_id`` (slice-5 provenance)
    — the reference-count the incremental delta's orphan pass reads. Stamping at the loop
    level (every node/edge a doc emitted) keeps the per-kind extractors unchanged.
    """
    nodes: list[Node] = []
    edges: list[Edge] = []

    for doc in docs:
        n_start, e_start = len(nodes), len(edges)
        if doc.kind == "sigs_index":
            _extract_sigs_index(doc, aliases, nodes, edges)
        elif doc.kind == "sig_readme":
            _extract_sig_readme(doc, nodes)
        elif doc.kind == "kep_yaml":
            _extract_kep_yaml(doc, aliases, nodes, edges)
        elif doc.kind == "kep_readme":
            _extract_kep_readme(doc, aliases, nodes, edges)
        for node in nodes[n_start:]:
            node.doc_paths.add(doc.doc_id)
        for edge in edges[e_start:]:
            edge.doc_paths.add(doc.doc_id)
    return nodes, edges


def _extract_sigs_index(
    doc: ParsedDoc, aliases: dict[str, str], nodes: list[Node], edges: list[Edge]
) -> None:
    for sig in doc.payload.get("sigs", []) or []:
        if not isinstance(sig, dict):
            continue
        slug = sig.get("dir")
        if not isinstance(slug, str):
            continue
        sid = sig_id(slug)
        nodes.append(
            Node(
                sid,
                EntityKind.SIG,
                props={"slug": slug, "label": sig.get("label"), "name": sig.get("name")},
                sources={COMMUNITY},
            )
        )
        leadership = sig.get("leadership") or {}
        for role, edge_kind in (("chairs", EdgeKind.CHAIRS), ("tech_leads", EdgeKind.TECH_LEADS)):
            for member in leadership.get(role, []) or []:
                if not isinstance(member, dict):
                    continue
                handle = member.get("github")
                if not isinstance(handle, str):
                    continue
                pid = person_id(handle, aliases)
                props = {"name": member["name"]} if member.get("name") else {}
                nodes.append(Node(pid, EntityKind.PERSON, props=props, sources={COMMUNITY}))
                edges.append(Edge(pid, sid, edge_kind, sources={COMMUNITY}))

        for sub in sig.get("subprojects", []) or []:
            if not isinstance(sub, dict) or not isinstance(sub.get("name"), str):
                continue
            sub_id = subproject_id(slug, sub["name"])
            nodes.append(
                Node(
                    sub_id, EntityKind.SUBPROJECT, props={"name": sub["name"]}, sources={COMMUNITY}
                )
            )
            edges.append(Edge(sid, sub_id, EdgeKind.HAS_SUBPROJECT, sources={COMMUNITY}))


def _extract_sig_readme(doc: ParsedDoc, nodes: list[Node]) -> None:
    """Enrich the SIG node with its charter title from the README front matter."""
    slug = doc.payload.get("slug")
    if not isinstance(slug, str) or doc.markdown is None:
        return
    title = doc.markdown.front_matter.get("title")
    props: dict[str, object] = {"charter_title": title} if isinstance(title, str) else {}
    nodes.append(Node(sig_id(slug), EntityKind.SIG, props=props, sources={COMMUNITY}))


def _extract_kep_yaml(
    doc: ParsedDoc, aliases: dict[str, str], nodes: list[Node], edges: list[Edge]
) -> None:
    p = doc.payload
    number = p.get("kep-number", p.get("dir_number"))
    kid = kep_id(str(number))
    nodes.append(
        Node(
            kid,
            EntityKind.KEP,
            props={"title": p.get("title"), "status": p.get("status")},
            sources={ENHANCEMENTS},
        )
    )
    owning = p.get("owning-sig") or p.get("owning_sig_dir")
    if isinstance(owning, str):
        edges.append(Edge(sig_id(owning), kid, EdgeKind.OWNS, sources={ENHANCEMENTS}))
        nodes.append(Node(sig_id(owning), EntityKind.SIG, sources={ENHANCEMENTS}))

    for pid, _raw in _persons(p.get("authors"), aliases):
        nodes.append(Node(pid, EntityKind.PERSON, sources={ENHANCEMENTS}))
        edges.append(Edge(pid, kid, EdgeKind.AUTHORS, sources={ENHANCEMENTS}))
    for pid, _raw in _persons(p.get("approvers"), aliases):
        nodes.append(Node(pid, EntityKind.PERSON, sources={ENHANCEMENTS}))
        edges.append(Edge(pid, kid, EdgeKind.APPROVES, sources={ENHANCEMENTS}))


def _extract_kep_readme(
    doc: ParsedDoc, aliases: dict[str, str], nodes: list[Node], edges: list[Edge]
) -> None:
    p = doc.payload
    md = doc.markdown
    number = p.get("dir_number")
    if number is None:  # defensive — load_enhancements always sets dir_number
        return
    kid = kep_id(str(number))
    title = md.headings[0].lstrip("# ").strip() if md and md.headings else None
    nodes.append(Node(kid, EntityKind.KEP, props={"title": title}, sources={ENHANCEMENTS}))

    # A legacy KEP has no kep.yaml: its owning SIG comes from the path and its
    # author(s) from prose (resolved through the alias table).
    if not p.get("has_kep_yaml"):
        owning = p.get("owning_sig_dir")
        if isinstance(owning, str):
            edges.append(Edge(sig_id(owning), kid, EdgeKind.OWNS, sources={ENHANCEMENTS}))
            nodes.append(Node(sig_id(owning), EntityKind.SIG, sources={ENHANCEMENTS}))
        if md is not None:
            m = _PROSE_AUTHORS.search(md.body)
            if m:
                cleaned = re.sub(r"[*`]", "", m.group(1))  # drop Markdown emphasis
                for name in (n.strip() for n in cleaned.split(",")):
                    if name:
                        pid = person_id(name, aliases)
                        nodes.append(Node(pid, EntityKind.PERSON, sources={ENHANCEMENTS}))
                        edges.append(Edge(pid, kid, EdgeKind.AUTHORS, sources={ENHANCEMENTS}))
