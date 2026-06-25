"""Chunking the prose-rich doc subset — SIG charters + KEP READMEs (slice-2 AC1).

Only prose-rich docs are chunked (``sig_readme``, ``kep_readme``); the structured
YAML (``sigs.yaml``, ``kep.yaml``) is the *graph's* input, not the vector index's
(charter pattern 2 — favour the prose-rich subset for the vector showcase). Each
chunk carries provenance (source repo, doc path, nearest heading) and the owning
entity IDs — **derived via the slice-1 ``normalize`` functions so they are
byte-identical to the graph node IDs**, which slice-3 seed-and-expand joins on
(ADR-0001). Note the ID-form asymmetry is slice-1's, not new: SIG/Person use ``:``,
KEP uses ``-`` (``normalize.kep_id``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .normalize import kep_id, sig_id

if TYPE_CHECKING:
    # Imported for type hints only. Keeping these out of the runtime import graph
    # means `import graphrag.chunk` does not pull in `parse`/`sources` (and thus
    # PyYAML), so the vector smoke-probe Lambda — which bundles only the pure-Python
    # package, with boto3/botocore from the runtime and no pyyaml — imports cleanly.
    from .parse import ParsedMarkdown
    from .sources import ParsedDoc

DEFAULT_CHUNK_SIZE = 1000
DEFAULT_OVERLAP = 150

# The two prose-rich doc kinds; everything else (sigs_index, kep_yaml) is graph input.
_PROSE_KINDS = ("sig_readme", "kep_readme")


@dataclass
class Chunk:
    """A retrievable unit of prose with provenance and owning-entity metadata."""

    id: str
    text: str
    source: str  # COMMUNITY | ENHANCEMENTS
    doc_path: str  # path relative to the corpus root, for provenance in the trace
    heading: str  # the nearest preceding heading (the section the chunk came from)
    entity_ids: list[str] = field(default_factory=list)
    # Synthetic visibility tier (slice 4) — a TEACHING stand-in for an ACL, never real
    # authz. Defaults to "public"; the ingest-path labeling (graphrag.labels) sets it to
    # compose() of the owning entities' tiers. Carried as OpenSearch chunk metadata for the
    # permission filter (store/opensearch.py); never set by chunk_corpus itself, which stays
    # PyYAML-free.
    visibility: str = "public"


def _entity_ids(doc: ParsedDoc) -> list[str]:
    """Owning entity IDs for a prose doc, derived via ``normalize`` (== graph node IDs)."""
    if doc.kind == "sig_readme":
        slug = doc.payload.get("slug")
        return [sig_id(slug)] if isinstance(slug, str) else []
    if doc.kind == "kep_readme":
        ids: list[str] = []
        number = doc.payload.get("dir_number")
        if number is not None:
            ids.append(kep_id(str(number)))
        owning_sig = doc.payload.get("owning_sig_dir")
        if isinstance(owning_sig, str):
            ids.append(sig_id(owning_sig))
        return ids
    return []


def _sections(md: ParsedMarkdown) -> list[tuple[str, str]]:
    """Split a body into (nearest-heading, section-text) pairs; heading lines are dropped
    from the text but kept as the section's metadata. Empty sections are skipped."""
    heading = ""
    buf: list[str] = []
    out: list[tuple[str, str]] = []
    for line in md.body.splitlines():
        if line.lstrip().startswith("#"):
            text = "\n".join(buf).strip()
            if text:
                out.append((heading, text))
            buf = []
            heading = line.strip().lstrip("#").strip()
        else:
            buf.append(line)
    text = "\n".join(buf).strip()
    if text:
        out.append((heading, text))
    return out


def _split(text: str, size: int, overlap: int) -> list[str]:
    """Sliding-window split of an over-long section, with overlap between windows."""
    if len(text) <= size:
        return [text]
    step = max(1, size - overlap)
    return [text[i : i + size] for i in range(0, len(text), step)]


def chunk_corpus(
    docs: list[ParsedDoc],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """Chunk the prose-rich docs of a parsed corpus into provenance-bearing ``Chunk``s.

    Heading-aware: each section becomes one or more chunks (sliding-window split when
    longer than ``chunk_size``). A doc with no prose body yields no chunks.
    """
    chunks: list[Chunk] = []
    for doc in docs:
        if doc.kind not in _PROSE_KINDS or doc.markdown is None:
            continue
        entity_ids = _entity_ids(doc)
        ordinal = 0
        for heading, section in _sections(doc.markdown):
            for piece in _split(section, chunk_size, overlap):
                body = piece.strip()
                if not body:
                    continue
                chunks.append(
                    Chunk(
                        id=f"{doc.source}/{doc.path}#{ordinal}",
                        text=body,
                        source=doc.source,
                        doc_path=doc.path,
                        heading=heading,
                        entity_ids=list(entity_ids),
                    )
                )
                ordinal += 1
    return chunks
