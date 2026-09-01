"""Immutable decision-grade candidate constraints and Govern selections."""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from statistics import NormalDist
from typing import Any, Mapping, Sequence
from uuid import uuid4

DECISION_CONSTRAINT_SCHEMA_VERSION = "decision-constraint.v1"
GOVERN_DECISION_SCHEMA_VERSION = "govern-decision.v1"


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


def _continued_fraction_beta(a: float, b: float, x: float) -> float:
    maximum_iterations = 300
    epsilon = 3e-14
    minimum = 1e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < minimum:
        d = minimum
    d = 1.0 / d
    result = d
    for iteration in range(1, maximum_iterations + 1):
        m2 = 2 * iteration
        numerator = iteration * (b - iteration) * x / (
            (qam + m2) * (a + m2)
        )
        d = 1.0 + numerator * d
        if abs(d) < minimum:
            d = minimum
        c = 1.0 + numerator / c
        if abs(c) < minimum:
            c = minimum
        d = 1.0 / d
        result *= d * c
        numerator = -(a + iteration) * (qab + iteration) * x / (
            (a + m2) * (qap + m2)
        )
        d = 1.0 + numerator * d
        if abs(d) < minimum:
            d = minimum
        c = 1.0 + numerator / c
        if abs(c) < minimum:
            c = minimum
        d = 1.0 / d
        delta = d * c
        result *= delta
        if abs(delta - 1.0) <= epsilon:
            return result
    raise ArithmeticError("incomplete beta calculation did not converge")


