"""T1 — visibility tier model + persona/clearance resolution (AC1).

Pure functions over constants: ordering, most-restrictive-wins compose, persona
resolution, fail-closed semantics, and the PyYAML-free import guarantee.

# STUB: AC1
"""

from __future__ import annotations

import builtins
import importlib
import sys
from typing import Any

import pytest

from graphrag.visibility import (
    PERSONAS,
    Clearance,
    Visibility,
    compose,
    rank,
    resolve_clearance,
)


def test_tier_ordering() -> None:
    assert rank(Visibility.PUBLIC.value) < rank(Visibility.INTERNAL.value)
    assert rank(Visibility.INTERNAL.value) < rank(Visibility.RESTRICTED.value)


def test_compose_is_most_restrictive() -> None:
    assert compose("public", "restricted") == "restricted"
    assert compose("public", "internal") == "internal"
    assert compose("internal", "restricted") == "restricted"
    assert compose("public", "public") == "public"


def test_compose_empty_is_public_default() -> None:
    assert compose() == "public"


def test_compose_unknown_label_treated_as_public_rank() -> None:
    # An unknown tier must not silently outrank a real one (safe default).
    assert compose("public", "bogus") == "public"


def test_resolve_clearance_personas() -> None:
    assert resolve_clearance("public-reader").allowed == frozenset({"public"})
    assert resolve_clearance("member").allowed == frozenset({"public", "internal"})
    assert resolve_clearance("maintainer").allowed == frozenset(
        {"public", "internal", "restricted"}
    )


def test_clearance_allows_within_not_above() -> None:
    member = resolve_clearance("member")
    assert member.allows("public")
    assert member.allows("internal")
    assert not member.allows("restricted")


def test_every_shipped_clearance_is_downward_closed() -> None:
    # If a tier is allowed, every less-restrictive tier is too — the property the
    # edge-filter equivalence (edge.vis = max(endpoints)) rests on.
    for persona in PERSONAS:
        allowed = resolve_clearance(persona).allowed
        max_rank = max((rank(t) for t in allowed), default=0)
        assert allowed == frozenset(v.value for v in Visibility if rank(v.value) <= max_rank)


def test_unknown_persona_raises_valueerror() -> None:
    with pytest.raises(ValueError, match="unknown persona"):
        resolve_clearance("root")


def test_empty_clearance_sees_nothing_fail_closed() -> None:
    # An empty allowed-set is fail-closed: it filters everything, never falls through to
    # unrestricted (only the query layer's literal clearance=None means unrestricted).
    locked = Clearance(persona="locked", allowed=frozenset())
    assert not locked.allows("public")
    assert not locked.allows("restricted")


def test_visibility_module_imports_no_yaml() -> None:
    """The query Lambda (PyYAML-free) imports ``visibility``; it must never pull in yaml."""
    real_import = builtins.__import__

    def _blocking(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "yaml" or name.startswith("yaml."):
            raise ImportError("yaml must not be imported by graphrag.visibility")
        return real_import(name, *args, **kwargs)

    def _is_target(mod: str) -> bool:
        return mod == "yaml" or mod.startswith("yaml.") or mod == "graphrag.visibility"

    saved = {m: sys.modules.pop(m) for m in list(sys.modules) if _is_target(m)}
    builtins.__import__ = _blocking
    try:
        importlib.import_module("graphrag.visibility")
    finally:
        builtins.__import__ = real_import
        for m in [m for m in list(sys.modules) if _is_target(m)]:
            del sys.modules[m]
        sys.modules.update(saved)
