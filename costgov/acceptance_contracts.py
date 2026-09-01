"""Explicit accepted-task rules and immutable outcomes."""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

ACCEPTANCE_RULE_SCHEMA_VERSION = "acceptance-rule.v1"
ACCEPTANCE_OUTCOME_SCHEMA_VERSION = "acceptance-outcome.v1"


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


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


class AcceptanceDecision(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


class ReviewMethod(str, Enum):
    AUTOMATED = "automated_evaluator"
    HUMAN = "human_review"


@dataclass(frozen=True)
class AcceptanceRule:
    schema_version: str
    rule_id: str
    version: str
    segment_id: str
    segment_version: str
    evaluator_id: str
    evaluator_version: str
    evaluator_content_hash: str
    minimum_score: float
    created_at: str

    def __post_init__(self) -> None:
        if self.schema_version != ACCEPTANCE_RULE_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {ACCEPTANCE_RULE_SCHEMA_VERSION}"
            )
        for field in (
            "rule_id",
            "version",
            "segment_id",
            "segment_version",
            "evaluator_id",
            "evaluator_version",
        ):
            _required(getattr(self, field), field)
        _hash(self.evaluator_content_hash, "evaluator_content_hash")
        if (
            isinstance(self.minimum_score, bool)
            or not isinstance(self.minimum_score, (int, float))
            or not math.isfinite(self.minimum_score)
            or not 0 <= self.minimum_score <= 1
        ):
            raise ValueError("minimum_score must be finite and between 0 and 1")
        _utc(self.created_at, "rule created_at")

    @classmethod
    def from_dict(cls, value: object) -> "AcceptanceRule":
        values = _mapping(value, "acceptance rule")
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "rule_id": self.rule_id,
            "version": self.version,
            "segment_id": self.segment_id,
            "segment_version": self.segment_version,
            "evaluator_id": self.evaluator_id,
            "evaluator_version": self.evaluator_version,
            "evaluator_content_hash": self.evaluator_content_hash,
            "minimum_score": self.minimum_score,
            "created_at": self.created_at,
        }

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.to_dict(), allow_nan=False, separators=(",", ":"), sort_keys=True
            ).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class ReviewEvidence:
    method: ReviewMethod
    reviewer_id: str
    evidence_id: str
    evidence_version: str
    evidence_content_hash: str
    score: float | None = None
    decision: AcceptanceDecision | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.method, ReviewMethod):
            raise ValueError("review method is invalid")
        for field in ("reviewer_id", "evidence_id", "evidence_version"):
            _required(getattr(self, field), field)
        _hash(self.evidence_content_hash, "review evidence content_hash")
        if self.score is not None and (
            isinstance(self.score, bool)
            or not isinstance(self.score, (int, float))
            or not math.isfinite(self.score)
            or not 0 <= self.score <= 1
        ):
            raise ValueError("review score must be finite and between 0 and 1")
        if self.decision is not None and not isinstance(
            self.decision, AcceptanceDecision
        ):
            raise ValueError("review decision is invalid")
        if self.method is ReviewMethod.AUTOMATED and self.decision is not None:
            raise ValueError("automated evidence records a score, not a decision")
        if self.method is ReviewMethod.HUMAN and self.decision is None:
            raise ValueError("human review requires an explicit decision")

    @classmethod
    def from_dict(cls, value: object) -> "ReviewEvidence":
        values = dict(_mapping(value, "review evidence"))
        values["method"] = ReviewMethod(values.get("method"))
        decision = values.get("decision")
        values["decision"] = AcceptanceDecision(decision) if decision else None
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method.value,
            "reviewer_id": self.reviewer_id,
            "evidence_id": self.evidence_id,
            "evidence_version": self.evidence_version,
            "evidence_content_hash": self.evidence_content_hash,
            "score": self.score,
            "decision": self.decision.value if self.decision else None,
        }


