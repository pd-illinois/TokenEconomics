"""Source-specific actual imports and immutable composite reconciliation."""

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

from .atomic_publish import publish_immutable

from .consumption_models import ConsumptionFamily
from .route_capabilities import ROUTE_CAPABILITY_PROFILES

SOURCE_ACTUAL_SCHEMA_VERSION = "source-actual-import.v1"
COMPOSITE_RECONCILIATION_SCHEMA_VERSION = "composite-route-reconciliation.v1"
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


def _amount(value: object, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{field} must be finite and non-negative")
    return float(value)


class ActualSource(str, Enum):
    M365_COST_MANAGEMENT = "m365_cost_management"
    POWER_PLATFORM = "power_platform"
    GITHUB_BILLING = "github_billing"
    AZURE_BILLING = "azure_billing"


class FinalizationState(str, Enum):
    PROVISIONAL = "provisional"
    FINALIZED = "finalized"


class PurchaseModel(str, Enum):
    SEAT = "seat"
    PACK = "pack"
    P3 = "p3"
    PAYG = "payg"
    GITHUB_ALLOWANCE = "github_allowance"
    AZURE_COST = "azure_cost"


_SOURCE_PURCHASE_MODELS = {
    ActualSource.M365_COST_MANAGEMENT: {
        PurchaseModel.SEAT,
        PurchaseModel.PACK,
        PurchaseModel.P3,
        PurchaseModel.PAYG,
    },
    ActualSource.POWER_PLATFORM: {
        PurchaseModel.PACK,
        PurchaseModel.P3,
        PurchaseModel.PAYG,
    },
    ActualSource.GITHUB_BILLING: {
        PurchaseModel.SEAT,
        PurchaseModel.GITHUB_ALLOWANCE,
        PurchaseModel.PAYG,
    },
    ActualSource.AZURE_BILLING: {PurchaseModel.AZURE_COST},
}
_SOURCE_ROUTES = {
    ActualSource.M365_COST_MANAGEMENT: {
        "included",
        "cowork",
        "agent_builder",
        "copilot_studio",
        "work_iq",
        "copilot_studio_byom",
        "foundry_work_iq",
    },
    ActualSource.POWER_PLATFORM: {"copilot_studio", "copilot_studio_byom"},
    ActualSource.GITHUB_BILLING: {"github_copilot"},
    ActualSource.AZURE_BILLING: {
        "foundry",
        "copilot_studio_byom",
        "foundry_work_iq",
    },
}


@dataclass(frozen=True)
class ActualObservation:
    observation_id: str
    route_id: str
    authority: str
    target_leg: str
    meter_family: ConsumptionFamily
    meter_id: str
    native_unit: str
    native_currency: str
    quantity: float
    purchase_model: PurchaseModel
    source_row_hash: str
    actual_cost: float | None = None
    cost_currency: str | None = None

    def __post_init__(self) -> None:
        for field in (
            "observation_id",
            "route_id",
            "authority",
            "target_leg",
            "meter_id",
            "native_unit",
            "native_currency",
        ):
            _required(getattr(self, field), field)
        if self.route_id not in _ROUTES:
            raise ValueError(f"unsupported route: {self.route_id}")
        if not isinstance(self.meter_family, ConsumptionFamily):
            raise ValueError("meter_family is invalid")
        if not isinstance(self.purchase_model, PurchaseModel):
            raise ValueError("purchase_model is invalid")
        _amount(self.quantity, "actual quantity")
        _hash(self.source_row_hash, "source_row_hash")
        if self.actual_cost is None:
            if self.cost_currency is not None:
                raise ValueError("cost currency requires an actual cost")
        else:
            _amount(self.actual_cost, "actual cost")
            _required(self.cost_currency, "cost_currency")

    @classmethod
    def from_value(
        cls, value: "ActualObservation | Mapping[str, Any]"
    ) -> "ActualObservation":
        if isinstance(value, cls):
            return value
        values = dict(value)
        values["meter_family"] = ConsumptionFamily(values.get("meter_family"))
        values["purchase_model"] = PurchaseModel(values.get("purchase_model"))
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.__dict__)
        payload["meter_family"] = self.meter_family.value
        payload["purchase_model"] = self.purchase_model.value
        return payload

    @property
    def content_hash(self) -> str:
        return _digest(self.to_dict())


