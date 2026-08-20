"""Main prediction orchestrator — ties all components together."""

from __future__ import annotations

import os
import math
from typing import Optional

from future_token_predictor.archetypes import match_archetype
from future_token_predictor.classifier import classify
from future_token_predictor.cost_calculator import _get_prices, calculate_cost_with_ci
from future_token_predictor.model_validator import validate_model, ValidationStatus
from future_token_predictor.models.schemas import (
    CostEstimate,
    ModalityBreakdown,
    PredictionResult,
    UseCaseProfile,
)
from future_token_predictor.report import build_result
from future_token_predictor.scale_projector import project_scale
from future_token_predictor.tool_cost_estimator import estimate_tool_costs
from future_token_predictor.workflow_predictor import predict_workflow


# Module-level calibrator state
_calibrator = None
_calibrator_init_attempted: bool = False


def _scaled_tokens(tokens: ModalityBreakdown, share: float) -> ModalityBreakdown:
    """Allocate a token breakdown to one agent without changing its composition."""
    return ModalityBreakdown(
        text_input=tokens.text_input * share,
        text_output=tokens.text_output * share,
        cached_input=tokens.cached_input * share,
        image_input=tokens.image_input * share,
        image_output=tokens.image_output * share,
        document_input=tokens.document_input * share,
        audio_input=tokens.audio_input * share,
        audio_output=tokens.audio_output * share,
        reasoning=tokens.reasoning * share,
    )


def _price_agent_models(
    profile: UseCaseProfile,
    tokens: ModalityBreakdown,
    p5_total: float,
    p95_total: float,
    p99_total: float,
) -> tuple[CostEstimate, dict[str, float], list[dict], list[str], bool]:
    """Price normalized shares of a multi-agent token workload per assignment."""
    total_weight = sum(item.turn_weight for item in profile.agent_models)
    aggregate = CostEstimate()
    weighted_prices: dict[str, float] = {}
    ledger: list[dict] = []
    warnings: list[str] = []
    all_verified = True

    for assignment in profile.agent_models:
        validation = validate_model(
            assignment.model,
            provider_hint=assignment.provider.value,
        )
        if validation.status == ValidationStatus.NOT_FOUND:
            raise ValueError(
                f"Unknown model for agent '{assignment.agent_id}': "
                f"{assignment.provider.value}:{assignment.model}"
            )
        if validation.status == ValidationStatus.SUBSTITUTED:
            warnings.append(
                f"Agent '{assignment.agent_id}': {validation.warning or validation.message}"
            )
            assignment.model = validation.resolved_model

        share = assignment.turn_weight / total_weight
        allocated = _scaled_tokens(tokens, share)
        estimate = calculate_cost_with_ci(
            allocated,
            assignment.model,
            provider=assignment.provider,
            p5_total=p5_total * share,
            p95_total=p95_total * share,
            p99_total=p99_total * share,
        )
        prices = _get_prices(assignment.model, assignment.provider)
        for key, value in prices.items():
            weighted_prices[key] = weighted_prices.get(key, 0.0) + value * share

        aggregate.mean += estimate.mean
        aggregate.ci_95_low += estimate.ci_95_low
        aggregate.ci_95_high += estimate.ci_95_high
        aggregate.worst_case += estimate.worst_case
        all_verified = all_verified and validation.is_valid

        component_specs = [
            ("text input", allocated.text_input, prices.get("input", 2.0)),
            ("cached input", allocated.cached_input, prices.get("cached_input", prices.get("input", 2.0) * 0.25)),
            ("text output", allocated.text_output, prices.get("output", 8.0)),
            ("image input", allocated.image_input, prices.get("image_input", prices.get("input", 2.0))),
            ("image output", allocated.image_output, prices.get("output", 40.0)),
            ("document input", allocated.document_input, prices.get("input", 2.0)),
            ("audio input", allocated.audio_input, prices.get("audio_input", prices.get("input", 2.0))),
            ("audio output", allocated.audio_output, prices.get("audio_output", prices.get("output", 8.0))),
            ("reasoning", allocated.reasoning, prices.get("output", 8.0)),
        ]
        components = [
            {
                "component": f"{assignment.agent_id} / {label}",
                "tokens": round(token_count, 6),
                "rate_per_million": rate,
                "cost_usd": round(token_count * rate / 1_000_000, 9),
            }
            for label, token_count, rate in component_specs
            if token_count > 0
        ]
        ledger.append({
            "agent_id": assignment.agent_id,
            "role": assignment.role,
            "provider": assignment.provider.value,
            "model": assignment.model,
            "turn_weight": assignment.turn_weight,
            "allocation_share": share,
            "allocated_tokens": allocated.total,
            "cost_per_invocation_usd": estimate.mean,
            "pricing_verified": validation.is_valid,
            "pricing_url": validation.pricing_url,
            "model_catalog_url": validation.model_catalog_url,
            "components": components,
        })

    return aggregate, weighted_prices, ledger, warnings, all_verified


