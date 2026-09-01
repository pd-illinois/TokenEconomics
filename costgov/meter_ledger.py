"""Append-only native-meter and allocatable-cost evidence by trajectory."""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import uuid4

from .consumption_models import ConsumptionFamily

METER_LEDGER_SCHEMA_VERSION = "meter-ledger-entry.v1"


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


class MeterEvidenceStatus(str, Enum):
    MEASURED = "measured"
    MODELED = "modeled"
    UNAVAILABLE = "unavailable"
    EXCLUDED = "excluded"


class CostCoverage(str, Enum):
    PRICED = "priced"
    UNPRICED = "unpriced"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class MeterLedgerEntry:
    schema_version: str
    entry_id: str
    experiment_id: str
    experiment_revision: str
    arm_id: str
    task_id: str
    trajectory_id: str
    step_id: str | None
    segment_id: str
    tenant_id: str
    product: str
    environment: str
    meter_stack_id: str
    meter_stack_version: str
    meter_stack_content_hash: str
    policy_candidate_id: str
    policy_candidate_version: str
    policy_candidate_content_hash: str
    meter_family: ConsumptionFamily
    meter_id: str
    native_unit: str
    native_currency: str
    quantity: float | None
    evidence_status: MeterEvidenceStatus
    entitlement_disposition: str
    purchase_source: str
    evidence_source: str
    evidence_content_hash: str
    pricing_revision: str | None
    rate_card_revision: str | None
    billing_period: str
    calculation_method: str
    allocation_method: str
    cost_coverage: CostCoverage
    allocated_cost_usd: float | None
    recorded_at: str
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != METER_LEDGER_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {METER_LEDGER_SCHEMA_VERSION}")
        for field in (
            "entry_id",
            "experiment_id",
            "experiment_revision",
            "arm_id",
            "task_id",
            "trajectory_id",
            "segment_id",
            "tenant_id",
            "product",
            "environment",
            "meter_stack_id",
            "meter_stack_version",
            "policy_candidate_id",
            "policy_candidate_version",
            "meter_id",
            "native_unit",
            "native_currency",
            "entitlement_disposition",
            "purchase_source",
            "evidence_source",
            "billing_period",
            "calculation_method",
            "allocation_method",
        ):
            _required(getattr(self, field), field)
        if self.step_id is not None:
            _required(self.step_id, "step_id")
        _hash(self.evidence_content_hash, "evidence_content_hash")
        _hash(self.meter_stack_content_hash, "meter_stack_content_hash")
        _hash(
            self.policy_candidate_content_hash,
            "policy_candidate_content_hash",
        )
        if not isinstance(self.evidence_status, MeterEvidenceStatus):
            raise ValueError("meter evidence_status is invalid")
        if not isinstance(self.meter_family, ConsumptionFamily):
            raise ValueError("meter_family is invalid")
        if not isinstance(self.cost_coverage, CostCoverage):
            raise ValueError("meter cost_coverage is invalid")
        if self.evidence_status in {
            MeterEvidenceStatus.MEASURED,
            MeterEvidenceStatus.MODELED,
        }:
            if (
                isinstance(self.quantity, bool)
                or not isinstance(self.quantity, (int, float))
                or not math.isfinite(self.quantity)
                or self.quantity < 0
            ):
                raise ValueError("measured or modeled quantity must be finite and non-negative")
        elif self.quantity is not None:
            raise ValueError("unavailable or excluded quantity must be null")
        elif not self.unavailable_reason:
            raise ValueError("unavailable or excluded meter evidence requires a reason")
        if self.cost_coverage is CostCoverage.PRICED:
            if (
                isinstance(self.allocated_cost_usd, bool)
                or not isinstance(self.allocated_cost_usd, (int, float))
                or not math.isfinite(self.allocated_cost_usd)
                or self.allocated_cost_usd < 0
            ):
                raise ValueError("priced entries require finite non-negative allocated_cost_usd")
            if not self.pricing_revision and not self.rate_card_revision:
                raise ValueError("priced entries require a pricing or rate-card revision")
        elif self.allocated_cost_usd is not None:
            raise ValueError("unpriced entries cannot carry allocated_cost_usd")
        _utc(self.recorded_at, "meter recorded_at")

    @classmethod
    def from_dict(cls, value: object) -> "MeterLedgerEntry":
        if not isinstance(value, Mapping):
            raise ValueError("meter ledger entry must be an object")
        values = dict(value)
        values["meter_family"] = ConsumptionFamily(values.get("meter_family"))
        values["evidence_status"] = MeterEvidenceStatus(values.get("evidence_status"))
        values["cost_coverage"] = CostCoverage(values.get("cost_coverage"))
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.__dict__)
        payload["meter_family"] = self.meter_family.value
        payload["evidence_status"] = self.evidence_status.value
        payload["cost_coverage"] = self.cost_coverage.value
        return payload

    def to_canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(), allow_nan=False, separators=(",", ":"), sort_keys=True
        )


