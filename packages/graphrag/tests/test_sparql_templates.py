"""T-SPARQL-TMPL — Named SPARQL template registry governance and execution tests.

Mirrors test_templates.py for the SPARQL registry:
- T1: registry contents and accessor API
- T2: every template is read-only (SPARQL mutation denylist)
- T3: no string-interpolation markers in any template
- T4: declared params each appear as ?varname in the SPARQL body
- T5: execute() returns rows against the fixture corpus via initBindings
- T6: injection safety — a domain value containing SPARQL payload characters is handled safely
- T7: module-level execute_template() dispatch
"""

from __future__ import annotations

import pathlib

import pytest
import rdflib

from graphrag.sparql_templates import (
    SPARQL_TEMPLATE_BY_ID,
    SPARQL_TEMPLATES,
    execute_template,
    get_sparql_template,
)

# ---------------------------------------------------------------------------
# Fixture: load the biz_ops_fixture.ttl corpus into an rdflib Dataset
# ---------------------------------------------------------------------------

FIXTURE_TTL = pathlib.Path(__file__).parent / "fixtures" / "biz_ops_fixture.ttl"


@pytest.fixture(scope="module")
def corpus() -> rdflib.Dataset:
    """rdflib Dataset loaded from the biz_ops_fixture corpus (TriG format)."""
    ds = rdflib.Dataset()
    ds.parse(str(FIXTURE_TTL), format="trig")
    return ds


# ---------------------------------------------------------------------------
# T1: registry contents and accessor API
# ---------------------------------------------------------------------------


def test_registry_has_at_least_one_template() -> None:
    assert len(SPARQL_TEMPLATES) >= 1
    assert len(SPARQL_TEMPLATE_BY_ID) == len(SPARQL_TEMPLATES)
    for t in SPARQL_TEMPLATES:
        assert get_sparql_template(t.id) is t


def test_registry_contains_policies_by_domain() -> None:
    t = get_sparql_template("policies_by_domain")
    assert t is not None
    assert t.id == "policies_by_domain"
    assert t.description


def test_get_sparql_template_unknown_returns_none() -> None:
    assert get_sparql_template("does-not-exist") is None


def test_template_ids_are_unique() -> None:
    ids = [t.id for t in SPARQL_TEMPLATES]
    assert len(ids) == len(set(ids)), "Template ids are not unique"


# ---------------------------------------------------------------------------
# T2: every template is read-only (SPARQL mutation denylist)
# ---------------------------------------------------------------------------


def test_every_template_is_read_only() -> None:
    for t in SPARQL_TEMPLATES:
        assert t.is_read_only(), (
            f"Template {t.id!r} is not read-only: contains mutation keyword. SPARQL:\n{t.sparql}"
        )


_MUTATION_KEYWORDS = ("INSERT", "DELETE", "DROP", "CLEAR", "LOAD", "CREATE")


@pytest.mark.parametrize("kw", _MUTATION_KEYWORDS)
def test_no_mutation_keyword_in_any_template(kw: str) -> None:
    for t in SPARQL_TEMPLATES:
        assert kw not in t.sparql.upper(), f"Template {t.id!r} contains mutation keyword {kw!r}"


# ---------------------------------------------------------------------------
# T3: no string-interpolation markers in any template
# ---------------------------------------------------------------------------


def test_no_fstring_or_format_interpolation_in_templates() -> None:
    """Defense-in-depth: no f-string or .format() marker may appear in any
    template's sparql string — values must be bound via initBindings, never
    interpolated."""
    for t in SPARQL_TEMPLATES:
        # Curly-brace interpolation markers (f-string format-style)
        assert "{{" not in t.sparql and "}}" not in t.sparql, (
            f"Template {t.id!r} has escaped {{ }} braces — remove format-style markers"
        )
        # Python %-format markers
        assert "%s" not in t.sparql and "%(" not in t.sparql, (
            f"Template {t.id!r} has %-format marker"
        )
        # .format() method call
        assert ".format(" not in t.sparql, f"Template {t.id!r} has .format( marker"


# ---------------------------------------------------------------------------
# T4: declared params each appear as ?varname in the SPARQL body
# ---------------------------------------------------------------------------


