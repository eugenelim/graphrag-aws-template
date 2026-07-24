"""Named SPARQL template registry — the SPARQL equivalent of templates.py.

The graphrag **SPARQL Templates** pattern, translated to SPARQL 1.1 and
implemented as the *governed, auditable, low-risk* enterprise query path over
the RDF named-graph store (``urn:graph:normative``, ``urn:graph:descriptive``).
The library is a **fixed set of expert-authored, parameterized, read-only**
SPARQL SELECT queries.

**Safe parameterization (initBindings).** All user-supplied values are bound
via rdflib's ``initBindings=`` API — never via f-string or ``.format()``
interpolation. ``initBindings`` injects values as RDF terms (``Literal``,
``URIRef``) before query execution, so special characters in parameter values
cannot escape into the query text. This satisfies the SPARQL injection
denylist equivalent of the openCypher ``$param`` governance contract.

**Why a Python registry, not a YAML/JSON data file.** The SPARQL strings are
reviewed code — that *is* the "governed/auditable" property (they change only
through PR review) — and keeping them as Python literals keeps this module out
of the query Lambda's PyYAML-free import graph
(``packages/graphrag/AGENTS.md``).

**Read-only enforcement.** Each template is checked at import time against the
SPARQL mutation denylist (INSERT, DELETE, DROP, CLEAR, LOAD, CREATE). The
``SparqlTemplate.is_read_only()`` method is the app-layer belt-and-suspenders
check; the IAM ``ReadDataViaQuery``-only grant is the load-bearing control.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from rdflib import Literal as RdfLiteral
from rdflib import URIRef

# Shared SPARQL mutation denylist regex — word-boundary anchors catch
# keyword-as-token occurrences. Adequate for hand-authored literals reviewed
# at PR time; same limitations as sparql_base.MUTATION_RE.
_MUTATION_RE = re.compile(r"\b(INSERT|DELETE|DROP|CLEAR|LOAD|CREATE)\b", re.IGNORECASE)

SparqlParamKind = Literal["literal", "uri"]


@dataclass(frozen=True)
class SparqlParamSpec:
    """A typed parameter slot of a SPARQL template.

    ``literal`` slots are bound as ``rdflib.Literal`` (plain string terms);
    ``uri`` slots are bound as ``rdflib.URIRef``. Both are injected via
    ``initBindings=`` — never interpolated into the query text.
    """

    name: str
    kind: SparqlParamKind = "literal"
    required: bool = True


@dataclass(frozen=True)
class SparqlTemplate:
    """One expert-authored, parameterized, read-only SPARQL SELECT template.

    ``sparql`` is the governed artifact (runs via rdflib or a SPARQL 1.1
    endpoint). Parameters are declared in ``params`` and bound at execution
    time via rdflib's ``initBindings=`` API — never via f-string or
    ``.format()`` interpolation.

    Every template must:
    - contain no SPARQL mutation keyword (INSERT, DELETE, DROP, CLEAR, LOAD,
      CREATE) — enforced by ``is_read_only()`` at import time;
    - declare every param as a ``?varname`` reference in the SPARQL string —
      enforced by ``_validate()`` at import time;
    - carry no string-interpolation marker (``{{``, ``}}``, ``%s``,
      ``%(``, ``.format(``) — enforced by ``_validate()`` at import time.
    """

    id: str
    description: str
    params: tuple[SparqlParamSpec, ...]
    sparql: str

    def param_names(self) -> set[str]:
        """The set of declared parameter names."""
        return {p.name for p in self.params}

    def is_read_only(self) -> bool:
        """True iff the sparql contains no SPARQL mutation keyword.

        A blocklist over upper-cased text — adequate because templates are
        PR-reviewed Python literals, not attacker input. Enforcement is
        import-time only (via ``_validate()``); the IAM
        ``ReadDataViaQuery``-only grant is the load-bearing runtime control.
        """
        return not _MUTATION_RE.search(self.sparql)

    def _validate(self) -> None:
        """Raise ``ValueError`` if this template violates the governance contract.

        Called at module import time so a misconfigured template can never
        reach the runtime path.
        """
        if not self.is_read_only():
            raise ValueError(
                f"SPARQL template {self.id!r} is not read-only — mutation keyword detected"
            )
        for param in self.params:
            if f"?{param.name}" not in self.sparql:
                raise ValueError(
                    f"SPARQL template {self.id!r} declares param {param.name!r} "
                    f"but ?{param.name} does not appear in the SPARQL string"
                )
        # Defense-in-depth: no string-interpolation markers
        for marker in ("{{", "}}", "%s", "%(", ".format("):
            if marker in self.sparql:
                raise ValueError(
                    f"SPARQL template {self.id!r} contains interpolation "
                    f"marker {marker!r} — use initBindings instead"
                )

    def execute(self, graph: Any, params: dict[str, Any]) -> list[dict[str, str]]:
        """Execute this template against an rdflib graph via ``initBindings``.

        ``graph`` must be an ``rdflib.ConjunctiveGraph`` or ``rdflib.Dataset``
        (or any object that exposes a ``.query(sparql, initBindings=...)``
        method). ``params`` maps declared parameter names to Python values
        (``str`` for literals; ``str`` URI for uri-kind params).

        All values are wrapped in the correct rdflib term type before being
        passed to ``initBindings`` — they are never interpolated into the
        SPARQL text.

        Returns a list of binding dicts: each dict maps SPARQL variable name
        (``str``) to its bound value as a ``str`` representation of the RDF
        term. Unbound optional variables are omitted from the dict.
        """
        # Enforce required params — a missing required param silently produces
        # an unbound SPARQL variable, which FILTER drops as an error-value and
        # returns zero rows, indistinguishable from "no matching triples".
        for spec in self.params:
            if spec.required and params.get(spec.name) is None:
                raise ValueError(f"Required param {spec.name!r} missing for template {self.id!r}")

        bindings: dict[str, Any] = {}
        for spec in self.params:
            raw = params.get(spec.name)
            if raw is None:
                continue
            if spec.kind == "uri":
                bindings[spec.name] = URIRef(str(raw))
            else:
                # Default: literal (plain xsd:string)
                bindings[spec.name] = RdfLiteral(str(raw))

        result = graph.query(self.sparql, initBindings=bindings)
        rows: list[dict[str, str]] = []
        for row in result:
            row_dict: dict[str, str] = {}
            for var in result.vars:
                val = getattr(row, str(var), None)
                if val is not None:
                    row_dict[str(var)] = str(val)
            rows.append(row_dict)
        return rows


# ---------------------------------------------------------------------------
# The fixed template library — governed SPARQL SELECT queries
#
# Naming convention: templates that filter by a parameter use the form
# ``<subject>_by_<param>``.  Every template queries a specific named graph;
# cross-graph queries use ``GRAPH ?g`` with a ``VALUES`` clause to restrict
# the graph set.
#
# Parameter binding convention: all caller-supplied values are declared as
# ``SparqlParamSpec`` entries.  The SPARQL string uses ``?name`` for those
# slots and ``initBindings=`` is used at execution time — never f-strings.
# ---------------------------------------------------------------------------

_POLICIES_BY_DOMAIN_SPARQL = """
PREFIX biz:    <https://graphrag-aws.demo/biz-ops/ontology#>
PREFIX schema: <https://schema.org/>
SELECT ?policy ?name ?effectiveDate ?scope WHERE {
    GRAPH <urn:graph:normative> {
        ?policy a biz:Policy ;
                schema:name ?name ;
                biz:scope ?scope .
        OPTIONAL { ?policy biz:effectiveDate ?effectiveDate . }
        FILTER(?scope = ?domain)
    }
}
"""


SPARQL_TEMPLATES: tuple[SparqlTemplate, ...] = (
    SparqlTemplate(
        id="policies_by_domain",
        description=(
            "All biz:Policy resources in urn:graph:normative whose biz:scope "
            "matches the given domain literal (e.g. 'hr', 'finance', 'all')."
        ),
        params=(SparqlParamSpec("domain", kind="literal"),),
        sparql=_POLICIES_BY_DOMAIN_SPARQL,
    ),
)

SPARQL_TEMPLATE_BY_ID: dict[str, SparqlTemplate] = {t.id: t for t in SPARQL_TEMPLATES}

# Fail at import if any template violates the governance contract — read-only,
# declared params appear in the SPARQL, no interpolation markers. This makes the
# contract load-bearing at the seam: a template added without a test entry still
# can't ship a mutation verb or an unbound value.
for _t in SPARQL_TEMPLATES:
    _t._validate()


def get_sparql_template(template_id: str) -> SparqlTemplate | None:
    """Return the ``SparqlTemplate`` with this id, or ``None``.

    The ``None`` case is the validation gate for a selector's output — a name
    outside the fixed library never resolves to a query.
    """
    return SPARQL_TEMPLATE_BY_ID.get(template_id)


def execute_template(
    template_id: str,
    params: dict[str, Any],
    graph: Any,
) -> list[dict[str, str]]:
    """Look up ``template_id`` in the registry and execute it against ``graph``.

    Raises ``KeyError`` if ``template_id`` is not in the registry (callers that
    want a graceful error should call ``get_sparql_template`` first).
    """
    return SPARQL_TEMPLATE_BY_ID[template_id].execute(graph, params)