def build_source_actual_import(
    *,
    source: ActualSource | str,
    import_id: str,
    source_revision: str,
    source_export_hash: str,
    billing_period: str,
    refreshed_at: str,
    finalization_state: FinalizationState | str,
    observations: Iterable[ActualObservation | Mapping[str, Any]],
    finalized_at: str | None = None,
) -> dict[str, Any]:
    """Normalize one authority without erasing its refresh/finalization state."""
    source = ActualSource(source)
    state = FinalizationState(finalization_state)
    for field, value in (
        ("import_id", import_id),
        ("source_revision", source_revision),
        ("billing_period", billing_period),
    ):
        _required(value, field)
    _hash(source_export_hash, "source_export_hash")
    _utc(refreshed_at, "refreshed_at")
    if state is FinalizationState.FINALIZED:
        _utc(finalized_at, "finalized_at")
    elif finalized_at is not None:
        raise ValueError("provisional imports cannot claim a finalized timestamp")
    rows = tuple(ActualObservation.from_value(item) for item in observations)
    if not rows:
        raise ValueError("source actual import requires observations")
    if len({row.observation_id for row in rows}) != len(rows):
        raise ValueError("source observation IDs must be unique")
    invalid = {
        row.purchase_model
        for row in rows
        if row.purchase_model not in _SOURCE_PURCHASE_MODELS[source]
    }
    if invalid:
        raise ValueError(
            f"{source.value} cannot report purchase models: "
            + ", ".join(sorted(item.value for item in invalid))
        )
    invalid_routes = {row.route_id for row in rows if row.route_id not in _SOURCE_ROUTES[source]}
    if invalid_routes:
        raise ValueError(
            f"{source.value} cannot report routes: "
            + ", ".join(sorted(invalid_routes))
        )
    payload = {
        "schema_version": SOURCE_ACTUAL_SCHEMA_VERSION,
        "import_id": import_id,
        "source": source.value,
        "source_revision": source_revision,
        "source_export_hash": source_export_hash,
        "billing_period": billing_period,
        "refreshed_at": refreshed_at,
        "finalization_state": state.value,
        "finalized_at": finalized_at,
        "observations": [row.to_dict() for row in rows],
    }
    payload["content_hash"] = _digest(payload)
    return payload


