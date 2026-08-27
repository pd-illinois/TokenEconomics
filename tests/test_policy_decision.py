from __future__ import annotations

from dataclasses import dataclass

import pytest

from costgov.contracts import ForecastReceipt, SegmentForecast
from costgov.decision import react
from costgov.evaluator import EvalReport
from costgov.policy import PolicyCandidate, select_policy


def _receipt():
    forecast = SegmentForecast(
        prediction_id=1, segment="easy", model="gpt-4.1", provider="openai",
        archetype="single", prediction_method="tier1_heuristic",
        expected_tokens=1000, p5_tokens=700, p50_tokens=1000, p95_tokens=1500,
        expected_cost_usd=1.0, cost_range_low_usd=0.7,
        cost_range_high_usd=1.5, cost_modeled_high_usd=2.0,
        bound_method="heuristic_multiplier", bound_samples=1, bound_seed=None,
        pricing_verified=True, pricing_timestamp=None,
    )
    return ForecastReceipt("run", "w1", "g1", "completed_task", (forecast,))


def test_policy_selector_chooses_cheapest_quality_eligible_candidate():
    candidates = (
        PolicyCandidate("cost-v1", "cost", 0.7, 0.3),
        PolicyCandidate("balanced-v1", "balanced", 0.9, 0.6),
        PolicyCandidate("quality-v1", "quality", 1.0, 1.0),
    )

    selection = select_policy(
        _receipt(), candidates, quality_floor=0.8, budget_usd=1.0
    )

    assert selection.candidate.routing_mode == "balanced"
    assert selection.expected_cost_usd == pytest.approx(0.6)


def test_policy_selector_weights_forecast_by_segment_volume():
    candidates = (PolicyCandidate("balanced-v1", "balanced", 0.9, 0.5),)

    selection = select_policy(
        _receipt(), candidates, quality_floor=0.8, budget_usd=5.0,
        segment_volumes={"easy": 8},
    )

    assert selection.expected_cost_usd == pytest.approx(4.0)


@dataclass
class Store:
    data: dict

    def update(self, keys, value, reason):
        node = self.data
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value


def _store(min_samples=3, breaches=2):
    return Store({
        "routing": {"mode": "cost"},
        "semantic_cache": {"score_threshold": 0.83},
        "evaluation": {
            "min_quality": 0.8,
            "min_segment_samples": min_samples,
            "consecutive_breaches": breaches,
        },
    })


def test_decision_requires_sample_count_and_consecutive_breaches():
    store = _store()
    sparse = EvalReport(0.5, 2, {"hard": 0.5}, {"hard": 2})
    assert react(store, sparse, True)[0].startswith("HOLD")
    assert store.data["routing"]["mode"] == "cost"

    breach = EvalReport(0.5, 3, {"hard": 0.5}, {"hard": 3})
    assert "1/2" in react(store, breach, True)[0]
    assert store.data["routing"]["mode"] == "cost"
    assert react(store, breach, True)[0].startswith("REVERT")
    assert store.data["routing"]["mode"] == "balanced"