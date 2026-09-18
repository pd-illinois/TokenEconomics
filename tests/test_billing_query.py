import copy
import base64
import hashlib
import json
from io import BytesIO
from datetime import datetime, timezone
from urllib.error import HTTPError
from types import SimpleNamespace

import pytest

from costgov.billing_query import matches_query_source, query_request, sync_query
from costgov.billing_snapshots import BillingSnapshotStore


SCOPE = "/subscriptions/sub/resourceGroups/group"
RESOURCE = SCOPE + "/providers/Microsoft.Search/searchServices/books"
NOW = datetime(2026, 9, 14, 18, 0, tzinfo=timezone.utc)


def config():
    return {
        "schema_version": "studio-billing-source.v1",
        "account_url": "https://test.blob.core.windows.net",
        "container": "costs", "prefix": "daily/", "export_id": "/exports/daily",
        "binding": {"schema_version": "workload-resource-binding.v1",
                    "resources": [{"resource_id": RESOURCE}]},
        "query": {"schema_version": "studio-billing-query-source.v1", "scope": SCOPE,
                  "api_version": "2023-11-01", "grouping": "Meter"},
    }


def response(rows=None, next_link=None):
    return {"properties": {
        "columns": [{"name": name, "type": kind} for name, kind in [
            ("Cost", "Number"), ("UsageDate", "Number"), ("ResourceId", "String"),
            ("Meter", "String"), ("Currency", "String")]],
        "rows": [[2.5, 20260909, RESOURCE, "Standard S1 Unit", "USD"]] if rows is None else rows,
        "nextLink": next_link,
    }}


def imported(tmp_path, value=None, cfg=None):
    return sync_query(
        cfg or config(), BillingSnapshotStore(tmp_path), run_date="2026-09-09",
        read_page=lambda *_: json.dumps(value or response()).encode(), now=NOW,
    )


def test_query_uses_actualcost_exact_resource_filter_and_bounded_dates():
    url, body = query_request(config(), "2026-09-09")
    assert url == f"https://management.azure.com{SCOPE}/providers/Microsoft.CostManagement/query?api-version=2023-11-01"
    assert body["type"] == "ActualCost"
    assert body["timePeriod"] == {"from": "2026-09-01T00:00:00Z", "to": "2026-09-09T23:59:59Z"}
    assert body["dataset"]["filter"]["dimensions"]["values"] == [RESOURCE]
    assert body["dataset"]["granularity"] == "Daily"


def test_query_import_preserves_source_type_and_unavailable_quantity(tmp_path):
    snapshot, created = imported(tmp_path)
    assert created and matches_query_source(snapshot, config())
    assert snapshot["source"]["kind"] == "azure_cost_management_query"
    assert snapshot["totals_by_currency"] == {"USD": 2.5}
    assert snapshot["rows"][0]["meter_name"] == "Standard S1 Unit"
    assert snapshot["rows"][0]["quantity"] is None
    assert snapshot["rows"][0]["unit_of_measure"] is None
    assert snapshot["rows"][0]["meter_id"] is None
    assert snapshot["source"]["page_count"] == 1
    raw_page = snapshot["source"]["pages"][0]
    raw = base64.b64decode(raw_page["response_bytes_base64"], validate=True)
    assert hashlib.sha256(raw).hexdigest() == raw_page["content_hash"]
    assert json.loads(raw) == response()
    assert "delivered_at" not in snapshot["source"]
    assert imported(tmp_path) == (snapshot, False)


def test_all_pages_are_required_and_signed_costs_and_currencies_remain_separate(tmp_path):
    url, _ = query_request(config(), "2026-09-09")
    next_link = url + "&$skiptoken=page2"
    pages = {url: response(next_link=next_link),
             next_link: response(rows=[[-0.5, 20260909, RESOURCE, "Adjustment", "USD"],
                                       [0, 20260909, RESOURCE, "Other", "EUR"]])}
    snapshot, _ = sync_query(config(), BillingSnapshotStore(tmp_path), run_date="2026-09-09",
                             read_page=lambda url, _: json.dumps(pages[url]).encode(), now=NOW)
    assert snapshot["row_count"] == 3
    assert snapshot["source"]["page_count"] == 2
    assert snapshot["totals_by_currency"] == {"EUR": 0, "USD": 2}


@pytest.mark.parametrize("next_link", [
    "https://foreign.example/steal",
    f"https://management.azure.com{SCOPE}/providers/Microsoft.Storage/accounts?api-version=2023-11-01",
    f"https://management.azure.com{SCOPE}/providers/Microsoft.CostManagement/query?api-version=2023-11-01&other=1",
])
def test_foreign_pagination_is_rejected_before_following_it(tmp_path, next_link):
    calls = []

    def read(url, _):
        calls.append(url)
        return json.dumps(response(next_link=next_link)).encode()

    with pytest.raises(ValueError, match="authority"):
        sync_query(config(), BillingSnapshotStore(tmp_path), run_date="2026-09-09", read_page=read, now=NOW)
    assert len(calls) == 1 and not list(tmp_path.glob("*.json"))


