"""
Evaluation engine (control plane).

Foundry cloud eval stand-in: an LLM-as-judge simulated by scoring an answer against the
golden set's `must_include` reference facts. Runs in two modes (both from file 05/06):

  - offline(): pre-deployment CI gate over the whole golden set (Pattern A)
  - continuous(): scores the *sampled* live stream (Pattern B, a drift detector)

Score in [0,1] = fraction of required facts present. This is the "governance tax":
in production each judged item costs judge tokens, so we only judge a sample.
"""

from __future__ import annotations
import json
from dataclasses import dataclass


@dataclass
class EvalReport:
    mean_score: float
    n: int
    by_difficulty: dict
    by_difficulty_n: dict | None = None


class Evaluator:
    def __init__(self, golden_path: str):
        with open(golden_path, encoding="utf-8") as fh:
            self.golden = json.load(fh)["cases"]
        self._by_q = {c["question"]: c for c in self.golden}

    def _score_answer(self, text: str, must_include) -> float:
        t = text.lower()
        hits = sum(1 for token in must_include if token.lower() in t)
        return hits / len(must_include) if must_include else 1.0

    def _match_case(self, question: str):
        q = question.lower()
        best, best_overlap = None, 0
        for case in self.golden:
            overlap = len(set(q.split()) & set(case["question"].lower().split()))
            if overlap > best_overlap:
                best, best_overlap = case, overlap
        return best

    def offline(self, answer_fn) -> EvalReport:
        """CI gate: run every golden case through answer_fn(question, difficulty)->text."""
        scores, buckets = [], {}
        for case in self.golden:
            text = answer_fn(case["question"], case["difficulty"])
            s = self._score_answer(text, case["must_include"])
            scores.append(s)
            buckets.setdefault(case["difficulty"], []).append(s)
        return self._report(scores, buckets)

    def continuous(self, sampled) -> EvalReport:
        """Score the sampled live stream against matched golden references."""
        scores, buckets = [], {}
        for item in sampled:
            case = self._match_case(item["question"])
            if not case:
                continue
            s = self._score_answer(item["answer_text"], case["must_include"])
            scores.append(s)
            buckets.setdefault(item["difficulty"], []).append(s)
        return self._report(scores, buckets)

    @staticmethod
    def _report(scores, buckets) -> EvalReport:
        mean = sum(scores) / len(scores) if scores else 1.0
        by_diff = {k: round(sum(v) / len(v), 3) for k, v in buckets.items()}
        by_diff_n = {key: len(values) for key, values in buckets.items()}
        return EvalReport(round(mean, 3), len(scores), by_diff, by_diff_n)
