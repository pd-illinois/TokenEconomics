"""Framework-neutral task and trajectory evidence contracts."""

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

TRAJECTORY_SCHEMA_VERSION = "trajectory-envelope.v1"
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


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


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


class StepKind(str, Enum):
    RETRIEVAL = "retrieval"
    MODEL = "model"
    TOOL = "tool"
    CACHE = "cache"
    RETRY = "retry"
    ITERATION = "iteration"


class StepStatus(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class EvidenceField:
    key: str
    value_json: str

    def __post_init__(self) -> None:
        _stable_id(self.key, "evidence key")
        try:
            json.loads(self.value_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("evidence value_json must contain valid JSON") from exc

    @classmethod
    def from_value(cls, key: str, value: Any) -> "EvidenceField":
        try:
            value_json = json.dumps(
                value,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"evidence value for {key} must be finite JSON") from exc
        return cls(key=key, value_json=value_json)

    @classmethod
    def from_dict(cls, value: object) -> "EvidenceField":
        values = _mapping(value, "evidence field")
        return cls.from_value(
            _required_text(values.get("key"), "evidence key"),
            values.get("value"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "value": json.loads(self.value_json)}


def _evidence_fields(value: object, field: str) -> tuple[EvidenceField, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be an array")
    result = tuple(EvidenceField.from_dict(item) for item in value)
    keys = [item.key for item in result]
    if len(keys) != len(set(keys)):
        raise ValueError(f"{field} keys must be unique")
    return result


@dataclass(frozen=True)
class WorkloadIdentity:
    workload_id: str
    version: str

    def __post_init__(self) -> None:
        _stable_id(self.workload_id, "workload_id")
        _stable_id(self.version, "workload version")

    @classmethod
    def from_dict(cls, value: object) -> "WorkloadIdentity":
        values = _mapping(value, "workload")
        return cls(
            workload_id=values.get("workload_id"),
            version=values.get("version"),
        )

    def to_dict(self) -> dict[str, str]:
        return {"workload_id": self.workload_id, "version": self.version}


@dataclass(frozen=True)
class SegmentIdentity:
    segment_id: str
    version: str
    attributes: tuple[EvidenceField, ...] = ()

    def __post_init__(self) -> None:
        _stable_id(self.segment_id, "segment_id")
        _stable_id(self.version, "segment version")
        keys = [item.key for item in self.attributes]
        if len(keys) != len(set(keys)):
            raise ValueError("segment attribute keys must be unique")

    @classmethod
    def from_dict(cls, value: object) -> "SegmentIdentity":
        values = _mapping(value, "segment")
        return cls(
            segment_id=values.get("segment_id"),
            version=values.get("version"),
            attributes=_evidence_fields(
                values.get("attributes", []), "segment attributes"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "version": self.version,
            "attributes": [item.to_dict() for item in self.attributes],
        }


@dataclass(frozen=True)
class TaskIdentity:
    task_id: str
    report_id: str
    workload: WorkloadIdentity
    segment: SegmentIdentity
    created_at: str

    def __post_init__(self) -> None:
        _stable_id(self.task_id, "task_id")
        _stable_id(self.report_id, "report_id")
        _utc_timestamp(self.created_at, "task created_at")

    @classmethod
    def from_dict(cls, value: object) -> "TaskIdentity":
        values = _mapping(value, "task")
        return cls(
            task_id=values.get("task_id"),
            report_id=values.get("report_id"),
            workload=WorkloadIdentity.from_dict(values.get("workload")),
            segment=SegmentIdentity.from_dict(values.get("segment")),
            created_at=values.get("created_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "report_id": self.report_id,
            "workload": self.workload.to_dict(),
            "segment": self.segment.to_dict(),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class PredictionBinding:
    prediction_id: str
    receipt_id: str
    schema_version: str
    content_hash: str

    def __post_init__(self) -> None:
        _stable_id(self.prediction_id, "prediction_id")
        _stable_id(self.receipt_id, "prediction receipt_id")
        _stable_id(self.schema_version, "prediction schema_version")
        _content_hash(self.content_hash, "prediction content_hash")

    @classmethod
    def from_dict(cls, value: object) -> "PredictionBinding":
        values = _mapping(value, "prediction_binding")
        return cls(
            prediction_id=values.get("prediction_id"),
            receipt_id=values.get("receipt_id"),
            schema_version=values.get("schema_version"),
            content_hash=values.get("content_hash"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "prediction_id": self.prediction_id,
            "receipt_id": self.receipt_id,
            "schema_version": self.schema_version,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True)
class PolicyBinding:
    policy_id: str
    version: str
    content_hash: str
    source: str
    label: str
    etag: str

    def __post_init__(self) -> None:
        _stable_id(self.policy_id, "policy_id")
        _stable_id(self.version, "policy version")
        _content_hash(self.content_hash, "policy content_hash")
        _stable_id(self.source, "policy source")
        _required_text(self.label, "policy label")
        _required_text(self.etag, "policy etag")

    @classmethod
    def from_dict(cls, value: object) -> "PolicyBinding":
        values = _mapping(value, "policy_binding")
        return cls(
            policy_id=values.get("policy_id"),
            version=values.get("version"),
            content_hash=values.get("content_hash"),
            source=values.get("source"),
            label=values.get("label"),
            etag=values.get("etag"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "content_hash": self.content_hash,
            "source": self.source,
            "label": self.label,
            "etag": self.etag,
        }


@dataclass(frozen=True)
class TrajectoryContractBinding:
    schema_version: str
    workload: WorkloadIdentity
    segment_schema_version: str
    prediction_binding: PredictionBinding | None = None
    policy_binding: PolicyBinding | None = None

    def __post_init__(self) -> None:
        if self.schema_version != TRAJECTORY_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {TRAJECTORY_SCHEMA_VERSION}"
            )
        _stable_id(self.segment_schema_version, "segment_schema_version")

    @classmethod
    def from_dict(cls, value: object) -> "TrajectoryContractBinding":
        values = _mapping(value, "trajectory contract")
        prediction = values.get("prediction_binding")
        policy = values.get("policy_binding")
        return cls(
            schema_version=values.get("schema_version"),
            workload=WorkloadIdentity.from_dict(values.get("workload")),
            segment_schema_version=values.get("segment_schema_version"),
            prediction_binding=(
                PredictionBinding.from_dict(prediction)
                if prediction is not None
                else None
            ),
            policy_binding=(
                PolicyBinding.from_dict(policy) if policy is not None else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "workload": self.workload.to_dict(),
            "segment_schema_version": self.segment_schema_version,
            "prediction_binding": (
                self.prediction_binding.to_dict()
                if self.prediction_binding is not None
                else None
            ),
            "policy_binding": (
                self.policy_binding.to_dict()
                if self.policy_binding is not None
                else None
            ),
        }


@dataclass(frozen=True)
class StepEvidence:
    step_id: str
    sequence: int
    kind: StepKind
    status: StepStatus
    operation: str
    started_at: str
    ended_at: str | None = None
    parent_step_id: str | None = None
    attempt: int = 1
    evidence: tuple[EvidenceField, ...] = ()

    def __post_init__(self) -> None:
        _stable_id(self.step_id, "step_id")
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 1
        ):
            raise ValueError("step sequence must be a positive integer")
        if not isinstance(self.kind, StepKind):
            raise ValueError("step kind is invalid")
        if not isinstance(self.status, StepStatus):
            raise ValueError("step status is invalid")
        _required_text(self.operation, "step operation")
        _utc_timestamp(self.started_at, "step started_at")
        if self.ended_at is not None:
            _utc_timestamp(self.ended_at, "step ended_at")
            if _instant(self.ended_at) < _instant(self.started_at):
                raise ValueError("step ended_at must not precede started_at")
        if self.status in {
            StepStatus.COMPLETED,
            StepStatus.FAILED,
            StepStatus.SKIPPED,
        } and self.ended_at is None:
            raise ValueError("terminal step status requires ended_at")
        if self.parent_step_id is not None:
            _stable_id(self.parent_step_id, "parent_step_id")
            if self.parent_step_id == self.step_id:
                raise ValueError("step cannot be its own parent")
        if (
            isinstance(self.attempt, bool)
            or not isinstance(self.attempt, int)
            or self.attempt < 1
        ):
            raise ValueError("step attempt must be a positive integer")
        keys = [item.key for item in self.evidence]
        if len(keys) != len(set(keys)):
            raise ValueError("step evidence keys must be unique")

    @classmethod
    def from_dict(cls, value: object) -> "StepEvidence":
        values = _mapping(value, "step")
        try:
            kind = StepKind(values.get("kind"))
        except ValueError as exc:
            raise ValueError("step kind is invalid") from exc
        try:
            status = StepStatus(values.get("status"))
        except ValueError as exc:
            raise ValueError("step status is invalid") from exc
        return cls(
            step_id=values.get("step_id"),
            sequence=values.get("sequence"),
            kind=kind,
            status=status,
            operation=values.get("operation"),
            started_at=values.get("started_at"),
            ended_at=values.get("ended_at"),
            parent_step_id=values.get("parent_step_id"),
            attempt=values.get("attempt", 1),
            evidence=_evidence_fields(values.get("evidence", []), "step evidence"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "sequence": self.sequence,
            "kind": self.kind.value,
            "status": self.status.value,
            "operation": self.operation,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "parent_step_id": self.parent_step_id,
            "attempt": self.attempt,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True)
class TrajectoryEnvelope:
    schema_version: str
    trajectory_id: str
    run_id: str
    trace_id: str
    task: TaskIdentity
    prediction_binding: PredictionBinding
    policy_binding: PolicyBinding
    status: str
    started_at: str
    ended_at: str | None
    recorded_at: str
    steps: tuple[StepEvidence, ...]

    def __post_init__(self) -> None:
        if self.schema_version != TRAJECTORY_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {TRAJECTORY_SCHEMA_VERSION}"
            )
        _stable_id(self.trajectory_id, "trajectory_id")
        _stable_id(self.run_id, "run_id")
        _stable_id(self.trace_id, "trace_id")
        if self.status not in {"running", "completed", "failed", "cancelled"}:
            raise ValueError("trajectory status is invalid")
        _utc_timestamp(self.started_at, "trajectory started_at")
        _utc_timestamp(self.recorded_at, "trajectory recorded_at")
        if self.ended_at is not None:
            _utc_timestamp(self.ended_at, "trajectory ended_at")
            if _instant(self.ended_at) < _instant(self.started_at):
                raise ValueError(
                    "trajectory ended_at must not precede started_at"
                )
        if self.status != "running" and self.ended_at is None:
            raise ValueError("terminal trajectory status requires ended_at")
        if _instant(self.task.created_at) > _instant(self.started_at):
            raise ValueError("task created_at must not follow trajectory started_at")
        if _instant(self.recorded_at) < _instant(self.started_at):
            raise ValueError("trajectory recorded_at must not precede started_at")
        if self.ended_at and _instant(self.recorded_at) < _instant(self.ended_at):
            raise ValueError("trajectory recorded_at must not precede ended_at")
        if not self.steps:
            raise ValueError("trajectory steps are required")

        step_ids = [step.step_id for step in self.steps]
        sequences = [step.sequence for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("trajectory step_id values must be unique")
        if len(sequences) != len(set(sequences)):
            raise ValueError("trajectory step sequence values must be unique")
        ordered = sorted(self.steps, key=lambda step: step.sequence)
        known_sequences = {step.step_id: step.sequence for step in self.steps}
        for step in ordered:
            if _instant(step.started_at) < _instant(self.started_at):
                raise ValueError("step started_at precedes trajectory")
            if self.ended_at and step.ended_at:
                if _instant(step.ended_at) > _instant(self.ended_at):
                    raise ValueError("step ended_at follows trajectory")
            if step.parent_step_id is not None:
                parent_sequence = known_sequences.get(step.parent_step_id)
                if parent_sequence is None:
                    raise ValueError("step parent does not exist")
                if parent_sequence >= step.sequence:
                    raise ValueError("step parent must precede child")

    @classmethod
    def from_dict(cls, value: object) -> "TrajectoryEnvelope":
        values = _mapping(value, "trajectory envelope")
        raw_steps = values.get("steps")
        if not isinstance(raw_steps, (list, tuple)):
            raise ValueError("trajectory steps must be an array")
        return cls(
            schema_version=values.get("schema_version"),
            trajectory_id=values.get("trajectory_id"),
            run_id=values.get("run_id"),
            trace_id=values.get("trace_id"),
            task=TaskIdentity.from_dict(values.get("task")),
            prediction_binding=PredictionBinding.from_dict(
                values.get("prediction_binding")
            ),
            policy_binding=PolicyBinding.from_dict(values.get("policy_binding")),
            status=values.get("status"),
            started_at=values.get("started_at"),
            ended_at=values.get("ended_at"),
            recorded_at=values.get("recorded_at"),
            steps=tuple(StepEvidence.from_dict(item) for item in raw_steps),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "trajectory_id": self.trajectory_id,
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "task": self.task.to_dict(),
            "prediction_binding": self.prediction_binding.to_dict(),
            "policy_binding": self.policy_binding.to_dict(),
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "recorded_at": self.recorded_at,
            "steps": [step.to_dict() for step in self.steps],
        }

    def to_canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


@dataclass(frozen=True)
class TrajectoryRecord:
    content_hash: str
    envelope: TrajectoryEnvelope


class TrajectoryStore:
    """Append-only store with atomic publication and verified reopen.

    Directory metadata is fsynced on POSIX. Windows provides process-crash-safe
    atomic publication, but full power-loss durability remains filesystem-defined.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @staticmethod
    def _hash(envelope: TrajectoryEnvelope) -> str:
        return hashlib.sha256(
            envelope.to_canonical_json().encode("utf-8")
        ).hexdigest()

    def _path(self, trajectory_id: str) -> Path:
        _stable_id(trajectory_id, "trajectory_id")
        filename = hashlib.sha256(trajectory_id.encode("utf-8")).hexdigest()
        return self.root / f"{filename}.json"

    @staticmethod
    def _sync_directory(path: Path) -> None:
        if os.name != "posix":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def append(self, envelope: TrajectoryEnvelope) -> TrajectoryRecord:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(envelope.trajectory_id)
        temporary = self.root / f".{path.stem}.{uuid4().hex}.tmp"
        record = TrajectoryRecord(
            content_hash=self._hash(envelope),
            envelope=envelope,
        )
        payload = {
            "content_hash": record.content_hash,
            "envelope": envelope.to_dict(),
        }
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, allow_nan=False, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, path)
            self._sync_directory(self.root)
        finally:
            temporary.unlink(missing_ok=True)
        return record

    def get(self, trajectory_id: str) -> TrajectoryRecord | None:
        path = self._path(trajectory_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            envelope = TrajectoryEnvelope.from_dict(payload["envelope"])
            stored_hash = _content_hash(
                payload["content_hash"], "trajectory content_hash"
            )
        except (KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("trajectory evidence integrity check failed") from exc
        if envelope.trajectory_id != trajectory_id:
            raise ValueError("trajectory evidence integrity check failed")
        if self._hash(envelope) != stored_hash:
            raise ValueError("trajectory evidence integrity check failed")
        return TrajectoryRecord(content_hash=stored_hash, envelope=envelope)
