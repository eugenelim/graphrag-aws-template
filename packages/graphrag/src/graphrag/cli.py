"""``graphrag`` CLI — ingest, multi-hop graph-query (with trace), and resolve-eval.

Every verb prints a human-readable trace (charter principle 1 / AC10): ingest
prints the resolution report, graph-query prints the seed→hop→result trace, and
resolve-eval prints the precision/recall of the open confirmation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .chunk import chunk_corpus
from .compare import run_modes
from .delta import manifest_from_json, manifest_to_json
from .embed import BedrockTitanEmbedder, Embedder, HashEmbedder
from .eval import evaluate, load_labeled_sample
from .extract_llm import BedrockTripleExtractor, RuleTripleExtractor, TripleExtractor
from .generate import (
    BedrockText2CypherGenerator,
    RuleText2CypherGenerator,
    Text2CypherGenerator,
)
from .globalsearch import global_query
from .governed import governed_query
from .hybrid import hybrid_query
from .ingest import ingest, ingest_delta, rebuild
from .labels import label_chunks, load_labels
from .model import Direction, EdgeKind
from .normalize import kep_id, person_id, sig_id
from .parentchild import group_into_parents, parentchild_query
from .query import Step, traverse
from .resolve import load_aliases, resolve
from .schema_extract import extract_schema_guided
from .select import BedrockTemplateSelector, RuleTemplateSelector, TemplateSelector
from .selfquery import (
    BedrockMetadataExtractor,
    MetadataExtractor,
    RuleMetadataExtractor,
    selfquery_query,
)
from .sources import load_corpus
from .store.base import GraphStore
from .store.memory import MemoryGraphStore
from .store.neptune import HttpClient, HttpResponse, _UrllibClient
from .store.parentchild_base import ParentChildStore
from .store.parentchild_memory import MemoryParentChildStore
from .store.vector_base import EmbeddedChunk, VectorStore
from .store.vector_memory import MemoryVectorStore
from .synthesize import (
    DEFAULT_SYNTHESIS_MODEL_ID,
    BedrockClaudeSynthesizer,
    Synthesizer,
    TemplateSynthesizer,
)
from .text2cypher import text2cypher_query
from .vector import vector_search
from .vector_eval import (
    evaluate_query_set,
    freeze_embeddings,
    load_frozen,
    load_query_set,
)
from .visibility import PERSONAS, Clearance, resolve_clearance

# Default region for SigV4 signing when a Function URL doesn't encode one.
_DEFAULT_REGION = "us-east-1"
_LAMBDA_SERVICE = "lambda"
# Read timeout (s) for the live Function-URL client — long enough for a cold VPC
# Lambda + the full hybrid path, not the single-hop 30s default.
_FUNCTION_URL_TIMEOUT = 150
# Region embedded in a Function URL host: <id>.lambda-url.<region>.on.aws
_FUNCTION_URL_REGION = re.compile(r"\.lambda-url\.([a-z0-9-]+)\.on\.aws")


def _target_store(args: argparse.Namespace) -> GraphStore:
    """The destination store — a Neptune adapter if an endpoint is given, else a
    fresh in-memory store. (One place owns the lazy, deploy-only Neptune import.)"""
    if getattr(args, "neptune_endpoint", None):
        from .store.neptune import NeptuneGraphStore  # imported lazily (deploy-only path)

        return NeptuneGraphStore(args.neptune_endpoint, args.region)
    return MemoryGraphStore()


def _populated_store(args: argparse.Namespace) -> GraphStore:
    """A store ready to query — Neptune as-is (already ingested), or in-memory
    populated by ingesting the corpus now."""
    store = _target_store(args)
    if isinstance(store, MemoryGraphStore):
        ingest(Path(args.community), Path(args.enhancements), store)
    return store


def _seed_id(start: str, kind: str, aliases: dict[str, str]) -> str:
    if ":" in start or start.startswith("kep-"):
        return start  # already a node id
    if kind == "sig":
        return sig_id(start)
    if kind == "kep":
        return kep_id(start)
    return person_id(start, aliases)


def _parse_steps(spec: str) -> list[Step]:
    """Parse ``"TECH_LEADS>,OWNS>"`` / ``"<OWNS"`` into (EdgeKind, Direction) steps."""
    steps: list[Step] = []
    for token in (t.strip() for t in spec.split(",") if t.strip()):
        if token.endswith(">"):
            direction, name = Direction.OUT, token[:-1]
        elif token.startswith("<"):
            direction, name = Direction.IN, token[1:]
        else:
            raise ValueError(f"step {token!r} must end with '>' (out) or start with '<' (in)")
        steps.append((EdgeKind(name.strip()), direction))
    return steps


def _embedder(args: argparse.Namespace) -> Embedder:
    """Real Titan v2 when ``--bedrock`` (needs creds), else the offline non-semantic embedder."""
    if getattr(args, "bedrock", False):
        return BedrockTitanEmbedder(region=args.region)
    return HashEmbedder()


def _vector_store(args: argparse.Namespace) -> VectorStore:
    """OpenSearch when an endpoint is given (deployed, in-VPC), else a fresh in-memory store."""
    if getattr(args, "opensearch_endpoint", None):
        from .store.opensearch import OpenSearchVectorStore  # lazy: deploy-only path

        return OpenSearchVectorStore(args.opensearch_endpoint, args.region)
    return MemoryVectorStore()


def _synthesizer(args: argparse.Namespace) -> Synthesizer:
    """Real Bedrock Claude when ``--bedrock`` (needs creds), else the offline
    deterministic, non-semantic synthesizer."""
    if getattr(args, "bedrock", False):
        model_id = getattr(args, "synthesis_model_id", None) or DEFAULT_SYNTHESIS_MODEL_ID
        return BedrockClaudeSynthesizer(model_id=model_id, region=args.region)
    return TemplateSynthesizer()


def _clearance(args: argparse.Namespace) -> Clearance | None:
    """Resolve ``--persona`` to a Clearance, or ``None`` (unrestricted) when absent.

    An unknown persona exits non-zero with a clear message (fail-closed — never a silent
    fall-through to unrestricted). The labels are a synthetic stand-in for ACLs, not real
    authz (charter principle 5)."""
    persona = getattr(args, "persona", None)
    if not persona:
        return None
    try:
        return resolve_clearance(persona)
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from exc


def _print_persona(clearance: Clearance | None) -> None:
    """Print the active persona + clearance when filtering is on; nothing when it's off
    (so no-persona output stays byte-identical to the pre-slice-4 trace)."""
    if clearance is not None:
        allowed = ", ".join(sorted(clearance.allowed))
        print(
            f"persona: {clearance.persona}  clearance allows: [{allowed}] "
            "(synthetic visibility labels — a teaching stand-in for ACLs, not real authz)"
        )


def _make_http_client() -> HttpClient:
    """The HTTP client seam for the live Function-URL path (monkeypatched in tests).

    A longer read timeout than a single Neptune hop: the hybrid query runs vector
    search + multi-hop expansion + Bedrock Claude synthesis, and a VPC Lambda cold
    start adds seconds — cover the function's full budget rather than the 30s default."""
    return _UrllibClient(timeout=_FUNCTION_URL_TIMEOUT)


