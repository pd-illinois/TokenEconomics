"""Framework-neutral normalization of route-native technical and commercial usage."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import uuid4

from .atomic_publish import publish_immutable

from .consumption_models import ConsumptionFamily
from .route_capabilities import route_capability_profile_for

ROUTE_USAGE_SCHEMA_VERSION = "route-usage-observation.v1"
_DECIMAL_PATTERN = re.compile(r"^(0|[1-9][0-9]*)([.][0-9]+)?$")


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


class SourceKind(str, Enum):
    MICROSOFT_365 = "microsoft_365"
    POWER_PLATFORM = "power_platform"
    GITHUB = "github"
    AZURE = "azure"
    WORKLOAD_OWNED = "workload_owned"


class UsageEvidenceLabel(str, Enum):
    MEASURED = "measured"
    MODELED = "modeled"
    SIMULATED = "simulated"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"


class SourceState(str, Enum):
    AVAILABLE = "available"
    DELAYED = "delayed"
    UNAVAILABLE = "unavailable"


class RefreshState(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    PENDING = "pending"
    UNAVAILABLE = "unavailable"


class FinalizationState(str, Enum):
    PROVISIONAL = "provisional"
    FINALIZED = "finalized"
    UNAVAILABLE = "unavailable"


class LatencyClass(str, Enum):
    REAL_TIME = "real_time"
    DELAYED = "delayed"
    PERIODIC = "periodic"


class CoverageState(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class Granularity(str, Enum):
    EVENT = "event"
    TASK = "task"
    USER = "user"
    ENVIRONMENT = "environment"
    TENANT = "tenant"
    BILLING_PERIOD = "billing_period"


class AttributionState(str, Enum):
    ATTRIBUTED = "attributed"
    UNATTRIBUTED = "unattributed"
    UNAVAILABLE = "unavailable"


class QuantityPrecision(str, Enum):
    EXACT = "exact"
    SOURCE_REPORTED = "source_reported"
    ROUNDED = "rounded"
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"


class UsageDisposition(str, Enum):
    METERED = "metered"
    INCLUDED = "included"
    ALLOWANCE_DRAWDOWN = "allowance_drawdown"
    OVERAGE = "overage"
    UNATTRIBUTED = "unattributed"


@dataclass(frozen=True)
class NativeQuantity:
    value: str | None
    precision: QuantityPrecision
    source_scale: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.precision, QuantityPrecision):
            raise ValueError("quantity precision is invalid")
        if self.precision is QuantityPrecision.UNAVAILABLE:
            if self.value is not None or self.source_scale is not None:
                raise ValueError("unavailable quantity cannot carry a value or scale")
            return
        text = _required(self.value, "native quantity value")
        if not _DECIMAL_PATTERN.fullmatch(text):
            raise ValueError("native quantity must be a non-negative decimal string")
        try:
            value = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError("native quantity is invalid") from exc
        if not value.is_finite() or value < 0:
            raise ValueError("native quantity must be finite and non-negative")
        if (
            isinstance(self.source_scale, bool)
            or not isinstance(self.source_scale, int)
            or self.source_scale < 0
        ):
            raise ValueError("available quantity requires non-negative source_scale")
        lexical_scale = len(text.partition(".")[2])
        if lexical_scale != self.source_scale:
            raise ValueError("source_scale must preserve the source decimal precision")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "NativeQuantity":
        values = dict(value)
        values["precision"] = QuantityPrecision(values.get("precision"))
        return cls(**values)

    @classmethod
    def unavailable(cls) -> "NativeQuantity":
        return cls(None, QuantityPrecision.UNAVAILABLE, None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "precision": self.precision.value,
            "source_scale": self.source_scale,
        }


@dataclass(frozen=True)
class UsageSource:
    source_id: str
    source_kind: SourceKind
    authority: str
    record_id: str
    revision: str
    content_hash: str

    def __post_init__(self) -> None:
        for field in ("source_id", "authority", "record_id", "revision"):
            _required(getattr(self, field), f"usage source {field}")
        if not isinstance(self.source_kind, SourceKind):
            raise ValueError("usage source kind is invalid")
        _hash(self.content_hash, "usage source content_hash")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "UsageSource":
        values = dict(value)
        values["source_kind"] = SourceKind(values.get("source_kind"))
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        result = dict(self.__dict__)
        result["source_kind"] = self.source_kind.value
        return result


@dataclass(frozen=True)
class RouteUsageObservation:
    schema_version: str
    observation_id: str
    route_id: str
    leg_id: str
    meter_family: ConsumptionFamily
    meter_id: str
    native_unit: str
    native_currency: str
    quantity: NativeQuantity
    allowance_quantity: NativeQuantity | None
    disposition: UsageDisposition
    included_usage: bool
    evidence_label: UsageEvidenceLabel
    source: UsageSource
    source_state: SourceState
    refresh_state: RefreshState
    finalization_state: FinalizationState
    latency_class: LatencyClass
    coverage_state: CoverageState
    granularity: Granularity
    snapshot_semantics: str
    observed_at: str
    snapshot_at: str
    refreshed_at: str
    refresh_latency_seconds: int
    retention_until: str | None
    attribution_state: AttributionState
    task_id: str | None
    trajectory_id: str | None
    step_id: str | None
    segment_id: str | None
    preflight_enforcement_eligible: bool
    unavailable_reason: str | None

    def __post_init__(self) -> None:
        if self.schema_version != ROUTE_USAGE_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {ROUTE_USAGE_SCHEMA_VERSION}")
        for field in (
            "observation_id",
            "route_id",
            "leg_id",
            "meter_id",
            "native_unit",
            "native_currency",
            "snapshot_semantics",
        ):
            _required(getattr(self, field), field)
        for value, enum_type, field in (
            (self.meter_family, ConsumptionFamily, "meter_family"),
            (self.disposition, UsageDisposition, "disposition"),
            (self.evidence_label, UsageEvidenceLabel, "evidence_label"),
            (self.source_state, SourceState, "source_state"),
            (self.refresh_state, RefreshState, "refresh_state"),
            (self.finalization_state, FinalizationState, "finalization_state"),
            (self.latency_class, LatencyClass, "latency_class"),
            (self.coverage_state, CoverageState, "coverage_state"),
            (self.granularity, Granularity, "granularity"),
            (self.attribution_state, AttributionState, "attribution_state"),
        ):
            if not isinstance(value, enum_type):
                raise ValueError(f"{field} is invalid")
        _utc(self.observed_at, "observed_at")
        _utc(self.snapshot_at, "snapshot_at")
        _utc(self.refreshed_at, "refreshed_at")
        if _instant(self.snapshot_at) < _instant(self.observed_at):
            raise ValueError("snapshot_at cannot precede observed_at")
        if _instant(self.refreshed_at) < _instant(self.snapshot_at):
            raise ValueError("refreshed_at cannot precede snapshot_at")
        actual_latency = int(
            (_instant(self.refreshed_at) - _instant(self.snapshot_at)).total_seconds()
        )
        if (
            isinstance(self.refresh_latency_seconds, bool)
            or self.refresh_latency_seconds != actual_latency
        ):
            raise ValueError("refresh_latency_seconds must match source timestamps")
        if self.retention_until is not None:
            _utc(self.retention_until, "retention_until")
            if _instant(self.retention_until) < _instant(self.refreshed_at):
                raise ValueError("retention_until cannot precede refreshed_at")
        if self.included_usage != (
            self.disposition is UsageDisposition.INCLUDED
        ):
            raise ValueError("included_usage must agree with usage disposition")
        if (
            self.disposition is UsageDisposition.ALLOWANCE_DRAWDOWN
            and self.allowance_quantity is None
        ):
            raise ValueError("allowance drawdown requires allowance_quantity")
        if self.attribution_state is AttributionState.ATTRIBUTED:
            for field in ("task_id", "trajectory_id", "segment_id"):
                _required(getattr(self, field), field)
            if self.granularity in {
                Granularity.TENANT,
                Granularity.BILLING_PERIOD,
                Granularity.ENVIRONMENT,
            }:
                raise ValueError("coarse usage cannot be attributed to an individual task")
        elif any(
            value is not None
            for value in (
                self.task_id,
                self.trajectory_id,
                self.step_id,
                self.segment_id,
            )
        ):
            raise ValueError("unattributed usage cannot carry task correlation identifiers")
        if self.step_id is not None:
            _required(self.step_id, "step_id")
        unavailable = (
            self.evidence_label
            in {UsageEvidenceLabel.UNAVAILABLE, UsageEvidenceLabel.BLOCKED}
            or self.source_state is SourceState.UNAVAILABLE
            or self.refresh_state is RefreshState.UNAVAILABLE
            or self.coverage_state is CoverageState.UNAVAILABLE
        )
        if unavailable:
            if self.quantity.precision is not QuantityPrecision.UNAVAILABLE:
                raise ValueError("unavailable usage cannot carry a native quantity")
            if self.finalization_state is not FinalizationState.UNAVAILABLE:
                raise ValueError("unavailable usage requires unavailable finalization")
            if not self.unavailable_reason:
                raise ValueError("unavailable usage requires a reason")
        elif self.quantity.precision is QuantityPrecision.UNAVAILABLE:
            raise ValueError("available usage requires a native quantity")
        expected_preflight = (
            self.evidence_label is UsageEvidenceLabel.MEASURED
            and self.source_state is SourceState.AVAILABLE
            and self.refresh_state is RefreshState.FRESH
            and self.latency_class is LatencyClass.REAL_TIME
            and self.coverage_state is CoverageState.COMPLETE
            and self.attribution_state is AttributionState.ATTRIBUTED
        )
        if self.preflight_enforcement_eligible != expected_preflight:
            raise ValueError(
                "preflight eligibility contradicts source latency or coverage"
            )
        if (
            self.evidence_label
            in {UsageEvidenceLabel.MODELED, UsageEvidenceLabel.SIMULATED}
            and self.finalization_state is FinalizationState.FINALIZED
        ):
            raise ValueError("modeled or simulated usage cannot be finalized actuals")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RouteUsageObservation":
        values = dict(value)
        values["meter_family"] = ConsumptionFamily(values.get("meter_family"))
        values["quantity"] = NativeQuantity.from_dict(values.get("quantity", {}))
        allowance = values.get("allowance_quantity")
        values["allowance_quantity"] = (
            NativeQuantity.from_dict(allowance) if allowance else None
        )
        values["disposition"] = UsageDisposition(values.get("disposition"))
        values["evidence_label"] = UsageEvidenceLabel(values.get("evidence_label"))
        values["source"] = UsageSource.from_dict(values.get("source", {}))
        values["source_state"] = SourceState(values.get("source_state"))
        values["refresh_state"] = RefreshState(values.get("refresh_state"))
        values["finalization_state"] = FinalizationState(
            values.get("finalization_state")
        )
        values["latency_class"] = LatencyClass(values.get("latency_class"))
        values["coverage_state"] = CoverageState(values.get("coverage_state"))
        values["granularity"] = Granularity(values.get("granularity"))
        values["attribution_state"] = AttributionState(
            values.get("attribution_state")
        )
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "observation_id": self.observation_id,
            "route_id": self.route_id,
            "leg_id": self.leg_id,
            "meter_family": self.meter_family.value,
            "meter_id": self.meter_id,
            "native_unit": self.native_unit,
            "native_currency": self.native_currency,
            "quantity": self.quantity.to_dict(),
            "allowance_quantity": (
                self.allowance_quantity.to_dict()
                if self.allowance_quantity is not None
                else None
            ),
            "disposition": self.disposition.value,
            "included_usage": self.included_usage,
            "evidence_label": self.evidence_label.value,
            "source": self.source.to_dict(),
            "source_state": self.source_state.value,
            "refresh_state": self.refresh_state.value,
            "finalization_state": self.finalization_state.value,
            "latency_class": self.latency_class.value,
            "coverage_state": self.coverage_state.value,
            "granularity": self.granularity.value,
            "snapshot_semantics": self.snapshot_semantics,
            "observed_at": self.observed_at,
            "snapshot_at": self.snapshot_at,
            "refreshed_at": self.refreshed_at,
            "refresh_latency_seconds": self.refresh_latency_seconds,
            "retention_until": self.retention_until,
            "attribution_state": self.attribution_state.value,
            "task_id": self.task_id,
            "trajectory_id": self.trajectory_id,
            "step_id": self.step_id,
            "segment_id": self.segment_id,
            "preflight_enforcement_eligible": self.preflight_enforcement_eligible,
            "unavailable_reason": self.unavailable_reason,
        }

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict()).encode()).hexdigest()


ROUTE_LEDGER_LEGS: Mapping[str, tuple[str, ...]] = {
    "included": ("subscription",),
    "cowork": ("commercial",),
    "agent_builder": ("subscription",),
    "copilot_studio": ("commercial",),
    "work_iq": ("work_iq",),
    "foundry": ("foundry",),
    "github_copilot": ("github_copilot",),
    "copilot_studio_byom": ("commercial", "foundry"),
    "foundry_work_iq": ("foundry", "work_iq"),
}

_SOURCE_KINDS: Mapping[str, SourceKind] = {
    "m365-usage": SourceKind.MICROSOFT_365,
    "copilot-credit-usage": SourceKind.MICROSOFT_365,
    "power-platform-usage": SourceKind.POWER_PLATFORM,
    "azure-usage": SourceKind.AZURE,
    "github-ai-usage": SourceKind.GITHUB,
}


def normalize_route_usage(
    *,
    observation_id: str,
    route_id: str,
    leg_id: str,
    meter_family: ConsumptionFamily,
    meter_id: str,
    native_unit: str,
    native_currency: str,
    quantity: NativeQuantity,
    allowance_quantity: NativeQuantity | None,
    disposition: UsageDisposition,
    evidence_label: UsageEvidenceLabel,
    source: UsageSource,
    source_state: SourceState,
    refresh_state: RefreshState,
    finalization_state: FinalizationState,
    latency_class: LatencyClass,
    coverage_state: CoverageState,
    granularity: Granularity,
    snapshot_semantics: str,
    observed_at: str,
    snapshot_at: str,
    refreshed_at: str,
    retention_until: str | None,
    attribution_state: AttributionState,
    task_id: str | None = None,
    trajectory_id: str | None = None,
    step_id: str | None = None,
    segment_id: str | None = None,
    unavailable_reason: str | None = None,
) -> RouteUsageObservation:
    """Normalize an already mapped source record without product-specific inference."""
    profile = route_capability_profile_for(
        route_id, as_of=_instant(_utc(refreshed_at, "refreshed_at")).date()
    )
    allowed_legs = ROUTE_LEDGER_LEGS.get(route_id)
    if allowed_legs is None or leg_id not in allowed_legs:
        raise ValueError("usage leg does not match the route")
    declared_sources = {
        item["source_id"]: item["authority"]
        for item in profile["actual_usage_sources"]
    }
    if source.source_kind is SourceKind.WORKLOAD_OWNED:
        if source.authority != "workload_owner":
            raise ValueError("workload-owned usage requires workload_owner authority")
    elif (
        source.source_id not in declared_sources
        or source.authority != declared_sources[source.source_id]
        or source.source_kind is not _SOURCE_KINDS[source.source_id]
    ):
        raise ValueError("usage source does not match the route capability profile")
    latency = int(
        (_instant(_utc(refreshed_at, "refreshed_at"))
         - _instant(_utc(snapshot_at, "snapshot_at"))).total_seconds()
    )
    preflight = (
        evidence_label is UsageEvidenceLabel.MEASURED
        and source_state is SourceState.AVAILABLE
        and refresh_state is RefreshState.FRESH
        and latency_class is LatencyClass.REAL_TIME
        and coverage_state is CoverageState.COMPLETE
        and attribution_state is AttributionState.ATTRIBUTED
    )
    return RouteUsageObservation(
        schema_version=ROUTE_USAGE_SCHEMA_VERSION,
        observation_id=observation_id,
        route_id=route_id,
        leg_id=leg_id,
        meter_family=meter_family,
        meter_id=meter_id,
        native_unit=native_unit,
        native_currency=native_currency,
        quantity=quantity,
        allowance_quantity=allowance_quantity,
        disposition=disposition,
        included_usage=disposition is UsageDisposition.INCLUDED,
        evidence_label=evidence_label,
        source=source,
        source_state=source_state,
        refresh_state=refresh_state,
        finalization_state=finalization_state,
        latency_class=latency_class,
        coverage_state=coverage_state,
        granularity=granularity,
        snapshot_semantics=snapshot_semantics,
        observed_at=observed_at,
        snapshot_at=snapshot_at,
        refreshed_at=refreshed_at,
        refresh_latency_seconds=latency,
        retention_until=retention_until,
        attribution_state=attribution_state,
        task_id=task_id,
        trajectory_id=trajectory_id,
        step_id=step_id,
        segment_id=segment_id,
        preflight_enforcement_eligible=preflight,
        unavailable_reason=unavailable_reason,
    )


def split_route_ledgers(
    route_id: str, observations: Iterable[RouteUsageObservation]
) -> Mapping[str, tuple[RouteUsageObservation, ...]]:
    """Return independent native ledgers and require every hybrid leg."""
    values = tuple(observations)
    expected = ROUTE_LEDGER_LEGS.get(route_id)
    if expected is None:
        raise ValueError(f"unsupported route: {route_id}")
    if any(item.route_id != route_id for item in values):
        raise ValueError("usage observations cannot mix routes")
    by_leg = {
        leg: tuple(item for item in values if item.leg_id == leg) for leg in expected
    }
    if any(not items for items in by_leg.values()):
        raise ValueError("usage observations are missing a required route ledger leg")
    return by_leg


@dataclass(frozen=True)
class RouteUsageRecord:
    content_hash: str
    observation: RouteUsageObservation


class RouteUsageStore:
    """Append-only usage observations with idempotent source replay."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, observation_id: str) -> Path:
        name = hashlib.sha256(_required(observation_id, "observation_id").encode())
        return self.root / f"{name.hexdigest()}.json"

    def record(self, observation: RouteUsageObservation) -> RouteUsageRecord:
        self.root.mkdir(parents=True, exist_ok=True)
        existing = self.get(observation.observation_id)
        if existing is not None:
            if existing.content_hash == observation.content_hash:
                return existing
            raise ValueError("usage observation replay conflicts with immutable evidence")
        path = self._path(observation.observation_id)
        temporary = self.root / f".{path.stem}.{uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(
                    {
                        "content_hash": observation.content_hash,
                        "observation": observation.to_dict(),
                    },
                    stream,
                    allow_nan=False,
                    indent=2,
                    sort_keys=True,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            try:
                publish_immutable(temporary, path)
            except FileExistsError:
                existing = self.get(observation.observation_id)
                if existing and existing.content_hash == observation.content_hash:
                    return existing
                raise ValueError(
                    "usage observation replay conflicts with immutable evidence"
                )
        finally:
            temporary.unlink(missing_ok=True)
        return RouteUsageRecord(observation.content_hash, observation)

    def get(self, observation_id: str) -> RouteUsageRecord | None:
        path = self._path(observation_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            observation = RouteUsageObservation.from_dict(payload["observation"])
            content_hash = _hash(payload["content_hash"], "usage content_hash")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("usage observation integrity check failed") from exc
        if observation.content_hash != content_hash:
            raise ValueError("usage observation integrity check failed")
        return RouteUsageRecord(content_hash, observation)
