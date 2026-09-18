"""Route-aware Azure infrastructure proposals and retail-price evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

ARCHITECTURE_SCHEMA_VERSION = "infrastructure-architecture.v1"
PRICE_SCHEMA_VERSION = "azure-retail-price-snapshot.v1"
FORECAST_SCHEMA_VERSION = "infrastructure-forecast.v1"
BASELINE_REVISION = "secure-single-region.v3"
DEFAULT_AZURE_MCP_PACKAGE_VERSION = "3.0.0-beta.41"
APPLICABLE_ROUTES = frozenset(
    {"foundry", "copilot_studio_byom", "foundry_work_iq"}
)
SAFEGUARDS = (
    "managed_identity",
    "least_privilege_rbac",
    "vnet_integration",
    "private_endpoint_subnet",
    "private_dns",
    "restricted_public_access",
    "encryption",
    "centralized_monitoring",
    "zone_redundancy_where_supported",
    "backup_recovery",
    "data_residency_validation",
)


class AzureInfrastructureError(RuntimeError):
    """Raised when Azure MCP evidence cannot be acquired or validated."""


class AzureMcpTransport(Protocol):
    def collect(
        self, architecture_intent: str, price_queries: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Return the architecture guidance result and one result per price query."""


def _canonical(value: object) -> str:
    return json.dumps(
        value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )


def content_hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def route_requires_infrastructure(route_id: str) -> bool:
    return route_id in APPLICABLE_ROUTES