def _function_url_query(
    url: str, question: str, region: str, persona: str | None = None, mode: str = "hybrid"
) -> dict[str, Any]:
    """Thin live client: a SigV4-signed (service=lambda) POST of the question to the
    in-VPC query Lambda's Function URL, the signature **covering the body** (an
    ``X-Amz-Content-SHA256`` payload hash, never ``UNSIGNED-PAYLOAD``, so a tampered
    body is rejected). A non-2xx raises with the body (loud, like the adapters).

    A ``persona`` (slice-4 permission filter) rides the body when set, so the live query is
    permission-filtered server-side by the same clearance the offline path applies.

    ``mode`` selects the server path (``hybrid`` default | ``governed`` | ``text2cypher`` |
    ``selfquery`` | ``parentchild`` | ``global``); it rides the body only when non-default, so a
    hybrid call's wire form is byte-unchanged (the additive, back-compat Function-URL extension
    the catalog slices ship)."""
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.session import Session

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"function URL must be https://, got {url!r}")
    host_match = _FUNCTION_URL_REGION.search(parsed.netloc)
    signing_region = host_match.group(1) if host_match else region

    payload: dict[str, str] = {"question": question}
    if persona:
        payload["persona"] = persona
    if mode != "hybrid":
        payload["mode"] = mode
    body = json.dumps(payload).encode("utf-8")
    payload_hash = hashlib.sha256(body).hexdigest()
    request = AWSRequest(
        method="POST",
        url=url,
        data=body,
        # Make the payload hash a signed header so the signature covers the body.
        headers={"Content-Type": "application/json", "X-Amz-Content-SHA256": payload_hash},
    )
    credentials = Session().get_credentials()
    if credentials is None:
        raise RuntimeError("no AWS credentials resolved from the default provider chain")
    SigV4Auth(credentials, _LAMBDA_SERVICE, signing_region).add_auth(request)

    resp: HttpResponse = _make_http_client().post(
        url, data=body, headers=dict(request.headers), verify=True
    )
    if not 200 <= resp.status < 300:
        raise RuntimeError(f"function URL {resp.status}: {resp.text}")
    parsed_body = json.loads(resp.text)
    return parsed_body if isinstance(parsed_body, dict) else {}


def _index_corpus(
    store: VectorStore, embedder: Embedder, community: Path, enhancements: Path
) -> list[EmbeddedChunk]:
    """Chunk the prose-rich subset, embed it, and index every chunk into ``store``."""
    chunks = chunk_corpus(load_corpus(community, enhancements))
    # Slice-4: label the offline corpus too, so `--persona` can actually filter. Visibility
    # is inert without a persona (the trace/filter are gated on a clearance), so no-persona
    # output is unchanged. Mirrors the Fargate dual-write's labeling.
    label_chunks(chunks, load_labels())
    vectors = embedder.embed([c.text for c in chunks])
    embedded = [EmbeddedChunk(c, v) for c, v in zip(chunks, vectors, strict=True)]
    for ec in embedded:
        store.index_chunk(ec)
    return embedded


def _parentchild_store(args: argparse.Namespace) -> ParentChildStore:
    """OpenSearch nested store when an endpoint is given (deployed, in-VPC), else in-memory."""
    if getattr(args, "opensearch_endpoint", None):
        from .store.parentchild_opensearch import OpenSearchParentChildStore  # lazy: deploy-only

        return OpenSearchParentChildStore(args.opensearch_endpoint, args.region)
    return MemoryParentChildStore()


def _index_parentchild_corpus(
    store: ParentChildStore, embedder: Embedder, community: Path, enhancements: Path
) -> None:
    """Chunk + embed the prose subset, group chunks into parents, and index the nested store —
    the offline twin of the Fargate parent-child dual-write (one embed pass)."""
    docs = load_corpus(community, enhancements)
    chunks = chunk_corpus(docs)
    label_chunks(chunks, load_labels())  # so --persona can filter offline (inert without one)
    vectors = embedder.embed([c.text for c in chunks])
    embedded = [EmbeddedChunk(c, v) for c, v in zip(chunks, vectors, strict=True)]
    bodies = {d.doc_id: d.markdown.body for d in docs if d.markdown is not None}
    for parent in group_into_parents(embedded, bodies):
        store.index_parent(parent)


def _cmd_vector_ingest(args: argparse.Namespace) -> int:
    store = _vector_store(args)
    store.create_index()  # no-op for the in-memory store; creates the k-NN index on OpenSearch
    embedder = _embedder(args)
    embedded = _index_corpus(store, embedder, Path(args.community), Path(args.enhancements))
    by_source = Counter(ec.chunk.source for ec in embedded)
    print("== vector-ingest ==")
    print(f"embedding: {embedder.model_id} (dim={embedder.dimensions})")
    print(
        f"chunks: {len(embedded)}  by source: "
        + ", ".join(f"{k}={v}" for k, v in sorted(by_source.items()))
    )
    return 0


