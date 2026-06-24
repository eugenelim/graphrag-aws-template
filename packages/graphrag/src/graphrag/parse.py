"""Parsing — Markdown front-matter + YAML, the *single parse* the demo narrates.

All YAML is read with ``yaml.safe_load`` (never ``yaml.load``): the corpus is
untrusted external input parsed inside the Fargate task's IAM role, so a
``!!python/object`` tag in any file must be inert, not constructed (CWE-502). A
malformed front-matter block is skipped with a warning rather than crashing the
run — a real corpus has messy docs (the de-risk verdict flagged pre-`kep.yaml`
KEPs).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("graphrag.parse")

_FRONT_MATTER_FENCE = "---"


@dataclass
class ParsedMarkdown:
    """A parsed Markdown doc: YAML front matter, heading lines, and the body."""

    front_matter: dict[str, object] = field(default_factory=dict)
    headings: list[str] = field(default_factory=list)
    body: str = ""


def load_yaml(path: Path) -> dict[str, Any]:
    """Safely load a YAML file into a dict (``{}`` if empty/non-mapping)."""
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


def safe_load_str(text: str) -> object:
    """``yaml.safe_load`` a string — the one chokepoint, so the safe loader is
    provably the only YAML entry point (the security boundary, AC1)."""
    return yaml.safe_load(text)


def parse_markdown(text: str, *, source_hint: str = "<markdown>") -> ParsedMarkdown:
    """Split optional YAML front matter from the body and collect headings.

    Front matter is a leading ``---``-fenced block. Malformed front-matter YAML is
    dropped (empty dict) with a warning — never raised — so one bad doc cannot
    fail the whole ingest.
    """
    front_matter: dict[str, object] = {}
    body = text

    if text.lstrip().startswith(_FRONT_MATTER_FENCE):
        stripped = text.lstrip()
        rest = stripped[len(_FRONT_MATTER_FENCE) :]
        end = rest.find(f"\n{_FRONT_MATTER_FENCE}")
        if end != -1:
            block = rest[:end]
            body = rest[end + len(_FRONT_MATTER_FENCE) + 1 :].lstrip("\n")
            try:
                loaded = yaml.safe_load(block)
                if isinstance(loaded, dict):
                    front_matter = loaded
                else:
                    logger.warning("%s: front matter is not a mapping; ignored", source_hint)
            except yaml.YAMLError as exc:
                logger.warning("%s: malformed front matter skipped (%s)", source_hint, exc)

    headings = [line.strip() for line in body.splitlines() if line.lstrip().startswith("#")]
    return ParsedMarkdown(front_matter=front_matter, headings=headings, body=body)


def parse_markdown_file(path: Path) -> ParsedMarkdown:
    return parse_markdown(path.read_text(encoding="utf-8"), source_hint=str(path))
