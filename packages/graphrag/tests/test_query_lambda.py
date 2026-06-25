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
from graphrag.store.neptune import NeptuneGraphStore
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
    def replace_node(self, node: Node) -> None: ...
    def replace_edge(self, edge: Edge) -> None: ...
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
        # opencypher-templates: the governed import graph must also stay PyYAML-free (it
        # rides the same Code.from_asset bundle).
        for mod in (
            "graphrag.governed",
            "graphrag.templates",
            "graphrag.select",
            "graphrag.params",
            # text2opencypher-guarded: the text2cypher import graph also rides the bundle.
            "graphrag.text2cypher",
            "graphrag.generate",
            "graphrag.validate",
            "graphrag.cypher_eval",
        ):
            importlib.import_module(mod)
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


# --- opencypher-templates: governed-mode dispatch through the Lambda (AC7) -------------


class _FakeSelector:
    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    @property
    def model_id(self) -> str:
        return "fake-selector"

    def select(self, question: str, templates: Any) -> str:
        return "sig_owned_keps"


def test_governed_mode_returns_audit_envelope(wired: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(query_lambda, "BedrockTemplateSelector", _FakeSelector)
    result = query_lambda.lambda_handler(
        {"question": "Which KEPs does SIG Network own?", "mode": "governed"}, None
    )
    assert result["template_id"] == "sig_owned_keps"
    assert result["params"] == {"sig": "sig:sig-network"}
    # the parameterized cypher is returned literally; the value is in the param map, not inlined.
    assert "$sig" in result["cypher"]
    assert "sig:sig-network" not in result["cypher"]
    assert "kep-2086" in result["rows"]  # the executed rows
    assert result["answer"] == "grounded answer"
    assert "template: sig_owned_keps" in result["trace"]


def test_unknown_mode_is_a_client_error(wired: None) -> None:
    result = query_lambda.lambda_handler({"question": "hi", "mode": "frobnicate"}, None)
    assert "error" in result
    assert "unknown mode" in result["error"]
    assert "answer" not in result  # orchestration did not run


class _NoMatchSelector:
    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    @property
    def model_id(self) -> str:
        return "fake-selector"

    def select(self, question: str, templates: Any) -> str | None:
        return None


def test_governed_no_match_logs_warning_and_returns_reason(
    wired: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    monkeypatch.setattr(query_lambda, "BedrockTemplateSelector", _NoMatchSelector)
    with caplog.at_level(logging.WARNING):
        result = query_lambda.lambda_handler(
            {"question": "what is the weather", "mode": "governed"}, None
        )
    # a governed no-match is a legible result (no query ran), distinct in the log from an "ok".
    assert result["template_id"] is None
    assert result["no_match_reason"]
    assert result["cypher"] == ""
    assert any("governed no-match" in r.message for r in caplog.records)


# --- text2opencypher-guarded: text2cypher-mode dispatch through the Lambda (AC8) ------


class _FakeGenerator:
    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    @property
    def model_id(self) -> str:
        return "fake-generator"

    def generate(self, question: str, schema: str, *, feedback: str | None = None) -> str:
        # a within-subset OWNS hop the offline evaluator can run over the fake store.
        return (
            "MATCH (a:Entity {id: 'sig:sig-network'})-[r:REL {kind: 'OWNS'}]->(n:Entity) "
            "RETURN n LIMIT 25"
        )


class _ScriptedLambdaGenerator:
    """Emits an invalid query first, then a valid one — to drive a multi-attempt self-heal
    *through* the Lambda so the serialized envelope's `attempts` join is exercised."""

    def __init__(self, *a: Any, **k: Any) -> None:
        self._queries = [
            "MATCH (n:Entity) DELETE n RETURN n LIMIT 5",  # rejected by the validator
            "MATCH (a:Entity {id: 'sig:sig-network'})-[r:REL {kind: 'OWNS'}]->(n:Entity) "
            "RETURN n LIMIT 5",  # valid on the heal retry
        ]
        self._i = 0

    @property
    def model_id(self) -> str:
        return "scripted-lambda"

    def generate(self, question: str, schema: str, *, feedback: str | None = None) -> str:
        out = self._queries[self._i] if self._i < len(self._queries) else ""
        self._i += 1
        return out


class _CannedRowsNeptune(NeptuneGraphStore):
    """A REAL NeptuneGraphStore subclass whose live `run_read_query` returns canned rows — so
    the Lambda happy path takes the production `run_read_query` branch (not the offline
    evaluator), matching what the deployed handler actually runs."""

    def run_read_query(self, cypher: str) -> list[Node]:
        return [Node("kep-2086", EntityKind.KEP), Node("kep-1880", EntityKind.KEP)]


def test_text2cypher_mode_self_heals_and_serializes_both_attempts_via_live_branch(
    wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The integrated journey THROUGH the Lambda: generate → reject → self-heal → regenerate →
    # run_read_query (the live branch, real NeptuneGraphStore subclass) → serialize. Guards the
    # multi-attempt envelope join (a regression dropping the rejected attempt would pass the
    # direct-orchestrator tests but fail here).
    monkeypatch.setattr(query_lambda, "BedrockText2CypherGenerator", _ScriptedLambdaGenerator)
    monkeypatch.setattr(query_lambda, "NeptuneGraphStore", _CannedRowsNeptune)
    result = query_lambda.lambda_handler(
        {"question": "Which KEPs does SIG Network own?", "mode": "text2cypher"}, None
    )
    assert result["refusal_reason"] is None
    assert [a["valid"] for a in result["attempts"]] == [False, True]  # rejected then healed
    assert result["attempts"][0]["violated_rule"]  # the rejection is legible
    assert result["executed_query"]
    assert sorted(result["rows"]) == ["kep-1880", "kep-2086"]  # via the live run_read_query branch


class _AccessDeniedNeptune(NeptuneGraphStore):
    """A **real** NeptuneGraphStore subclass whose live read raises an IAM-AccessDenied-shaped
    error — exercises the **live** execution branch (`run_read_query`, the production failure
    mode when the write backstop fires on a validator-missed write), not the offline evaluator.
    `__init__` is inherited (validates the https endpoint; no network at construction)."""

    def run_read_query(self, cypher: str) -> list[Node]:
        raise RuntimeError(
            "AccessDeniedException: User is not authorized to perform "
            "neptune-db:WriteDataViaQuery on resource arn:aws:neptune-db:us-east-1:123:cluster/abc"
        )


def test_text2cypher_mode_returns_audit_envelope(
    wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(query_lambda, "BedrockText2CypherGenerator", _FakeGenerator)
    result = query_lambda.lambda_handler(
        {"question": "Which KEPs does SIG Network own?", "mode": "text2cypher"}, None
    )
    assert result["executed_query"]
    assert "kep-2086" in result["rows"]  # the executed rows from the fake store
    assert result["answer"] == "grounded answer"
    assert result["refusal_reason"] is None
    assert result["attempts"][0]["valid"] is True
    assert "executed query:" in result["trace"]


def test_text2cypher_execution_error_is_sanitized(
    wired: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    monkeypatch.setattr(query_lambda, "BedrockText2CypherGenerator", _FakeGenerator)
    # the REAL NeptuneGraphStore subclass, so text2cypher._execute takes the live
    # run_read_query branch (not the offline evaluator) — the actual production path.
    monkeypatch.setattr(query_lambda, "NeptuneGraphStore", _AccessDeniedNeptune)
    with caplog.at_level(logging.WARNING):
        result = query_lambda.lambda_handler(
            {"question": "Which KEPs does SIG Network own?", "mode": "text2cypher"}, None
        )
    # the validator-missed-write backstop firing as an IAM denial must NOT leak across the URL.
    blob = json.dumps(result)
    assert "AccessDenied" not in blob
    assert "arn:" not in blob
    assert "WriteDataViaQuery" not in blob
    # the caller sees a clean refusal; the attempt records the failure as a boolean only.
    assert result["refusal_reason"]
    assert result["executed_query"] is None
    assert any(a["execution_failed"] for a in result["attempts"])


def test_text2cypher_pyyaml_free_import_graph_guarded() -> None:
    # T8: the guard test above (test_query_lambda_import_graph_is_pyyaml_free) now also imports
    # the text2cypher modules with yaml blocked; this asserts they import cleanly here too.
    import graphrag.cypher_eval  # noqa: F401
    import graphrag.generate  # noqa: F401
    import graphrag.text2cypher  # noqa: F401
    import graphrag.validate  # noqa: F401