def _cmd_vector_query(args: argparse.Namespace) -> int:
    store = _vector_store(args)
    embedder = _embedder(args)
    if isinstance(store, MemoryVectorStore):  # offline: populate by chunk+embed now
        _index_corpus(store, embedder, Path(args.community), Path(args.enhancements))
    clearance = _clearance(args)
    result = vector_search(store, embedder, args.q, k=args.k, clearance=clearance)
    print("== vector-query ==")
    _print_persona(clearance)
    print(result.render())
    return 0


def _offline_label(embedder: Embedder, synthesizer: Synthesizer) -> str:
    return (
        f"embedder: {embedder.model_id}\n"
        f"synthesizer: {synthesizer.model_id}\n"
        "(offline embedder/synthesizer are NON-SEMANTIC — structural demo only; "
        "semantic quality is the live path / frozen-vector eval)"
    )


def _cmd_hybrid_query(args: argparse.Namespace) -> int:
    if getattr(args, "function_url", None):
        # Live: thin SigV4 Function-URL client to the in-VPC query Lambda. Resolve the
        # persona client-side first (fail-closed on an unknown one, before the network
        # call), and print the same persona banner the offline verbs print so the
        # synthetic-stand-in framing is consistent across ingresses.
        clearance = _clearance(args)
        result = _function_url_query(
            args.function_url, args.q, args.region, getattr(args, "persona", None)
        )
        print("== hybrid-query (live function-url) ==")
        _print_persona(clearance)
        print(f"seeds: {json.dumps(result.get('seeds', []))}")
        print(f"hops: {json.dumps(result.get('hops', []))}")
        print("trace:")
        print(result.get("trace", "(none)"))
        print("citations:")
        for cite in result.get("citations", []) or []:
            print(f"  - {cite}")
        print("answer:")
        print(f"  {result.get('answer', '')}")
        return 0

    # Offline: in-memory stores from the fixture corpus + offline embedder/synthesizer.
    graph = _populated_store(args)
    vstore = _vector_store(args)
    embedder = _embedder(args)
    synthesizer = _synthesizer(args)
    if isinstance(vstore, MemoryVectorStore):
        _index_corpus(vstore, embedder, Path(args.community), Path(args.enhancements))
    result_h = hybrid_query(
        args.q,
        vector_store=vstore,
        graph_store=graph,
        embedder=embedder,
        synthesizer=synthesizer,
        aliases=load_aliases(),
        k=args.k,
        max_hops=args.max_hops,
        clearance=_clearance(args),
    )
    print("== hybrid-query (offline) ==")
    print(_offline_label(embedder, synthesizer))
    print(result_h.render())
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    graph = _populated_store(args)
    vstore = _vector_store(args)
    embedder = _embedder(args)
    synthesizer = _synthesizer(args)
    if isinstance(vstore, MemoryVectorStore):
        _index_corpus(vstore, embedder, Path(args.community), Path(args.enhancements))
    clearance = _clearance(args)
    result = run_modes(
        args.q,
        vector_store=vstore,
        graph_store=graph,
        embedder=embedder,
        synthesizer=synthesizer,
        aliases=load_aliases(),
        k=args.k,
        max_hops=args.max_hops,
        clearance=clearance,
    )
    print("== compare (offline) ==")
    print(_offline_label(embedder, synthesizer))
    _print_persona(clearance)
    print(result.render())
    return 0


def _selector(args: argparse.Namespace) -> TemplateSelector:
    """Real Bedrock Claude selector when ``--bedrock`` (needs creds), else the offline
    deterministic, non-semantic rule selector."""
    if getattr(args, "bedrock", False):
        model_id = getattr(args, "synthesis_model_id", None) or DEFAULT_SYNTHESIS_MODEL_ID
        return BedrockTemplateSelector(model_id=model_id, region=args.region)
    return RuleTemplateSelector()


def _offline_governed_label(selector: TemplateSelector, synthesizer: Synthesizer) -> str:
    return (
        f"selector: {selector.model_id}\n"
        f"synthesizer: {synthesizer.model_id}\n"
        "(offline selector/synthesizer are NON-SEMANTIC — structural demo only; "
        "semantic selection/quality is the live path)"
    )


def _cmd_governed_query(args: argparse.Namespace) -> int:
    """The governed Cypher-Templates path: select a vetted template, bind validated
    parameters, run the parameterized openCypher, and print the full audit trace."""
    if getattr(args, "function_url", None):
        # Live: thin SigV4 Function-URL client, mode=governed.
        result = _function_url_query(args.function_url, args.q, args.region, None, mode="governed")
        print("== governed-query (live function-url) ==")
        print(f"template: {result.get('template_id')}")
        print(f"params: {json.dumps(result.get('params', {}))}")
        print(f"cypher: {result.get('cypher', '')}")
        print("trace:")
        print(result.get("trace", "(none)"))
        print("citations:")
        for cite in result.get("citations", []) or []:
            print(f"  - {cite}")
        print("answer:")
        print(f"  {result.get('answer', '')}")
        return 0

    # Offline: in-memory store from the fixture corpus + rule selector + offline synthesizer.
    graph = _populated_store(args)
    selector = _selector(args)
    synthesizer = _synthesizer(args)
    result_g = governed_query(
        args.q,
        graph_store=graph,
        selector=selector,
        synthesizer=synthesizer,
        aliases=load_aliases(),
    )
    print("== governed-query (offline) ==")
    print(_offline_governed_label(selector, synthesizer))
    print(result_g.render())
    return 0


def _metadata_extractor(args: argparse.Namespace) -> MetadataExtractor:
    """Real Bedrock Claude extractor when ``--bedrock`` (needs creds), else the offline
    deterministic, non-semantic rule extractor."""
    if getattr(args, "bedrock", False):
        model_id = getattr(args, "synthesis_model_id", None) or DEFAULT_SYNTHESIS_MODEL_ID
        return BedrockMetadataExtractor(model_id=model_id, region=args.region)
    return RuleMetadataExtractor()