def test_declared_params_appear_as_sparql_variables() -> None:
    """Every declared param name must appear as ?name in the SPARQL string."""
    for t in SPARQL_TEMPLATES:
        for param_name in t.param_names():
            assert f"?{param_name}" in t.sparql, (
                f"Template {t.id!r} declares param {param_name!r} "
                f"but ?{param_name} does not appear in the SPARQL"
            )


def test_all_templates_declare_at_least_one_param() -> None:
    for t in SPARQL_TEMPLATES:
        assert t.params, f"Template {t.id!r} declares no parameters"


# ---------------------------------------------------------------------------
# T5: execute() returns rows against the fixture corpus via initBindings
# ---------------------------------------------------------------------------


def test_policies_by_domain_hr_returns_one_row(corpus: rdflib.Dataset) -> None:
    """AC7: query(template_name='policies_by_domain', params={'domain': 'hr'})
    returns QueryResult with row_count > 0 against the fixture corpus."""
    t = get_sparql_template("policies_by_domain")
    assert t is not None
    rows = t.execute(corpus, {"domain": "hr"})
    assert len(rows) == 1, (
        f"Expected 1 row for domain=hr (hr-leave policy), got {len(rows)}: {rows}"
    )
    row = rows[0]
    assert "policy" in row
    assert "urn:biz:policy:hr-leave" in row["policy"]


def test_policies_by_domain_finance_returns_one_row(corpus: rdflib.Dataset) -> None:
    t = get_sparql_template("policies_by_domain")
    assert t is not None
    rows = t.execute(corpus, {"domain": "finance"})
    assert len(rows) == 1
    row = rows[0]
    assert "urn:biz:policy:expense-reimbursement" in row["policy"]


def test_policies_by_domain_unknown_domain_returns_empty(corpus: rdflib.Dataset) -> None:
    t = get_sparql_template("policies_by_domain")
    assert t is not None
    rows = t.execute(corpus, {"domain": "nonexistent_xyz"})
    assert rows == []


def test_policies_by_domain_rows_have_expected_keys(corpus: rdflib.Dataset) -> None:
    """Each result row must contain the SELECT variables: policy, name, scope."""
    t = get_sparql_template("policies_by_domain")
    assert t is not None
    rows = t.execute(corpus, {"domain": "hr"})
    assert len(rows) == 1
    row = rows[0]
    # Required fields (always present for hr-leave)
    assert "policy" in row
    assert "name" in row
    assert "scope" in row


# ---------------------------------------------------------------------------
# T6: injection safety — SPARQL payload characters in param values are safe
# ---------------------------------------------------------------------------


def test_injection_safety_sparql_payload_in_domain(corpus: rdflib.Dataset) -> None:
    """A domain value containing SPARQL injection characters must not corrupt
    the query or return unexpected rows (it should return 0 rows, not raise,
    and must not return fixture rows that don't match)."""
    t = get_sparql_template("policies_by_domain")
    assert t is not None

    injection_values = [
        # SPARQL comment injection attempt
        "hr} # injected",
        # SPARQL string escape attempt
        'hr" ; DROP GRAPH <urn:graph:normative> ; "',
        # Unicode apostrophe
        "hr's division",
        # FILTER bypass attempt
        "hr' OR '1'='1",
    ]

    for bad_val in injection_values:
        # Must not raise — injection via initBindings treats the value as a literal
        rows = t.execute(corpus, {"domain": bad_val})
        # None of these values match any fixture biz:scope, so rows must be empty.
        # If injection worked, it could return real rows — that would be a failure.
        assert rows == [], (
            f"Injection attempt {bad_val!r} returned non-empty rows: {rows} "
            "— initBindings may not be engaged"
        )


def test_injection_safety_does_not_raise_on_special_chars(corpus: rdflib.Dataset) -> None:
    """Regression: special SPARQL characters in the domain param must not raise
    an exception (rdflib initBindings escapes them)."""
    t = get_sparql_template("policies_by_domain")
    assert t is not None
    for special in ['"; DROP', "' UNION SELECT", "<script>", "}\n{"]:
        try:
            rows = t.execute(corpus, {"domain": special})
            assert isinstance(rows, list)
        except Exception as exc:
            pytest.fail(f"execute raised {type(exc).__name__} for domain={special!r}: {exc}")


# ---------------------------------------------------------------------------
# T7: module-level execute_template() dispatch
# ---------------------------------------------------------------------------


def test_execute_template_module_function(corpus: rdflib.Dataset) -> None:
    """execute_template() module-level function dispatches to the named template."""
    rows = execute_template("policies_by_domain", {"domain": "hr"}, corpus)
    assert len(rows) == 1


