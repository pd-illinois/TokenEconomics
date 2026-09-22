from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from costgov.acceptance_contracts import AcceptanceDecision
from costgov.route_acceptance import (
    ACCEPTANCE_PACK_SCHEMA_VERSION,
    ROUTE_ACCEPTANCE_OUTCOME_SCHEMA_VERSION,
    AcceptanceAssertion,
    AcceptancePackStore,
    AssertionMethod,
    EvidenceAvailability,
    OperationalOutcome,
    ProvenanceEvidence,
    ProvenanceKind,
    QualityScore,
    RouteAcceptanceStore,
    SampleEvidence,
    SegmentAcceptanceRequirement,
    WorkloadAcceptancePack,
    evaluate_segment_acceptance_floors,
    record_route_acceptance,
)
from costgov.route_usage import ROUTE_LEDGER_LEGS

ROOT = Path(__file__).resolve().parents[1]
HASH = "a" * 64


def _pack() -> WorkloadAcceptancePack:
    return WorkloadAcceptancePack(
        schema_version=ACCEPTANCE_PACK_SCHEMA_VERSION,
        pack_id="calendar-count-acceptance",
        version="pack.v1",
        workload_id="calendar-count",
        workload_version="workload.v1",
        route_ids=tuple(ROUTE_LEDGER_LEGS),
        requirements=(
            SegmentAcceptanceRequirement(
                segment_id="hard",
                segment_version="segment.v1",
                minimum_segment_samples=60,
                minimum_acceptance_rate="0.80",
                minimum_task_assertions=1,
                required_provenance=(
                    ProvenanceKind.TRAJECTORY,
                    ProvenanceKind.OUTPUT,
                    ProvenanceKind.GROUND_TRUTH,
                ),
            ),
        ),
        created_at="2026-09-02T16:00:00+00:00",
        source_revision="calendar-acceptance.v1",
        source_content_hash=HASH,
    )


def _sample(count: int = 60) -> SampleEvidence:
    return SampleEvidence(
        sample_id="sample-task-1",
        sample_set_id="representative-set",
        sample_set_revision="samples.v1",
        sample_set_content_hash="b" * 64,
        segment_completed_samples=count,
        representative=True,
    )


def _provenance(
    unavailable: ProvenanceKind | None = None,
) -> tuple[ProvenanceEvidence, ...]:
    return tuple(
        ProvenanceEvidence(
            kind=kind,
            evidence_id=f"{kind.value}-1",
            revision=f"{kind.value}.v1",
            content_hash=None if kind is unavailable else "c" * 64,
            availability=(
                EvidenceAvailability.UNAVAILABLE
                if kind is unavailable
                else EvidenceAvailability.AVAILABLE
            ),
            reason=(
                "The source did not expose this evidence."
                if kind is unavailable
                else None
            ),
        )
        for kind in (
            ProvenanceKind.TRAJECTORY,
            ProvenanceKind.OUTPUT,
            ProvenanceKind.GROUND_TRUTH,
        )
    )


def _score(value: str = "1.0") -> QualityScore:
    return QualityScore(
        metric_id="quality",
        evaluator_revision="evaluator.v1",
        evidence_content_hash="d" * 64,
        score=value,
    )


def _assertion(
    decision: AcceptanceDecision = AcceptanceDecision.ACCEPTED,
    *,
    evidence_id: str = "assertion-1",
    method: AssertionMethod = AssertionMethod.AUTOMATED,
) -> AcceptanceAssertion:
    return AcceptanceAssertion(
        method=method,
        reviewer_id="calendar-count-evaluator",
        evidence_id=evidence_id,
        evidence_revision="assertion.v1",
        evidence_content_hash="e" * 64,
        decision=decision,
        reason="Explicit comparison with versioned ground truth.",
    )


def _outcome(route_id: str = "foundry", **changes):
    values = {
        "route_id": route_id,
        "task_id": "task-1",
        "trajectory_id": "trajectory-1",
        "segment_id": "hard",
        "segment_version": "segment.v1",
        "operational_outcome": OperationalOutcome.COMPLETED,
        "sample": _sample(),
        "provenance": _provenance(),
        "quality_scores": (_score(),),
        "assertions": (_assertion(),),
        "evaluated_at": "2026-09-02T16:01:00+00:00",
    }
    values.update(changes)
    return record_route_acceptance(_pack(), **values)


