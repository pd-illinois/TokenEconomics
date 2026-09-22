import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from costgov.billing_snapshots import BillingSnapshotStore, allocate_snapshot, normalize_rows


ONE = "/subscriptions/sub/resourceGroups/group/providers/Service/type/one".lower()
TWO = "/subscriptions/sub/resourceGroups/group/providers/Service/type/two".lower()
DAY = "2026-09-09"
SOURCE = {
    "kind": "azure_cost_management_export",
    "source_id": "/subscriptions/sub/providers/Microsoft.CostManagement/exports/daily",
    "revision": "revision-one",
    "content_hash": "a" * 64,
}


def row(cost="2.5", *, resource=ONE, day=DAY, currency="USD", **extra):
    return {"Date": day, "ResourceId": resource, "Cost": cost, "Currency": currency, **extra}


def snapshot(rows=None, **kwargs):
    options = {
        "source": SOURCE, "period_start": "2026-09-01", "period_end": "2026-09-30",
        "allowed_resource_ids": [ONE, TWO], "retrieved_at": "2026-09-10T12:00:00+00:00",
    }
    options.update(kwargs)
    return normalize_rows([row()] if rows is None else rows, **options)


def binding(method="unallocated", *, resources=None):
    return {
        "schema_version": "workload-resource-binding.v1",
        "binding_id": "workload-resources", "revision": "v1", "policy_revision": "policy-v1",
        "valid_from": "2026-09-01", "valid_to": "2026-09-30",
        "resources": resources or [
            {"resource_id": ONE, "role": "compute", "allocation_method": method},
            {"resource_id": TWO, "role": "storage", "allocation_method": method},
        ],
    }


def rule(resource=ONE, currency="USD", numerator=1, denominator=4):
    return {
        "resource_id": resource, "currency": currency, "date": DAY, "run_id": "run-one",
        "period_start": DAY, "period_end": DAY, "policy_revision": "policy-v1",
        "numerator": numerator, "denominator": denominator,
        "numerator_ref": "usage:run-one:v1", "denominator_ref": "usage:all-workloads:v1",
        "denominator_complete": True, "measurement_unit": "cpu_seconds",
    }


def allocate(value, **kwargs):
    options = {"binding": binding(), "run_id": "run-one", "run_date": DAY}
    options.update(kwargs)
    return allocate_snapshot(value, **options)


def test_duplicate_rows_are_legitimate_and_repeated_import_is_idempotent(tmp_path):
    store = BillingSnapshotStore(tmp_path)
    value = snapshot([row(), row()])
    assert value["row_count"] == 2
    assert value["totals_by_currency"] == {"USD": 5}
    assert value["rows"][0]["source_row_hash"] != value["rows"][1]["source_row_hash"]
    stored, created = store.append(value)
    assert created
    replay, created = store.append(snapshot([row(), row()], retrieved_at="2026-09-11T00:00:00Z"))
    assert not created and replay == stored
    assert store.append(stored) == (stored, False)
    assert store.get(stored["snapshot_id"]) == stored
    assert store.list() == [stored]
    assert not list(tmp_path.glob("*.pending"))


def test_actual_cost_2021_export_currency_and_provider_columns():
    result = snapshot([{"date": "09/09/2026", "ResourceId": ONE,
                        "costInBillingCurrency": "1.25", "billingCurrency": "USD",
                        "consumedService": "Microsoft.Storage", "quantity": "2",
                        "unitOfMeasure": "1 GB/Month"}])
    assert result["totals_by_currency"] == {"USD": 1.25}
    assert result["rows"][0]["service_name"] == "Microsoft.Storage"
    assert result["rows"][0]["quantity"] == 2


def test_corrected_overlapping_mtd_is_a_revision_not_an_added_charge(tmp_path):
    store = BillingSnapshotStore(tmp_path)
    original, _ = store.append(snapshot([row(10)]))
    corrected, _ = store.append(snapshot(
        [row(6)], source={**SOURCE, "revision": "revision-two", "content_hash": "b" * 64},
        retrieved_at="2026-09-11T12:00:00Z",
    ))
    assert corrected["snapshot_id"] != original["snapshot_id"]
    assert store.latest() == corrected
    assert store.latest()["totals_by_currency"] == {"USD": 6}
    assert len(store.list()) == 2
    assert store.get(original["snapshot_id"])["totals_by_currency"] == {"USD": 10}


