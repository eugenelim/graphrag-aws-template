"""T5 — governed orchestration + the audit trace (AC5).

Offline (rule selector + in-memory store + offline synthesizer) on the exemplar: select →
extract → execute → synthesize, with a render that narrates question → template → params →
cypher + param map → rows → answer. A no-match (no template, or an unvalidatable required
param) returns a result with ``no_match_reason`` and **no query executed**.

# STUB: AC5
"""

from __future__ import annotations

from pathlib import Path

from graphrag.governed import GovernedResult, governed_query
from graphrag.resolve import resolve
from graphrag.select import RuleTemplateSelector
from graphrag.sources import load_corpus
from graphrag.store import MemoryGraphStore
from graphrag.synthesize import TemplateSynthesizer
from graphrag.templates import Template


def _store(community_root: Path, enhancements_root: Path) -> MemoryGraphStore:
    return MemoryGraphStore.from_graph(resolve(load_corpus(community_root, enhancements_root)))


class _FixedSelector:
    """Forces a specific template id (or None) — for exercising the no-match branches."""

    def __init__(self, template_id: str | None) -> None:
        self._id = template_id

    def select(self, question: str, templates: list[Template]) -> str | None:
        return self._id


def test_governed_happy_path_audits_the_owned_keps(
    community_root: Path, enhancements_root: Path
) -> None:
    result = governed_query(
        "Which KEPs does SIG Network own?",
        graph_store=_store(community_root, enhancements_root),
        selector=RuleTemplateSelector(),
        synthesizer=TemplateSynthesizer(),
    )
    assert isinstance(result, GovernedResult)
    assert result.template_id == "sig_owned_keps"
    assert result.no_match_reason is None
    # the deterministically-bound, store-confirmed parameter.
    assert result.param_map == {"sig": "sig:sig-network"}
    # the governed cypher is shown literally, with the value bound in a separate param map.
    assert "$sig" in result.cypher and "sig:sig-network" not in result.cypher
    # the executed rows are the owned KEPs, sorted.
    assert [n.id for n in result.rows] == ["kep-1880", "kep-2086"]
    assert result.answer


def test_governed_render_orders_the_audit_trace(
    community_root: Path, enhancements_root: Path
) -> None:
    rendered = governed_query(
        "Which KEPs does SIG Network own?",
        graph_store=_store(community_root, enhancements_root),
        selector=RuleTemplateSelector(),
        synthesizer=TemplateSynthesizer(),
    ).render()
    order = [
        rendered.index("question:"),
        rendered.index("template: sig_owned_keps"),
        rendered.index("bound params:"),
        rendered.index("cypher:"),
        rendered.index("param map:"),
        rendered.index("rows:"),
        rendered.index("answer:"),
    ]
    assert order == sorted(order)  # no black-box hop — each stage narrated in order


def test_no_template_fits_is_a_governed_no_match(
    community_root: Path, enhancements_root: Path
) -> None:
    result = governed_query(
        "what is the weather today",
        graph_store=_store(community_root, enhancements_root),
        selector=RuleTemplateSelector(),
        synthesizer=TemplateSynthesizer(),
    )
    assert result.template_id is None
    assert result.no_match_reason
    assert result.cypher == ""  # no query executed
    assert result.rows == []
    assert "no query executed" in result.render()


def test_unvalidatable_required_param_is_a_no_match_with_no_query(
    community_root: Path, enhancements_root: Path
) -> None:
    # force the SIG template on a question whose SIG does not exist in the graph.
    result = governed_query(
        "Which KEPs does SIG Nonexistent own?",
        graph_store=_store(community_root, enhancements_root),
        selector=_FixedSelector("sig_owned_keps"),
        synthesizer=TemplateSynthesizer(),
    )
    assert result.template_id is None  # downgraded to no-match
    assert "could not bind parameters" in (result.no_match_reason or "")
    assert result.cypher == "" and result.rows == []  # governed refusal — no query ran


def test_selector_returning_unknown_id_is_a_no_match(
    community_root: Path, enhancements_root: Path
) -> None:
    result = governed_query(
        "anything",
        graph_store=_store(community_root, enhancements_root),
        selector=_FixedSelector("totally_made_up"),
        synthesizer=TemplateSynthesizer(),
    )
    assert result.template_id is None
    assert "unknown template id" in (result.no_match_reason or "")
    assert result.cypher == ""
