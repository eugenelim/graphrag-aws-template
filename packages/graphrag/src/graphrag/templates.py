"""The governed query path's template library — Cypher Templates on Neptune (AC1/AC2).

The graphrag.com **Cypher Templates** pattern, translated to Neptune openCypher and
implemented as the *governed, auditable, low-risk* enterprise query path (the safe
half of the governed-vs-risky pair; the risky half is the separate
``text2opencypher-guarded`` slice). The library is a **fixed set of expert-authored,
parameterized, read-only** openCypher queries. The LLM's only job downstream is to
*select* one of these by id (``select.py``) and parameters are extracted and validated
deterministically (``params.py``) — the query that executes is always one of these
vetted strings, with every value bound through the openCypher parameter map, never a
string the model wrote.

**Why a Python registry, not a YAML/JSON data file.** The cypher strings are reviewed
code — that *is* the "governed/auditable" property (they change only through PR review)
— and keeping them as Python literals keeps this module out of the query Lambda's
PyYAML-free import graph (``packages/graphrag/AGENTS.md``).

**Dual-form (AC2).** Each template carries both the parameterized openCypher (the
governed artifact, executed live on Neptune via ``NeptuneGraphStore.run_template_query``)
and an app-layer ``evaluate`` over the ``GraphStore`` seam (the offline/in-memory
backend). Both return the **same sorted node set** for a given bound parameter set — the
same dual-form invariant ``neighbors_batch`` already lives under (results sorted by id so
order is backend-independent). ``governed.execute_template`` dispatches between them.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from .model import Direction, EdgeKind, EntityKind, Node
from .store.base import GraphStore

ParamKind = Literal["entity", "enum", "int"]

# Mutating openCypher clauses + procedure calls. A template is read-only by review and
# by this list: the executable surface is a fixed library of bounded reads, so a write
# verb in a template is a governance violation, caught by the AC1 lint (``read_only``).
_MUTATING_KEYWORDS = (
    "CREATE",
    "MERGE",
    "DELETE",
    "DETACH",
    "SET",
    "REMOVE",
    "DROP",
    "CALL",
)
_PARAM_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True)
class ParamSpec:
    """A typed parameter slot of a template.

    ``entity`` slots are filled from the question via the slice-3
    ``link_question``/``normalize`` functions and confirmed against the store, so the
    bound value is always a real graph node id; ``enum`` slots are validated against
    ``choices``; ``int`` slots are parsed and bounded by ``min``/``max`` (``params.py``).
    """

    name: str
    kind: ParamKind
    entity_kind: EntityKind | None = None  # required when kind == "entity"
    choices: tuple[str, ...] | None = None  # required when kind == "enum"
    min: int | None = None  # optional lower bound when kind == "int"
    max: int | None = None  # optional upper bound when kind == "int"
    required: bool = True


# An app-layer evaluator: run the template against the GraphStore seam and return the
# nodes the openCypher would return on Neptune (caller sorts; see ``execute_template``).
TemplateEvaluator = Callable[[GraphStore, Mapping[str, object]], list[Node]]


@dataclass(frozen=True)
class Template:
    """One expert-authored, parameterized, read-only openCypher template.

    ``cypher`` is the governed artifact (runs live on Neptune); ``evaluate`` is the
    paired app-layer form (runs offline over the in-memory store) — both return the same
    sorted node set (AC2). Every template ``RETURN``s its rows under the alias ``n``.
    """

    id: str
    description: str
    params: tuple[ParamSpec, ...]
    cypher: str
    evaluate: TemplateEvaluator

    def placeholders(self) -> set[str]:
        """The set of ``$name`` placeholders referenced in the cypher."""
        return set(_PARAM_RE.findall(self.cypher))

    def param_names(self) -> set[str]:
        return {p.name for p in self.params}

    def is_read_only(self) -> bool:
        """True iff the cypher contains no mutating clause or procedure call (AC1).

        A blocklist over upper-cased text — adequate because templates are PR-reviewed
        Python literals, not attacker input. Known limit: it would not catch a write verb
        hidden in a back-tick-quoted identifier or behind a non-``CALL`` procedure alias;
        if the library ever grows beyond hand-authored literals, prefer an allowlist of
        permitted read clauses. ``execute_template`` also calls this at the execution seam,
        so a non-read-only template is refused at runtime, not only by the CI lint."""
        upper = self.cypher.upper()
        return not any(re.search(rf"\b{kw}\b", upper) for kw in _MUTATING_KEYWORDS)


# --- The fixed template library --------------------------------------------------------
#
# Entity-parameter templates over the corpus's structural question classes. Each cypher
# binds the user-derived value through ``$param`` only; the edge ``kind`` and node
# ``kind`` are authored constants of the template (not user input), so they ride as
# literals — the injection surface is the parameter map, which carries only validated
# values (params.py). The ``evaluate`` twin composes the same traversal over the seam.


def _dedupe_sorted(nodes: list[Node]) -> list[Node]:
    """Dedupe by id and sort by id — the backend-independent ordering (AC2)."""
    by_id: dict[str, Node] = {}
    for node in nodes:
        by_id.setdefault(node.id, node)
    return [by_id[node_id] for node_id in sorted(by_id)]


def _sig_owned_keps(store: GraphStore, params: Mapping[str, object]) -> list[Node]:
    return _dedupe_sorted(store.neighbors(str(params["sig"]), EdgeKind.OWNS, Direction.OUT))


def _sig_tech_leads(store: GraphStore, params: Mapping[str, object]) -> list[Node]:
    return _dedupe_sorted(store.neighbors(str(params["sig"]), EdgeKind.TECH_LEADS, Direction.IN))


def _person_led_sigs(store: GraphStore, params: Mapping[str, object]) -> list[Node]:
    person = str(params["person"])
    reached = store.neighbors(person, EdgeKind.TECH_LEADS, Direction.OUT) + store.neighbors(
        person, EdgeKind.CHAIRS, Direction.OUT
    )
    return _dedupe_sorted([n for n in reached if n.kind is EntityKind.SIG])


def _kep_owning_sig(store: GraphStore, params: Mapping[str, object]) -> list[Node]:
    return _dedupe_sorted(store.neighbors(str(params["kep"]), EdgeKind.OWNS, Direction.IN))


TEMPLATES: tuple[Template, ...] = (
    Template(
        id="sig_owned_keps",
        description="The KEPs that a given SIG owns.",
        params=(ParamSpec("sig", "entity", entity_kind=EntityKind.SIG),),
        cypher=("MATCH (s:Entity {id: $sig})-[r:REL {kind: 'OWNS'}]->(n:Entity) RETURN n"),
        evaluate=_sig_owned_keps,
    ),
    Template(
        id="sig_tech_leads",
        description="The people who tech-lead a given SIG.",
        params=(ParamSpec("sig", "entity", entity_kind=EntityKind.SIG),),
        cypher=("MATCH (n:Entity)-[r:REL {kind: 'TECH_LEADS'}]->(s:Entity {id: $sig}) RETURN n"),
        evaluate=_sig_tech_leads,
    ),
    Template(
        id="person_led_sigs",
        description="The SIGs that a given person tech-leads or chairs.",
        params=(ParamSpec("person", "entity", entity_kind=EntityKind.PERSON),),
        cypher=(
            "MATCH (p:Entity {id: $person})-[r:REL]->(n:Entity) "
            "WHERE r.kind IN ['TECH_LEADS', 'CHAIRS'] AND n.kind = 'SIG' RETURN n"
        ),
        evaluate=_person_led_sigs,
    ),
    Template(
        id="kep_owning_sig",
        description="The SIG that owns a given KEP.",
        params=(ParamSpec("kep", "entity", entity_kind=EntityKind.KEP),),
        cypher=("MATCH (n:Entity)-[r:REL {kind: 'OWNS'}]->(k:Entity {id: $kep}) RETURN n"),
        evaluate=_kep_owning_sig,
    ),
)

TEMPLATE_BY_ID: dict[str, Template] = {t.id: t for t in TEMPLATES}

# Fail at import if any template violates the governance contract — read-only, and declared
# params exactly matching the cypher's $placeholders. This makes the contract load-bearing at
# the seam (a template added without a corresponding test entry still can't ship a write verb
# or an unbound value), not only enforced by the CI lint in test_templates.py.
for _t in TEMPLATES:
    if not _t.is_read_only():
        raise ValueError(f"governed template {_t.id!r} is not read-only")
    if _t.placeholders() != _t.param_names():
        raise ValueError(
            f"governed template {_t.id!r} placeholders {_t.placeholders()} "
            f"!= declared params {_t.param_names()}"
        )


def get_template(template_id: str) -> Template | None:
    """The template with this id, or ``None`` — the validation gate for a selector's
    output (an id outside the fixed set never resolves to a query)."""
    return TEMPLATE_BY_ID.get(template_id)