@dataclass(frozen=True)
class StdioAzureMcpTransport:
    package_version: str = DEFAULT_AZURE_MCP_PACKAGE_VERSION
    timeout_seconds: float = 90.0
    max_response_bytes: int = 2_000_000

    def collect(
        self, architecture_intent: str, price_queries: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return asyncio.run(self._collect(architecture_intent, price_queries))

    async def _collect(
        self, architecture_intent: str, price_queries: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        command = "npx.cmd" if sys.platform == "win32" else "npx"
        parameters = StdioServerParameters(
            command=command,
            args=[
                "-y",
                f"@azure/mcp@{self.package_version}",
                "server",
                "start",
            ],
        )
        try:
            with anyio.fail_after(self.timeout_seconds):
                async with stdio_client(parameters) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                        for expected in ("cloudarchitect", "pricing"):
                            if expected not in tools:
                                raise AzureInfrastructureError(
                                    f"Azure MCP tool contract missing: {expected}"
                                )
                        architecture = await self._call(
                            session,
                            "cloudarchitect",
                            {
                                "intent": architecture_intent,
                                "command": "cloudarchitect_design",
                                "parameters": {
                                    "question": "Describe the confirmed workload and constraints.",
                                    "question-number": 1,
                                    "total-questions": 1,
                                    "answer": architecture_intent,
                                    "next-question-needed": False,
                                    "confidence-score": 0.8,
                                },
                            },
                        )
                        prices = []
                        for query in price_queries:
                            prices.append(
                                await self._call(
                                    session,
                                    "pricing",
                                    {
                                        "intent": query["intent"],
                                        "command": "pricing_get",
                                        "parameters": query["parameters"],
                                    },
                                )
                            )
                        return architecture, prices
        except TimeoutError as exc:
            raise AzureInfrastructureError("Azure MCP request timed out") from exc
        except AzureInfrastructureError:
            raise
        except Exception as exc:
            raise AzureInfrastructureError(f"Azure MCP request failed: {exc}") from exc

    async def _call(
        self, session: ClientSession, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        result = await session.call_tool(tool_name, arguments)
        if result.isError or not result.content:
            raise AzureInfrastructureError(f"Azure MCP {tool_name} returned an error")
        text = getattr(result.content[0], "text", None)
        if not text:
            raise AzureInfrastructureError(
                f"Azure MCP {tool_name} returned no JSON content"
            )
        if len(text.encode("utf-8")) > self.max_response_bytes:
            raise AzureInfrastructureError(
                f"Azure MCP {tool_name} response exceeded the configured limit"
            )
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AzureInfrastructureError(
                f"Azure MCP {tool_name} returned invalid JSON"
            ) from exc
        if payload.get("status") != 200:
            raise AzureInfrastructureError(
                f"Azure MCP {tool_name} failed: {payload.get('message') or 'unknown error'}"
            )
        return payload


@dataclass(frozen=True)
class HttpAzureMcpTransport:
    """Entra-authenticated Streamable HTTP transport for deployed Studio."""

    endpoint: str
    scope: str
    server_revision: str
    timeout_seconds: float = 90.0
    max_response_bytes: int = 2_000_000

    @property
    def evidence_revision(self) -> str:
        return self.server_revision

    def collect(
        self, architecture_intent: str, price_queries: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        from azure.identity import DefaultAzureCredential

        token = DefaultAzureCredential().get_token(self.scope)
        return asyncio.run(
            self._collect(
                architecture_intent,
                price_queries,
                {"Authorization": f"Bearer {token.token}"},
            )
        )

    async def _collect(
        self,
        architecture_intent: str,
        price_queries: list[dict[str, Any]],
        headers: dict[str, str],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        verifier = StdioAzureMcpTransport(
            package_version=self.server_revision,
            timeout_seconds=self.timeout_seconds,
            max_response_bytes=self.max_response_bytes,
        )
        try:
            with anyio.fail_after(self.timeout_seconds):
                async with streamablehttp_client(
                    self.endpoint,
                    headers=headers,
                    timeout=timedelta(seconds=self.timeout_seconds),
                    sse_read_timeout=timedelta(seconds=self.timeout_seconds),
                ) as (read_stream, write_stream, _):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                        for expected in ("cloudarchitect", "pricing"):
                            if expected not in tools:
                                raise AzureInfrastructureError(
                                    f"Azure MCP tool contract missing: {expected}"
                                )
                        architecture = await verifier._call(
                            session,
                            "cloudarchitect",
                            {
                                "intent": architecture_intent,
                                "command": "cloudarchitect_design",
                                "parameters": {
                                    "question": "Describe the confirmed workload and constraints.",
                                    "question-number": 1,
                                    "total-questions": 1,
                                    "answer": architecture_intent,
                                    "next-question-needed": False,
                                    "confidence-score": 0.8,
                                },
                            },
                        )
                        prices = [
                            await verifier._call(
                                session,
                                "pricing",
                                {
                                    "intent": query["intent"],
                                    "command": "pricing_get",
                                    "parameters": query["parameters"],
                                },
                            )
                            for query in price_queries
                        ]
                        return architecture, prices
        except TimeoutError as exc:
            raise AzureInfrastructureError("Azure MCP HTTP request timed out") from exc
        except AzureInfrastructureError:
            raise
        except Exception as exc:
            raise AzureInfrastructureError(
                f"Azure MCP HTTP request failed: {exc}"
            ) from exc


def _meter_query(
    *,
    line_id: str,
    service_name: str,
    sku_name: str,
    meter_name: str,
    region: str,
    quantity: float,
    quantity_source: str,
    material: bool = True,
) -> dict[str, Any]:
    escaped = lambda value: value.replace("'", "''")
    filter_value = (
        f"serviceName eq '{escaped(service_name)}' "
        f"and skuName eq '{escaped(sku_name)}' "
        f"and meterName eq '{escaped(meter_name)}' "
        f"and armRegionName eq '{escaped(region)}' "
        "and priceType eq 'Consumption'"
    )
    return {
        "line_id": line_id,
        "service_name": service_name,
        "sku_name": sku_name,
        "meter_name": meter_name,
        "quantity": quantity,
        "quantity_source": quantity_source,
        "material": material,
        "intent": (
            f"Get the exact {region} Consumption retail meter for "
            f"{service_name}, SKU {sku_name}, meter {meter_name}"
        ),
        "parameters": {"currency": "USD", "filter": filter_value},
    }


def _baseline(route_id: str, region: str, parameters: Mapping[str, Any]) -> dict[str, Any]:
    users = int(parameters.get("users") or 1)
    calls = int(parameters.get("calls_per_user_per_day") or 1)
    monthly_requests = users * calls * 30
    active_seconds = monthly_requests * 2
    resources = [
        {
            "resource_id": "runtime",
            "arm_resource_type": "Microsoft.App/containerApps",
            "service": "Azure Container Apps",
            "sku": "Consumption",
            "role": "Application runtime and ingress",
            "material": True,
            "safeguards": [
                "managed_identity",
                "least_privilege_rbac",
                "vnet_integration",
                "restricted_public_access",
                "centralized_monitoring",
                "zone_redundancy_where_supported",
            ],
        },
        {
            "resource_id": "registry",
            "arm_resource_type": "Microsoft.ContainerRegistry/registries",
            "service": "Container Registry",
            "sku": "Basic",
            "role": "Private application image registry",
            "material": True,
            "safeguards": [
                "managed_identity",
                "least_privilege_rbac",
                "encryption",
            ],
        },
        {
            "resource_id": "secrets",
            "arm_resource_type": "Microsoft.KeyVault/vaults",
            "service": "Key Vault",
            "sku": "Standard",
            "role": "Secrets and certificate boundary",
            "material": True,
            "safeguards": [
                "managed_identity",
                "least_privilege_rbac",
                "private_endpoint_subnet",
                "private_dns",
                "restricted_public_access",
                "encryption",
            ],
        },
        {
            "resource_id": "observability",
            "arm_resource_type": "Microsoft.OperationalInsights/workspaces",
            "service": "Log Analytics",
            "sku": "Pay-as-you-go",
            "role": "Centralized logs, metrics, and retention",
            "material": True,
            "safeguards": ["centralized_monitoring", "data_residency_validation"],
        },
        {
            "resource_id": "network",
            "arm_resource_type": "Microsoft.Network/virtualNetworks",
            "service": "Virtual Network and Private Link",
            "sku": "Standard",
            "role": "VNet integration, private endpoints, and DNS",
            "material": True,
            "safeguards": [
                "vnet_integration",
                "private_endpoint_subnet",
                "private_dns",
                "restricted_public_access",
            ],
        },
    ]
    is_rag = (parameters.get("confirmed_profile") or {}).get("agent_pattern") == "rag_pipeline"
    if is_rag:
        resources.append(
            {
                "resource_id": "rag_search",
                "arm_resource_type": "Microsoft.Search/searchServices",
                "service": "Azure AI Search",
                "sku": "Standard S1",
                "replicas": 3,
                "partitions": 1,
                "role": "Route-specific RAG retrieval",
                "material": True,
                "safeguards": [
                    "managed_identity",
                    "private_endpoint_subnet",
                    "private_dns",
                    "restricted_public_access",
                    "encryption",
                ],
            }
        )
    queries = [
        _meter_query(
            line_id="runtime_requests",
            service_name="Azure Container Apps",
            sku_name="Standard",
            meter_name="Standard Requests",
            region=region,
            quantity=monthly_requests / 1_000_000,
            quantity_source="users * calls_per_user_per_day * 30 / 1M",
        ),
        _meter_query(
            line_id="runtime_vcpu",
            service_name="Azure Container Apps",
            sku_name="Standard",
            meter_name="Standard vCPU Active Usage",
            region=region,
            quantity=active_seconds * 0.5,
            quantity_source="monthly requests * 2 active seconds * 0.5 vCPU",
        ),
        _meter_query(
            line_id="runtime_memory",
            service_name="Azure Container Apps",
            sku_name="Standard",
            meter_name="Standard Memory Active Usage",
            region=region,
            quantity=active_seconds,
            quantity_source="monthly requests * 2 active seconds * 1 GiB",
        ),
        _meter_query(
            line_id="registry_unit",
            service_name="Container Registry",
            sku_name="Basic",
            meter_name="Basic Registry Unit",
            region=region,
            quantity=30,
            quantity_source="one registry * 30 modeled days",
        ),
        _meter_query(
            line_id="secrets_operations",
            service_name="Key Vault",
            sku_name="Standard",
            meter_name="Operations",
            region=region,
            quantity=1,
            quantity_source="modeled 10,000 standard operations per month",
        ),
        _meter_query(
            line_id="observability_ingestion",
            service_name="Log Analytics",
            sku_name="Analytics Logs",
            meter_name="Analytics Logs Data Ingestion",
            region=region,
            quantity=5,
            quantity_source="modeled 5 GB centralized log ingestion per month",
        ),
        _meter_query(
            line_id="network_private_endpoint",
            service_name="Azure Container Apps",
            sku_name="Environment",
            meter_name="Environment Private Endpoint",
            region=region,
            quantity=730,
            quantity_source="one private endpoint * 730 modeled hours per month",
        ),
    ]
    if is_rag:
        queries.append(_meter_query(
            line_id="rag_search_capacity",
            service_name="Azure Cognitive Search",
            sku_name="Standard S1",
            meter_name="Standard S1 Unit",
            region=region,
            quantity=3 * 730,
            quantity_source="3 modeled replicas * 1 partition * 730 hours; not existing-resource allocation",
        ))
    return {
        "schema_version": ARCHITECTURE_SCHEMA_VERSION,
        "route_id": route_id,
        "region": region,
        "baseline_revision": BASELINE_REVISION,
        "classification": "proposed",
        "resources": resources,
        "safeguards": [
            {"safeguard_id": safeguard, "status": "design_satisfied"}
            for safeguard in SAFEGUARDS
        ],
        "price_queries": queries,
        "assumptions": [
            "Secure single-region production baseline.",
            "Zone redundancy is required only where the selected service and SKU support it.",
            "Container Apps uses two active seconds, 0.5 vCPU, and 1 GiB per modeled request.",
            "Retail list price is not a negotiated rate or invoice.",
            *([
                "RAG Search capacity models Standard S1, three replicas and one partition; it does not discover or allocate the existing index's cost.",
                "Search semantic ranking, ingestion enrichment and agentic retrieval are outside this lexical-retrieval capacity baseline.",
            ] if is_rag else []),
        ],
    }


def _architecture_intent(
    description: str,
    route_id: str,
    region: str,
    parameters: Mapping[str, Any],
) -> str:
    profile = parameters.get("confirmed_profile") or {}
    return (
        f"Design guidance for a {route_id} workload in {region}. "
        f"Confirmed workload: {description}. Confirmed profile: {_canonical(profile)}. "
        "Use a secure single-region production baseline with managed identity, "
        "least-privilege RBAC, VNet integration, a distinct private-endpoint subnet, "
        "private DNS, restricted public access, encryption, centralized monitoring, "
        "backup and recovery assumptions, data-residency validation, and zone "
        "redundancy where supported. Model charges are priced separately."
    )


def _price_line(
    query: Mapping[str, Any], response: Mapping[str, Any]
) -> dict[str, Any]:
    prices = (
        response.get("results", {}).get("prices", [])
        if isinstance(response.get("results"), Mapping)
        else []
    )
    exact = [
        item
        for item in prices
        if item.get("serviceName") == query["service_name"]
        and item.get("skuName") == query["sku_name"]
        and item.get("meterName") == query["meter_name"]
        and item.get("region") == query["parameters"]["filter"].split(
            "armRegionName eq '", 1
        )[1].split("'", 1)[0]
        and item.get("priceType") == "Consumption"
    ]
    base = {
        "line_id": query["line_id"],
        "service_name": query["service_name"],
        "sku_name": query["sku_name"],
        "meter_name": query["meter_name"],
        "quantity": query["quantity"],
        "quantity_source": query["quantity_source"],
        "material": query["material"],
    }
    selection_evidence: dict[str, Any] = {}
    if len(exact) > 1:
        dated = []
        now = datetime.now(timezone.utc)
        for item in exact:
            try:
                effective = datetime.fromisoformat(
                    str(item.get("effectiveStartDate", "")).replace("Z", "+00:00")
                )
            except ValueError:
                continue
            if effective <= now:
                dated.append((effective, item))
        if dated:
            newest = max(item[0] for item in dated)
            exact = [item for effective, item in dated if effective == newest]
    if len(exact) > 1:
        identities = {
            (
                item.get("meterId"),
                item.get("unitOfMeasure"),
                item.get("effectiveStartDate"),
            )
            for item in exact
        }
        positive = [
            item for item in exact if float(item.get("retailPrice", 0)) > 0
        ]
        zero = [
            item for item in exact if float(item.get("retailPrice", 0)) == 0
        ]
        if len(identities) == 1 and len(positive) == 1 and len(zero) == len(exact) - 1:
            exact = positive
            selection_evidence = {
                "selection_rule": "conservative_positive_rate_same_meter_v1",
                "excluded_zero_allowance_rows": len(zero),
                "allowance_application": "not_applied_without_versioned_quantity",
                "candidate_hashes": sorted(content_hash(item) for item in prices),
            }
    if len(exact) != 1:
        return {
            **base,
            "evidence_status": "unpriced",
            "reason": (
                "no_exact_meter_match" if not exact else "ambiguous_exact_meter_match"
            ),
            "candidate_count": len(exact),
            "monthly_cost_usd": None,
        }
    price = exact[0]
    unit_price = float(price["retailPrice"])
    quantity = float(query["quantity"])
    monthly_cost = unit_price * quantity
    return {
        **base,
        "evidence_status": "sourced",
        "arm_sku_name": price.get("armSkuName", ""),
        "meter_id": price["meterId"],
        "unit_of_measure": price["unitOfMeasure"],
        "effective_start_date": price["effectiveStartDate"],
        "retail_unit_price": unit_price,
        "currency": price["currencyCode"],
        "price_type": price["priceType"],
        "monthly_cost_usd": round(monthly_cost, 6),
        "price_snapshot_hash": content_hash(price),
        **selection_evidence,
    }


def build_infrastructure_forecast(
    description: str,
    parameters: Mapping[str, Any],
    *,
    transport: AzureMcpTransport | None = None,
) -> dict[str, Any]:
    route_id = str(parameters.get("route") or "foundry")
    if not route_requires_infrastructure(route_id):
        return {
            "schema_version": FORECAST_SCHEMA_VERSION,
            "status": "not_applicable",
            "route_id": route_id,
            "classification": "modeled",
            "message": "Customer-hosted Azure infrastructure is not applicable to this route.",
        }
    region = str(parameters.get("azure_region") or "eastus").strip().lower()
    if not region:
        raise ValueError("azure_region is required")
    architecture = _baseline(route_id, region, parameters)
    architecture["confirmed_profile_hash"] = content_hash(
        parameters.get("confirmed_profile") or {}
    )
    intent = _architecture_intent(description, route_id, region, parameters)
    package_version = os.environ.get(
        "AZURE_MCP_PACKAGE_VERSION", DEFAULT_AZURE_MCP_PACKAGE_VERSION
    )
    endpoint = os.environ.get("AZURE_MCP_ENDPOINT", "").strip()
    if transport is None and endpoint:
        scope = os.environ.get("AZURE_MCP_SCOPE", "").strip()
        revision = os.environ.get("AZURE_MCP_SERVER_REVISION", "").strip()
        if not scope or not revision:
            raise AzureInfrastructureError(
                "AZURE_MCP_SCOPE and AZURE_MCP_SERVER_REVISION are required "
                "for remote Azure MCP"
            )
        transport = HttpAzureMcpTransport(endpoint, scope, revision)
    if transport is None and os.environ.get(
        "TOKENECONOMICS_AZURE_MCP_ENABLED", ""
    ).lower() in {"1", "true", "yes"}:
        transport = StdioAzureMcpTransport(package_version=package_version)
    if transport is None:
        return {
            "schema_version": FORECAST_SCHEMA_VERSION,
            "status": "needs_clarification",
            "route_id": route_id,
            "region": region,
            "classification": "modeled",
            "confirmed": False,
            "architecture": {
                **architecture,
                "mcp": {
                    "status": "unavailable",
                    "package_version": package_version,
                },
            },
            "price_snapshot": {
                "schema_version": PRICE_SCHEMA_VERSION,
                "status": "unavailable",
                "currency": "USD",
                "price_type": "Consumption",
                "lines": [],
            },
            "priced_subtotal_usd": None,
            "coverage_ratio": 0.0,
            "unpriced_material_items": [
                resource["resource_id"] for resource in architecture["resources"]
            ],
            "message": (
                "Azure MCP is disabled. Enable the pinned read-only transport to "
                "source architecture guidance and retail-price evidence."
            ),
        }

    guidance, responses = transport.collect(intent, architecture["price_queries"])
    evidence_revision = str(
        getattr(transport, "evidence_revision", package_version)
    )
    transport_kind = (
        "entra_streamable_http"
        if isinstance(transport, HttpAzureMcpTransport)
        else "pinned_local_stdio"
    )
    if len(responses) != len(architecture["price_queries"]):
        raise AzureInfrastructureError(
            "Azure MCP returned an incomplete pricing response set"
        )
    lines = [
        _price_line(query, response)
        for query, response in zip(architecture["price_queries"], responses)
    ]
    priced = [line for line in lines if line["evidence_status"] == "sourced"]
    resource_prefixes = {
        "runtime_": "runtime",
        "registry_": "registry",
        "secrets_": "secrets",
        "observability_": "observability",
        "network_": "network",
        "rag_search_": "rag_search",
    }
    priced_resource_ids = {
        resource_id
        for line in priced
        for prefix, resource_id in resource_prefixes.items()
        if line["line_id"].startswith(prefix)
    }
    material_resource_ids = {
        resource["resource_id"]
        for resource in architecture["resources"]
        if resource["material"]
    }
    unpriced = sorted(material_resource_ids - priced_resource_ids)
    unpriced_lines = sorted(
        line["line_id"]
        for line in lines
        if line["material"] and line["evidence_status"] != "sourced"
    )
    coverage_ratio = (
        len(material_resource_ids - set(unpriced)) / len(material_resource_ids)
        if material_resource_ids
        else 1.0
    )
    subtotal = round(sum(line["monthly_cost_usd"] for line in priced), 6)
    status = "estimated" if not unpriced and not unpriced_lines else "partial"
    architecture = {
        **architecture,
        "mcp": {
            "status": "sourced",
            "revision": evidence_revision,
            "transport": transport_kind,
            "tool": "cloudarchitect/cloudarchitect_design",
            "response_hash": content_hash(guidance),
            "note": (
                "The Azure MCP Cloud Architect response is guided design state; "
                "the bill of materials is normalized by the versioned route baseline."
            ),
        },
    }
    architecture.pop("price_queries", None)
    forecast = {
        "schema_version": FORECAST_SCHEMA_VERSION,
        "status": status,
        "route_id": route_id,
        "region": region,
        "classification": "modeled",
        "confirmed": False,
        "architecture": architecture,
        "price_snapshot": {
            "schema_version": PRICE_SCHEMA_VERSION,
            "status": "complete" if all(
                line["evidence_status"] == "sourced" for line in lines
            ) else "partial",
            "source": "Azure MCP pricing/pricing_get",
            "revision": evidence_revision,
            "transport": transport_kind,
            "currency": "USD",
            "price_type": "Consumption",
            "captured_at": _now(),
            "lines": lines,
        },
        "priced_subtotal_usd": subtotal,
        "modeled_range_usd": {
            "low": round(subtotal * 0.8, 6),
            "expected": subtotal,
            "high": round(subtotal * 1.25, 6),
            "classification": "modeled_not_calibrated_tail_risk",
        },
        "coverage_ratio": round(coverage_ratio, 6),
        "unpriced_material_items": unpriced,
        "unpriced_material_lines": unpriced_lines,
        "message": (
            "All material resources have sourced price evidence."
            if status == "estimated"
            else "Some material resources remain unpriced; the subtotal is not a total."
        ),
    }
    validate_infrastructure_forecast(forecast)
    forecast["content_hash"] = content_hash(forecast)
    return forecast


def confirm_infrastructure_forecast(
    forecast: Mapping[str, Any], expected_hash: str | None = None
) -> dict[str, Any]:
    current = dict(forecast)
    stored_hash = current.pop("content_hash", None)
    calculated = content_hash(current)
    if stored_hash and stored_hash != calculated:
        raise ValueError("infrastructure forecast integrity check failed")
    if expected_hash and expected_hash != (stored_hash or calculated):
        raise ValueError("infrastructure confirmation does not match the reviewed forecast")
    if current.get("status") not in {"estimated", "partial"}:
        raise ValueError("only an estimated or partial forecast can be confirmed")
    current["confirmed"] = True
    current["confirmed_at"] = _now()
    validate_infrastructure_forecast(current)
    current["content_hash"] = content_hash(current)
    return current


def validate_infrastructure_forecast(forecast: Mapping[str, Any]) -> None:
    if forecast.get("schema_version") != FORECAST_SCHEMA_VERSION:
        raise ValueError("unsupported infrastructure forecast schema")
    if forecast.get("status") not in {
        "estimated",
        "partial",
        "needs_clarification",
        "not_applicable",
    }:
        raise ValueError("unsupported infrastructure forecast status")
    for field in ("priced_subtotal_usd", "coverage_ratio"):
        value = forecast.get(field)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"infrastructure {field} must be finite")
    ratio = forecast.get("coverage_ratio")
    if ratio is not None and not 0 <= ratio <= 1:
        raise ValueError("infrastructure coverage_ratio must be between 0 and 1")
    lines = forecast.get("price_snapshot", {}).get("lines", [])
    identities: set[tuple[str, str]] = set()
    subtotal = 0.0
    for line in lines:
        identity = (str(line.get("line_id")), str(line.get("meter_id")))
        if identity in identities:
            raise ValueError("duplicate infrastructure price-line identity")
        identities.add(identity)
        cost = line.get("monthly_cost_usd")
        if line.get("evidence_status") == "sourced":
            if not isinstance(cost, (int, float)) or not math.isfinite(cost):
                raise ValueError("sourced infrastructure line requires a finite cost")
            subtotal += cost
        elif cost is not None:
            raise ValueError("unpriced infrastructure line cannot have a cost")
    recorded = forecast.get("priced_subtotal_usd")
    if recorded is not None and not math.isclose(
        subtotal, float(recorded), rel_tol=0, abs_tol=1e-6
    ):
        raise ValueError("infrastructure priced subtotal is inconsistent")
