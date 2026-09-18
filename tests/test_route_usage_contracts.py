from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from costgov.consumption_models import ConsumptionFamily
from costgov.route_usage import (
    ROUTE_LEDGER_LEGS,
    ROUTE_USAGE_SCHEMA_VERSION,
    AttributionState,
    CoverageState,
    FinalizationState,
    Granularity,
    LatencyClass,
    NativeQuantity,
    QuantityPrecision,
    RefreshState,
    RouteUsageStore,
    SourceKind,
    SourceState,
    UsageDisposition,
    UsageEvidenceLabel,
    UsageSource,
    normalize_route_usage,
    split_route_ledgers,
)

ROOT = Path(__file__).resolve().parents[1]
HASH = "a" * 64
SOURCE_BY_ROUTE = {
    "included": ("m365-usage", SourceKind.MICROSOFT_365, "Microsoft 365 administration"),
    "cowork": (
        "copilot-credit-usage",
        SourceKind.MICROSOFT_365,
        "Microsoft 365 Cost Management",
    ),
    "agent_builder": (
        "m365-usage",
        SourceKind.MICROSOFT_365,
        "Microsoft 365 administration",
    ),
    "copilot_studio": (
        "power-platform-usage",
        SourceKind.POWER_PLATFORM,
        "Power Platform administration",
    ),
    "work_iq": (
        "copilot-credit-usage",
        SourceKind.MICROSOFT_365,
        "Microsoft 365 Cost Management",
    ),
    "foundry": ("azure-usage", SourceKind.AZURE, "Azure billing and telemetry"),
    "github_copilot": ("github-ai-usage", SourceKind.GITHUB, "GitHub billing"),
    "copilot_studio_byom": (
        "power-platform-usage",
        SourceKind.POWER_PLATFORM,
        "Power Platform administration",
    ),
    "foundry_work_iq": (
        "azure-usage",
        SourceKind.AZURE,
        "Azure billing and telemetry",
    ),
}


def _source(
    route_id: str,
    *,
    source_id: str | None = None,
    source_kind: SourceKind | None = None,
    authority: str | None = None,
) -> UsageSource:
    default_id, default_kind, default_authority = SOURCE_BY_ROUTE[route_id]
    return UsageSource(
        source_id=source_id or default_id,
        source_kind=source_kind or default_kind,
        authority=authority or default_authority,
        record_id=f"record-{route_id}",
        revision="source.v1",
        content_hash=HASH,
    )


def _quantity(value: str = "12.340") -> NativeQuantity:
    return NativeQuantity(
        value=value,
        precision=QuantityPrecision.SOURCE_REPORTED,
        source_scale=len(value.partition(".")[2]),
    )


def _observation(route_id: str, leg_id: str | None = None, **changes):
    leg = leg_id or ROUTE_LEDGER_LEGS[route_id][0]
    values = {
        "observation_id": f"observation-{route_id}-{leg}",
        "route_id": route_id,
        "leg_id": leg,
        "meter_family": ConsumptionFamily.NATIVE_CREDIT,
        "meter_id": "native-usage",
        "native_unit": "native_event",
        "native_currency": "native_units",
        "quantity": _quantity(),
        "allowance_quantity": None,
        "disposition": UsageDisposition.METERED,
        "evidence_label": UsageEvidenceLabel.MEASURED,
        "source": _source(route_id),
        "source_state": SourceState.AVAILABLE,
        "refresh_state": RefreshState.FRESH,
        "finalization_state": FinalizationState.PROVISIONAL,
        "latency_class": LatencyClass.REAL_TIME,
        "coverage_state": CoverageState.COMPLETE,
        "granularity": Granularity.TASK,
        "snapshot_semantics": "provider_event_snapshot",
        "observed_at": "2026-09-02T15:59:58+00:00",
        "snapshot_at": "2026-09-02T15:59:59+00:00",
        "refreshed_at": "2026-09-02T16:00:00+00:00",
        "retention_until": "2026-10-02T16:00:00+00:00",
        "attribution_state": AttributionState.ATTRIBUTED,
        "task_id": "task-1",
        "trajectory_id": "trajectory-1",
        "step_id": "step-1",
        "segment_id": "hard",
        "unavailable_reason": None,
    }
    values.update(changes)
    return normalize_route_usage(**values)