@pytest.mark.parametrize("change", [
    {"source": {**SOURCE, "revision": "revision-two"}},
    {"source": {**SOURCE, "content_hash": "b" * 64}},
    {"source": {**SOURCE, "source_id": "/subscriptions/other"}},
    {"allowed_resource_ids": [ONE]},
])
def test_source_and_resource_scope_are_part_of_identity(tmp_path, change):
    store = BillingSnapshotStore(tmp_path)
    first, _ = store.append(snapshot())
    second, created = store.append(snapshot(**change))
    assert created and first["snapshot_id"] != second["snapshot_id"]


def test_date_aliases_signed_adjustments_zero_and_separate_currencies():
    value = snapshot([
        row("+12.50", day="09/09/2026", Quantity="-3", UnitOfMeasure="Hours"),
        row("-2.5", day=20260909),
        row(0, currency="EUR", Quantity=0),
        {"UsageDate": "20260909", "ResourceId": ONE, "CostInBillingCurrency": "-0.5",
         "BillingCurrencyCode": "EUR", "MeterId": "meter-1", "MeterName": "Compute",
         "ServiceName": "Compute service"},
    ])
    assert value["totals_by_currency"] == {"EUR": -0.5, "USD": 10}
    assert value["observed_dates"] == [DAY]
    assert value["rows"][0]["quantity"] == -3
    assert value["rows"][1]["quantity"] is None
    assert value["rows"][2]["quantity"] == 0
    assert value["rows"][1]["service_name"] is None
    assert value["rows"][1]["meter_id"] is None
    assert value["rows"][3]["meter_id"] == "meter-1"


def test_query_missing_dimensions_are_explicitly_unavailable():
    value = snapshot(source={**SOURCE, "kind": "azure_cost_management_query"})
    for field in ("meter_id", "meter_name", "service_name", "quantity", "unit_of_measure"):
        assert value["rows"][0][field] is None


def test_conflicting_aliases_cannot_hide_billing_cost():
    with pytest.raises(ValueError):
        snapshot([row(1, CostInBillingCurrency=100)])


def test_cancellation_and_decimal_totals_do_not_lose_signed_adjustments():
    value = snapshot([row("10000000000000000"), row("1"), row("-10000000000000000"),
                      row("0.1"), row("0.2"), row("-0.3")])
    assert value["totals_by_currency"] == {"USD": 1}


def test_overflowing_currency_totals_are_rejected():
    with pytest.raises(ValueError):
        snapshot([row("1e308"), row("1e308")])


@pytest.mark.parametrize("value", ["", None, True, False, "NaN", "Infinity", "-inf", float("nan"), float("inf"),
                                  "1,200.30", "EUR2", [], {}, "1e999", "1e-999"])
def test_malformed_costs_are_rejected(value):
    with pytest.raises(ValueError):
        snapshot([row(value)])


@pytest.mark.parametrize("value", [True, False, float("nan"), "Infinity", [], "1,000"])
def test_malformed_quantities_are_rejected(value):
    with pytest.raises(ValueError):
        snapshot([row(Quantity=value)])


@pytest.mark.parametrize("day", ["", None, True, 20260909.0, "2026-02-30", "09/31/2026",
                                "2026-9-9", "2026-09-09T00:00:00Z", "yesterday"])
def test_malformed_dates_are_rejected(day):
    with pytest.raises(ValueError):
        snapshot([row(day=day)])


@pytest.mark.parametrize("currency", ["", None, True, "usd", "$", "US", "USDD", " USD", 123])
def test_missing_or_malformed_currency_is_not_assumed(currency):
    with pytest.raises(ValueError):
        snapshot([row(currency=currency)])


