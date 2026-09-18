from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from costgov.route_learning import (
    EvidenceSufficiencyGate,
    LearningDomain,
    LearningObservation,
    RouteLearningStore,
    build_route_learning_receipt,
    link_later_forecast,
)

ROOT = Path(__file__).resolve().parents[1]
REFERENCES = {
    "reconciliation_reference": {
        "id": "reconciliation-1",
        "revision": "v1",
        "content_hash": "a" * 64,
    },
    "prior_calibration_reference": {
        "id": "calibration-1",
        "revision": "v1",
        "content_hash": "b" * 64,
    },
}


def _observations(
    metric: str,
    count: int,
    *,
    groups: int,
    acceptance: bool = False,
) -> list[LearningObservation]:
    rows = []
    for index in range(count):
        decision = "accepted" if index % 2 == 0 else "rejected"
        value = (
            (1.0 if decision == "accepted" else 0.0)
            if acceptance
            else (index + 1) / count
            if metric == "purchase_utilization"
            else float(index + 1)
        )
        rows.append(
            LearningObservation(
                observation_id=f"observation-{metric}-{index}",
                evidence_content_hash=f"{index + 1:x}" * 64,
                independent_group=f"group-{index % groups}",
                value=value,
                metric=metric,
                segment_id="hard" if acceptance else None,
                acceptance_decision=decision if acceptance else None,
            )
        )
    return rows


def _build(
    domain: LearningDomain,
    route: str,
    metric: str,
    *,
    count: int = 4,
    groups: int = 2,
    acceptance: bool = False,
):
    return build_route_learning_receipt(
        domain=domain,
        route_id=route,
        observations=_observations(
            metric, count, groups=groups, acceptance=acceptance
        ),
        gate=EvidenceSufficiencyGate(
            minimum_samples=4,
            minimum_independent_groups=2,
            maximum_group_fraction=0.5,
        ),
        proposed_calibration_revision="v2",
        created_at="2026-09-04T12:00:00+00:00",
        **REFERENCES,
    )


@pytest.mark.parametrize(
    ("domain", "route", "metric", "acceptance"),
    [
        (LearningDomain.MODEL_TOKENS, "foundry", "model_tokens_per_task", False),
        (
            LearningDomain.COMMERCIAL_DEMAND,
            "cowork",
            "native_demand_per_task",
            False,
        ),
        (
            LearningDomain.PURCHASE_UTILIZATION,
            "included",
            "purchase_utilization",
            False,
        ),
        (
            LearningDomain.SEGMENT_ACCEPTANCE,
            "foundry",
            "segment_acceptance",
            True,
        ),
    ],
)
def test_learning_domains_update_only_their_explicit_boundary(
    domain: LearningDomain,
    route: str,
    metric: str,
    acceptance: bool,
) -> None:
    result = _build(domain, route, metric, acceptance=acceptance)

    assert result["result"] == "revised"
    assert result["domain"] == domain.value
    assert domain.value not in result["untouched_domains"]
    assert len(result["untouched_domains"]) == 3
    assert result["cross_domain_updates_performed"] == []
    assert result["distribution_semantics"]["calibrated_tail_risk_claim"] is False


def test_sparse_and_correlated_evidence_leave_calibration_unchanged() -> None:
    sparse = _build(
        LearningDomain.MODEL_TOKENS,
        "foundry",
        "model_tokens_per_task",
        count=1,
        groups=1,
    )
    correlated = _build(
        LearningDomain.COMMERCIAL_DEMAND,
        "github_copilot",
        "native_demand_per_task",
        count=4,
        groups=1,
    )

    for result in (sparse, correlated):
        assert result["result"] == "unchanged_insufficient_evidence"
        assert result["effective_calibration"]["revision"] == "v1"
        assert result["effective_calibration"]["content_hash"] == "b" * 64
        assert result["distribution_semantics"]["empirical_calibration_applied"] is False


def test_learning_gate_cannot_be_weakened_to_manufacture_calibration() -> None:
    with pytest.raises(ValueError, match="cannot be weaker"):
        EvidenceSufficiencyGate(
            minimum_samples=1,
            minimum_independent_groups=1,
            maximum_group_fraction=1.0,
        )


def test_later_forecast_must_link_the_effective_revision() -> None:
    revised = _build(
        LearningDomain.MODEL_TOKENS, "foundry", "model_tokens_per_task"
    )
    effective = revised["effective_calibration"]
    link = link_later_forecast(
        learning_receipt=revised,
        forecast_id="forecast-2",
        forecast_revision="v2",
        forecast_content_hash="c" * 64,
        calibration_revision=effective["revision"],
        calibration_content_hash=effective["content_hash"],
        created_at="2026-09-05T12:00:00+00:00",
    )

    assert link["source_verifiable_revision_change"] is True
    assert link["evidence_based_unchanged"] is False
    with pytest.raises(ValueError, match="effective calibration"):
        link_later_forecast(
            learning_receipt=revised,
            forecast_id="forecast-3",
            forecast_revision="v3",
            forecast_content_hash="d" * 64,
            calibration_revision="wrong",
            calibration_content_hash="e" * 64,
            created_at="2026-09-06T12:00:00+00:00",
        )


def test_domain_mismatch_fails_closed() -> None:
    with pytest.raises(ValueError, match="Foundry-backed"):
        _build(
            LearningDomain.MODEL_TOKENS,
            "cowork",
            "model_tokens_per_task",
        )


def test_learning_store_and_schemas_are_immutable_and_aligned(tmp_path: Path) -> None:
    receipt = _build(
        LearningDomain.SEGMENT_ACCEPTANCE,
        "foundry",
        "segment_acceptance",
        acceptance=True,
    )
    effective = receipt["effective_calibration"]
    link = link_later_forecast(
        learning_receipt=receipt,
        forecast_id="forecast",
        forecast_revision="v2",
        forecast_content_hash="c" * 64,
        calibration_revision=effective["revision"],
        calibration_content_hash=effective["content_hash"],
        created_at="2026-09-05T12:00:00+00:00",
    )
    first, created = RouteLearningStore(tmp_path).append(receipt)
    second, created_again = RouteLearningStore(tmp_path).append(receipt)
    learning_schema = json.loads(
        (ROOT / "data/contracts/route-learning-receipt.v1.schema.json").read_text()
    )
    link_schema = json.loads(
        (ROOT / "data/contracts/forecast-learning-link.v1.schema.json").read_text()
    )

    assert first == second
    assert created is True
    assert created_again is False
    Draft202012Validator(learning_schema).validate(receipt)
    Draft202012Validator(link_schema).validate(link)
