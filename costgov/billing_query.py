"""Explicit, Entra-authenticated ActualCost queries; never an export fallback."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .billing_ingestion import _credential, validate_source
from .billing_snapshots import normalize_rows
from .studio_lifecycle import digest


QUERY_SOURCE_VERSION = "studio-billing-query-source.v1"
API_VERSION = "2023-11-01"
MANAGEMENT_ORIGIN = "https://management.azure.com"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def query_request(config: dict, run_date: str) -> tuple[str, dict]:
    """Pin the query to server-owned resources and the selected run's month/day."""
    validate_source(config)
    query = config.get("query")
    if (not isinstance(query, dict)
            or set(query) != {"schema_version", "scope", "api_version", "grouping"}
            or query["schema_version"] != QUERY_SOURCE_VERSION
            or query["api_version"] != API_VERSION
            or query["grouping"] not in {"Meter", "ServiceName"}):
        raise ValueError("A supported server-owned billing query configuration is required")
    scope = query["scope"]
    if not isinstance(scope, str) or not re.fullmatch(
        r"/subscriptions/[A-Za-z0-9-]+(?:/resourceGroups/[A-Za-z0-9_.()-]+)?", scope
    ):
        raise ValueError("Billing query scope must be an exact subscription or resource group")
    selected = date.fromisoformat(run_date)
    if selected.isoformat() != run_date:
        raise ValueError("Billing query date must be an ISO calendar date")
    resources = [item["resource_id"] for item in config["binding"].get("resources", [])]
    if (not resources or not all(isinstance(resource, str) for resource in resources)
            or len(resources) != len({resource.lower() for resource in resources})
            or any(not resource.lower().startswith(scope.lower() + "/") for resource in resources)):
        raise ValueError("Billing query resources must be unique and within the configured scope")
    body = {
        "type": "ActualCost", "timeframe": "Custom",
        "timePeriod": {
            "from": selected.replace(day=1).isoformat() + "T00:00:00Z",
            "to": selected.isoformat() + "T23:59:59Z",
        },
        "dataset": {
            "granularity": "Daily",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            "grouping": [
                {"type": "Dimension", "name": "ResourceId"},
                {"type": "Dimension", "name": query["grouping"]},
            ],
            "filter": {"dimensions": {"name": "ResourceId", "operator": "In",
                                      "values": resources}},
        },
    }
    return f"{MANAGEMENT_ORIGIN}{scope}/providers/Microsoft.CostManagement/query?api-version={API_VERSION}", body


