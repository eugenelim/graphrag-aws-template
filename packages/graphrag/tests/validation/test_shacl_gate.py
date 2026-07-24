"""TDD tests for graphrag.validation.shacl.ShaclGate."""

from __future__ import annotations

import hashlib
import logging
from unittest.mock import MagicMock, patch

import pytest
from rdflib import Graph, Literal, Namespace
from rdflib.namespace import RDF, XSD

from graphrag.ontology import ShapeViolation, ValidationResult
from graphrag.validation.shacl import GateResult, ShaclGate

BIZ = Namespace("https://graphrag-aws.demo/biz-ops/ontology#")
SCHEMA = Namespace("https://schema.org/")

DOC_URI = "urn:doc:my-repo:policies/x.md"
SHA = "abc123"
QUARANTINE_GRAPH = "urn:graph:quarantine"

# Injection payload: a SPARQL-structural sequence masquerading as an sh:resultMessage.
INJECT_PAYLOAD = '" . } GRAPH <urn:graph:normative> { <urn:evil> a biz:Policy } #'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _policy_graph(*, omit: set[str] | None = None) -> Graph:
    """Build a minimal well-formed biz:Policy graph.

    Pass omit={"effectiveDate", "scope"} to create SHACL violations.
    """
    omit = omit or set()
    g = Graph()
    doc = BIZ.doc1
    g.add((doc, RDF.type, BIZ.Policy))
    props: dict[str, tuple] = {
        "name": (SCHEMA.name, Literal("AUP", datatype=XSD.string)),
        "effectiveDate": (BIZ.effectiveDate, Literal("2024-01-01", datatype=XSD.date)),
        "scope": (BIZ.scope, Literal("All employees", datatype=XSD.string)),
        "hasPII": (BIZ.hasPII, Literal("true", datatype=XSD.boolean)),
        "gitCommitSHA": (BIZ.gitCommitSHA, Literal("sha-x", datatype=XSD.string)),
    }
    for key, (pred, obj) in props.items():
        if key not in omit:
            g.add((doc, pred, obj))
    return g


def _parse_quarantine_insert(sparql: str) -> Graph:
    """Extract the N-Triples body from INSERT DATA ... GRAPH <quarantine> ... and parse it."""
    prefix = "INSERT DATA { GRAPH <" + QUARANTINE_GRAPH + "> { "
    suffix = " } }"
    assert sparql.startswith(prefix), (
        "Expected SPARQL to start with quarantine INSERT prefix; got: " + repr(sparql[:160])
    )
    assert sparql.endswith(suffix), "Expected SPARQL to end with ' } }'; got tail: " + repr(
        sparql[-40:]
    )
    body = sparql[len(prefix) : -len(suffix)]
    g = Graph()
    g.parse(data=body, format="nt")
    return g


# ---------------------------------------------------------------------------
# Empty graph: no subjects → validate_graph returns conforms=True → passed
# ---------------------------------------------------------------------------


def test_empty_graph_passes_with_no_sparql_call() -> None:
    """An empty rdflib.Graph() has no SHACL-targeted subjects -> conforms=True -> "passed"."""
    mock_client = MagicMock()
    gate = ShaclGate(mock_client)

    result = gate.validate(Graph(), doc_uri=DOC_URI, sha=SHA)

    assert result.outcome == "passed"
    mock_client.sparql_update.assert_not_called()


# ---------------------------------------------------------------------------
# AC1 — passed case: valid graph, no SPARQL call
# ---------------------------------------------------------------------------


def test_passed_case_returns_gate_result() -> None:
    """Well-formed biz:Policy -> GateResult(outcome="passed")."""
    mock_client = MagicMock()
    gate = ShaclGate(mock_client)

    result = gate.validate(_policy_graph(), doc_uri=DOC_URI, sha=SHA)

    assert result == GateResult(outcome="passed")


def test_passed_case_no_sparql_update_call() -> None:
    """Well-formed biz:Policy -> mock client receives no sparql_update call."""
    mock_client = MagicMock()
    gate = ShaclGate(mock_client)

    gate.validate(_policy_graph(), doc_uri=DOC_URI, sha=SHA)

    mock_client.sparql_update.assert_not_called()