def _regularized_beta(x: float, a: float, b: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    scale = math.exp(
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return scale * _continued_fraction_beta(a, b, x) / a
    return 1.0 - scale * _continued_fraction_beta(b, a, 1.0 - x) / b


def _beta_quantile(probability: float, a: float, b: float) -> float:
    low, high = 0.0, 1.0
    for _ in range(100):
        midpoint = (low + high) / 2.0
        if _regularized_beta(midpoint, a, b) < probability:
            low = midpoint
        else:
            high = midpoint
    return (low + high) / 2.0


def clopper_pearson_upper(
    successes: int, trials: int, confidence_level: float
) -> float:
    """Return a one-sided exact binomial upper confidence bound."""
    if trials < 1 or not 0 <= successes <= trials:
        raise ValueError("successes must be between zero and positive trials")
    if not 0 < confidence_level < 1:
        raise ValueError("confidence_level must be between zero and one")
    if successes == trials:
        return 1.0
    return _beta_quantile(
        confidence_level,
        successes + 1,
        trials - successes,
    )


def wilson_lower(successes: int, trials: int, confidence_level: float) -> float:
    """Return a one-sided Wilson score lower confidence bound."""
    if trials < 1 or not 0 <= successes <= trials:
        raise ValueError("successes must be between zero and positive trials")
    if not 0 < confidence_level < 1:
        raise ValueError("confidence_level must be between zero and one")
    z = NormalDist().inv_cdf(confidence_level)
    proportion = successes / trials
    denominator = 1.0 + z * z / trials
    center = proportion + z * z / (2.0 * trials)
    margin = z * math.sqrt(
        proportion * (1.0 - proportion) / trials
        + z * z / (4.0 * trials * trials)
    )
    return max(0.0, (center - margin) / denominator)


class ConstraintOutcome(str, Enum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class SegmentConstraint:
    segment_id: str
    segment_version: str
    sample_count: int
    accepted_count: int
    budget_breach_count: int
    expected_allocatable_cost_usd: float | None
    budget_usd: float
    breach_tolerance: float
    minimum_samples: int
    confidence_level: float
    minimum_acceptance_lower_bound: float
    empirical_breach_probability: float | None
    breach_probability_upper_bound: float | None
    acceptance_rate: float | None
    acceptance_wilson_lower_bound: float | None
    outcome: ConstraintOutcome
    reason_codes: tuple[str, ...]
    acceptance_evidence_hashes: tuple[str, ...]
    cost_evidence_hashes: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: object) -> "SegmentConstraint":
        if not isinstance(value, Mapping):
            raise ValueError("segment constraint must be an object")
        values = dict(value)
        values["outcome"] = ConstraintOutcome(values.get("outcome"))
        for field in (
            "reason_codes",
            "acceptance_evidence_hashes",
            "cost_evidence_hashes",
        ):
            raw = values.get(field)
            if not isinstance(raw, list):
                raise ValueError(f"{field} must be an array")
            values[field] = tuple(raw)
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        result = dict(self.__dict__)
        result["outcome"] = self.outcome.value
        result["reason_codes"] = list(self.reason_codes)
        result["acceptance_evidence_hashes"] = list(
            self.acceptance_evidence_hashes
        )
        result["cost_evidence_hashes"] = list(self.cost_evidence_hashes)
        return result


def evaluate_segment_constraint(
    *,
    segment_id: str,
    segment_version: str,
    acceptance_decisions: Sequence[str],
    allocatable_costs_usd: Sequence[float | None],
    acceptance_evidence_hashes: Sequence[str],
    cost_evidence_hashes: Sequence[str],
    budget_usd: float = 0.02,
    breach_tolerance: float = 0.05,
    minimum_samples: int = 60,
    confidence_level: float = 0.95,
    minimum_acceptance_lower_bound: float = 0.80,
) -> SegmentConstraint:
    _required(segment_id, "segment_id")
    _required(segment_version, "segment_version")
    if len(acceptance_decisions) != len(allocatable_costs_usd):
        raise ValueError("acceptance and cost observations must align by task")
    if len(acceptance_evidence_hashes) != len(acceptance_decisions):
        raise ValueError("each acceptance observation requires an evidence hash")
    if len(cost_evidence_hashes) != len(allocatable_costs_usd):
        raise ValueError("each cost observation requires an evidence hash")
    for evidence_hash in (*acceptance_evidence_hashes, *cost_evidence_hashes):
        _hash(evidence_hash, "observation evidence hash")
    if budget_usd <= 0 or not 0 < breach_tolerance < 1:
        raise ValueError("budget and breach tolerance must be positive")
    if minimum_samples < 1:
        raise ValueError("minimum_samples must be positive")
    valid_decisions = {"accepted", "rejected", "inconclusive"}
    if any(item not in valid_decisions for item in acceptance_decisions):
        raise ValueError("acceptance decision is invalid")
    priced_costs = [
        float(item) for item in allocatable_costs_usd if item is not None
    ]
    if any(not math.isfinite(item) or item < 0 for item in priced_costs):
        raise ValueError("allocatable costs must be finite and non-negative")

    samples = len(acceptance_decisions)
    accepted = sum(item == "accepted" for item in acceptance_decisions)
    conclusive = sum(item != "inconclusive" for item in acceptance_decisions)
    breaches = sum(item > budget_usd for item in priced_costs)
    reasons: list[str] = []
    if samples < minimum_samples:
        reasons.append("insufficient_segment_samples")
    if conclusive != samples:
        reasons.append("inconclusive_acceptance_observations")
    if len(priced_costs) != samples:
        reasons.append("incomplete_priced_cost_coverage")

    breach_estimate = breaches / samples if samples else None
    breach_upper = (
        clopper_pearson_upper(breaches, samples, confidence_level)
        if samples and len(priced_costs) == samples
        else None
    )
    acceptance_rate = accepted / samples if samples else None
    acceptance_lower = (
        wilson_lower(accepted, samples, confidence_level)
        if samples and conclusive == samples
        else None
    )
    if not reasons:
        if breach_upper is None or breach_upper > breach_tolerance:
            reasons.append("monetary_tail_constraint_failed")
        if (
            acceptance_lower is None
            or acceptance_lower < minimum_acceptance_lower_bound
        ):
            reasons.append("acceptance_quality_constraint_failed")
    if any(
        reason
        in {
            "insufficient_segment_samples",
            "inconclusive_acceptance_observations",
            "incomplete_priced_cost_coverage",
        }
        for reason in reasons
    ):
        outcome = ConstraintOutcome.INCONCLUSIVE
    elif reasons:
        outcome = ConstraintOutcome.INELIGIBLE
    else:
        outcome = ConstraintOutcome.ELIGIBLE
        reasons.append("all_segment_constraints_satisfied")
    return SegmentConstraint(
        segment_id=segment_id,
        segment_version=segment_version,
        sample_count=samples,
        accepted_count=accepted,
        budget_breach_count=breaches,
        expected_allocatable_cost_usd=(
            sum(priced_costs) / len(priced_costs) if priced_costs else None
        ),
        budget_usd=budget_usd,
        breach_tolerance=breach_tolerance,
        minimum_samples=minimum_samples,
        confidence_level=confidence_level,
        minimum_acceptance_lower_bound=minimum_acceptance_lower_bound,
        empirical_breach_probability=breach_estimate,
        breach_probability_upper_bound=breach_upper,
        acceptance_rate=acceptance_rate,
        acceptance_wilson_lower_bound=acceptance_lower,
        outcome=outcome,
        reason_codes=tuple(reasons),
        acceptance_evidence_hashes=tuple(acceptance_evidence_hashes),
        cost_evidence_hashes=tuple(cost_evidence_hashes),
    )


def build_candidate_constraint_from_run(
    run_result: Mapping[str, Any],
    run_root: str | Path,
    *,
    budget_usd: float = 0.02,
    breach_tolerance: float = 0.05,
    minimum_samples: int = 60,
    confidence_level: float = 0.95,
    minimum_acceptance_lower_bound: float = 0.80,
) -> "CandidateConstraintEvidence":
    """Derive one candidate constraint from verified complete-task evidence."""
    from .meter_ledger import CostCoverage
    from .observe_economics import load_verified_run_evidence

    trajectories, outcomes, entries = load_verified_run_evidence(
        dict(run_result), run_root
    )
    if not trajectories:
        raise ValueError("candidate constraint requires completed trajectories")

    trajectory_by_task = {}
    for trajectory in trajectories:
        task_id = trajectory.task.task_id
        if task_id in trajectory_by_task:
            raise ValueError("candidate constraint requires one trajectory per task")
        if trajectory.status != "completed":
            raise ValueError("candidate constraint requires completed tasks")
        trajectory_by_task[task_id] = trajectory
    outcome_by_task = {}
    for outcome in outcomes:
        if outcome.task_id in outcome_by_task:
            raise ValueError("candidate constraint requires one outcome per task")
        outcome_by_task[outcome.task_id] = outcome
    entry_by_task = {}
    for entry in entries:
        entry_by_task.setdefault(entry.task_id, []).append(entry)
    if set(outcome_by_task) != set(trajectory_by_task):
        raise ValueError(
            "candidate constraint requires one explicit acceptance outcome per task"
        )
    if set(entry_by_task) != set(trajectory_by_task):
        raise ValueError(
            "candidate constraint requires meter-ledger evidence for every task"
        )

    first_outcome = outcomes[0]
    candidate_binding = (
        first_outcome.policy_candidate_id,
        first_outcome.policy_candidate_version,
        first_outcome.policy_candidate_content_hash,
        first_outcome.experiment_id,
        first_outcome.experiment_revision,
        first_outcome.arm_id,
    )
    if any(
        (
            item.policy_candidate_id,
            item.policy_candidate_version,
            item.policy_candidate_content_hash,
            item.experiment_id,
            item.experiment_revision,
            item.arm_id,
        )
        != candidate_binding
        for item in outcomes
    ):
        raise ValueError("run evidence contains mixed candidate bindings")
    if any(
        (
            item.policy_candidate_id,
            item.policy_candidate_version,
            item.policy_candidate_content_hash,
            item.experiment_id,
            item.experiment_revision,
            item.arm_id,
        )
        != candidate_binding
        for item in entries
    ):
        raise ValueError("meter evidence does not match the candidate binding")

    acceptance_hashes = {
        item["task_id"]: item["content_hash"]
        for item in run_result.get("acceptance_outcomes", [])
    }
    meter_hashes = {
        item["entry_id"]: item["content_hash"]
        for item in run_result.get("meter_ledger_evidence", [])
    }
    segments = []
    segment_keys = sorted(
        {
            (
                trajectory.task.segment.segment_id,
                trajectory.task.segment.version,
            )
            for trajectory in trajectories
        }
    )
    for segment_id, segment_version in segment_keys:
        task_ids = sorted(
            task_id
            for task_id, trajectory in trajectory_by_task.items()
            if trajectory.task.segment.segment_id == segment_id
            and trajectory.task.segment.version == segment_version
        )
        decisions = []
        costs = []
        acceptance_evidence = []
        cost_evidence = []
        for task_id in task_ids:
            outcome = outcome_by_task[task_id]
            decisions.append(outcome.decision.value)
            acceptance_evidence.append(
                _hash(
                    acceptance_hashes.get(task_id),
                    f"acceptance hash for {task_id}",
                )
            )
            task_entries = sorted(
                entry_by_task[task_id], key=lambda item: item.entry_id
            )
            entry_hashes = [
                _hash(
                    meter_hashes.get(item.entry_id),
                    f"meter hash for {item.entry_id}",
                )
                for item in task_entries
            ]
            cost_evidence.append(
                hashlib.sha256(_canonical(entry_hashes).encode()).hexdigest()
            )
            if all(
                item.cost_coverage
                in {CostCoverage.PRICED, CostCoverage.NOT_APPLICABLE}
                for item in task_entries
            ):
                costs.append(
                    sum(
                        float(item.allocated_cost_usd or 0.0)
                        for item in task_entries
                        if item.cost_coverage is CostCoverage.PRICED
                    )
                )
            else:
                costs.append(None)
        segments.append(
            evaluate_segment_constraint(
                segment_id=segment_id,
                segment_version=segment_version,
                acceptance_decisions=decisions,
                allocatable_costs_usd=costs,
                acceptance_evidence_hashes=acceptance_evidence,
                cost_evidence_hashes=cost_evidence,
                budget_usd=budget_usd,
                breach_tolerance=breach_tolerance,
                minimum_samples=minimum_samples,
                confidence_level=confidence_level,
                minimum_acceptance_lower_bound=minimum_acceptance_lower_bound,
            )
        )

    candidate_id, candidate_version, candidate_hash, experiment_id, revision, arm_id = (
        candidate_binding
    )
    run_id = _required(run_result.get("run_id"), "run_id")
    return CandidateConstraintEvidence(
        schema_version=DECISION_CONSTRAINT_SCHEMA_VERSION,
        constraint_id=f"constraint-{run_id}",
        experiment_id=experiment_id,
        experiment_revision=revision,
        arm_id=arm_id,
        candidate_id=candidate_id,
        candidate_version=candidate_version,
        candidate_content_hash=candidate_hash,
        observation_unit="completed_task",
        evaluated_at=max(item.evaluated_at for item in outcomes),
        evidence_classification=_required(
            run_result.get("evidence_classification"),
            "evidence_classification",
        ),
        probability_model=(
            "Representative completed tasks are IID Bernoulli trials for "
            "C_task > B; x/n is descriptive and eligibility uses a one-sided "
            "exact Clopper-Pearson upper bound. Acceptance eligibility uses a "
            "separate one-sided Wilson lower bound."
        ),
        segments=tuple(segments),
    )


@dataclass(frozen=True)
class CandidateConstraintEvidence:
    schema_version: str
    constraint_id: str
    experiment_id: str
    experiment_revision: str
    arm_id: str
    candidate_id: str
    candidate_version: str
    candidate_content_hash: str
    observation_unit: str
    evaluated_at: str
    evidence_classification: str
    probability_model: str
    segments: tuple[SegmentConstraint, ...]

    def __post_init__(self) -> None:
        if self.schema_version != DECISION_CONSTRAINT_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {DECISION_CONSTRAINT_SCHEMA_VERSION}"
            )
        for field in (
            "constraint_id",
            "experiment_id",
            "experiment_revision",
            "arm_id",
            "candidate_id",
            "candidate_version",
            "observation_unit",
            "evidence_classification",
            "probability_model",
        ):
            _required(getattr(self, field), field)
        _hash(self.candidate_content_hash, "candidate_content_hash")
        _utc(self.evaluated_at, "evaluated_at")
        if self.observation_unit != "completed_task":
            raise ValueError("decision constraints require completed_task evidence")
        if not self.segments:
            raise ValueError("at least one material segment is required")
        if len({item.segment_id for item in self.segments}) != len(self.segments):
            raise ValueError("segment constraints must be unique")

    @classmethod
    def from_dict(cls, value: object) -> "CandidateConstraintEvidence":
        if not isinstance(value, Mapping):
            raise ValueError("candidate constraint evidence must be an object")
        values = dict(value)
        raw_segments = values.get("segments")
        if not isinstance(raw_segments, list):
            raise ValueError("segments must be an array")
        values["segments"] = tuple(
            SegmentConstraint.from_dict(item) for item in raw_segments
        )
        values.pop("outcome", None)
        values.pop("expected_allocatable_cost_usd", None)
        return cls(**values)

    @property
    def outcome(self) -> ConstraintOutcome:
        outcomes = {item.outcome for item in self.segments}
        if ConstraintOutcome.INELIGIBLE in outcomes:
            return ConstraintOutcome.INELIGIBLE
        if ConstraintOutcome.INCONCLUSIVE in outcomes:
            return ConstraintOutcome.INCONCLUSIVE
        return ConstraintOutcome.ELIGIBLE

    @property
    def expected_allocatable_cost_usd(self) -> float | None:
        values = [
            item.expected_allocatable_cost_usd
            for item in self.segments
            if item.expected_allocatable_cost_usd is not None
        ]
        return sum(values) / len(values) if len(values) == len(self.segments) else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "constraint_id": self.constraint_id,
            "experiment_id": self.experiment_id,
            "experiment_revision": self.experiment_revision,
            "arm_id": self.arm_id,
            "candidate_id": self.candidate_id,
            "candidate_version": self.candidate_version,
            "candidate_content_hash": self.candidate_content_hash,
            "observation_unit": self.observation_unit,
            "evaluated_at": self.evaluated_at,
            "evidence_classification": self.evidence_classification,
            "probability_model": self.probability_model,
            "outcome": self.outcome.value,
            "expected_allocatable_cost_usd": self.expected_allocatable_cost_usd,
            "segments": [item.to_dict() for item in self.segments],
        }

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict()).encode()).hexdigest()


