"""T1 — the governed template registry + static governance lint (AC1).

Every template in the fixed library must be read-only, bind every value through a
declared ``$param`` (no value interpolated into the query string), and have its declared
param slots match its ``$placeholders`` exactly. T2's dual-form execution identity (AC2)
is tested in ``test_templates_exec.py``.

# STUB: AC1
"""

from __future__ import annotations

from graphrag.templates import TEMPLATE_BY_ID, TEMPLATES, get_template


def test_registry_has_at_least_four_templates() -> None:
    assert len(TEMPLATES) >= 4
    # ids are unique and round-trip through the lookup map + accessor.
    assert len(TEMPLATE_BY_ID) == len(TEMPLATES)
    for t in TEMPLATES:
        assert get_template(t.id) is t
    assert get_template("does-not-exist") is None  # the selector-validation gate


def test_every_template_is_read_only() -> None:
    # The executable surface is a fixed library of bounded reads: no mutating clause or
    # procedure call may appear in any template (the governance contract, AC1).
    for t in TEMPLATES:
        assert t.is_read_only(), f"template {t.id} is not read-only: {t.cypher!r}"
        upper = t.cypher.upper()
        for kw in ("CREATE", "MERGE", "DELETE", "SET", "REMOVE", "DETACH", "DROP", "CALL"):
            assert f" {kw} " not in f" {upper} ", f"{t.id} contains mutating {kw}"
        # it reads (MATCH) and returns rows under the conventional alias n.
        assert "MATCH" in upper
        assert "RETURN N" in upper


def test_declared_params_match_placeholders_and_are_bound() -> None:
    # Every declared param appears as a $placeholder, and every $placeholder is declared —
    # so no value is interpolated and no slot is unfilled (AC1).
    for t in TEMPLATES:
        assert t.placeholders() == t.param_names(), (
            f"{t.id}: placeholders {t.placeholders()} != declared {t.param_names()}"
        )
        assert t.params, f"{t.id} declares no parameters"


def test_no_user_value_interpolation_token() -> None:
    # Defense-in-depth: a Python interpolation marker in a template string would mean a
    # value was (or could be) built into the query text rather than bound. The literal
    # cypher must carry none of %s / %( / str.format placeholders.
    for t in TEMPLATES:
        assert "%s" not in t.cypher and "%(" not in t.cypher, f"{t.id} has a %-format marker"
        assert ".format(" not in t.cypher, f"{t.id} has a .format( marker"


def test_param_specs_are_typed_consistently() -> None:
    for t in TEMPLATES:
        for p in t.params:
            assert p.kind in ("entity", "enum", "int")
            if p.kind == "entity":
                assert p.entity_kind is not None, f"{t.id}.{p.name}: entity slot needs entity_kind"
            if p.kind == "enum":
                assert p.choices, f"{t.id}.{p.name}: enum slot needs choices"