@pytest.mark.parametrize("route_id", tuple(ROUTE_LEDGER_LEGS))
def test_all_nine_routes_preserve_native_quantities_and_source_semantics(
    route_id: str,
) -> None:
    observation = _observation(route_id)

    assert observation.route_id == route_id
    assert observation.quantity.value == "12.340"
    assert observation.quantity.source_scale == 3
    assert observation.native_unit == "native_event"
    assert observation.native_currency == "native_units"
    assert observation.source.source_id == SOURCE_BY_ROUTE[route_id][0]
    assert observation.refresh_latency_seconds == 1
    assert observation.finalization_state is FinalizationState.PROVISIONAL
    assert observation.preflight_enforcement_eligible is True


def test_allowance_and_included_usage_remain_native_usage_not_zero_cost_inference():
    allowance = _quantity("100.00")
    drawdown = _observation(
        "github_copilot",
        quantity=_quantity("7.50"),
        allowance_quantity=allowance,
        disposition=UsageDisposition.ALLOWANCE_DRAWDOWN,
        native_unit="GitHub AI Credit",
        native_currency="GitHub AI Credits",
    )
    included = _observation(
        "included",
        quantity=_quantity("3"),
        disposition=UsageDisposition.INCLUDED,
        meter_family=ConsumptionFamily.INCLUDED,
        native_unit="qualifying_use",
        native_currency="included",
    )

    assert drawdown.quantity.value == "7.50"
    assert drawdown.allowance_quantity.value == "100.00"
    assert drawdown.native_currency == "GitHub AI Credits"
    assert included.quantity.value == "3"
    assert included.included_usage is True
    assert "cost" not in included.to_dict()


def test_delayed_admin_snapshot_is_not_real_time_enforcement_evidence() -> None:
    delayed = _observation(
        "cowork",
        source_state=SourceState.DELAYED,
        latency_class=LatencyClass.PERIODIC,
        granularity=Granularity.TENANT,
        coverage_state=CoverageState.PARTIAL,
        attribution_state=AttributionState.UNATTRIBUTED,
        task_id=None,
        trajectory_id=None,
        step_id=None,
        segment_id=None,
        observed_at="2026-09-01T00:00:00+00:00",
        snapshot_at="2026-09-01T23:59:59+00:00",
        refreshed_at="2026-09-02T16:00:00+00:00",
        snapshot_semantics="previous_day_tenant_total",
        disposition=UsageDisposition.UNATTRIBUTED,
    )

    assert delayed.preflight_enforcement_eligible is False
    assert delayed.refresh_latency_seconds == 57601
    assert delayed.attribution_state is AttributionState.UNATTRIBUTED
    assert delayed.task_id is None
    assert delayed.quantity.value == "12.340"
    with pytest.raises(ValueError, match="coarse usage"):
        replace(
            delayed,
            attribution_state=AttributionState.ATTRIBUTED,
            task_id="task-1",
            trajectory_id="trajectory-1",
            segment_id="hard",
        )


def test_unavailable_task_join_is_explicit_without_fabricated_quantity() -> None:
    unavailable = _observation(
        "work_iq",
        quantity=NativeQuantity.unavailable(),
        evidence_label=UsageEvidenceLabel.UNAVAILABLE,
        source_state=SourceState.UNAVAILABLE,
        refresh_state=RefreshState.UNAVAILABLE,
        finalization_state=FinalizationState.UNAVAILABLE,
        latency_class=LatencyClass.PERIODIC,
        coverage_state=CoverageState.UNAVAILABLE,
        granularity=Granularity.TENANT,
        attribution_state=AttributionState.UNAVAILABLE,
        task_id=None,
        trajectory_id=None,
        step_id=None,
        segment_id=None,
        unavailable_reason="Admin export did not support a task-level join.",
    )

    assert unavailable.quantity.value is None
    assert unavailable.preflight_enforcement_eligible is False
    assert unavailable.unavailable_reason


