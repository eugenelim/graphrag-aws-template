"""In-VPC query Lambda handler — the live hybrid-query path (slice-3 AC7).

The twin of the slice-1/2 smoke probes, but the *product* path: a scale-to-zero
Lambda in the private isolated subnets, behind an **IAM-auth Function URL** (the only
public ingress; ADR-0002). It reads the deployed endpoints + synthesis model id from
the environment, builds the live stores + Titan embedder + Bedrock Claude synthesizer
from the execution role, runs the **same** ``hybrid_query`` the CLI uses, and returns
``{answer, citations, trace, seeds, hops}``. Because it reuses the real orchestration,
a green live result proves the real path, not a reimplementation.

Public-ingress posture (AC7):

- An **over-long question** is rejected before any orchestration runs (a bounded input
  length — info/DoS guard at the boundary).
- On **any** failure, the handler returns a **generic, sanitized error envelope** (a
  correlation id, no internal endpoint / ARN / stack detail) and logs the real detail
  to CloudWatch. The loud-raise-with-body posture stays on the CLI / adapter side,
  in-VPC — it never crosses the Function URL (information-disclosure boundary).

**Pure-Python Lambda / PyYAML-free import graph (critical).** This module and its
transitive imports must NOT import ``pyyaml`` at module load (the ``Code.from_asset``
Lambda bundle ships boto3/botocore from the runtime but excludes pyyaml). It therefore
imports only ``hybrid``/``synthesize``/``embed``/``store``/``model`` — none of which
pull in ``parse``/``sources``/``resolve`` (PyYAML).

**Aliases decision (deliberate, commented).** Entity-linking normally takes the
slice-1 **display-name → handle** alias table from ``resolve.load_aliases()`` — but
that loader uses ``yaml``, which is absent from the Lambda bundle. So the live handler
runs ``link_question`` with ``aliases={}``: the mechanical normalizers (``@handles``,
SIG slugs, KEP numbers) still resolve without the alias table; only bare *display
names* (e.g. "Tim Hockin") would need it, and the curated demo questions use
``@handles`` / slugs / KEP numbers, not bare display names. Bundling the alias table as
already-parsed data was the alternative; ``{}`` keeps the bundle pyyaml-free with no
loss for the demo vocabulary (see ``packages/graphrag/AGENTS.md``).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import uuid
from dataclasses import asdict
from typing import Any

from .embed import BedrockTitanEmbedder
from .generate import BedrockText2CypherGenerator
from .globalsearch import GlobalSearchResult, global_query
from .governed import GovernedResult, governed_query
from .hybrid import HybridResult, hybrid_query
from .parentchild import ParentChildResult, parentchild_query
from .select import BedrockTemplateSelector
from .selfquery import BedrockMetadataExtractor, SelfQueryResult, selfquery_query
from .store.community_neptune import NeptuneCommunityStore
from .store.neptune import NeptuneGraphStore
from .store.opensearch import OpenSearchVectorStore
from .store.parentchild_opensearch import OpenSearchParentChildStore
from .synthesize import DEFAULT_SYNTHESIS_MODEL_ID, BedrockClaudeSynthesizer
from .text2cypher import Text2CypherResult, text2cypher_query
from .visibility import resolve_clearance

logger = logging.getLogger(__name__)

# Bounded question length — a few KB caps a runaway / DoS input at the public ingress
# (AC7). The curated demo questions are well under this.
MAX_QUESTION_BYTES = 8192
# Cap the raw body *before* base64-decode / JSON-parse, so the ingress guard protects
# the decode path too (not only the orchestration). 64 KB leaves generous headroom over
# MAX_QUESTION_BYTES while staying far under the AWS Function-URL 6 MB payload cap.
MAX_BODY_BYTES = 65536


def _parse_payload(event: Any) -> dict[str, Any]:
    """Parse a Function-URL event (``body``, base64-aware) or a bare event dict into the
    request payload dict. The raw body is length-checked before any decode/parse of the
    attacker-controlled input (the ingress guard protects the decode path too)."""
    if isinstance(event, dict) and "body" in event:
        body = event.get("body") or ""
        if len(body) > MAX_BODY_BYTES:
            raise ValueError("request body too large")
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")
        parsed = json.loads(body) if body else {}
    elif isinstance(event, dict):
        parsed = event
    else:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_question(payload: dict[str, Any]) -> str:
    question = payload.get("question", "")
    return question if isinstance(question, str) else ""


def _extract_persona(payload: dict[str, Any]) -> str | None:
    """The optional ``persona`` (slice-4 permission filter); absent ⇒ unrestricted."""
    persona = payload.get("persona")
    return persona if isinstance(persona, str) and persona else None


def _extract_mode(payload: dict[str, Any]) -> str:
    """The optional ``mode`` (``hybrid`` default | ``governed`` | ``text2cypher`` |
    ``selfquery`` | ``parentchild`` | ``global``) — the additive, back-compat Function-URL field
    (``governed`` added by opencypher-templates, ``text2cypher`` by text2opencypher-guarded,
    ``selfquery`` by metadata-filtering, ``parentchild`` by parent-child-retrieval, ``global`` by
    global-community-summary). An absent or non-string mode is ``hybrid``, so an existing caller
    is unaffected."""
    mode = payload.get("mode")
    return mode if isinstance(mode, str) and mode else "hybrid"


def lambda_handler(event: Any, context: Any) -> dict[str, Any]:
    correlation_id = uuid.uuid4().hex
    try:
        payload = _parse_payload(event)
        question = _extract_question(payload)
        if not question:
            return {"error": "missing 'question'", "correlation_id": correlation_id}
        # Reject an over-long question before any orchestration runs.
        if len(question.encode("utf-8")) > MAX_QUESTION_BYTES:
            return {
                "error": f"question exceeds {MAX_QUESTION_BYTES} bytes",
                "correlation_id": correlation_id,
            }

        region = os.environ.get("AWS_REGION", "us-east-1")
        model_id = os.environ.get("SYNTHESIS_MODEL_ID", DEFAULT_SYNTHESIS_MODEL_ID)

        # Mode dispatch (additive, back-compat): absent ⇒ hybrid. The governed path runs the
        # Cypher-Templates orchestration (select a vetted template → bind validated params →
        # run the parameterized openCypher → synthesize); it reuses the same Neptune
        # data-access + synthesis-model Converse grant the hybrid path already holds.
        mode = _extract_mode(payload)
        if mode == "governed":
            graph_store_g = NeptuneGraphStore(os.environ["NEPTUNE_ENDPOINT"], region)
            selector = BedrockTemplateSelector(model_id=model_id, region=region)
            synthesizer_g = BedrockClaudeSynthesizer(model_id=model_id, region=region)
            governed = governed_query(
                question,
                graph_store=graph_store_g,
                selector=selector,
                synthesizer=synthesizer_g,
                aliases={},  # PyYAML-free Lambda: mechanical normalizers only (no alias table)
            )
            if governed.no_match_reason is not None:
                # A live mis-selection / unbindable param must be diagnosable from CloudWatch
                # and distinct from a real empty result (no question text — no PII).
                logger.warning(
                    "query_lambda governed no-match (correlation_id=%s) reason=%s",
                    correlation_id,
                    governed.no_match_reason,
                )
            else:
                logger.info(
                    "query_lambda governed ok (correlation_id=%s) template=%s rows=%d",
                    correlation_id,
                    governed.template_id,
                    len(governed.rows),
                )
            return _serialize_governed(governed)
        if mode == "text2cypher":
            # The flexible (risky) path: the LLM WRITES the openCypher; it is validated
            # read-only + bounded-self-healed, and executed under the query Lambda's READ-ONLY
            # Neptune grant (ADR-0004 — a validator-missed write is denied by IAM at the engine).
            graph_store_t = NeptuneGraphStore(os.environ["NEPTUNE_ENDPOINT"], region)
            generator = BedrockText2CypherGenerator(model_id=model_id, region=region)
            synthesizer_t = BedrockClaudeSynthesizer(model_id=model_id, region=region)
            t2c = text2cypher_query(
                question,
                graph_store=graph_store_t,
                generator=generator,
                synthesizer=synthesizer_t,
            )
            if t2c.refusal_reason is not None:
                # A refusal must be diagnosable from CloudWatch and distinct from a real empty
                # result (no question text — no PII; no raw Neptune error — that stays internal).
                logger.warning(
                    "query_lambda text2cypher refusal (correlation_id=%s) reason=%s attempts=%d",
                    correlation_id,
                    t2c.refusal_reason,
                    len(t2c.attempts),
                )
            else:
                logger.info(
                    "query_lambda text2cypher ok (correlation_id=%s) rows=%d attempts=%d",
                    correlation_id,
                    len(t2c.rows),
                    len(t2c.attempts),
                )
            return _serialize_text2cypher(t2c)

        # The optional persona (slice-4 permission filter) is resolved fail-closed for the
        # filtering modes (hybrid + selfquery). An unknown persona is a client error (it is
        # user-supplied, not internal) — never a silent fall-through to unrestricted.
        persona = _extract_persona(payload)
        try:
            clearance = resolve_clearance(persona) if persona is not None else None
        except ValueError:
            return {"error": "unknown persona", "correlation_id": correlation_id}

        if mode == "selfquery":
            # The self-query path: Bedrock extracts a structured filter (source/entity_ids) from
            # the question; the vector search applies it DURING the ANN scan, composed with the
            # persona clearance. It reuses the same OpenSearch data-access + synthesis-model
            # Converse grant the hybrid path holds — and builds NO Neptune store (entity
            # validation is pure controlled-vocab resolution, so the path never touches the graph).
            vector_store_s = OpenSearchVectorStore(os.environ["OPENSEARCH_ENDPOINT"], region)
            embedder_s = BedrockTitanEmbedder(region=region)
            extractor = BedrockMetadataExtractor(model_id=model_id, region=region)
            synthesizer_s = BedrockClaudeSynthesizer(model_id=model_id, region=region)
            selfq = selfquery_query(
                question,
                extractor=extractor,
                vector_store=vector_store_s,
                embedder=embedder_s,
                synthesizer=synthesizer_s,
                aliases={},  # PyYAML-free Lambda: mechanical normalizers only (no alias table)
                mode="vector",
                clearance=clearance,
            )
            logger.info(
                "query_lambda selfquery ok (correlation_id=%s) filter_fields=%s hits=%d",
                correlation_id,
                ",".join(sorted(selfq.extraction.filter.terms)) or "(none)",
                len(selfq.hits),
            )
            return _serialize_selfquery(selfq)

        if mode == "parentchild":
            # The Parent-Child Retriever path: a small child chunk's vector is matched (precise)
            # on the nested index, the larger parent document BODY is returned for
            # context-complete synthesis, composed with the persona clearance. Vector-only — it
            # builds NO Neptune store (the same posture + grants as the self-query branch above).
            parent_store = OpenSearchParentChildStore(os.environ["OPENSEARCH_ENDPOINT"], region)
            embedder_pc = BedrockTitanEmbedder(region=region)
            synthesizer_pc = BedrockClaudeSynthesizer(model_id=model_id, region=region)
            pchild = parentchild_query(
                question,
                store=parent_store,
                embedder=embedder_pc,
                synthesizer=synthesizer_pc,
                clearance=clearance,
            )
            logger.info(
                "query_lambda parentchild ok (correlation_id=%s) hits=%d",
                correlation_id,
                len(pchild.hits),
            )
            return _serialize_parentchild(pchild)

        if mode == "global":
            # The Global Community Summary path (MS GraphRAG global): map-reduce a corpus-wide
            # answer over per-community summaries, clearance-gated. Reads pre-computed Community
            # nodes from Neptune through a READ-ONLY store — the existing read-only Neptune grant
            # (ADR-0004) suffices — and DETECTS NOTHING (no networkx in the Lambda). The persona
            # was already resolved fail-closed above (this branch sits after that block).
            community_store = NeptuneCommunityStore(os.environ["NEPTUNE_ENDPOINT"], region)
            synthesizer_g = BedrockClaudeSynthesizer(model_id=model_id, region=region)
            gresult = global_query(
                question,
                community_store=community_store,
                synthesizer=synthesizer_g,
                clearance=clearance,
            )
            logger.info(
                "query_lambda global ok (correlation_id=%s) considered=%d survivors=%d",
                correlation_id,
                len(gresult.communities_considered),
                sum(1 for v in gresult.map_verdicts if v.relevant),
            )
            return _serialize_global(gresult)

        if mode != "hybrid":
            return {"error": f"unknown mode {mode!r}", "correlation_id": correlation_id}

        graph_store = NeptuneGraphStore(os.environ["NEPTUNE_ENDPOINT"], region)
        vector_store = OpenSearchVectorStore(os.environ["OPENSEARCH_ENDPOINT"], region)
        embedder = BedrockTitanEmbedder(region=region)
        synthesizer = BedrockClaudeSynthesizer(model_id=model_id, region=region)

        result = hybrid_query(
            question,
            vector_store=vector_store,
            graph_store=graph_store,
            embedder=embedder,
            synthesizer=synthesizer,
            # PyYAML-free Lambda: entity-linking runs with the mechanical normalizers
            # only (no display-name alias table — see module docstring).
            aliases={},
            clearance=clearance,
        )
        # Happy-path log: tie the correlation id to seed/hop counts (no question text,
        # no payload) so a live mis-seed is diagnosable from CloudWatch, not only the
        # caller's screen.
        logger.info(
            "query_lambda ok (correlation_id=%s) seeds=%d hops=%d citations=%d",
            correlation_id,
            len(result.seeds),
            len(result.hop_trace.trace),
            len(result.citations),
        )
        return _serialize(result)
    except Exception:  # noqa: BLE001 - the boundary: log detail, return a sanitized envelope
        # Log the real detail (endpoints/ARNs/stack) to CloudWatch only.
        logger.exception("query_lambda failure (correlation_id=%s)", correlation_id)
        return {
            "error": "internal error processing the query",
            "correlation_id": correlation_id,
        }


def _serialize_governed(result: GovernedResult) -> dict[str, Any]:
    """Shape the GovernedResult into the Function-URL audit envelope (no internal detail).

    The cypher and the parameter map are returned **separately** (never interpolated), so a
    caller sees exactly which vetted query ran with which validated values."""
    return {
        "template_id": result.template_id,
        "template_description": result.template_description,
        "params": dict(result.param_map),
        "bound_params": [asdict(bp) for bp in result.bound_params],
        "cypher": result.cypher,
        "rows": [node.id for node in result.rows],
        "answer": result.answer,
        "citations": list(result.citations),
        "trace": result.render(),
        "no_match_reason": result.no_match_reason,
    }


def _sanitized_text2cypher_trace(result: Text2CypherResult) -> str:
    """The audit trace for the caller — narrates question → schema → generated query (+ verdict)
    → executed query → rows → answer, but **omits the raw execution error** (which can carry
    Neptune schema / an IAM ARN). The full ``render()`` with the raw error stays in-VPC / on the
    CLI; the boundary sees only the verdict and an "execution failed" flag."""
    lines = [f"question: {result.question}", "schema:", result.schema, "generated attempts:"]
    for index, attempt in enumerate(result.attempts, start=1):
        verdict = (
            "valid" if attempt.validation.ok else f"rejected: {attempt.validation.violated_rule}"
        )
        lines.append(f"  {index}. {attempt.query}")
        lines.append(f"     verdict: {verdict}")
        if attempt.error is not None:
            lines.append("     execution: failed (detail in server logs)")
    if result.refusal_reason is not None:
        lines.append(f"refusal: {result.refusal_reason}")
        lines.append("(no query executed)")
        return "\n".join(lines)
    lines.append(f"executed query: {result.executed_query}")
    lines.append(f"rows: {', '.join(n.id for n in result.rows) or '(none)'}")
    lines.append(f"answer: {result.answer}")
    return "\n".join(lines)


def _serialize_text2cypher(result: Text2CypherResult) -> dict[str, Any]:
    """Shape the Text2CypherResult into the Function-URL audit envelope (no internal detail).

    The generated queries and their validation **verdicts** are returned (the audit value), but
    the raw execution error is **not** — an execution failure is a boolean, so a Neptune error /
    IAM ARN never crosses the Function URL (the validator-missed-write backstop firing surfaces
    as a clean refusal, not a schema leak)."""
    return {
        "schema": result.schema,
        "attempts": [
            {
                "query": attempt.query,
                "valid": attempt.validation.ok,
                "violated_rule": attempt.validation.violated_rule,
                "execution_failed": attempt.error is not None,
            }
            for attempt in result.attempts
        ],
        "executed_query": result.executed_query,
        "rows": [node.id for node in result.rows],
        "answer": result.answer,
        "citations": list(result.citations),
        "trace": _sanitized_text2cypher_trace(result),
        "refusal_reason": result.refusal_reason,
    }


def _serialize_selfquery(result: SelfQueryResult) -> dict[str, Any]:
    """Shape the SelfQueryResult into the Function-URL envelope (no internal detail).

    The extracted+validated filter and what the validator dropped are returned (the audit
    value — exactly which structured filter the model produced and how it was bounded)."""
    return {
        "mode": result.mode,
        "extracted_filter": {k: list(v) for k, v in result.extraction.filter.terms.items()},
        "dropped": [asdict(d) for d in result.extraction.dropped],
        "hits": [hit.chunk.id for hit in result.hits],
        "answer": result.answer,
        "citations": list(result.citations),
        "trace": result.render(),
    }


def _serialize_parentchild(result: ParentChildResult) -> dict[str, Any]:
    """Shape the ParentChildResult into the Function-URL envelope (no internal detail).

    Returns the matched **children** (the precise match) and the returned **parents** (the
    parent ids — the units whose full body synthesis read), so a caller sees both halves of the
    decoupling the pattern is about."""
    return {
        "hits": [hit.parent.parent_id for hit in result.hits],
        "matched_children": [
            hit.matched_child.child_id if hit.matched_child is not None else None
            for hit in result.hits
        ],
        "answer": result.answer,
        "citations": list(result.citations),
        "trace": result.render(),
    }


def _serialize_global(result: GlobalSearchResult) -> dict[str, Any]:
    """Shape the GlobalSearchResult into the Function-URL envelope (no internal detail).

    Returns the communities considered (id/tier/size — never an above-clearance community, which
    was filtered before the map), the per-community map verdicts, the reduced answer, and the
    citations composed in ``global_query`` (community ids + member docs, no synthetic
    provenance)."""
    return {
        "communities": [
            {"id": c.id, "tier": c.tier, "size": c.size, "title": c.title}
            for c in result.communities_considered
        ],
        "map_verdicts": [
            {"community_id": v.community_id, "relevant": v.relevant} for v in result.map_verdicts
        ],
        "answer": result.answer,
        "citations": list(result.citations),
        "trace": result.render(),
    }


def _serialize(result: HybridResult) -> dict[str, Any]:
    """Shape the HybridResult into the Function-URL response (no internal detail)."""
    return {
        "answer": result.answer,
        "citations": list(result.citations),
        "trace": result.render(),
        "seeds": [asdict(s) for s in result.seeds],
        "hops": [
            {
                "hop": entry.hop,
                "frontier_in": list(entry.frontier_in),
                "reached": list(entry.reached),
                "edge_kinds": [ek.value for ek in entry.edge_kinds],
                "truncated": entry.truncated,
            }
            for entry in result.hop_trace.trace
        ],
    }