def _get_calibrator():
    """Lazy-init the Tier 2 calibrator with default DB."""
    global _calibrator, _calibrator_init_attempted
    if _calibrator_init_attempted:
        return _calibrator
    _calibrator_init_attempted = True
    try:
        from future_token_predictor.history import Calibrator, HistoryDatabase
        _calibrator = Calibrator(HistoryDatabase())
    except Exception:
        _calibrator = None  # DB unavailable — Tier 2 is disabled
    return _calibrator


def _tier3_available(client=None) -> bool:
    """Check if Tier 3 LLM-assisted estimation can run.

    Returns True if a client is provided or credentials are in the environment.
    Supports API key auth and Entra ID (endpoint-only, no key).
    """
    if client is not None:
        return True
    return bool(
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("AZURE_OPENAI_API_KEY")
        or os.environ.get("AZURE_OPENAI_ENDPOINT")  # Entra ID — no key needed
        or os.environ.get("TIER3_API_KEY")
    )


def predict(
    description: str | None = None,
    profile: UseCaseProfile | None = None,
    *,
    enable_tier2: bool = True,
    enable_tier3: bool | None = None,
    tier3_client=None,
    db_path: Optional[str] = None,
    missing_parameters: list[str] | None = None,
) -> PredictionResult:
    """Run a complete token and cost prediction.

    Accepts either:
    - A natural language description (will be classified into a profile)
    - A pre-built UseCaseProfile

    Args:
        description: Natural language description of the use case.
        profile: Pre-built UseCaseProfile.
        enable_tier2: If True (default), apply Tier 2 calibration when available.
        enable_tier3: If True, use LLM-assisted estimation. If None (default),
            auto-enables when an API key is available (OPENAI_API_KEY,
            AZURE_OPENAI_API_KEY, or TIER3_API_KEY).
        tier3_client: An LLM client for Tier 3 (implements LLMClient protocol).
            If None, auto-creates OpenAICompatibleClient when Tier 3 is enabled.
        db_path: Custom path for the history database (for testing).

    Returns a full PredictionResult with token breakdown, costs, and projections.
    """
    # Auto-detect Tier 3 availability when not explicitly set
    if enable_tier3 is None:
        enable_tier3 = _tier3_available(tier3_client)

    if profile is None:
        if description is None:
            raise ValueError("Provide either 'description' or 'profile'")
        profile = classify(description)

    # Step 0: Validate model exists
    model_warnings: list[str] = []
    requested_model = profile.model
    provider_hint = profile.provider.value if profile.provider else None
    validation = validate_model(profile.model, provider_hint=provider_hint)
    pricing_url = validation.pricing_url
    model_catalog_url = validation.model_catalog_url

    if validation.status == ValidationStatus.NOT_FOUND:
        # Model not found anywhere — return error result, never assume
        from future_token_predictor.models.schemas import (
            CostEstimate, ModalityBreakdown, ToolCostBreakdown,
        )
        return PredictionResult(
            model=profile.model,
            provider=provider_hint or "",
            archetype="unknown",
            prediction_method="validation_failed",
            pricing_verified=False,
            model_warnings=[validation.warning or validation.message],
            requested_model=requested_model,
            optimizations=[
                f"Model '{profile.model}' does not exist in any known catalog. "
                f"Sources checked: {', '.join(validation.sources_checked)}. "
                f"Please verify the model name and try again."
            ],
        )
    elif validation.status == ValidationStatus.SUBSTITUTED:
        model_warnings.append(validation.warning or validation.message)
        profile.model = validation.resolved_model
        if validation.provider:
            from future_token_predictor.models.schemas import Provider as ProvEnum
            try:
                profile.provider = ProvEnum(validation.provider)
            except ValueError:
                pass
    elif validation.status == ValidationStatus.VALID_CATALOG:
        model_warnings.append(validation.message)

    # Auto-detect agent pattern from tools when not explicitly set.
    # function_calling/custom_function/mcp_server imply an agentic tool-loop,
    # not a single prompt call.
    from future_token_predictor.models.schemas import (
        AgentPattern, AgentType, Tool as ToolEnum,
    )
    _AGENTIC_TOOLS = {ToolEnum.FUNCTION_CALLING, ToolEnum.CUSTOM_FUNCTION, ToolEnum.MCP_SERVER}
    if (
        profile.agent_pattern == AgentPattern.SINGLE_CALL
        and profile.tools
        and _AGENTIC_TOOLS & set(profile.tools)
    ):
        profile.agent_pattern = AgentPattern.TOOL_AGENT
        profile.agent_type = AgentType.HOSTED

    # Step 1: Predict tokens (workflow-aware, with Monte Carlo for complex agents)
    workflow_result = predict_workflow(profile)
    prediction_method = "tier1_heuristic"

    # Step 1b: Try Tier 2 calibration
    tokens = workflow_result.mean_tokens
    tier1_tokens = {
        "text_input": tokens.text_input,
        "text_output": tokens.text_output,
        "document_input": tokens.document_input,
        "reasoning": tokens.reasoning,
        "total": tokens.total,
    }
    # Track Monte Carlo percentiles as mutable values so Tier 2/3
    # adjustments can scale them proportionally to the new mean.
    p5_total = workflow_result.p5_total
    p50_total = workflow_result.p50_total
    p95_total = workflow_result.p95_total
    p99_total = workflow_result.p99_total
    archetype = match_archetype(
        agent_type=profile.agent_type,
        modalities=profile.modalities,
        tools=profile.tools,
        agent_pattern=profile.agent_pattern,
    )

    method_trace: dict = {
        "selected": prediction_method,
        "reason": "No matching historical calibration or accepted Tier 3 estimate was available.",
    }
    if enable_tier2:
        calibrator = None
        if db_path is not None:
            try:
                from future_token_predictor.history import Calibrator, HistoryDatabase
                calibrator = Calibrator(HistoryDatabase(db_path))
            except Exception:
                pass
        else:
            calibrator = _get_calibrator()

        if calibrator is not None:
            calibrated = calibrator.calibrate_tokens(tokens, profile.model, archetype)
            if calibrated is not None:
                if tokens.total > 0:
                    scale = calibrated.total / tokens.total
                    p5_total *= scale
                    p50_total *= scale
                    p95_total *= scale
                    p99_total *= scale
                tokens = calibrated
                prediction_method = "tier2_calibrated"
                method_trace = {
                    "selected": prediction_method,
                    "reason": "Historical actual usage matched this model and archetype, so it calibrated the Tier 1 estimate.",
                    "scale_factor": scale,
                }

    # Step 1c: Try Tier 3 LLM-assisted estimation
    # Only when we have a natural-language description to reason over; an empty
    # string gives the LLM no context and can override a valid heuristic result.
    if enable_tier3 and description:
        try:
            from future_token_predictor.history.tier3_estimator import (
                OpenAICompatibleClient,
                apply_tier3,
            )

            client = tier3_client
            if client is None:
                client = OpenAICompatibleClient()

            estimate = client.estimate_tokens(description, profile)
            if estimate is not None:
                tier3_adjusted = apply_tier3(tokens, estimate)
                if tier3_adjusted is not None:
                    if tokens.total > 0:
                        scale = tier3_adjusted.total / tokens.total
                        p5_total *= scale
                        p50_total *= scale
                        p95_total *= scale
                        p99_total *= scale
                    tokens = tier3_adjusted
                    prediction_method = "tier3_llm_assisted"
                    method_trace = {
                        "selected": prediction_method,
                        "reason": (
                            "A configured estimator model assessed the workload description; its estimate "
                            "passed confidence and sanity checks and was blended with Tier 1."
                        ),
                        "estimator_model": estimate.model_used,
                        "confidence": estimate.confidence,
                        "estimator_reasoning": estimate.reasoning,
                        "estimated_input_tokens": estimate.estimated_input_tokens,
                        "estimated_output_tokens": estimate.estimated_output_tokens,
                        "estimated_reasoning_tokens": estimate.estimated_reasoning_tokens,
                        "estimated_steps": estimate.estimated_steps,
                        "blend_formula": "final = (1 - confidence) × Tier 1 + confidence × estimator",
                    }
        except Exception:
            pass  # Tier 3 is optional — never break predictions

    # Step 2: Estimate tool costs
    tool_costs = estimate_tool_costs(profile)

    # Step 3: Calculate cost with confidence intervals
    agent_model_ledger: list[dict] = []
    if profile.agent_models:
        cost, prices, agent_model_ledger, assignment_warnings, assignment_pricing_verified = (
            _price_agent_models(profile, tokens, p5_total, p95_total, p99_total)
        )
        model_warnings.extend(assignment_warnings)
    else:
        cost = calculate_cost_with_ci(
            tokens,
            profile.model,
            provider=profile.provider,
            p5_total=p5_total,
            p95_total=p95_total,
            p99_total=p99_total,
        )
        prices = _get_prices(profile.model, profile.provider)
        assignment_pricing_verified = validation.is_valid

    # Step 4: Scale projections
    projection = project_scale(
        profile,
        tokens,
        cost.mean,
        cost.ci_95_low,
        cost.ci_95_high,
        cost.worst_case,
        tool_costs,
        input_price_per_million=prices.get("input", 2.0),
        cached_input_price_per_million=prices.get(
            "cached_input", prices.get("input", 2.0) * 0.25
        ),
    )

    # Pricing is verified only when model was found in provider catalogs
    pricing_verified = assignment_pricing_verified
    pricing_timestamp = None
    try:
        from future_token_predictor.azure_pricing import get_pricing_client
        client = get_pricing_client()
        if client.pricing_timestamp:
            pricing_timestamp = client.pricing_timestamp
    except Exception:
        pass

    provider_label = profile.provider.value if profile.provider else "openai"

    from future_token_predictor.archetypes import get_token_profile
    from future_token_predictor.providers import get_provider

    token_profile = get_token_profile(archetype, profile.complexity)
    try:
        tokenizer = get_provider(profile.provider).get_tokenizer_name(profile.model)
    except (KeyError, NotImplementedError):
        tokenizer = "o200k_base"
    if agent_model_ledger:
        cost_components = [
            component
            for assignment in agent_model_ledger
            for component in assignment["components"]
        ]
    else:
        token_components = [
            ("text input", tokens.text_input, prices.get("input", 2.0)),
            ("cached input", tokens.cached_input, prices.get("cached_input", prices.get("input", 2.0) * 0.25)),
            ("text output", tokens.text_output, prices.get("output", 8.0)),
            ("image input", tokens.image_input, prices.get("image_input", prices.get("input", 2.0))),
            ("image output", tokens.image_output, prices.get("output", 40.0)),
            ("document input", tokens.document_input, prices.get("input", 2.0)),
            ("audio input", tokens.audio_input, prices.get("audio_input", prices.get("input", 2.0))),
            ("audio output", tokens.audio_output, prices.get("audio_output", prices.get("output", 8.0))),
            ("reasoning", tokens.reasoning, prices.get("output", 8.0)),
        ]
        cost_components = [
            {
                "component": label,
                "tokens": round(token_count, 6),
                "rate_per_million": rate,
                "cost_usd": round(token_count * rate / 1_000_000, 9),
            }
            for label, token_count, rate in token_components
            if token_count > 0
        ]
    cache_discount_factor = projection.cache_discount_factor
    calculation_trace = {
        "tokens": {
            "workload_statement": description or "Structured profile supplied without a workload statement.",
            "sample_statement": None,
            "tokenizer": tokenizer,
            "tokenizer_applied": False,
            "approach": (
                "This is a workload estimate, not a token count of a fabricated example prompt. "
                "The tokenizer is shown for model provenance but was not applied to the archetype defaults."
            ),
            "archetype": archetype,
            "complexity": profile.complexity.value,
            "tier1_baseline": {key: round(value, 6) for key, value in tier1_tokens.items()},
            "archetype_defaults": {
                key: token_profile[key]
                for key in ("system_prompt", "user_input", "output_mean", "chunks_per_search")
                if key in token_profile
            },
            "document_formula": (
                f'{token_profile.get("chunks_per_search", 0)} retrieved chunks × 512 tokens/chunk'
                if tokens.document_input > 0 else None
            ),
            "final_formula": "sum of the displayed modality token estimates",
        },
        "cost_per_call": {
            "pricing_unit": "USD per 1 million tokens",
            "components": cost_components,
            "formula": "Σ(component tokens × component rate / 1,000,000)",
            "result_usd": cost.mean,
        },
        "agent_models": {
            "allocation_method": "normalized modeled turn weights",
            "assumption": (
                "The existing multi-agent token distribution is allocated across agents "
                "by normalized turn_weight. This is modeled planning evidence, not measured "
                "per-agent runtime usage."
            ),
            "assignments": agent_model_ledger,
        },
        "tool_costs": {
            "included_in_model_cost": False,
            "is_complete": not bool(tool_costs.unpriced_external_tools),
            "unpriced_external_tools": tool_costs.unpriced_external_tools,
            "components_per_invocation": [
                {
                    "component": "file search",
                    "quantity": tool_costs.file_search_calls,
                    "cost_usd": tool_costs.file_search_cost_usd,
                },
                {
                    "component": "code interpreter",
                    "quantity": tool_costs.code_interpreter_sessions,
                    "cost_usd": tool_costs.code_interpreter_cost_usd,
                },
                {
                    "component": "web search",
                    "quantity": tool_costs.web_search_calls,
                    "cost_usd": tool_costs.web_search_cost_usd,
                },
            ],
            "per_invocation_usd": tool_costs.total_usd,
            "storage_gb": tool_costs.storage_gb,
            "storage_usd_per_day": tool_costs.storage_cost_usd_per_day,
            "daily_formula": "per-invocation tool cost × daily calls + storage cost per day",
            "daily_result_usd": projection.daily_tool_cost_usd,
            "monthly_formula": "daily tool cost × 30 days",
            "monthly_result_usd": projection.monthly_tool_cost_usd,
        },
        "scale": {
            "users": profile.users,
            "calls_per_user_per_day": profile.calls_per_user_per_day,
            "daily_calls": projection.daily_calls,
            "daily_calls_formula": "users × calls per user per day",
            "cache_assumptions": {
                "system_prompt_share_of_text_input": 0.6,
                "cache_hit_rate_after_first_call": 0.75,
                "cached_calls_per_day": max(0, profile.calls_per_user_per_day - 1) * profile.users,
                "estimated_token_savings_pct": projection.cache_savings_pct,
                "cost_discount_factor": cache_discount_factor,
                "input_rate_per_million": prices.get("input", 2.0),
                "cached_input_rate_per_million": prices.get(
                    "cached_input", prices.get("input", 2.0) * 0.25
                ),
                "daily_cache_savings_usd": projection.daily_cache_savings_usd,
                "note": "Cache savings reprice the estimated cached system-prompt tokens at the model catalog's cached-input rate.",
            },
            "daily_formula": "cost per call × daily calls × cache discount factor",
            "daily_result_usd": projection.daily_cost.mean,
            "monthly_formula": "daily cost × 30 days",
            "monthly_result_usd": projection.monthly_cost.mean,
            "annual_formula": "daily cost × 365 days",
            "annual_result_usd": projection.annual_cost.mean,
        },
        "method": method_trace,
        "bounds": {
            "method": "monte_carlo_quantile" if workflow_result.samples > 1 else "heuristic_multiplier",
            "samples": workflow_result.samples,
            "seed": 42 if workflow_result.samples > 1 else None,
            "formula": (
                "P5/P50/P95 are empirical quantiles of simulated workflow runs."
                if workflow_result.samples > 1
                else "P5 = 70% of estimate; P50 = estimate; P95 = 150% of estimate."
            ),
        },
    }

    # Step 5: Record prediction in history (best-effort)
    prediction_id = _record_prediction(
        tokens, cost, profile, archetype, prediction_method, description, db_path
    )

    return build_result(
        tokens=tokens,
        cost=cost,
        tool_costs=tool_costs,
        projection=projection,
        model=profile.model,
        provider=provider_label,
        archetype=archetype,
        pricing_timestamp=pricing_timestamp,
        pricing_verified=pricing_verified,
        prediction_method=prediction_method,
        tokens_p5=p5_total,
        tokens_p50=p50_total,
        tokens_p95=p95_total,
        model_warnings=model_warnings,
        requested_model=requested_model if requested_model != profile.model else None,
        pricing_url=pricing_url,
        model_catalog_url=model_catalog_url,
        missing_parameters=missing_parameters or [],
        prediction_id=prediction_id,
        bound_method=(
            "monte_carlo_quantile"
            if workflow_result.samples > 1
            else "heuristic_multiplier"
        ),
        bound_samples=workflow_result.samples,
        bound_seed=42 if workflow_result.samples > 1 else None,
        calculation_trace=calculation_trace,
        agent_model_assignments=agent_model_ledger,
    )


