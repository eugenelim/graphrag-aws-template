"""T9 — the CLI surface; every verb narrates (AC6/AC10).

# STUB: AC6
# STUB: AC10
"""

from __future__ import annotations

from pathlib import Path

import pytest

from graphrag.cli import _parse_steps, main
from graphrag.model import Direction, EdgeKind

CORPUS = Path(__file__).parent / "fixtures" / "corpus"
SAMPLE = Path(__file__).parent / "fixtures" / "labeled_sample.yaml"


def _corpus_args() -> list[str]:
    return [
        "--community",
        str(CORPUS / "community"),
        "--enhancements",
        str(CORPUS / "enhancements"),
    ]


def test_ingest_narrates_counts_and_merges(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["ingest", *_corpus_args()])
    out = capsys.readouterr().out
    assert rc == 0
    assert "== ingest ==" in out
    assert "parsed docs:" in out
    assert "SIG=" in out and "Person=" in out and "KEP=" in out
    # The cross-source punchline is named, not hidden.
    assert "cross-source resolved nodes" in out
    assert "sig:sig-network (appeared in both sources -> one node)" in out
    assert "person:thockin" in out


def test_graph_query_prints_ordered_trace(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(
        [
            "graph-query",
            *_corpus_args(),
            "--start",
            "@thockin",
            "--start-kind",
            "person",
            "--steps",
            "TECH_LEADS>,OWNS>",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "seeds: person:thockin" in out
    assert "hop 1: TECH_LEADS OUT" in out
    assert "hop 2: OWNS OUT" in out
    assert "kep-2086" in out and "kep-1880" in out
    assert "kep-1287" not in out  # correctly scoped out
    assert out.index("hop 1") < out.index("hop 2") < out.index("result:")


def test_resolve_eval_reports_metrics_and_passes(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["resolve-eval", "--sample", str(SAMPLE)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "precision:" in out and "recall:" in out
    assert "PASS" in out


def test_parse_steps_directions() -> None:
    assert _parse_steps("TECH_LEADS>,OWNS>") == [
        (EdgeKind.TECH_LEADS, Direction.OUT),
        (EdgeKind.OWNS, Direction.OUT),
    ]
    assert _parse_steps("<OWNS") == [(EdgeKind.OWNS, Direction.IN)]
    with pytest.raises(ValueError, match="must end with"):
        _parse_steps("OWNS")