# ---------------------------------------------------------------------------
# AC2 — single violation: quarantine INSERT is issued with correct structure
# ---------------------------------------------------------------------------


def test_single_violation_returns_quarantined() -> None:
    """biz:Policy missing effectiveDate -> outcome="quarantined"."""
    mock_client = MagicMock()
    gate = ShaclGate(mock_client)

    result = gate.validate(_policy_graph(omit={"effectiveDate"}), doc_uri=DOC_URI, sha=SHA)

    assert result.outcome == "quarantined"


def test_single_violation_calls_sparql_update_once() -> None:
    """Single violation -> sparql_update called exactly once."""
    mock_client = MagicMock()
    gate = ShaclGate(mock_client)

    gate.validate(_policy_graph(omit={"effectiveDate"}), doc_uri=DOC_URI, sha=SHA)

    mock_client.sparql_update.assert_called_once()


def test_single_violation_insert_targets_quarantine_graph() -> None:
    """Quarantine INSERT DATA targets urn:graph:quarantine."""
    mock_client = MagicMock()
    gate = ShaclGate(mock_client)
    gate.validate(_policy_graph(omit={"effectiveDate"}), doc_uri=DOC_URI, sha=SHA)

    sparql = mock_client.sparql_update.call_args[0][0]
    assert "INSERT DATA" in sparql
    assert "GRAPH <" + QUARANTINE_GRAPH + ">" in sparql


def test_single_violation_record_has_required_triples() -> None:
    """Quarantine record: type, documentURI, quarantineReason, quarantinedAt, violationPath."""
    mock_client = MagicMock()
    gate = ShaclGate(mock_client)
    gate.validate(_policy_graph(omit={"effectiveDate"}), doc_uri=DOC_URI, sha=SHA)

    g = _parse_quarantine_insert(mock_client.sparql_update.call_args[0][0])

    records = list(g.subjects(RDF.type, BIZ.QuarantineRecord))
    assert len(records) == 1, f"Expected one QuarantineRecord subject, found {len(records)}"
    record = records[0]

    # documentURI
    doc_uris = list(g.objects(record, BIZ.documentURI))
    assert len(doc_uris) == 1
    assert str(doc_uris[0]) == DOC_URI

    # quarantineReason — string literal; references the failing property
    reasons = list(g.objects(record, BIZ.quarantineReason))
    assert len(reasons) == 1
    assert "effectiveDate" in str(reasons[0])

    # quarantinedAt — xsd:dateTime literal
    at_times = list(g.objects(record, BIZ.quarantinedAt))
    assert len(at_times) == 1
    assert at_times[0].datatype == XSD.dateTime  # type: ignore[union-attr]

    # violationPath — references biz:effectiveDate
    paths = list(g.objects(record, BIZ.violationPath))
    assert len(paths) == 1
    assert "effectiveDate" in str(paths[0])


def test_single_violation_record_uri_scheme() -> None:
    """Record subject URI follows urn:quarantine:{sha}:{hash16(doc_uri)} scheme."""
    mock_client = MagicMock()
    gate = ShaclGate(mock_client)
    gate.validate(_policy_graph(omit={"effectiveDate"}), doc_uri=DOC_URI, sha=SHA)

    g = _parse_quarantine_insert(mock_client.sparql_update.call_args[0][0])
    records = list(g.subjects(RDF.type, BIZ.QuarantineRecord))
    assert len(records) == 1

    expected_hash = hashlib.sha256(DOC_URI.encode()).hexdigest()[:16]
    expected_uri = f"urn:quarantine:{SHA}:{expected_hash}"
    assert str(records[0]) == expected_uri


# ---------------------------------------------------------------------------
# AC3 — multi-violation: two biz:violationPath triples on same record
# ---------------------------------------------------------------------------


def test_multi_violation_two_paths() -> None:
    """biz:Policy missing effectiveDate and scope -> two biz:violationPath triples."""
    mock_client = MagicMock()
    gate = ShaclGate(mock_client)
    gate.validate(_policy_graph(omit={"effectiveDate", "scope"}), doc_uri=DOC_URI, sha=SHA)

    g = _parse_quarantine_insert(mock_client.sparql_update.call_args[0][0])
    records = list(g.subjects(RDF.type, BIZ.QuarantineRecord))
    assert len(records) == 1

    paths = list(g.objects(records[0], BIZ.violationPath))
    assert len(paths) == 2, f"Expected 2 violationPath triples, got {len(paths)}: {paths}"
    path_strs = {str(p) for p in paths}
    assert any("effectiveDate" in p for p in path_strs)
    assert any("scope" in p for p in path_strs)