def _record_prediction(
    tokens,
    cost,
    profile: UseCaseProfile,
    archetype: str,
    prediction_method: str,
    description: Optional[str],
    db_path: Optional[str],
) -> Optional[int]:
    """Best-effort recording of predictions into the history DB."""
    try:
        from future_token_predictor.history import HistoryDatabase, PredictionRecord

        db = HistoryDatabase(db_path) if db_path else HistoryDatabase()
        record = PredictionRecord(
            model=profile.model,
            provider=profile.provider.value if profile.provider else "openai",
            archetype=archetype,
            complexity=profile.complexity.value if profile.complexity else "medium",
            predicted_text_input=tokens.text_input,
            predicted_text_output=tokens.text_output,
            predicted_image_input=tokens.image_input,
            predicted_document_input=tokens.document_input,
            predicted_audio_input=tokens.audio_input,
            predicted_reasoning=tokens.reasoning,
            predicted_total=tokens.total,
            predicted_cost=cost.mean,
            prediction_method=prediction_method,
            description=description,
        )
        return db.record_prediction(record)
    except Exception:
        # Never let history recording break predictions
        return None


def record_actual(
    prediction_id: int,
    *,
    actual_text_input: float = 0.0,
    actual_text_output: float = 0.0,
    actual_image_input: float = 0.0,
    actual_document_input: float = 0.0,
    actual_audio_input: float = 0.0,
    actual_reasoning: float = 0.0,
    actual_total: Optional[float] = None,
    actual_cost: Optional[float] = None,
    db_path: Optional[str] = None,
) -> "ActualRecordingResult":
    """Record actual token usage for a previous prediction.

    Call this after running the actual LLM call to feed Tier 2 calibration.
    """
    from future_token_predictor.history import HistoryDatabase
    from future_token_predictor.models.schemas import ActualRecordingResult

    values = {
        "actual_text_input": actual_text_input,
        "actual_text_output": actual_text_output,
        "actual_image_input": actual_image_input,
        "actual_document_input": actual_document_input,
        "actual_audio_input": actual_audio_input,
        "actual_reasoning": actual_reasoning,
        "actual_total": actual_total,
        "actual_cost": actual_cost,
    }
    for name, value in values.items():
        if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
            raise ValueError(f"{name} must be a finite non-negative number")

    db = HistoryDatabase(db_path) if db_path else HistoryDatabase()
    status = db.record_actual(
        prediction_id,
        actual_text_input=actual_text_input,
        actual_text_output=actual_text_output,
        actual_image_input=actual_image_input,
        actual_document_input=actual_document_input,
        actual_audio_input=actual_audio_input,
        actual_reasoning=actual_reasoning,
        actual_total=actual_total,
        actual_cost=actual_cost,
    )
    return ActualRecordingResult(prediction_id=prediction_id, status=status)
