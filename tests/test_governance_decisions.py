from __future__ import annotations

from datetime import datetime, timezone

import pytest

from costgov.decision_state import DecisionStateStore
from costgov.governance_decisions import (
    DECISION_CONSTRAINT_SCHEMA_VERSION,
    CandidateConstraintEvidence,
    ConstraintOutcome,
    GovernanceEvidenceStore,
    GovernOutcome,
    clopper_pearson_upper,
    evaluate_segment_constraint,
    select_candidate,
    wilson_lower,
)

HASH = "a" * 64


def _segment(*, accepted=60, breaches=0, samples=60):
    costs = [0.01] * (samples - breaches) + [0.03] * breaches
    return evaluate_segment_constraint(
        segment_id="hard",
        segment_version="segments.v1",
        acceptance_decisions=["accepted"] * accepted
        + ["rejected"] * (samples - accepted),
        allocatable_costs_usd=costs,
        acceptance_evidence_hashes=[HASH] * samples,
        cost_evidence_hashes=[HASH] * samples,
    )


def _evidence(
    segment,
    candidate_id="candidate-a",
    cost_class="measured",
    window="1",
):
    return CandidateConstraintEvidence(
        schema_version=DECISION_CONSTRAINT_SCHEMA_VERSION,
        constraint_id=f"constraint-{candidate_id}-{segment.sample_count}-{segment.accepted_count}-{segment.budget_breach_count}-{window}",
        experiment_id="rag-policy-comparison",
        experiment_revision="2026-08-31.1",
        arm_id=candidate_id,
        candidate_id=candidate_id,
        candidate_version="bench-rag.v1",
        candidate_content_hash=HASH,
        observation_unit="completed_task",
        evaluated_at="2026-08-31T18:00:00+00:00",
        evidence_classification=cost_class,
        probability_model=(
            "Representative completed tasks are IID Bernoulli trials for "
            "C_task > B; the point estimate is x/n and the decision uses a "
            "one-sided exact 95% Clopper-Pearson upper bound."
        ),
        segments=(segment,),
    )


def test_exact_and_wilson_bounds_match_selected_policy():
    assert clopper_pearson_upper(0, 60, 0.95) == pytest.approx(
        0.0487029133, rel=1e-8
    )
    assert clopper_pearson_upper(1, 60, 0.95) > 0.05
    assert wilson_lower(60, 60, 0.95) > 0.80


def test_segment_constraints_require_sufficient_complete_evidence():
    insufficient = _segment(samples=59, accepted=59)
    assert insufficient.outcome is ConstraintOutcome.INCONCLUSIVE
    assert "insufficient_segment_samples" in insufficient.reason_codes

    missing_cost = evaluate_segment_constraint(
        segment_id="hard",
        segment_version="segments.v1",
        acceptance_decisions=["accepted"] * 60,
        allocatable_costs_usd=[0.01] * 59 + [None],
        acceptance_evidence_hashes=[HASH] * 60,
        cost_evidence_hashes=[HASH] * 60,
    )
    assert missing_cost.outcome is ConstraintOutcome.INCONCLUSIVE
    assert missing_cost.breach_probability_upper_bound is None


def test_zero_breaches_and_high_acceptance_are_eligible():
    result = _segment()
    assert result.outcome is ConstraintOutcome.ELIGIBLE
    assert result.breach_probability_upper_bound <= 0.05
    assert result.acceptance_wilson_lower_bound >= 0.80


def test_one_breach_or_weak_quality_is_ineligible():
    assert _segment(breaches=1).outcome is ConstraintOutcome.INELIGIBLE
    assert _segment(accepted=52).outcome is ConstraintOutcome.INELIGIBLE


def test_govern_selects_lowest_expected_cost_and_never_mutates():
    expensive = _evidence(
        evaluate_segment_constraint(
            segment_id="hard",
            segment_version="segments.v1",
            acceptance_decisions=["accepted"] * 60,
            allocatable_costs_usd=[0.015] * 60,
            acceptance_evidence_hashes=[HASH] * 60,
            cost_evidence_hashes=[HASH] * 60,
        ),
        "expensive",
    )
    cheap = _evidence(_segment(), "cheap")
    decision = select_candidate(
        [expensive, cheap],
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    assert decision.outcome is GovernOutcome.SELECTED
    assert decision.selected_candidate_id == "cheap"
    assert decision.mutation_performed is False


def test_govern_is_inconclusive_when_any_candidate_lacks_evidence():
    decision = select_candidate(
        [_evidence(_segment(samples=59, accepted=59))],
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    assert decision.outcome is GovernOutcome.INCONCLUSIVE
    assert decision.selected_candidate_id is None


def test_governance_evidence_is_append_only_and_verified(tmp_path):
    evidence = _evidence(_segment())
    store = GovernanceEvidenceStore(tmp_path)
    record = store.append(evidence)
    reopened = store.get(evidence.constraint_id)
    assert reopened == record
    with pytest.raises(FileExistsError):
        store.append(evidence)


def test_hysteresis_survives_restart_and_requires_reviewed_recovery(tmp_path):
    first = DecisionStateStore(tmp_path).record(
        _evidence(_segment(breaches=1), window="breach-1")
    )
    assert first[0]["status"] == "breach_observed"
    second = DecisionStateStore(tmp_path).record(
        _evidence(_segment(breaches=1), window="breach-2")
    )
    assert second[0]["status"] == "revert_required"

    third = DecisionStateStore(tmp_path).record(
        _evidence(_segment(), window="recovery-1")
    )
    assert third[0]["status"] == "recovery_observed"
    fourth = DecisionStateStore(tmp_path).record(
        _evidence(_segment(), window="recovery-2")
    )
    assert fourth[0]["status"] == "recovery_review_required"


def test_hysteresis_does_not_count_the_same_window_twice(tmp_path):
    failing = _evidence(_segment(breaches=1), window="breach-1")
    store = DecisionStateStore(tmp_path)
    assert store.record(failing)[0]["status"] == "breach_observed"
    assert store.record(failing) == []
    assert store.get("candidate-a", "bench-rag.v1", "hard")[
        "consecutive_breaches"
    ] == 1