def test_foreign_resources_and_out_of_period_rows_are_excluded_not_prefix_matched():
    value = snapshot([
        row(resource=ONE.upper()), row(resource=ONE + "-other"),
        row(resource=TWO + "/children/child"), row(day="2026-08-31"),
        {"ResourceId": "/subscriptions/foreign", "Cost": "invalid"},
        {"ResourceId": "", "Cost": "unattributed"},
    ])
    assert value["row_count"] == 1
    assert value["excluded_row_count"] == 5
    assert value["rows"][0]["resource_id"] == ONE
    assert value["rows"][0]["source_row_index"] == 0


def test_empty_source_has_no_observed_zero():
    value = snapshot([])
    assert value["totals_by_currency"] == {}
    assert value["latest_observed_date"] is None
    result = allocate(value)
    assert result["status"] == "awaiting_billing_data"
    assert result["allocated_by_currency"] == {}
    assert result["missing_resource_ids"] == sorted([ONE, TWO])


def test_no_run_day_data_vs_observed_zero_and_source_period_totals():
    no_day = allocate(snapshot([row(100, day="2026-09-08")]))
    zero = allocate(snapshot([row(0)]))
    assert no_day["status"] == "awaiting_billing_data"
    assert no_day["totals_by_currency"] == {}
    assert no_day["source_period_totals_by_currency"] == {"USD": 100}
    assert no_day["freshness"]["run_date_observed"] is False
    assert zero["status"] == "partial"
    assert zero["totals_by_currency"] == {"USD": 0}
    assert zero["unallocated_resource_ids"] == [ONE]
    assert zero["missing_resource_ids"] == [TWO]
    assert zero["complete_task_cost"] is False
    assert zero["catalog_model_usage_included"] is False


def test_allocation_only_selects_bound_resource_day_and_preserves_signed_conservation():
    value = snapshot([
        row(10), row(-2), row(0, currency="EUR"), row(-8, resource=TWO, currency="EUR"),
        row(100, day="2026-09-08"),
    ])
    result = allocate(value, binding=binding("measured_share"), allocations=[
        rule(), rule(currency="EUR"), rule(resource=TWO, currency="EUR", numerator=1, denominator=2),
    ])
    assert result["status"] == "allocated"
    assert result["row_count"] == 4
    assert result["totals_by_currency"] == {"USD": 8, "EUR": -8}
    assert result["allocated_by_currency"] == {"USD": 2, "EUR": -4}
    assert result["unallocated_by_currency"] == {"USD": 6, "EUR": -4}
    for currency, total in result["totals_by_currency"].items():
        assert total == result["allocated_by_currency"][currency] + result["unallocated_by_currency"][currency]
    assert result["source_period_totals_by_currency"] == {"USD": 108, "EUR": -8}
    assert result["complete_task_cost"] is False
    assert result["missing_resource_ids"] == []
    assert result["freshness"]["billing_finality"] == "not_established"


@pytest.mark.parametrize("numerator,denominator", [(0, 1), (1, 1), (1, 3), (2, 7)])
def test_allocation_fractional_zero_and_full_shares_conserve_currency(numerator, denominator):
    result = allocate(
        snapshot([row("0.1"), row("0.2"), row("-0.07")]),
        binding=binding("measured_share"),
        allocations=[rule(numerator=numerator, denominator=denominator)],
    )
    assert result["totals_by_currency"]["USD"] == pytest.approx(
        result["allocated_by_currency"]["USD"] + result["unallocated_by_currency"]["USD"]
    )
    assert result["allocated_by_currency"]["USD"] == pytest.approx(0.23 * numerator / denominator)
    assert result["status"] == "partial"  # The other bound resource still has no billing.


def test_binding_subset_does_not_attribute_other_allowlisted_resources():
    value = snapshot([row(10), row(100, resource=TWO)])
    result = allocate(value, binding=binding(resources=[
        {"resource_id": ONE, "role": "compute", "allocation_method": "unallocated"},
    ]))
    assert result["totals_by_currency"] == {"USD": 10}
    assert result["source_period_totals_by_currency"] == {"USD": 110}


