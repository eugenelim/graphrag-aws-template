"""T5 — resolver eval: the de-risk "open confirmation" made mechanical.

# STUB: AC5
"""

from __future__ import annotations

from pathlib import Path

from graphrag.eval import evaluate, load_labeled_sample
from graphrag.resolve import load_aliases


def test_resolver_clears_80pct_precision_and_recall(fixtures_dir: Path) -> None:
    mentions = load_labeled_sample(fixtures_dir / "labeled_sample.yaml")
    result = evaluate(mentions, load_aliases())

    # The predeclared de-risk bar — asserted, not eyeballed.
    assert result.precision >= 0.80, f"precision {result.precision:.3f} below bar"
    assert result.recall >= 0.80, f"recall {result.recall:.3f} below bar"


def test_eval_has_teeth_not_a_trivial_perfect_score(fixtures_dir: Path) -> None:
    # Un-aliased prose names are genuinely missed: the metric measures real
    # behavior, so recall is below 1.0 while still clearing the bar.
    mentions = load_labeled_sample(fixtures_dir / "labeled_sample.yaml")
    result = evaluate(mentions, load_aliases())
    assert result.fn > 0, "expected some false negatives from un-aliased prose names"
    assert result.recall < 1.0
    assert result.precision == 1.0  # normalized match makes no false merges here
