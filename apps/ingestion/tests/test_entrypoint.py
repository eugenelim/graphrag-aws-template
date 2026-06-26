"""T10 — Fargate entrypoint: S3 download + ingest wiring (S3 + store mocked)."""

from __future__ import annotations

import io
import shutil
from pathlib import Path
from typing import Any

from graphrag.embed import HashEmbedder
from graphrag.store import MemoryGraphStore, MemoryVectorStore
from ingestion.entrypoint import run

CORPUS = Path(__file__).parents[3] / "packages/graphrag/tests/fixtures/corpus"


class FakeS3:
    """Serves a corpus directory as an S3 snapshot under a prefix, plus an in-memory object
    store for the manifest (slice-5 get_object/put_object)."""

    def __init__(self, root: Path, prefix: str) -> None:
        self._root = root
        self._prefix = prefix
        self._objects: dict[str, bytes] = {}  # put_object/get_object (the manifest)

    def _corpus_files(self) -> dict[str, Path]:
        return {
            f"{self._prefix}{p.relative_to(self._root).as_posix()}": p
            for p in self._root.rglob("*")
            if p.is_file()
        }

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        prefix = kwargs.get("Prefix", "")
        contents = [{"Key": k} for k in self._corpus_files() if k.startswith(prefix)]
        return {"Contents": contents, "IsTruncated": False}

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:  # noqa: N803
        shutil.copyfile(self._corpus_files()[Key], Filename)

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        if Key not in self._objects:
            raise FileNotFoundError(Key)  # entrypoint treats this as "no prior manifest"
        return {"Body": io.BytesIO(self._objects[Key])}

    def put_object(self, Bucket: str, Key: str, Body: bytes) -> dict[str, Any]:  # noqa: N803
        self._objects[Key] = Body
        return {}


def test_download_rejects_keys_that_escape_dest(tmp_path: Path) -> None:
    import pytest

    from ingestion.entrypoint import download_corpus

    class EvilS3:
        def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
            return {"Contents": [{"Key": "snap/../../../../tmp/evil.txt"}], "IsTruncated": False}

        def download_file(self, Bucket: str, Key: str, Filename: str) -> None:  # noqa: N803
            raise AssertionError("must not download a path-traversal key")

        def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
            raise AssertionError("unused")

        def put_object(self, Bucket: str, Key: str, Body: bytes) -> dict[str, Any]:  # noqa: N803
            raise AssertionError("unused")

    with pytest.raises(ValueError, match="escapes the corpus dir"):
        download_corpus("b", "snap/", tmp_path, EvilS3())


def test_entrypoint_downloads_and_ingests() -> None:
    from graphrag.ingest import IngestReport

    store = MemoryGraphStore()
    report = run(
        {"CORPUS_BUCKET": "demo-bucket", "CORPUS_PREFIX": "snap/", "AWS_REGION": "us-east-1"},
        s3_client=FakeS3(CORPUS, "snap/"),
        store=store,
    )
    # The deployed path runs the same ingest as the CLI: same nodes, same merges.
    assert isinstance(report, IngestReport)
    assert report.nodes == 22
    assert "sig:sig-network" in report.merges
    assert len(store.all_nodes()) == 22


def test_entrypoint_dual_writes_graph_and_vector() -> None:
    # One parse, two stores (charter pattern 2): the graph and vector indices are
    # written from the same corpus read so they can't diverge.
    graph = MemoryGraphStore()
    vectors = MemoryVectorStore()
    run(
        {"CORPUS_BUCKET": "demo-bucket", "CORPUS_PREFIX": "snap/", "AWS_REGION": "us-east-1"},
        s3_client=FakeS3(CORPUS, "snap/"),
        store=graph,
        vector_store=vectors,
        embedder=HashEmbedder(),
    )
    assert graph.all_nodes()  # graph half written
    assert vectors.count() > 0  # vector half written from the same parse


def _env(mode: str = "full") -> dict[str, str]:
    return {
        "CORPUS_BUCKET": "demo-bucket",
        "CORPUS_PREFIX": "snap/",
        "AWS_REGION": "us-east-1",
        "MODE": mode,
    }


def test_full_mode_writes_manifest_last() -> None:
    s3 = FakeS3(CORPUS, "snap/")
    run(_env("full"), s3_client=s3, store=MemoryGraphStore())
    # The manifest is persisted so the next --delta has a baseline (AC8).
    assert "snap/manifest.json" in s3._objects
    assert b'"docs"' in s3._objects["snap/manifest.json"]


