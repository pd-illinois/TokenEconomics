"""Immutable source billing and conservative, run-day resource allocation.

The ingestion adapter verifies original export/query bytes. This module preserves
that source hash and verifies normalized evidence, not Azure source authenticity.
Resource billing is not catalog model usage, quality acceptance, or task cost.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from .atomic_publish import publish_immutable


SNAPSHOT_VERSION = "billing-snapshot.v1"
ALLOCATION_VERSION = "billing-allocation.v1"
_SNAPSHOT_KEYS = {
    "schema_version", "source", "period", "retrieved_at", "allowed_resource_ids",
    "rows", "row_count", "totals_by_currency", "observed_dates",
    "latest_observed_date", "excluded_row_count",
}
_ROW_KEYS = {
    "date", "resource_id", "meter_id", "meter_name", "service_name", "quantity",
    "unit_of_measure", "cost", "currency", "source_row_hash", "source_row_index",
}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _copy(value):
    try:
        return json.loads(_canonical(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("Billing evidence must be finite JSON data") from exc


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("A lowercase SHA-256 evidence hash is required")
    return value


def _iso_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("An ISO calendar date is required")
    return date.fromisoformat(value).isoformat()


def _export_date(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("Invalid billing date")
    value = str(value).strip()
    if re.fullmatch(r"\d{8}", value):
        value = f"{value[:4]}-{value[4:6]}-{value[6:]}"
    elif re.fullmatch(r"\d{2}/\d{2}/\d{4}", value):
        value = f"{value[6:]}-{value[:2]}-{value[3:5]}"
    return _iso_date(value)


def _timestamp(value):
    value = _text(value, "retrieved_at")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("A timezone-aware retrieval timestamp is required") from exc
    if result.tzinfo is None:
        raise ValueError("A timezone-aware retrieval timestamp is required")
    return result.astimezone(timezone.utc)


def _number(value, field, *, raw=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, str) if raw else (int, float)):
        raise ValueError(f"{field} must be a finite number")
    if isinstance(value, str) and not re.fullmatch(
        r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", value.strip()
    ):
        raise ValueError(f"{field} must be a finite number")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    if result == 0 and Decimal(str(value).strip()) != 0:
        raise ValueError(f"{field} is too small to represent")
    return result


def _currency(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{3}", value):
        raise ValueError("An explicit uppercase three-letter billing currency is required")
    return value


def _resource(value):
    value = _text(value, "resource_id")
    if (not value.startswith("/") or "\\" in value or "?" in value or "#" in value
            or re.search(r"[\x00-\x20\x7f]", value)
            or any(part in {"", ".", ".."} for part in value[1:].split("/"))):
        raise ValueError("An exact resource ID is required")
    return value.lower()


def _allowlist(values):
    if not isinstance(values, list) or not values:
        raise ValueError("A nonempty exact resource allowlist is required")
    result = [_resource(value) for value in values]
    if len(result) != len(set(result)):
        raise ValueError("Resource IDs must be unique")
    return sorted(result)


def _source(source):
    if not isinstance(source, dict) or source.get("kind") not in {
        "azure_cost_management_export", "azure_cost_management_query",
    }:
        raise ValueError("A supported Azure billing source is required")
    _resource(source.get("source_id"))
    _text(source.get("revision"), "source revision")
    _sha(source.get("content_hash"))
    return _copy(source)


def _alias(row, *names):
    matches = [row[name] for name in names if name in row and row[name] not in (None, "")]
    if len(matches) > 1 and any(value != matches[0] for value in matches[1:]):
        raise ValueError(f"Conflicting billing aliases for {names[0]}")
    return matches[0] if matches else None


def _optional(value, field):
    return None if value is None or value == "" else _text(value, field)


def _totals(rows, field="cost"):
    totals = {}
    for row in rows:
        currency = row["currency"]
        totals[currency] = totals.get(currency, Decimal(0)) + Decimal(str(row[field]))
    return {key: _number(str(value), "currency total", raw=True)
            for key, value in sorted(totals.items())}


def normalize_rows(
    rows: list[dict], *, source: dict, period_start: str, period_end: str,
    allowed_resource_ids: list[str], retrieved_at: str,
) -> dict:
    """Normalize one complete source revision without deduplicating charge rows.

    Foreign resources and dates outside the inclusive source period are counted
    as exclusions. Missing meter/service/quantity dimensions remain ``None``.
    ``source_row_hash`` pins both the raw row and its original export position.
    """
    start, end = _iso_date(period_start), _iso_date(period_end)
    if start > end:
        raise ValueError("Billing period end precedes start")
    source = _source(source)
    allowed = _allowlist(allowed_resource_ids)
    retrieved_at = _timestamp(retrieved_at).isoformat()
    if not isinstance(rows, list):
        raise ValueError("Billing rows must be a list")
    normalized, excluded = [], 0
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError("Each billing row must be an object")
        resource = _alias(raw, "ResourceId", "ResourceID", "resourceId", "resource_id")
        if resource is None:
            excluded += 1
            continue
        resource = _resource(resource)
        if resource not in allowed:
            excluded += 1
            continue
        day = _export_date(_alias(raw, "Date", "UsageDate", "date", "usageDate"))
        if not start <= day <= end:
            excluded += 1
            continue
        quantity = _alias(raw, "Quantity", "quantity")
        normalized.append({
            "date": day,
            "resource_id": resource,
            "meter_id": _optional(_alias(raw, "MeterId", "MeterID", "meterId", "meter_id"), "meter_id"),
            "meter_name": _optional(_alias(raw, "MeterName", "meterName", "meter_name"), "meter_name"),
            "service_name": _optional(_alias(raw, "ServiceName", "serviceName", "service_name")
                                      or _alias(raw, "ConsumedService", "consumedService"), "service_name"),
            "quantity": None if quantity is None else _number(quantity, "quantity", raw=True),
            "unit_of_measure": _optional(_alias(raw, "UnitOfMeasure", "unitOfMeasure", "unit_of_measure"), "unit_of_measure"),
            "cost": _number(_alias(raw, "CostInBillingCurrency", "Cost", "costInBillingCurrency", "cost"), "cost", raw=True),
            "currency": _currency(_alias(raw, "BillingCurrencyCode", "Currency", "billingCurrencyCode",
                                         "currency", "BillingCurrency", "billingCurrency")),
            "source_row_hash": _hash({"source_row_index": index, "row": _copy(raw)}),
            "source_row_index": index,
        })
    observed = sorted({row["date"] for row in normalized})
    result = {
        "schema_version": SNAPSHOT_VERSION,
        "source": source,
        "period": {"start": start, "end": end},
        "retrieved_at": retrieved_at,
        "allowed_resource_ids": allowed,
        "rows": normalized,
        "row_count": len(normalized),
        "totals_by_currency": _totals(normalized),
        "observed_dates": observed,
        "latest_observed_date": observed[-1] if observed else None,
        "excluded_row_count": excluded,
    }
    _validate(result)
    return result


def _identity(snapshot):
    return "billing-" + _hash({
        key: value for key, value in snapshot.items()
        if key not in {"retrieved_at", "snapshot_id", "content_hash"}
    })


def _validate(snapshot, *, sealed=False):
    keys = _SNAPSHOT_KEYS | ({"snapshot_id", "content_hash"} if sealed else set())
    if not isinstance(snapshot, dict) or set(snapshot) != keys:
        raise ValueError("Invalid billing snapshot fields")
    if snapshot["schema_version"] != SNAPSHOT_VERSION:
        raise ValueError("Unsupported billing snapshot schema")
    _source(snapshot["source"])
    if snapshot["allowed_resource_ids"] != _allowlist(snapshot["allowed_resource_ids"]):
        raise ValueError("Snapshot resource scope must be canonical")
    _timestamp(snapshot["retrieved_at"])
    period = snapshot["period"]
    if not isinstance(period, dict) or set(period) != {"start", "end"}:
        raise ValueError("Invalid billing period")
    start, end = _iso_date(period["start"]), _iso_date(period["end"])
    if start > end:
        raise ValueError("Invalid billing period")
    rows = snapshot["rows"]
    if not isinstance(rows, list):
        raise ValueError("Invalid billing rows")
    for field in ("row_count", "excluded_row_count"):
        if type(snapshot[field]) is not int or snapshot[field] < 0:
            raise ValueError("Invalid billing row count")
    if snapshot["row_count"] != len(rows):
        raise ValueError("Billing row count mismatch")
    indices, hashes = [], set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != _ROW_KEYS:
            raise ValueError("Invalid normalized billing row")
        if not start <= _iso_date(row["date"]) <= end:
            raise ValueError("Billing row is outside source period")
        if (_resource(row["resource_id"]) != row["resource_id"]
                or row["resource_id"] not in snapshot["allowed_resource_ids"]):
            raise ValueError("Billing row is outside resource scope")
        _number(row["cost"], "cost")
        _currency(row["currency"])
        if row["quantity"] is not None:
            _number(row["quantity"], "quantity")
        for field in ("meter_id", "meter_name", "service_name", "unit_of_measure"):
            if row[field] is not None:
                _text(row[field], field)
        _sha(row["source_row_hash"])
        index = row["source_row_index"]
        if type(index) is not int or not 0 <= index < len(rows) + snapshot["excluded_row_count"]:
            raise ValueError("Invalid original source row index")
        indices.append(index)
        if row["source_row_hash"] in hashes:
            raise ValueError("Duplicate source row position evidence")
        hashes.add(row["source_row_hash"])
    if indices != sorted(set(indices)):
        raise ValueError("Source row positions must be unique and ordered")
    observed = sorted({row["date"] for row in rows})
    if (snapshot["observed_dates"] != observed
            or snapshot["latest_observed_date"] != (observed[-1] if observed else None)):
        raise ValueError("Billing observed dates mismatch")
    totals = snapshot["totals_by_currency"]
    if not isinstance(totals, dict):
        raise ValueError("Invalid billing totals")
    for currency, value in totals.items():
        _currency(currency)
        _number(value, "currency total")
    if totals != _totals(rows):
        raise ValueError("Billing currency totals mismatch")
    if sealed and (
        snapshot["snapshot_id"] != _identity(snapshot)
        or _sha(snapshot["content_hash"]) != _hash({
            key: value for key, value in snapshot.items() if key != "content_hash"
        })
    ):
        raise ValueError("Billing snapshot integrity check failed")


class BillingSnapshotStore:
    """Atomic, append-only revisions; ``latest`` returns one revision, not a sum."""

    def __init__(self, root):
        self.root = Path(root)

    def _path(self, snapshot_id):
        if not isinstance(snapshot_id, str) or not re.fullmatch(r"billing-[a-f0-9]{64}", snapshot_id):
            raise ValueError("Invalid billing snapshot identifier")
        path = self.root / f"{snapshot_id}.json"
        if path.is_symlink() or not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("Billing evidence path escapes its store")
        return path

    def append(self, snapshot) -> tuple[dict, bool]:
        snapshot = _copy(snapshot)
        if not isinstance(snapshot, dict):
            raise ValueError("A billing snapshot object is required")
        sealed = "snapshot_id" in snapshot or "content_hash" in snapshot
        _validate(snapshot, sealed=sealed)
        snapshot_id = _identity(snapshot)
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(snapshot_id)
        if path.exists():
            return self.get(snapshot_id), False
        stored = {**snapshot, "snapshot_id": snapshot_id}
        stored.pop("content_hash", None)
        stored["content_hash"] = _hash(stored)
        pending = self.root / f".{snapshot_id}-{uuid4().hex}.pending"
        try:
            with pending.open("x", encoding="utf-8") as stream:
                stream.write(_canonical(stored))
                stream.flush()
                os.fsync(stream.fileno())
            try:
                publish_immutable(pending, path)
            except FileExistsError:
                return self.get(snapshot_id), False
        finally:
            pending.unlink(missing_ok=True)
        return self.get(snapshot_id), True

    def get(self, snapshot_id) -> dict:
        path = self._path(snapshot_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise KeyError("Billing snapshot not found") from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Unreadable billing snapshot") from exc
        _validate(value, sealed=True)
        if value["snapshot_id"] != snapshot_id:
            raise ValueError("Billing snapshot disk identity mismatch")
        return value

    def list(self) -> list[dict]:
        records = [self.get(path.stem) for path in self.root.glob("*.json")]
        return sorted(records, key=lambda item: (_timestamp(item["retrieved_at"]), item["snapshot_id"]))

    def latest(self) -> dict | None:
        records = self.list()
        return records[-1] if records else None


def _binding(binding, run_date, allowed):
    if not isinstance(binding, dict) or binding.get("schema_version") != "workload-resource-binding.v1":
        raise ValueError("A versioned workload-resource binding is required")
    _text(binding.get("binding_id"), "binding_id")
    _text(binding.get("revision"), "binding revision")
    start, end = _iso_date(binding.get("valid_from")), _iso_date(binding.get("valid_to"))
    if not start <= run_date <= end:
        raise ValueError("Resource binding is not valid for the run date")
    resources = binding.get("resources")
    if not isinstance(resources, list) or not resources:
        raise ValueError("Bound resources are required")
    seen = set()
    for resource in resources:
        if not isinstance(resource, dict):
            raise ValueError("Invalid bound resource")
        identity = _resource(resource.get("resource_id"))
        if identity not in allowed or identity in seen:
            raise ValueError("Binding has a foreign or duplicated resource")
        seen.add(identity)
        _text(resource.get("role"), "resource role")
        if resource.get("allocation_method") not in {"unallocated", "measured_share"}:
            raise ValueError("Unsupported resource allocation method")
    return _copy(binding)


def _share(rule, binding, run_id, run_date, groups, resources):
    if not isinstance(rule, dict):
        raise ValueError("Invalid measured-share rule")
    resource = _resource(rule.get("resource_id"))
    currency = _currency(rule.get("currency"))
    key = resource, currency
    if key not in groups or resources[resource]["allocation_method"] != "measured_share":
        raise ValueError("Measured share has no eligible resource-day billing")
    if (rule.get("date") != run_date or rule.get("run_id") != run_id
            or rule.get("period_start") != run_date or rule.get("period_end") != run_date):
        raise ValueError("Allocation evidence must match this run and its exact day")
    revision = _text(binding.get("policy_revision"), "binding policy_revision")
    if rule.get("policy_revision") != revision:
        raise ValueError("Allocation policy revision mismatch")
    if rule.get("denominator_complete") is not True:
        raise ValueError("A complete independently measured denominator is required")
    # These native meters can support real shared-resource denominators. Workload
    # task/response/sample counts alone never establish a resource usage share.
    if rule.get("measurement_unit") not in {
        "cpu_seconds", "gpu_seconds", "byte_seconds", "bytes", "resource_seconds", "meter_quantity",
    }:
        raise ValueError("A supported native resource measurement unit is required")
    numerator_ref = _text(rule.get("numerator_ref"), "numerator_ref")
    denominator_ref = _text(rule.get("denominator_ref"), "denominator_ref")
    if numerator_ref == denominator_ref:
        raise ValueError("Numerator and complete denominator require distinct evidence")
    numerator = Decimal(str(_number(rule.get("numerator"), "numerator")))
    denominator = Decimal(str(_number(rule.get("denominator"), "denominator")))
    if denominator <= 0 or not 0 <= numerator <= denominator:
        raise ValueError("Measured share must be between zero and one with a positive denominator")
    return key, numerator / denominator


def allocate_snapshot(
    snapshot: dict, *, binding: dict, run_id: str, run_date: str,
    allocations: list[dict] | None = None,
) -> dict:
    """Attribute only run-day billing with explicit complete-denominator evidence.

    A measured-share rule uses resource_id/currency/date/run_id, period_start/end
    (both run_date), policy_revision (matching binding.policy_revision), finite
    numerator/denominator, distinct numerator_ref/denominator_ref, native
    measurement_unit, and denominator_complete=True. One rule per resource/day/
    currency is allowed. Missing rules remain partial, even for observed zero.
    Allocation conservation covers only this record, not concurrent other runs.
    """
    snapshot = _copy(snapshot)
    if not isinstance(snapshot, dict):
        raise ValueError("A billing snapshot object is required")
    _validate(snapshot, sealed="snapshot_id" in snapshot or "content_hash" in snapshot)
    run_date = _iso_date(run_date)
    _text(run_id, "run_id")
    binding = _binding(binding, run_date, snapshot["allowed_resource_ids"])
    resources = {_resource(item["resource_id"]): item for item in binding["resources"]}
    selected = [row for row in snapshot["rows"]
                if row["date"] == run_date and row["resource_id"] in resources]
    groups = {}
    for row in selected:
        groups.setdefault((row["resource_id"], row["currency"]), []).append(row)
    if allocations is None:
        allocations = []
    if not isinstance(allocations, list):
        raise ValueError("Allocations must be a list")
    shares = {}
    for rule in allocations:
        key, share = _share(rule, binding, run_id, run_date, groups, resources)
        if key in shares:
            raise ValueError("Duplicate allocation would risk oversubscribing resource billing")
        shares[key] = share
    allocated_rows, unallocated_rows, coverage = [], [], []
    for resource_id, resource in sorted(resources.items()):
        rows = [row for row in selected if row["resource_id"] == resource_id]
        missing_rules = sorted({row["currency"] for row in rows
                                if (resource_id, row["currency"]) not in shares})
        for row in rows:
            share = shares.get((resource_id, row["currency"]), Decimal(0))
            cost = Decimal(str(row["cost"]))
            allocated = _number(str(cost * share), "allocated cost", raw=True)
            unallocated = _number(str(cost - Decimal(str(allocated))), "unallocated cost", raw=True)
            allocated_rows.append({"currency": row["currency"], "cost": allocated})
            unallocated_rows.append({"currency": row["currency"], "cost": unallocated})
        coverage.append({
            "resource_id": resource_id,
            "role": resource["role"],
            "allocation_method": resource["allocation_method"],
            "status": "missing_billing_data" if not rows else "unallocated" if missing_rules else "allocated",
            "row_count": len(rows),
            "observed_dates": sorted({row["date"] for row in snapshot["rows"]
                                      if row["resource_id"] == resource_id}),
            "totals_by_currency": _totals(rows),
            "missing_allocation_currencies": missing_rules,
        })
    missing = [item["resource_id"] for item in coverage if item["row_count"] == 0]
    status = ("awaiting_billing_data" if not selected else
              "partial" if any(item["status"] != "allocated" for item in coverage) else "allocated")
    latest_date = snapshot["latest_observed_date"]
    retrieved_date = _timestamp(snapshot["retrieved_at"]).date()
    snapshot_id = snapshot.get("snapshot_id", _identity(snapshot))
    snapshot_hash = snapshot.get("content_hash")
    if snapshot_hash is None:
        snapshot_hash = _hash({**snapshot, "snapshot_id": snapshot_id})
    result = {
        "schema_version": ALLOCATION_VERSION,
        "status": status,
        "evidence_classification": "measured_billing_partial_scope",
        "cost_scope": "bound_resource_billing_for_run_day",
        "run_id": run_id,
        "run_date": run_date,
        "snapshot_id": snapshot_id,
        "snapshot_hash": snapshot_hash,
        "binding": binding,
        "binding_hash": _hash(binding),
        "allocations": _copy(allocations),
        "period": {"start": run_date, "end": run_date},
        "source_period": snapshot["period"],
        "source_period_totals_by_currency": snapshot["totals_by_currency"],
        "rows": selected,
        "row_count": len(selected),
        "totals_by_currency": _totals(selected),
        "allocated_by_currency": _totals(allocated_rows),
        "unallocated_by_currency": _totals(unallocated_rows),
        "resource_coverage": coverage,
        "missing_resource_ids": missing,
        "unallocated_resource_ids": [item["resource_id"] for item in coverage if item["status"] == "unallocated"],
        "latest_observed_date": latest_date,
        "freshness": {
            "retrieved_at": snapshot["retrieved_at"],
            "latest_observed_date": latest_date,
            "billing_lag_days_at_retrieval": (retrieved_date - date.fromisoformat(latest_date)).days if latest_date else None,
            "run_date_observed": bool(selected),
            "run_date_in_source_period": snapshot["period"]["start"] <= run_date <= snapshot["period"]["end"],
            "billing_finality": "not_established",
        },
        "catalog_model_usage_included": False,
        "complete_task_cost": False,
        "full_task_cost_usd": None,
        "quality_acceptance": "not_evaluated",
    }
    result["allocation_id"] = "allocation-" + _hash(result)
    result["content_hash"] = _hash(result)
    return result
