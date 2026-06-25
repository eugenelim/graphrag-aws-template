"""T7 — CLI verbs: vector-ingest, vector-query, vector-eval (AC5, AC10).

Offline paths use the deterministic non-semantic embedder + the in-memory store, so
they run with no AWS creds; vector-eval scores the curated set over frozen real vectors.

# STUB: AC5
# STUB: AC10
"""

from __future__ import annotations

from pathlib import Path

import pytest

from graphrag.cli import main

GRAPH_CORPUS = Path(__file__).parent / "fixtures" / "corpus"
VECTOR_FIXT = Path(__file__).parent / "fixtures" / "vector"


def _corpus_args(root: Path) -> list[str]:
    return ["--community", str(root / "community"), "--enhancements", str(root / "enhancements")]


def test_vector_ingest_prints_counts_and_dims(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["vector-ingest", *_corpus_args(GRAPH_CORPUS)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "== vector-ingest ==" in out
    assert "embedding:" in out and "dim=256" in out
    assert "chunks:" in out and "by source:" in out


def test_vector_query_prints_trace_with_provenance(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(
        ["vector-query", *_corpus_args(GRAPH_CORPUS), "--q", "in-place pod resize", "--k", "3"]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "== vector-query ==" in out
    assert out.index("query: in-place pod resize") < out.index("embedding:")
    assert "1. score=" in out
    assert "entities:" in out  # provenance/entity line present


def test_vector_eval_passes_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(
        [
            "vector-eval",
            *_corpus_args(VECTOR_FIXT / "corpus"),
            "--query-set",
            str(VECTOR_FIXT / "query_set.yaml"),
            "--frozen",
            str(VECTOR_FIXT / "frozen_embeddings.json"),
        ]
    )
    out = capsys.readouterr().out
    assert "semantic hit@5: 1.000" in out
    assert out.strip().endswith("PASS")
    assert rc == 0  # PASS -> exit 0


def test_vector_eval_exits_nonzero_on_fail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Deterministic FAIL: take the real query set but relabel the known entity-led
    # miss m1 as a *semantic* query (expect_miss: false). It still misses against the
    # real frozen vectors, so semantic hit@5 < 1.0 -> FAIL -> non-zero exit.
    import yaml

    data = yaml.safe_load((VECTOR_FIXT / "query_set.yaml").read_text(encoding="utf-8"))
    for row in data["queries"]:
        if row["id"] == "m1":
            row["class"], row["expect_miss"] = "semantic", False
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(data), encoding="utf-8")

    rc = main(
        [
            "vector-eval",
            *_corpus_args(VECTOR_FIXT / "corpus"),
            "--query-set",
            str(bad),
            "--frozen",
            str(VECTOR_FIXT / "frozen_embeddings.json"),
        ]
    )
    assert capsys.readouterr().out.strip().endswith("FAIL")
    assert rc == 1  # FAIL -> non-zero exit (CI gate)


# --- slice 4: vector-query --persona (AC6) --------------------------------------------


def _vquery(extra: list[str]) -> list[str]:
    return [
        "vector-query",
        *_corpus_args(GRAPH_CORPUS),
        "--q",
        "in-place pod resize",
        "--k",
        "5",
        *extra,
    ]


def test_vector_query_persona_filters_restricted_chunk(capsys: pytest.CaptureFixture[str]) -> None:
    # KEP-1287's prose chunk is restricted; a public-reader must not see it surface.
    rc = main(_vquery(["--persona", "public-reader"]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "persona: public-reader" in out
    assert "not real authz" in out
    assert "1287" not in out  # the restricted KEP-1287 chunk is filtered out of k-NN


def test_vector_query_no_persona_unfiltered(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(_vquery([]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "persona:" not in out  # byte-identical to pre-slice-4 output


def test_vector_query_unknown_persona_exits_nonzero() -> None:
    import pytest as _pytest

    with _pytest.raises(SystemExit):
        main(_vquery(["--persona", "root"]))
