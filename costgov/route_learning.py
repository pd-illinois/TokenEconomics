"""Domain-isolated route learning with explicit evidence sufficiency gates."""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import uuid4

from .atomic_publish import publish_immutable

from .route_capabilities import ROUTE_CAPABILITY_PROFILES

ROUTE_LEARNING_SCHEMA_VERSION = "route-learning-receipt.v1"
FORECAST_LEARNING_LINK_SCHEMA_VERSION = "forecast-learning-link.v1"
_ROUTES = {profile.route_id for profile in ROUTE_CAPABILITY_PROFILES}


def _canonical(value: object) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _required(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value


def _hash(value: object, field: str) -> str:
    text = _required(value, field)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
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


class LearningDomain(str, Enum):
    MODEL_TOKENS = "model_tokens"
    COMMERCIAL_DEMAND = "commercial_demand"
    PURCHASE_UTILIZATION = "purchase_utilization"
    SEGMENT_ACCEPTANCE = "segment_acceptance"


_DOMAIN_TARGET = {
    LearningDomain.MODEL_TOKENS: "future_token_predictor",
    LearningDomain.COMMERCIAL_DEMAND: "commercial_scenario_calibration",
    LearningDomain.PURCHASE_UTILIZATION: "allocation_calibration",
    LearningDomain.SEGMENT_ACCEPTANCE: "segment_acceptance_evidence",
}
_MODEL_ROUTES = {"foundry", "copilot_studio_byom", "foundry_work_iq"}
_COMMERCIAL_ROUTES = {
    "cowork",
    "work_iq",
    "copilot_studio",
    "github_copilot",
    "copilot_studio_byom",
    "foundry_work_iq",
}


@dataclass(frozen=True)
class LearningObservation:
    observation_id: str
    evidence_content_hash: str
    independent_group: str
    value: float
    metric: str
    segment_id: str | None = None
    acceptance_decision: str | None = None

    def __post_init__(self) -> None:
        for field in ("observation_id", "independent_group", "metric"):
            _required(getattr(self, field), field)
        _hash(self.evidence_content_hash, "observation evidence_content_hash")
        if (
            isinstance(self.value, bool)
            or not isinstance(self.value, (int, float))
            or not math.isfinite(self.value)
            or self.value < 0
        ):
            raise ValueError("learning observation value must be finite and non-negative")
        if self.segment_id is not None:
            _required(self.segment_id, "segment_id")
        if self.acceptance_decision not in {None, "accepted", "rejected"}:
            raise ValueError("acceptance_decision must be accepted or rejected")

    @classmethod
    def from_value(
        cls, value: "LearningObservation | Mapping[str, Any]"
    ) -> "LearningObservation":
        return value if isinstance(value, cls) else cls(**dict(value))

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class EvidenceSufficiencyGate:
    minimum_samples: int
    minimum_independent_groups: int
    maximum_group_fraction: float

    def __post_init__(self) -> None:
        if self.minimum_samples < 1 or self.minimum_independent_groups < 1:
            raise ValueError("sufficiency minimums must be positive")
        if not 0 < self.maximum_group_fraction <= 1:
            raise ValueError("maximum_group_fraction must be in (0, 1]")
        if (
            self.minimum_samples < 4
            or self.minimum_independent_groups < 2
            or self.maximum_group_fraction > 0.5
        ):
            raise ValueError(
                "sufficiency gate cannot be weaker than 4 samples, "
                "2 independent groups, and 0.5 maximum group fraction"
            )

    def evaluate(self, observations: tuple[LearningObservation, ...]) -> dict[str, Any]:
        groups = Counter(item.independent_group for item in observations)
        maximum_fraction = max(groups.values()) / len(observations) if observations else None
        reasons = []
        if len(observations) < self.minimum_samples:
            reasons.append("insufficient_sample_count")
        if len(groups) < self.minimum_independent_groups:
            reasons.append("insufficient_independent_groups")
        if maximum_fraction is not None and maximum_fraction > self.maximum_group_fraction:
            reasons.append("correlated_sample_concentration")
        return {
            "sufficient": not reasons,
            "sample_count": len(observations),
            "independent_group_count": len(groups),
            "maximum_observed_group_fraction": maximum_fraction,
            "minimum_samples": self.minimum_samples,
            "minimum_independent_groups": self.minimum_independent_groups,
            "maximum_group_fraction": self.maximum_group_fraction,
            "constitutional_floor": {
                "minimum_samples": 4,
                "minimum_independent_groups": 2,
                "maximum_group_fraction": 0.5,
            },
            "reason_codes": reasons or ["evidence_sufficiency_gate_satisfied"],
        }


def _validate_domain(
    domain: LearningDomain,
    route_id: str,
    observations: tuple[LearningObservation, ...],
) -> None:
    if route_id not in _ROUTES:
        raise ValueError(f"unsupported route: {route_id}")
    metrics = {item.metric for item in observations}
    if domain is LearningDomain.MODEL_TOKENS:
        if route_id not in _MODEL_ROUTES or metrics != {"model_tokens_per_task"}:
            raise ValueError("model-token learning requires a Foundry-backed route and metric")
    elif domain is LearningDomain.COMMERCIAL_DEMAND:
        if route_id not in _COMMERCIAL_ROUTES or metrics != {"native_demand_per_task"}:
            raise ValueError("commercial demand learning requires a commercial demand route")
    elif domain is LearningDomain.PURCHASE_UTILIZATION:
        if metrics != {"purchase_utilization"} or any(item.value > 1 for item in observations):
            raise ValueError("purchase utilization observations must be in [0, 1]")
    elif (
        metrics != {"segment_acceptance"}
        or any(item.segment_id is None for item in observations)
        or any(item.acceptance_decision is None for item in observations)
        or any(
            item.value != (1.0 if item.acceptance_decision == "accepted" else 0.0)
            for item in observations
        )
    ):
        raise ValueError("segment acceptance requires explicit binary accepted/rejected outcomes")


def build_route_learning_receipt(
    *,
    domain: LearningDomain | str,
    route_id: str,
    reconciliation_reference: Mapping[str, str],
    prior_calibration_reference: Mapping[str, str],
    proposed_calibration_revision: str,
    observations: Iterable[LearningObservation | Mapping[str, Any]],
    gate: EvidenceSufficiencyGate,
    created_at: str,
) -> dict[str, Any]:
    """Build a receipt that revises exactly one domain or records unchanged."""
    domain = LearningDomain(domain)
    rows = tuple(LearningObservation.from_value(item) for item in observations)
    if not rows:
        raise ValueError("learning requires at least one observation")
    if len({item.observation_id for item in rows}) != len(rows):
        raise ValueError("learning observation IDs must be unique")
    if not isinstance(gate, EvidenceSufficiencyGate):
        raise ValueError("gate must be EvidenceSufficiencyGate")
    _validate_domain(domain, route_id, rows)
    _utc(created_at, "created_at")
    for name, reference in (
        ("reconciliation_reference", reconciliation_reference),
        ("prior_calibration_reference", prior_calibration_reference),
    ):
        _required(reference.get("id"), f"{name}.id")
        _required(reference.get("revision"), f"{name}.revision")
        _hash(reference.get("content_hash"), f"{name}.content_hash")
    _required(proposed_calibration_revision, "proposed_calibration_revision")
    sufficiency = gate.evaluate(rows)
    revised = sufficiency["sufficient"]
    if revised and proposed_calibration_revision == prior_calibration_reference["revision"]:
        raise ValueError("sufficient evidence requires a new calibration revision")
    values = [float(item.value) for item in rows]
    result = "revised" if revised else "unchanged_insufficient_evidence"
    effective_revision = (
        proposed_calibration_revision
        if revised
        else prior_calibration_reference["revision"]
    )
    calibration_payload = (
        {
            "domain": domain.value,
            "route_id": route_id,
            "revision": effective_revision,
            "sample_count": len(rows),
            "mean": sum(values) / len(values),
            "minimum": min(values),
            "maximum": max(values),
            "observation_hashes": sorted(item.evidence_content_hash for item in rows),
        }
        if revised
        else {
            "domain": domain.value,
            "route_id": route_id,
            "revision": effective_revision,
            "content_hash": prior_calibration_reference["content_hash"],
            "reason": "prior_calibration_retained",
        }
    )
    effective_hash = (
        _digest(calibration_payload)
        if revised
        else prior_calibration_reference["content_hash"]
    )
    other_domains = sorted(item.value for item in LearningDomain if item is not domain)
    payload = {
        "schema_version": ROUTE_LEARNING_SCHEMA_VERSION,
        "created_at": created_at,
        "route_id": route_id,
        "domain": domain.value,
        "target_system": _DOMAIN_TARGET[domain],
        "reconciliation_reference": dict(reconciliation_reference),
        "prior_calibration_reference": dict(prior_calibration_reference),
        "result": result,
        "sufficiency": sufficiency,
        "observation_hashes": sorted(item.evidence_content_hash for item in rows),
        "effective_calibration": {
            "revision": effective_revision,
            "content_hash": effective_hash,
            "payload": calibration_payload,
        },
        "untouched_domains": other_domains,
        "cross_domain_updates_performed": [],
        "distribution_semantics": {
            "modeled_percentiles_remain_distinct": True,
            "empirical_calibration_applied": revised,
            "calibrated_tail_risk_claim": False,
        },
        "historical_receipts_mutated": False,
        "historical_decisions_mutated": False,
    }
    payload["learning_receipt_id"] = f"route-learning-{_digest(payload)[:32]}"
    payload["content_hash"] = _digest(payload)
    return payload


def link_later_forecast(
    *,
    learning_receipt: Mapping[str, Any],
    forecast_id: str,
    forecast_revision: str,
    forecast_content_hash: str,
    calibration_revision: str,
    calibration_content_hash: str,
    created_at: str,
) -> dict[str, Any]:
    """Prove a later forecast used the effective revision or unchanged prior."""
    if learning_receipt.get("schema_version") != ROUTE_LEARNING_SCHEMA_VERSION:
        raise ValueError("learning receipt schema version is unsupported")
    receipt_hash = learning_receipt.get("content_hash")
    if receipt_hash != _digest(
        {
            key: value
            for key, value in learning_receipt.items()
            if key != "content_hash"
        }
    ):
        raise ValueError("learning receipt integrity check failed")
    for field, value in (
        ("forecast_id", forecast_id),
        ("forecast_revision", forecast_revision),
    ):
        _required(value, field)
    _hash(forecast_content_hash, "forecast_content_hash")
    _required(calibration_revision, "calibration_revision")
    _hash(calibration_content_hash, "calibration_content_hash")
    _utc(created_at, "created_at")
    effective = learning_receipt["effective_calibration"]
    if (
        calibration_revision != effective["revision"]
        or calibration_content_hash != effective["content_hash"]
    ):
        raise ValueError("later forecast does not reference the effective calibration")
    payload = {
        "schema_version": FORECAST_LEARNING_LINK_SCHEMA_VERSION,
        "created_at": created_at,
        "learning_receipt_id": learning_receipt["learning_receipt_id"],
        "learning_receipt_content_hash": receipt_hash,
        "learning_result": learning_receipt["result"],
        "forecast_id": forecast_id,
        "forecast_revision": forecast_revision,
        "forecast_content_hash": forecast_content_hash,
        "calibration_revision": calibration_revision,
        "calibration_content_hash": calibration_content_hash,
        "source_verifiable_revision_change": (
            learning_receipt["result"] == "revised"
        ),
        "evidence_based_unchanged": (
            learning_receipt["result"] == "unchanged_insufficient_evidence"
        ),
        "historical_forecast_mutated": False,
    }
    payload["link_id"] = f"forecast-learning-{_digest(payload)[:32]}"
    payload["content_hash"] = _digest(payload)
    return payload


class RouteLearningStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def append(self, receipt: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        payload = dict(receipt)
        receipt_id = _required(payload.get("learning_receipt_id"), "learning_receipt_id")
        if payload.get("content_hash") != _digest(
            {field: value for field, value in payload.items() if field != "content_hash"}
        ):
            raise ValueError("route learning receipt content hash is invalid")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{receipt_id}.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing != payload:
                raise ValueError("route learning receipt identity collision")
            return existing, False
        temporary = self.root / f".{receipt_id}.{uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, allow_nan=False, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            publish_immutable(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return payload, True
