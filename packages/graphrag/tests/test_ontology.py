"""Tests for graphrag.ontology — TDD stubs, red before green."""
import subprocess
import sys

import pyshacl
import pytest
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, SKOS, XSD

BIZ = Namespace("https://graphrag-aws.demo/biz-ops/ontology#")
SCHEMA = Namespace("https://schema.org/")
PROV = Namespace("http://www.w3.org/ns/prov#")


def _policy_graph(*, omit: str | None = None) -> Graph:
    """Well-formed biz:Policy triple-set; pass omit=<prop_localname> to break it."""
    g = Graph()
    doc = BIZ.doc1
    g.add((doc, RDF.type, BIZ.Policy))
    props: dict[str, tuple] = {
        "name":          (SCHEMA.name,        Literal("AUP", datatype=XSD.string)),
        "effectiveDate": (BIZ.effectiveDate,  Literal("2024-01-01", datatype=XSD.date)),
        "scope":         (BIZ.scope,          Literal("All employees", datatype=XSD.string)),
        "hasPII":        (BIZ.hasPII,         Literal("true", datatype=XSD.boolean)),
        "gitCommitSHA":  (BIZ.gitCommitSHA,   Literal("abc123", datatype=XSD.string)),
    }
    for key, (pred, obj) in props.items():
        if key != omit:
            g.add((doc, pred, obj))
    return g


def _chunk_graph(*, omit: str | None = None) -> Graph:
    g = Graph()
    doc = BIZ.chunk1
    g.add((doc, RDF.type, BIZ.Chunk))
    props: dict[str, tuple] = {
        "wasDerivedFrom": (PROV.wasDerivedFrom, URIRef("urn:doc:example")),
        "chunkIndex":     (BIZ.chunkIndex,      Literal(0, datatype=XSD.integer)),
        "embeddingModel": (BIZ.embeddingModel,  Literal("titan-v2", datatype=XSD.string)),
    }
    for key, (pred, obj) in props.items():
        if key != omit:
            g.add((doc, pred, obj))
    return g


# ---------------------------------------------------------------------------
# AC3 — well-formed Policy conforms
# ---------------------------------------------------------------------------

def test_validate_well_formed_policy() -> None:
    from graphrag.ontology import validate_graph

    result = validate_graph(_policy_graph())
    assert result.conforms is True
    assert result.violations == []


# ---------------------------------------------------------------------------
# AC4 — missing effectiveDate → violation with path containing "effectiveDate"
# ---------------------------------------------------------------------------

def test_validate_missing_effective_date() -> None:
    from graphrag.ontology import validate_graph

    result = validate_graph(_policy_graph(omit="effectiveDate"))
    assert result.conforms is False
    assert any("effectiveDate" in v.path for v in result.violations)


# ---------------------------------------------------------------------------
# AC5 — Chunk missing prov:wasDerivedFrom
# ---------------------------------------------------------------------------

def test_validate_chunk_missing_derived_from() -> None:
    from graphrag.ontology import validate_graph

    result = validate_graph(_chunk_graph(omit="wasDerivedFrom"))
    assert result.conforms is False
    assert any("wasDerivedFrom" in v.path for v in result.violations)


# ---------------------------------------------------------------------------
# Positive-conformance for all 9 shapes
# ---------------------------------------------------------------------------

def _full_graph(class_uri: str) -> Graph:
    """Build a minimal well-formed graph satisfying all required properties for class_uri."""
    g = Graph()
    cls = URIRef(class_uri.replace("biz:", str(BIZ)).replace("schema:", str(SCHEMA)))
    doc = URIRef("urn:doc:test")
    g.add((doc, RDF.type, cls))
    if class_uri in ("biz:Policy", "biz:Standard", "biz:Guideline"):
        g.add((doc, SCHEMA.name,        Literal("Doc", datatype=XSD.string)))
        g.add((doc, BIZ.effectiveDate,  Literal("2024-01-01", datatype=XSD.date)))
        g.add((doc, BIZ.scope,          Literal("All", datatype=XSD.string)))
        g.add((doc, BIZ.hasPII,         Literal("false", datatype=XSD.boolean)))
        g.add((doc, BIZ.gitCommitSHA,   Literal("sha1", datatype=XSD.string)))
    elif class_uri in ("biz:SOP", "biz:JobAid", "biz:Transcript"):
        g.add((doc, SCHEMA.name,       Literal("Doc", datatype=XSD.string)))
        g.add((doc, BIZ.gitCommitSHA,  Literal("sha1", datatype=XSD.string)))
    elif class_uri == "biz:Chunk":
        g.add((doc, PROV.wasDerivedFrom, URIRef("urn:doc:parent")))
        g.add((doc, BIZ.chunkIndex,      Literal(0, datatype=XSD.integer)))
        g.add((doc, BIZ.embeddingModel,  Literal("titan-v2", datatype=XSD.string)))
    elif class_uri in ("biz:BusinessDomain", "biz:Journey"):
        g.add((doc, SKOS.prefLabel, Literal("Label", lang="en")))
    return g


