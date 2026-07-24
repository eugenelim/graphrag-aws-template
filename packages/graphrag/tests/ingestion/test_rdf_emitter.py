"""TDD tests for graphrag.ingestion._rdf.RDFEmitter."""

from __future__ import annotations

from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDF

from graphrag.ingestion._rdf import EmitResult, RDFEmitter
from graphrag.ingestion._rdf._classify import classify

BIZ = Namespace("https://graphrag-aws.demo/biz-ops/ontology#")
SCHEMA = Namespace("https://schema.org/")

DOC_URI = "urn:doc:test-repo:policies/hr.md"
SHA = "cafe1234"  # pragma: allowlist secret
GIT_REPO = "test-org/test-repo"
GIT_PATH = "policies/hr.md"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_POLICY_TEXT = """---
type: policy
title: HR Acceptable Use Policy
effective_date: 2024-01-01
scope: All employees
---

# HR Acceptable Use Policy

This policy describes acceptable use of company resources.
Employees are expected to comply with all sections of this document.
Failure to comply may result in disciplinary action.
"""

_VALID_SOP_TEXT = """# Incident Response SOP

This document describes how to respond to security incidents.
Follow these steps carefully to minimize impact on production systems.
Escalate to the security team if needed. Document all actions taken.
"""


def _emit(
    path: str,
    text: str,
    doc_uri: str = DOC_URI,
    pii_flagged: bool = False,
) -> EmitResult:
    emitter = RDFEmitter()
    return emitter.emit(
        doc_uri=doc_uri,
        path=path,
        sha=SHA,
        git_repo=GIT_REPO,
        extractor="pandoc",
        clean_text=text,
        pii_flagged=pii_flagged,
    )


# ---------------------------------------------------------------------------
# T3-1: Policy path → rdf:type biz:Policy, normative partition
# ---------------------------------------------------------------------------


def test_policy_path_gives_normative_partition() -> None:
    result = _emit("policies/hr.md", _VALID_POLICY_TEXT)
    assert result.conforms is True
    assert result.named_graph == "urn:graph:normative"
    # Verify rdf:type in turtle
    g = Graph()
    g.parse(data=result.turtle, format="turtle")
    doc = URIRef(DOC_URI)
    assert (doc, RDF.type, BIZ.Policy) in g


# ---------------------------------------------------------------------------
# T3-2: SOP path → rdf:type biz:SOP, descriptive partition
# ---------------------------------------------------------------------------


def test_sop_path_gives_descriptive_partition() -> None:
    sop_uri = "urn:doc:test-repo:sops/ir.md"
    result = _emit("sops/ir.md", _VALID_SOP_TEXT, doc_uri=sop_uri)
    assert result.conforms is True
    assert result.named_graph == "urn:graph:descriptive"
    g = Graph()
    g.parse(data=result.turtle, format="turtle")
    doc = URIRef(sop_uri)
    assert (doc, RDF.type, BIZ.SOP) in g


# ---------------------------------------------------------------------------
# T3-3: biz:Policy missing biz:effectiveDate → SHACL fail → quarantine
# ---------------------------------------------------------------------------


def test_policy_without_effective_date_fails_shacl() -> None:
    # No front-matter (and no effectiveDate/scope) → SHACL violation
    text_no_fm = (
        "# HR Policy\n\n"
        "This policy describes the acceptable use of company resources.\n"
        "Employees must comply with all sections of this document.\n"
        "Violations will be addressed through our disciplinary process."
    )
    result = _emit("policies/hr.md", text_no_fm)
    assert result.conforms is False
    assert result.quarantine_reason is not None
    assert "SHACL" in result.quarantine_reason


# ---------------------------------------------------------------------------
# T3-4: PROV-O triples present on document subject
# ---------------------------------------------------------------------------


def test_provenance_triples_on_document_subject() -> None:
    result = _emit("policies/hr.md", _VALID_POLICY_TEXT)
    assert result.conforms is True
    g = Graph()
    g.parse(data=result.turtle, format="turtle")
    # SPARQL SELECT to verify all required provenance properties
    sparql = (
        """
    PREFIX biz: <https://graphrag-aws.demo/biz-ops/ontology#>
    SELECT ?sha ?path ?repo ?extractor WHERE {
        <"""
        + DOC_URI
        + """> biz:gitCommitSHA ?sha ;
             biz:gitPath      ?path ;
             biz:gitRepo      ?repo ;
             biz:extractorUsed ?extractor .
    }
    """
    )
    rows = list(g.query(sparql))
    assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
    row = rows[0]
    assert str(row.sha) == SHA
    assert str(row.path) == GIT_PATH
    assert str(row.repo) == GIT_REPO
    assert str(row.extractor) == "pandoc"


# ---------------------------------------------------------------------------
# T3-5: PII-flagged SOP stays in descriptive partition
# ---------------------------------------------------------------------------


def test_pii_flagged_sop_stays_in_descriptive_partition() -> None:
    sop_uri = "urn:doc:test-repo:sops/ir.md"
    result = _emit("sops/ir.md", _VALID_SOP_TEXT, doc_uri=sop_uri, pii_flagged=True)
    assert result.conforms is True
    assert result.named_graph == "urn:graph:descriptive"
    # biz:hasPII is true on the document subject
    g = Graph()
    g.parse(data=result.turtle, format="turtle")
    doc = URIRef(sop_uri)
    # SOP shape doesn't require biz:hasPII, but we still emit it
    pii_triples = list(g.triples((doc, BIZ.hasPII, None)))
    assert len(pii_triples) == 1


# ---------------------------------------------------------------------------
# T3-6: Emitted Turtle parses without error
# ---------------------------------------------------------------------------


def test_emitted_turtle_parses_cleanly() -> None:
    sop_uri = "urn:doc:test-repo:sops/ir.md"
    result = _emit("sops/ir.md", _VALID_SOP_TEXT, doc_uri=sop_uri)
    assert result.turtle, "turtle should be non-empty for a conforming graph"
    g = Graph()
    # This raises if the Turtle is malformed
    g.parse(data=result.turtle, format="turtle")
    assert len(g) > 0


# ---------------------------------------------------------------------------
# classify() unit tests (fast, no SHACL)
# ---------------------------------------------------------------------------


def test_classify_policies_path() -> None:
    cls = classify("policies/hr.md", "# HR Policy")
    assert cls.doc_type == "policy"
    assert cls.partition == "urn:graph:normative"


def test_classify_sops_path() -> None:
    cls = classify("sops/ir.md", "# IR SOP")
    assert cls.doc_type == "sop"
    assert cls.partition == "urn:graph:descriptive"


def test_classify_procedures_path() -> None:
    cls = classify("procedures/onboarding.md", "# Onboarding")
    assert cls.doc_type == "sop"


def test_classify_frontmatter_type_overrides_path() -> None:
    text = "---\ntype: guideline\n---\n# Something"
    cls = classify("policies/something.md", text)
    assert cls.doc_type == "guideline"
    assert cls.partition == "urn:graph:normative"


def test_classify_defaults_to_sop() -> None:
    cls = classify("misc/unknown.md", "# Unknown document")
    assert cls.doc_type == "sop"
    assert cls.partition == "urn:graph:descriptive"


def test_classify_extracts_effective_date_from_frontmatter() -> None:
    text = "---\ntype: policy\neffective_date: 2024-06-01\nscope: Global\n---"
    cls = classify("policies/x.md", text)
    assert cls.effective_date == "2024-06-01"
    assert cls.scope == "Global"