@dataclass(frozen=True)
class AcceptanceOutcome:
    schema_version: str
    outcome_id: str
    experiment_id: str
    experiment_revision: str
    arm_id: str
    policy_candidate_id: str
    policy_candidate_version: str
    policy_candidate_content_hash: str
    task_id: str
    trajectory_id: str
    segment_id: str
    segment_version: str
    rule_id: str
    rule_version: str
    rule_content_hash: str
    decision: AcceptanceDecision
    reason_code: str
    evaluated_at: str
    reviews: tuple[ReviewEvidence, ...]

    def __post_init__(self) -> None:
        if self.schema_version != ACCEPTANCE_OUTCOME_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {ACCEPTANCE_OUTCOME_SCHEMA_VERSION}"
            )
        for field in (
            "outcome_id",
            "experiment_id",
            "experiment_revision",
            "arm_id",
            "policy_candidate_id",
            "policy_candidate_version",
            "task_id",
            "trajectory_id",
            "segment_id",
            "segment_version",
            "rule_id",
            "rule_version",
            "reason_code",
        ):
            _required(getattr(self, field), field)
        _hash(self.rule_content_hash, "rule_content_hash")
        _hash(
            self.policy_candidate_content_hash,
            "policy_candidate_content_hash",
        )
        if not isinstance(self.decision, AcceptanceDecision):
            raise ValueError("acceptance decision is invalid")
        _utc(self.evaluated_at, "outcome evaluated_at")
        methods = [review.method for review in self.reviews]
        if len(methods) != len(set(methods)):
            raise ValueError("review methods must be unique")

    @classmethod
    def from_dict(cls, value: object) -> "AcceptanceOutcome":
        values = dict(_mapping(value, "acceptance outcome"))
        values["decision"] = AcceptanceDecision(values.get("decision"))
        raw_reviews = values.get("reviews")
        if not isinstance(raw_reviews, list):
            raise ValueError("acceptance reviews must be an array")
        values["reviews"] = tuple(ReviewEvidence.from_dict(item) for item in raw_reviews)
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "outcome_id": self.outcome_id,
            "experiment_id": self.experiment_id,
            "experiment_revision": self.experiment_revision,
            "arm_id": self.arm_id,
            "policy_candidate_id": self.policy_candidate_id,
            "policy_candidate_version": self.policy_candidate_version,
            "policy_candidate_content_hash": self.policy_candidate_content_hash,
            "task_id": self.task_id,
            "trajectory_id": self.trajectory_id,
            "segment_id": self.segment_id,
            "segment_version": self.segment_version,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "rule_content_hash": self.rule_content_hash,
            "decision": self.decision.value,
            "reason_code": self.reason_code,
            "evaluated_at": self.evaluated_at,
            "reviews": [review.to_dict() for review in self.reviews],
        }

    def to_canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(), allow_nan=False, separators=(",", ":"), sort_keys=True
        )