@pytest.mark.parametrize("class_uri", [
    "biz:Policy",
    "biz:Standard",
    "biz:Guideline",
    "biz:SOP",
    "biz:JobAid",
    "biz:Transcript",
    "biz:Chunk",
    "biz:BusinessDomain",
    "biz:Journey",
])
def test_validate_well_formed_all_shapes(class_uri: str) -> None:
    from graphrag.ontology import validate_graph

    result = validate_graph(_full_graph(class_uri))
    assert result.conforms is True, f"{class_uri}: {result.violations}"
    assert result.violations == []


# ---------------------------------------------------------------------------
# AC2 behavioral coverage — one required property omitted per shape
# ---------------------------------------------------------------------------

def _minimal_graph(class_uri: str, omit_prop: str) -> Graph:
    """Build a minimal well-formed graph for class_uri then omit omit_prop."""
    g = Graph()
    cls = URIRef(class_uri.replace("biz:", str(BIZ)).replace("schema:", str(SCHEMA)))
    doc = URIRef("urn:doc:test")
    g.add((doc, RDF.type, cls))

    # Full property sets by class family
    if class_uri in ("biz:Standard", "biz:Guideline"):
        props = {
            "schema:name":        (SCHEMA.name,        Literal("Doc", datatype=XSD.string)),
            "biz:effectiveDate":  (BIZ.effectiveDate,  Literal("2024-01-01", datatype=XSD.date)),
            "biz:scope":          (BIZ.scope,           Literal("All", datatype=XSD.string)),
            "biz:hasPII":         (BIZ.hasPII,          Literal("false", datatype=XSD.boolean)),
            "biz:gitCommitSHA":   (BIZ.gitCommitSHA,   Literal("sha1", datatype=XSD.string)),
        }
    elif class_uri in ("biz:SOP", "biz:JobAid", "biz:Transcript"):
        props = {
            "schema:name":       (SCHEMA.name,       Literal("Doc", datatype=XSD.string)),
            "biz:gitCommitSHA":  (BIZ.gitCommitSHA,  Literal("sha1", datatype=XSD.string)),
        }
    elif class_uri == "biz:BusinessDomain":
        props = {
            "skos:prefLabel": (SKOS.prefLabel, Literal("Finance", lang="en")),
        }
    elif class_uri == "biz:Journey":
        props = {
            "skos:prefLabel": (SKOS.prefLabel, Literal("Onboarding", lang="en")),
        }
    else:
        props = {}

    for key, (pred, obj) in props.items():
        if key != omit_prop:
            g.add((doc, pred, obj))
    return g


@pytest.mark.parametrize("class_uri,omit_prop,omit_localname", [
    ("biz:Standard",       "biz:effectiveDate",  "effectiveDate"),
    ("biz:Guideline",      "biz:scope",          "scope"),
    ("biz:SOP",            "schema:name",        "name"),
    ("biz:JobAid",         "biz:gitCommitSHA",   "gitCommitSHA"),
    ("biz:Transcript",     "schema:name",        "name"),
    ("biz:BusinessDomain", "skos:prefLabel",     "prefLabel"),
    ("biz:Journey",        "skos:prefLabel",     "prefLabel"),
])
def test_validate_missing_required_property(
    class_uri: str, omit_prop: str, omit_localname: str
) -> None:
    from graphrag.ontology import validate_graph

    result = validate_graph(_minimal_graph(class_uri, omit_prop))
    assert result.conforms is False
    assert any(omit_localname in v.path for v in result.violations)


# ---------------------------------------------------------------------------
# AC1 — exactly 9 owl:Class URIs + subclass hierarchy pinned
# ---------------------------------------------------------------------------

def test_ontology_exactly_9_classes_and_hierarchy() -> None:
    from graphrag.ontology import load_ontology

    SCHEMA_NS = Namespace("https://schema.org/")
    SKOS_NS = Namespace("http://www.w3.org/2004/02/skos/core#")

    g = load_ontology()
    classes = list(g.subjects(RDF.type, OWL.Class))
    assert len(classes) == 9

    # Key rdfs:subClassOf edges from ADR-0012
    assert (BIZ.Policy,    RDFS.subClassOf, SCHEMA_NS.DigitalDocument) in g
    assert (BIZ.Standard,  RDFS.subClassOf, BIZ.Policy) in g
    assert (BIZ.Guideline, RDFS.subClassOf, BIZ.Policy) in g
    assert (BIZ.SOP,       RDFS.subClassOf, SCHEMA_NS.CreativeWork) in g
    assert (BIZ.Chunk,     RDFS.subClassOf, SCHEMA_NS.CreativeWork) in g
    assert (BIZ.BusinessDomain, RDFS.subClassOf, SKOS_NS.ConceptScheme) in g
    assert (BIZ.Journey,   RDFS.subClassOf, SKOS_NS.Concept) in g