def test_measured_share_without_evidence_stays_partial():
    result = allocate(snapshot([row(), row(resource=TWO)]), binding=binding("measured_share"))
    assert result["status"] == "partial"
    assert result["allocated_by_currency"] == {"USD": 0}
    assert result["missing_resource_ids"] == []
    assert result["unallocated_by_currency"] == {"USD": 5}


@pytest.mark.parametrize("changes", [
    {"numerator": -1}, {"numerator": 5}, {"numerator": True}, {"denominator": 0},
    {"denominator": "4"}, {"denominator": float("inf")}, {"denominator_complete": False},
    {"denominator_complete": 1}, {"numerator_ref": ""}, {"denominator_ref": ""},
    {"denominator_ref": "usage:run-one:v1"}, {"measurement_unit": "measurement_count"},
    {"measurement_unit": "task_count"}, {"measurement_unit": "response_count"},
    {"policy_revision": "other"}, {"date": "2026-09-08"}, {"run_id": "other"},
    {"period_start": "2026-09-01"}, {"period_end": "2026-09-30"}, {"currency": "EUR"},
    {"resource_id": "/subscriptions/foreign"},
])
def test_incomplete_mismatched_or_unsound_allocation_evidence_fails(changes):
    with pytest.raises(ValueError):
        allocate(snapshot(), binding=binding("measured_share"), allocations=[{**rule(), **changes}])


@pytest.mark.parametrize("missing", list(rule()))
def test_all_measured_share_evidence_fields_are_required(missing):
    evidence = rule()
    evidence.pop(missing)
    with pytest.raises(ValueError):
        allocate(snapshot(), binding=binding("measured_share"), allocations=[evidence])


def test_duplicate_share_rules_and_unallocated_resource_share_are_rejected():
    with pytest.raises(ValueError):
        allocate(snapshot(), binding=binding("measured_share"), allocations=[rule(), rule()])
    with pytest.raises(ValueError):
        allocate(snapshot(), allocations=[rule()])


@pytest.mark.parametrize("changes", [
    {"schema_version": "other"}, {"binding_id": ""}, {"revision": ""},
    {"valid_from": "2026-09-10"}, {"valid_to": "2026-09-08"}, {"resources": []},
    {"resources": [{"resource_id": "/foreign", "role": "compute", "allocation_method": "unallocated"}]},
    {"resources": [{"resource_id": ONE, "role": "compute", "allocation_method": "task_count"}]},
])
def test_invalid_or_out_of_validity_binding_fails(changes):
    with pytest.raises(ValueError):
        allocate(snapshot(), binding={**binding(), **changes})


def test_duplicate_binding_resources_are_rejected():
    value = binding()
    value["resources"].append(copy.deepcopy(value["resources"][0]))
    with pytest.raises(ValueError):
        allocate(snapshot(), binding=value)


@pytest.mark.parametrize("changes", [
    {"source": {**SOURCE, "content_hash": "bad"}},
    {"source": {**SOURCE, "kind": "estimate"}},
    {"source": {**SOURCE, "revision": ""}},
    {"source": {**SOURCE, "source_id": "not-an-arm-id"}},
    {"retrieved_at": "2026-09-09"}, {"retrieved_at": "2026-09-09T00:00:00"},
    {"period_start": "2026-10-01"}, {"allowed_resource_ids": []},
    {"allowed_resource_ids": [ONE, ONE.upper()]},
])
def test_invalid_source_scope_period_and_timestamp_fail(changes):
    with pytest.raises(ValueError):
        snapshot(**changes)


@pytest.mark.parametrize("resource", ["/resource/../other", "/resource//other", "/resource/one/",
                                    "/resource\\other", "/resource/one?query=x", "/resource/\x00one"])
def test_resource_scope_cannot_contain_ambiguous_paths(resource):
    with pytest.raises(ValueError):
        snapshot(allowed_resource_ids=[resource])


