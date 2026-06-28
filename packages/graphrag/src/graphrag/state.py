"""`IngestState` — the v1 manifest widened for medallion staging (T1).

The v1 manifest (`delta.py`) maps `doc_id -> content_hash` and is the record of "what is
ingested". `IngestState` is the **backward-compatible** widening: the same per-document content
hash plus per-document Silver artifact keys, the config fingerprints the Silver artifacts were
computed at, and a per-document stage watermark (Bronze -> Silver -> Gold). A v1 envelope upgrades
in with **no migration script** (Silver cold, stage = bronze), and `as_manifest()` projects an
`IngestState` back to the exact v1 `{doc_id: content_hash}` dict so `delta.diff_manifests` is
reused unchanged.

Like `delta.py` this is **ingest-path only** — it must never be imported by the PyYAML-free query
Lambda. The on-disk shape is a versioned JSON envelope, mirroring `delta.manifest_to_json`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum

INGEST_STATE_VERSION = 2


class Stage(StrEnum):
    """The medallion stage watermark a document has reached (RFC-0003)."""

    BRONZE = "bronze"  # raw parsed + content-hashed
    SILVER = "silver"  # per-doc chunks+vectors / candidate triples materialized + cached
    GOLD = "gold"  # resolved, grounded, and reconciled into the store


def _opt_str(value: object) -> str | None:
    """Coerce a JSON value to ``str | None`` (a Silver key is a string or absent)."""
    return None if value is None else str(value)


@dataclass
class DocState:
    """Per-document ingest state: its content hash, stage watermark, and Silver artifact keys.

    ``silver_chunks`` / ``silver_candidates`` are the S3 keys of the document's cached Silver
    artifacts (``None`` until materialized). They are content+config addressed, so a key encodes
    the fingerprint it was computed at — a config change yields a different key and so a cache miss.
    """

    content_hash: str
    stage: Stage = Stage.BRONZE
    silver_chunks: str | None = None
    silver_candidates: str | None = None


@dataclass
class IngestState:
    """The widened ingest manifest — v2 of `delta`'s `{doc_id: content_hash}` record.

    ``fingerprints`` records the embedder / extraction config fingerprints the state was last
    computed at (a convenience watermark; the per-document Silver keys are the authoritative
    addressing). ``ingested_commit`` is the optional "ingested commit" watermark, carried for
    forward-compatibility and serialized even when ``None``.
    """

    docs: dict[str, DocState] = field(default_factory=dict)
    fingerprints: dict[str, str] = field(default_factory=dict)
    ingested_commit: str | None = None
    version: int = INGEST_STATE_VERSION

    def as_manifest(self) -> dict[str, str]:
        """Project to the exact v1 `{doc_id: content_hash}` manifest (reuses `diff_manifests`)."""
        return {doc_id: ds.content_hash for doc_id, ds in self.docs.items()}


def to_json(state: IngestState) -> str:
    """Serialize an `IngestState` to its versioned JSON envelope (mirrors `manifest_to_json`)."""
    return json.dumps(
        {
            "version": state.version,
            "fingerprints": dict(sorted(state.fingerprints.items())),
            "ingested_commit": state.ingested_commit,
            "docs": {
                doc_id: {
                    "content_hash": ds.content_hash,
                    "stage": ds.stage.value,
                    "silver_chunks": ds.silver_chunks,
                    "silver_candidates": ds.silver_candidates,
                }
                for doc_id, ds in sorted(state.docs.items())
            },
        },
        indent=2,
        sort_keys=False,
    )


def _upgrade_v1_docs(docs_raw: dict[str, object]) -> dict[str, DocState]:
    """Upgrade a v1 `{doc_id: content_hash}` docs map: Silver cold, stage = bronze."""
    return {str(k): DocState(content_hash=str(v), stage=Stage.BRONZE) for k, v in docs_raw.items()}


def from_json(text: str) -> IngestState:
    """Parse an `IngestState` JSON envelope, upgrading a v1 manifest in (empty for blank input)."""
    if not text.strip():
        return IngestState()
    data = json.loads(text)
    if not isinstance(data, dict):
        return IngestState()
    docs_raw = data.get("docs", {})
    docs_raw = docs_raw if isinstance(docs_raw, dict) else {}

    if data.get("version") != INGEST_STATE_VERSION:
        # v1 (or any pre-v2) envelope: docs values are bare content-hash strings.
        return IngestState(docs=_upgrade_v1_docs(docs_raw))

    docs: dict[str, DocState] = {}
    for k, v in docs_raw.items():
        if isinstance(v, dict):
            docs[str(k)] = DocState(
                content_hash=str(v.get("content_hash", "")),
                stage=Stage(str(v.get("stage", Stage.BRONZE.value))),
                silver_chunks=_opt_str(v.get("silver_chunks")),
                silver_candidates=_opt_str(v.get("silver_candidates")),
            )
        else:  # tolerate a stray v1-shaped entry inside a v2 envelope
            docs[str(k)] = DocState(content_hash=str(v), stage=Stage.BRONZE)

    fingerprints_raw = data.get("fingerprints", {})
    fingerprints = (
        {str(a): str(b) for a, b in fingerprints_raw.items()}
        if isinstance(fingerprints_raw, dict)
        else {}
    )
    return IngestState(
        docs=docs, fingerprints=fingerprints, ingested_commit=_opt_str(data.get("ingested_commit"))
    )