# ---------------------------------------------------------------------------
# AC2 — exactly 9 sh:NodeShape URIs
# ---------------------------------------------------------------------------

def test_shapes_exactly_9() -> None:
    from graphrag.ontology._resources import _load_shapes

    SH = Namespace("http://www.w3.org/ns/shacl#")
    g = _load_shapes()
    shapes = list(g.subjects(RDF.type, SH.NodeShape))
    assert len(shapes) == 9


# ---------------------------------------------------------------------------
# Cross-cutting integration — ontology conforms against its own shapes
# (plan construction test: classes/properties, no instance → conforms=True)
# ---------------------------------------------------------------------------

def test_ontology_self_conforms_against_shapes() -> None:
    from graphrag.ontology import load_ontology
    from graphrag.ontology._resources import _load_shapes

    conforms, _, _ = pyshacl.validate(load_ontology(), shacl_graph=_load_shapes(), inference="none")
    assert conforms is True


# ---------------------------------------------------------------------------
# AC6 — load_ontology returns ≥9 OWL classes
# ---------------------------------------------------------------------------

def test_load_ontology_returns_9_classes() -> None:
    from graphrag.ontology import load_ontology

    g = load_ontology()
    classes = list(g.subjects(RDF.type, OWL.Class))
    assert len(classes) >= 9


# ---------------------------------------------------------------------------
# AC7 — SKOS concept addable without modifying biz_ops.ttl
# ---------------------------------------------------------------------------

def test_skos_concept_addable() -> None:
    from graphrag.ontology import load_ontology

    g = load_ontology()
    ttl = """
@prefix biz:  <https://graphrag-aws.demo/biz-ops/ontology#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
biz:Finance a biz:BusinessDomain ; skos:prefLabel "Finance"@en .
"""
    g.parse(data=ttl, format="turtle")
    results = list(g.subjects(RDF.type, BIZ.BusinessDomain))
    assert any("Finance" in str(r) for r in results)


# ---------------------------------------------------------------------------
# AC11 — datatype constraint: plain string for effectiveDate
# ---------------------------------------------------------------------------

def test_validate_wrong_datatype() -> None:
    from graphrag.ontology import validate_graph

    g = _policy_graph(omit="effectiveDate")
    g.add((BIZ.doc1, BIZ.effectiveDate, Literal("2024-01-01")))  # plain xsd:string, no ^^xsd:date
    result = validate_graph(g)
    assert result.conforms is False


# ---------------------------------------------------------------------------
# AC11 — maxCount constraint: two distinct xsd:date literals
# ---------------------------------------------------------------------------

def test_validate_max_count_violation() -> None:
    from graphrag.ontology import validate_graph

    g = _policy_graph()  # already has "2024-01-01"^^xsd:date
    # Add a second DISTINCT date — identical triples deduplicate in RDF set semantics,
    # so the two values must differ for two triples to actually be asserted.
    g.add((BIZ.doc1, BIZ.effectiveDate, Literal("2024-07-01", datatype=XSD.date)))
    result = validate_graph(g)
    assert result.conforms is False
    assert any("effectiveDate" in v.path for v in result.violations)


# ---------------------------------------------------------------------------
# AC8 — import isolation: no AWS SDK modules in sys.modules after import
# ---------------------------------------------------------------------------

def test_import_no_aws_deps() -> None:
    proc = subprocess.run(
        [
            sys.executable, "-c",
            "import sys; import graphrag.ontology; "
            "assert not {'boto3','botocore','aws_cdk'} & sys.modules.keys(), "
            "f'AWS SDK leaked: {set(sys.modules) & {\"boto3\",\"botocore\",\"aws_cdk\"}}'",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


# ---------------------------------------------------------------------------
# T5 — AC9 completeness lint (added here; implementation in _lint.py)
# ---------------------------------------------------------------------------

def test_completeness_lint_clean_pair() -> None:
    from graphrag.ontology import check_class_shape_completeness, load_ontology
    from graphrag.ontology._resources import _load_shapes

    missing = check_class_shape_completeness(load_ontology(), _load_shapes())
    assert missing == []


def test_completeness_lint_detects_missing_shape() -> None:
    from graphrag.ontology import check_class_shape_completeness

    ont = Graph()
    ont.add((BIZ.NewClass, RDF.type, OWL.Class))
    shapes = Graph()  # empty — no sh:NodeShape for biz:NewClass
    missing = check_class_shape_completeness(ont, shapes)
    assert str(BIZ.NewClass) in missing


def test_ci_completeness_fixture() -> None:
    """CI gate: fails if any OWL class in biz_ops.ttl lacks a matching sh:NodeShape."""
    from graphrag.ontology import check_class_shape_completeness, load_ontology
    from graphrag.ontology._resources import _load_shapes

    assert check_class_shape_completeness(load_ontology(), _load_shapes()) == []
