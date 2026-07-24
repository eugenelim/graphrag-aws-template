"""Tests for graphrag.ontology_loader — OntologyLoader.

Covers spec rdf-owl-ontology-loader AC1 (ontology triples), AC2 (PROV-O provenance),
AC3 (custom named graph), and AC4 (import isolation).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from graphrag.ontology_loader import OntologyLoader
from graphrag.store.neptune_sparql_memory import MemorySparqlStore

_OWL_CLASS = "http://www.w3.org/2002/07/owl#Class"
_PROV_ACTIVITY = "http://www.w3.org/ns/prov#Activity"
_PROV_AGENT = "http://www.w3.org/ns/prov#SoftwareAgent"
_DEFAULT_GRAPH = "urn:graph:ontology"
_LOADER_AGENT = "urn:agent:ontology-loader"


# ── AC1: Ontology triples loaded ─────────────────────────────────────────────


def test_loader_loads_nine_owl_classes() -> None:
    """OntologyLoader.load() inserts exactly 9 owl:Class triples into the store."""
    store = MemorySparqlStore()
    OntologyLoader(store).load()
    rows = store.sparql_select(
        f"SELECT ?c WHERE {{ GRAPH <{_DEFAULT_GRAPH}> {{ ?c a <{_OWL_CLASS}> }} }}"
    )
    assert len(rows) == 9, f"expected 9 OWL classes, got {len(rows)}"


def test_loader_biz_policy_class_present() -> None:
    """biz:Policy owl:Class is in the loaded graph."""
    store = MemorySparqlStore()
    OntologyLoader(store).load()
    biz_prefix = "https://graphrag-aws.demo/biz-ops/ontology#"
    query = (
        "SELECT ?c WHERE { "
        f"GRAPH <{_DEFAULT_GRAPH}> {{ "
        f"?c a <{_OWL_CLASS}> . "
        f'FILTER(STRSTARTS(STR(?c), "{biz_prefix}")) '
        "} }"
    )
    rows = store.sparql_select(query)
    class_uris = {r["c"] for r in rows}
    assert f"{biz_prefix}Policy" in class_uris


# ── AC2: PROV-O provenance emitted ───────────────────────────────────────────


def test_loader_emits_prov_activity() -> None:
    """A prov:Activity with 'load-ontology' in its URI is present after load()."""
    store = MemorySparqlStore()
    OntologyLoader(store).load()
    rows = store.sparql_select(
        f"SELECT ?act WHERE {{ GRAPH <{_DEFAULT_GRAPH}> {{ ?act a <{_PROV_ACTIVITY}> }} }}"
    )
    assert len(rows) >= 1, "no prov:Activity found"
    act_uris = [r["act"] for r in rows]
    assert any("load-ontology" in uri for uri in act_uris), (
        f"no activity URI contains 'load-ontology': {act_uris}"
    )


def test_loader_emits_prov_agent() -> None:
    """urn:agent:ontology-loader prov:SoftwareAgent is present after load()."""
    store = MemorySparqlStore()
    OntologyLoader(store).load()
    rows = store.sparql_select(
        f"SELECT ?a WHERE {{ GRAPH <{_DEFAULT_GRAPH}> {{ "
        f"<{_LOADER_AGENT}> a <{_PROV_AGENT}> . BIND(<{_LOADER_AGENT}> AS ?a) }} }}"
    )
    assert len(rows) == 1, "loader agent not found in graph"


# ── AC3: Custom named graph ───────────────────────────────────────────────────


def test_loader_custom_named_graph_puts_triples_there() -> None:
    """load(named_graph=X) puts triples in X, not urn:graph:ontology."""
    store = MemorySparqlStore()
    custom = "urn:graph:custom"
    OntologyLoader(store).load(named_graph=custom)
    # Custom graph has classes
    rows_custom = store.sparql_select(
        f"SELECT ?c WHERE {{ GRAPH <{custom}> {{ ?c a <{_OWL_CLASS}> }} }}"
    )
    assert len(rows_custom) == 9
    # Default graph is empty
    rows_default = store.sparql_select(
        f"SELECT ?c WHERE {{ GRAPH <{_DEFAULT_GRAPH}> {{ ?c a <{_OWL_CLASS}> }} }}"
    )
    assert len(rows_default) == 0


# ── AC4: Import isolation ─────────────────────────────────────────────────────


def test_import_ontology_loader_no_aws_deps() -> None:
    """import graphrag.ontology_loader exits 0 without boto3/botocore."""
    # Inject PYTHONPATH so the subprocess finds this worktree's src even when the
    # pip editable install points at a different worktree (multi-worktree CI setup).
    src_dir = str(Path(__file__).parents[1] / "src")
    env = {**os.environ, "PYTHONPATH": src_dir}
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import graphrag.ontology_loader; "
            "bad = {'boto3', 'botocore'} & set(sys.modules); "
            "assert not bad, f'AWS SDK leaked: {bad}'",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


# ── AC5: PROV-O emission failure must not propagate (spec Boundaries §Never do) ──


def test_loader_prov_failure_does_not_raise() -> None:
    """If PROV-O provenance emission fails, load() must not raise.

    Per spec Boundaries "Never do": the ontology triples are the primary load;
    a failure in the provenance emit step is non-fatal.
    """
    # FailingOnSecondCall simulates a store that accepts the ontology load but
    # raises on the provenance load (second load_turtle call).
    _calls: list[int] = []

    class FailingSecondStore(MemorySparqlStore):
        def load_turtle(self, ttl: str, named_graph: str) -> None:
            _calls.append(1)
            if len(_calls) == 2:
                raise RuntimeError("injected provenance-write failure")
            super().load_turtle(ttl, named_graph)

    store = FailingSecondStore()
    import logging

    logger = logging.getLogger("graphrag.ontology_loader._loader")
    log_records: list[logging.LogRecord] = []

    class CapHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            log_records.append(record)

    handler = CapHandler()
    logger.addHandler(handler)
    try:
        OntologyLoader(store).load()  # must not raise
    finally:
        logger.removeHandler(handler)

    # Primary outcome: ontology triples must still be present
    rows = store.sparql_select(
        f"SELECT ?c WHERE {{ GRAPH <{_DEFAULT_GRAPH}> {{ ?c a <{_OWL_CLASS}> }} }}"
    )
    assert len(rows) == 9, "ontology triples must survive a provenance-write failure"
    # Observability: a warning must have been logged (not swallowed silently)
    assert any("non-fatal" in r.getMessage() for r in log_records), (
        "expected a non-fatal warning but got: " + str([r.getMessage() for r in log_records])
    )