def test_multi_violation_single_quarantine_insert_call() -> None:
    """Multiple violations -> still exactly one sparql_update call."""
    mock_client = MagicMock()
    gate = ShaclGate(mock_client)
    gate.validate(_policy_graph(omit={"effectiveDate", "scope"}), doc_uri=DOC_URI, sha=SHA)

    mock_client.sparql_update.assert_called_once()


# ---------------------------------------------------------------------------
# AC4 — Neptune INSERT failure -> quarantine_insert_failed (no exception propagation)
# ---------------------------------------------------------------------------


def test_neptune_failure_returns_quarantine_insert_failed() -> None:
    """ConnectionError during INSERT -> GateResult(outcome="quarantine_insert_failed")."""
    mock_client = MagicMock()
    mock_client.sparql_update.side_effect = ConnectionError("timed out")
    gate = ShaclGate(mock_client)

    result = gate.validate(_policy_graph(omit={"effectiveDate"}), doc_uri=DOC_URI, sha=SHA)

    assert result.outcome == "quarantine_insert_failed"


def test_neptune_failure_error_field_contains_exception_text() -> None:
    """error field on quarantine_insert_failed contains str(exception)."""
    mock_client = MagicMock()
    mock_client.sparql_update.side_effect = ConnectionError("timed out")
    gate = ShaclGate(mock_client)

    result = gate.validate(_policy_graph(omit={"effectiveDate"}), doc_uri=DOC_URI, sha=SHA)

    assert result.error is not None
    assert "timed out" in result.error


def test_neptune_failure_does_not_raise() -> None:
    """ShaclGate.validate() never raises on any Neptune exception."""
    mock_client = MagicMock()
    mock_client.sparql_update.side_effect = RuntimeError("unexpected")
    gate = ShaclGate(mock_client)

    result = gate.validate(_policy_graph(omit={"effectiveDate"}), doc_uri=DOC_URI, sha=SHA)
    assert result.outcome == "quarantine_insert_failed"


def test_neptune_failure_logs_error(caplog: pytest.LogCaptureFixture) -> None:
    """ERROR log is emitted on quarantine INSERT failure with exception detail."""
    mock_client = MagicMock()
    mock_client.sparql_update.side_effect = ConnectionError("timed out")
    gate = ShaclGate(mock_client)

    with caplog.at_level(logging.ERROR, logger="graphrag.validation.shacl._gate"):
        gate.validate(_policy_graph(omit={"effectiveDate"}), doc_uri=DOC_URI, sha=SHA)

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(error_records) >= 1
    # Log message carries doc_uri and sha for correlation; exception detail is in exc_info.
    assert any("INSERT failed" in r.message for r in error_records)
    assert any(r.exc_info is not None for r in error_records)


# ---------------------------------------------------------------------------
# Zero-violations edge case — conforms=False but no ShapeViolation objects
# ---------------------------------------------------------------------------


def test_zero_violations_edge_case(caplog: pytest.LogCaptureFixture) -> None:
    """conforms=False with no violations -> quarantined with fallback reason and WARNING log."""
    mock_client = MagicMock()

    fake_result = ValidationResult(conforms=False, violations=[])

    with patch("graphrag.validation.shacl._gate.validate_graph", return_value=fake_result):
        gate = ShaclGate(mock_client)
        with caplog.at_level(logging.WARNING, logger="graphrag.validation.shacl._gate"):
            result = gate.validate(Graph(), doc_uri=DOC_URI, sha=SHA)

    assert result.outcome == "quarantined"
    mock_client.sparql_update.assert_called_once()

    # Fallback reason in the INSERT
    g = _parse_quarantine_insert(mock_client.sparql_update.call_args[0][0])
    reasons = list(g.objects(None, BIZ.quarantineReason))
    assert len(reasons) == 1
    assert "no violation details" in str(reasons[0])

    # No violationPath triples (no paths to extract)
    paths = list(g.objects(None, BIZ.violationPath))
    assert len(paths) == 0

    # WARNING log emitted
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) >= 1
    assert any("no violations" in r.message for r in warnings)


