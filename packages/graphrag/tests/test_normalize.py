"""T1 — normalization. # STUB: AC4 (the resolution merge key)."""

from __future__ import annotations

from graphrag.normalize import (
    kep_id,
    normalize_handle,
    normalize_slug,
    person_id,
    sig_id,
)


def test_handle_strips_at_and_lowercases() -> None:
    assert normalize_handle("@Thockin") == "thockin"
    assert normalize_handle("thockin") == "thockin"
    assert normalize_handle("@SergeyKanzhelev") == "sergeykanzhelev"


def test_handle_applies_alias_before_normalizing() -> None:
    aliases = {"tim hockin": "thockin"}
    assert normalize_handle("Tim Hockin", aliases) == "thockin"
    # A name absent from the alias table is normalized mechanically, not merged.
    assert normalize_handle("Some Person", aliases) == "some person"


def test_slug_folds_label_and_slug_to_same_value() -> None:
    assert normalize_slug("sig-network") == "sig-network"
    assert normalize_slug("SIG Network") == "sig-network"
    assert normalize_slug("Network") == "sig-network"


def test_stable_ids() -> None:
    assert kep_id(1287) == "kep-1287"
    assert kep_id("2086") == "kep-2086"
    assert person_id("@thockin") == "person:thockin"
    assert sig_id("Network") == "sig:sig-network"