def _offline_selfquery_label(extractor: MetadataExtractor, synthesizer: Synthesizer) -> str:
    return (
        f"extractor: {extractor.model_id}\n"
        f"synthesizer: {synthesizer.model_id}\n"
        "(offline extractor/synthesizer are NON-SEMANTIC — structural demo only; "
        "semantic extraction/quality is the live path)"
    )


def _cmd_selfquery_query(args: argparse.Namespace) -> int:
    """The self-query path: Bedrock extracts a structured filter (source/entity_ids) from the
    question, the vector search applies it DURING the ANN scan, and the trace is printed."""
    if getattr(args, "function_url", None):
        # Live: thin SigV4 Function-URL client, mode=selfquery (persona rides the body too).
        clearance = _clearance(args)
        result = _function_url_query(
            args.function_url, args.q, args.region, getattr(args, "persona", None), mode="selfquery"
        )
        print("== selfquery-query (live function-url) ==")
        _print_persona(clearance)
        print(f"extracted filter: {json.dumps(result.get('extracted_filter', {}))}")
        if result.get("dropped"):
            print(f"dropped: {json.dumps(result['dropped'])}")
        print("trace:")
        print(result.get("trace", "(none)"))
        print("citations:")
        for cite in result.get("citations", []) or []:
            print(f"  - {cite}")
        print("answer:")
        print(f"  {result.get('answer', '')}")
        return 0

    # Offline: in-memory stores from the fixture corpus + rule extractor + offline synthesizer.
    vstore = _vector_store(args)
    embedder = _embedder(args)
    if isinstance(vstore, MemoryVectorStore):
        _index_corpus(vstore, embedder, Path(args.community), Path(args.enhancements))
    extractor = _metadata_extractor(args)
    synthesizer = _synthesizer(args)
    clearance = _clearance(args)
    graph = _populated_store(args) if args.mode == "hybrid" else None
    result_s = selfquery_query(
        args.q,
        extractor=extractor,
        vector_store=vstore,
        embedder=embedder,
        synthesizer=synthesizer,
        aliases=load_aliases(),
        mode=args.mode,
        graph_store=graph,
        k=args.k,
        clearance=clearance,
    )
    print(f"== selfquery-query (offline, mode={args.mode}) ==")
    print(_offline_selfquery_label(extractor, synthesizer))
    _print_persona(clearance)
    print(result_s.render())
    return 0


def _text2cypher_generator(args: argparse.Namespace) -> Text2CypherGenerator:
    """Real Bedrock Claude generator when ``--bedrock``, else the offline non-semantic rule
    generator (which emits within the offline evaluator's bounded read subset)."""
    if getattr(args, "bedrock", False):
        model_id = getattr(args, "synthesis_model_id", None) or DEFAULT_SYNTHESIS_MODEL_ID
        return BedrockText2CypherGenerator(model_id=model_id, region=args.region)
    return RuleText2CypherGenerator()


def _offline_text2cypher_label(generator: Text2CypherGenerator, synthesizer: Synthesizer) -> str:
    return (
        f"generator: {generator.model_id}\n"
        f"synthesizer: {synthesizer.model_id}\n"
        "(offline generator/synthesizer are NON-SEMANTIC — structural demo only; the offline "
        "evaluator runs a bounded read SUBSET, and live Neptune is the execution-fidelity "
        "oracle — semantic generation/quality is the live path)"
    )


def _cmd_parentchild_query(args: argparse.Namespace) -> int:
    """The Parent-Child Retriever path: a small child chunk's vector is matched (precise), the
    larger parent document body is returned for context-complete synthesis, with the trace."""
    if getattr(args, "function_url", None):
        # Live: thin SigV4 Function-URL client, mode=parentchild (persona rides the body too).
        clearance = _clearance(args)
        result = _function_url_query(
            args.function_url,
            args.q,
            args.region,
            getattr(args, "persona", None),
            mode="parentchild",
        )
        print("== parentchild-query (live function-url) ==")
        _print_persona(clearance)
        print("trace:")
        print(result.get("trace", "(none)"))
        print("citations:")
        for cite in result.get("citations", []) or []:
            print(f"  - {cite}")
        print("answer:")
        print(f"  {result.get('answer', '')}")
        return 0

    # Offline: in-memory nested store from the fixture corpus + offline embedder/synthesizer.
    store = _parentchild_store(args)
    embedder = _embedder(args)
    synthesizer = _synthesizer(args)
    if isinstance(store, MemoryParentChildStore):
        _index_parentchild_corpus(store, embedder, Path(args.community), Path(args.enhancements))
    clearance = _clearance(args)
    result_pc = parentchild_query(
        args.q,
        store=store,
        embedder=embedder,
        synthesizer=synthesizer,
        k=args.k,
        clearance=clearance,
    )
    print("== parentchild-query (offline) ==")
    print(_offline_label(embedder, synthesizer))
    _print_persona(clearance)
    print(result_pc.render())
    return 0


def _offline_community_store(args: argparse.Namespace, synthesizer: Synthesizer) -> Any:
    """Build an in-memory community store offline: ingest the corpus into a graph (labeled,
    so ``--persona`` can filter), detect communities (Louvain), summarize each, and stamp
    ``communityId`` — the offline twin of the Fargate community write-back (ADR-0005)."""
    from .community_detect import detect_communities, summarize_communities  # lazy: networkx
    from .store.community_memory import MemoryCommunityStore

    graph = MemoryGraphStore()
    ingest(Path(args.community), Path(args.enhancements), graph)  # labels nodes (slice-4)
    nodes, edges = graph.all_nodes(), graph.all_edges()
    communities = summarize_communities(detect_communities(nodes, edges), nodes, edges, synthesizer)
    store = MemoryCommunityStore()
    for community in communities:
        store.upsert_community(community)
        for entity_id in community.entity_ids:
            store.set_community_id(entity_id, community.id)
    return store