class GovernOutcome(str, Enum):
    SELECTED = "selected"
    NONE_ELIGIBLE = "none_eligible"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class GovernDecision:
    schema_version: str
    decision_id: str
    experiment_id: str
    experiment_revision: str
    observation_unit: str
    created_at: str
    outcome: GovernOutcome
    selected_candidate_id: str | None
    selected_candidate_version: str | None
    selected_candidate_content_hash: str | None
    candidate_constraint_hashes: tuple[str, ...]
    candidates: tuple[CandidateConstraintEvidence, ...]
    reason_code: str
    mutation_performed: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != GOVERN_DECISION_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {GOVERN_DECISION_SCHEMA_VERSION}"
            )
        for field in (
            "decision_id",
            "experiment_id",
            "experiment_revision",
            "observation_unit",
            "reason_code",
        ):
            _required(getattr(self, field), field)
        _utc(self.created_at, "created_at")
        if self.observation_unit != "completed_task":
            raise ValueError("Govern requires completed_task evidence")
        if self.mutation_performed:
            raise ValueError("Govern decision evidence cannot publish policy")
        if len(self.candidates) != len(self.candidate_constraint_hashes):
            raise ValueError("candidate evidence hashes do not align")
        for candidate, evidence_hash in zip(
            self.candidates, self.candidate_constraint_hashes
        ):
            _hash(evidence_hash, "candidate constraint hash")
            if candidate.content_hash != evidence_hash:
                raise ValueError("candidate constraint hash mismatch")
        if self.outcome is GovernOutcome.SELECTED:
            _required(self.selected_candidate_id, "selected_candidate_id")
            _required(self.selected_candidate_version, "selected_candidate_version")
            _hash(
                self.selected_candidate_content_hash,
                "selected_candidate_content_hash",
            )
        elif any(
            value is not None
            for value in (
                self.selected_candidate_id,
                self.selected_candidate_version,
                self.selected_candidate_content_hash,
            )
        ):
            raise ValueError("non-selected decisions cannot identify a candidate")

    @classmethod
    def from_dict(cls, value: object) -> "GovernDecision":
        if not isinstance(value, Mapping):
            raise ValueError("Govern decision must be an object")
        values = dict(value)
        values["outcome"] = GovernOutcome(values.get("outcome"))
        raw_candidates = values.get("candidates")
        if not isinstance(raw_candidates, list):
            raise ValueError("candidates must be an array")
        values["candidates"] = tuple(
            CandidateConstraintEvidence.from_dict(item)
            for item in raw_candidates
        )
        hashes = values.get("candidate_constraint_hashes")
        if not isinstance(hashes, list):
            raise ValueError("candidate_constraint_hashes must be an array")
        values["candidate_constraint_hashes"] = tuple(hashes)
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "decision_id": self.decision_id,
            "experiment_id": self.experiment_id,
            "experiment_revision": self.experiment_revision,
            "observation_unit": self.observation_unit,
            "created_at": self.created_at,
            "outcome": self.outcome.value,
            "selected_candidate_id": self.selected_candidate_id,
            "selected_candidate_version": self.selected_candidate_version,
            "selected_candidate_content_hash": self.selected_candidate_content_hash,
            "candidate_constraint_hashes": list(
                self.candidate_constraint_hashes
            ),
            "candidates": [item.to_dict() for item in self.candidates],
            "reason_code": self.reason_code,
            "mutation_performed": self.mutation_performed,
        }

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict()).encode()).hexdigest()


