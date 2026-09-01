from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from costgov.acceptance_contracts import (
    ACCEPTANCE_OUTCOME_SCHEMA_VERSION,
    ACCEPTANCE_RULE_SCHEMA_VERSION,
    AcceptanceDecision,
    AcceptanceOutcomeStore,
    AcceptanceRule,
    AcceptanceRuleStore,
    ReviewEvidence,
    ReviewMethod,
    evaluate_acceptance,
)

ROOT = Path(__file__).resolve().parents[1]


def _rule() -> AcceptanceRule:
    return AcceptanceRule(
        schema_version=ACCEPTANCE_RULE_SCHEMA_VERSION,
        rule_id="hard-segment-acceptance",
        version="rules.v1",
        segment_id="hard",
        segment_version="segment.v1",
        evaluator_id="token-coverage-evaluator",
        evaluator_version="evaluator.v1",
        evaluator_content_hash="a" * 64,
        minimum_score=0.8,
        created_at="2026-08-31T18:00:00+00:00",
    )


def _automated(score: float) -> ReviewEvidence:
    return ReviewEvidence(
        method=ReviewMethod.AUTOMATED,
        reviewer_id="token-coverage-evaluator",
        evidence_id="evaluation-task-1",
        evidence_version="evaluator.v1",
        evidence_content_hash="b" * 64,
        score=score,
    )


def _evaluate(**changes):
    values = {
        "experiment_id": "rag-policy-comparison",
        "experiment_revision": "2026-08-31.1",
        "arm_id": "governed-candidate",
        "policy_candidate_id": "governed-candidate-configuration",
        "policy_candidate_version": "bench-rag.v1",
        "policy_candidate_content_hash": "d" * 64,
        "task_id": "task-1",
        "trajectory_id": "trajectory-1",
        "segment_id": "hard",
        "segment_version": "segment.v1",
        "evaluated_at": "2026-08-31T18:01:00+00:00",
    }
    values.update(changes)
    return evaluate_acceptance(_rule(), **values)


def test_segment_rule_derives_explicit_outcomes_without_treating_score_as_probability():
    accepted = _evaluate(automated_review=_automated(0.8))
    rejected = _evaluate(automated_review=_automated(0.79))
    inconclusive = _evaluate()

    assert accepted.decision is AcceptanceDecision.ACCEPTED
    assert accepted.reason_code == "automated_score_meets_rule"
    assert rejected.decision is AcceptanceDecision.REJECTED
    assert rejected.reason_code == "automated_score_below_rule"
    assert inconclusive.decision is AcceptanceDecision.INCONCLUSIVE
    assert inconclusive.reason_code == "missing_evaluation_evidence"
    assert accepted.reviews[0].score == 0.8


def test_human_review_is_separate_and_can_override_automated_evidence():
    human = ReviewEvidence(
        method=ReviewMethod.HUMAN,
        reviewer_id="reviewer-42",
        evidence_id="human-review-task-1",
        evidence_version="review-form.v1",
        evidence_content_hash="c" * 64,
        decision=AcceptanceDecision.REJECTED,
    )

    outcome = _evaluate(automated_review=_automated(1.0), human_review=human)

    assert outcome.decision is AcceptanceDecision.REJECTED
    assert outcome.reason_code == "human_rejected"
    assert [review.method for review in outcome.reviews] == [
        ReviewMethod.AUTOMATED,
        ReviewMethod.HUMAN,
    ]


def test_acceptance_rule_and_outcome_stores_are_append_only_and_verify_integrity(
    tmp_path,
):
    rule_store = AcceptanceRuleStore(tmp_path / "rules")
    outcome_store = AcceptanceOutcomeStore(tmp_path / "outcomes")
    rule = _rule()
    outcome = _evaluate(automated_review=_automated(1.0))

    rule_record = rule_store.append(rule)
    outcome_record = outcome_store.append(outcome)

    assert rule_store.get(rule.rule_id, rule.version) == rule_record
    assert outcome_store.get(outcome.outcome_id) == outcome_record
    with pytest.raises(FileExistsError):
        rule_store.append(rule)
    with pytest.raises(FileExistsError):
        outcome_store.append(outcome)

    outcome_path = next((tmp_path / "outcomes").glob("*.json"))
    payload = json.loads(outcome_path.read_text(encoding="utf-8"))
    payload["outcome"]["decision"] = "rejected"
    outcome_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        outcome_store.get(outcome.outcome_id)


def test_acceptance_contract_schemas_match_runtime_versions():
    rule_schema = json.loads(
        (ROOT / "data/contracts/acceptance-rule.v1.schema.json").read_text()
    )
    outcome_schema = json.loads(
        (ROOT / "data/contracts/acceptance-outcome.v1.schema.json").read_text()
    )

    assert rule_schema["properties"]["schema_version"]["const"] == (
        ACCEPTANCE_RULE_SCHEMA_VERSION
    )
    assert outcome_schema["properties"]["schema_version"]["const"] == (
        ACCEPTANCE_OUTCOME_SCHEMA_VERSION
    )
    assert set(outcome_schema["properties"]["decision"]["enum"]) == {
        "accepted",
        "rejected",
        "inconclusive",
    }


def test_rule_rejects_invalid_threshold_and_segment_mismatch():
    with pytest.raises(ValueError, match="minimum_score"):
        replace(_rule(), minimum_score=float("nan"))
    with pytest.raises(ValueError, match="does not match"):
        _evaluate(segment_id="easy")