def _cmd_global_query(args: argparse.Namespace) -> int:
    """The Global Community Summary path: answer a corpus-wide question by map-reducing over
    per-community summaries (MS GraphRAG global), with the clearance-gated trace."""
    if getattr(args, "function_url", None):
        # Live: thin SigV4 Function-URL client, mode=global (persona rides the body).
        clearance = _clearance(args)
        result = _function_url_query(
            args.function_url, args.q, args.region, getattr(args, "persona", None), mode="global"
        )
        print("== global-query (live function-url) ==")
        _print_persona(clearance)
        print("trace:")
        print(result.get("trace", "(none)"))
        print("citations:")
        for cite in result.get("citations", []) or []:
            print(f"  - {cite}")
        print("answer:")
        print(f"  {result.get('answer', '')}")
        return 0

    # Offline: in-memory community store built by detecting + summarizing the fixture corpus.
    synthesizer = _synthesizer(args)
    store = _offline_community_store(args, synthesizer)
    clearance = _clearance(args)
    result_g = global_query(
        args.q,
        community_store=store,
        synthesizer=synthesizer,
        clearance=clearance,
        top_n=args.top_n,
    )
    print("== global-query (offline) ==")
    print(
        f"synthesizer: {synthesizer.model_id}\n"
        "(offline synthesizer is NON-SEMANTIC — structural demo only; semantic quality is the "
        "live path). Communities: Louvain (networkx, seeded) — in-task, not Neptune Analytics."
    )
    _print_persona(clearance)
    print(result_g.render())
    return 0


def _cmd_detect_communities(args: argparse.Namespace) -> int:
    """Print the detected community partition + per-community summaries offline (the ingest-side
    view of the Global Community Summary slice)."""
    synthesizer = _synthesizer(args)
    store = _offline_community_store(args, synthesizer)
    print("== detect-communities (offline) ==")
    print(
        "algorithm: Louvain (networkx, seeded) — computed in-task, NOT a standing Neptune "
        "Analytics service (ADR-0005); algorithm is Louvain, not Leiden (charter honesty note)."
    )
    print(f"synthesizer: {synthesizer.model_id} (NON-SEMANTIC offline)")
    for community in store.all_communities():
        print(f"  {community.id} [{community.tier}] size={community.size} — {community.title}")
        print(f"    members: {', '.join(community.entity_ids)}")
        print(f"    summary: {community.summary}")
    return 0


def _cmd_text2cypher_query(args: argparse.Namespace) -> int:
    """The flexible text2openCypher path: the LLM WRITES the openCypher, executed read-only with
    validation + bounded self-heal, printing the full audit trace (the risky half of the
    governed-vs-risky pair)."""
    if getattr(args, "function_url", None):
        # Live: thin SigV4 Function-URL client, mode=text2cypher.
        result = _function_url_query(
            args.function_url, args.q, args.region, None, mode="text2cypher"
        )
        print("== text2cypher-query (live function-url) ==")
        print(f"executed query: {result.get('executed_query')}")
        if result.get("refusal_reason"):
            print(f"refusal: {result.get('refusal_reason')}")
        print("trace:")
        print(result.get("trace", "(none)"))
        print("citations:")
        for cite in result.get("citations", []) or []:
            print(f"  - {cite}")
        print("answer:")
        print(f"  {result.get('answer', '')}")
        return 0

    # Offline: in-memory store from the fixture corpus + rule generator + offline synthesizer.
    graph = _populated_store(args)
    generator = _text2cypher_generator(args)
    synthesizer = _synthesizer(args)
    result_t = text2cypher_query(
        args.q, graph_store=graph, generator=generator, synthesizer=synthesizer
    )
    print("== text2cypher-query (offline) ==")
    print(_offline_text2cypher_label(generator, synthesizer))
    print(result_t.render())
    return 0


def _triple_extractor(args: argparse.Namespace) -> TripleExtractor:
    """Real Bedrock Claude when ``--bedrock`` (needs creds), else the offline non-semantic
    rule extractor."""
    if getattr(args, "bedrock", False):
        return BedrockTripleExtractor(region=args.region)
    return RuleTripleExtractor()


def _cmd_extract_llm(args: argparse.Namespace) -> int:
    """Schema-guided LLM extraction over the prose bodies — the LLM-assisted end of the
    extraction spectrum (the deterministic end is ``ingest``). Prints the ordered per-triple
    audit trace: schema shown → doc/span → candidate triple → verdict → resulting edge. Offline
    by default (non-semantic ``RuleTripleExtractor`` — pins orchestration + provenance, NOT
    extraction quality); ``--bedrock`` switches to the live ``BedrockTripleExtractor``."""
    docs = load_corpus(Path(args.community), Path(args.enhancements))
    graph = resolve(docs)
    extractor = _triple_extractor(args)
    result = extract_schema_guided(docs, graph, extractor=extractor, aliases=load_aliases())
    print("== extract-llm (bedrock) ==" if args.bedrock else "== extract-llm (offline) ==")
    if not args.bedrock:
        print(
            "extractor is OFFLINE + NON-SEMANTIC — it pins the orchestration + per-triple "
            "provenance contract, never extraction quality (the honest semantic win is --bedrock)."
        )
    print(result.render())
    return 0


def _cmd_vector_eval(args: argparse.Namespace) -> int:
    cases = load_query_set(Path(args.query_set))
    corpus = {
        c.id: c for c in chunk_corpus(load_corpus(Path(args.community), Path(args.enhancements)))
    }
    frozen_path = Path(args.frozen)
    if getattr(args, "refresh_embeddings", False):
        if not args.bedrock:
            raise SystemExit("--refresh-embeddings requires --bedrock (real Titan v2 vectors)")
        frozen = freeze_embeddings(corpus, cases, BedrockTitanEmbedder(region=args.region))
        frozen_path.write_text(json.dumps(frozen, indent=2, sort_keys=True), encoding="utf-8")
        print(
            f"wrote frozen embeddings: {frozen_path} ({len(corpus)} chunks, {len(cases)} queries)"
        )
    result = evaluate_query_set(cases, load_frozen(frozen_path), corpus, k=args.k)
    print(result.render())
    return 0 if result.passes() else 1