def test_execute_template_unknown_name_raises(corpus: rdflib.Dataset) -> None:
    with pytest.raises(KeyError):
        execute_template("nonexistent_template", {}, corpus)


# ---------------------------------------------------------------------------
# T8: required param enforcement
# ---------------------------------------------------------------------------


def test_execute_raises_when_required_param_missing(corpus: rdflib.Dataset) -> None:
    """execute() must raise ValueError when a required param is absent — not silently
    return empty rows (which is indistinguishable from 'no matching triples')."""
    t = get_sparql_template("policies_by_domain")
    assert t is not None
    with pytest.raises(ValueError, match="Required param"):
        t.execute(corpus, {})  # 'domain' is required


def test_execute_raises_missing_param_on_none_value(corpus: rdflib.Dataset) -> None:
    """Passing None explicitly for a required param must also raise ValueError."""
    t = get_sparql_template("policies_by_domain")
    assert t is not None
    with pytest.raises(ValueError, match="Required param"):
        t.execute(corpus, {"domain": None})


# ---------------------------------------------------------------------------
# T9: injection positive control
# ---------------------------------------------------------------------------


def test_injection_positive_control(corpus: rdflib.Dataset) -> None:
    """Positive control: bare 'hr' returns 1 row, while a payload that embeds 'hr'
    inside SPARQL syntax characters returns 0 rows — proving the FILTER treats the
    entire string as a single literal term and not as injectable SPARQL text."""
    t = get_sparql_template("policies_by_domain")
    assert t is not None

    # Positive: real scope value → 1 row
    rows_real = t.execute(corpus, {"domain": "hr"})
    assert len(rows_real) == 1, "Positive control failed: bare 'hr' should return 1 row"

    # Injection attempt embedding the real scope value with SPARQL syntax noise
    # If injection worked, the union/filter manipulation would return real rows.
    # initBindings treats the whole string as one literal → 0 rows.
    injection_payloads = [
        'hr" ) UNION { ?policy a <https://graphrag-aws.demo/biz-ops/ontology#Policy> }',
        "hr' OR scope='all",
        "hr\\n} GRAPH <urn:graph:normative> { ?policy a ?x",
    ]
    for payload in injection_payloads:
        rows_injected = t.execute(corpus, {"domain": payload})
        assert rows_injected == [], (
            f"Injection payload {payload!r} returned {len(rows_injected)} row(s) "
            "— initBindings may not be treating the value as a literal"
        )


# ---------------------------------------------------------------------------
# T10: uri-kind param branch
# ---------------------------------------------------------------------------


def test_uri_kind_param_wraps_value_as_uriref() -> None:
    """The 'uri' kind branch in execute() wraps the raw value as rdflib.URIRef.
    This is a unit test of the binding construction — no graph execution needed."""
    from rdflib import URIRef as RdfURIRef

    from graphrag.sparql_templates import SparqlParamSpec, SparqlTemplate

    # Minimal valid template with a uri-kind param
    t = SparqlTemplate(
        id="test_uri_kind",
        description="Test template for uri-kind param coverage",
        params=(SparqlParamSpec("subject", kind="uri"),),
        sparql="""
SELECT ?subject ?p ?o WHERE {
    GRAPH <urn:graph:normative> {
        ?subject ?p ?o .
    }
}
""",
    )
    t._validate()  # Ensure governance contract is met

    # Build a mock graph that records the query binding
    class CapturingGraph:
        def __init__(self) -> None:
            self.captured_bindings: dict[str, object] = {}
            self.vars: list[str] = []

        def query(  # noqa: N803
            self, sparql: str, initBindings: dict[str, object] | None = None
        ) -> CapturingGraph:
            self.captured_bindings = initBindings or {}
            return self

        def __iter__(self):  # type: ignore[override]
            return iter([])

    mock_graph = CapturingGraph()
    t.execute(mock_graph, {"subject": "urn:biz:policy:hr-leave"})  # type: ignore[arg-type]

    assert "subject" in mock_graph.captured_bindings
    bound_val = mock_graph.captured_bindings["subject"]
    assert isinstance(bound_val, RdfURIRef), (
        f"Expected URIRef binding for kind='uri', got {type(bound_val)}"
    )
    assert str(bound_val) == "urn:biz:policy:hr-leave"