@dataclass(frozen=True)
class MeterAggregate:
    meter_family: ConsumptionFamily
    native_unit: str
    native_currency: str
    known_quantity: float
    priced_cost_usd: float
    entry_count: int
    priced_entries: int
    unpriced_entries: int
    unavailable_entries: int
    cost_coverage_complete: bool


def aggregate_meter_entries(entries: Iterable[MeterLedgerEntry]) -> tuple[MeterAggregate, ...]:
    groups: dict[tuple[str, str, str], list[MeterLedgerEntry]] = {}
    for entry in entries:
        groups.setdefault(
            (entry.meter_family, entry.native_unit, entry.native_currency), []
        ).append(entry)
    result = []
    for (family, unit, currency), items in sorted(groups.items()):
        result.append(
            MeterAggregate(
                meter_family=family,
                native_unit=unit,
                native_currency=currency,
                known_quantity=sum(item.quantity or 0 for item in items),
                priced_cost_usd=sum(item.allocated_cost_usd or 0 for item in items),
                entry_count=len(items),
                priced_entries=sum(
                    item.cost_coverage is CostCoverage.PRICED for item in items
                ),
                unpriced_entries=sum(
                    item.cost_coverage is CostCoverage.UNPRICED for item in items
                ),
                unavailable_entries=sum(
                    item.evidence_status
                    in {MeterEvidenceStatus.UNAVAILABLE, MeterEvidenceStatus.EXCLUDED}
                    for item in items
                ),
                cost_coverage_complete=all(
                    item.cost_coverage
                    in {CostCoverage.PRICED, CostCoverage.NOT_APPLICABLE}
                    for item in items
                ),
            )
        )
    return tuple(result)


def reconcile_meter_quantity(
    entries: Iterable[MeterLedgerEntry],
    *,
    meter_id: str,
    source_quantity: float,
    tolerance: float,
) -> dict[str, Any]:
    if source_quantity < 0 or tolerance < 0:
        raise ValueError("source_quantity and tolerance must be non-negative")
    matching = [entry for entry in entries if entry.meter_id == meter_id]
    unavailable = [
        entry
        for entry in matching
        if entry.evidence_status
        in {MeterEvidenceStatus.UNAVAILABLE, MeterEvidenceStatus.EXCLUDED}
    ]
    ledger_quantity = sum(entry.quantity or 0 for entry in matching)
    difference = ledger_quantity - source_quantity
    status = (
        "incomplete"
        if unavailable
        else "matched"
        if abs(difference) <= tolerance
        else "mismatch"
    )
    return {
        "meter_id": meter_id,
        "status": status,
        "source_quantity": source_quantity,
        "ledger_quantity": ledger_quantity,
        "difference": difference,
        "tolerance": tolerance,
    }


