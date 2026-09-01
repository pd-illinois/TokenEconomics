from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from costgov.consumption_models import ConsumptionFamily
from costgov.meter_ledger import (
    METER_LEDGER_SCHEMA_VERSION,
    CostCoverage,
    MeterEvidenceStatus,
    MeterLedgerEntry,
    MeterLedgerStore,
    aggregate_meter_entries,
    entries_from_gateway_record,
    reconcile_meter_quantity,
)

ROOT = Path(__file__).resolve().parents[1]


def _entry(**changes) -> MeterLedgerEntry:
    values = {
        "schema_version": METER_LEDGER_SCHEMA_VERSION,
        "entry_id": "entry-1",
        "experiment_id": "experiment-1",
        "experiment_revision": "v1",
        "arm_id": "candidate",
        "task_id": "task-1",
        "trajectory_id": "trajectory-1",
        "step_id": "step-1",
        "segment_id": "hard",
        "tenant_id": "tenant-1",
        "product": "copilot_studio",
        "environment": "test",
        "meter_stack_id": "copilot-studio-meter-stack",
        "meter_stack_version": "consumption-models.v1",
        "meter_stack_content_hash": "c" * 64,
        "policy_candidate_id": "candidate-1",
        "policy_candidate_version": "v1",
        "policy_candidate_content_hash": "b" * 64,
        "meter_family": ConsumptionFamily.NATIVE_CREDIT,
        "meter_id": "copilot_studio_events",
        "native_unit": "Microsoft Copilot Credit",
        "native_currency": "Microsoft Copilot Credits",
        "quantity": 25.0,
        "evidence_status": MeterEvidenceStatus.MEASURED,
        "entitlement_disposition": "included",
        "purchase_source": "tenant_pack",
        "evidence_source": "microsoft_cost_management",
        "evidence_content_hash": "a" * 64,
        "pricing_revision": None,
        "rate_card_revision": None,
        "billing_period": "2026-08",
        "calculation_method": "provider_reported_quantity",
        "allocation_method": "direct_to_task",
        "cost_coverage": CostCoverage.NOT_APPLICABLE,
        "allocated_cost_usd": None,
        "recorded_at": "2026-08-31T18:00:00+00:00",
        "unavailable_reason": None,
    }
    values.update(changes)
    return MeterLedgerEntry(**values)


def test_native_meter_aggregation_does_not_collapse_currencies():
    credits = _entry()
    tokens = _entry(
        entry_id="entry-2",
        meter_family=ConsumptionFamily.DIRECT_TOKEN,
        meter_id="foundry_model",
        native_unit="model_token",
        native_currency="USD",
        quantity=1200,
        entitlement_disposition="pay_as_you_go",
        purchase_source="azure_consumption",
        pricing_revision="pricing.v1",
        cost_coverage=CostCoverage.PRICED,
        allocated_cost_usd=0.012,
    )

    aggregates = aggregate_meter_entries((credits, tokens))

    assert len(aggregates) == 2
    assert {item.native_currency for item in aggregates} == {
        "Microsoft Copilot Credits",
        "USD",
    }
    assert next(
        item for item in aggregates if item.meter_family is ConsumptionFamily.NATIVE_CREDIT
    ).known_quantity == 25


def test_included_usage_keeps_quantity_and_unknown_cost_is_not_zero():
    included = _entry(
        meter_family=ConsumptionFamily.INCLUDED,
        meter_id="qualifying_native_use",
        native_unit="experience_use",
        native_currency="included",
        quantity=3,
        cost_coverage=CostCoverage.NOT_APPLICABLE,
        allocated_cost_usd=None,
    )
    unavailable = _entry(
        entry_id="entry-unknown",
        meter_family=ConsumptionFamily.RESOURCE,
        meter_id="foundry_resources",
        native_unit="resource_unit",
        native_currency="USD",
        quantity=None,
        evidence_status=MeterEvidenceStatus.UNAVAILABLE,
        cost_coverage=CostCoverage.UNPRICED,
        unavailable_reason="No resource-meter export was available.",
    )

    assert included.quantity == 3
    assert included.allocated_cost_usd is None
    assert unavailable.allocated_cost_usd is None
    assert aggregate_meter_entries((unavailable,))[0].cost_coverage_complete is False
    with pytest.raises(ValueError, match="cannot carry"):
        replace(unavailable, allocated_cost_usd=0)


def test_gateway_translation_records_tokens_evaluation_and_uncovered_resources():
    telemetry = SimpleNamespace(
        timestamp="2026-08-31T18:00:00+00:00",
        task_id="task-1",
        trajectory_id="trajectory-1",
        segment_id="hard",
        tenant="tenant-1",
        policy_id="active-policy",
        policy_version="active.v1",
        policy_candidate_id="candidate-1",
        policy_candidate_version="candidate.v1",
        policy_candidate_content_hash="b" * 64,
        model="premium",
        cache_hit=False,
        input_tokens=100,
        output_tokens=20,
        cost_usd=0.01,
    )

    entries = entries_from_gateway_record(
        telemetry,
        experiment_id="experiment-1",
        experiment_revision="v1",
        arm_id="candidate",
        environment="test",
        meter_stack_id="foundry-meter-stack",
        meter_stack_version="consumption-models.v1",
        meter_stack_content_hash="c" * 64,
        evaluation_performed=True,
    )

    assert [entry.meter_family for entry in entries] == [
        ConsumptionFamily.DIRECT_TOKEN,
        ConsumptionFamily.EVALUATION,
        ConsumptionFamily.RESOURCE,
    ]
    assert entries[0].quantity == 120
    assert entries[0].allocated_cost_usd == 0.01
    assert entries[0].policy_candidate_id == "candidate-1"
    assert entries[1].quantity == 1
    assert entries[1].cost_coverage is CostCoverage.UNPRICED
    assert entries[2].evidence_status is MeterEvidenceStatus.UNAVAILABLE


def test_ledger_store_and_reconciliation_are_integrity_and_coverage_aware(tmp_path):
    store = MeterLedgerStore(tmp_path)
    entry = _entry()
    record = store.append(entry)

    assert store.get(entry.entry_id) == record
    with pytest.raises(FileExistsError):
        store.append(entry)
    assert reconcile_meter_quantity(
        (entry,), meter_id=entry.meter_id, source_quantity=25, tolerance=0
    )["status"] == "matched"
    assert reconcile_meter_quantity(
        (entry,), meter_id=entry.meter_id, source_quantity=24, tolerance=0
    )["status"] == "mismatch"

    path = next(tmp_path.glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["entry"]["quantity"] = 26
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        store.get(entry.entry_id)


def test_meter_ledger_schema_matches_runtime_taxonomy():
    schema = json.loads(
        (ROOT / "data/contracts/meter-ledger-entry.v1.schema.json").read_text()
    )

    assert schema["properties"]["schema_version"]["const"] == (
        METER_LEDGER_SCHEMA_VERSION
    )
    assert set(schema["properties"]["meter_family"]["enum"]) == {
        family.value for family in ConsumptionFamily
    }
