"""Immutable route-aware execution authorization and authority bindings."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .atomic_publish import publish_immutable

from .route_admission import ReadinessOutcome, RouteAdmissionReadiness
from .route_capabilities import EnforcementScope, route_capability_profile_for

EXECUTION_AUTHORIZATION_SCHEMA_VERSION = "composite-execution-authorization.v1"


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


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _canonical(value: object) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


class ExecutionMode(str, Enum):
    BILLABLE = "billable"
    SIMULATION = "simulation"


class EvidenceLabel(str, Enum):
    MEASURED = "measured"
    SIMULATED = "simulated"
    MODELED = "modeled"
    BLOCKED = "blocked"


class AuthorityState(str, Enum):
    SATISFIED = "satisfied"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class BindingOutcome(str, Enum):
    RUNTIME_BOUND = "runtime_bound"
    CONTROL_PLANE_BOUND = "control_plane_bound"
    PRODUCT_ADMIN_VERIFIED = "product_admin_verified"
    DESIGN_TIME_VERIFIED = "design_time_verified"
    EXTERNAL_REQUIREMENT_VERIFIED = "external_requirement_verified"
    ADVISORY_BOUNDARY = "advisory_boundary"
    CORRELATION_BOUNDARY = "correlation_boundary"
    BLOCKED = "blocked"


class AuthorizationOutcome(str, Enum):
    AUTHORIZED = "authorized"
    EXTERNALLY_AUTHORIZED = "externally_authorized"
    SIMULATION_AUTHORIZED = "simulation_authorized"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class PolicyProvenance:
    policy_id: str
    version: str
    content_hash: str
    source: str
    label: str
    etag: str

    def __post_init__(self) -> None:
        for field in ("policy_id", "version", "source", "label", "etag"):
            _required(getattr(self, field), f"policy {field}")
        _hash(self.content_hash, "policy content_hash")
        if self.source != "azure_app_configuration":
            raise ValueError("runtime TokenGov policy source must be azure_app_configuration")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PolicyProvenance":
        return cls(**value)

    def to_dict(self) -> dict[str, str]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class AuthorityEvidence:
    control_id: str
    authority: str
    state: AuthorityState
    evidence_label: EvidenceLabel
    evidence_id: str
    evidence_revision: str
    content_hash: str
    observed_at: str
    valid_until: str
    reason: str | None = None

    def __post_init__(self) -> None:
        for field in (
            "control_id",
            "authority",
            "evidence_id",
            "evidence_revision",
        ):
            _required(getattr(self, field), f"authority evidence {field}")
        if not isinstance(self.state, AuthorityState):
            raise ValueError("authority evidence state is invalid")
        if not isinstance(self.evidence_label, EvidenceLabel):
            raise ValueError("authority evidence label is invalid")
        _hash(self.content_hash, "authority evidence content_hash")
        _utc(self.observed_at, "authority evidence observed_at")
        _utc(self.valid_until, "authority evidence valid_until")
        if _instant(self.valid_until) < _instant(self.observed_at):
            raise ValueError("authority evidence valid_until precedes observed_at")
        if self.state is not AuthorityState.SATISFIED and not self.reason:
            raise ValueError("non-satisfied authority evidence requires a reason")
        if self.evidence_label is EvidenceLabel.BLOCKED and not self.reason:
            raise ValueError("blocked authority evidence requires a reason")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AuthorityEvidence":
        values = dict(value)
        values["state"] = AuthorityState(values.get("state"))
        values["evidence_label"] = EvidenceLabel(values.get("evidence_label"))
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        result = dict(self.__dict__)
        result["state"] = self.state.value
        result["evidence_label"] = self.evidence_label.value
        return result


@dataclass(frozen=True)
class ControlBinding:
    control_id: str
    control_kind: str
    target_leg: str
    authority: str
    capability: str
    enforcement_scope: EnforcementScope
    outcome: BindingOutcome
    authority_evidence_hash: str | None
    tokengov_enforcement_event: bool
    reason: str

    def __post_init__(self) -> None:
        for field in (
            "control_id",
            "control_kind",
            "target_leg",
            "authority",
            "capability",
            "reason",
        ):
            _required(getattr(self, field), f"control binding {field}")
        if not isinstance(self.enforcement_scope, EnforcementScope):
            raise ValueError("control binding enforcement_scope is invalid")
        if not isinstance(self.outcome, BindingOutcome):
            raise ValueError("control binding outcome is invalid")
        if self.authority_evidence_hash is not None:
            _hash(self.authority_evidence_hash, "authority_evidence_hash")
        if self.tokengov_enforcement_event and not (
            self.authority == "azure_tokengov"
            and self.enforcement_scope is EnforcementScope.RUNTIME_ENFORCED
            and self.outcome is BindingOutcome.RUNTIME_BOUND
        ):
            raise ValueError("TokenGov enforcement events require a bound Azure runtime control")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ControlBinding":
        values = dict(value)
        values["enforcement_scope"] = EnforcementScope(values.get("enforcement_scope"))
        values["outcome"] = BindingOutcome(values.get("outcome"))
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        result = dict(self.__dict__)
        result["enforcement_scope"] = self.enforcement_scope.value
        result["outcome"] = self.outcome.value
        return result


@dataclass(frozen=True)
class CompositeExecutionAuthorization:
    schema_version: str
    authorization_id: str
    execution_mode: ExecutionMode
    outcome: AuthorizationOutcome
    evidence_label: EvidenceLabel
    route_id: str
    capability_profile_version: str
    capability_profile_hash: str
    readiness_hash: str
    govern_decision_id: str
    govern_decision_hash: str
    plan_receipt_id: str
    plan_receipt_hash: str
    candidate_id: str
    candidate_version: str
    candidate_hash: str
    task_id: str
    trajectory_id: str
    segment_id: str
    segment_version: str
    policy: PolicyProvenance | None
    bindings: tuple[ControlBinding, ...]
    authority_evidence: tuple[AuthorityEvidence, ...]
    authorized_legs: tuple[str, ...]
    blocked_reasons: tuple[str, ...]
    preflight_at: str
    billable_execution_permitted: bool

    def __post_init__(self) -> None:
        if self.schema_version != EXECUTION_AUTHORIZATION_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {EXECUTION_AUTHORIZATION_SCHEMA_VERSION}"
            )
        for field in (
            "authorization_id",
            "route_id",
            "capability_profile_version",
            "govern_decision_id",
            "plan_receipt_id",
            "candidate_id",
            "candidate_version",
            "task_id",
            "trajectory_id",
            "segment_id",
            "segment_version",
        ):
            _required(getattr(self, field), field)
        for field in (
            "capability_profile_hash",
            "readiness_hash",
            "govern_decision_hash",
            "plan_receipt_hash",
            "candidate_hash",
        ):
            _hash(getattr(self, field), field)
        if not isinstance(self.execution_mode, ExecutionMode):
            raise ValueError("execution_mode is invalid")
        if not isinstance(self.outcome, AuthorizationOutcome):
            raise ValueError("authorization outcome is invalid")
        if not isinstance(self.evidence_label, EvidenceLabel):
            raise ValueError("authorization evidence_label is invalid")
        if not self.bindings:
            raise ValueError("execution authorization requires control bindings")
        binding_ids = [item.control_id for item in self.bindings]
        if len(binding_ids) != len(set(binding_ids)):
            raise ValueError("execution binding IDs must be unique")
        evidence_ids = [item.control_id for item in self.authority_evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("authority evidence control IDs must be unique")
        _utc(self.preflight_at, "preflight_at")
        expected_billable = (
            self.execution_mode is ExecutionMode.BILLABLE
            and self.outcome
            in {AuthorizationOutcome.AUTHORIZED, AuthorizationOutcome.EXTERNALLY_AUTHORIZED}
        )
        if self.billable_execution_permitted != expected_billable:
            raise ValueError("billable execution permission contradicts authorization")
        if self.outcome is AuthorizationOutcome.BLOCKED and not self.blocked_reasons:
            raise ValueError("blocked authorization requires reasons")
        if self.outcome is not AuthorizationOutcome.BLOCKED and self.blocked_reasons:
            raise ValueError("successful authorization cannot carry blocked reasons")
        permits_route_legs = self.outcome in {
            AuthorizationOutcome.AUTHORIZED,
            AuthorizationOutcome.EXTERNALLY_AUTHORIZED,
        }
        if permits_route_legs and not self.authorized_legs:
            raise ValueError("authorized execution requires at least one authorized leg")
        if not permits_route_legs and self.authorized_legs:
            raise ValueError("non-authorized execution cannot carry authorized legs")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CompositeExecutionAuthorization":
        values = dict(value)
        values["execution_mode"] = ExecutionMode(values.get("execution_mode"))
        values["outcome"] = AuthorizationOutcome(values.get("outcome"))
        values["evidence_label"] = EvidenceLabel(values.get("evidence_label"))
        policy = values.get("policy")
        values["policy"] = PolicyProvenance.from_dict(policy) if policy else None
        values["bindings"] = tuple(
            ControlBinding.from_dict(item) for item in values.get("bindings", ())
        )
        values["authority_evidence"] = tuple(
            AuthorityEvidence.from_dict(item)
            for item in values.get("authority_evidence", ())
        )
        values["authorized_legs"] = tuple(values.get("authorized_legs", ()))
        values["blocked_reasons"] = tuple(values.get("blocked_reasons", ()))
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "authorization_id": self.authorization_id,
            "execution_mode": self.execution_mode.value,
            "outcome": self.outcome.value,
            "evidence_label": self.evidence_label.value,
            "route_id": self.route_id,
            "capability_profile_version": self.capability_profile_version,
            "capability_profile_hash": self.capability_profile_hash,
            "readiness_hash": self.readiness_hash,
            "govern_decision_id": self.govern_decision_id,
            "govern_decision_hash": self.govern_decision_hash,
            "plan_receipt_id": self.plan_receipt_id,
            "plan_receipt_hash": self.plan_receipt_hash,
            "candidate_id": self.candidate_id,
            "candidate_version": self.candidate_version,
            "candidate_hash": self.candidate_hash,
            "task_id": self.task_id,
            "trajectory_id": self.trajectory_id,
            "segment_id": self.segment_id,
            "segment_version": self.segment_version,
            "policy": self.policy.to_dict() if self.policy else None,
            "bindings": [item.to_dict() for item in self.bindings],
            "authority_evidence": [
                item.to_dict() for item in self.authority_evidence
            ],
            "authorized_legs": list(self.authorized_legs),
            "blocked_reasons": list(self.blocked_reasons),
            "preflight_at": self.preflight_at,
            "billable_execution_permitted": self.billable_execution_permitted,
        }

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict()).encode()).hexdigest()


_VERIFIED_OUTCOME = {
    EnforcementScope.RUNTIME_ENFORCED: BindingOutcome.RUNTIME_BOUND,
    EnforcementScope.CONTROL_PLANE_ENFORCED: BindingOutcome.CONTROL_PLANE_BOUND,
    EnforcementScope.PRODUCT_ADMIN_ENFORCED: BindingOutcome.PRODUCT_ADMIN_VERIFIED,
    EnforcementScope.DESIGN_TIME_ENFORCED: BindingOutcome.DESIGN_TIME_VERIFIED,
    EnforcementScope.EXTERNAL_REQUIREMENT: BindingOutcome.EXTERNAL_REQUIREMENT_VERIFIED,
}


def _authorization_id(values: tuple[str, ...]) -> str:
    return "execution-" + hashlib.sha256(":".join(values).encode()).hexdigest()[:32]


def authorize_route_execution(
    *,
    route_id: str,
    readiness: RouteAdmissionReadiness,
    govern_decision_id: str,
    govern_decision_hash: str,
    plan_receipt_id: str,
    plan_receipt_hash: str,
    candidate_id: str,
    candidate_version: str,
    candidate_hash: str,
    task_id: str,
    trajectory_id: str,
    segment_id: str,
    segment_version: str,
    authority_evidence: Mapping[str, AuthorityEvidence],
    policy: PolicyProvenance | None,
    execution_mode: ExecutionMode,
    preflight_at: str,
    as_of: date | None = None,
) -> CompositeExecutionAuthorization:
    """Preflight one route before execution without mutating any authority."""
    _utc(preflight_at, "preflight_at")
    if not isinstance(execution_mode, ExecutionMode):
        raise ValueError("execution_mode is invalid")
    profile = route_capability_profile_for(route_id, as_of=as_of)
    if (
        readiness.route_id != route_id
        or readiness.plan_receipt_hash != plan_receipt_hash
        or readiness.capability_profile_version != profile["version"]
        or readiness.capability_profile_hash != profile["content_hash"]
    ):
        raise ValueError("readiness binding does not match route execution evidence")

    required_ids = {
        item["control_id"]
        for item in profile["controls"]
        if item["enforcement_scope"] != EnforcementScope.ADVISORY.value
    }
    unknown = set(authority_evidence) - required_ids
    if unknown:
        raise ValueError(
            "authority evidence contains non-required controls: "
            + ", ".join(sorted(unknown))
        )
    azure_controls = {
        item["control_id"]
        for item in profile["controls"]
        if item["authority"] == "azure_tokengov"
    }
    if azure_controls and policy is None:
        policy_reason = "missing_exact_azure_policy_provenance"
    elif not azure_controls and policy is not None:
        raise ValueError("external route cannot carry fabricated Azure policy provenance")
    else:
        policy_reason = None

    now = _instant(preflight_at)
    reasons: list[str] = []
    bindings: list[ControlBinding] = []
    for control in profile["controls"]:
        scope = EnforcementScope(control["enforcement_scope"])
        common = {
            "control_id": control["control_id"],
            "control_kind": control["control_kind"],
            "target_leg": control["target_leg"],
            "authority": control["authority"],
            "capability": control["capability"],
            "enforcement_scope": scope,
        }
        if scope is EnforcementScope.ADVISORY:
            bindings.append(
                ControlBinding(
                    **common,
                    outcome=BindingOutcome.ADVISORY_BOUNDARY,
                    authority_evidence_hash=None,
                    tokengov_enforcement_event=False,
                    reason="advisory_not_execution_authority",
                )
            )
            continue

        evidence = authority_evidence.get(control["control_id"])
        failure: str | None = None
        if evidence is None:
            failure = "required_authority_evidence_missing"
        elif evidence.control_id != control["control_id"]:
            failure = "authority_evidence_control_mismatch"
        elif evidence.authority != control["authority"]:
            failure = "authority_mismatch"
        elif evidence.state is not AuthorityState.SATISFIED:
            failure = f"authority_{evidence.state.value}"
        elif evidence.evidence_label in {EvidenceLabel.MODELED, EvidenceLabel.BLOCKED}:
            failure = "authority_cannot_be_modeled_or_blocked"
        elif now > _instant(evidence.valid_until):
            failure = "authority_evidence_stale"
        elif now < _instant(evidence.observed_at):
            failure = "authority_evidence_observed_in_future"
        elif (
            execution_mode is ExecutionMode.BILLABLE
            and evidence.evidence_label is not EvidenceLabel.MEASURED
        ):
            failure = "billable_execution_requires_measured_authority"
        elif control["control_id"] in azure_controls and (
            policy_reason is not None
            or evidence.content_hash != policy.content_hash
        ):
            failure = policy_reason or "runtime_policy_revision_mismatch"

        if failure:
            reasons.append(f"{control['control_id']}:{failure}")
            bindings.append(
                ControlBinding(
                    **common,
                    outcome=BindingOutcome.BLOCKED,
                    authority_evidence_hash=(
                        evidence.content_hash if evidence is not None else None
                    ),
                    tokengov_enforcement_event=False,
                    reason=failure,
                )
            )
        else:
            bindings.append(
                ControlBinding(
                    **common,
                    outcome=_VERIFIED_OUTCOME[scope],
                    authority_evidence_hash=evidence.content_hash,
                    tokengov_enforcement_event=(
                        scope is EnforcementScope.RUNTIME_ENFORCED
                        and control["authority"] == "azure_tokengov"
                    ),
                    reason="authority_preflight_satisfied",
                )
            )

    if readiness.outcome is not ReadinessOutcome.READY:
        reasons.append(f"readiness:{readiness.outcome.value}")
    if reasons:
        outcome = AuthorizationOutcome.BLOCKED
        label = EvidenceLabel.BLOCKED
    elif execution_mode is ExecutionMode.SIMULATION:
        outcome = AuthorizationOutcome.SIMULATION_AUTHORIZED
        label = EvidenceLabel.SIMULATED
    elif any(
        item.enforcement_scope is EnforcementScope.RUNTIME_ENFORCED
        for item in bindings
    ):
        outcome = AuthorizationOutcome.AUTHORIZED
        label = EvidenceLabel.MEASURED
    else:
        outcome = AuthorizationOutcome.EXTERNALLY_AUTHORIZED
        label = EvidenceLabel.MEASURED

    correlation_bindings = [
        ControlBinding(
            control_id=f"usage-source:{source['source_id']}",
            control_kind="usage_correlation",
            target_leg="observation",
            authority=source["authority"],
            capability=source["granularity"],
            enforcement_scope=EnforcementScope.CORRELATION_ONLY,
            outcome=BindingOutcome.CORRELATION_BOUNDARY,
            authority_evidence_hash=None,
            tokengov_enforcement_event=False,
            reason="usage_source_is_not_execution_authority",
        )
        for source in profile["actual_usage_sources"]
    ]
    all_bindings = tuple(bindings + correlation_bindings)
    authorized_legs = (
        tuple(
            sorted(
                {
                    item.target_leg
                    for item in all_bindings
                    if item.outcome
                    not in {
                        BindingOutcome.BLOCKED,
                        BindingOutcome.ADVISORY_BOUNDARY,
                        BindingOutcome.CORRELATION_BOUNDARY,
                    }
                }
            )
        )
        if outcome
        in {
            AuthorizationOutcome.AUTHORIZED,
            AuthorizationOutcome.EXTERNALLY_AUTHORIZED,
        }
        else ()
    )
    auth_id = _authorization_id(
        (
            route_id,
            govern_decision_id,
            govern_decision_hash,
            plan_receipt_id,
            plan_receipt_hash,
            candidate_id,
            candidate_version,
            candidate_hash,
            task_id,
            trajectory_id,
            segment_id,
            segment_version,
            readiness.content_hash,
            preflight_at,
            execution_mode.value,
            policy.content_hash if policy else "no-policy",
            _canonical(
                [
                    authority_evidence[key].to_dict()
                    for key in sorted(authority_evidence)
                ]
            ),
        )
    )
    return CompositeExecutionAuthorization(
        schema_version=EXECUTION_AUTHORIZATION_SCHEMA_VERSION,
        authorization_id=auth_id,
        execution_mode=execution_mode,
        outcome=outcome,
        evidence_label=label,
        route_id=route_id,
        capability_profile_version=profile["version"],
        capability_profile_hash=profile["content_hash"],
        readiness_hash=readiness.content_hash,
        govern_decision_id=govern_decision_id,
        govern_decision_hash=_hash(govern_decision_hash, "govern_decision_hash"),
        plan_receipt_id=plan_receipt_id,
        plan_receipt_hash=_hash(plan_receipt_hash, "plan_receipt_hash"),
        candidate_id=candidate_id,
        candidate_version=candidate_version,
        candidate_hash=_hash(candidate_hash, "candidate_hash"),
        task_id=task_id,
        trajectory_id=trajectory_id,
        segment_id=segment_id,
        segment_version=segment_version,
        policy=policy,
        bindings=all_bindings,
        authority_evidence=tuple(
            authority_evidence[key] for key in sorted(authority_evidence)
        ),
        authorized_legs=authorized_legs,
        blocked_reasons=tuple(reasons),
        preflight_at=preflight_at,
        billable_execution_permitted=(
            execution_mode is ExecutionMode.BILLABLE
            and outcome
            in {
                AuthorizationOutcome.AUTHORIZED,
                AuthorizationOutcome.EXTERNALLY_AUTHORIZED,
            }
        ),
    )


@dataclass(frozen=True)
class ExecutionAuthorizationRecord:
    content_hash: str
    authorization: CompositeExecutionAuthorization


class ExecutionAuthorizationStore:
    """Replay-safe append-only store; identical retries are idempotent."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, authorization_id: str) -> Path:
        name = hashlib.sha256(_required(authorization_id, "authorization_id").encode())
        return self.root / f"{name.hexdigest()}.json"

    def record(
        self, authorization: CompositeExecutionAuthorization
    ) -> ExecutionAuthorizationRecord:
        self.root.mkdir(parents=True, exist_ok=True)
        existing = self.get(authorization.authorization_id)
        if existing is not None:
            if existing.content_hash == authorization.content_hash:
                return existing
            raise ValueError("execution authorization replay conflicts with immutable evidence")
        path = self._path(authorization.authorization_id)
        temporary = self.root / f".{path.stem}.{uuid4().hex}.tmp"
        payload = {
            "content_hash": authorization.content_hash,
            "authorization": authorization.to_dict(),
        }
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(payload, stream, allow_nan=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            try:
                publish_immutable(temporary, path)
            except FileExistsError:
                existing = self.get(authorization.authorization_id)
                if existing and existing.content_hash == authorization.content_hash:
                    return existing
                raise ValueError(
                    "execution authorization replay conflicts with immutable evidence"
                )
        finally:
            temporary.unlink(missing_ok=True)
        return ExecutionAuthorizationRecord(authorization.content_hash, authorization)

    def get(self, authorization_id: str) -> ExecutionAuthorizationRecord | None:
        path = self._path(authorization_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            authorization = CompositeExecutionAuthorization.from_dict(
                payload["authorization"]
            )
            content_hash = _hash(payload["content_hash"], "authorization content_hash")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("execution authorization integrity check failed") from exc
        if authorization.content_hash != content_hash:
            raise ValueError("execution authorization integrity check failed")
        return ExecutionAuthorizationRecord(content_hash, authorization)