def _cmd_ingest(args: argparse.Namespace) -> int:
    report = ingest(Path(args.community), Path(args.enhancements), _target_store(args))
    print(report.render())
    return 0


def _write_manifest_out(args: argparse.Namespace, manifest: dict[str, str]) -> None:
    """Persist the run's new manifest to ``--manifest-out`` when given (next delta's baseline)."""
    out = getattr(args, "manifest_out", None)
    if out:
        Path(out).write_text(manifest_to_json(manifest), encoding="utf-8")
        print(f"wrote manifest: {out} ({len(manifest)} docs)")


def _cmd_delta(args: argparse.Namespace) -> int:
    """Incremental delta re-ingest against a stored manifest (slice 5). With no readable
    ``--prev-manifest`` it falls back to a full ingest (the no-prior-manifest case, AC8b)."""
    prev = None
    prev_path = getattr(args, "prev_manifest", None)
    if prev_path and Path(prev_path).is_file():
        prev = manifest_from_json(Path(prev_path).read_text(encoding="utf-8"))
    report = ingest_delta(
        prev,
        Path(args.community),
        Path(args.enhancements),
        _target_store(args),
        _vector_store(args),
        _embedder(args),
    )
    print(report.render())
    _write_manifest_out(args, report.new_manifest)
    return 0


def _cmd_rebuild(args: argparse.Namespace) -> int:
    """The ``--rebuild`` escape hatch (slice 5): clear both stores, then full-ingest fresh."""
    report = rebuild(
        Path(args.community),
        Path(args.enhancements),
        _target_store(args),
        _vector_store(args),
        _embedder(args),
    )
    print(report.render())
    _write_manifest_out(args, report.new_manifest)
    return 0


def _cmd_delta_demo(args: argparse.Namespace) -> int:
    """Before/after incremental-delta demo over two real corpus snapshots (slice 5; AC10).

    Offline and in-process (in-memory stores, non-semantic embedder) so the base ingest and the
    delta share state: ingest the base snapshot, print the BEFORE counts, then re-ingest only the
    delta into the *same* stores and print the classified add/change/delete/move set, the orphans
    removed, and the AFTER counts — a legible freshness narration, no black-box hop (AC10)."""
    graph = MemoryGraphStore()
    vstore = MemoryVectorStore()
    embedder = HashEmbedder()

    base = ingest_delta(
        None,
        Path(args.base_community),
        Path(args.base_enhancements),
        graph,
        vstore,
        embedder,
    )
    print("== delta demo ==")
    print(
        "(synthetic teaching demo — offline non-semantic embedder; BOTH stores updated from one "
        "pass, kept consistent by stable key = doc path + content hash)"
    )
    print(
        f"BEFORE (base snapshot): nodes={base.after_nodes} edges={base.after_edges} "
        f"chunks={base.after_chunks}"
    )
    report = ingest_delta(
        base.new_manifest,
        Path(args.community),
        Path(args.enhancements),
        graph,
        vstore,
        embedder,
    )
    print(report.render())
    return 0


def _cmd_graph_query(args: argparse.Namespace) -> int:
    aliases = load_aliases()
    store = _populated_store(args)
    seed = _seed_id(args.start, args.start_kind, aliases)
    steps = _parse_steps(args.steps)
    clearance = _clearance(args)
    result = traverse(store, [seed], steps, max_hops=args.max_hops, clearance=clearance)
    print("== graph-query ==")
    _print_persona(clearance)
    print(result.render())
    return 0


