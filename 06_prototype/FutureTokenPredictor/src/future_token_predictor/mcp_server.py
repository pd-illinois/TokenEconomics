"""MCP Server for Future Token Predictor.

Exposes tools for predicting LLM token usage and costs
across all major providers for multimodal agentic workflows.

Transport: stdio (default) or SSE.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from enum import Enum
import json
import math
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from future_token_predictor.classifier import analyze_workload, classify
from future_token_predictor.models.schemas import (
    AgentModelAssignment,
    AgentPattern,
    AgentType,
    AudioInputProfile,
    Complexity,
    DeploymentRegion,
    DetailLevel,
    DocumentInputProfile,
    ImageInputProfile,
    Modality,
    Provider,
    RetrievalStrategy,
    Tool as ToolEnum,
    UseCaseProfile,
)
from future_token_predictor.predictor import predict, record_actual
from future_token_predictor.report import format_report
from future_token_predictor.token_calculator import (
    image_input_tokens,
    document_tokens,
)

# --- Server Setup ---

server = Server("future-token-predictor")


def _build_all_models() -> list[str]:
    """Derive priced model IDs from the provider registry at import time.

    Falls back to an empty list if the registry can't be initialised (e.g.
    during early tests before providers are registered).
    """
    try:
        from future_token_predictor.model_catalog import build_model_catalog

        return list(dict.fromkeys(
            offering["model"]
            for offering in build_model_catalog()["offerings"]
        ))
    except Exception:
        return []


_ALL_MODELS = _build_all_models()

_ALL_PROVIDERS = [p.value for p in Provider]


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available MCP tools."""
    return [
        Tool(
            name="analyze_workload",
            description=(
                "Analyze a workload description into versioned topology, agent-count, "
                "modality, tool, evidence, uncertainty, and clarification fields without "
                "performing a cost prediction."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "description": {"type": "string", "minLength": 1},
                },
                "required": ["description"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="predict_token_usage",
            description=(
                "Predict total LLM token usage and cost for a multimodal "
                "agentic workflow. Supports all major providers: OpenAI, Anthropic, "
                "Google, Mistral, Cohere, AWS Bedrock, and local models. Accepts "
                "a natural language description or structured parameters."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": (
                            "Natural language description of the use case. "
                            "Example: 'A ReAct agent using Claude Sonnet 4 "
                            "with RAG over PDFs, 100 users, 10 queries/day'"
                        ),
                    },
                    "model": {
                        "type": "string",
                        "description": (
                            "Model name (e.g., gpt-4.1, gpt-5, claude-sonnet-4). "
                            "Models are validated against provider catalogs, live "
                            "APIs, and the Azure AI model catalog before pricing. "
                            "Unknown models will return an error instead of guessing."
                        ),
                    },
                    "provider": {
                        "type": "string",
                        "description": "LLM provider",
                        "enum": _ALL_PROVIDERS,
                    },
                    "agent_pattern": {
                        "type": "string",
                        "enum": [p.value for p in AgentPattern],
                        "description": "Agent workflow pattern",
                    },
                    "modalities": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [m.value for m in Modality],
                        },
                    },
                    "tools": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [t.value for t in ToolEnum],
                        },
                    },
                    "complexity": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                    },
                    "users": {"type": "integer", "minimum": 1},
                    "calls_per_user_per_day": {"type": "integer", "minimum": 1},
                    "multi_agent_count": {"type": "integer", "minimum": 1},
                    "agent_models": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "agent_id": {"type": "string", "minLength": 1},
                                "role": {"type": "string"},
                                "provider": {
                                    "type": "string",
                                    "enum": _ALL_PROVIDERS,
                                },
                                "model": {"type": "string", "minLength": 1},
                                "turn_weight": {
                                    "type": "number",
                                    "exclusiveMinimum": 0,
                                    "default": 1,
                                },
                            },
                            "required": ["agent_id", "provider", "model"],
                            "additionalProperties": False,
                        },
                        "description": (
                            "Optional per-agent provider/model assignments. Token workload "
                            "is allocated by normalized turn_weight and each share is priced "
                            "against its own verified model catalog entry."
                        ),
                    },
                    "image_count": {"type": "integer"},
                    "image_width": {"type": "integer"},
                    "image_height": {"type": "integer"},
                    "image_detail": {"type": "string", "enum": ["low", "high"]},
                    "document_pages": {"type": "integer"},
                    "document_count": {"type": "integer"},
                    "audio_seconds": {"type": "number"},
                    "searches_per_call": {"type": "integer", "minimum": 1},
                    "workflow_steps": {"type": "integer", "minimum": 1},
                    "output_format": {
                        "type": "string",
                        "enum": ["markdown", "json"],
                        "description": "Output format: markdown (default, human-readable) or json (structured, for agent composition)",
                        "default": "markdown",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="get_model_pricing",
            description=(
                "Get pricing for any LLM model from any provider. "
                "Returns per-modality pricing in USD per 1M tokens."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model": {
                        "type": "string",
                        "description": "Model name (e.g., gpt-4.1, claude-sonnet-4, gemini-2.5-pro)",
                    },
                    "provider": {
                        "type": "string",
                        "enum": _ALL_PROVIDERS,
                        "description": "Provider (auto-detected from model if omitted)",
                    },
                },
                "required": ["model"],
            },
        ),
        Tool(
            name="estimate_image_tokens",
            description=(
                "Calculate image input tokens using provider-specific formulas. "
                "OpenAI: tile-based (512×512). Anthropic: resolution tiers. "
                "Google: 258 fixed. Mistral: 16×16 pixel tiles."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "width": {"type": "integer", "description": "Image width in pixels"},
                    "height": {"type": "integer", "description": "Image height in pixels"},
                    "detail": {"type": "string", "enum": ["low", "high"], "default": "high"},
                    "count": {"type": "integer", "default": 1},
                    "model": {"type": "string", "description": "Model for provider-specific calculation"},
                    "provider": {"type": "string", "enum": _ALL_PROVIDERS},
                },
                "required": ["width", "height"],
            },
        ),
        Tool(
            name="estimate_document_tokens",
            description=(
                "Estimate tokens for document inputs. "
                "Direct/RAG: ~650 tokens/page. File Search: 512 tokens/chunk × top_k."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "pages": {"type": "integer"},
                    "document_count": {"type": "integer", "default": 1},
                    "strategy": {"type": "string", "enum": ["direct", "file_search", "rag"], "default": "file_search"},
                    "top_k": {"type": "integer", "default": 5},
                },
                "required": ["pages"],
            },
        ),
        Tool(
            name="compare_providers",
            description=(
                "Compare token costs across multiple providers for the same workload. "
                "Returns a cost comparison table for all providers or a specified subset."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "Natural language workload description",
                    },
                    "providers": {
                        "type": "array",
                        "items": {"type": "string", "enum": _ALL_PROVIDERS},
                        "description": "Providers to compare (defaults to all)",
                    },
                    "input_tokens": {"type": "integer", "description": "Input tokens per call"},
                    "output_tokens": {"type": "integer", "description": "Output tokens per call"},
                    "calls": {"type": "integer", "description": "Number of calls", "default": 1000},
                },
                "required": [],
            },
        ),
        Tool(
            name="refresh_models",
            description=(
                "Refresh model registry by fetching live models from provider APIs. "
                "Requires API keys set as environment variables (OPENAI_API_KEY, "
                "ANTHROPIC_API_KEY, GOOGLE_API_KEY, MISTRAL_API_KEY). "
                "Azure pricing is always available (free, no auth)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "enum": ["openai", "anthropic", "google", "mistral", "azure"],
                        "description": "Provider to refresh (omit for all)",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Force refresh even if cache is fresh",
                        "default": False,
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="record_actual_usage",
            description=(
                "Record actual token usage and cost for a prior prediction. "
                "Returns updated, already_recorded, or not_found."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "prediction_id": {"type": "integer", "minimum": 1},
                    "actual_text_input": {"type": "number", "minimum": 0},
                    "actual_text_output": {"type": "number", "minimum": 0},
                    "actual_image_input": {"type": "number", "minimum": 0},
                    "actual_document_input": {"type": "number", "minimum": 0},
                    "actual_audio_input": {"type": "number", "minimum": 0},
                    "actual_reasoning": {"type": "number", "minimum": 0},
                    "actual_total": {"type": "number", "minimum": 0},
                    "actual_cost": {"type": "number", "minimum": 0},
                    "db_path": {
                        "type": "string",
                        "description": "Optional local history database path.",
                    },
                },
                "required": ["prediction_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool invocations."""
    handlers = {
        "analyze_workload": _handle_analyze,
        "predict_token_usage": _handle_predict,
        "get_model_pricing": _handle_pricing,
        "estimate_image_tokens": _handle_image_tokens,
        "estimate_document_tokens": _handle_document_tokens,
        "compare_providers": _handle_compare,
        "refresh_models": _handle_refresh,
        "record_actual_usage": _handle_record_actual,
    }
    handler = handlers.get(name)
    if handler:
        return await handler(arguments)
    return [TextContent(type="text", text=f"Unknown tool: {name}")]


def _json_default(value):
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


async def _handle_analyze(args: dict) -> list[TextContent]:
    """Return deterministic workload evidence without running prediction."""
    description = str(args.get("description", "")).strip()
    if not description:
        raise ValueError("description is required")
    payload = asdict(analyze_workload(description))
    return [TextContent(
        type="text",
        text=json.dumps(payload, default=_json_default),
    )]


async def _handle_predict(args: dict) -> list[TextContent]:
    """Handle predict_token_usage tool call."""
    description = args.get("description")
    output_format = args.get("output_format", "markdown")

    # Track which key parameters the caller did NOT provide
    _TRACKABLE_PARAMS = {
        "model": "model (defaulted to gpt-4.1)",
        "provider": "provider (auto-detected from model)",
        "agent_pattern": "agent_pattern (defaulted to single_call)",
        "users": "users (defaulted to 1)",
        "calls_per_user_per_day": "calls_per_user_per_day (defaulted to 1)",
        "complexity": "complexity (defaulted to medium)",
        "modalities": "modalities (defaulted to text-only)",
        "tools": "tools (none specified)",
    }
    missing_parameters = [v for k, v in _TRACKABLE_PARAMS.items() if k not in args]

    profile = classify(description) if description else None
    structured_profile_keys = {
        "model",
        "provider",
        "agent_pattern",
        "modalities",
        "tools",
        "complexity",
        "users",
        "calls_per_user_per_day",
        "multi_agent_count",
        "agent_models",
        "image_count",
        "document_pages",
        "document_count",
        "audio_seconds",
        "searches_per_call",
        "workflow_steps",
    }
    if profile is not None or structured_profile_keys.intersection(args):
        profile = profile or UseCaseProfile()
        if "model" in args:
            profile.model = args["model"]
        if "provider" in args:
            profile.provider = Provider(args["provider"])
        elif "model" in args:
            # Auto-detect provider from model name
            from future_token_predictor.providers import resolve_provider_for_model
            result = resolve_provider_for_model(args["model"])
            if result:
                profile.provider = result[0]
        if "agent_pattern" in args:
            profile.agent_pattern = AgentPattern(args["agent_pattern"])
            profile.agent_type = {
                AgentPattern.SINGLE_CALL: AgentType.PROMPT,
                AgentPattern.RAG_PIPELINE: AgentType.PROMPT,
                AgentPattern.CODE_EXEC: AgentType.PROMPT,
                AgentPattern.REACT_AGENT: AgentType.HOSTED,
                AgentPattern.MULTI_AGENT: AgentType.HOSTED,
                AgentPattern.WORKFLOW: AgentType.WORKFLOW,
            }[profile.agent_pattern]
        if "modalities" in args:
            profile.modalities = [Modality(m) for m in args["modalities"]]
        if "tools" in args:
            profile.tools = [ToolEnum(t) for t in args["tools"]]
        if "complexity" in args:
            profile.complexity = Complexity(args["complexity"])
        if "users" in args:
            profile.users = args["users"]
        if "calls_per_user_per_day" in args:
            profile.calls_per_user_per_day = args["calls_per_user_per_day"]
        if "multi_agent_count" in args:
            profile.multi_agent_count = args["multi_agent_count"]
        if "agent_models" in args:
            if len(args["agent_models"]) < 2:
                raise ValueError("agent_models requires at least two assignments")
            seen_agent_ids: set[str] = set()
            profile.agent_models = []
            for item in args["agent_models"]:
                agent_id = str(item["agent_id"]).strip()
                if not agent_id or agent_id in seen_agent_ids:
                    raise ValueError("agent_models requires unique, non-empty agent_id values")
                model = str(item["model"]).strip()
                if not str(item["provider"]).strip() or not model:
                    raise ValueError("agent_models requires non-empty provider and model values")
                turn_weight = float(item.get("turn_weight", 1.0))
                if not math.isfinite(turn_weight) or turn_weight <= 0:
                    raise ValueError(
                        "agent model turn_weight must be finite and greater than zero"
                    )
                seen_agent_ids.add(agent_id)
                profile.agent_models.append(AgentModelAssignment(
                    agent_id=agent_id,
                    role=str(item.get("role", "")).strip(),
                    provider=Provider(item["provider"]),
                    model=model,
                    turn_weight=turn_weight,
                ))
            if "multi_agent_count" in args and profile.multi_agent_count != len(profile.agent_models):
                raise ValueError("multi_agent_count must equal the number of agent_models")
            profile.multi_agent_count = len(profile.agent_models)
            profile.agent_pattern = AgentPattern.MULTI_AGENT
            profile.agent_type = AgentType.HOSTED
        if "image_count" in args:
            profile.image_inputs = ImageInputProfile(
                count_per_call=args["image_count"],
                avg_width=args.get("image_width", 1024),
                avg_height=args.get("image_height", 1024),
                detail_level=DetailLevel(args.get("image_detail", "high")),
            )
        if "document_pages" in args:
            profile.document_inputs = DocumentInputProfile(
                count=args.get("document_count", 1),
                avg_pages=args["document_pages"],
            )
        if "audio_seconds" in args:
            profile.audio_inputs = AudioInputProfile(
                avg_duration_seconds=args["audio_seconds"]
            )
        if "searches_per_call" in args:
            profile.searches_per_call = args["searches_per_call"]
        if "workflow_steps" in args:
            profile.workflow_steps = args["workflow_steps"]

    result = predict(description=description, profile=profile, missing_parameters=missing_parameters)

    if output_format == "json":
        from future_token_predictor.report import build_json_output
        return [TextContent(type="text", text=json.dumps(build_json_output(result), indent=2))]

    report = format_report(result)
    return [TextContent(type="text", text=report)]


async def _handle_record_actual(args: dict) -> list[TextContent]:
    """Handle record_actual_usage tool call."""
    outcome = record_actual(
        args["prediction_id"],
        actual_text_input=args.get("actual_text_input", 0.0),
        actual_text_output=args.get("actual_text_output", 0.0),
        actual_image_input=args.get("actual_image_input", 0.0),
        actual_document_input=args.get("actual_document_input", 0.0),
        actual_audio_input=args.get("actual_audio_input", 0.0),
        actual_reasoning=args.get("actual_reasoning", 0.0),
        actual_total=args.get("actual_total"),
        actual_cost=args.get("actual_cost"),
        db_path=args.get("db_path"),
    )
    return [TextContent(type="text", text=json.dumps({
        "prediction_id": outcome.prediction_id,
        "status": outcome.status,
    }))]


async def _handle_pricing(args: dict) -> list[TextContent]:
    """Handle get_model_pricing tool call."""
    model = args["model"]
    provider_str = args.get("provider")

    # Validate model exists before returning pricing
    from future_token_predictor.model_validator import validate_model, ValidationStatus
    provider_hint = provider_str if provider_str else None
    validation = validate_model(model, provider_hint=provider_hint)

    if validation.status == ValidationStatus.NOT_FOUND:
        return [TextContent(
            type="text",
            text=(
                f"## ⚠️ Model Not Found: {model}\n\n"
                f"Model '{model}' was not found in any provider catalog.\n\n"
                f"**Sources checked:** {', '.join(validation.sources_checked)}\n\n"
                f"Please verify the model name. Cannot provide pricing for an unknown model."
            ),
        )]

    resolved_model = validation.resolved_model
    provider = Provider(provider_str) if provider_str else None
    from future_token_predictor.cost_calculator import _get_prices
    prices = _get_prices(resolved_model, provider)

    provider_label = provider.value if provider else "auto-detected"
    output = f"## {resolved_model} Pricing ({provider_label})\n\n"

    if validation.warning:
        output += f"⚠️ {validation.warning}\n\n"

    output += "| Type | USD per 1M tokens |\n|------|-------------------|\n"
    for key, value in sorted(prices.items()):
        output += f"| {key} | ${value:.4f} |\n"

    # Add source citations
    if validation.pricing_url or validation.model_catalog_url:
        output += "\n### 📎 Sources\n\n"
        if validation.model_catalog_url:
            output += f"- **Model Catalog:** {validation.model_catalog_url}\n"
        if validation.pricing_url:
            output += f"- **Pricing Reference:** {validation.pricing_url}\n"

    return [TextContent(type="text", text=output)]


async def _handle_image_tokens(args: dict) -> list[TextContent]:
    """Handle estimate_image_tokens tool call."""
    width = args["width"]
    height = args["height"]
    detail = DetailLevel(args.get("detail", "high"))
    count = args.get("count", 1)
    model = args.get("model", "gpt-4.1")
    provider = Provider(args["provider"]) if "provider" in args else None

    profile = ImageInputProfile(
        count_per_call=count, avg_width=width, avg_height=height, detail_level=detail,
    )
    tokens = image_input_tokens(profile, model=model, provider=provider)

    output = f"## Image Token Estimate\n\n"
    output += f"- Resolution: {width}×{height}\n"
    output += f"- Detail: {detail.value}\n"
    output += f"- Count: {count}\n"
    output += f"- Model: {model}\n"
    output += f"- **Total tokens: {tokens:,}**\n"

    return [TextContent(type="text", text=output)]


async def _handle_document_tokens(args: dict) -> list[TextContent]:
    """Handle estimate_document_tokens tool call."""
    pages = args["pages"]
    count = args.get("document_count", 1)
    strategy = RetrievalStrategy(args.get("strategy", "file_search"))
    top_k = args.get("top_k", 5)

    profile = DocumentInputProfile(
        count=count, avg_pages=pages, retrieval_strategy=strategy, top_k=top_k,
    )
    tokens = document_tokens(profile)

    output = f"## Document Token Estimate\n\n"
    output += f"- Documents: {count}\n"
    output += f"- Pages/doc: {pages}\n"
    output += f"- Strategy: {strategy.value}\n"
    if strategy == RetrievalStrategy.FILE_SEARCH:
        output += f"- Top-k chunks: {top_k}\n"
    output += f"- **Total tokens: {tokens:,}**\n"

    return [TextContent(type="text", text=output)]


async def _handle_compare(args: dict) -> list[TextContent]:
    """Handle compare_providers tool call."""
    from future_token_predictor.providers import list_providers, get_provider
    from future_token_predictor.models.schemas import ModalityBreakdown
    from future_token_predictor.cost_calculator import calculate_cost_with_ci

    input_tokens = args.get("input_tokens", 1000)
    output_tokens = args.get("output_tokens", 2000)
    calls = args.get("calls", 1000)
    requested = args.get("providers")

    tokens = ModalityBreakdown(text_input=input_tokens, text_output=output_tokens)

    providers_to_compare = [Provider(p) for p in requested] if requested else list_providers()
    # Deduplicate (azure_openai and openai share models)
    seen = set()
    unique_providers = []
    for p in providers_to_compare:
        if p not in seen:
            seen.add(p)
            unique_providers.append(p)

    output = "## Provider Cost Comparison\n\n"
    output += f"Workload: {input_tokens:,} input + {output_tokens:,} output tokens × {calls:,} calls\n\n"
    output += "| Provider | Model | Cost/call (mean) | Range (90% CI) | Cost/{:,} calls |\n".format(calls)
    output += "|----------|-------|-----------------|----------------|----------------|\n"

    rows = []
    for pid in unique_providers:
        try:
            prov = get_provider(pid)
        except KeyError:
            continue
        models = prov.list_models()
        # Pick the first general-purpose text model
        best_model = models[0] if models else None
        if not best_model:
            continue
        cost = calculate_cost_with_ci(tokens, best_model, provider=pid)
        total = cost.mean * calls
        rows.append((prov.display_name, best_model, cost, total))

    rows.sort(key=lambda r: r[2].mean)
    for display_name, model, cost, total in rows:
        output += f"| {display_name} | {model} | ${cost.mean:.6f} | ${cost.ci_95_low:.6f} — ${cost.ci_95_high:.6f} | ${total:.2f} |\n"

    if rows:
        cheapest = rows[0]
        most_expensive = rows[-1]
        if most_expensive[2].mean > 0:
            ratio = most_expensive[2].mean / cheapest[2].mean if cheapest[2].mean > 0 else float('inf')
            output += f"\n*Cheapest: {cheapest[0]} ({cheapest[1]}). "
            output += f"Most expensive is {ratio:.1f}× more.*"

    return [TextContent(type="text", text=output)]


async def _handle_refresh(args: dict) -> list[TextContent]:
    """Handle refresh_models tool call."""
    from future_token_predictor.providers.live_registry import (
        fetch_live_models,
        fetch_azure_pricing,
        invalidate_cache,
    )

    provider = args.get("provider")
    force = args.get("force", False)

    if force:
        invalidate_cache(provider)

    targets = [provider] if provider and provider != "azure" else ["openai", "anthropic", "google", "mistral"]
    include_azure = provider in (None, "azure")

    output = "## Model Registry Refresh\n\n"
    total = 0

    for p in targets:
        models = fetch_live_models(p)
        count = len(models)
        total += count
        if count:
            sample = ", ".join(m.model_id for m in models[:5])
            if count > 5:
                sample += f", ... (+{count - 5} more)"
            output += f"- **{p}**: {count} models ({sample})\n"
        else:
            output += f"- **{p}**: No API key set or fetch failed\n"

    if include_azure:
        pricing = fetch_azure_pricing()
        if pricing:
            models_with_pricing = ", ".join(e.model_id for e in pricing[:5])
            output += f"- **azure pricing**: {len(pricing)} models ({models_with_pricing})\n"
        else:
            output += "- **azure pricing**: Fetch failed or no data\n"

    output += f"\n**Total live models discovered: {total}**\n"
    output += "\nLive models supplement the static catalog. Unknown models "
    output += "found via API will be usable for predictions."

    return [TextContent(type="text", text=output)]


def main():
    """Run the MCP server with stdio transport."""
    from dotenv import load_dotenv
    load_dotenv()
    import anyio
    anyio.run(_run)


async def _run():
    """Async entry point for the MCP server."""
    # Pre-warm the pricing cache so the sync HTTP fetch never runs on the hot path.
    # _get_cached_or_fetch() uses httpx.Client (sync); doing this once at startup
    # via asyncio.to_thread keeps the event loop unblocked during predictions.
    from future_token_predictor.azure_pricing import get_pricing_client
    await asyncio.to_thread(get_pricing_client()._get_cached_or_fetch)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    main()