@pytest.mark.parametrize("route_id", tuple(ROUTE_LEDGER_LEGS))
def test_all_nine_routes_use_one_workload_pack_and_explicit_outcome(
    route_id: str,
) -> None:
    outcome = _outcome(route_id)

    assert outcome.route_id == route_id
    assert outcome.workload_id == _pack().workload_id
    assert outcome.acceptance_decision is AcceptanceDecision.ACCEPTED
    assert outcome.reason_code == "explicit_accepted_assertion"
    assert outcome.sample_sufficient is True
    assert outcome.pack_content_hash == _pack().content_hash
    assert "billing" not in outcome.to_dict()


def test_raw_quality_score_and_product_completion_never_infer_acceptance() -> None:
    outcome = _outcome(assertions=(), quality_scores=(_score("1.0"),))

    assert outcome.operational_outcome is OperationalOutcome.COMPLETED
    assert outcome.quality_scores[0].score == "1.0"
    assert outcome.acceptance_decision is AcceptanceDecision.INCONCLUSIVE
    assert outcome.reason_code == "missing_explicit_acceptance_assertion"


def test_explicit_assertions_are_separate_from_scores_and_can_reject() -> None:
    rejected = _outcome(
        operational_outcome=OperationalOutcome.NEEDS_HUMAN_REWORK,
        quality_scores=(_score("0.95"),),
        assertions=(
            _assertion(
                AcceptanceDecision.REJECTED,
                method=AssertionMethod.HUMAN,
            ),
        ),
    )

    assert rejected.acceptance_decision is AcceptanceDecision.REJECTED
    assert rejected.quality_scores[0].score == "0.95"
    assert rejected.assertions[0].method is AssertionMethod.HUMAN


def test_ambiguous_or_insufficient_review_evidence_remains_inconclusive() -> None:
    ambiguous = _outcome(
        assertions=(
            _assertion(AcceptanceDecision.ACCEPTED, evidence_id="accepted"),
            _assertion(AcceptanceDecision.REJECTED, evidence_id="rejected"),
        )
    )
    insufficient = _outcome(sample=_sample(59))

    assert ambiguous.acceptance_decision is AcceptanceDecision.INCONCLUSIVE
    assert ambiguous.reason_code == "ambiguous_acceptance_assertions"
    assert insufficient.acceptance_decision is AcceptanceDecision.INCONCLUSIVE
    assert insufficient.reason_code == "insufficient_segment_sample"
    assert insufficient.evidence_availability is EvidenceAvailability.AVAILABLE


def test_inconclusive_assertion_and_operational_conflict_cannot_accept() -> None:
    inconclusive_review = _outcome(
        assertions=(_assertion(AcceptanceDecision.INCONCLUSIVE),)
    )
    blocked_but_claimed_accepted = _outcome(
        operational_outcome=OperationalOutcome.BLOCKED_POLICY,
        assertions=(_assertion(AcceptanceDecision.ACCEPTED),),
    )

    assert inconclusive_review.acceptance_decision is AcceptanceDecision.INCONCLUSIVE
    assert inconclusive_review.reason_code == "ambiguous_acceptance_assertions"
    assert (
        blocked_but_claimed_accepted.acceptance_decision
        is AcceptanceDecision.INCONCLUSIVE
    )
    assert (
        blocked_but_claimed_accepted.reason_code
        == "operational_outcome_conflicts_with_acceptance"
    )


def test_unavailable_provenance_is_truthful_and_cannot_accept() -> None:
    unavailable = _outcome(
        provenance=_provenance(unavailable=ProvenanceKind.GROUND_TRUTH),
        assertions=(_assertion(AcceptanceDecision.ACCEPTED),),
        operational_outcome=OperationalOutcome.UNAVAILABLE,
    )

    assert unavailable.acceptance_decision is AcceptanceDecision.INCONCLUSIVE
    assert unavailable.evidence_availability is EvidenceAvailability.UNAVAILABLE
    assert unavailable.reason_code == "required_provenance_unavailable"
    ground_truth = next(
        item
        for item in unavailable.provenance
        if item.kind is ProvenanceKind.GROUND_TRUTH
    )
    assert ground_truth.content_hash is None
    assert ground_truth.reason


def test_missing_sample_is_persisted_as_unavailable_outcome() -> None:
    outcome = _outcome(sample=None)

    assert outcome.acceptance_decision is AcceptanceDecision.INCONCLUSIVE
    assert outcome.evidence_availability is EvidenceAvailability.UNAVAILABLE
    assert outcome.reason_code == "sample_evidence_unavailable"
    assert outcome.sample is None


