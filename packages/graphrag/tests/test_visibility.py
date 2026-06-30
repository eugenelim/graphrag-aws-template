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
    DEFAULT_DENY_PERSONA,
    PERSONAS,
    Clearance,
    Visibility,
    compose,
    rank,
    resolve_clearance,
    resolve_clearance_or_default_deny,
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


# --- security-hardening-followups: opt-in default-deny clearance (AC7) ----------------------


def test_default_deny_no_principal_sees_nothing() -> None:
    # The fail-open->fail-closed inversion: with default-deny on and no principal (None or ""),
    # resolution returns the EMPTY Clearance (sees nothing), not None (unrestricted). The persona
    # field is required, so the sentinel value is pinned.
    for absent in (None, ""):
        c = resolve_clearance_or_default_deny(absent, default_deny=True)
        assert c == Clearance(persona=DEFAULT_DENY_PERSONA, allowed=frozenset())
        for tier in (v.value for v in Visibility):
            assert not c.allows(tier)


def test_default_deny_unknown_persona_still_raises() -> None:
    # The existing fail-closed raise is preserved under default-deny — never a silent deny.
    with pytest.raises(ValueError, match="unknown persona"):
        resolve_clearance_or_default_deny("root", default_deny=True)


def test_default_deny_known_persona_is_normal_clearance() -> None:
    # A present, known persona resolves to its normal clearance regardless of the flag.
    assert resolve_clearance_or_default_deny("member", default_deny=True) == resolve_clearance(
        "member"
    )


def test_default_deny_off_is_byte_identical_to_today() -> None:
    # Flag off: no principal => None (unrestricted, today's fail-open default); a present persona
    # resolves exactly as resolve_clearance — every shipped mode is byte-unchanged.
    assert resolve_clearance_or_default_deny(None, default_deny=False) is None
    assert resolve_clearance_or_default_deny("", default_deny=False) is None
    assert resolve_clearance_or_default_deny("maintainer", default_deny=False) == resolve_clearance(
        "maintainer"
    )


def test_default_deny_precedence_present_persona_ignores_flag() -> None:
    # The flag governs ONLY the absent-principal cell: a present persona resolves the same with
    # the flag on or off (unknown => raise either way; known => same clearance either way).
    for flag in (True, False):
        assert resolve_clearance_or_default_deny(
            "public-reader", default_deny=flag
        ) == resolve_clearance("public-reader")
        with pytest.raises(ValueError):
            resolve_clearance_or_default_deny("nope", default_deny=flag)


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
