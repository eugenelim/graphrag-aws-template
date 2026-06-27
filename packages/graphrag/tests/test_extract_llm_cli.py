"""AC6 — CLI verb ``extract-llm`` (offline default + --bedrock live extractor).

Offline runs are deterministic over the fixture corpus (in-memory graph + non-semantic rule
extractor) and print the ordered per-triple audit trace; ``--bedrock`` selects the live extractor.

# STUB: AC6
"""

from __future__ import annotations

from pathlib import Path

import pytest

from graphrag import cli

CORPUS = Path(__file__).parent / "fixtures" / "corpus"
COMMUNITY = str(CORPUS / "community")
ENHANCEMENTS = str(CORPUS / "enhancements")


def test_extract_llm_offline_prints_per_triple_trace(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["extract-llm", "--community", COMMUNITY, "--enhancements", ENHANCEMENTS])
    assert rc == 0
    out = capsys.readouterr().out
    assert "== extract-llm (offline) ==" in out
    assert "NON-SEMANTIC" in out  # the offline label so a reader is never misled
    # the ordered audit trace: schema shown -> doc/span -> triple -> verdict -> edge.
    assert "EXTRACTION SCHEMA" in out
    assert "COLLABORATES_WITH" in out
    assert "sig-network/README.md" in out
    assert "verdict: accepted" in out
    assert "sig:sig-network -[COLLABORATES_WITH]-> sig:sig-node" in out
    assert "summary: +" in out


def test_extract_llm_bedrock_flag_selects_the_live_extractor() -> None:
    # The flag routes to BedrockTripleExtractor (no live call made here — just the selection).
    import argparse

    from graphrag.extract_llm import BedrockTripleExtractor, RuleTripleExtractor

    offline = cli._triple_extractor(argparse.Namespace(bedrock=False, region="us-east-1"))
    live = cli._triple_extractor(argparse.Namespace(bedrock=True, region="us-east-1"))
    assert isinstance(offline, RuleTripleExtractor)
    assert isinstance(live, BedrockTripleExtractor)
