"""Resolver evaluation — the de-risk verdict's "open confirmation", mechanized.

Runs the normalized-match + alias resolver over a hand-labeled sample of *real*
mention forms (handles as they actually appear across the two repos, plus the
prose display-name case) and scores it with the standard pairwise
entity-resolution metric:

- A *pair* of mentions is gold-same when they share a gold canonical id.
- The resolver predicts same when it assigns the two mentions the same node id.
- TP = gold-same ∧ pred-same; FP = pred-same ∧ gold-different; FN = gold-same ∧
  pred-different.

precision = TP/(TP+FP), recall = TP/(TP+FN). The de-risk bar is ≥0.80 on both.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import yaml

from .normalize import kep_id, person_id, sig_id


@dataclass
class EvalResult:
    precision: float
    recall: float
    tp: int
    fp: int
    fn: int
    n_mentions: int

    def passes(self, bar: float = 0.80) -> bool:
        return self.precision >= bar and self.recall >= bar


def _predicted_id(raw: str, kind: str, aliases: dict[str, str]) -> str:
    if kind == "sig":
        return sig_id(raw)
    if kind == "kep":
        return kep_id(raw)
    return person_id(raw, aliases)


def load_labeled_sample(path: Path) -> list[dict[str, str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("mentions", []))


def evaluate(mentions: list[dict[str, str]], aliases: dict[str, str]) -> EvalResult:
    """Score the resolver against the labeled mentions (pairwise P/R)."""
    predicted = [_predicted_id(m["raw"], m.get("kind", "person"), aliases) for m in mentions]
    gold = [m["gold"] for m in mentions]

    tp = fp = fn = 0
    for (gi, pi), (gj, pj) in combinations(zip(gold, predicted, strict=True), 2):
        gold_same = gi == gj
        pred_same = pi == pj
        if gold_same and pred_same:
            tp += 1
        elif pred_same and not gold_same:
            fp += 1
        elif gold_same and not pred_same:
            fn += 1

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return EvalResult(precision, recall, tp, fp, fn, len(mentions))
