"""Immutable policy candidates for experiment comparison without authority mutation."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

POLICY_CANDIDATE_SCHEMA_VERSION = "policy-candidate.v1"

_CAPABILITIES = {
    "model_selection": ("azure_tokengov", "model_routing", "runtime_enforced"),
    "routing": ("azure_tokengov", "model_routing", "runtime_enforced"),
    "cache": ("azure_tokengov", "semantic_cache", "runtime_enforced"),
    "context": ("azure_tokengov", "context_management", "runtime_enforced"),
    "retrieval": ("workload_adapter", "retrieval_configuration", "runtime_enforced"),
    "retry": ("azure_tokengov", "retry_control", "runtime_enforced"),
    "iteration": ("azure_tokengov", "iteration_control", "runtime_enforced"),
    "evaluation": ("azure_tokengov", "evaluation_gate", "control_plane_enforced"),
    "monetary_budget": ("azure_tokengov", "budget_enforcement", "runtime_enforced"),
    "native_meter_cap": (
        "product_meter_authority",
        "native_meter_capacity",
        "external_requirement",
    ),
    "committed_capacity": (
        "commercial_evidence_authority",
        "committed_capacity_evidence",
        "external_requirement",
    ),
    "overage_action": (
        "product_meter_authority",
        "overage_control",
        "external_requirement",
    ),
    "external_governance_posture": (
        "external_governance_authority",
        "posture_evidence",
        "external_requirement",
    ),
}


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


class CandidateStatus(str, Enum):
    PROPOSED = "proposed"
    RETIRED = "retired"


@dataclass(frozen=True)
class PolicyControl:
    control_id: str
    kind: str
    path: str
    value_json: str
    authority: str
    capability: str
    enforcement_scope: str

    def __post_init__(self) -> None:
        for field in ("control_id", "kind", "path", "authority", "capability", "enforcement_scope"):
            _required(getattr(self, field), field)
        if self.kind not in _CAPABILITIES:
            raise ValueError(f"unsupported policy control kind: {self.kind}")
        if (
            self.authority,
            self.capability,
            self.enforcement_scope,
        ) != _CAPABILITIES[self.kind]:
            raise ValueError(
                f"policy control {self.kind} does not match its declared authority and capability"
            )
        try:
            value = json.loads(self.value_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("policy control value_json must contain valid JSON") from exc
        if self.kind == "external_governance_posture":
            if not isinstance(value, dict):
                raise ValueError("external governance posture value must be an object")
            required = {
                "source",
                "freshness",
                "maturity",
                "enforcement_coverage",
                "status",
            }
            if not required <= set(value) or value["status"] not in {
                "satisfied",
                "failed",
                "inconclusive",
                "unavailable",
            }:
                raise ValueError(
                    "external governance posture requires source, freshness, "
                    "maturity, enforcement_coverage, and a valid status"
                )

    @classmethod
    def from_value(
        cls,
        control_id: str,
        kind: str,
        path: str,
        value: Any,
        *,
        authority: str,
        capability: str,
        enforcement_scope: str,
    ) -> "PolicyControl":
        try:
            value_json = json.dumps(
                value, allow_nan=False, separators=(",", ":"), sort_keys=True
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("policy control value must contain finite JSON") from exc
        return cls(
            control_id,
            kind,
            path,
            value_json,
            authority,
            capability,
            enforcement_scope,
        )

    @classmethod
    def from_dict(cls, value: object) -> "PolicyControl":
        if not isinstance(value, Mapping):
            raise ValueError("policy control must be an object")
        return cls.from_value(
            value.get("control_id"),
            value.get("kind"),
            value.get("path"),
            value.get("value"),
            authority=value.get("authority"),
            capability=value.get("capability"),
            enforcement_scope=value.get("enforcement_scope"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "control_id": self.control_id,
            "kind": self.kind,
            "path": self.path,
            "value": json.loads(self.value_json),
            "authority": self.authority,
            "capability": self.capability,
            "enforcement_scope": self.enforcement_scope,
        }


@dataclass(frozen=True)
class PolicyCandidate:
    schema_version: str
    candidate_id: str
    version: str
    status: CandidateStatus
    created_at: str
    experiment_id: str
    experiment_revision: str
    meter_stack_id: str
    meter_stack_version: str
    meter_stack_content_hash: str
    controls: tuple[PolicyControl, ...]

    def __post_init__(self) -> None:
        if self.schema_version != POLICY_CANDIDATE_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {POLICY_CANDIDATE_SCHEMA_VERSION}"
            )
        for field in (
            "candidate_id",
            "version",
            "experiment_id",
            "experiment_revision",
            "meter_stack_id",
            "meter_stack_version",
        ):
            _required(getattr(self, field), field)
        if not isinstance(self.status, CandidateStatus):
            raise ValueError("candidate status is invalid")
        _utc(self.created_at, "candidate created_at")
        _hash(self.meter_stack_content_hash, "meter_stack_content_hash")
        ids = [control.control_id for control in self.controls]
        paths = [control.path for control in self.controls]
        if not self.controls or len(ids) != len(set(ids)) or len(paths) != len(set(paths)):
            raise ValueError("policy candidate controls must be non-empty and unique")

    @classmethod
    def from_dict(cls, value: object) -> "PolicyCandidate":
        if not isinstance(value, Mapping):
            raise ValueError("policy candidate must be an object")
        values = dict(value)
        values["status"] = CandidateStatus(values.get("status"))
        raw_controls = values.get("controls")
        if not isinstance(raw_controls, list):
            raise ValueError("policy candidate controls must be an array")
        values["controls"] = tuple(PolicyControl.from_dict(item) for item in raw_controls)
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "version": self.version,
            "status": self.status.value,
            "created_at": self.created_at,
            "experiment_id": self.experiment_id,
            "experiment_revision": self.experiment_revision,
            "meter_stack_id": self.meter_stack_id,
            "meter_stack_version": self.meter_stack_version,
            "meter_stack_content_hash": self.meter_stack_content_hash,
            "controls": [control.to_dict() for control in self.controls],
        }

    def to_canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(), allow_nan=False, separators=(",", ":"), sort_keys=True
        )

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.to_canonical_json().encode()).hexdigest()


def validate_candidate_binding(
    candidate: PolicyCandidate,
    manifest: object,
    arm_id: str,
) -> None:
    """Validate the acyclic candidate -> experiment and manifest -> candidate binding."""
    if (
        candidate.experiment_id != getattr(manifest, "experiment_id", None)
        or candidate.experiment_revision != getattr(manifest, "revision", None)
    ):
        raise ValueError("policy candidate does not bind the experiment revision")
    arm = next(
        (item for item in getattr(manifest, "arms", ()) if item.arm_id == arm_id),
        None,
    )
    if arm is None:
        raise ValueError("policy candidate arm is not present in the experiment")
    reference = arm.policy_candidate
    if (
        reference.evidence_id != candidate.candidate_id
        or reference.revision != candidate.version
        or reference.content_hash != candidate.content_hash
    ):
        raise ValueError("experiment arm does not hash-bind the policy candidate")


def validate_candidate_application(
    candidate: PolicyCandidate,
    applied_values: Mapping[str, Any],
) -> None:
    """Fail closed when an enforceable candidate control was not actually applied."""
    for control in candidate.controls:
        if control.enforcement_scope == "external_requirement":
            continue
        if control.path not in applied_values:
            raise ValueError(
                f"candidate control is unsupported by this runtime: {control.path}"
            )
        applied_json = json.dumps(
            applied_values[control.path],
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if applied_json != control.value_json:
            raise ValueError(
                f"candidate control was not applied as declared: {control.path}"
            )


@dataclass(frozen=True)
class PolicyCandidateRecord:
    content_hash: str
    candidate: PolicyCandidate


class PolicyCandidateStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, candidate_id: str, version: str) -> Path:
        key = f"{_required(candidate_id, 'candidate_id')}:{_required(version, 'version')}"
        return self.root / f"{hashlib.sha256(key.encode()).hexdigest()}.json"

    def append(self, candidate: PolicyCandidate) -> PolicyCandidateRecord:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(candidate.candidate_id, candidate.version)
        temporary = self.root / f".{path.stem}.{uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(
                    {"content_hash": candidate.content_hash, "candidate": candidate.to_dict()},
                    stream,
                    indent=2,
                    allow_nan=False,
                    sort_keys=True,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return PolicyCandidateRecord(candidate.content_hash, candidate)

    def get(self, candidate_id: str, version: str) -> PolicyCandidateRecord | None:
        path = self._path(candidate_id, version)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            candidate = PolicyCandidate.from_dict(payload["candidate"])
            content_hash = _hash(payload["content_hash"], "candidate content_hash")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("policy candidate integrity check failed") from exc
        if (
            candidate.candidate_id != candidate_id
            or candidate.version != version
            or candidate.content_hash != content_hash
        ):
            raise ValueError("policy candidate integrity check failed")
        return PolicyCandidateRecord(content_hash, candidate)
