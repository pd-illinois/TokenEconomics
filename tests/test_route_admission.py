from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from costgov.route_admission import (
    ROUTE_READINESS_SCHEMA_VERSION,
    AdmissionEvidence,
    EvidenceState,
    ReadinessOutcome,
    assess_route_readiness,
)
from costgov.route_capabilities import route_capability_profile_for

ROOT = Path(__file__).resolve().parents[1]
AS_OF = date(2026, 9, 2)
CREATED_AT = "2026-09-02T16:00:00+00:00"
HASH = "a" * 64


def _satisfied(requirement: str) -> AdmissionEvidence:
    return AdmissionEvidence(
        requirement=requirement,
        state=EvidenceState.SATISFIED,
        authority="test-authority",
        evidence_revision="test.v1",
        content_hash=HASH,
        reason=None,
    )


def _all_satisfied(route_id: str) -> dict[str, AdmissionEvidence]:
    profile = route_capability_profile_for(route_id, as_of=AS_OF)
    return {
        requirement: _satisfied(requirement)
        for requirement in profile["evidence_requirements"]
    }


def test_commercial_route_can_be_ready_without_model_evidence() -> None:
    readiness = assess_route_readiness(
        route_id="included",
        plan_receipt_hash=HASH,
        evidence=_all_satisfied("included"),
        created_at=CREATED_AT,
        as_of=AS_OF,
    )

    assert readiness.outcome is ReadinessOutcome.READY
    assert "released_model" not in {
        item.requirement for item in readiness.evidence
    }
    assert readiness.mutation_performed is False


def test_foundry_route_requires_its_own_model_and_resource_evidence() -> None:
    readiness = assess_route_readiness(
        route_id="foundry",
        plan_receipt_hash=HASH,
        evidence={},
        created_at=CREATED_AT,
        as_of=AS_OF,
    )

    assert readiness.outcome is ReadinessOutcome.BLOCKED
    assert set(readiness.missing_requirements) == {
        "released_model",
        "verified_pricing",
        "infrastructure_coverage",
        "acceptance_rule",
    }


def test_inconclusive_and_blocked_evidence_remain_distinct() -> None:
    evidence = _all_satisfied("cowork")
    evidence["acceptance_rule"] = AdmissionEvidence(
        requirement="acceptance_rule",
        state=EvidenceState.INCONCLUSIVE,
        authority="acceptance-authority",
        evidence_revision="acceptance.v1",
        content_hash=HASH,
        reason="representative_segment_not_confirmed",
    )
    inconclusive = assess_route_readiness(
        route_id="cowork",
        plan_receipt_hash=HASH,
        evidence=evidence,
        created_at=CREATED_AT,
        as_of=AS_OF,
    )
    evidence.pop("capacity_and_overage")
    blocked = assess_route_readiness(
        route_id="cowork",
        plan_receipt_hash=HASH,
        evidence=evidence,
        created_at=CREATED_AT,
        as_of=AS_OF,
    )

    assert inconclusive.outcome is ReadinessOutcome.INCONCLUSIVE
    assert blocked.outcome is ReadinessOutcome.BLOCKED
    assert "capacity_and_overage" in blocked.missing_requirements


def test_non_applicable_evidence_is_rejected_instead_of_silently_reused() -> None:
    with pytest.raises(ValueError, match="do not apply"):
        assess_route_readiness(
            route_id="included",
            plan_receipt_hash=HASH,
            evidence={"released_model": _satisfied("released_model")},
            created_at=CREATED_AT,
            as_of=AS_OF,
        )


def test_satisfied_evidence_requires_versioned_content() -> None:
    with pytest.raises(ValueError, match="evidence content_hash"):
        AdmissionEvidence(
            requirement="acceptance_rule",
            state=EvidenceState.SATISFIED,
            authority="acceptance-authority",
            evidence_revision="acceptance.v1",
            content_hash=None,
            reason=None,
        )


def test_readiness_hash_changes_when_evidence_changes() -> None:
    evidence = _all_satisfied("included")
    first = assess_route_readiness(
        route_id="included",
        plan_receipt_hash=HASH,
        evidence=evidence,
        created_at=CREATED_AT,
        as_of=AS_OF,
    )
    evidence["acceptance_rule"] = AdmissionEvidence(
        requirement="acceptance_rule",
        state=EvidenceState.SATISFIED,
        authority="test-authority",
        evidence_revision="test.v2",
        content_hash="b" * 64,
        reason=None,
    )
    second = assess_route_readiness(
        route_id="included",
        plan_receipt_hash=HASH,
        evidence=evidence,
        created_at=CREATED_AT,
        as_of=AS_OF,
    )

    assert first.content_hash != second.content_hash


def test_route_readiness_schema_matches_runtime_contract() -> None:
    schema = json.loads(
        (
            ROOT
            / "data"
            / "contracts"
            / "route-admission-readiness.v1.schema.json"
        ).read_text(encoding="utf-8")
    )

    assert schema["properties"]["schema_version"]["const"] == (
        ROUTE_READINESS_SCHEMA_VERSION
    )
    assert set(schema["properties"]["outcome"]["enum"]) == {
        item.value for item in ReadinessOutcome
    }