def test_delta_mode_reads_manifest_runs_delta_and_rewrites_it(tmp_path: Path) -> None:
    from graphrag.ingest import DeltaReport

    corpus = tmp_path / "corpus"
    shutil.copytree(CORPUS, corpus)
    s3 = FakeS3(corpus, "snap/")
    graph, vectors = MemoryGraphStore(), MemoryVectorStore()
    # Seed: a full ingest writes the baseline manifest into S3.
    run(_env("full"), s3_client=s3, store=graph, vector_store=vectors, embedder=HashEmbedder())
    baseline = s3._objects["snap/manifest.json"]

    # Mutate the snapshot: add a new KEP, then run MODE=delta.
    new_kep = corpus / "enhancements" / "keps" / "sig-node" / "4242-brand-new"
    new_kep.mkdir(parents=True)
    (new_kep / "kep.yaml").write_text(
        "kep-number: 4242\ntitle: New\nstatus: provisional\nowning-sig: sig-node\n",
        encoding="utf-8",
    )
    (new_kep / "README.md").write_text("# New\n\nProse.\n", encoding="utf-8")

    report = run(
        _env("delta"), s3_client=s3, store=graph, vector_store=vectors, embedder=HashEmbedder()
    )
    assert isinstance(report, DeltaReport)
    assert not report.full_ingest  # a real manifest was read back
    assert graph.get_node("kep-4242") is not None
    assert s3._objects["snap/manifest.json"] != baseline  # manifest rewritten last


