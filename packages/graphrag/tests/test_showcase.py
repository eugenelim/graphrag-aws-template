"""T8 — consolidated showcase set + loader (AC10).

The single home for the demo's queries: >=5-6 per mode, each with gold entity/chunk
ids that resolve in the fixture corpus and a non-empty highlight.

# STUB: AC10
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from graphrag.chunk import chunk_corpus
from graphrag.resolve import resolve
from graphrag.showcase import ShowcaseQuery, load_showcase
from graphrag.sources import load_corpus


def test_showcase_parses() -> None:
    queries = load_showcase()
    assert queries
    assert all(isinstance(q, ShowcaseQuery) for q in queries)


def test_at_least_five_per_mode() -> None:
    counts = Counter(q.wins for q in load_showcase())
    for mode in ("vector", "graph", "hybrid"):
        assert counts[mode] >= 5, f"need >=5 {mode} queries, got {counts[mode]}"


def test_every_gold_resolves_in_fixture(community_root: Path, enhancements_root: Path) -> None:
    docs = load_corpus(community_root, enhancements_root)
    graph = resolve(docs)
    chunk_ids = {c.id for c in chunk_corpus(docs)}
    node_ids = set(graph.nodes)

    for q in load_showcase():
        assert q.wins in ("vector", "graph", "hybrid")
        assert q.query.strip()
        assert q.highlight.strip(), f"query {q.id} has an empty highlight"
        assert q.gold, f"query {q.id} names no gold entity/chunk"
        for gold in q.gold:
            assert gold in node_ids or gold in chunk_ids, (
                f"query {q.id} gold {gold!r} resolves to neither a graph node nor a chunk id"
            )


# --- slice 4: permission-filtered showcase queries (AC10) -----------------------------

from graphrag.labels import load_labels  # noqa: E402
from graphrag.showcase import PermissionShowcaseQuery, load_permission_showcase  # noqa: E402
from graphrag.visibility import DEFAULT_VISIBILITY, resolve_clearance  # noqa: E402


def test_permission_showcase_parses() -> None:
    queries = load_permission_showcase()
    assert queries
    assert all(isinstance(q, PermissionShowcaseQuery) for q in queries)


def test_permission_showcase_consistent_with_labels_and_personas(
    community_root: Path, enhancements_root: Path
) -> None:
    docs = load_corpus(community_root, enhancements_root)
    node_ids = set(resolve(docs).nodes)
    labels = load_labels()

    for q in load_permission_showcase():
        assert q.query.strip()
        assert q.highlight.strip(), f"{q.id} has an empty highlight"
        assert q.visible or q.filtered, f"{q.id} names no visible/filtered split"
        clearance = resolve_clearance(q.persona)  # persona must be a known clearance
        # every named id resolves in the fixture graph, and the visible/filtered split is
        # CONSISTENT with the actual labels + the persona's clearance (no hand-wavy gold).
        for vid in q.visible:
            assert vid in node_ids, f"{q.id} visible {vid!r} missing from fixture"
            assert clearance.allows(labels.get(vid, DEFAULT_VISIBILITY))
        for fid in q.filtered:
            assert fid in node_ids, f"{q.id} filtered {fid!r} missing from fixture"
            assert not clearance.allows(labels.get(fid, DEFAULT_VISIBILITY))


# --- opencypher-templates: governed-path showcase queries (AC10) -----------------------

from graphrag.governed import execute_template  # noqa: E402
from graphrag.params import ParamBinding, extract_params  # noqa: E402
from graphrag.resolve import load_aliases  # noqa: E402
from graphrag.showcase import GovernedShowcaseQuery, load_governed_showcase  # noqa: E402
from graphrag.store import MemoryGraphStore  # noqa: E402
from graphrag.templates import get_template  # noqa: E402


def test_governed_showcase_parses() -> None:
    queries = load_governed_showcase()
    assert len(queries) >= 4
    assert all(isinstance(q, GovernedShowcaseQuery) for q in queries)


def test_governed_showcase_consistent_with_templates_and_fixture(
    community_root: Path, enhancements_root: Path
) -> None:
    store = MemoryGraphStore.from_graph(resolve(load_corpus(community_root, enhancements_root)))
    node_ids = {n.id for n in store.all_nodes()}

    for q in load_governed_showcase():
        assert q.query.strip()
        assert q.highlight.strip(), f"{q.id} has an empty highlight"
        template = get_template(q.template)
        assert template is not None, f"{q.id} names unknown template {q.template!r}"
        assert q.param in node_ids, f"{q.id} param {q.param!r} missing from fixture"
        slot = template.params[0]
        # the showcase QUESTION deterministically extracts to the labeled param (the
        # select-and-extract join AC10's `param` field promises — not just template+gold).
        binding = extract_params(q.query, template, load_aliases(), store)
        assert isinstance(binding, ParamBinding), f"{q.id}: question failed extraction"
        bound = {bp.name: bp.value for bp in binding.bound}
        assert bound.get(slot.name) == q.param, (
            f"{q.id}: question extracts {bound.get(slot.name)!r}, labeled param is {q.param!r}"
        )
        # running the vetted template with the extracted param returns exactly the gold rows
        # (no hand-wavy gold — the showcase is consistent with the real query).
        rows = [n.id for n in execute_template(store, template, {slot.name: q.param})]
        assert rows == sorted(q.gold), f"{q.id}: rows {rows} != gold {sorted(q.gold)}"
        for gid in q.gold:
            assert gid in node_ids, f"{q.id} gold {gid!r} missing from fixture"


# --- text2opencypher-guarded: the flexible-path showcase set (AC11) -------------------
from graphrag.showcase import (  # noqa: E402
    Text2CypherShowcaseQuery,
    load_text2cypher_showcase,
)


def test_text2cypher_showcase_parses() -> None:
    queries = load_text2cypher_showcase()
    assert len(queries) >= 3
    assert all(isinstance(q, Text2CypherShowcaseQuery) for q in queries)
    # at least one head-to-head shared with a governed template, and at least one open-ended.
    assert any(q.shared_with_template for q in queries), "expected a governed head-to-head"
    assert any(q.shared_with_template is None for q in queries), "expected an open-ended query"


def test_text2cypher_showcase_gold_resolves_and_shared_template_exists(
    community_root: Path, enhancements_root: Path
) -> None:
    node_ids = {
        n.id
        for n in MemoryGraphStore.from_graph(
            resolve(load_corpus(community_root, enhancements_root))
        ).all_nodes()
    }
    for q in load_text2cypher_showcase():
        assert q.query.strip()
        assert q.highlight.strip(), f"{q.id} has an empty highlight"
        assert q.gold, f"{q.id} has no gold rows"
        for gid in q.gold:
            assert gid in node_ids, f"{q.id} gold {gid!r} missing from fixture"
        if q.shared_with_template is not None:
            assert get_template(q.shared_with_template) is not None, (
                f"{q.id} names unknown shared template {q.shared_with_template!r}"
            )


# --- metadata-filtering: the self-query showcase set (AC10) ---------------------------
from graphrag.embed import HashEmbedder  # noqa: E402
from graphrag.selfquery import FIELD_BY_NAME, RuleMetadataExtractor, selfquery_query  # noqa: E402
from graphrag.showcase import SelfQueryShowcaseQuery, load_selfquery_showcase  # noqa: E402
from graphrag.store import MemoryVectorStore  # noqa: E402
from graphrag.store.vector_base import EmbeddedChunk  # noqa: E402
from graphrag.synthesize import TemplateSynthesizer  # noqa: E402


def test_selfquery_showcase_parses_and_spans_both_modes() -> None:
    queries = load_selfquery_showcase()
    assert len(queries) >= 4
    assert all(isinstance(q, SelfQueryShowcaseQuery) for q in queries)
    modes = {q.mode for q in queries}
    assert "vector" in modes and "hybrid" in modes, "showcase must span vector AND hybrid"


def test_selfquery_showcase_consistent_with_schema_and_fixture(
    community_root: Path, enhancements_root: Path
) -> None:
    docs = load_corpus(community_root, enhancements_root)
    chunks = chunk_corpus(docs)
    chunk_ids = {c.id for c in chunks}
    entity_ids = {eid for c in chunks for eid in c.entity_ids}
    graph = MemoryGraphStore.from_graph(resolve(docs))

    # one in-memory vector store from the fixture, shared across entries.
    embedder = HashEmbedder()
    vstore = MemoryVectorStore()
    for c, v in zip(chunks, embedder.embed([c.text for c in chunks]), strict=True):
        vstore.index_chunk(EmbeddedChunk(c, v))

    for q in load_selfquery_showcase():
        assert q.query.strip()
        assert q.highlight.strip(), f"{q.id} has an empty highlight"
        assert q.mode in ("vector", "hybrid")
        # the expected_filter only names declared fields; its values resolve in the fixture.
        assert q.expected_filter, f"{q.id} names no expected filter"
        for field_name, values in q.expected_filter.items():
            spec = FIELD_BY_NAME.get(field_name)
            assert spec is not None, f"{q.id} expected_filter names undeclared field {field_name!r}"
            for value in values:
                if spec.kind == "enum":
                    assert spec.choices is not None and value in spec.choices
                else:  # entity — its normalized id matches >=1 fixture chunk (gold-data, AC10)
                    assert value in entity_ids, f"{q.id} entity {value!r} matches no fixture chunk"
        for cid in q.visible + q.excluded:
            assert cid in chunk_ids, f"{q.id} chunk id {cid!r} missing from fixture"

        # end-to-end: the offline rule extractor + during-ANN filter keep `visible`, prune
        # `excluded` (no hand-wavy gold — the showcase is consistent with the real filter).
        result = selfquery_query(
            q.query,
            extractor=RuleMetadataExtractor(),
            vector_store=vstore,
            embedder=embedder,
            synthesizer=TemplateSynthesizer(),
            aliases=load_aliases(),
            mode=q.mode,
            graph_store=graph if q.mode == "hybrid" else None,
            k=len(chunks),  # no top-k truncation; the filter is the only pruning under test
        )
        hit_ids = {h.chunk.id for h in result.hits}
        for vid in q.visible:
            assert vid in hit_ids, f"{q.id}: expected-visible {vid!r} not returned"
        for eid in q.excluded:
            assert eid not in hit_ids, f"{q.id}: expected-excluded {eid!r} leaked into hits"


# --- parent-child-retrieval: parent-child showcase set (AC8) ---------------------------

from graphrag.parentchild import group_into_parents, parentchild_query  # noqa: E402
from graphrag.showcase import (  # noqa: E402
    ParentChildShowcaseQuery,
    load_parentchild_showcase,
)
from graphrag.store.parentchild_memory import MemoryParentChildStore  # noqa: E402


def test_parentchild_showcase_parses() -> None:
    queries = load_parentchild_showcase()
    assert len(queries) >= 3
    assert all(isinstance(q, ParentChildShowcaseQuery) for q in queries)


def test_parentchild_showcase_consistent_with_fixture(
    community_root: Path, enhancements_root: Path
) -> None:
    docs = load_corpus(community_root, enhancements_root)
    chunks = chunk_corpus(docs)
    chunk_ids = {c.id for c in chunks}

    embedder = HashEmbedder()
    embedded = [
        EmbeddedChunk(c, v)
        for c, v in zip(chunks, embedder.embed([c.text for c in chunks]), strict=True)
    ]
    bodies = {d.doc_id: d.markdown.body for d in docs if d.markdown is not None}
    parents = group_into_parents(embedded, bodies)
    parent_ids = {p.parent_id for p in parents}

    store = MemoryParentChildStore()
    for parent in parents:
        store.index_parent(parent)

    for q in load_parentchild_showcase():
        assert q.query.strip()
        assert q.highlight.strip(), f"{q.id} has an empty highlight"
        assert q.contrast.strip(), (
            f"{q.id} has an empty contrast (the flat-vs-parent-child framing)"
        )
        # the gold matched child resolves to a fixture chunk, and BELONGS to the gold parent
        assert q.expected_matched_child in chunk_ids, f"{q.id} matched child missing from fixture"
        assert q.expected_parent in parent_ids, f"{q.id} parent missing from fixture"
        assert q.expected_matched_child.rsplit("#", 1)[0] == q.expected_parent, (
            f"{q.id}: matched child {q.expected_matched_child!r} is not a child of "
            f"the gold parent {q.expected_parent!r}"
        )
        # end-to-end: the parent-child query returns the gold parent, and synthesis reads its
        # full BODY (the returned hit carries the parent body, not a child fragment).
        result = parentchild_query(
            q.query,
            store=store,
            embedder=embedder,
            synthesizer=TemplateSynthesizer(),
            k=len(parents),
        )
        returned = {h.parent.parent_id for h in result.hits}
        assert q.expected_parent in returned, (
            f"{q.id}: gold parent {q.expected_parent!r} not returned"
        )
        gold_hit = next(h for h in result.hits if h.parent.parent_id == q.expected_parent)
        assert gold_hit.parent.body, f"{q.id}: returned parent has no body for synthesis"
        assert gold_hit.matched_child is not None  # a precise child surfaced the parent


# --- global-community-summary: corpus-wide showcase (AC9) -------------------------------
def test_global_showcase_parses() -> None:
    from graphrag.showcase import GlobalShowcaseQuery, load_global_showcase

    queries = load_global_showcase()
    assert len(queries) >= 3
    assert all(isinstance(q, GlobalShowcaseQuery) for q in queries)


def test_fixture_yields_all_three_visibility_tiers(
    community_root: Path, enhancements_root: Path
) -> None:
    # Pins the queries.yaml partition claim AND makes the AC10 persona check non-vacuous: the
    # fixture must yield communities spanning public/internal/restricted, so a public-reader
    # call is a STRICT subset of the unrestricted call (an above-public community is omitted).
    from graphrag.community_detect import detect_communities
    from graphrag.labels import label_graph, load_labels

    graph = resolve(load_corpus(community_root, enhancements_root))
    label_graph(graph, load_labels())  # stamp visibility (as ingest() does) so tiers are real
    tiers = {spec.tier for spec in detect_communities(list(graph.nodes.values()), graph.edges)}
    assert {"public", "internal", "restricted"} <= tiers, (
        f"fixture must yield all three visibility tiers (got {tiers}) — AC10 persona check "
        "relies on at least one above-public community existing"
    )


def test_global_showcase_entities_resolve_and_land_in_communities(
    community_root: Path, enhancements_root: Path
) -> None:
    from graphrag.community_detect import detect_communities
    from graphrag.showcase import load_global_showcase

    graph = resolve(load_corpus(community_root, enhancements_root))
    nodes, edges = list(graph.nodes.values()), graph.edges
    node_ids = {n.id for n in nodes}
    # entities that landed in some detected community (membership union)
    in_a_community = {eid for spec in detect_communities(nodes, edges) for eid in spec.entity_ids}

    for q in load_global_showcase():
        assert q.query.strip()
        assert q.highlight.strip(), f"{q.id} has an empty highlight"
        assert q.theme.strip(), f"{q.id} has an empty corpus-wide theme"
        assert q.expected_entities, f"{q.id} names no expected entities"
        for entity_id in q.expected_entities:
            assert entity_id in node_ids, f"{q.id} entity {entity_id!r} missing from fixture"
            assert entity_id in in_a_community, (
                f"{q.id} entity {entity_id!r} is in no detected community"
            )
