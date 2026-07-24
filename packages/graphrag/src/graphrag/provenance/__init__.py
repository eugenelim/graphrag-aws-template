"""graphrag.provenance — PROV-O emission and citation resolution.

Public API:

    ProvenanceEmitter
        .emit_provenance(doc_uri, sha, git_path, git_repo, extractor,
                         started_at, ended_at) -> rdflib.Graph
        .emit_chunk_provenance(chunk_uri, doc_uri, chunk_index) -> rdflib.Graph

    CitationResolver(store)
        .resolve(result_uris, *, relevance=None) -> list[Citation]

    Citation  (dataclass with uri, title, doc_type, partition, commit_sha,
               git_path, git_repo, extractor, excerpt, relevance, effective_date)

All classes are importable without boto3 or botocore.
"""

from graphrag.provenance._emitter import ProvenanceEmitter
from graphrag.provenance._resolver import CitationResolver
from graphrag.provenance._types import Citation

__all__ = [
    "Citation",
    "CitationResolver",
    "ProvenanceEmitter",
]
