from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from costgov.observe_economics import (
    OBSERVE_ECONOMICS_SCHEMA_VERSION,
    build_observe_economics,
)

ROOT = Path(__file__).resolve().parents[1]


def _trajectory(task_id: str, segment: str, status: str = "completed"):
    return SimpleNamespace(
        run_id="run-1",
        status=status,
        task=SimpleNamespace(
            task_id=task_id,
            report_id="report-1",
            segment=SimpleNamespace(segment_id=segment, version="segment.v1"),
        ),
    )


def _outcome(task_id: str, segment: str, decision: str, score: float | None):
    reviews = (
        SimpleNamespace(method="automated_evaluator", score=score),
    ) if score is not None else ()
    return SimpleNamespace(
        task_id=task_id,
        segment_id=segment,
        decision=decision,
        reviews=reviews,
    )


def _entry(
    task_id: str,
    segment: str,
    *,
    family: str,
    meter_id: str,
    unit: str,
    currency: str,
    quantity: float | None,
    coverage: str,
    cost: float | None,
    evidence: str = "modeled",
    disposition: str = "pay_as_you_go",
    reason: str | None = None,
):
    return SimpleNamespace(
        task_id=task_id,
        segment_id=segment,
        meter_family=family,
        meter_id=meter_id,
        native_unit=unit,
        native_currency=currency,
        quantity=quantity,
        cost_coverage=coverage,
        allocated_cost_usd=cost,
        evidence_status=evidence,
        entitlement_disposition=disposition,
        purchase_source="test-source",
        unavailable_reason=reason,
        product="foundry" if family != "native_credit" else "copilot_studio",
        environment="simulation",
        meter_stack_id=(
            "foundry-meter-stack"
            if family != "native_credit"
            else "copilot-studio-meter-stack"
        ),
        meter_stack_version="consumption-models.v1",
        meter_stack_content_hash="a" * 64,
        policy_candidate_id="candidate-1",
        policy_candidate_version="v1",
        policy_candidate_content_hash="b" * 64,
    )


def _projection(*, accepted: bool = True):
    trajectories = (
        _trajectory("task-1", "easy"),
        _trajectory("task-2", "hard"),
        _trajectory("task-3", "hard", status="failed"),
    )
    outcomes = (
        _outcome("task-1", "easy", "accepted" if accepted else "rejected", 0.9),
        _outcome("task-2", "hard", "rejected", 0.6),
        _outcome("task-3", "hard", "inconclusive", None),
    )
    entries = (
        _entry(
            "task-1",
            "easy",
            family="direct_token",
            meter_id="foundry_model",
            unit="model_token",
            currency="USD",
            quantity=100,
            coverage="priced",
            cost=2,
        ),
        _entry(
            "task-2",
            "hard",
            family="native_credit",
            meter_id="copilot_studio_events",
            unit="Microsoft Copilot Credit",
            currency="Microsoft Copilot Credits",
            quantity=5,
            coverage="not_applicable",
            cost=None,
            disposition="included",
        ),
        _entry(
            "task-2",
            "hard",
            family="evaluation",
            meter_id="automated_task_evaluation",
            unit="evaluation",
            currency="not_monetized",
            quantity=1,
            coverage="unpriced",
            cost=None,
        ),
        _entry(
            "task-3",
            "hard",
            family="resource",
            meter_id="foundry_resources",
            unit="resource_unit",
            currency="USD",
            quantity=None,
            coverage="unpriced",
            cost=None,
            evidence="unavailable",
            reason="Resource quantity is unavailable.",
        ),
    )
    return build_observe_economics(
        run_id="run-1",
        report_id="report-1",
        evidence_classification="simulated",
        trajectories=trajectories,
        outcomes=outcomes,
        entries=entries,
    )


def test_observe_exposes_denominators_native_currencies_and_partial_costs():
    projection = _projection()
    overall = projection["overall"]

    assert overall["operational_completion"] == {
        "attempted_tasks": 3,
        "completed_tasks": 2,
        "incomplete_tasks": 1,
        "completion_rate": 2 / 3,
        "denominator": "persisted_trajectory_count",
    }
    assert overall["acceptance"]["accepted"] == 1
    assert overall["acceptance"]["rejected"] == 1
    assert overall["acceptance"]["inconclusive"] == 1
    assert overall["acceptance"]["evaluated_tasks"] == 3
    assert overall["quality"]["scored_tasks"] == 2
    assert overall["quality"]["mean_automated_score"] == 0.75
    assert overall["economics"]["coverage_status"] == "incomplete"
    assert overall["economics"]["priced_allocatable_cost_usd"] == 2
    assert overall["economics"]["priced_cost_per_completed_task_usd"] == 1
    assert overall["economics"]["priced_cost_per_accepted_task_usd"] == 2
    assert {item["native_currency"] for item in overall["native_meters"]} == {
        "USD",
        "Microsoft Copilot Credits",
        "not_monetized",
    }
    assert len(overall["economics"]["uncovered_components"]) == 2
    assert {item["segment_id"] for item in projection["dimensions"]["segments"]} == {
        "easy",
        "hard",
    }
    assert len(projection["projection_hash"]) == 64
    assert projection["projection_hash"] == _projection()["projection_hash"]
    assert projection["evidence_basis"]["integrity_verified"] is False


def test_cost_per_accepted_task_is_unavailable_without_accepted_denominator():
    projection = _projection(accepted=False)

    assert projection["overall"]["acceptance"]["accepted"] == 0
    assert (
        projection["overall"]["economics"][
            "priced_cost_per_accepted_task_usd"
        ]
        is None
    )


def test_observe_schema_declares_versioned_denominator_projection():
    schema = json.loads(
        (
            ROOT
            / "data/contracts/accepted-task-economics.v1.schema.json"
        ).read_text(encoding="utf-8")
    )

    assert schema["properties"]["schema_version"]["const"] == (
        OBSERVE_ECONOMICS_SCHEMA_VERSION
    )
    assert "segments" in schema["properties"]["dimensions"]["properties"]
    assert "projection_hash" in schema["required"]