def entries_from_gateway_record(
    record: object,
    *,
    experiment_id: str,
    experiment_revision: str,
    arm_id: str,
    environment: str,
    meter_stack_id: str,
    meter_stack_version: str,
    meter_stack_content_hash: str,
    evaluation_performed: bool,
) -> tuple[MeterLedgerEntry, ...]:
    """Translate one gateway record without inventing missing resource costs."""
    telemetry = dict(vars(record))
    evidence_hash = hashlib.sha256(
        json.dumps(
            telemetry, allow_nan=False, separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest()
    recorded_at = _utc(telemetry.get("timestamp"), "telemetry timestamp")
    task_id = _required(telemetry.get("task_id"), "task_id")
    trajectory_id = _required(telemetry.get("trajectory_id"), "trajectory_id")
    segment_id = _required(telemetry.get("segment_id"), "segment_id")
    tenant_id = _required(telemetry.get("tenant"), "tenant")
    policy_id = _required(
        telemetry.get("policy_candidate_id") or telemetry.get("policy_id"),
        "policy_candidate_id",
    )
    policy_version = _required(
        telemetry.get("policy_candidate_version") or telemetry.get("policy_version"),
        "policy_candidate_version",
    )
    policy_content_hash = _hash(
        telemetry.get("policy_candidate_content_hash")
        or telemetry.get("policy_hash"),
        "policy_candidate_content_hash",
    )
    model = _required(telemetry.get("model"), "model")
    cache_hit = bool(telemetry.get("cache_hit"))
    model_executed = not cache_hit and model != "none"
    billing_period = recorded_at[:7]
    common = {
        "schema_version": METER_LEDGER_SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "experiment_revision": experiment_revision,
        "arm_id": arm_id,
        "task_id": task_id,
        "trajectory_id": trajectory_id,
        "segment_id": segment_id,
        "tenant_id": tenant_id,
        "product": "foundry",
        "environment": environment,
        "meter_stack_id": _required(meter_stack_id, "meter_stack_id"),
        "meter_stack_version": _required(
            meter_stack_version, "meter_stack_version"
        ),
        "meter_stack_content_hash": _hash(
            meter_stack_content_hash, "meter_stack_content_hash"
        ),
        "policy_candidate_id": policy_id,
        "policy_candidate_version": policy_version,
        "policy_candidate_content_hash": policy_content_hash,
        "evidence_source": "gateway.telemetry.v1",
        "evidence_content_hash": evidence_hash,
        "billing_period": billing_period,
        "recorded_at": recorded_at,
    }
    tokens = float(telemetry.get("input_tokens", 0)) + float(
        telemetry.get("output_tokens", 0)
    )
    direct = MeterLedgerEntry(
        **common,
        entry_id=f"meter-{task_id}-foundry-model",
        step_id=f"step-{task_id[5:]}-work",
        meter_family=ConsumptionFamily.DIRECT_TOKEN,
        meter_id="foundry_model",
        native_unit="model_token",
        native_currency="USD",
        quantity=tokens,
        evidence_status=MeterEvidenceStatus.MODELED,
        entitlement_disposition=(
            "pay_as_you_go"
            if model_executed
            else "semantic_cache_no_model_call"
            if cache_hit
            else "budget_rejection_no_model_call"
        ),
        purchase_source="azure_consumption" if model_executed else "not_applicable",
        pricing_revision="simulated-models.v1" if model_executed else None,
        rate_card_revision=None,
        calculation_method=(
            "gateway_reported_model_cost"
            if model_executed
            else "no_model_execution"
        ),
        allocation_method="direct_to_task",
        cost_coverage=(
            CostCoverage.PRICED
            if model_executed
            else CostCoverage.NOT_APPLICABLE
        ),
        allocated_cost_usd=(
            float(telemetry.get("cost_usd", 0)) if model_executed else None
        ),
    )
    evaluation = MeterLedgerEntry(
        **common,
        entry_id=f"meter-{task_id}-evaluation",
        step_id=None,
        meter_family=ConsumptionFamily.EVALUATION,
        meter_id="automated_task_evaluation",
        native_unit="evaluation",
        native_currency="not_monetized",
        quantity=1.0 if evaluation_performed else 0.0,
        evidence_status=MeterEvidenceStatus.MODELED,
        entitlement_disposition="not_applicable",
        purchase_source="not_allocated",
        pricing_revision=None,
        rate_card_revision=None,
        calculation_method="one_evaluation_per_sampled_task",
        allocation_method="direct_to_task",
        cost_coverage=CostCoverage.UNPRICED,
        allocated_cost_usd=None,
        unavailable_reason=(
            None
            if evaluation_performed
            else "The task was not selected for automated evaluation."
        ),
    )
    resource = MeterLedgerEntry(
        **common,
        entry_id=f"meter-{task_id}-foundry-resources",
        step_id=None,
        meter_family=ConsumptionFamily.RESOURCE,
        meter_id="foundry_resources",
        native_unit="tool_or_infrastructure_unit",
        native_currency="USD",
        quantity=None,
        evidence_status=MeterEvidenceStatus.UNAVAILABLE,
        entitlement_disposition="unknown",
        purchase_source="azure_consumption",
        pricing_revision=None,
        rate_card_revision=None,
        calculation_method="unavailable_from_gateway_telemetry",
        allocation_method="not_allocated",
        cost_coverage=CostCoverage.UNPRICED,
        allocated_cost_usd=None,
        unavailable_reason=(
            "Gateway telemetry does not emit retrieval, tool, or infrastructure units."
        ),
    )
    return direct, evaluation, resource


@dataclass(frozen=True)
class MeterLedgerRecord:
    content_hash: str
    entry: MeterLedgerEntry


class MeterLedgerStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, entry_id: str) -> Path:
        return self.root / f"{hashlib.sha256(_required(entry_id, 'entry_id').encode()).hexdigest()}.json"

    def append(self, entry: MeterLedgerEntry) -> MeterLedgerRecord:
        self.root.mkdir(parents=True, exist_ok=True)
        content_hash = hashlib.sha256(entry.to_canonical_json().encode()).hexdigest()
        path = self._path(entry.entry_id)
        temporary = self.root / f".{path.stem}.{uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(
                    {"content_hash": content_hash, "entry": entry.to_dict()},
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
        return MeterLedgerRecord(content_hash, entry)

    def get(self, entry_id: str) -> MeterLedgerRecord | None:
        path = self._path(entry_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            entry = MeterLedgerEntry.from_dict(payload["entry"])
            content_hash = _hash(payload["content_hash"], "ledger content_hash")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("meter ledger integrity check failed") from exc
        if (
            entry.entry_id != entry_id
            or hashlib.sha256(entry.to_canonical_json().encode()).hexdigest()
            != content_hash
        ):
            raise ValueError("meter ledger integrity check failed")
        return MeterLedgerRecord(content_hash, entry)