class SourceActualImportStore:
    """Append-only persistence for independently refreshed source observations."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def append(self, actual_import: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        payload = dict(actual_import)
        import_id = _required(payload.get("import_id"), "import_id")
        if payload.get("schema_version") != SOURCE_ACTUAL_SCHEMA_VERSION:
            raise ValueError("source actual import schema version is unsupported")
        if payload.get("content_hash") != _digest(
            {field: value for field, value in payload.items() if field != "content_hash"}
        ):
            raise ValueError("source actual import content hash is invalid")
        rebuilt = build_source_actual_import(
            source=payload.get("source"),
            import_id=payload.get("import_id"),
            source_revision=payload.get("source_revision"),
            source_export_hash=payload.get("source_export_hash"),
            billing_period=payload.get("billing_period"),
            refreshed_at=payload.get("refreshed_at"),
            finalization_state=payload.get("finalization_state"),
            finalized_at=payload.get("finalized_at"),
            observations=payload.get("observations", []),
        )
        if rebuilt != payload:
            raise ValueError("source actual import contract validation failed")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{hashlib.sha256(import_id.encode()).hexdigest()}.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing != payload:
                raise ValueError("source actual import identity collision")
            return existing, False
        temporary = self.root / f".{path.stem}.{uuid4().hex}.tmp"
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


@dataclass(frozen=True)
class ForecastQuantity:
    forecast_line_id: str
    forecast_evidence_hash: str
    route_id: str
    target_leg: str
    meter_family: ConsumptionFamily
    meter_id: str
    native_unit: str
    native_currency: str
    purchase_model: PurchaseModel
    billing_period: str
    quantity: float

    def __post_init__(self) -> None:
        for field in (
            "forecast_line_id",
            "route_id",
            "target_leg",
            "meter_id",
            "native_unit",
            "native_currency",
            "billing_period",
        ):
            _required(getattr(self, field), field)
        if self.route_id not in _ROUTES:
            raise ValueError(f"unsupported route: {self.route_id}")
        _hash(self.forecast_evidence_hash, "forecast_evidence_hash")
        if not isinstance(self.meter_family, ConsumptionFamily):
            raise ValueError("meter_family is invalid")
        if not isinstance(self.purchase_model, PurchaseModel):
            raise ValueError("purchase_model is invalid")
        _amount(self.quantity, "forecast quantity")

    @classmethod
    def from_value(
        cls, value: "ForecastQuantity | Mapping[str, Any]"
    ) -> "ForecastQuantity":
        if isinstance(value, cls):
            return value
        values = dict(value)
        values["meter_family"] = ConsumptionFamily(values.get("meter_family"))
        values["purchase_model"] = PurchaseModel(values.get("purchase_model"))
        return cls(**values)

    def key(self) -> tuple[str, ...]:
        return (
            self.route_id,
            self.target_leg,
            self.meter_family.value,
            self.meter_id,
            self.native_unit,
            self.native_currency,
            self.purchase_model.value,
            self.billing_period,
        )


@dataclass(frozen=True)
class ApprovedAllocation:
    allocation_id: str
    observation_id: str
    allocation_revision: str
    allocation_content_hash: str
    allocation_basis: str
    allocated_cost_usd: float

    def __post_init__(self) -> None:
        for field in (
            "allocation_id",
            "observation_id",
            "allocation_revision",
            "allocation_basis",
        ):
            _required(getattr(self, field), field)
        _hash(self.allocation_content_hash, "allocation_content_hash")
        _amount(self.allocated_cost_usd, "allocated_cost_usd")

    @classmethod
    def from_value(
        cls, value: "ApprovedAllocation | Mapping[str, Any]"
    ) -> "ApprovedAllocation":
        return value if isinstance(value, cls) else cls(**dict(value))


def _observation_key(row: ActualObservation, billing_period: str) -> tuple[str, ...]:
    return (
        row.route_id,
        row.target_leg,
        row.meter_family.value,
        row.meter_id,
        row.native_unit,
        row.native_currency,
        row.purchase_model.value,
        billing_period,
    )


def build_composite_reconciliation(
    *,
    reconciliation_scope_id: str,
    forecasts: Iterable[ForecastQuantity | Mapping[str, Any]],
    source_imports: Iterable[Mapping[str, Any]],
    approved_allocations: Iterable[ApprovedAllocation | Mapping[str, Any]],
    created_at: str,
) -> dict[str, Any]:
    """Reconcile native quantities first, then apply only approved USD allocation."""
    _required(reconciliation_scope_id, "reconciliation_scope_id")
    _utc(created_at, "created_at")
    forecast_rows = tuple(ForecastQuantity.from_value(item) for item in forecasts)
    imports = tuple(dict(item) for item in source_imports)
    allocations = tuple(
        ApprovedAllocation.from_value(item) for item in approved_allocations
    )
    if not forecast_rows or not imports:
        raise ValueError("composite reconciliation requires forecasts and actual imports")
    if len({item.get("import_id") for item in imports}) != len(imports):
        raise ValueError("source import IDs must be unique")
    forecast_by_key = {row.key(): row for row in forecast_rows}
    if len(forecast_by_key) != len(forecast_rows):
        raise ValueError("forecast native-meter keys must be unique")
    observation_records: list[tuple[ActualObservation, Mapping[str, Any]]] = []
    for actual_import in imports:
        if actual_import.get("schema_version") != SOURCE_ACTUAL_SCHEMA_VERSION:
            raise ValueError("source import schema version is unsupported")
        content_hash = actual_import.get("content_hash")
        if content_hash != _digest(
            {key: value for key, value in actual_import.items() if key != "content_hash"}
        ):
            raise ValueError("source actual import integrity check failed")
        rebuilt = build_source_actual_import(
            source=actual_import.get("source"),
            import_id=actual_import.get("import_id"),
            source_revision=actual_import.get("source_revision"),
            source_export_hash=actual_import.get("source_export_hash"),
            billing_period=actual_import.get("billing_period"),
            refreshed_at=actual_import.get("refreshed_at"),
            finalization_state=actual_import.get("finalization_state"),
            finalized_at=actual_import.get("finalized_at"),
            observations=actual_import.get("observations", []),
        )
        if rebuilt != actual_import:
            raise ValueError("source actual import contract validation failed")
        for raw in actual_import.get("observations", []):
            observation_records.append(
                (ActualObservation.from_value(raw), actual_import)
            )
    observation_ids = [row.observation_id for row, _ in observation_records]
    if len(observation_ids) != len(set(observation_ids)):
        raise ValueError("observation IDs must be unique across source imports")
    allocation_by_observation = {
        allocation.observation_id: allocation for allocation in allocations
    }
    if len(allocation_by_observation) != len(allocations):
        raise ValueError("one approved allocation is allowed per observation")
    unknown_allocations = set(allocation_by_observation) - set(observation_ids)
    if unknown_allocations:
        raise ValueError("allocation references an unknown actual observation")

    observations_by_key: dict[
        tuple[str, ...], list[tuple[ActualObservation, Mapping[str, Any]]]
    ] = {}
    for row, actual_import in observation_records:
        observations_by_key.setdefault(
            _observation_key(row, actual_import["billing_period"]), []
        ).append((row, actual_import))

    variances = []
    coverage_gaps = []
    for key, forecast in sorted(forecast_by_key.items()):
        actuals = observations_by_key.get(key, [])
        if not actuals:
            coverage_gaps.append(
                {
                    "reason": "missing_actual",
                    "forecast_line_id": forecast.forecast_line_id,
                    "forecast_evidence_hash": forecast.forecast_evidence_hash,
                    "actual_evidence_hashes": [],
                }
            )
            continue
        actual_quantity = sum(row.quantity for row, _ in actuals)
        variances.append(
            {
                "reconciliation_stage": "native_quantity_before_money",
                "route_id": forecast.route_id,
                "target_leg": forecast.target_leg,
                "meter_family": forecast.meter_family.value,
                "meter_id": forecast.meter_id,
                "native_unit": forecast.native_unit,
                "native_currency": forecast.native_currency,
                "purchase_model": forecast.purchase_model.value,
                "billing_period": forecast.billing_period,
                "forecast_quantity": forecast.quantity,
                "actual_quantity": actual_quantity,
                "quantity_variance": actual_quantity - forecast.quantity,
                "forecast_evidence_hash": forecast.forecast_evidence_hash,
                "actual_evidence_hashes": sorted(
                    {
                        actual_import["content_hash"]
                        for _, actual_import in actuals
                    }
                    | {row.content_hash for row, _ in actuals}
                ),
            }
        )

    monetary_allocations = []
    unattributed = []
    for row, actual_import in observation_records:
        key = _observation_key(row, actual_import["billing_period"])
        allocation = allocation_by_observation.get(row.observation_id)
        if key not in forecast_by_key or allocation is None:
            unattributed.append(
                {
                    "observation_id": row.observation_id,
                    "source_import_hash": actual_import["content_hash"],
                    "observation_content_hash": row.content_hash,
                    "purchase_model": row.purchase_model.value,
                    "quantity": row.quantity,
                    "native_unit": row.native_unit,
                    "actual_cost": row.actual_cost,
                    "cost_currency": row.cost_currency,
                    "reason": (
                        "no_matching_forecast"
                        if key not in forecast_by_key
                        else "no_approved_allocation"
                    ),
                }
            )
            continue
        monetary_allocations.append(
            {
                "observation_id": row.observation_id,
                "purchase_model": row.purchase_model.value,
                "allocated_cost_usd": allocation.allocated_cost_usd,
                "allocation_id": allocation.allocation_id,
                "allocation_revision": allocation.allocation_revision,
                "allocation_content_hash": allocation.allocation_content_hash,
                "allocation_basis": allocation.allocation_basis,
                "actual_import_hash": actual_import["content_hash"],
                "observation_content_hash": row.content_hash,
            }
        )

    purchase_views = []
    for purchase_model in PurchaseModel:
        native_rows = [
            row for row, _ in observation_records if row.purchase_model is purchase_model
        ]
        allocated_rows = [
            row
            for row in monetary_allocations
            if row["purchase_model"] == purchase_model.value
        ]
        purchase_views.append(
            {
                "purchase_model": purchase_model.value,
                "observation_count": len(native_rows),
                "native_quantities": [
                    {
                        "native_unit": unit,
                        "quantity": sum(
                            row.quantity for row in native_rows if row.native_unit == unit
                        ),
                    }
                    for unit in sorted({row.native_unit for row in native_rows})
                ],
                "approved_allocated_cost_usd": sum(
                    row["allocated_cost_usd"] for row in allocated_rows
                ),
            }
        )

    source_refs = [
        {
            "import_id": item["import_id"],
            "source": item["source"],
            "content_hash": item["content_hash"],
            "source_revision": item["source_revision"],
            "billing_period": item["billing_period"],
            "refreshed_at": item["refreshed_at"],
            "finalization_state": item["finalization_state"],
            "finalized_at": item["finalized_at"],
        }
        for item in imports
    ]
    idempotency_basis = {
        "scope": reconciliation_scope_id,
        "forecast_hashes": sorted(row.forecast_evidence_hash for row in forecast_rows),
        "source_import_hashes": sorted(item["content_hash"] for item in imports),
        "allocation_hashes": sorted(
            allocation.allocation_content_hash for allocation in allocations
        ),
    }
    payload = {
        "schema_version": COMPOSITE_RECONCILIATION_SCHEMA_VERSION,
        "reconciliation_scope_id": reconciliation_scope_id,
        "created_at": created_at,
        "status": (
            "finalized"
            if not coverage_gaps
            and all(
                item["finalization_state"] == FinalizationState.FINALIZED.value
                for item in imports
            )
            else "provisional"
        ),
        "reconciliation_order": [
            "native_quantity_variance",
            "approved_monetary_allocation",
        ],
        "source_imports": source_refs,
        "native_variances": variances,
        "monetary_allocations": monetary_allocations,
        "purchase_views": purchase_views,
        "unattributed": unattributed,
        "coverage_gaps": coverage_gaps,
        "idempotency_key": _digest(idempotency_basis),
        "source_observations_mutated": False,
        "historical_forecasts_mutated": False,
    }
    payload["reconciliation_id"] = (
        f"composite-reconciliation-{payload['idempotency_key'][:32]}"
    )
    payload["content_hash"] = _digest(payload)
    return payload


class CompositeReconciliationStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def append(self, evidence: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        payload = dict(evidence)
        key = _hash(payload.get("idempotency_key"), "idempotency_key")
        content_hash = payload.get("content_hash")
        if content_hash != _digest(
            {field: value for field, value in payload.items() if field != "content_hash"}
        ):
            raise ValueError("composite reconciliation content hash is invalid")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{key}.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing != payload:
                raise ValueError("composite reconciliation idempotency collision")
            return existing, False
        temporary = self.root / f".{key}.{uuid4().hex}.tmp"
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
