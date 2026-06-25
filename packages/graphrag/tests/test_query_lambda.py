"""T7 — in-VPC query Lambda handler (AC7).

With the embedder, both stores, and the synthesizer mocked, the handler runs the
**same** hybrid_query the CLI uses end-to-end (no network) and returns
{answer, citations, trace, seeds, hops}. The public-ingress posture is asserted: an
over-long question is rejected before orchestration; any internal failure returns a
sanitized envelope (correlation id, NO endpoint/ARN/stack text).

# STUB: AC7
"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from graphrag import query_lambda
from graphrag.chunk import Chunk
from graphrag.model import Direction, Edge, EdgeKind, EntityKind, Node
from graphrag.store.base import GraphStore
from graphrag.store.vector_base import EmbeddedChunk, VectorHit


class _FakeEmbedder:
    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    @property
    def model_id(self) -> str:
        return "fake"

    @property
    def dimensions(self) -> int:
        return 256

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 256 for _ in texts]


class _FakeVectorStore:
    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    def knn(
        self, vector: list[float], k: int, *, allowed_labels: frozenset[str] | None = None
    ) -> list[VectorHit]:
        chunk = Chunk(
            id="ENHANCEMENTS/keps/2086/README.md#0",
            text="Service Internal Traffic Policy.",
            source="ENHANCEMENTS",
            doc_path="keps/2086/README.md",
            heading="Summary",
            entity_ids=["kep-2086", "sig:sig-network"],
            visibility="public",
        )
        if allowed_labels is not None and chunk.visibility not in allowed_labels:
            return []
        return [VectorHit(chunk, 0.5)]

    def index_chunk(self, embedded: EmbeddedChunk) -> None: ...
    def count(self) -> int:
        return 0

    def delete(self, ids: list[str]) -> None: ...


class _FakeGraphStore(GraphStore):
    def __init__(self, *a: Any, **k: Any) -> None:
        self._nodes = {
            "person:thockin": Node("person:thockin", EntityKind.PERSON),
            "sig:sig-network": Node("sig:sig-network", EntityKind.SIG),
            "kep-2086": Node("kep-2086", EntityKind.KEP),
            # a restricted KEP the SIG owns — visible only to a maintainer (slice 4).
            "kep-secret": Node("kep-secret", EntityKind.KEP, props={"visibility": "restricted"}),
        }
        self._edges = [
            Edge("person:thockin", "sig:sig-network", EdgeKind.TECH_LEADS),
            Edge("sig:sig-network", "kep-2086", EdgeKind.OWNS),
            Edge("sig:sig-network", "kep-secret", EdgeKind.OWNS),
        ]

    def get_node(self, node_id: str) -> Node | None:
        return self._nodes.get(node_id)

    def neighbors(
        self,
        node_id: str,
        edge_kind: EdgeKind,
        direction: Direction,
        *,
        allowed_labels: frozenset[str] | None = None,
    ) -> list[Node]:
        out: list[Node] = []
        for e in self._edges:
            if e.kind != edge_kind:
                continue
            if direction is Direction.OUT and e.src_id == node_id:
                target = self._nodes[e.dst_id]
            elif direction is Direction.IN and e.dst_id == node_id:
                target = self._nodes[e.src_id]
            else:
                continue
            # Mirror the real store's during-traversal filter on the neighbor's visibility
            # (default public) so the lambda persona test exercises a real filtered path.
            if (
                allowed_labels is not None
                and str(target.props.get("visibility", "public")) not in allowed_labels
            ):
                continue
            out.append(target)
        return out

    def upsert_node(self, node: Node) -> None: ...
    def upsert_edge(self, edge: Edge) -> None: ...
    def delete_node(self, node_id: str) -> None: ...
    def delete_edge(self, src_id: str, kind: EdgeKind, dst_id: str) -> None: ...
    def clear(self) -> None: ...
    def all_nodes(self) -> list[Node]:
        return list(self._nodes.values())

    def all_edges(self) -> list[Edge]:
        return self._edges


class _FakeSynth:
    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    @property
    def model_id(self) -> str:
        return "fake-claude"

    def synthesize(self, question: str, chunks: Any, facts: Any) -> Any:
        from graphrag.synthesize import SynthesisResult

        return SynthesisResult(answer="grounded answer", citations=["kep-2086"])


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEPTUNE_ENDPOINT", "https://neptune.internal.example:8182")
    monkeypatch.setenv("OPENSEARCH_ENDPOINT", "https://vectors.internal.example")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("SYNTHESIS_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
    monkeypatch.setattr(query_lambda, "BedrockTitanEmbedder", _FakeEmbedder)
    monkeypatch.setattr(query_lambda, "OpenSearchVectorStore", _FakeVectorStore)
    monkeypatch.setattr(query_lambda, "NeptuneGraphStore", _FakeGraphStore)
    monkeypatch.setattr(query_lambda, "BedrockClaudeSynthesizer", _FakeSynth)


def test_bare_question_event(wired: None) -> None:
    result = query_lambda.lambda_handler({"question": "what does @thockin own"}, None)
    assert result["answer"] == "grounded answer"
    assert result["citations"] == ["kep-2086"]
    assert "trace" in result
    assert isinstance(result["seeds"], list)
    assert isinstance(result["hops"], list)
    # @thockin links to person:thockin as a question seed.
    assert any(s["entity_id"] == "person:thockin" for s in result["seeds"])


def test_function_url_event_body_plain(wired: None) -> None:
    event = {"body": json.dumps({"question": "what does @thockin own"}), "isBase64Encoded": False}
    result = query_lambda.lambda_handler(event, None)
    assert result["answer"] == "grounded answer"


def test_function_url_event_body_base64(wired: None) -> None:
    raw = json.dumps({"question": "what does @thockin own"}).encode("utf-8")
    event = {"body": base64.b64encode(raw).decode("ascii"), "isBase64Encoded": True}
    result = query_lambda.lambda_handler(event, None)
    assert result["answer"] == "grounded answer"


def test_over_long_question_rejected(wired: None) -> None:
    event = {"question": "x" * 9000}
    result = query_lambda.lambda_handler(event, None)
    assert "error" in result
    assert "answer" not in result  # orchestration did not run


def test_internal_failure_returns_sanitized_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEPTUNE_ENDPOINT", "https://neptune.internal.example:8182")
    monkeypatch.setenv("OPENSEARCH_ENDPOINT", "https://vectors.internal.example")

    def _boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("neptune endpoint https://neptune.internal.example:8182 blew up")

    monkeypatch.setattr(query_lambda, "BedrockTitanEmbedder", _FakeEmbedder)
    monkeypatch.setattr(query_lambda, "OpenSearchVectorStore", _FakeVectorStore)
    monkeypatch.setattr(query_lambda, "NeptuneGraphStore", _boom)
    monkeypatch.setattr(query_lambda, "BedrockClaudeSynthesizer", _FakeSynth)

    result = query_lambda.lambda_handler({"question": "hi"}, None)
    blob = json.dumps(result)
    assert "error" in result
    assert "correlation_id" in result
    # NO internal endpoint/ARN/stack detail leaks across the public Function URL.
    assert "neptune.internal.example" not in blob
    assert "vectors.internal.example" not in blob
    assert "blew up" not in blob


def test_oversized_raw_body_rejected_before_decode() -> None:
    # A body over the raw-ingress cap is rejected without base64-decoding / JSON-parsing
    # the attacker-controlled input; the sanitized envelope comes back.
    event = {"body": "A" * (query_lambda.MAX_BODY_BYTES + 1)}
    result = query_lambda.lambda_handler(event, None)
    assert "error" in result
    assert "answer" not in result


def test_query_lambda_import_graph_is_pyyaml_free() -> None:
    """The Code.from_asset Lambda bundle excludes PyYAML, so query_lambda and every
    transitive import must load without `import yaml`. Guards the invariant
    packages/graphrag/AGENTS.md documents — a stray `import yaml` would ship a Lambda
    that fails only at deploy/runtime (the 3am failure mode)."""
    import builtins
    import importlib
    import sys

    real_import = builtins.__import__

    def _blocking(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "yaml" or name.startswith("yaml."):
            raise ImportError("yaml is not bundled in the query Lambda")
        return real_import(name, *args, **kwargs)

    def _is_target(mod: str) -> bool:
        return mod == "yaml" or mod.startswith("yaml.") or mod.startswith("graphrag")

    saved = {m: sys.modules.pop(m) for m in list(sys.modules) if _is_target(m)}
    builtins.__import__ = _blocking
    try:
        importlib.import_module("graphrag.query_lambda")  # must not pull in yaml
        # Slice-4: threading `visibility` (pure) must NOT transitively drag in `labels`
        # (which imports yaml) — the read path stays PyYAML-free.
        assert "graphrag.labels" not in sys.modules
    finally:
        builtins.__import__ = real_import
        for m in [m for m in list(sys.modules) if _is_target(m)]:
            del sys.modules[m]
        sys.modules.update(saved)


# --- slice-4: persona permission filter through the Lambda (AC7) ----------------------


def test_persona_filters_restricted_entity(wired: None) -> None:
    reader = query_lambda.lambda_handler(
        {"question": "what does @thockin own", "persona": "public-reader"}, None
    )
    maint = query_lambda.lambda_handler(
        {"question": "what does @thockin own", "persona": "maintainer"}, None
    )
    # divergent: the restricted KEP is absent for the reader, present for the maintainer.
    assert "kep-secret" not in json.dumps(reader)
    assert "kep-secret" in json.dumps(maint)
    # the trace names the persona/clearance (the filtered-out teaching aid).
    assert "public-reader" in reader["trace"]
    assert "not real authz" in reader["trace"]


def test_unknown_persona_returns_sanitized_envelope(wired: None) -> None:
    result = query_lambda.lambda_handler({"question": "hi", "persona": "root"}, None)
    assert "error" in result
    assert "correlation_id" in result
    assert "answer" not in result  # orchestration did not run


def test_no_persona_is_unrestricted(wired: None) -> None:
    result = query_lambda.lambda_handler({"question": "what does @thockin own"}, None)
    # unrestricted: the restricted KEP is reachable (no filter), and no clearance line.
    assert "kep-secret" in json.dumps(result)
    assert "clearance:" not in result["trace"]
