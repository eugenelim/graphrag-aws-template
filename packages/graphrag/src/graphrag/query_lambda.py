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
from .hybrid import HybridResult, hybrid_query
from .store.neptune import NeptuneGraphStore
from .store.opensearch import OpenSearchVectorStore
from .synthesize import DEFAULT_SYNTHESIS_MODEL_ID, BedrockClaudeSynthesizer

logger = logging.getLogger(__name__)

# Bounded question length — a few KB caps a runaway / DoS input at the public ingress
# (AC7). The curated demo questions are well under this.
MAX_QUESTION_BYTES = 8192
# Cap the raw body *before* base64-decode / JSON-parse, so the ingress guard protects
# the decode path too (not only the orchestration). 64 KB leaves generous headroom over
# MAX_QUESTION_BYTES while staying far under the AWS Function-URL 6 MB payload cap.
MAX_BODY_BYTES = 65536


def _extract_question(event: Any) -> str:
    """Parse the question from a Function-URL event (``body``, base64-aware) or a bare
    ``{"question": ...}`` event."""
    if isinstance(event, dict) and "body" in event:
        body = event.get("body") or ""
        # Length-check the raw body before decoding/parsing the attacker-controlled input.
        if len(body) > MAX_BODY_BYTES:
            raise ValueError("request body too large")
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")
        parsed = json.loads(body) if body else {}
    elif isinstance(event, dict):
        parsed = event
    else:
        parsed = {}
    question = parsed.get("question", "") if isinstance(parsed, dict) else ""
    return question if isinstance(question, str) else ""


def lambda_handler(event: Any, context: Any) -> dict[str, Any]:
    correlation_id = uuid.uuid4().hex
    try:
        question = _extract_question(event)
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
