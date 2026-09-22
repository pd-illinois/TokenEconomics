"""Route-aware Govern readiness without execution or policy mutation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Mapping

from .route_capabilities import EnforcementScope, route_capability_profile_for

ROUTE_READINESS_SCHEMA_VERSION = "route-admission-readiness.v1"


class EvidenceState(str, Enum):
    SATISFIED = "satisfied"
    FAILED = "failed"
    MISSING = "missing"
    STALE = "stale"
    INCONCLUSIVE = "inconclusive"


class ReadinessOutcome(str, Enum):
    READY = "ready_for_admission"
    BLOCKED = "blocked"
    INCONCLUSIVE = "inconclusive"
    ADVISORY_ONLY = "advisory_only"


def _required(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value


def _hash(value: object, field: str) -> str:
    text = _required(value, field)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ValueError(f"{field} must be a lowercase SHA-256 hash")
    return text


def _utc(value: object, field: str) -> str:
    text = _required(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be an ISO-8601 UTC timestamp")
    return text


def _canonical(value: object) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class AdmissionEvidence:
    requirement: str
    state: EvidenceState
    authority: str | None
    evidence_revision: str | None
    content_hash: str | None
    reason: str | None

    def __post_init__(self) -> None:
        _required(self.requirement, "requirement")
        if not isinstance(self.state, EvidenceState):
            raise ValueError("evidence state is invalid")
        if self.state is EvidenceState.SATISFIED:
            _required(self.authority, "evidence authority")
            _required(self.evidence_revision, "evidence revision")
            _hash(self.content_hash, "evidence content_hash")
            if self.reason is not None:
                _required(self.reason, "evidence reason")
        elif not self.reason:
            raise ValueError("non-satisfied evidence requires a reason")
        elif self.content_hash is not None:
            _hash(self.content_hash, "evidence content_hash")

    @classmethod
    def from_dict(
        cls,
        requirement: str,
        value: Mapping[str, Any],
    ) -> "AdmissionEvidence":
        try:
            state = EvidenceState(value.get("state"))
        except ValueError as exc:
            raise ValueError(f"invalid evidence state for {requirement}") from exc
        return cls(
            requirement=requirement,
            state=state,
            authority=value.get("authority"),
            evidence_revision=value.get("evidence_revision"),
            content_hash=value.get("content_hash"),
            reason=value.get("reason"),
        )

    @classmethod
    def missing(cls, requirement: str) -> "AdmissionEvidence":
        return cls(
            requirement=requirement,
            state=EvidenceState.MISSING,
            authority=None,
            evidence_revision=None,
            content_hash=None,
            reason="required_evidence_not_supplied",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement": self.requirement,
            "state": self.state.value,
            "authority": self.authority,
            "evidence_revision": self.evidence_revision,
            "content_hash": self.content_hash,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RouteAdmissionReadiness:
    schema_version: str
    route_id: str
    plan_receipt_hash: str
    capability_profile_version: str
    capability_profile_hash: str
    created_at: str
    outcome: ReadinessOutcome
    reason_codes: tuple[str, ...]
    evidence: tuple[AdmissionEvidence, ...]
    enforcement_scopes: tuple[str, ...]
    missing_requirements: tuple[str, ...]
    inconclusive_requirements: tuple[str, ...]
    mutation_performed: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != ROUTE_READINESS_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {ROUTE_READINESS_SCHEMA_VERSION}"
            )
        for field in (
            "route_id",
            "capability_profile_version",
        ):
            _required(getattr(self, field), field)
        _hash(self.plan_receipt_hash, "plan_receipt_hash")
        _hash(self.capability_profile_hash, "capability_profile_hash")
        _utc(self.created_at, "created_at")
        if not isinstance(self.outcome, ReadinessOutcome):
            raise ValueError("readiness outcome is invalid")
        if not self.reason_codes or not self.evidence or not self.enforcement_scopes:
            raise ValueError("readiness evidence and reasons must not be empty")
        if self.mutation_performed:
            raise ValueError("readiness assessment cannot mutate policy")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "route_id": self.route_id,
            "plan_receipt_hash": self.plan_receipt_hash,
            "capability_profile_version": self.capability_profile_version,
            "capability_profile_hash": self.capability_profile_hash,
            "created_at": self.created_at,
            "outcome": self.outcome.value,
            "reason_codes": list(self.reason_codes),
            "evidence": [item.to_dict() for item in self.evidence],
            "enforcement_scopes": list(self.enforcement_scopes),
            "missing_requirements": list(self.missing_requirements),
            "inconclusive_requirements": list(
                self.inconclusive_requirements
            ),
            "mutation_performed": self.mutation_performed,
        }

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict()).encode()).hexdigest()


def _is_advisory_only(enforcement_scopes: tuple[str, ...]) -> bool:
    non_advisory = {
        EnforcementScope.RUNTIME_ENFORCED.value,
        EnforcementScope.CONTROL_PLANE_ENFORCED.value,
        EnforcementScope.PRODUCT_ADMIN_ENFORCED.value,
        EnforcementScope.DESIGN_TIME_ENFORCED.value,
        EnforcementScope.EXTERNAL_REQUIREMENT.value,
    }
    return not non_advisory.intersection(enforcement_scopes)


def assess_route_readiness(
    *,
    route_id: str,
    plan_receipt_hash: str,
    evidence: Mapping[str, AdmissionEvidence | Mapping[str, Any]],
    created_at: str,
    as_of: date | None = None,
) -> RouteAdmissionReadiness:
    """Assess applicable route evidence before decision-grade admission."""
    _hash(plan_receipt_hash, "plan_receipt_hash")
    _utc(created_at, "created_at")
    profile = route_capability_profile_for(route_id, as_of=as_of)
    applicable = tuple(profile["evidence_requirements"])
    unknown = set(evidence) - set(applicable)
    if unknown:
        raise ValueError(
            "evidence contains requirements that do not apply to this route: "
            + ", ".join(sorted(unknown))
        )

    resolved: list[AdmissionEvidence] = []
    for requirement in applicable:
        supplied = evidence.get(requirement)
        if supplied is None:
            resolved.append(AdmissionEvidence.missing(requirement))
        elif isinstance(supplied, AdmissionEvidence):
            if supplied.requirement != requirement:
                raise ValueError("evidence requirement binding mismatch")
            resolved.append(supplied)
        elif isinstance(supplied, Mapping):
            resolved.append(AdmissionEvidence.from_dict(requirement, supplied))
        else:
            raise ValueError(f"evidence for {requirement} must be an object")

    blocked = tuple(
        item.requirement
        for item in resolved
        if item.state in {
            EvidenceState.FAILED,
            EvidenceState.MISSING,
            EvidenceState.STALE,
        }
    )
    inconclusive = tuple(
        item.requirement
        for item in resolved
        if item.state is EvidenceState.INCONCLUSIVE
    )
    scopes = tuple(
        sorted({item["enforcement_scope"] for item in profile["controls"]})
    )
    if blocked:
        outcome = ReadinessOutcome.BLOCKED
        reasons = tuple(
            f"{item.state.value}:{item.requirement}"
            for item in resolved
            if item.requirement in blocked
        )
    elif inconclusive:
        outcome = ReadinessOutcome.INCONCLUSIVE
        reasons = tuple(f"inconclusive:{item}" for item in inconclusive)
    elif _is_advisory_only(scopes):
        outcome = ReadinessOutcome.ADVISORY_ONLY
        reasons = ("runtime_externally_controlled_advisory_only",)
    else:
        outcome = ReadinessOutcome.READY
        reasons = ("all_applicable_route_evidence_satisfied",)

    return RouteAdmissionReadiness(
        schema_version=ROUTE_READINESS_SCHEMA_VERSION,
        route_id=route_id,
        plan_receipt_hash=plan_receipt_hash,
        capability_profile_version=profile["version"],
        capability_profile_hash=profile["content_hash"],
        created_at=created_at,
        outcome=outcome,
        reason_codes=reasons,
        evidence=tuple(resolved),
        enforcement_scopes=scopes,
        missing_requirements=blocked,
        inconclusive_requirements=inconclusive,
    )
