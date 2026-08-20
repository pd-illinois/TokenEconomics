"""GitHub Copilot token-derived AI Credit forecasting."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .commercial_contracts import EvidenceStatus
from .commercial_forecasting import CommercialForecastError


DEFAULT_RATE_CARD = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "commercial"
    / "github_copilot_usage.v1.json"
)


@dataclass(frozen=True)
class GitHubTokenUsage:
    input_tokens: float
    cached_input_tokens: float
    cache_write_tokens: float
    output_tokens: float
    max_input_tokens_per_request: int | None = None

    def __post_init__(self) -> None:
        values = (
            self.input_tokens,
            self.cached_input_tokens,
            self.cache_write_tokens,
            self.output_tokens,
        )
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise CommercialForecastError(
                "GitHub Copilot token usage must be finite and non-negative"
            )
        if sum(values) <= 0:
            raise CommercialForecastError(
                "GitHub Copilot requires at least one token quantity"
            )
        if (
            self.max_input_tokens_per_request is not None
            and (
                isinstance(self.max_input_tokens_per_request, bool)
                or not isinstance(self.max_input_tokens_per_request, int)
                or self.max_input_tokens_per_request < 1
            )
        ):
            raise CommercialForecastError(
                "max_input_tokens_per_request must be a positive integer"
            )


def load_github_rate_card(
    path: str | Path = DEFAULT_RATE_CARD, *, as_of: date | None = None
) -> dict:
    path = Path(path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CommercialForecastError(f"GitHub rate card not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise CommercialForecastError("GitHub rate card could not be read") from exc
    if not isinstance(document, dict):
        raise CommercialForecastError("GitHub rate card must be an object")
    required = (
        "schema_version",
        "rate_card_id",
        "version",
        "effective_from",
        "review_after",
        "source_url",
        "source_revision",
        "source_retrieved_at",
        "models",
        "plans",
    )
    if any(document.get(field) in (None, "") for field in required):
        raise CommercialForecastError("GitHub rate card evidence is incomplete")
    if document.get("status") != "active_reviewed":
        raise CommercialForecastError("GitHub rate card is not reviewed")
    if document.get("evidence_status") != EvidenceStatus.DOCUMENTED.value:
        raise CommercialForecastError("GitHub rate card must be documented evidence")
    forecast_date = as_of or date.today()
    try:
        effective_from = date.fromisoformat(document["effective_from"])
        effective_to = (
            date.fromisoformat(document["effective_to"])
            if document.get("effective_to")
            else None
        )
        review_after = date.fromisoformat(document["review_after"])
    except (TypeError, ValueError) as exc:
        raise CommercialForecastError(
            "GitHub rate card contains an invalid evidence date"
        ) from exc
    if forecast_date < effective_from or (
        effective_to is not None and forecast_date > effective_to
    ):
        raise CommercialForecastError(
            f"GitHub rate card is not effective on {forecast_date.isoformat()}"
        )
    if forecast_date > review_after:
        raise CommercialForecastError(
            f"GitHub rate card is stale after {review_after.isoformat()}"
        )
    if not str(document["source_url"]).startswith("https://"):
        raise CommercialForecastError("GitHub rate card source_url must use https")
    try:
        datetime.fromisoformat(
            str(document["source_retrieved_at"]).replace("Z", "+00:00")
        )
    except (TypeError, ValueError) as exc:
        raise CommercialForecastError(
            "GitHub rate card source_retrieved_at must be an ISO timestamp"
        ) from exc
    for group in ("models", "plans"):
        if not isinstance(document[group], dict) or not document[group]:
            raise CommercialForecastError(f"GitHub {group} must be a non-empty object")
    try:
        credits_per_usd = float(document["github_ai_credits_per_usd"])
        rate_fields = (
            "input_usd_per_million",
            "cached_input_usd_per_million",
            "cache_write_usd_per_million",
            "output_usd_per_million",
        )
        model_rates = [
            float(model[field])
            for model in document["models"].values()
            for field in rate_fields
        ]
        for model in document["models"].values():
            threshold = model.get("long_context_threshold_input_tokens")
            long_context = model.get("long_context")
            if (threshold is None) != (long_context is None):
                raise CommercialForecastError(
                    "GitHub long-context evidence requires a threshold and rates"
                )
            if threshold is not None:
                if isinstance(threshold, bool) or int(threshold) < 1:
                    raise CommercialForecastError(
                        "GitHub long-context threshold must be positive"
                    )
                model_rates.extend(float(long_context[field]) for field in rate_fields)
        included_credits = (
            float(plan["included_ai_credits_per_user_month"])
            for plan in document["plans"].values()
        )
        numeric_values = [credits_per_usd, *model_rates, *included_credits]
    except (KeyError, TypeError, ValueError) as exc:
        raise CommercialForecastError(
            "GitHub rate card contains incomplete numeric rates"
        ) from exc
    if credits_per_usd <= 0 or any(
        not math.isfinite(value) or value < 0 for value in numeric_values
    ):
        raise CommercialForecastError(
            "GitHub rate card rates must be finite and non-negative"
        )
    if any(
        not isinstance(item, dict) or not str(item.get("display_name", "")).strip()
        for group in ("models", "plans")
        for item in document[group].values()
    ):
        raise CommercialForecastError(
            "GitHub rate card entries require display names"
        )
    return document


def forecast_github_copilot(
    rate_card: dict,
    *,
    model_id: str,
    plan_id: str,
    seat_count: int,
    usage: GitHubTokenUsage,
    fixed_seat_cost_usd: float,
    additional_usage_enabled: bool,
) -> dict:
    """Convert sourced per-token GitHub prices into GitHub AI Credits."""
    if seat_count < 1:
        raise CommercialForecastError("GitHub Copilot seat_count must be positive")
    if not math.isfinite(fixed_seat_cost_usd) or fixed_seat_cost_usd < 0:
        raise CommercialForecastError(
            "GitHub Copilot fixed seat cost must be finite and non-negative"
        )
    try:
        model = rate_card["models"][model_id]
        plan = rate_card["plans"][plan_id]
    except KeyError as exc:
        raise CommercialForecastError(
            f"GitHub rate evidence not found: {exc.args[0]}"
        ) from exc
    threshold = model.get("long_context_threshold_input_tokens")
    if threshold is not None and usage.max_input_tokens_per_request is None:
        raise CommercialForecastError(
            "max_input_tokens_per_request is required for tiered GitHub model pricing"
        )
    long_context = (
        threshold is not None
        and usage.max_input_tokens_per_request is not None
        and usage.max_input_tokens_per_request > int(threshold)
    )
    selected_rates = model["long_context"] if long_context else model
    rates = {
        "input_tokens": float(selected_rates["input_usd_per_million"]),
        "cached_input_tokens": float(
            selected_rates["cached_input_usd_per_million"]
        ),
        "cache_write_tokens": float(
            selected_rates["cache_write_usd_per_million"]
        ),
        "output_tokens": float(selected_rates["output_usd_per_million"]),
    }
    quantities = {
        "input_tokens": usage.input_tokens,
        "cached_input_tokens": usage.cached_input_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "output_tokens": usage.output_tokens,
    }
    lines = [
        {
            "component": component,
            "tokens": quantities[component],
            "rate_usd_per_million": rate,
            "cost_usd": quantities[component] * rate / 1_000_000,
        }
        for component, rate in rates.items()
    ]
    model_cost_usd = sum(line["cost_usd"] for line in lines)
    credits_per_usd = float(rate_card["github_ai_credits_per_usd"])
    gross_credits = model_cost_usd * credits_per_usd
    included_credits = float(plan["included_ai_credits_per_user_month"]) * seat_count
    additional_credits = max(0.0, gross_credits - included_credits)
    additional_cost = (
        0.0
        if additional_credits == 0
        else additional_credits / credits_per_usd
        if additional_usage_enabled
        else None
    )
    capacity_risk = additional_credits > 0 and not additional_usage_enabled
    return {
        "schema_version": "1.0",
        "scope": "github_token_derived_credit",
        "status": "capacity_risk" if capacity_risk else "complete",
        "evidence_status": EvidenceStatus.MODELED.value,
        "currency": "GitHub AI Credits",
        "credit_definition": {
            "credits_per_usd": credits_per_usd,
            "usd_per_credit": 1 / credits_per_usd,
            "not_microsoft_copilot_credits": True,
        },
        "rate_card": {
            "rate_card_id": rate_card["rate_card_id"],
            "version": rate_card["version"],
            "source_url": rate_card["source_url"],
            "source_revision": rate_card["source_revision"],
            "source_retrieved_at": rate_card["source_retrieved_at"],
        },
        "model": {"model_id": model_id, "display_name": model["display_name"]},
        "pricing_tier": "long_context" if long_context else "default",
        "max_input_tokens_per_request": usage.max_input_tokens_per_request,
        "plan": {
            "plan_id": plan_id,
            "display_name": plan["display_name"],
            "seat_count": seat_count,
            "included_ai_credits": included_credits,
        },
        "token_usage": quantities,
        "lines": lines,
        "model_usage_cost_usd": model_cost_usd,
        "gross_github_ai_credits": gross_credits,
        "included_github_ai_credits": min(gross_credits, included_credits),
        "additional_github_ai_credits": additional_credits,
        "additional_usage_enabled": additional_usage_enabled,
        "additional_usage_cost_usd": additional_cost,
        "fixed_seat_cost_usd": fixed_seat_cost_usd,
        "modeled_total_cost_usd": (
            fixed_seat_cost_usd + additional_cost
            if additional_cost is not None
            else None
        ),
        "unpriced_exclusions": [
            "GitHub Actions minutes and other non-model resource meters"
        ],
        "claim": (
            "Modeled from user-provided token quantities and a versioned GitHub "
            "rate card; not an authoritative billing record."
        ),
    }
