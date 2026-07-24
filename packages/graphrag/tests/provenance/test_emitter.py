"""Tests for ProvenanceEmitter — PROV-O graph emission.

Covers spec-provenance-citations AC1 (bronze entity), AC2 (full chain),
AC3 (chunk provenance), and the IRI-safety construction test from plan T1.
"""

from __future__ import annotations

import urllib.parse
from datetime import UTC, datetime

from rdflib import Graph, URIRef
from rdflib.namespace import PROV, RDF

from graphrag.provenance import ProvenanceEmitter

BIZ = "https://graphrag-aws.demo/biz-ops/ontology#"

_DOC_URI = "urn:doc:my-repo:policies/aup.md"
_SHA = "abc1234def5678abc1234def5678abc1234def56"  # pragma: allowlist secret
_GIT_PATH = "policies/aup.md"
_GIT_REPO = "my-org/my-repo"
_EXTRACTOR = "pandoc"
_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_T1 = datetime(2024, 1, 1, 12, 1, 0, tzinfo=UTC)


def _emit() -> Graph:
    return ProvenanceEmitter().emit_provenance(
        _DOC_URI, _SHA, _GIT_PATH, _GIT_REPO, _EXTRACTOR, _T0, _T1
    )


# ── AC1: Bronze entity + SHACL-required fields ───────────────────────────────


def test_emit_bronze_entity_uri_pattern() -> None:
    """Bronze entity URI follows urn:entity:bronze:{repo}:{path}:{sha}."""
    g = _emit()
    enc_repo = urllib.parse.quote(_GIT_REPO, safe="/:@.-_~")
    enc_path = urllib.parse.quote(_GIT_PATH, safe="/:@.-_~")
    bronze = URIRef(f"urn:entity:bronze:{enc_repo}:{enc_path}:{_SHA}")
    assert (bronze, RDF.type, PROV.Entity) in g


def test_emit_bronze_entity_git_fields() -> None:
    """Bronze entity carries biz:gitCommitSHA, biz:gitPath, biz:gitRepo."""
    g = _emit()
    sparql = f"""
    SELECT ?sha ?path ?repo WHERE {{
      ?e a <{PROV}Entity> .
      FILTER(STRSTARTS(STR(?e), "urn:entity:bronze:"))
      ?e <{BIZ}gitCommitSHA> ?sha .
      ?e <{BIZ}gitPath> ?path .
      ?e <{BIZ}gitRepo> ?repo .
    }}
    """
    rows = list(g.query(sparql))
    assert len(rows) == 1
    row = rows[0]
    assert str(row[0]) == _SHA
    assert str(row[1]) == _GIT_PATH
    assert str(row[2]) == _GIT_REPO


# ── AC2: Full PROV-O chain (5 subject types) ─────────────────────────────────


def test_emit_full_prov_chain_types() -> None:
    """SPARQL confirms all 5 PROV-O entities/activities are present."""
    g = _emit()
    # Bronze entity
    bronze_q = (
        f"SELECT ?e WHERE {{ ?e a <{PROV}Entity>"
        " . FILTER(STRSTARTS(STR(?e), 'urn:entity:bronze:')) }"
    )
    assert list(g.query(bronze_q)), "missing Bronze entity"
    # Silver entity
    silver_q = (
        f"SELECT ?e WHERE {{ ?e a <{PROV}Entity>"
        " . FILTER(STRSTARTS(STR(?e), 'urn:entity:silver:')) }"
    )
    assert list(g.query(silver_q)), "missing Silver entity"
    # Extract activity
    extract_q = (
        f"SELECT ?a WHERE {{ ?a a <{PROV}Activity>"
        " . FILTER(STRSTARTS(STR(?a), 'urn:activity:extract:')) }"
    )
    assert list(g.query(extract_q)), "missing extraction activity"
    # Emit activity
    emit_q = (
        f"SELECT ?a WHERE {{ ?a a <{PROV}Activity>"
        " . FILTER(STRSTARTS(STR(?a), 'urn:activity:emit:')) }"
    )
    assert list(g.query(emit_q)), "missing emit activity"
    # Document entity (Gold)
    doc_q = f"SELECT ?d WHERE {{ <{_DOC_URI}> <{PROV}wasGeneratedBy> ?d }}"
    assert list(g.query(doc_q)), "missing Gold document entity link"


def test_emit_extract_activity_links() -> None:
    """Extraction activity links bronze, silver, and agent correctly."""
    g = _emit()
    sparql = f"""
    SELECT ?act ?agent WHERE {{
      ?act a <{PROV}Activity> .
      FILTER(STRSTARTS(STR(?act), "urn:activity:extract:"))
      ?act <{PROV}wasAssociatedWith> ?agent .
    }}
    """
    rows = list(g.query(sparql))
    assert len(rows) == 1
    agent_str = str(rows[0][1])
    assert agent_str == f"urn:agent:{_EXTRACTOR}"


