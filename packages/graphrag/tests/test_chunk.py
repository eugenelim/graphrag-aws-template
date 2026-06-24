"""T1 — chunking the prose-rich subset, with provenance + entity IDs (AC1).

# STUB: AC1
"""

from __future__ import annotations

from pathlib import Path

from graphrag.chunk import chunk_corpus
from graphrag.normalize import kep_id, sig_id
from graphrag.parse import ParsedMarkdown
from graphrag.sources import ENHANCEMENTS, ParsedDoc, load_corpus

CORPUS = Path(__file__).parent / "fixtures" / "vector" / "corpus"


def _docs() -> list[ParsedDoc]:
    return load_corpus(CORPUS / "community", CORPUS / "enhancements")


def test_only_prose_docs_are_chunked() -> None:
    chunks = chunk_corpus(_docs())
    # Every chunk traces to a sig_readme or kep_readme path — never sigs.yaml/kep.yaml.
    assert chunks
    for chunk in chunks:
        assert chunk.doc_path.endswith("README.md")
    assert not any("sigs.yaml" in c.doc_path or "kep.yaml" in c.doc_path for c in chunks)


def test_chunks_carry_provenance_and_entity_ids() -> None:
    by_id = {c.id: c for c in chunk_corpus(_docs())}
    sig_chunk = next(c for c in by_id.values() if c.doc_path == "sig-network/README.md")
    assert sig_chunk.source == "community"
    assert sig_chunk.heading  # nearest preceding heading captured
    assert sig_chunk.entity_ids == [sig_id("sig-network")]

    kep_chunk = next(
        c for c in by_id.values() if "1287-in-place-update-pod-resources" in c.doc_path
    )
    assert kep_chunk.entity_ids == [kep_id("1287"), sig_id("sig-node")]


def test_kep_entity_id_is_byte_identical_to_graph_node_id() -> None:
    # The slice-3 join requires the chunk's KEP entity-id to equal the graph KEP node
    # id exactly. Guards against the colon/hyphen drift (kep-<n>, not kep:<n>).
    chunks = chunk_corpus(_docs())
    legacy = next(c for c in chunks if "0009-legacy-node-allocatable" in c.doc_path)
    assert kep_id("0009") == "kep-9"  # normalize strips the zero-pad
    assert "kep-9" in legacy.entity_ids
    assert not any(eid.startswith("kep:") for c in chunks for eid in c.entity_ids)


def test_doc_with_no_prose_body_yields_no_chunks() -> None:
    empty = ParsedDoc(
        ENHANCEMENTS,
        "keps/sig-node/9999-empty/README.md",
        "kep_readme",
        payload={"owning_sig_dir": "sig-node", "dir_number": "9999"},
        markdown=ParsedMarkdown(
            front_matter={}, headings=["# Title"], body="# Title\n## Heading\n"
        ),
    )
    assert chunk_corpus([empty]) == []


def test_long_section_splits_with_overlap() -> None:
    body = "# Big\n" + ("word " * 600)  # ~3000 chars under one heading
    doc = ParsedDoc(
        ENHANCEMENTS,
        "keps/sig-node/1-big/README.md",
        "kep_readme",
        payload={"owning_sig_dir": "sig-node", "dir_number": "1"},
        markdown=ParsedMarkdown(front_matter={}, headings=["# Big"], body=body),
    )
    chunks = chunk_corpus([doc], chunk_size=1000, overlap=150)
    assert len(chunks) >= 3
    assert all(len(c.text) <= 1000 for c in chunks)
    # ids are per-doc ordinals.
    assert [c.id.rsplit("#", 1)[1] for c in chunks] == [str(i) for i in range(len(chunks))]