@pytest.mark.parametrize("field,value", [
    ("schema_version", "billing-snapshot.v2"), ("row_count", 0), ("row_count", True),
    ("excluded_row_count", -1), ("totals_by_currency", {"USD": 99}),
    ("observed_dates", []), ("latest_observed_date", None), ("source", {**SOURCE, "revision": "changed"}),
    ("retrieved_at", "2026-09-11T00:00:00Z"),
])
def test_disk_content_tampering_fails_get_list_and_latest(tmp_path, field, value):
    store = BillingSnapshotStore(tmp_path)
    stored, _ = store.append(snapshot())
    damaged = {**stored, field: value}
    (tmp_path / f"{stored['snapshot_id']}.json").write_text(json.dumps(damaged), encoding="utf-8")
    for read in (lambda: store.get(stored["snapshot_id"]), store.list, store.latest):
        with pytest.raises(ValueError):
            read()


def test_rehashed_invalid_schema_and_disk_identity_still_fail(tmp_path):
    store = BillingSnapshotStore(tmp_path)
    stored, _ = store.append(snapshot())
    original_path = tmp_path / f"{stored['snapshot_id']}.json"
    damaged = copy.deepcopy(stored)
    damaged["rows"][0]["cost"] = True
    damaged["content_hash"] = hashlib.sha256(json.dumps(
        {key: value for key, value in damaged.items() if key != "content_hash"},
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()
    original_path.write_text(json.dumps(damaged), encoding="utf-8")
    with pytest.raises(ValueError):
        store.get(stored["snapshot_id"])
    original_path.write_text(json.dumps(stored), encoding="utf-8")
    other = "billing-" + "f" * 64
    (tmp_path / f"{other}.json").write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(ValueError):
        store.get(other)


@pytest.mark.parametrize("identity", ["../x", "..\\x", "C:\\x", "/x", "", "billing-a", None, "billing-" + "g" * 64])
def test_path_traversal_and_invalid_ids_fail(tmp_path, identity):
    with pytest.raises(ValueError):
        BillingSnapshotStore(tmp_path).get(identity)


def test_missing_and_corrupt_evidence_are_not_empty_success(tmp_path):
    store = BillingSnapshotStore(tmp_path)
    assert store.latest() is None and store.list() == []
    with pytest.raises(KeyError):
        store.get("billing-" + "a" * 64)
    (tmp_path / ("billing-" + "a" * 64 + ".json")).write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        store.latest()


@pytest.mark.parametrize("value", [None, [], "", 1, True])
def test_non_object_snapshots_fail_closed(tmp_path, value):
    with pytest.raises(ValueError):
        BillingSnapshotStore(tmp_path).append(value)
    with pytest.raises(ValueError):
        allocate(value)


def test_symlinked_snapshot_is_not_followed(tmp_path):
    store = BillingSnapshotStore(tmp_path / "store")
    stored, _ = store.append(snapshot())
    path = store.root / f"{stored['snapshot_id']}.json"
    target = tmp_path / "outside.json"
    target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.unlink()
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("The operating system does not permit symlink creation")
    with pytest.raises(ValueError):
        store.get(stored["snapshot_id"])


def test_concurrent_imports_create_one_immutable_record(tmp_path):
    store = BillingSnapshotStore(tmp_path)
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(lambda _: store.append(snapshot()), range(12)))
    assert sum(created for _, created in results) == 1
    assert len(store.list()) == 1
    assert all(stored == results[0][0] for stored, _ in results)
    assert list(tmp_path.glob("*.pending")) == []


def test_latest_is_deterministic_utc_order_not_append_order(tmp_path):
    store = BillingSnapshotStore(tmp_path)
    later, _ = store.append(snapshot(retrieved_at="2026-09-10T14:00:00+02:00"))
    earlier, _ = store.append(snapshot([row(3)], retrieved_at="2026-09-10T11:59:59Z"))
    assert store.latest() == later
    assert store.list() == [earlier, later]


def test_mutating_returned_values_does_not_mutate_history_or_inputs(tmp_path):
    value = snapshot()
    store = BillingSnapshotStore(tmp_path)
    stored, _ = store.append(value)
    allocated = allocate(stored)
    allocated["rows"][0]["cost"] = 500
    assert stored["rows"][0]["cost"] == 2.5
    stored["rows"][0]["cost"] = 100
    assert value["rows"][0]["cost"] == 2.5
    assert store.latest()["rows"][0]["cost"] == 2.5
