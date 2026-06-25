"""The governed query orchestration — Cypher Templates end to end (AC2/AC5).

``governed_query`` is the governed counterpart to ``hybrid.hybrid_query``: a question is
routed to **one vetted template** (``select.py``), its parameters are extracted and
validated **deterministically** (``params.py``), the template's parameterized openCypher
runs (live on Neptune / app-layer offline — ``execute_template``), and a display answer
is synthesized over the returned rows. The ``GovernedResult`` carries the full **audit
trace** — which template ran and why, the bound parameters and how each was extracted, the
literal cypher + its parameter map shown separately, the rows, and the answer — so an
auditor sees exactly which reviewed query ran with which validated values (charter
principle 1: no black-box hop).

This module is **PyYAML-free** (it rides the query Lambda's ``Code.from_asset`` bundle):
it imports only ``templates``/``select``/``params``/``synthesize``/``store``/``model``,
none of which import ``yaml`` at module load.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .model import Node
from .params import BoundParam, ExtractionFailure, extract_params
from .select import TemplateSelector
from .store.base import GraphStore
from .store.neptune import NeptuneGraphStore
from .synthesize import Synthesizer
from .templates import TEMPLATES, Template


def execute_template(
    store: GraphStore, template: Template, params: Mapping[str, object]
) -> list[Node]:
    """Run a template against ``store`` and return its rows, sorted-identical per backend.

    The dual form (AC2): the Neptune backend runs the governed ``template.cypher`` via
    ``run_template_query`` (the live, parameterized path); every other backend runs the
    paired app-layer ``template.evaluate`` over the ``GraphStore`` seam (offline/CI). Both
    results are sorted by node id here so the trace is byte-identical across backends — the
    same invariant ``neighbors_batch`` lives under.

    Read-only is enforced **at the seam**, not only by the library's CI lint: a template that
    is not read-only is refused before any query runs, so the governance property holds even
    for a template that escaped test coverage (the trade-off that lets this path skip the
    run-time read-only guard the text2cypher path needs — IAM read-only data-action scoping
    per ADR-0004; see the governed-vs-risky doc).
    """
    if not template.is_read_only():
        raise ValueError(f"refusing to execute non-read-only template {template.id!r}")
    if isinstance(store, NeptuneGraphStore):
        nodes = store.run_template_query(template.cypher, dict(params))
    else:
        nodes = list(template.evaluate(store, params))
    by_id: dict[str, Node] = {}
    for node in nodes:
        by_id.setdefault(node.id, node)
    return [by_id[node_id] for node_id in sorted(by_id)]


@dataclass
class GovernedResult:
    """The audit artifact of one governed query.

    On a match: the selected template, the bound parameters (with extraction provenance),
    the literal parameterized cypher + the parameter map (shown **separately** — never
    interpolated together), the returned rows, the synthesized answer, and citations.
    On a no-match: ``template_id`` is ``None``, ``no_match_reason`` explains why, and **no
    query was executed** (``cypher``/``rows`` empty).
    """

    question: str
    template_id: str | None = None
    template_description: str = ""
    bound_params: list[BoundParam] = field(default_factory=list)
    dropped_candidates: list[str] = field(default_factory=list)
    cypher: str = ""
    param_map: dict[str, object] = field(default_factory=dict)
    rows: list[Node] = field(default_factory=list)
    answer: str = ""
    citations: list[str] = field(default_factory=list)
    no_match_reason: str | None = None

    def render(self) -> str:
        """Narrate the audit trace in order: question → template (+why) → bound params →
        cypher + param map → rows → answer (no black-box hop, charter principle 1)."""
        lines = [f"question: {self.question}"]
        if self.template_id is None:
            lines.append(f"no-match: {self.no_match_reason or 'no template selected'}")
            lines.append("(no query executed)")
            return "\n".join(lines)
        lines.append(f"template: {self.template_id} — {self.template_description}")
        if self.bound_params:
            lines.append("bound params:")
            for bp in self.bound_params:
                lines.append(f"  - {bp.name} = {bp.value} (via {bp.via})")
        else:
            lines.append("bound params: (none)")
        if self.dropped_candidates:
            lines.append(f"dropped candidates: {', '.join(self.dropped_candidates)}")
        lines.append(f"cypher: {self.cypher}")
        lines.append(f"param map: {self.param_map}")
        rows = ", ".join(node.id for node in self.rows) or "(none)"
        lines.append(f"rows: {rows}")
        lines.append("citations:")
        for cite in self.citations:
            lines.append(f"  - {cite}")
        lines.append(f"answer: {self.answer}")
        return "\n".join(lines)


def _catalog(templates: tuple[Template, ...]) -> list[Template]:
    return list(templates)


def governed_query(
    question: str,
    *,
    graph_store: GraphStore,
    selector: TemplateSelector,
    synthesizer: Synthesizer,
    aliases: Mapping[str, str] | None = None,
    templates: tuple[Template, ...] = TEMPLATES,
) -> GovernedResult:
    """Select → extract+validate → execute → synthesize, with a full audit trace (AC5).

    A no-match (no template fits, or a required parameter can't be validated) returns a
    ``GovernedResult`` with ``no_match_reason`` set and **no query executed** — the
    governed refusal, never a fabricated query.
    """
    alias_map: dict[str, str] = dict(aliases or {})
    by_id = {t.id: t for t in templates}
    selected_id = selector.select(question, _catalog(templates))
    # Resolve against the catalog the selector actually saw (not the global registry), so a
    # caller passing a subset can't have an out-of-subset id slip through. The unknown-id
    # branch is belt-and-suspenders: the selectors already validate their output against this
    # same set, so selected_id is always None or a real id — but the guard keeps the
    # "never execute an id we didn't vet" invariant local and obvious.
    template = by_id.get(selected_id) if selected_id is not None else None
    if template is None:
        reason = (
            "no template fit the question"
            if selected_id is None
            else f"selector returned unknown template id {selected_id!r}"
        )
        return GovernedResult(question=question, no_match_reason=reason)

    extraction = extract_params(question, template, alias_map, graph_store)
    if isinstance(extraction, ExtractionFailure):
        return GovernedResult(
            question=question,
            template_id=None,
            no_match_reason=f"could not bind parameters for {template.id}: {extraction.reason}",
            dropped_candidates=extraction.dropped,
        )

    param_map: dict[str, object] = {bp.name: bp.value for bp in extraction.bound}
    rows = execute_template(graph_store, template, param_map)
    synthesis = synthesizer.synthesize(question, [], rows)
    return GovernedResult(
        question=question,
        template_id=template.id,
        template_description=template.description,
        bound_params=extraction.bound,
        dropped_candidates=extraction.dropped,
        cypher=template.cypher,
        param_map=param_map,
        rows=rows,
        answer=synthesis.answer,
        citations=synthesis.citations,
    )
