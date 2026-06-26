"""Corpus-wide global search — the query-side map-reduce (global-community-summary slice).

The Global Community Summary pattern (Microsoft GraphRAG *global*): answer a **corpus-wide**
question — one with no seed entity for the seed-and-expand hybrid to expand from — by
**map-reducing over per-community summaries** (produced at ingest by ``community_detect`` and
stored by the ``CommunityStore``).

- **Clearance gate first.** ``community_store.all_communities`` filters by the persona's
  clearance **before** the map step (a summary blends all its members, so it is gated whole by
  its composed tier — fail-closed), so an above-clearance community never reaches the
  synthesizer, the trace, or the citations.
- **Map.** Per community (top-N by size), the synthesizer is asked what that community
  contributes to the question. A community is dropped **only when its map answer, stripped,
  *equals* the ``NOT RELEVANT`` sentinel** (sole-token, not a substring check — a summary that
  merely embeds the literal string still participates: LLM04→LLM01 sentinel-collision
  robustness).
- **Reduce.** The surviving partials are combined into the final grounded answer.
- **Citations** are composed **here** — surviving ``community:<id>`` + the deduped member
  document ``doc_paths`` carried on each ``Community`` — never the synthesizer's chunk-derived
  citations over the synthetic context hit.

Both the map and the reduce place all community-derived content in the synthesizer's **data**
parameters (question + context), which ``BedrockClaudeSynthesizer`` puts in Converse
``messages`` (never ``system``) with a defensive directive + bounded ``maxTokens`` — this
module constructs no system prompt of its own.

PyYAML-free **and networkx-free** (detection is ingest-only), so it bundles in the
``Code.from_asset`` query Lambda.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .chunk import Chunk
from .store.community_base import Community, CommunityStore
from .store.vector_base import VectorHit
from .synthesize import Synthesizer
from .visibility import Clearance

# The map verdict that drops a community — matched by stripped EQUALITY, never substring, so a
# persisted summary that embeds the literal string cannot suppress its own community.
NOT_RELEVANT = "NOT RELEVANT"

# Bound the map fan-out: the top-N largest communities (the store returns them size-sorted).
DEFAULT_TOP_N = 16

_MAP_DIRECTIVE = (
    "\n\nUsing ONLY the community summary provided as context above, state in one or two "
    "sentences what this community contributes to answering the question. If the community "
    f"contributes nothing to the question, reply with exactly: {NOT_RELEVANT}"
)


@dataclass(frozen=True)
class MapVerdict:
    """The map step's per-community result: whether it contributed, and its partial answer."""

    community_id: str
    tier: str
    size: int
    relevant: bool
    partial: str


@dataclass
class GlobalSearchResult:
    """A corpus-wide answer + the legible map-reduce trace."""

    question: str
    communities_considered: list[Community]
    map_verdicts: list[MapVerdict]
    answer: str
    citations: list[str] = field(default_factory=list)
    clearance: Clearance | None = None

    def render(self) -> str:
        lines = [f"Q: {self.question}"]
        persona = self.clearance.persona if self.clearance else "(unrestricted)"
        lines.append(f"clearance: {persona}")
        if not self.communities_considered:
            lines.append("communities considered: (none in scope)")
        else:
            lines.append(f"communities considered ({len(self.communities_considered)}):")
            for community in self.communities_considered:
                lines.append(
                    f"  - {community.id} [{community.tier}] size={community.size} "
                    f"— {community.title}"
                )
        lines.append("map verdicts:")
        for verdict in self.map_verdicts:
            mark = "contributes" if verdict.relevant else "NOT RELEVANT"
            lines.append(f"  - {verdict.community_id}: {mark}")
        lines.append(f"answer: {self.answer}")
        if self.citations:
            lines.append("citations: " + ", ".join(self.citations))
        return "\n".join(lines)


def _summary_hit(community: Community, text: str) -> VectorHit:
    """Wrap a community's summary (or map partial) as a synthesis-context ``VectorHit`` — used
    for the *prompt only*; its provenance is never the source of the result citations."""
    chunk = Chunk(
        id=community.id,
        text=text,
        source="community",
        doc_path=community.id,
        heading=community.title,
    )
    return VectorHit(chunk=chunk, score=1.0)


def global_query(
    question: str,
    *,
    community_store: CommunityStore,
    synthesizer: Synthesizer,
    clearance: Clearance | None = None,
    top_n: int = DEFAULT_TOP_N,
) -> GlobalSearchResult:
    """Map-reduce a corpus-wide answer over the clearance-gated community summaries."""
    allowed = clearance.allowed if clearance is not None else None
    considered = community_store.all_communities(allowed_labels=allowed)[: max(0, top_n)]

    verdicts: list[MapVerdict] = []
    survivors: list[tuple[Community, str]] = []
    for community in considered:
        map_question = (
            f"QUESTION: {question}\n\nCOMMUNITY SUMMARY:\n{community.summary}{_MAP_DIRECTIVE}"
        )
        # The summary rides the synthesizer's DATA params (question + context), never a system
        # prompt this module builds.
        hit = _summary_hit(community, community.summary)
        result = synthesizer.synthesize(map_question, [hit], [])
        partial = result.answer.strip()
        relevant = partial != NOT_RELEVANT  # stripped EQUALITY, not substring
        verdicts.append(
            MapVerdict(
                community_id=community.id,
                tier=community.tier,
                size=community.size,
                relevant=relevant,
                partial=result.answer,
            )
        )
        if relevant:
            survivors.append((community, result.answer))

    if survivors:
        partial_hits = [_summary_hit(community, partial) for community, partial in survivors]
        answer = synthesizer.synthesize(question, partial_hits, []).answer
    else:
        answer = (
            "No community summaries in scope contribute to this corpus-wide question "
            "(none were retrieved, or none cleared the persona's visibility)."
        )

    # Citations composed HERE — surviving community ids + the deduped member documents carried
    # on each community — never the synthesizer's chunk-derived citations (no synthetic
    # provenance). The doc set is a subset of the served (in-clearance) communities' members.
    citations = [f"community:{community.id}" for community, _ in survivors]
    citations += sorted({dp for community, _ in survivors for dp in community.doc_paths})

    return GlobalSearchResult(
        question=question,
        communities_considered=considered,
        map_verdicts=verdicts,
        answer=answer,
        citations=citations,
        clearance=clearance,
    )