def select_candidate(
    candidates: Sequence[CandidateConstraintEvidence],
    *,
    created_at: str,
) -> GovernDecision:
    if not candidates:
        raise ValueError("at least one candidate is required")
    first = candidates[0]
    if any(
        item.experiment_id != first.experiment_id
        or item.experiment_revision != first.experiment_revision
        or item.observation_unit != first.observation_unit
        for item in candidates
    ):
        raise ValueError("candidate constraints are not comparable")
    eligible = [
        item
        for item in candidates
        if item.outcome is ConstraintOutcome.ELIGIBLE
        and item.expected_allocatable_cost_usd is not None
    ]
    if eligible:
        selected = min(
            eligible,
            key=lambda item: (
                item.expected_allocatable_cost_usd,
                item.candidate_id,
                item.candidate_version,
            ),
        )
        outcome = GovernOutcome.SELECTED
        reason = "least_expected_allocatable_cost_eligible_candidate"
        selected_values = (
            selected.candidate_id,
            selected.candidate_version,
            selected.candidate_content_hash,
        )
    elif any(
        item.outcome is ConstraintOutcome.INCONCLUSIVE for item in candidates
    ):
        outcome = GovernOutcome.INCONCLUSIVE
        reason = "insufficient_decision_evidence"
        selected_values = (None, None, None)
    else:
        outcome = GovernOutcome.NONE_ELIGIBLE
        reason = "no_candidate_satisfies_all_constraints"
        selected_values = (None, None, None)
    return GovernDecision(
        schema_version=GOVERN_DECISION_SCHEMA_VERSION,
        decision_id=f"govern-{uuid4().hex}",
        experiment_id=first.experiment_id,
        experiment_revision=first.experiment_revision,
        observation_unit=first.observation_unit,
        created_at=created_at,
        outcome=outcome,
        selected_candidate_id=selected_values[0],
        selected_candidate_version=selected_values[1],
        selected_candidate_content_hash=selected_values[2],
        candidate_constraint_hashes=tuple(
            item.content_hash for item in candidates
        ),
        candidates=tuple(candidates),
        reason_code=reason,
    )


