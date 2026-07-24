"""Bundled-file resource loaders for graphrag.ontology."""

from __future__ import annotations

import importlib.resources
from functools import lru_cache

import rdflib
from rdflib import Namespace

# Canonical biz: namespace IRI — must match the @prefix in biz_ops.ttl and biz_ops_shapes.ttl.
BIZ = Namespace("https://graphrag-aws.demo/biz-ops/ontology#")


def _load_ttl(filename: str) -> rdflib.Graph:
    pkg = importlib.resources.files("graphrag.ontology")
    resource = pkg.joinpath(filename)
    try:
        with importlib.resources.as_file(resource) as path:
            g = rdflib.Graph()
            g.parse(str(path), format="turtle")
            return g
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Bundled ontology file '{filename}' not found in graphrag.ontology package"
        ) from exc


def load_ontology() -> rdflib.Graph:
    """Return a parsed rdflib.Graph of the bundled OWL ontology (biz_ops.ttl)."""
    return _load_ttl("biz_ops.ttl")


@lru_cache(maxsize=1)
def _load_shapes() -> rdflib.Graph:
    """Return the bundled SHACL shapes graph (biz_ops_shapes.ttl), memoized per process."""
    return _load_ttl("biz_ops_shapes.ttl")


def load_shapes() -> rdflib.Graph:
    """Return a parsed rdflib.Graph of the bundled SHACL shapes (biz_ops_shapes.ttl)."""
    return _load_shapes()