def _continuation(url, initial_url):
    parsed, initial = urlparse(url), urlparse(initial_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    if (parsed.scheme != "https" or parsed.netloc != initial.netloc
            or parsed.path != initial.path or parsed.fragment
            or params.get("api-version") != [API_VERSION]
            or set(params) - {"api-version", "$skiptoken"}
            or ("$skiptoken" in params and (len(params["$skiptoken"]) != 1 or not params["$skiptoken"][0]))):
        raise ValueError("Billing query continuation leaves the configured authority")


def _query_rows(page, grouping):
    if not isinstance(page, dict) or not isinstance(page.get("properties"), dict):
        raise ValueError("Malformed Cost Management query response")
    properties = page["properties"]
    columns, rows = properties.get("columns"), properties.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise ValueError("Cost Management query columns and rows are required")
    names = [column.get("name") if isinstance(column, dict) else None for column in columns]
    if not all(isinstance(name, str) for name in names):
        raise ValueError("Cost Management query column names must be strings")
    required = {"UsageDate", "ResourceId", "Currency", grouping}
    cost_names = set(names) & {"Cost", "PreTaxCost"}
    if len(cost_names) != 1 or set(names) != required | cost_names or len(names) != len(set(names)):
        raise ValueError("Cost Management query columns do not match the requested billing scope")
    cost_name = next(iter(cost_names))
    result = []
    for values in rows:
        if not isinstance(values, list) or len(values) != len(names):
            raise ValueError("Cost Management query row does not match its columns")
        row = dict(zip(names, values))
        result.append({
            "UsageDate": row["UsageDate"], "ResourceId": row["ResourceId"],
            "Cost": row[cost_name], "Currency": row["Currency"],
            "MeterName" if grouping == "Meter" else "ServiceName": row[grouping],
        })
    next_link = properties.get("nextLink")
    if next_link is not None and (not isinstance(next_link, str) or not next_link):
        raise ValueError("Malformed billing query continuation")
    return result, next_link


def sync_query(config, store, *, run_date, read_page=None, now=None):
    """Persist only a complete query, with original page hashes and native gaps."""
    initial_url, body = query_request(config, run_date)
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None or date.fromisoformat(run_date) > instant.astimezone(timezone.utc).date():
        raise ValueError("Billing query requires an aware retrieval time and a nonfuture run date")
    credential = None
    if read_page is None:
        credential = _credential()

        def read_page(url, request_body):
            token = credential.get_token("https://management.azure.com/.default")
            request = Request(
                url, data=json.dumps(request_body).encode("utf-8"), method="POST",
                headers={"Authorization": f"Bearer {token.token}", "Content-Type": "application/json",
                         "ClientType": "TokenEconomicsStudio"},
            )
            with build_opener(_NoRedirect()).open(request, timeout=60) as response:
                return response.read(8 * 1024 * 1024 + 1)

    rows, pages, seen = [], [], set()
    url = initial_url
    try:
        while url:
            _continuation(url, initial_url)
            if url in seen or len(pages) >= 100:
                raise ValueError("Billing query pagination is repeated or exceeds the bounded import")
            seen.add(url)
            raw = read_page(url, body)
            if not isinstance(raw, bytes):
                raise ValueError("Billing query must preserve the original response bytes")
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError("Billing query page exceeds the bounded source size")
            try:
                page = json.loads(raw)
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError("Unreadable billing query response") from exc
            page_rows, next_url = _query_rows(page, config["query"]["grouping"])
            rows.extend(page_rows)
            pages.append({
                "url": url, "content_hash": hashlib.sha256(raw).hexdigest(),
                "response_bytes_base64": base64.b64encode(raw).decode("ascii"),
            })
            url = next_url
        source_hash = digest({"request": body, "pages": pages})
        snapshot = normalize_rows(
            rows, source={
                "kind": "azure_cost_management_query",
                "source_id": urlparse(initial_url).path,
                "api_version": API_VERSION, "revision": source_hash, "content_hash": source_hash,
                "query": body, "pages": pages, "page_count": len(pages),
                "row_scope": "daily_resource_groups_not_itemized_export",
            },
            period_start=body["timePeriod"]["from"][:10], period_end=run_date,
            allowed_resource_ids=body["dataset"]["filter"]["dimensions"]["values"],
            retrieved_at=instant.isoformat(),
        )
        if snapshot["excluded_row_count"]:
            raise ValueError("Billing query returned rows outside its exact resource/date filter")
        return store.append(snapshot)
    except HTTPError as exc:
        if exc.code == 429:
            raise ValueError("Azure Cost Management query is rate limited; respect its retry-after interval before retrying.") from exc
        raise ValueError(f"Azure Cost Management query failed (HTTP {exc.code}); check query-scope read access.") from exc
    except URLError as exc:
        raise ValueError("Azure Cost Management query could not reach the configured management endpoint.") from exc
    finally:
        if credential is not None:
            credential.close()


def matches_query_source(snapshot, config):
    """Do not select a foreign, differently filtered or differently typed query."""
    source = snapshot["source"]
    if source["kind"] != "azure_cost_management_query" or config.get("query") is None:
        return False
    url, body = query_request(config, snapshot["period"]["end"])
    return (
        source["source_id"].casefold() == urlparse(url).path.casefold()
        and source.get("api_version") == API_VERSION
        and source.get("query") == body
        and snapshot["period"]["start"] == body["timePeriod"]["from"][:10]
    )