@dataclass(frozen=True)
class ImmutableRecord:
    content_hash: str
    value: CandidateConstraintEvidence | GovernDecision


class GovernanceEvidenceStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def append(
        self, value: CandidateConstraintEvidence | GovernDecision
    ) -> ImmutableRecord:
        self.root.mkdir(parents=True, exist_ok=True)
        identity = (
            value.constraint_id
            if isinstance(value, CandidateConstraintEvidence)
            else value.decision_id
        )
        path = self.root / f"{hashlib.sha256(identity.encode()).hexdigest()}.json"
        temporary = self.root / f".{path.stem}.{uuid4().hex}.tmp"
        payload = {
            "kind": (
                "constraint"
                if isinstance(value, CandidateConstraintEvidence)
                else "govern_decision"
            ),
            "content_hash": value.content_hash,
            "value": value.to_dict(),
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
        return ImmutableRecord(value.content_hash, value)

    def get(self, identity: str) -> ImmutableRecord | None:
        path = self.root / f"{hashlib.sha256(_required(identity, 'identity').encode()).hexdigest()}.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            value = (
                CandidateConstraintEvidence.from_dict(payload["value"])
                if payload["kind"] == "constraint"
                else GovernDecision.from_dict(payload["value"])
            )
            content_hash = _hash(payload["content_hash"], "content_hash")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("governance evidence integrity check failed") from exc
        if value.content_hash != content_hash:
            raise ValueError("governance evidence integrity check failed")
        return ImmutableRecord(content_hash, value)

    def list(self, *, kind: str | None = None) -> list[ImmutableRecord]:
        if kind not in {None, "constraint", "govern_decision"}:
            raise ValueError("unsupported governance evidence kind")
        if not self.root.exists():
            return []
        records = []
        for path in sorted(self.root.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if kind is not None and payload.get("kind") != kind:
                continue
            value = payload.get("value") or {}
            identity = (
                value.get("constraint_id")
                if payload.get("kind") == "constraint"
                else value.get("decision_id")
            )
            record = self.get(identity)
            if record is None:
                raise ValueError("governance evidence integrity check failed")
            records.append(record)
        return sorted(
            records,
            key=lambda item: (
                item.value.evaluated_at
                if isinstance(item.value, CandidateConstraintEvidence)
                else item.value.created_at
            ),
            reverse=True,
        )
