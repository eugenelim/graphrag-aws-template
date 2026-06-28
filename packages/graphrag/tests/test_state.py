"""Tests for `IngestState` v2 — JSON round-trip, v1→v2 upgrade, and the v1 manifest projection.

`IngestState` is the v1 manifest (`{doc_id: content_hash}`) widened with per-document Silver
keys, config fingerprints, and a stage watermark (medallion-staging T1). A v1 envelope upgrades
in with no migration script (Silver cold, stage=bronze), and `as_manifest()` projects back to the
exact v1 dict so `diff_manifests` is reused unchanged.
"""

from __future__ import annotations

from graphrag.delta import diff_manifests
from graphrag.state import DocState, IngestState, Stage, from_json, to_json


def _v2_state() -> IngestState:
    return IngestState(
        docs={
            "community/sig-network/README.md": DocState(
                content_hash="aaaa",
                stage=Stage.GOLD,
                silver_chunks="silver/embfp/aaaa/chunks.json",
                silver_candidates="silver/extfp/aaaa/candidates.json",
            ),
            "enhancements/keps/sig-node/1287/kep.yaml": DocState(
                content_hash="bbbb", stage=Stage.BRONZE
            ),
        },
        fingerprints={"embedder": "embfp", "extraction": "extfp"},
        ingested_commit="deadbeef",
    )


def test_v2_json_round_trip_is_identity() -> None:
    state = _v2_state()
    assert from_json(to_json(state)) == state


def test_v1_envelope_upgrades_in_silver_cold_stage_bronze() -> None:
    v1_text = '{"version": 1, "docs": {"community/a/README.md": "h1", "x/b.yaml": "h2"}}'
    state = from_json(v1_text)
    assert state.version == 2
    assert set(state.docs) == {"community/a/README.md", "x/b.yaml"}
    for ds in state.docs.values():
        assert ds.stage is Stage.BRONZE
        assert ds.silver_chunks is None
        assert ds.silver_candidates is None
    assert state.docs["community/a/README.md"].content_hash == "h1"


def test_as_manifest_reproduces_the_exact_v1_dict() -> None:
    v1_docs = {"community/a/README.md": "h1", "x/b.yaml": "h2"}
    v1_text = '{"version": 1, "docs": {"community/a/README.md": "h1", "x/b.yaml": "h2"}}'
    state = from_json(v1_text)
    assert state.as_manifest() == v1_docs


def test_as_manifest_feeds_diff_manifests_and_classifies_a_move() -> None:
    # The projection is exercised THROUGH diff_manifests (not just dict-compared): the same
    # content hash at a new path must classify as a move (AC3, AC4).
    prev = from_json('{"version": 1, "docs": {"src/old/path.md": "samehash"}}')
    new = from_json('{"version": 1, "docs": {"src/new/path.md": "samehash"}}')
    delta = diff_manifests(prev.as_manifest(), new.as_manifest())
    assert delta.moved == [("src/old/path.md", "src/new/path.md")]
    assert not delta.added and not delta.deleted and not delta.changed


def test_empty_text_is_an_empty_state() -> None:
    state = from_json("")
    assert state == IngestState()
    assert state.version == 2
    assert state.as_manifest() == {}


def test_v2_round_trip_preserves_fingerprints_and_commit() -> None:
    state = from_json(to_json(_v2_state()))
    assert state.fingerprints == {"embedder": "embfp", "extraction": "extfp"}
    assert state.ingested_commit == "deadbeef"
