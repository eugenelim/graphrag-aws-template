"""Tests for the delta module — content hash, manifest, and add/change/delete/move diff."""

from __future__ import annotations

from pathlib import Path

from graphrag.delta import (
    build_manifest,
    content_hash,
    diff_manifests,
    manifest_from_json,
    manifest_to_json,
)


def test_content_hash_stable_and_sensitive() -> None:
    assert content_hash(b"hello") == content_hash(b"hello")
    assert content_hash(b"hello") != content_hash(b"hellp")  # 1-byte change


def test_build_manifest_keys_match_doc_ids(community_root: Path, enhancements_root: Path) -> None:
    manifest = build_manifest(community_root, enhancements_root)
    # Every key is a source-qualified doc id, and the structural + prose docs are present.
    assert "community/sigs.yaml" in manifest
    assert any(k.startswith("community/") and k.endswith("/README.md") for k in manifest)
    assert any(k.endswith("/kep.yaml") for k in manifest)
    assert all(k.startswith(("community/", "enhancements/")) for k in manifest)
    assert all(isinstance(v, str) and len(v) == 64 for v in manifest.values())  # sha256 hex


def test_diff_classifies_added_changed_deleted() -> None:
    old = {"community/a": "h1", "community/b": "h2", "community/c": "h3"}
    new = {"community/a": "h1", "community/b": "h2_changed", "community/d": "h4"}
    delta = diff_manifests(old, new)
    assert delta.added == ["community/d"]
    assert delta.changed == ["community/b"]
    assert delta.deleted == ["community/c"]
    assert delta.moved == []


def test_diff_detects_move_as_same_hash_new_path() -> None:
    old = {"enhancements/keps/x/README.md": "hX"}
    new = {"enhancements/keps/y/README.md": "hX"}  # same content, new path
    delta = diff_manifests(old, new)
    assert delta.moved == [("enhancements/keps/x/README.md", "enhancements/keps/y/README.md")]
    assert delta.added == []
    assert delta.deleted == []
    assert delta.changed == []


def test_move_plus_edit_is_delete_plus_add_not_move() -> None:
    # Path changed AND content changed → hash differs → not a move.
    old = {"enhancements/keps/x/README.md": "hX"}
    new = {"enhancements/keps/y/README.md": "hX_edited"}
    delta = diff_manifests(old, new)
    assert delta.moved == []
    assert delta.added == ["enhancements/keps/y/README.md"]
    assert delta.deleted == ["enhancements/keps/x/README.md"]


def test_empty_delta_when_unchanged() -> None:
    m = {"community/a": "h1", "community/b": "h2"}
    delta = diff_manifests(m, dict(m))
    assert delta.is_empty


def test_diff_against_none_prev_treats_all_as_added() -> None:
    new = {"community/a": "h1", "community/b": "h2"}
    delta = diff_manifests(None, new)
    assert sorted(delta.added) == ["community/a", "community/b"]
    assert delta.changed == [] and delta.deleted == [] and delta.moved == []


def test_manifest_json_round_trip() -> None:
    m = {"community/sigs.yaml": "h1", "enhancements/keps/x/kep.yaml": "h2"}
    restored = manifest_from_json(manifest_to_json(m))
    assert restored == m


def test_manifest_from_json_handles_empty_or_missing() -> None:
    assert manifest_from_json("") == {}
    assert manifest_from_json('{"version": 1, "docs": {}}') == {}
