"""Workload-defined acceptance packs and explicit task acceptance evidence."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import uuid4

from .atomic_publish import publish_immutable

from .acceptance_contracts import AcceptanceDecision
from .route_usage import ROUTE_LEDGER_LEGS

ACCEPTANCE_PACK_SCHEMA_VERSION = "workload-acceptance-pack.v1"
ROUTE_ACCEPTANCE_OUTCOME_SCHEMA_VERSION = "route-acceptance-outcome.v1"


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


def _rate(value: object, field: str) -> str:
    text = _required(value, field)
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be a decimal string") from exc
    if not parsed.is_finite() or parsed < 0 or parsed > 1:
        raise ValueError(f"{field} must be between 0 and 1")
    return text


class ProvenanceKind(str, Enum):
    TRAJECTORY = "trajectory"
    OUTPUT = "output"
    GROUND_TRUTH = "ground_truth"
    REVIEW_MATERIAL = "review_material"


class EvidenceAvailability(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class AssertionMethod(str, Enum):
    AUTOMATED = "automated_assertion"
    HUMAN = "human_assertion"


class OperationalOutcome(str, Enum):
    COMPLETED = "completed"
    REJECTED_QUALITY = "rejected_quality"
    BLOCKED_CAPACITY = "blocked_capacity"
    BLOCKED_POLICY = "blocked_policy"
    ABANDONED = "abandoned"
    FAILED_TOOL = "failed_tool"
    NEEDS_HUMAN_REWORK = "needs_human_rework"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class SegmentAcceptanceRequirement:
    segment_id: str
    segment_version: str
    minimum_segment_samples: int
    minimum_acceptance_rate: str
    minimum_task_assertions: int
    required_provenance: tuple[ProvenanceKind, ...]

    def __post_init__(self) -> None:
        _required(self.segment_id, "segment_id")
        _required(self.segment_version, "segment_version")
        if (
            isinstance(self.minimum_segment_samples, bool)
            or not isinstance(self.minimum_segment_samples, int)
            or self.minimum_segment_samples < 1
        ):
            raise ValueError("minimum_segment_samples must be a positive integer")
        _rate(self.minimum_acceptance_rate, "minimum_acceptance_rate")
        if (
            isinstance(self.minimum_task_assertions, bool)
            or not isinstance(self.minimum_task_assertions, int)
            or self.minimum_task_assertions < 1
        ):
            raise ValueError("minimum_task_assertions must be a positive integer")
        if not self.required_provenance:
            raise ValueError("required_provenance must not be empty")
        if any(
            not isinstance(item, ProvenanceKind) for item in self.required_provenance
        ):
            raise ValueError("required provenance kind is invalid")
        if len(self.required_provenance) != len(set(self.required_provenance)):
            raise ValueError("required provenance kinds must be unique")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SegmentAcceptanceRequirement":
        values = dict(value)
        values["required_provenance"] = tuple(
            ProvenanceKind(item) for item in values.get("required_provenance", ())
        )
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "segment_version": self.segment_version,
            "minimum_segment_samples": self.minimum_segment_samples,
            "minimum_acceptance_rate": self.minimum_acceptance_rate,
            "minimum_task_assertions": self.minimum_task_assertions,
            "required_provenance": [
                item.value for item in self.required_provenance
            ],
        }


@dataclass(frozen=True)
class WorkloadAcceptancePack:
    schema_version: str
    pack_id: str
    version: str
    workload_id: str
    workload_version: str
    route_ids: tuple[str, ...]
    requirements: tuple[SegmentAcceptanceRequirement, ...]
    created_at: str
    source_revision: str
    source_content_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != ACCEPTANCE_PACK_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {ACCEPTANCE_PACK_SCHEMA_VERSION}")
        for field in (
            "pack_id",
            "version",
            "workload_id",
            "workload_version",
            "source_revision",
        ):
            _required(getattr(self, field), field)
        _hash(self.source_content_hash, "acceptance pack source_content_hash")
        _utc(self.created_at, "acceptance pack created_at")
        if not self.route_ids or any(route not in ROUTE_LEDGER_LEGS for route in self.route_ids):
            raise ValueError("acceptance pack route_ids must contain supported routes")
        if len(self.route_ids) != len(set(self.route_ids)):
            raise ValueError("acceptance pack route_ids must be unique")
        if not self.requirements:
            raise ValueError("acceptance pack requires segment requirements")
        keys = [
            (item.segment_id, item.segment_version) for item in self.requirements
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("acceptance pack segment requirements must be unique")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkloadAcceptancePack":
        values = dict(value)
        values["route_ids"] = tuple(values.get("route_ids", ()))
        values["requirements"] = tuple(
            SegmentAcceptanceRequirement.from_dict(item)
            for item in values.get("requirements", ())
        )
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "pack_id": self.pack_id,
            "version": self.version,
            "workload_id": self.workload_id,
            "workload_version": self.workload_version,
            "route_ids": list(self.route_ids),
            "requirements": [item.to_dict() for item in self.requirements],
            "created_at": self.created_at,
            "source_revision": self.source_revision,
            "source_content_hash": self.source_content_hash,
        }

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict()).encode()).hexdigest()

    def requirement_for(
        self, segment_id: str, segment_version: str
    ) -> SegmentAcceptanceRequirement:
        matches = [
            item
            for item in self.requirements
            if item.segment_id == segment_id
            and item.segment_version == segment_version
        ]
        if len(matches) != 1:
            raise ValueError("acceptance pack does not define the task segment")
        return matches[0]


@dataclass(frozen=True)
class SampleEvidence:
    sample_id: str
    sample_set_id: str
    sample_set_revision: str
    sample_set_content_hash: str
    segment_completed_samples: int
    representative: bool

    def __post_init__(self) -> None:
        for field in ("sample_id", "sample_set_id", "sample_set_revision"):
            _required(getattr(self, field), f"sample {field}")
        _hash(self.sample_set_content_hash, "sample_set_content_hash")
        if (
            isinstance(self.segment_completed_samples, bool)
            or not isinstance(self.segment_completed_samples, int)
            or self.segment_completed_samples < 1
        ):
            raise ValueError("segment_completed_samples must be a positive integer")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SampleEvidence":
        return cls(**value)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class ProvenanceEvidence:
    kind: ProvenanceKind
    evidence_id: str
    revision: str
    content_hash: str | None
    availability: EvidenceAvailability
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ProvenanceKind):
            raise ValueError("provenance kind is invalid")
        for field in ("evidence_id", "revision"):
            _required(getattr(self, field), f"provenance {field}")
        if not isinstance(self.availability, EvidenceAvailability):
            raise ValueError("provenance availability is invalid")
        if self.availability is EvidenceAvailability.AVAILABLE:
            _hash(self.content_hash, "provenance content_hash")
        elif self.content_hash is not None or not self.reason:
            raise ValueError(
                "unavailable provenance requires a reason and no content hash"
            )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProvenanceEvidence":
        values = dict(value)
        values["kind"] = ProvenanceKind(values.get("kind"))
        values["availability"] = EvidenceAvailability(values.get("availability"))
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        result = dict(self.__dict__)
        result["kind"] = self.kind.value
        result["availability"] = self.availability.value
        return result


@dataclass(frozen=True)
class QualityScore:
    metric_id: str
    evaluator_revision: str
    evidence_content_hash: str
    score: str

    def __post_init__(self) -> None:
        _required(self.metric_id, "quality metric_id")
        _required(self.evaluator_revision, "quality evaluator_revision")
        _hash(self.evidence_content_hash, "quality evidence_content_hash")
        _rate(self.score, "quality score")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "QualityScore":
        return cls(**value)

    def to_dict(self) -> dict[str, str]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class AcceptanceAssertion:
    method: AssertionMethod
    reviewer_id: str
    evidence_id: str
    evidence_revision: str
    evidence_content_hash: str
    decision: AcceptanceDecision
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.method, AssertionMethod):
            raise ValueError("acceptance assertion method is invalid")
        if not isinstance(self.decision, AcceptanceDecision):
            raise ValueError("acceptance assertion decision is invalid")
        for field in (
            "reviewer_id",
            "evidence_id",
            "evidence_revision",
            "reason",
        ):
            _required(getattr(self, field), f"acceptance assertion {field}")
        _hash(self.evidence_content_hash, "assertion evidence_content_hash")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AcceptanceAssertion":
        values = dict(value)
        values["method"] = AssertionMethod(values.get("method"))
        values["decision"] = AcceptanceDecision(values.get("decision"))
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        result = dict(self.__dict__)
        result["method"] = self.method.value
        result["decision"] = self.decision.value
        return result


@dataclass(frozen=True)
class RouteAcceptanceOutcome:
    schema_version: str
    outcome_id: str
    pack_id: str
    pack_version: str
    pack_content_hash: str
    workload_id: str
    workload_version: str
    route_id: str
    task_id: str
    trajectory_id: str
    segment_id: str
    segment_version: str
    operational_outcome: OperationalOutcome
    acceptance_decision: AcceptanceDecision
    evidence_availability: EvidenceAvailability
    reason_code: str
    sample: SampleEvidence | None
    sample_sufficient: bool
    provenance: tuple[ProvenanceEvidence, ...]
    quality_scores: tuple[QualityScore, ...]
    assertions: tuple[AcceptanceAssertion, ...]
    evaluated_at: str

    def __post_init__(self) -> None:
        if self.schema_version != ROUTE_ACCEPTANCE_OUTCOME_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {ROUTE_ACCEPTANCE_OUTCOME_SCHEMA_VERSION}"
            )
        for field in (
            "outcome_id",
            "pack_id",
            "pack_version",
            "workload_id",
            "workload_version",
            "route_id",
            "task_id",
            "trajectory_id",
            "segment_id",
            "segment_version",
            "reason_code",
        ):
            _required(getattr(self, field), field)
        _hash(self.pack_content_hash, "pack_content_hash")
        if not isinstance(self.operational_outcome, OperationalOutcome):
            raise ValueError("operational_outcome is invalid")
        if not isinstance(self.acceptance_decision, AcceptanceDecision):
            raise ValueError("acceptance_decision is invalid")
        if not isinstance(self.evidence_availability, EvidenceAvailability):
            raise ValueError("evidence_availability is invalid")
        _utc(self.evaluated_at, "evaluated_at")
        kinds = [item.kind for item in self.provenance]
        if len(kinds) != len(set(kinds)):
            raise ValueError("acceptance provenance kinds must be unique")
        assertion_ids = [item.evidence_id for item in self.assertions]
        if len(assertion_ids) != len(set(assertion_ids)):
            raise ValueError("acceptance assertion evidence IDs must be unique")
        if (
            self.evidence_availability is EvidenceAvailability.UNAVAILABLE
            and self.acceptance_decision is not AcceptanceDecision.INCONCLUSIVE
        ):
            raise ValueError("unavailable acceptance evidence must be inconclusive")
        if (
            self.acceptance_decision
            in {AcceptanceDecision.ACCEPTED, AcceptanceDecision.REJECTED}
            and not self.assertions
        ):
            raise ValueError("accepted or rejected outcomes require explicit assertions")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RouteAcceptanceOutcome":
        values = dict(value)
        values["operational_outcome"] = OperationalOutcome(
            values.get("operational_outcome")
        )
        values["acceptance_decision"] = AcceptanceDecision(
            values.get("acceptance_decision")
        )
        values["evidence_availability"] = EvidenceAvailability(
            values.get("evidence_availability")
        )
        sample = values.get("sample")
        values["sample"] = SampleEvidence.from_dict(sample) if sample else None
        values["provenance"] = tuple(
            ProvenanceEvidence.from_dict(item)
            for item in values.get("provenance", ())
        )
        values["quality_scores"] = tuple(
            QualityScore.from_dict(item) for item in values.get("quality_scores", ())
        )
        values["assertions"] = tuple(
            AcceptanceAssertion.from_dict(item)
            for item in values.get("assertions", ())
        )
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "outcome_id": self.outcome_id,
            "pack_id": self.pack_id,
            "pack_version": self.pack_version,
            "pack_content_hash": self.pack_content_hash,
            "workload_id": self.workload_id,
            "workload_version": self.workload_version,
            "route_id": self.route_id,
            "task_id": self.task_id,
            "trajectory_id": self.trajectory_id,
            "segment_id": self.segment_id,
            "segment_version": self.segment_version,
            "operational_outcome": self.operational_outcome.value,
            "acceptance_decision": self.acceptance_decision.value,
            "evidence_availability": self.evidence_availability.value,
            "reason_code": self.reason_code,
            "sample": self.sample.to_dict() if self.sample else None,
            "sample_sufficient": self.sample_sufficient,
            "provenance": [item.to_dict() for item in self.provenance],
            "quality_scores": [item.to_dict() for item in self.quality_scores],
            "assertions": [item.to_dict() for item in self.assertions],
            "evaluated_at": self.evaluated_at,
        }

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict()).encode()).hexdigest()


def record_route_acceptance(
    pack: WorkloadAcceptancePack,
    *,
    route_id: str,
    task_id: str,
    trajectory_id: str,
    segment_id: str,
    segment_version: str,
    operational_outcome: OperationalOutcome,
    sample: SampleEvidence | None,
    provenance: tuple[ProvenanceEvidence, ...],
    quality_scores: tuple[QualityScore, ...],
    assertions: tuple[AcceptanceAssertion, ...],
    evaluated_at: str,
) -> RouteAcceptanceOutcome:
    """Create an explicit outcome; raw scores and product completion never decide it."""
    if route_id not in pack.route_ids:
        raise ValueError("acceptance pack does not apply to this route")
    requirement = pack.requirement_for(segment_id, segment_version)
    provenance_by_kind = {item.kind: item for item in provenance}
    missing = [
        kind
        for kind in requirement.required_provenance
        if kind not in provenance_by_kind
        or provenance_by_kind[kind].availability is EvidenceAvailability.UNAVAILABLE
    ]
    sample_sufficient = bool(
        sample
        and sample.representative
        and sample.segment_completed_samples >= requirement.minimum_segment_samples
    )
    explicit_decisions = {
        item.decision
        for item in assertions
        if item.decision in {AcceptanceDecision.ACCEPTED, AcceptanceDecision.REJECTED}
    }
    has_inconclusive_assertion = any(
        item.decision is AcceptanceDecision.INCONCLUSIVE for item in assertions
    )
    if sample is None or missing:
        decision = AcceptanceDecision.INCONCLUSIVE
        availability = EvidenceAvailability.UNAVAILABLE
        reason = (
            "sample_evidence_unavailable"
            if sample is None
            else "required_provenance_unavailable"
        )
    elif not sample_sufficient:
        decision = AcceptanceDecision.INCONCLUSIVE
        availability = EvidenceAvailability.AVAILABLE
        reason = "insufficient_segment_sample"
    elif operational_outcome is OperationalOutcome.UNAVAILABLE:
        decision = AcceptanceDecision.INCONCLUSIVE
        availability = EvidenceAvailability.UNAVAILABLE
        reason = "operational_evidence_unavailable"
    elif len(assertions) < requirement.minimum_task_assertions:
        decision = AcceptanceDecision.INCONCLUSIVE
        availability = EvidenceAvailability.AVAILABLE
        reason = "missing_explicit_acceptance_assertion"
    elif has_inconclusive_assertion or len(explicit_decisions) != 1:
        decision = AcceptanceDecision.INCONCLUSIVE
        availability = EvidenceAvailability.AVAILABLE
        reason = (
            "ambiguous_acceptance_assertions"
            if explicit_decisions or has_inconclusive_assertion
            else "no_explicit_acceptance_assertion"
        )
    else:
        decision = next(iter(explicit_decisions))
        availability = EvidenceAvailability.AVAILABLE
        reason = f"explicit_{decision.value}_assertion"
        if (
            decision is AcceptanceDecision.ACCEPTED
            and operational_outcome is not OperationalOutcome.COMPLETED
        ):
            decision = AcceptanceDecision.INCONCLUSIVE
            reason = "operational_outcome_conflicts_with_acceptance"
    outcome_key = ":".join(
        (pack.pack_id, pack.version, route_id, task_id, trajectory_id)
    )
    outcome_id = "route-acceptance-" + hashlib.sha256(
        outcome_key.encode()
    ).hexdigest()[:32]
    return RouteAcceptanceOutcome(
        schema_version=ROUTE_ACCEPTANCE_OUTCOME_SCHEMA_VERSION,
        outcome_id=outcome_id,
        pack_id=pack.pack_id,
        pack_version=pack.version,
        pack_content_hash=pack.content_hash,
        workload_id=pack.workload_id,
        workload_version=pack.workload_version,
        route_id=route_id,
        task_id=task_id,
        trajectory_id=trajectory_id,
        segment_id=segment_id,
        segment_version=segment_version,
        operational_outcome=operational_outcome,
        acceptance_decision=decision,
        evidence_availability=availability,
        reason_code=reason,
        sample=sample,
        sample_sufficient=sample_sufficient,
        provenance=provenance,
        quality_scores=quality_scores,
        assertions=assertions,
        evaluated_at=evaluated_at,
    )


def evaluate_segment_acceptance_floors(
    pack: WorkloadAcceptancePack,
    outcomes: Iterable[RouteAcceptanceOutcome],
) -> dict[str, Any]:
    """Evaluate workload quality floors from explicit task outcomes by segment."""
    rows = tuple(outcomes)
    if not rows:
        raise ValueError("segment acceptance floor evaluation requires outcomes")
    if len({row.outcome_id for row in rows}) != len(rows):
        raise ValueError("acceptance outcome IDs must be unique")
    route_ids = {row.route_id for row in rows}
    if len(route_ids) != 1 or not route_ids.issubset(set(pack.route_ids)):
        raise ValueError("outcomes must use one route covered by the acceptance pack")
    if any(
        row.pack_id != pack.pack_id
        or row.pack_version != pack.version
        or row.pack_content_hash != pack.content_hash
        for row in rows
    ):
        raise ValueError("outcome acceptance-pack binding does not match")
    requirement_keys = {
        (requirement.segment_id, requirement.segment_version)
        for requirement in pack.requirements
    }
    if any(
        (row.segment_id, row.segment_version) not in requirement_keys for row in rows
    ):
        raise ValueError("outcome segment is not defined by the acceptance pack")

    segment_results = []
    for requirement in pack.requirements:
        segment_rows = tuple(
            row
            for row in rows
            if row.segment_id == requirement.segment_id
            and row.segment_version == requirement.segment_version
            and row.acceptance_decision
            in {AcceptanceDecision.ACCEPTED, AcceptanceDecision.REJECTED}
        )
        accepted = sum(
            row.acceptance_decision is AcceptanceDecision.ACCEPTED
            for row in segment_rows
        )
        observed_rate = (
            Decimal(accepted) / Decimal(len(segment_rows)) if segment_rows else None
        )
        minimum_rate = Decimal(requirement.minimum_acceptance_rate)
        if len(segment_rows) < requirement.minimum_segment_samples:
            status = "inconclusive"
            reason = "insufficient_explicit_segment_outcomes"
        elif observed_rate is not None and observed_rate < minimum_rate:
            status = "failed"
            reason = "segment_acceptance_floor_breached"
        else:
            status = "passed"
            reason = "segment_acceptance_floor_satisfied"
        segment_results.append(
            {
                "segment_id": requirement.segment_id,
                "segment_version": requirement.segment_version,
                "status": status,
                "reason_code": reason,
                "explicit_outcome_count": len(segment_rows),
                "accepted_count": accepted,
                "observed_acceptance_rate": (
                    format(observed_rate, "f") if observed_rate is not None else None
                ),
                "minimum_segment_samples": requirement.minimum_segment_samples,
                "minimum_acceptance_rate": requirement.minimum_acceptance_rate,
            }
        )

    statuses = {row["status"] for row in segment_results}
    overall = (
        "failed"
        if "failed" in statuses
        else "inconclusive"
        if "inconclusive" in statuses
        else "passed"
    )
    payload = {
        "pack_id": pack.pack_id,
        "pack_version": pack.version,
        "pack_content_hash": pack.content_hash,
        "route_id": next(iter(route_ids)),
        "status": overall,
        "segments": segment_results,
    }
    return {
        **payload,
        "content_hash": hashlib.sha256(_canonical(payload).encode()).hexdigest(),
    }


@dataclass(frozen=True)
class AcceptancePackRecord:
    content_hash: str
    pack: WorkloadAcceptancePack


@dataclass(frozen=True)
class RouteAcceptanceRecord:
    content_hash: str
    outcome: RouteAcceptanceOutcome


class _ImmutableStore:
    def __init__(self, root: str | Path, kind: str) -> None:
        self.root = Path(root)
        self.kind = kind

    def path(self, identity: str) -> Path:
        name = hashlib.sha256(_required(identity, f"{self.kind} identity").encode())
        return self.root / f"{name.hexdigest()}.json"

    def write(self, identity: str, content_hash: str, value: Mapping[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path(identity)
        temporary = self.root / f".{path.stem}.{uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(
                    {"content_hash": content_hash, self.kind: value},
                    stream,
                    allow_nan=False,
                    indent=2,
                    sort_keys=True,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            publish_immutable(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


class AcceptancePackStore:
    def __init__(self, root: str | Path) -> None:
        self._store = _ImmutableStore(root, "pack")

    def record(self, pack: WorkloadAcceptancePack) -> AcceptancePackRecord:
        identity = f"{pack.pack_id}:{pack.version}"
        existing = self.get(pack.pack_id, pack.version)
        if existing:
            if existing.content_hash == pack.content_hash:
                return existing
            raise ValueError("acceptance pack replay conflicts with immutable evidence")
        try:
            self._store.write(identity, pack.content_hash, pack.to_dict())
        except FileExistsError:
            existing = self.get(pack.pack_id, pack.version)
            if existing and existing.content_hash == pack.content_hash:
                return existing
            raise ValueError("acceptance pack replay conflicts with immutable evidence")
        return AcceptancePackRecord(pack.content_hash, pack)

    def get(self, pack_id: str, version: str) -> AcceptancePackRecord | None:
        path = self._store.path(f"{pack_id}:{version}")
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            pack = WorkloadAcceptancePack.from_dict(payload["pack"])
            content_hash = _hash(payload["content_hash"], "pack content_hash")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("acceptance pack integrity check failed") from exc
        if (
            pack.pack_id != pack_id
            or pack.version != version
            or pack.content_hash != content_hash
        ):
            raise ValueError("acceptance pack integrity check failed")
        return AcceptancePackRecord(content_hash, pack)


class RouteAcceptanceStore:
    def __init__(self, root: str | Path) -> None:
        self._store = _ImmutableStore(root, "outcome")

    def record(self, outcome: RouteAcceptanceOutcome) -> RouteAcceptanceRecord:
        existing = self.get(outcome.outcome_id)
        if existing:
            if existing.content_hash == outcome.content_hash:
                return existing
            raise ValueError("acceptance outcome replay conflicts with immutable evidence")
        try:
            self._store.write(
                outcome.outcome_id, outcome.content_hash, outcome.to_dict()
            )
        except FileExistsError:
            existing = self.get(outcome.outcome_id)
            if existing and existing.content_hash == outcome.content_hash:
                return existing
            raise ValueError("acceptance outcome replay conflicts with immutable evidence")
        return RouteAcceptanceRecord(outcome.content_hash, outcome)

    def get(self, outcome_id: str) -> RouteAcceptanceRecord | None:
        path = self._store.path(outcome_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            outcome = RouteAcceptanceOutcome.from_dict(payload["outcome"])
            content_hash = _hash(payload["content_hash"], "outcome content_hash")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("acceptance outcome integrity check failed") from exc
        if outcome.outcome_id != outcome_id or outcome.content_hash != content_hash:
            raise ValueError("acceptance outcome integrity check failed")
        return RouteAcceptanceRecord(content_hash, outcome)