def test_repeated_pagination_never_persists_partial_data(tmp_path):
    url, _ = query_request(config(), "2026-09-09")
    with pytest.raises(ValueError, match="repeated"):
        imported(tmp_path, response(next_link=url))
    assert not list(tmp_path.glob("*.json"))


@pytest.mark.parametrize("change", ["columns", "row_width", "foreign_resource", "outside_date", "currency", "nan"])
def test_malformed_or_out_of_scope_data_is_not_imported(tmp_path, change):
    page = response()
    if change == "columns":
        page["properties"]["columns"][0]["name"] = "Unknown"
    elif change == "row_width":
        page["properties"]["rows"][0].pop()
    elif change == "foreign_resource":
        page["properties"]["rows"][0][2] = RESOURCE + "-other"
    elif change == "outside_date":
        page["properties"]["rows"][0][1] = 20260910
    elif change == "currency":
        page["properties"]["rows"][0][-1] = None
    else:
        page["properties"]["rows"][0][0] = float("nan")
    with pytest.raises(ValueError):
        imported(tmp_path, page)
    assert not list(tmp_path.glob("*.json"))


def test_empty_query_is_missing_data_not_a_zero_bill(tmp_path):
    snapshot, _ = imported(tmp_path, response(rows=[]))
    assert snapshot["totals_by_currency"] == {}
    assert snapshot["latest_observed_date"] is None


def test_foreign_or_reinterpreted_source_cannot_be_selected(tmp_path):
    snapshot, _ = imported(tmp_path)
    for mutation in ("source_id", "type", "filter"):
        other = copy.deepcopy(snapshot)
        if mutation == "source_id":
            other["source"]["source_id"] = "/subscriptions/foreign/providers/Microsoft.CostManagement/query"
        elif mutation == "type":
            other["source"]["query"]["type"] = "AmortizedCost"
        else:
            other["source"]["query"]["dataset"].pop("filter")
        assert not matches_query_source(other, config())


def test_query_http_failure_is_not_an_export_fallback(tmp_path):
    def fail(url, _):
        raise HTTPError(url, 403, "Forbidden", {}, None)

    with pytest.raises(ValueError, match="HTTP 403"):
        sync_query(config(), BillingSnapshotStore(tmp_path), run_date="2026-09-09", read_page=fail, now=NOW)
    assert not list(tmp_path.glob("*.json"))


def test_invalid_scope_and_future_dates_are_rejected(tmp_path):
    cfg = config()
    cfg["query"]["scope"] = "/subscriptions/other"
    with pytest.raises(ValueError, match="within"):
        query_request(cfg, "2026-09-09")
    with pytest.raises(ValueError, match="nonfuture"):
        sync_query(config(), BillingSnapshotStore(tmp_path), run_date="2026-09-15", now=NOW)


@pytest.mark.parametrize("status", [200, 403])
def test_native_query_uses_entra_and_bounded_reads_and_closes_credential(tmp_path, monkeypatch, status):
    events = []
    url, body = query_request(config(), "2026-09-09")

    class Credential:
        def get_token(self, scope):
            assert scope == "https://management.azure.com/.default"
            return SimpleNamespace(token="test-token")

        def close(self):
            events.append("closed")

    class Response(BytesIO):
        def read(self, size):
            assert size == 8 * 1024 * 1024 + 1
            events.append("bounded-read")
            return super().read(size)

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == url and request.get_method() == "POST"
            assert json.loads(request.data) == body
            assert request.get_header("Authorization") == "Bearer test-token"
            assert timeout == 60
            if status != 200:
                raise HTTPError(url, status, "Forbidden", {}, None)
            return Response(json.dumps(response()).encode())

    monkeypatch.setattr("costgov.billing_query._credential", Credential)
    monkeypatch.setattr("costgov.billing_query.build_opener", lambda *_: Opener())
    store = BillingSnapshotStore(tmp_path)
    if status == 200:
        snapshot, created = sync_query(config(), store, run_date="2026-09-09", now=NOW)
        assert created and "test-token" not in json.dumps(snapshot)
        assert events == ["bounded-read", "closed"]
    else:
        with pytest.raises(ValueError, match="HTTP 403"):
            sync_query(config(), store, run_date="2026-09-09", now=NOW)
        assert events == ["closed"] and store.list() == []


def test_query_preserves_original_resource_order_and_case():
    cfg = config()
    second = RESOURCE + "-another"
    cfg["binding"]["resources"].append({"resource_id": second})
    _, body = query_request(cfg, "2026-09-09")
    assert body["dataset"]["filter"]["dimensions"]["values"] == [RESOURCE, second]
    cfg["binding"]["resources"].append({"resource_id": RESOURCE.lower()})
    with pytest.raises(ValueError, match="unique"):
        query_request(cfg, "2026-09-09")