def test_segment_acceptance_floor_is_evaluated_from_explicit_task_outcomes() -> None:
    def outcomes(accepted: int, total: int):
        return [
            _outcome(
                task_id=f"task-{index}",
                trajectory_id=f"trajectory-{index}",
                assertions=(
                    _assertion(
                        AcceptanceDecision.ACCEPTED
                        if index < accepted
                        else AcceptanceDecision.REJECTED,
                        evidence_id=f"assertion-{index}",
                    ),
                ),
            )
            for index in range(total)
        ]

    passing = evaluate_segment_acceptance_floors(_pack(), outcomes(48, 60))
    failing = evaluate_segment_acceptance_floors(_pack(), outcomes(47, 60))
    insufficient = evaluate_segment_acceptance_floors(_pack(), outcomes(47, 59))

    assert passing["status"] == "passed"
    assert passing["segments"][0]["observed_acceptance_rate"] == "0.8"
    assert failing["status"] == "failed"
    assert failing["segments"][0]["reason_code"] == "segment_acceptance_floor_breached"
    assert insufficient["status"] == "inconclusive"

    undefined = replace(
        outcomes(48, 60)[0],
        outcome_id="undefined-segment",
        segment_id="undefined",
    )
    with pytest.raises(ValueError, match="not defined"):
        evaluate_segment_acceptance_floors(_pack(), [undefined])


def test_pack_is_workload_and_segment_specific_not_product_defined() -> None:
    pack = _pack()

    assert pack.workload_id == "calendar-count"
    assert len(pack.route_ids) == 9
    assert pack.requirement_for("hard", "segment.v1").minimum_segment_samples == 60
    with pytest.raises(ValueError, match="does not define"):
        pack.requirement_for("easy", "segment.v1")
    with pytest.raises(ValueError, match="does not apply"):
        record_route_acceptance(
            replace(pack, route_ids=("foundry",)),
            route_id="cowork",
            task_id="task-1",
            trajectory_id="trajectory-1",
            segment_id="hard",
            segment_version="segment.v1",
            operational_outcome=OperationalOutcome.COMPLETED,
            sample=_sample(),
            provenance=_provenance(),
            quality_scores=(),
            assertions=(_assertion(),),
            evaluated_at="2026-09-02T16:01:00+00:00",
        )


def test_acceptance_hashes_and_stores_are_replay_safe_and_idempotent(tmp_path) -> None:
    pack = _pack()
    outcome = _outcome()
    pack_store = AcceptancePackStore(tmp_path / "packs")
    outcome_store = RouteAcceptanceStore(tmp_path / "outcomes")

    pack_record = pack_store.record(pack)
    outcome_record = outcome_store.record(outcome)

    assert pack_store.record(pack) == pack_record
    assert outcome_store.record(outcome) == outcome_record
    assert pack_store.get(pack.pack_id, pack.version) == pack_record
    assert outcome_store.get(outcome.outcome_id) == outcome_record
    with pytest.raises(ValueError, match="replay conflicts"):
        pack_store.record(replace(pack, source_content_hash="f" * 64))
    with pytest.raises(ValueError, match="replay conflicts"):
        outcome_store.record(
            replace(outcome, evaluated_at="2026-09-02T16:02:00+00:00")
        )

    path = next((tmp_path / "outcomes").glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["outcome"]["acceptance_decision"] = "rejected"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        outcome_store.get(outcome.outcome_id)


def test_acceptance_schemas_validate_runtime_serialization() -> None:
    pack_schema = json.loads(
        (
            ROOT / "data" / "contracts" / "workload-acceptance-pack.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    outcome_schema = json.loads(
        (
            ROOT / "data" / "contracts" / "route-acceptance-outcome.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(pack_schema)
    Draft202012Validator.check_schema(outcome_schema)
    Draft202012Validator(pack_schema).validate(_pack().to_dict())
    Draft202012Validator(outcome_schema).validate(_outcome().to_dict())

    assert pack_schema["properties"]["schema_version"]["const"] == (
        ACCEPTANCE_PACK_SCHEMA_VERSION
    )
    assert outcome_schema["properties"]["schema_version"]["const"] == (
        ROUTE_ACCEPTANCE_OUTCOME_SCHEMA_VERSION
    )
    assert set(outcome_schema["properties"]["acceptance_decision"]["enum"]) == {
        item.value for item in AcceptanceDecision
    }