def _cmd_resolve_eval(args: argparse.Namespace) -> int:
    mentions = load_labeled_sample(Path(args.sample))
    res = evaluate(mentions, load_aliases())
    print("== resolve-eval (open confirmation) ==")
    print(f"mentions: {res.n_mentions}  tp={res.tp} fp={res.fp} fn={res.fn}")
    print(f"precision: {res.precision:.3f}   recall: {res.recall:.3f}   bar: {args.bar:.2f}")
    ok = res.passes(args.bar)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def _add_persona_arg(p: argparse.ArgumentParser) -> None:
    """Add the slice-4 ``--persona`` filter (a synthetic ACL stand-in, not real authz)."""
    p.add_argument(
        "--persona",
        help="synthetic visibility persona to permission-filter retrieval by — a TEACHING "
        f"stand-in for ACLs, not real authz. One of: {', '.join(sorted(PERSONAS))}. "
        "Omit for unrestricted (slice-1-3 behavior).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="graphrag", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_corpus_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--community", required=True, help="path to the community source root")
        p.add_argument("--enhancements", required=True, help="path to the enhancements source root")
        p.add_argument("--neptune-endpoint", help="https Neptune endpoint (deployed graph store)")
        p.add_argument("--region", default="us-east-1", help="AWS region for SigV4 signing")

    p_ingest = sub.add_parser("ingest", help="parse + resolve + write the graph")
    add_corpus_args(p_ingest)
    p_ingest.set_defaults(func=_cmd_ingest)

    p_query = sub.add_parser("graph-query", help="bounded multi-hop traversal with a trace")
    add_corpus_args(p_query)
    p_query.add_argument("--start", required=True, help="seed: a node id or a handle/slug")
    p_query.add_argument("--start-kind", default="person", choices=["person", "sig", "kep"])
    p_query.add_argument(
        "--steps", required=True, help="edge steps, e.g. 'TECH_LEADS>,OWNS>' ('<KIND' = incoming)"
    )
    p_query.add_argument("--max-hops", type=int, default=2)
    _add_persona_arg(p_query)
    p_query.set_defaults(func=_cmd_graph_query)

    p_eval = sub.add_parser("resolve-eval", help="score the resolver (open confirmation)")
    p_eval.add_argument("--sample", required=True, help="path to the labeled sample YAML")
    p_eval.add_argument("--bar", type=float, default=0.80, help="precision/recall bar")
    p_eval.set_defaults(func=_cmd_resolve_eval)

    def add_vector_corpus_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--community", required=True, help="path to the community source root")
        p.add_argument("--enhancements", required=True, help="path to the enhancements source root")
        p.add_argument(
            "--opensearch-endpoint", help="https OpenSearch endpoint (deployed vector store)"
        )
        p.add_argument("--region", default="us-east-1", help="AWS region for SigV4 signing")
        p.add_argument(
            "--bedrock",
            action="store_true",
            help="use real Titan v2 embeddings (needs AWS creds); "
            "default is the offline non-semantic embedder",
        )

    p_vingest = sub.add_parser(
        "vector-ingest", help="chunk -> embed -> index the prose-rich subset"
    )
    add_vector_corpus_args(p_vingest)
    p_vingest.set_defaults(func=_cmd_vector_ingest)

    p_vquery = sub.add_parser(
        "vector-query", help="semantic search with a retrieval trace + provenance"
    )
    add_vector_corpus_args(p_vquery)
    p_vquery.add_argument("--q", required=True, help="the natural-language query")
    p_vquery.add_argument("--k", type=int, default=5, help="number of chunks to return")
    _add_persona_arg(p_vquery)
    p_vquery.set_defaults(func=_cmd_vector_query)

    p_veval = sub.add_parser(
        "vector-eval", help="credible-baseline confirmation (hit@k over the curated set)"
    )
    p_veval.add_argument(
        "--community", required=True, help="path to the eval-corpus community root"
    )
    p_veval.add_argument(
        "--enhancements", required=True, help="path to the eval-corpus enhancements root"
    )
    p_veval.add_argument("--query-set", required=True, help="path to the curated query-set YAML")
    p_veval.add_argument("--frozen", required=True, help="path to the frozen-embeddings JSON")
    p_veval.add_argument("--k", type=int, default=5, help="hit@k cutoff")
    p_veval.add_argument("--region", default="us-east-1", help="AWS region (for --refresh)")
    p_veval.add_argument("--bedrock", action="store_true", help="use real Titan v2 (for --refresh)")
    p_veval.add_argument(
        "--refresh-embeddings",
        action="store_true",
        help="regenerate the frozen vectors via live Titan v2 (requires --bedrock)",
    )
    p_veval.set_defaults(func=_cmd_vector_eval)

    def add_hybrid_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--community", required=True, help="path to the community source root")
        p.add_argument("--enhancements", required=True, help="path to the enhancements source root")
        p.add_argument("--neptune-endpoint", help="https Neptune endpoint (deployed graph store)")
        p.add_argument(
            "--opensearch-endpoint", help="https OpenSearch endpoint (deployed vector store)"
        )
        p.add_argument("--region", default="us-east-1", help="AWS region for SigV4 signing")
        p.add_argument("--q", required=True, help="the natural-language question")
        p.add_argument("--k", type=int, default=5, help="number of chunks to retrieve")
        p.add_argument("--max-hops", type=int, default=2, help="expansion hop limit (1-2)")
        p.add_argument(
            "--bedrock",
            action="store_true",
            help="use real Titan v2 embeddings + Bedrock Claude synthesis (needs AWS creds); "
            "default is the offline non-semantic embedder/synthesizer",
        )
        p.add_argument(
            "--synthesis-model-id",
            help="override the Bedrock Claude synthesis model id (with --bedrock)",
        )
        _add_persona_arg(p)

    p_hybrid = sub.add_parser(
        "hybrid-query", help="seed-and-expand hybrid retrieval with a dual-seed trace"
    )
    add_hybrid_args(p_hybrid)
    p_hybrid.add_argument(
        "--function-url",
        help="live: SigV4-signed POST to the in-VPC query Lambda's Function URL "
        "(thin client; the VPC-private stores are unreachable from a laptop)",
    )
    p_hybrid.set_defaults(func=_cmd_hybrid_query)

    p_compare = sub.add_parser(
        "compare", help="run vector-only / graph-only / hybrid side by side, each traced"
    )
    add_hybrid_args(p_compare)
    p_compare.set_defaults(func=_cmd_compare)

    p_governed = sub.add_parser(
        "governed-query",
        help="governed Cypher-Templates path: select a vetted parameterized openCypher "
        "template, bind validated params, run it, and print the audit trace",
    )
    p_governed.add_argument("--community", required=True, help="path to the community source root")
    p_governed.add_argument(
        "--enhancements", required=True, help="path to the enhancements source root"
    )
    p_governed.add_argument(
        "--neptune-endpoint", help="https Neptune endpoint (deployed graph store)"
    )
    p_governed.add_argument(
        "--region", default=_DEFAULT_REGION, help="AWS region for SigV4 signing"
    )
    p_governed.add_argument("--q", required=True, help="the natural-language question")
    p_governed.add_argument(
        "--bedrock",
        action="store_true",
        help="use the real Bedrock Claude selector + synthesis (needs AWS creds); "
        "default is the offline non-semantic rule selector/synthesizer",
    )
    p_governed.add_argument(
        "--synthesis-model-id",
        help="override the Bedrock Claude model id for selection + synthesis (with --bedrock)",
    )
    p_governed.add_argument(
        "--function-url",
        help="live: SigV4-signed POST (mode=governed) to the in-VPC query Lambda's Function URL",
    )
    p_governed.set_defaults(func=_cmd_governed_query)

    p_selfquery = sub.add_parser(
        "selfquery-query",
        help="self-query metadata filtering: Bedrock extracts a structured filter "
        "(source/entity_ids) from the question; the vector search applies it DURING the ANN scan",
    )
    add_hybrid_args(
        p_selfquery
    )  # community/enhancements/endpoints/region/q/k/max-hops/bedrock/persona
    p_selfquery.add_argument(
        "--mode",
        choices=("vector", "hybrid"),
        default="vector",
        help="apply the self-query filter to vector retrieval (default) or hybrid's vector leg",
    )
    p_selfquery.add_argument(
        "--function-url",
        help="live: SigV4-signed POST (mode=selfquery) to the in-VPC query Lambda's Function URL",
    )
    p_selfquery.set_defaults(func=_cmd_selfquery_query)

    p_parentchild = sub.add_parser(
        "parentchild-query",
        help="Parent-Child Retriever: a small child chunk's vector is matched (precise), the "
        "larger parent document body is returned for context-complete synthesis",
    )
    # community/enhancements/opensearch-endpoint/region/bedrock
    add_vector_corpus_args(p_parentchild)
    p_parentchild.add_argument("--q", required=True, help="the natural-language question")
    p_parentchild.add_argument("--k", type=int, default=5, help="number of parents to return")
    p_parentchild.add_argument(
        "--synthesis-model-id",
        help="override the Bedrock Claude synthesis model id (with --bedrock)",
    )
    _add_persona_arg(p_parentchild)
    p_parentchild.add_argument(
        "--function-url",
        help="live: SigV4-signed POST (mode=parentchild) to the in-VPC query Lambda's Function URL",
    )
    p_parentchild.set_defaults(func=_cmd_parentchild_query)

    p_global = sub.add_parser(
        "global-query",
        help="Global Community Summary (MS GraphRAG global): answer a corpus-wide question by "
        "map-reducing over per-community summaries, with a clearance-gated trace",
    )
    add_hybrid_args(p_global)  # community/enhancements/endpoints/region/q/k/bedrock/persona
    p_global.add_argument(
        "--top-n",
        type=int,
        default=16,
        help="max communities to map over (largest first); bounds the map fan-out",
    )
    p_global.add_argument(
        "--function-url",
        help="live: SigV4-signed POST (mode=global) to the in-VPC query Lambda's Function URL",
    )
    p_global.set_defaults(func=_cmd_global_query)

    p_detect = sub.add_parser(
        "detect-communities",
        help="print the detected community partition + per-community summaries (offline; "
        "Louvain in-task, not Neptune Analytics — ADR-0005)",
    )
    add_vector_corpus_args(p_detect)  # community/enhancements/opensearch-endpoint/region/bedrock
    p_detect.add_argument(
        "--synthesis-model-id",
        help="override the Bedrock Claude synthesis model id (with --bedrock)",
    )
    p_detect.set_defaults(func=_cmd_detect_communities)

    p_text2cypher = sub.add_parser(
        "text2cypher-query",
        help="flexible text2openCypher path: the LLM WRITES the openCypher, executed read-only "
        "with validation + bounded self-heal, printing the audit trace (the risky half)",
    )
    p_text2cypher.add_argument(
        "--community", required=True, help="path to the community source root"
    )
    p_text2cypher.add_argument(
        "--enhancements", required=True, help="path to the enhancements source root"
    )
    p_text2cypher.add_argument(
        "--neptune-endpoint", help="https Neptune endpoint (deployed graph store)"
    )
    p_text2cypher.add_argument(
        "--region", default=_DEFAULT_REGION, help="AWS region for SigV4 signing"
    )
    p_text2cypher.add_argument("--q", required=True, help="the natural-language question")
    p_text2cypher.add_argument(
        "--bedrock",
        action="store_true",
        help="use the real Bedrock Claude generator + synthesis (needs AWS creds); "
        "default is the offline non-semantic rule generator/synthesizer",
    )
    p_text2cypher.add_argument(
        "--synthesis-model-id",
        help="override the Bedrock Claude model id for generation + synthesis (with --bedrock)",
    )
    p_text2cypher.add_argument(
        "--function-url",
        help="live: SigV4-signed POST (mode=text2cypher) to the in-VPC query Lambda's Function URL",
    )
    p_text2cypher.set_defaults(func=_cmd_text2cypher_query)

    p_extract_llm = sub.add_parser(
        "extract-llm",
        help="schema-guided LLM extraction over the prose bodies (the LLM-assisted end of the "
        "extraction spectrum); prints the per-triple audit trace (schema -> doc/span -> triple -> "
        "verdict -> edge). Offline non-semantic by default; --bedrock for the live semantic path",
    )
    p_extract_llm.add_argument(
        "--community", required=True, help="path to the community source root"
    )
    p_extract_llm.add_argument(
        "--enhancements", required=True, help="path to the enhancements source root"
    )
    p_extract_llm.add_argument(
        "--region",
        default=_DEFAULT_REGION,
        help="AWS region for the Bedrock client (with --bedrock)",
    )
    p_extract_llm.add_argument(
        "--bedrock",
        action="store_true",
        help="use the real Bedrock Claude extractor (needs AWS creds); default is the offline "
        "non-semantic rule extractor (pins orchestration + provenance, not extraction quality)",
    )
    p_extract_llm.set_defaults(func=_cmd_extract_llm)

    def add_delta_args(p: argparse.ArgumentParser) -> None:
        add_vector_corpus_args(p)  # community/enhancements/opensearch-endpoint/region/bedrock
        p.add_argument("--neptune-endpoint", help="https Neptune endpoint (deployed graph store)")
        p.add_argument(
            "--manifest-out", help="write the run's new manifest (doc id -> hash) to this path"
        )

    p_delta = sub.add_parser(
        "delta", help="incremental delta re-ingest against a stored manifest (both stores)"
    )
    add_delta_args(p_delta)
    p_delta.add_argument(
        "--prev-manifest",
        help="path to the previously-ingested manifest JSON; omit/absent => full ingest fallback",
    )
    p_delta.set_defaults(func=_cmd_delta)

    p_rebuild = sub.add_parser(
        "rebuild", help="escape hatch: clear both stores, then full-ingest from scratch"
    )
    add_delta_args(p_rebuild)
    p_rebuild.set_defaults(func=_cmd_rebuild)

    p_demo = sub.add_parser(
        "delta-demo",
        help="before/after incremental-delta demo over two corpus snapshots (offline, in-memory)",
    )
    p_demo.add_argument("--base-community", required=True, help="base snapshot community root")
    p_demo.add_argument(
        "--base-enhancements", required=True, help="base snapshot enhancements root"
    )
    p_demo.add_argument("--community", required=True, help="new snapshot community root")
    p_demo.add_argument("--enhancements", required=True, help="new snapshot enhancements root")
    p_demo.set_defaults(func=_cmd_delta_demo)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
