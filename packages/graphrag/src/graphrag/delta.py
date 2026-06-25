"""Incremental-delta detection — content hash, the ingest manifest, and the diff (slice 5).

The **manifest** (`doc id → content hash`) is the record of "what is ingested" — the
design's "ingested commit". A delta diffs a new snapshot's manifest against the stored one
and classifies every changed document as exactly one of **add / change / delete / move**,
where a *move* is the same content hash appearing at a new path. This is the no-NAT,
S3-consistent detection source (ADR-0002): it never needs a live `git clone`.

Doc ids are `{source}/{path}` — byte-identical to the document-provenance carried on graph
nodes/edges (`model.py`) and to the source-qualified chunk identity (`chunk.py`), so the
same key threads the manifest, the graph provenance, and the vector chunk delete.

This module is **ingest-path only** (it imports `sources`/`parse`, hence PyYAML); it must
never be imported by the PyYAML-free query Lambda.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .sources import COMMUNITY, ParsedDoc, load_corpus

MANIFEST_VERSION = 1

# A manifest maps a source-qualified doc id (`{source}/{path}`) to its content hash.
Manifest = dict[str, str]


def content_hash(data: bytes) -> str:
    """The stable content hash of a document's raw bytes (sha256 hex)."""
    return hashlib.sha256(data).hexdigest()


def doc_id(source: str, path: str) -> str:
    """The source-qualified stable key used across the manifest, graph provenance, and chunks."""
    return f"{source}/{path}"


def manifest_from_docs(
    docs: list[ParsedDoc], community_root: Path, enhancements_root: Path
) -> Manifest:
    """Hash the raw file of every parsed doc, keyed by its source-qualified doc id.

    Built from the *same* ``ParsedDoc`` list a delta re-uses for extraction, so the manifest
    covers exactly the documents that are ingested — no phantom entries for files the parse
    skips.
    """
    out: Manifest = {}
    for doc in docs:
        root = community_root if doc.source == COMMUNITY else enhancements_root
        out[doc_id(doc.source, doc.path)] = content_hash((root / doc.path).read_bytes())
    return out


def build_manifest(community_root: Path, enhancements_root: Path) -> Manifest:
    """Parse the corpus and produce its manifest (the full-ingest / standalone path)."""
    return manifest_from_docs(
        load_corpus(community_root, enhancements_root), community_root, enhancements_root
    )


@dataclass
class Delta:
    """The classified document delta between a previous and a new manifest."""

    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    moved: list[tuple[str, str]] = field(default_factory=list)  # (old_id, new_id)

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.changed or self.deleted or self.moved)

    def added_doc_ids(self) -> set[str]:
        """Doc ids whose content must be (re-)ingested: added + changed + move targets."""
        return set(self.added) | set(self.changed) | {new for _old, new in self.moved}

    def removed_doc_ids(self) -> set[str]:
        """Doc ids whose contribution must be stripped: deleted + changed + move sources."""
        return set(self.deleted) | set(self.changed) | {old for old, _new in self.moved}


def diff_manifests(old: Manifest | None, new: Manifest) -> Delta:
    """Classify the document delta between ``old`` and ``new`` as add/change/delete/move.

    ``old=None`` (no prior manifest) treats every document as added. A **move** is detected
    among the symmetric-difference paths as the same content hash appearing at a new path
    (a renamed-and-edited file has a different hash, so it falls back to delete+add).
    """
    if old is None:
        return Delta(added=sorted(new))

    old_keys, new_keys = set(old), set(new)
    added_paths = new_keys - old_keys
    deleted_paths = old_keys - new_keys
    changed = sorted(p for p in old_keys & new_keys if old[p] != new[p])

    # Move detection: pair a deleted path with an added path that shares its content hash.
    added_by_hash: dict[str, list[str]] = defaultdict(list)
    for p in added_paths:
        added_by_hash[new[p]].append(p)
    deleted_by_hash: dict[str, list[str]] = defaultdict(list)
    for p in deleted_paths:
        deleted_by_hash[old[p]].append(p)

    moved: list[tuple[str, str]] = []
    for h, old_paths in deleted_by_hash.items():
        new_paths = added_by_hash.get(h, [])
        for old_p, new_p in zip(sorted(old_paths), sorted(new_paths), strict=False):
            moved.append((old_p, new_p))
    moved_old = {o for o, _ in moved}
    moved_new = {n for _, n in moved}

    return Delta(
        added=sorted(added_paths - moved_new),
        changed=changed,
        deleted=sorted(deleted_paths - moved_old),
        moved=sorted(moved),
    )


def manifest_to_json(manifest: Manifest) -> str:
    """Serialize a manifest to the versioned JSON envelope persisted to S3."""
    return json.dumps(
        {"version": MANIFEST_VERSION, "docs": dict(sorted(manifest.items()))},
        indent=2,
        sort_keys=False,
    )


def manifest_from_json(text: str) -> Manifest:
    """Parse a manifest JSON envelope back to a ``Manifest`` (empty for empty/blank input)."""
    if not text.strip():
        return {}
    data = json.loads(text)
    docs = data.get("docs", {}) if isinstance(data, dict) else {}
    return {str(k): str(v) for k, v in docs.items()}
