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
from .embed import BedrockTitanEmbedder, Embedder, HashEmbedder
from .eval import evaluate, load_labeled_sample
from .hybrid import hybrid_query
from .ingest import ingest
from .model import Direction, EdgeKind
from .normalize import kep_id, person_id, sig_id
from .query import Step, traverse
from .resolve import load_aliases
from .sources import load_corpus
from .store.base import GraphStore
from .store.memory import MemoryGraphStore
from .store.neptune import HttpClient, HttpResponse, _UrllibClient
from .store.vector_base import EmbeddedChunk, VectorStore
from .store.vector_memory import MemoryVectorStore
from .synthesize import (
    DEFAULT_SYNTHESIS_MODEL_ID,
    BedrockClaudeSynthesizer,
    Synthesizer,
    TemplateSynthesizer,
)
from .vector import vector_search
from .vector_eval import (
    evaluate_query_set,
    freeze_embeddings,
    load_frozen,
    load_query_set,
)

# Default region for SigV4 signing when a Function URL doesn't encode one.
_DEFAULT_REGION = "us-east-1"
_LAMBDA_SERVICE = "lambda"
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


def _make_http_client() -> HttpClient:
    """The HTTP client seam for the live Function-URL path (monkeypatched in tests)."""
    return _UrllibClient()


def _function_url_query(url: str, question: str, region: str) -> dict[str, Any]:
    """Thin live client: a SigV4-signed (service=lambda) POST of the question to the
    in-VPC query Lambda's Function URL, the signature **covering the body** (an
    ``X-Amz-Content-SHA256`` payload hash, never ``UNSIGNED-PAYLOAD``, so a tampered
    body is rejected). A non-2xx raises with the body (loud, like the adapters)."""
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.session import Session

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"function URL must be https://, got {url!r}")
    host_match = _FUNCTION_URL_REGION.search(parsed.netloc)
    signing_region = host_match.group(1) if host_match else region

    body = json.dumps({"question": question}).encode("utf-8")
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
    vectors = embedder.embed([c.text for c in chunks])
    embedded = [EmbeddedChunk(c, v) for c, v in zip(chunks, vectors, strict=True)]
    for ec in embedded:
        store.index_chunk(ec)
    return embedded


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
    result = vector_search(store, embedder, args.q, k=args.k)
    print("== vector-query ==")
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
        # Live: thin SigV4 Function-URL client to the in-VPC query Lambda.
        result = _function_url_query(args.function_url, args.q, args.region)
        print("== hybrid-query (live function-url) ==")
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
    result = run_modes(
        args.q,
        vector_store=vstore,
        graph_store=graph,
        embedder=embedder,
        synthesizer=synthesizer,
        aliases=load_aliases(),
        k=args.k,
        max_hops=args.max_hops,
    )
    print("== compare (offline) ==")
    print(_offline_label(embedder, synthesizer))
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


def _cmd_graph_query(args: argparse.Namespace) -> int:
    aliases = load_aliases()
    store = _populated_store(args)
    seed = _seed_id(args.start, args.start_kind, aliases)
    steps = _parse_steps(args.steps)
    result = traverse(store, [seed], steps, max_hops=args.max_hops)
    print("== graph-query ==")
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

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
