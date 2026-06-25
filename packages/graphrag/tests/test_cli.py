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


def test_rebuild_narrates_delta_report_and_writes_manifest(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    out_path = tmp_path / "manifest.json"
    rc = main(["rebuild", *_corpus_args(), "--manifest-out", str(out_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "== delta re-ingest ==" in out
    assert "nodes:" in out and "chunks:" in out
    assert out_path.is_file() and '"docs"' in out_path.read_text(encoding="utf-8")


def test_delta_without_prev_manifest_falls_back_to_full(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main(["delta", *_corpus_args()])  # no --prev-manifest -> full ingest fallback (AC8b)
    out = capsys.readouterr().out
    assert rc == 0
    assert "(full ingest — no prior manifest)" in out


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


# --- slice 4: graph-query --persona (AC6) ---------------------------------------------


def _graph_query(persona_args: list[str]) -> list[str]:
    return [
        "graph-query",
        *_corpus_args(),
        "--start",
        "@thockin",
        "--start-kind",
        "person",
        "--steps",
        "TECH_LEADS>,OWNS>",
        *persona_args,
    ]


def test_graph_query_persona_public_reader_filters_internal_neighbor(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # @thockin -TECH_LEADS-> sig-network -OWNS-> {kep-2086 (public), kep-1880 (internal)}.
    rc = main(_graph_query(["--persona", "public-reader"]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "persona: public-reader" in out
    assert "not real authz" in out
    assert "seeds: person:thockin" in out  # the explicit seed is shown as-is
    assert "kep-2086" in out  # public neighbor kept
    assert "kep-1880" not in out  # internal neighbor filtered DURING traversal


def test_graph_query_persona_member_sees_internal(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(_graph_query(["--persona", "member"]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "kep-1880" in out and "kep-2086" in out  # member clearance sees the internal KEP


def test_graph_query_no_persona_is_unfiltered(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(_graph_query([]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "persona:" not in out  # no persona line (byte-identical to pre-slice-4)
    assert "kep-1880" in out and "kep-2086" in out


def test_graph_query_unknown_persona_exits_nonzero() -> None:
    with pytest.raises(SystemExit):
        main(_graph_query(["--persona", "root"]))


def test_graph_query_explicit_restricted_seed_shown_hops_filtered(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Documented `traverse` behavior: an explicit user-named seed is shown AS-IS (the user
    # typed it), even when restricted — but the clearance still filters the hops. Pins the
    # seed-vs-hop asymmetry so a future "helpfully filter the seed" refactor goes red.
    def _run(persona: str) -> str:
        rc = main(
            [
                "graph-query",
                *_corpus_args(),
                "--start",
                "kep-1287",  # restricted
                "--start-kind",
                "kep",
                "--steps",
                "<OWNS",  # IN: the owning SIG
                "--persona",
                persona,
            ]
        )
        assert rc == 0
        return capsys.readouterr().out

    reader = _run("public-reader")
    # The explicit restricted seed is shown as-is (not silently dropped)...
    assert "seeds: kep-1287" in reader
    # ...but the OWNS edge into the restricted KEP composes to `restricted`, so a
    # public-reader can't traverse it: the hop reaches nothing (the edge filter at work).
    assert "result: (none)" in reader
    assert "sig:sig-node" not in reader

    # A maintainer can traverse the restricted edge and reach the owning SIG.
    maint = _run("maintainer")
    assert "seeds: kep-1287" in maint
    assert "sig:sig-node" in maint