def test_rebuild_mode_clears_then_reingests(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    shutil.copytree(CORPUS, corpus)
    s3 = FakeS3(corpus, "snap/")
    graph, vectors = MemoryGraphStore(), MemoryVectorStore()
    from graphrag.model import EntityKind, Node

    graph.upsert_node(Node("kep-stale", EntityKind.KEP, doc_paths={"gone/x"}))
    run(_env("rebuild"), s3_client=s3, store=graph, vector_store=vectors, embedder=HashEmbedder())
    assert graph.get_node("kep-stale") is None  # cleared
    assert graph.get_node("kep-2086") is not None  # reingested
    assert "snap/manifest.json" in s3._objects


class CountingEmbedder:
    """Wraps HashEmbedder and counts embed() calls — to prove the parent-child index reuses
    the flat dual-write's embeddings (one embed pass, not two)."""

    def __init__(self) -> None:
        self._inner = HashEmbedder()
        self.calls = 0
        self.embedded_texts: list[str] = []

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        self.embedded_texts = list(texts)
        return self._inner.embed(texts)


def test_full_mode_dual_writes_parentchild_index_from_one_embed_pass() -> None:
    # AC4: the parent-child nested index is written from the SAME parse+embed pass as the flat
    # index — the embedder is invoked exactly once (no second embed pass for the second index).
    from graphrag.store.parentchild_memory import MemoryParentChildStore

    graph = MemoryGraphStore()
    vectors = MemoryVectorStore()
    parents = MemoryParentChildStore()
    embedder = CountingEmbedder()
    run(
        _env("full"),
        s3_client=FakeS3(CORPUS, "snap/"),
        store=graph,
        vector_store=vectors,
        embedder=embedder,
        parentchild_store=parents,
    )
    assert embedder.calls == 1  # ONE embed pass shared by both indexes (no re-embed)
    assert vectors.count() > 0  # flat index written
    assert parents.count() > 0  # nested parent-child index written from the same vectors

    # every parent carries the document's full body, ordered children, entity_ids, visibility
    sample = parents.search(embedder.embed(["pod resize"])[0], 1)
    assert sample
    parent = sample[0].parent
    assert parent.body  # the app-stored full parent body
    assert parent.children  # the nested child chunks (with vectors)
    assert [c.child_id for c in parent.children] == sorted(
        (c.child_id for c in parent.children), key=lambda cid: int(cid.rsplit("#", 1)[1])
    )
    # a restricted doc's parent inherits the restricted tier (consistent with the flat labels)
    visibilities = {p.parent.visibility for p in parents.search(embedder.embed(["x"])[0], 50)}
    assert "restricted" in visibilities
    assert "public" in visibilities


def test_full_mode_no_parentchild_store_and_no_endpoint_is_a_noop() -> None:
    # AC4: absent both an injected parent-child store and OPENSEARCH_ENDPOINT, the parent-child
    # write is a no-op — the flat-only / graph-only deploys are unchanged.
    graph = MemoryGraphStore()
    vectors = MemoryVectorStore()
    # no parentchild_store, no OPENSEARCH_ENDPOINT in env → no parent-child write, no error
    run(
        _env("full"),
        s3_client=FakeS3(CORPUS, "snap/"),
        store=graph,
        vector_store=vectors,
        embedder=HashEmbedder(),
    )
    assert vectors.count() > 0  # flat write still happens; parent-child simply skipped


class CountingSynth:
    """A deterministic synthesizer that counts synthesize() calls — to prove community
    summarization runs exactly once per detected community (and not at all on delta)."""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def model_id(self) -> str:
        return "counting-offline"

    def synthesize(
        self, question: str, context_chunks: list[Any], graph_facts: list[Any]
    ) -> Any:
        from graphrag.synthesize import SynthesisResult

        self.calls += 1
        ids = ",".join(n.id for n in graph_facts)
        return SynthesisResult(answer=f"summary[{ids}]", citations=[])


def test_full_mode_writes_communities_one_summary_each() -> None:
    # AC5: full ingest detects communities from the written graph and summarizes each ONCE.
    from graphrag.store.community_memory import MemoryCommunityStore

    graph = MemoryGraphStore()
    communities = MemoryCommunityStore()
    synth = CountingSynth()
    run(
        _env("full"),
        s3_client=FakeS3(CORPUS, "snap/"),
        store=graph,
        community_store=communities,
        synthesizer=synth,
    )
    stored = communities.all_communities()
    assert stored  # communities were written
    assert synth.calls == len(stored)  # exactly one summarize call per community
    # communityId stamped on members (the entity→community trace affordance)
    member = stored[0].entity_ids[0]
    assert communities.community_of(member) == stored[0].id
    # every member entity resolves in the written graph
    for community in stored:
        for entity_id in community.entity_ids:
            assert graph.get_node(entity_id) is not None


def test_full_mode_no_community_store_and_no_neptune_is_a_noop() -> None:
    # AC5: absent both an injected community store and NEPTUNE_ENDPOINT, the write-back is a
    # no-op (a vector-only deploy is unchanged) — no error, graph still written.
    graph = MemoryGraphStore()
    run(_env("full"), s3_client=FakeS3(CORPUS, "snap/"), store=graph)
    assert graph.all_nodes()  # the graph half still written; community write-back simply skipped


def test_delta_mode_does_not_recompute_communities() -> None:
    # AC5: MODE=delta never recomputes communities (scoped out — full/rebuild rebuild them).
    from graphrag.store.community_memory import MemoryCommunityStore

    graph, vectors = MemoryGraphStore(), MemoryVectorStore()
    communities = MemoryCommunityStore()
    # Seed a baseline manifest with a full ingest (communities written here).
    run(
        _env("full"),
        s3_client=(s3 := FakeS3(CORPUS, "snap/")),
        store=graph,
        vector_store=vectors,
        embedder=HashEmbedder(),
        community_store=communities,
        synthesizer=CountingSynth(),
    )
    seeded = communities.count()
    assert seeded > 0
    # A delta run with a FRESH counting synth: it must never be invoked (no recompute).
    delta_synth = CountingSynth()
    run(
        _env("delta"),
        s3_client=s3,
        store=graph,
        vector_store=vectors,
        embedder=HashEmbedder(),
        community_store=communities,
        synthesizer=delta_synth,
    )
    assert delta_synth.calls == 0  # delta did not summarize any community
    assert communities.count() == seeded  # community set unchanged by delta


def test_entrypoint_dual_write_labels_chunks() -> None:
    # Slice 4: the same dual-write stamps synthetic visibility on every chunk, so the
    # vector store carries the permission-filter metadata consistent with the graph labels.
    graph = MemoryGraphStore()
    vectors = MemoryVectorStore()
    run(
        {"CORPUS_BUCKET": "demo-bucket", "CORPUS_PREFIX": "snap/", "AWS_REGION": "us-east-1"},
        s3_client=FakeS3(CORPUS, "snap/"),
        store=graph,
        vector_store=vectors,
        embedder=HashEmbedder(),
    )
    visibilities = {ec.chunk.visibility for ec in vectors._items.values()}
    # kep-1287 is labeled restricted in labels.yaml; its README chunks inherit it.
    assert "restricted" in visibilities
    # chunks owned only by public entities stay public.
    assert "public" in visibilities
