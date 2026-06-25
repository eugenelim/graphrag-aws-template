"""Source loading — locate and parse the files of both K8s repos into ParsedDocs.

A ``ParsedDoc`` is a thin, source-tagged envelope around parsed data; ``extract``
turns these into entities/edges. Keeping loading separate from extraction means
the *single parse* (charter pattern 2) is one legible pass, and a second source
plugs in by emitting more ``ParsedDoc``s — the pluggable seam the charter promises.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .parse import ParsedMarkdown, load_yaml, parse_markdown_file

logger = logging.getLogger("graphrag.sources")

COMMUNITY = "community"
ENHANCEMENTS = "enhancements"

# A KEP directory is "<number>-<slug>"; the leading digits are the KEP number.
_KEP_DIR = re.compile(r"^(\d+)-")


@dataclass
class ParsedDoc:
    """One parsed unit of source material, tagged with its origin."""

    source: str  # COMMUNITY | ENHANCEMENTS
    path: str  # path relative to the corpus root, for provenance in the trace
    kind: str  # "sigs_index" | "sig_readme" | "kep_yaml" | "kep_readme"
    payload: dict[str, Any] = field(default_factory=dict)  # parsed YAML is dynamic
    markdown: ParsedMarkdown | None = None

    @property
    def doc_id(self) -> str:
        """The source-qualified stable key (`{source}/{path}`) — the slice-5 manifest key,
        graph node/edge provenance member, and source-qualified chunk identity, all one form."""
        return f"{self.source}/{self.path}"


def load_community(root: Path) -> list[ParsedDoc]:
    """Load ``sigs.yaml`` plus each SIG's ``README.md`` charter."""
    docs: list[ParsedDoc] = []
    sigs_path = root / "sigs.yaml"
    if not sigs_path.is_file():
        raise FileNotFoundError(f"community source missing sigs.yaml at {sigs_path}")
    index = load_yaml(sigs_path)
    docs.append(ParsedDoc(COMMUNITY, "sigs.yaml", "sigs_index", payload=index))

    for sig in index.get("sigs", []) or []:
        if not isinstance(sig, dict):
            continue
        slug = sig.get("dir")
        if not isinstance(slug, str):
            continue
        readme = root / slug / "README.md"
        if readme.is_file():
            docs.append(
                ParsedDoc(
                    COMMUNITY,
                    f"{slug}/README.md",
                    "sig_readme",
                    payload={"slug": slug},
                    markdown=parse_markdown_file(readme),
                )
            )
    return docs


def load_enhancements(root: Path) -> list[ParsedDoc]:
    """Load every ``keps/<sig>/<number-slug>/`` directory's ``kep.yaml`` + README.

    A KEP dir without a ``kep.yaml`` (a legacy, pre-`kep.yaml` design proposal)
    still yields a ``kep_readme`` doc; its number and owning SIG come from the path.
    """
    docs: list[ParsedDoc] = []
    keps_root = root / "keps"
    if not keps_root.is_dir():
        raise FileNotFoundError(f"enhancements source missing keps/ at {keps_root}")

    for kep_dir in sorted(p for p in keps_root.glob("*/*") if p.is_dir()):
        owning_sig = kep_dir.parent.name
        m = _KEP_DIR.match(kep_dir.name)
        if not m:
            logger.warning("skipping KEP dir without a numeric prefix: %s", kep_dir.name)
            continue
        number = m.group(1)
        rel = f"keps/{owning_sig}/{kep_dir.name}"

        kep_yaml = kep_dir / "kep.yaml"
        if kep_yaml.is_file():
            payload = load_yaml(kep_yaml)
            payload["owning_sig_dir"] = owning_sig
            payload["dir_number"] = number
            docs.append(ParsedDoc(ENHANCEMENTS, f"{rel}/kep.yaml", "kep_yaml", payload=payload))

        readme = kep_dir / "README.md"
        if readme.is_file():
            docs.append(
                ParsedDoc(
                    ENHANCEMENTS,
                    f"{rel}/README.md",
                    "kep_readme",
                    payload={
                        "owning_sig_dir": owning_sig,
                        "dir_number": number,
                        "has_kep_yaml": kep_yaml.is_file(),
                    },
                    markdown=parse_markdown_file(readme),
                )
            )
    return docs


def load_corpus(community_root: Path, enhancements_root: Path) -> list[ParsedDoc]:
    """Load both sources into one ParsedDoc list (the single-parse pass)."""
    return load_community(community_root) + load_enhancements(enhancements_root)
