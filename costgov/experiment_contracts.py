"""Immutable experiment manifests shared by planning, policy comparison, and reconciliation."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

EXPERIMENT_SCHEMA_VERSION = "experiment-manifest.v1"
OBSERVATION_UNIT = "complete_task_trajectory"
EVIDENCE_CATEGORIES = frozenset(
    {
        "workload",
        "task_set",
        "corpus_index",
        "retrieval",
        "agent_prompt",
        "golden_set",
        "evaluator",
        "model_catalog",
        "pricing",
        "meter_stack",
        "commercial_rate_cards",
        "entitlement",
        "purchase_allocation",
        "billing_period",
        "infrastructure",
        "external_governance",
    }
)
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_FACTOR_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value


def _stable_id(value: object, field: str) -> str:
    text = _required_text(value, field)
    if not _ID_PATTERN.fullmatch(text):
        raise ValueError(f"{field} must be a stable identifier")
    return text


def _content_hash(value: object, field: str) -> str:
    text = _required_text(value, field)
    if not _HASH_PATTERN.fullmatch(text):
        raise ValueError(f"{field} must be a lowercase SHA-256 hash")
    return text


def _utc_timestamp(value: object, field: str) -> str:
    text = _required_text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be an ISO-8601 UTC timestamp")
    return text


def _finite_json(value: object, field: str) -> str:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must contain finite JSON") from exc


class EvidenceApplicability(str, Enum):
    APPLICABLE = "applicable"
    NON_APPLICABLE = "non_applicable"
    UNAVAILABLE = "unavailable"


class EvidenceStatus(str, Enum):
    MEASURED = "measured"
    MODELED = "modeled"
    SOURCED = "sourced"
    PROPOSED = "proposed"
    BLOCKED = "blocked"
    PRODUCTION_VALIDATED = "production_validated"


@dataclass(frozen=True)
class EvidenceReference:
    category: str
    evidence_id: str
    revision: str
    applicability: EvidenceApplicability
    status: EvidenceStatus
    authority: str
    content_hash: str | None = None
    location: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.category not in EVIDENCE_CATEGORIES and self.category != "policy_candidate":
            raise ValueError(f"unsupported evidence category: {self.category}")
        _stable_id(self.evidence_id, "evidence_id")
        _stable_id(self.revision, "evidence revision")
        _required_text(self.authority, "evidence authority")
        if not isinstance(self.applicability, EvidenceApplicability):
            raise ValueError("evidence applicability is invalid")
        if not isinstance(self.status, EvidenceStatus):
            raise ValueError("evidence status is invalid")
        if self.applicability is EvidenceApplicability.APPLICABLE:
            _content_hash(self.content_hash, "evidence content_hash")
        elif not self.reason or not self.reason.strip():
            raise ValueError("non-applicable or unavailable evidence requires a reason")
        if (
            self.content_hash is not None
            and self.applicability is not EvidenceApplicability.APPLICABLE
        ):
            _content_hash(self.content_hash, "evidence content_hash")
        if self.location is not None:
            _required_text(self.location, "evidence location")

    @classmethod
    def from_dict(cls, value: object) -> "EvidenceReference":
        values = _mapping(value, "evidence reference")
        try:
            applicability = EvidenceApplicability(values.get("applicability"))
            status = EvidenceStatus(values.get("status"))
        except ValueError as exc:
            raise ValueError("evidence applicability or status is invalid") from exc
        return cls(
            category=values.get("category"),
            evidence_id=values.get("evidence_id"),
            revision=values.get("revision"),
            applicability=applicability,
            status=status,
            authority=values.get("authority"),
            content_hash=values.get("content_hash"),
            location=values.get("location"),
            reason=values.get("reason"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "evidence_id": self.evidence_id,
            "revision": self.revision,
            "applicability": self.applicability.value,
            "status": self.status.value,
            "authority": self.authority,
            "content_hash": self.content_hash,
            "location": self.location,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ExperimentFactor:
    path: str
    value_json: str

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not _FACTOR_PATTERN.fullmatch(self.path):
            raise ValueError("experiment factor path must be stable")
        try:
            json.loads(self.value_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("experiment factor value_json must contain valid JSON") from exc

    @classmethod
    def from_value(cls, path: str, value: Any) -> "ExperimentFactor":
        return cls(path=path, value_json=_finite_json(value, f"factor {path}"))

    @classmethod
    def from_dict(cls, value: object) -> "ExperimentFactor":
        values = _mapping(value, "experiment factor")
        return cls.from_value(values.get("path"), values.get("value"))

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "value": json.loads(self.value_json)}


@dataclass(frozen=True)
class ExperimentArm:
    arm_id: str
    role: str
    policy_candidate: EvidenceReference
    factors: tuple[ExperimentFactor, ...]

    def __post_init__(self) -> None:
        _stable_id(self.arm_id, "arm_id")
        if self.role not in {"baseline", "candidate"}:
            raise ValueError("experiment arm role must be baseline or candidate")
        if self.policy_candidate.category != "policy_candidate":
            raise ValueError("experiment arm must pin a policy_candidate reference")
        if self.policy_candidate.applicability is not EvidenceApplicability.APPLICABLE:
            raise ValueError("policy_candidate evidence must be applicable")
        paths = [factor.path for factor in self.factors]
        if not paths or len(paths) != len(set(paths)):
            raise ValueError("experiment arm factors must be non-empty and unique")

    @classmethod
    def from_dict(cls, value: object) -> "ExperimentArm":
        values = _mapping(value, "experiment arm")
        raw_factors = values.get("factors")
        if not isinstance(raw_factors, list):
            raise ValueError("experiment arm factors must be an array")
        return cls(
            arm_id=values.get("arm_id"),
            role=values.get("role"),
            policy_candidate=EvidenceReference.from_dict(
                values.get("policy_candidate")
            ),
            factors=tuple(ExperimentFactor.from_dict(item) for item in raw_factors),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm_id": self.arm_id,
            "role": self.role,
            "policy_candidate": self.policy_candidate.to_dict(),
            "factors": [factor.to_dict() for factor in self.factors],
        }


@dataclass(frozen=True)
class ExperimentManifest:
    schema_version: str
    experiment_id: str
    revision: str
    created_at: str
    observation_unit: str
    evidence: tuple[EvidenceReference, ...]
    arms: tuple[ExperimentArm, ...]

    def __post_init__(self) -> None:
        if self.schema_version != EXPERIMENT_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {EXPERIMENT_SCHEMA_VERSION}")
        _stable_id(self.experiment_id, "experiment_id")
        _stable_id(self.revision, "experiment revision")
        _utc_timestamp(self.created_at, "experiment created_at")
        if self.observation_unit != OBSERVATION_UNIT:
            raise ValueError(f"observation_unit must be {OBSERVATION_UNIT}")

        categories = [item.category for item in self.evidence]
        missing = EVIDENCE_CATEGORIES - set(categories)
        extra = set(categories) - EVIDENCE_CATEGORIES
        if missing or extra or len(categories) != len(set(categories)):
            raise ValueError(
                "manifest evidence must contain each required category exactly once"
            )

        arm_ids = [arm.arm_id for arm in self.arms]
        baselines = [arm for arm in self.arms if arm.role == "baseline"]
        candidates = [arm for arm in self.arms if arm.role == "candidate"]
        if len(arm_ids) != len(set(arm_ids)):
            raise ValueError("experiment arm IDs must be unique")
        if len(baselines) != 1 or not candidates:
            raise ValueError(
                "experiment must contain exactly one baseline and at least one candidate"
            )
        factor_paths = {factor.path for factor in baselines[0].factors}
        baseline_values = {
            factor.path: factor.value_json for factor in baselines[0].factors
        }
        for arm in candidates:
            if {factor.path for factor in arm.factors} != factor_paths:
                raise ValueError("all experiment arms must pin the same factor paths")
            values = {factor.path: factor.value_json for factor in arm.factors}
            if (
                values == baseline_values
                and arm.policy_candidate == baselines[0].policy_candidate
            ):
                raise ValueError("candidate arm must differ from the baseline")

    @classmethod
    def from_dict(cls, value: object) -> "ExperimentManifest":
        values = _mapping(value, "experiment manifest")
        raw_evidence = values.get("evidence")
        raw_arms = values.get("arms")
        if not isinstance(raw_evidence, list):
            raise ValueError("manifest evidence must be an array")
        if not isinstance(raw_arms, list):
            raise ValueError("manifest arms must be an array")
        return cls(
            schema_version=values.get("schema_version"),
            experiment_id=values.get("experiment_id"),
            revision=values.get("revision"),
            created_at=values.get("created_at"),
            observation_unit=values.get("observation_unit"),
            evidence=tuple(
                EvidenceReference.from_dict(item) for item in raw_evidence
            ),
            arms=tuple(ExperimentArm.from_dict(item) for item in raw_arms),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "revision": self.revision,
            "created_at": self.created_at,
            "observation_unit": self.observation_unit,
            "evidence": [item.to_dict() for item in self.evidence],
            "arms": [arm.to_dict() for arm in self.arms],
        }

    def to_canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def differences_from_baseline(self, arm_id: str) -> tuple[dict[str, Any], ...]:
        baseline = next(arm for arm in self.arms if arm.role == "baseline")
        arm = next((item for item in self.arms if item.arm_id == arm_id), None)
        if arm is None:
            raise KeyError(arm_id)
        baseline_values = {
            factor.path: factor.value_json for factor in baseline.factors
        }
        differences: list[dict[str, Any]] = []
        if arm.policy_candidate != baseline.policy_candidate:
            differences.append(
                {
                    "path": "policy_candidate",
                    "baseline": baseline.policy_candidate.to_dict(),
                    "arm": arm.policy_candidate.to_dict(),
                }
            )
        differences.extend(
            {
                "path": factor.path,
                "baseline": json.loads(baseline_values[factor.path]),
                "arm": json.loads(factor.value_json),
            }
            for factor in arm.factors
            if factor.value_json != baseline_values[factor.path]
        )
        return tuple(differences)


@dataclass(frozen=True)
class ExperimentManifestRecord:
    content_hash: str
    manifest: ExperimentManifest


class ExperimentManifestStore:
    """Append-only content-addressed store with verified reopen."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @staticmethod
    def _hash(manifest: ExperimentManifest) -> str:
        return hashlib.sha256(
            manifest.to_canonical_json().encode("utf-8")
        ).hexdigest()

    def _path(self, experiment_id: str, revision: str) -> Path:
        _stable_id(experiment_id, "experiment_id")
        _stable_id(revision, "experiment revision")
        key = f"{experiment_id}:{revision}"
        filename = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.root / f"{filename}.json"

    def append(self, manifest: ExperimentManifest) -> ExperimentManifestRecord:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(manifest.experiment_id, manifest.revision)
        temporary = self.root / f".{path.stem}.{uuid4().hex}.tmp"
        record = ExperimentManifestRecord(
            content_hash=self._hash(manifest),
            manifest=manifest,
        )
        payload = {
            "content_hash": record.content_hash,
            "manifest": manifest.to_dict(),
        }
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, allow_nan=False, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return record

    def get(
        self, experiment_id: str, revision: str
    ) -> ExperimentManifestRecord | None:
        path = self._path(experiment_id, revision)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            manifest = ExperimentManifest.from_dict(payload["manifest"])
            stored_hash = _content_hash(
                payload["content_hash"], "experiment manifest content_hash"
            )
        except (KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("experiment manifest integrity check failed") from exc
        if (
            manifest.experiment_id != experiment_id
            or manifest.revision != revision
            or self._hash(manifest) != stored_hash
        ):
            raise ValueError("experiment manifest integrity check failed")
        return ExperimentManifestRecord(
            content_hash=stored_hash,
            manifest=manifest,
        )