def test_emit_timestamps_present() -> None:
    """Extraction activity carries prov:startedAtTime and prov:endedAtTime."""
    g = _emit()
    sparql = f"""
    SELECT ?start ?end WHERE {{
      ?act a <{PROV}Activity> .
      FILTER(STRSTARTS(STR(?act), "urn:activity:extract:"))
      ?act <{PROV}startedAtTime> ?start .
      ?act <{PROV}endedAtTime> ?end .
    }}
    """
    rows = list(g.query(sparql))
    assert len(rows) == 1


def test_emit_silver_derived_from_bronze() -> None:
    """Silver entity carries prov:wasDerivedFrom pointing to bronze entity."""
    g = _emit()
    sparql = f"""
    SELECT ?silver ?bronze WHERE {{
      ?silver a <{PROV}Entity> .
      FILTER(STRSTARTS(STR(?silver), "urn:entity:silver:"))
      ?silver <{PROV}wasDerivedFrom> ?bronze .
      FILTER(STRSTARTS(STR(?bronze), "urn:entity:bronze:"))
    }}
    """
    rows = list(g.query(sparql))
    assert len(rows) == 1


# ── AC3: Chunk provenance ─────────────────────────────────────────────────────


def test_emit_chunk_provenance_derived_from_and_index() -> None:
    """emit_chunk_provenance returns graph with wasDerivedFrom and chunkIndex."""
    emitter = ProvenanceEmitter()
    g = emitter.emit_chunk_provenance("urn:chunk:1", _DOC_URI, chunk_index=0)
    sparql = f"""
    SELECT ?parent ?idx WHERE {{
      <urn:chunk:1> <{PROV}wasDerivedFrom> ?parent .
      <urn:chunk:1> <{BIZ}chunkIndex> ?idx .
    }}
    """
    rows = list(g.query(sparql))
    assert len(rows) == 1
    assert str(rows[0][0]) == _DOC_URI
    assert int(rows[0][1]) == 0


def test_emit_chunk_provenance_three_chunks() -> None:
    """Fixture with 3 chunks: all have correct wasDerivedFrom and chunkIndex."""
    emitter = ProvenanceEmitter()
    for i in range(3):
        g = emitter.emit_chunk_provenance(f"urn:chunk:{i}", _DOC_URI, chunk_index=i)
        sparql = f"""
        SELECT ?parent ?idx WHERE {{
          <urn:chunk:{i}> <{PROV}wasDerivedFrom> ?parent .
          <urn:chunk:{i}> <{BIZ}chunkIndex> ?idx .
        }}
        """
        rows = list(g.query(sparql))
        assert len(rows) == 1, f"chunk {i} missing provenance"
        assert str(rows[0][0]) == _DOC_URI
        assert int(rows[0][1]) == i


# ── IRI safety ────────────────────────────────────────────────────────────────


def test_emit_iri_safety_special_chars_in_path() -> None:
    """Paths with spaces and # are percent-encoded; Turtle round-trips cleanly."""
    g = ProvenanceEmitter().emit_provenance(
        "urn:doc:my-repo:docs/dir%20name/file%231.md",
        _SHA,
        git_path="docs/dir name/file #1.md",
        git_repo="my-org/my-repo",
        extractor="passthrough",
        started_at=_T0,
        ended_at=_T1,
    )
    # The graph must parse without error and contain the bronze entity
    ttl = g.serialize(format="turtle")
    g2 = Graph()
    g2.parse(data=ttl, format="turtle")
    bronze_q = (
        f"SELECT ?e WHERE {{ ?e a <{PROV}Entity>"
        " . FILTER(STRSTARTS(STR(?e), 'urn:entity:bronze:')) }"
    )
    rows = list(g2.query(bronze_q))
    assert rows, "bronze entity missing after round-trip"
    # Encoded chars present in the URN
    bronze_uri = str(rows[0][0])
    assert "%20" in bronze_uri or " " not in bronze_uri, "space not encoded"
    assert "%23" in bronze_uri or "#" not in bronze_uri, "# not encoded"


# ── AC6 (goal-based): Import isolation ───────────────────────────────────────


def test_import_provenance_no_aws_deps() -> None:
    """import graphrag.provenance exits 0 without boto3/botocore."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    # Inject PYTHONPATH so the subprocess finds this worktree's src even when the
    # pip editable install points at a different worktree (multi-worktree CI setup).
    src_dir = str(Path(__file__).parents[2] / "src")
    env = {**os.environ, "PYTHONPATH": src_dir}
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import graphrag.provenance; "
            "bad = {'boto3', 'botocore'} & set(sys.modules); "
            "assert not bad, f'AWS SDK leaked: {bad}'",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
