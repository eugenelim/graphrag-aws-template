"""Deterministic parameter extraction + validation — the governance boundary (AC3).

The LLM selects *which* template (``select.py``); this module fills the chosen
template's typed slots **deterministically**, so a bound value is never free-form model
text:

- **entity** slots resolve through the slice-3 ``link_question``/``normalize`` functions
  and are **confirmed** against the store — the bound value is always a real graph node id
  (an unconfirmed candidate is dropped and recorded, never expanded);
- **enum** slots are validated against the template's declared ``choices``;
- **int** slots are parsed and bounded by ``min``/``max``.

A missing or invalid **required** slot yields an ``ExtractionFailure`` — the governed
refusal, never an execution with a bad parameter. PyYAML-free (imports ``entity_link`` /
``model`` / ``templates`` only).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from .entity_link import link_question
from .model import EntityKind
from .store.base import GraphStore
from .templates import ParamSpec, Template

# A template's entity-kind ↔ the slice-3 ``link_question`` candidate kind.
_ENTITY_KIND_TO_CANDIDATE: dict[EntityKind, str] = {
    EntityKind.SIG: "sig",
    EntityKind.PERSON: "person",
    EntityKind.KEP: "kep",
}
_INT_RE = re.compile(r"\b(\d+)\b")


@dataclass
class BoundParam:
    """One validated parameter binding, with the provenance of how it was extracted."""

    name: str
    value: object
    via: str


@dataclass
class ParamBinding:
    """A successful extraction: every required slot bound; ``dropped`` records entity
    candidates that matched a slot's kind but failed store confirmation."""

    bound: list[BoundParam] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


@dataclass
class ExtractionFailure:
    """A required slot could not be validated — the governed refusal (no query runs)."""

    reason: str
    dropped: list[str] = field(default_factory=list)


def _extract_entity(
    spec: ParamSpec,
    question: str,
    aliases: Mapping[str, str],
    store: GraphStore,
) -> tuple[BoundParam | None, list[str]]:
    """Bind an entity slot to the first store-confirmed candidate of the matching kind;
    return the binding (or ``None``) and the ids that matched the kind but didn't confirm."""
    if spec.entity_kind is None:  # guaranteed populated for kind == "entity" by ParamSpec
        return None, []
    candidate_kind = _ENTITY_KIND_TO_CANDIDATE[spec.entity_kind]
    dropped: list[str] = []
    for cand in link_question(question, dict(aliases)):
        if cand.kind != candidate_kind:
            continue
        if store.get_node(cand.entity_id) is not None:
            return BoundParam(spec.name, cand.entity_id, f"link:{cand.via}"), dropped
        dropped.append(cand.entity_id)
    return None, dropped


def _extract_enum(spec: ParamSpec, question: str) -> BoundParam | None:
    """Bind an enum slot to the first declared choice that appears in the question."""
    lowered = question.lower()
    for choice in spec.choices or ():
        if re.search(rf"\b{re.escape(choice.lower())}\b", lowered):
            return BoundParam(spec.name, choice, "enum-match")
    return None


def _extract_int(spec: ParamSpec, question: str) -> BoundParam | None:
    """Bind an int slot to the first integer in the question within ``min``/``max``."""
    for match in _INT_RE.finditer(question):
        value = int(match.group(1))
        if spec.min is not None and value < spec.min:
            continue
        if spec.max is not None and value > spec.max:
            continue
        return BoundParam(spec.name, value, "int-parse")
    return None


def extract_params(
    question: str,
    template: Template,
    aliases: Mapping[str, str],
    store: GraphStore,
) -> ParamBinding | ExtractionFailure:
    """Fill ``template``'s declared slots from the question, validating every value.

    Returns a ``ParamBinding`` when every required slot is bound, else an
    ``ExtractionFailure`` naming the first slot that could not be validated.
    """
    bound: list[BoundParam] = []
    dropped: list[str] = []
    for spec in template.params:
        if spec.kind == "entity":
            binding, slot_dropped = _extract_entity(spec, question, aliases, store)
            dropped.extend(slot_dropped)
        elif spec.kind == "enum":
            binding = _extract_enum(spec, question)
        else:  # "int"
            binding = _extract_int(spec, question)

        if binding is None:
            if spec.required:
                return ExtractionFailure(
                    reason=f"no valid {spec.kind} value for parameter {spec.name!r}",
                    dropped=dropped,
                )
            continue
        bound.append(binding)
    return ParamBinding(bound=bound, dropped=dropped)