@pytest.mark.parametrize(
    ("route_id", "leg_sources"),
    [
        (
            "copilot_studio_byom",
            {
                "commercial": (
                    "power-platform-usage",
                    SourceKind.POWER_PLATFORM,
                    "Power Platform administration",
                ),
                "foundry": (
                    "azure-usage",
                    SourceKind.AZURE,
                    "Azure billing and telemetry",
                ),
            },
        ),
        (
            "foundry_work_iq",
            {
                "foundry": (
                    "azure-usage",
                    SourceKind.AZURE,
                    "Azure billing and telemetry",
                ),
                "work_iq": (
                    "copilot-credit-usage",
                    SourceKind.MICROSOFT_365,
                    "Microsoft 365 Cost Management",
                ),
            },
        ),
    ],
)
def test_hybrid_routes_preserve_two_independent_ledgers(route_id, leg_sources) -> None:
    observations = []
    for leg, (source_id, source_kind, authority) in leg_sources.items():
        observations.append(
            _observation(
                route_id,
                leg,
                observation_id=f"observation-{route_id}-{leg}",
                source=_source(
                    route_id,
                    source_id=source_id,
                    source_kind=source_kind,
                    authority=authority,
                ),
                native_unit=f"{leg}_unit",
                native_currency=f"{leg}_currency",
            )
        )

    ledgers = split_route_ledgers(route_id, observations)

    assert set(ledgers) == set(leg_sources)
    assert all(len(items) == 1 for items in ledgers.values())
    assert {
        items[0].native_currency for items in ledgers.values()
    } == {f"{leg}_currency" for leg in leg_sources}
    with pytest.raises(ValueError, match="missing a required"):
        split_route_ledgers(route_id, observations[:1])


def test_source_authority_and_adapter_kind_must_match_route_profile() -> None:
    invalid = _source(
        "github_copilot",
        source_kind=SourceKind.MICROSOFT_365,
    )
    with pytest.raises(ValueError, match="route capability profile"):
        _observation("github_copilot", source=invalid)


def test_workload_owned_technical_telemetry_is_supported_without_product_mapping() -> None:
    source = UsageSource(
        source_id="workload-runtime-trace",
        source_kind=SourceKind.WORKLOAD_OWNED,
        authority="workload_owner",
        record_id="trace-record-1",
        revision="trace.v1",
        content_hash=HASH,
    )

    observation = _observation(
        "foundry",
        source=source,
        meter_family=ConsumptionFamily.TOOL,
        meter_id="external_tool_call",
        native_unit="tool_call",
        native_currency="not_monetized",
    )

    assert observation.source.source_kind is SourceKind.WORKLOAD_OWNED
    assert observation.source.authority == "workload_owner"


def test_source_decimal_precision_is_never_fabricated() -> None:
    assert _quantity("1.2300").to_dict() == {
        "value": "1.2300",
        "precision": "source_reported",
        "source_scale": 4,
    }
    with pytest.raises(ValueError, match="source_scale"):
        NativeQuantity("1.2300", QuantityPrecision.SOURCE_REPORTED, 2)
    with pytest.raises(ValueError, match="decimal string"):
        NativeQuantity("1e3", QuantityPrecision.ESTIMATED, 0)


def test_usage_hash_store_and_replay_are_immutable_and_idempotent(tmp_path) -> None:
    observation = _observation("foundry")
    store = RouteUsageStore(tmp_path)

    first = store.record(observation)
    replay = store.record(observation)
    reopened = store.get(observation.observation_id)

    assert first == replay == reopened
    assert reopened.observation.content_hash == observation.content_hash
    conflicting = replace(observation, native_currency="different_native_currency")
    with pytest.raises(ValueError, match="replay conflicts"):
        store.record(conflicting)

    path = next(tmp_path.glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["observation"]["quantity"]["value"] = "99.000"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        store.get(observation.observation_id)


def test_usage_schema_validates_runtime_serialization() -> None:
    schema = json.loads(
        (
            ROOT / "data" / "contracts" / "route-usage-observation.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    observation = _observation("github_copilot")
    Draft202012Validator(schema).validate(observation.to_dict())

    assert schema["properties"]["schema_version"]["const"] == (
        ROUTE_USAGE_SCHEMA_VERSION
    )
    assert set(schema["properties"]["source"]["$ref"] for _ in range(1)) == {
        "#/$defs/source"
    }