# ---------------------------------------------------------------------------
# Injection guard (spec AC3) — SPARQL payload in sh:resultMessage safely escaped
# ---------------------------------------------------------------------------


def test_injection_guard_reason_safely_escaped() -> None:
    """SPARQL injection payload in violation message is safely escaped by rdflib Literal.n3().

    The payload '" . } GRAPH <urn:graph:normative> { <urn:evil> a biz:Policy } #'
    is derived from a hypothetical malicious sh:resultMessage.  rdflib escapes the
    embedded double-quote to \\", keeping the payload inside the N-Triples literal.
    The Neptune client receives exactly one valid SPARQL call; the N-Triples body
    parses cleanly and the literal value equals the original payload verbatim.
    """
    mock_client = MagicMock()

    fake_result = ValidationResult(
        conforms=False,
        violations=[
            ShapeViolation(
                focus_node="urn:doc:test#doc1",
                path="https://graphrag-aws.demo/biz-ops/ontology#effectiveDate",
                message=INJECT_PAYLOAD,
                source_shape="",
            )
        ],
    )

    with patch("graphrag.validation.shacl._gate.validate_graph", return_value=fake_result):
        gate = ShaclGate(mock_client)
        result = gate.validate(Graph(), doc_uri=DOC_URI, sha=SHA)

    assert result.outcome == "quarantined"
    mock_client.sparql_update.assert_called_once()

    sparql = mock_client.sparql_update.call_args[0][0]

    # Parse the N-Triples body — if injection escaped the literal the parse would
    # fail or produce an unexpected graph structure.
    g = _parse_quarantine_insert(sparql)

    reasons = list(g.objects(None, BIZ.quarantineReason))
    assert len(reasons) == 1, f"Expected one quarantineReason, got {len(reasons)}"
    # The injection payload must be preserved verbatim as the literal value.
    assert str(reasons[0]) == INJECT_PAYLOAD


# ---------------------------------------------------------------------------
# URI-position security: invalid URI in doc_uri/sha → quarantine_insert_failed
# ---------------------------------------------------------------------------


def test_invalid_doc_uri_returns_quarantine_insert_failed() -> None:
    """doc_uri containing a URI-invalid char (space) -> quarantine_insert_failed, not raise.

    rdflib raises ValueError for invalid URIRefs; the gate's try/except must catch
    this so the caller always receives a GateResult, never a propagated exception.
    """
    mock_client = MagicMock()

    fake_result = ValidationResult(
        conforms=False,
        violations=[
            ShapeViolation(
                focus_node="urn:doc:test",
                path="https://graphrag-aws.demo/biz-ops/ontology#effectiveDate",
                message="missing",
                source_shape="",
            )
        ],
    )

    # A doc_uri with a space is invalid per IRI grammar; rdflib raises on serialize.
    bad_doc_uri = "urn:doc:has a space"

    with patch("graphrag.validation.shacl._gate.validate_graph", return_value=fake_result):
        gate = ShaclGate(mock_client)
        result = gate.validate(Graph(), doc_uri=bad_doc_uri, sha=SHA)

    # Must not propagate — gate returns quarantine_insert_failed gracefully.
    assert result.outcome == "quarantine_insert_failed"
    assert result.error is not None


# ---------------------------------------------------------------------------
# AC6 — import isolation: ShaclGate and GateResult importable without boto3
# ---------------------------------------------------------------------------


def test_import_no_boto3() -> None:
    """ShaclGate and GateResult are importable from an environment without boto3."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    # Ensure the source root is on PYTHONPATH regardless of install topology.
    src_root = str(Path(__file__).parent.parent.parent / "src")
    env = {**os.environ, "PYTHONPATH": src_root}

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from graphrag.validation.shacl import ShaclGate, GateResult; "
                "import sys; "
                "assert 'boto3' not in sys.modules, "
                "'boto3 leaked into sys.modules after graphrag.validation.shacl import'"
            ),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, "Import isolation check failed:\n" + proc.stderr
