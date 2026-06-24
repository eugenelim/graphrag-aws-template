"""``graphrag`` CLI — ingest, multi-hop graph-query (with trace), and resolve-eval.

Every verb prints a human-readable trace (charter principle 1 / AC10): ingest
prints the resolution report, graph-query prints the seed→hop→result trace, and
resolve-eval prints the precision/recall of the open confirmation.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .eval import evaluate, load_labeled_sample
from .ingest import ingest
from .model import Direction, EdgeKind
from .normalize import kep_id, person_id, sig_id
from .query import Step, traverse
from .resolve import load_aliases
from .store.base import GraphStore
from .store.memory import MemoryGraphStore


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

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
