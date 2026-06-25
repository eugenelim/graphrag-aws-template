"""T2 — synthetic label source + ingest labeling pass, propagated to both stores (AC2).

# STUB: AC2
"""

from __future__ import annotations

from pathlib import Path

from graphrag.chunk import Chunk
from graphrag.ingest import ingest
from graphrag.labels import label_chunks, label_graph, load_labels
from graphrag.model import Edge, EdgeKind, EntityKind, Graph, Node
from graphrag.store.memory import MemoryGraphStore


def test_load_labels_parses_packaged_map() -> None:
    labels = load_labels()
    assert labels.get("kep-1287") == "restricted"
    assert labels.get("kep-1880") == "internal"
    # an unlisted entity is absent (callers default it to public at lookup).
    assert "kep-2086" not in labels


def test_label_graph_sets_node_and_composed_edge_visibility() -> None:
    graph = Graph()
    graph.upsert_node(Node("sig:sig-node", EntityKind.SIG))
    graph.upsert_node(Node("kep-1287", EntityKind.KEP))
    graph.upsert_edge(Edge("sig:sig-node", "kep-1287", EdgeKind.OWNS))

    label_graph(graph, {"kep-1287": "restricted"})

    assert graph.nodes["sig:sig-node"].props["visibility"] == "public"  # unlabeled default
    assert graph.nodes["kep-1287"].props["visibility"] == "restricted"
    # edge visibility = compose(public, restricted) = restricted (most-restrictive-wins),
    # so a lower-clearance persona cannot traverse the OWNS edge into the restricted KEP.
    assert graph.edges[0].props["visibility"] == "restricted"


def test_label_chunks_compose_owning_entities() -> None:
    chunks = [
        Chunk("c0", "t", "S", "p", "h", entity_ids=["kep-1287", "sig:sig-node"]),
        Chunk("c1", "t", "S", "p", "h", entity_ids=["sig:sig-network"]),
        Chunk("c2", "t", "S", "p", "h", entity_ids=[]),
    ]
    label_chunks(chunks, {"kep-1287": "restricted"})
    assert chunks[0].visibility == "restricted"  # max(restricted, public)
    assert chunks[1].visibility == "public"
    assert chunks[2].visibility == "public"  # no owners -> compose() -> public


def test_fixture_labels_resolve_to_real_nodes(
    community_root: Path, enhancements_root: Path
) -> None:
    # Every labeled entity must exist in the bundled fixture graph, or the demo doesn't bite.
    store = MemoryGraphStore()
    ingest(community_root, enhancements_root, store)
    node_ids = {n.id for n in store.all_nodes()}
    labels = load_labels()
    non_public = [k for k, v in labels.items() if v != "public"]
    assert non_public, "labels.yaml must mark at least one entity above public"
    for entity_id in non_public:
        assert entity_id in node_ids, f"labeled entity {entity_id} missing from fixture graph"


def test_ingest_writes_labeled_nodes_and_edges(
    community_root: Path, enhancements_root: Path
) -> None:
    store = MemoryGraphStore()
    ingest(community_root, enhancements_root, store)

    kep = store.get_node("kep-1287")
    assert kep is not None and kep.props.get("visibility") == "restricted"
    # The OWNS edge into the restricted KEP composed to restricted — the during-traversal
    # filter excludes it for a lower-clearance persona.
    owns = [e for e in store.all_edges() if e.dst_id == "kep-1287" and e.kind == EdgeKind.OWNS]
    assert owns
    assert all(e.props.get("visibility") == "restricted" for e in owns)
    # An unlabeled node stays public.
    net = store.get_node("sig:sig-network")
    assert net is not None and net.props.get("visibility") == "public"


def test_all_packaged_yaml_declared_in_package_data() -> None:
    # Regression for the slice-4 live-deploy finding: labels.yaml existed in the src tree
    # (so src-layout tests passed) but was absent from [tool.setuptools.package-data], so
    # `pip install .` / the Fargate image omitted it and load_labels() crashed live. Guard:
    # every *.yaml under src/graphrag that is loaded as a packaged resource must be declared.
    import tomllib

    repo_root = Path(__file__).resolve().parents[3]
    pkg_root = repo_root / "packages/graphrag/src/graphrag"
    with (repo_root / "pyproject.toml").open("rb") as fh:
        declared = set(tomllib.load(fh)["tool"]["setuptools"]["package-data"]["graphrag"])
    on_disk = {p.relative_to(pkg_root).as_posix() for p in pkg_root.rglob("*.yaml")}
    missing = on_disk - declared
    assert not missing, f"packaged yaml not declared in pyproject package-data: {sorted(missing)}"
