"""T2 — parsing + source loading, including the YAML-safety boundary.

# STUB: AC1
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from graphrag.parse import parse_markdown, safe_load_str
from graphrag.sources import (
    COMMUNITY,
    ENHANCEMENTS,
    load_corpus,
    load_enhancements,
)


def test_loads_both_sources_with_provenance(community_root: Path, enhancements_root: Path) -> None:
    docs = load_corpus(community_root, enhancements_root)
    kinds = {d.kind for d in docs}
    assert {"sigs_index", "sig_readme", "kep_yaml", "kep_readme"} <= kinds
    # Provenance: every doc is tagged with one of the two sources.
    assert {d.source for d in docs} == {COMMUNITY, ENHANCEMENTS}

    sigs_index = next(d for d in docs if d.kind == "sigs_index")
    slugs = {s["dir"] for s in sigs_index.payload["sigs"]}
    assert slugs == {"sig-network", "sig-node"}


def test_legacy_kep_without_kep_yaml_still_loads(enhancements_root: Path) -> None:
    docs = load_enhancements(enhancements_root)
    legacy = [d for d in docs if d.kind == "kep_readme" and not d.payload["has_kep_yaml"]]
    assert len(legacy) == 1
    assert legacy[0].payload["owning_sig_dir"] == "sig-node"
    assert legacy[0].payload["dir_number"] == "0009"


def test_kep_yaml_keeps_owning_sig_and_number(enhancements_root: Path) -> None:
    docs = load_enhancements(enhancements_root)
    kep2086 = next(d for d in docs if d.kind == "kep_yaml" and d.payload.get("kep-number") == 2086)
    assert kep2086.payload["owning-sig"] == "sig-network"
    assert kep2086.payload["owning_sig_dir"] == "sig-network"


def test_markdown_front_matter_and_headings() -> None:
    pm = parse_markdown("---\ntitle: X\nsig: sig-node\n---\n\n# Head\n\nbody\n## Sub")
    assert pm.front_matter == {"title": "X", "sig": "sig-node"}
    assert pm.headings == ["# Head", "## Sub"]
    assert "body" in pm.body


def test_broken_front_matter_is_skipped_not_raised(
    fixtures_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    text = (fixtures_dir / "broken_frontmatter.md").read_text()
    with caplog.at_level(logging.WARNING, logger="graphrag.parse"):
        pm = parse_markdown(text, source_hint="broken_frontmatter.md")
    assert pm.front_matter == {}  # dropped, not crashed
    assert any("malformed front matter" in r.message for r in caplog.records)


def test_safe_load_refuses_python_object_tag(fixtures_dir: Path) -> None:
    # The security boundary (AC1 / CWE-502): a !!python/object tag must NOT be
    # constructed. safe_load raises a ConstructorError rather than running code.
    import yaml

    text = (fixtures_dir / "unsafe.yaml").read_text()
    with pytest.raises(yaml.YAMLError):
        safe_load_str(text)