def evaluate_acceptance(
    rule: AcceptanceRule,
    *,
    experiment_id: str,
    experiment_revision: str,
    arm_id: str,
    policy_candidate_id: str,
    policy_candidate_version: str,
    policy_candidate_content_hash: str,
    task_id: str,
    trajectory_id: str,
    segment_id: str,
    segment_version: str,
    automated_review: ReviewEvidence | None = None,
    human_review: ReviewEvidence | None = None,
    evaluated_at: str,
) -> AcceptanceOutcome:
    if segment_id != rule.segment_id or segment_version != rule.segment_version:
        raise ValueError("acceptance rule does not match the task segment")
    if automated_review and automated_review.method is not ReviewMethod.AUTOMATED:
        raise ValueError("automated_review has the wrong method")
    if human_review and human_review.method is not ReviewMethod.HUMAN:
        raise ValueError("human_review has the wrong method")

    reviews = tuple(
        review for review in (automated_review, human_review) if review is not None
    )
    if human_review is not None:
        decision = human_review.decision
        reason = f"human_{decision.value}"
    elif automated_review is None or automated_review.score is None:
        decision = AcceptanceDecision.INCONCLUSIVE
        reason = "missing_evaluation_evidence"
    elif automated_review.score >= rule.minimum_score:
        decision = AcceptanceDecision.ACCEPTED
        reason = "automated_score_meets_rule"
    else:
        decision = AcceptanceDecision.REJECTED
        reason = "automated_score_below_rule"
    return AcceptanceOutcome(
        schema_version=ACCEPTANCE_OUTCOME_SCHEMA_VERSION,
        outcome_id=f"acceptance-{uuid4().hex}",
        experiment_id=experiment_id,
        experiment_revision=experiment_revision,
        arm_id=arm_id,
        policy_candidate_id=policy_candidate_id,
        policy_candidate_version=policy_candidate_version,
        policy_candidate_content_hash=policy_candidate_content_hash,
        task_id=task_id,
        trajectory_id=trajectory_id,
        segment_id=segment_id,
        segment_version=segment_version,
        rule_id=rule.rule_id,
        rule_version=rule.version,
        rule_content_hash=rule.content_hash,
        decision=decision,
        reason_code=reason,
        evaluated_at=evaluated_at,
        reviews=reviews,
    )


@dataclass(frozen=True)
class AcceptanceOutcomeRecord:
    content_hash: str
    outcome: AcceptanceOutcome


@dataclass(frozen=True)
class AcceptanceRuleRecord:
    content_hash: str
    rule: AcceptanceRule


class AcceptanceRuleStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, rule_id: str, version: str) -> Path:
        key = f"{_required(rule_id, 'rule_id')}:{_required(version, 'version')}"
        return self.root / f"{hashlib.sha256(key.encode()).hexdigest()}.json"

    def append(self, rule: AcceptanceRule) -> AcceptanceRuleRecord:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(rule.rule_id, rule.version)
        temporary = self.root / f".{path.stem}.{uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(
                    {"content_hash": rule.content_hash, "rule": rule.to_dict()},
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
        return AcceptanceRuleRecord(rule.content_hash, rule)

    def get(self, rule_id: str, version: str) -> AcceptanceRuleRecord | None:
        path = self._path(rule_id, version)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rule = AcceptanceRule.from_dict(payload["rule"])
            content_hash = _hash(payload["content_hash"], "rule content_hash")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("acceptance rule integrity check failed") from exc
        if (
            rule.rule_id != rule_id
            or rule.version != version
            or rule.content_hash != content_hash
        ):
            raise ValueError("acceptance rule integrity check failed")
        return AcceptanceRuleRecord(content_hash, rule)


class AcceptanceOutcomeStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, outcome_id: str) -> Path:
        return self.root / f"{hashlib.sha256(_required(outcome_id, 'outcome_id').encode()).hexdigest()}.json"

    def append(self, outcome: AcceptanceOutcome) -> AcceptanceOutcomeRecord:
        self.root.mkdir(parents=True, exist_ok=True)
        content_hash = hashlib.sha256(outcome.to_canonical_json().encode()).hexdigest()
        path = self._path(outcome.outcome_id)
        temporary = self.root / f".{path.stem}.{uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(
                    {"content_hash": content_hash, "outcome": outcome.to_dict()},
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
        return AcceptanceOutcomeRecord(content_hash, outcome)

    def get(self, outcome_id: str) -> AcceptanceOutcomeRecord | None:
        path = self._path(outcome_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            outcome = AcceptanceOutcome.from_dict(payload["outcome"])
            content_hash = _hash(payload["content_hash"], "outcome content_hash")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("acceptance outcome integrity check failed") from exc
        if (
            outcome.outcome_id != outcome_id
            or hashlib.sha256(outcome.to_canonical_json().encode()).hexdigest()
            != content_hash
        ):
            raise ValueError("acceptance outcome integrity check failed")
        return AcceptanceOutcomeRecord(content_hash, outcome)
