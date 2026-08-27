"""Deterministic and modeled Plan-side Copilot commercial forecasts."""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass
from statistics import median

from .commercial_contracts import (
    BillingDisposition,
    CommercialRateCard,
    EntitlementContext,
    EvidenceStatus,
    PredictedUsageEvent,
)
from .commercial_entitlements import evaluate_entitlement


class CommercialForecastError(ValueError):
    """Raised when decision-relevant commercial evidence is invalid."""


@dataclass(frozen=True)
class PurchasePortfolio:
    version: str
    product: str
    environment: str
    committed_credits: float
    committed_cost_usd: float
    payg_enabled: bool
    payg_rate_usd_per_credit: float
    fixed_seat_cost_usd: float
    scope_evidence_version: str
    billing_period: str = "monthly"

    def __post_init__(self) -> None:
        required = (
            self.version,
            self.product,
            self.environment,
            self.scope_evidence_version,
        )
        if any(not value.strip() for value in required):
            raise CommercialForecastError("purchase portfolio evidence is incomplete")
        if self.billing_period not in {"monthly", "annual"}:
            raise CommercialForecastError(
                "purchase portfolio billing_period must be monthly or annual"
            )
        numeric = (
            self.committed_credits,
            self.committed_cost_usd,
            self.payg_rate_usd_per_credit,
            self.fixed_seat_cost_usd,
        )
        if any(not math.isfinite(value) or value < 0 for value in numeric):
            raise CommercialForecastError(
                "purchase portfolio values must be finite and non-negative"
            )


@dataclass(frozen=True)
class ScenarioPrior:
    prior_id: str
    version: str
    product: str
    segment: str
    credits_per_task_p50: float
    credits_per_task_p95: float
    source_url: str
    source_retrieved_at: str
    evidence_status: EvidenceStatus

    def __post_init__(self) -> None:
        required = (
            self.prior_id,
            self.version,
            self.product,
            self.segment,
            self.source_retrieved_at,
        )
        if any(not value.strip() for value in required):
            raise CommercialForecastError("scenario prior provenance is incomplete")
        if not self.source_url.startswith("https://"):
            raise CommercialForecastError("scenario prior source_url must use https")
        if self.evidence_status is not EvidenceStatus.MODELED:
            raise CommercialForecastError("variable scenario priors must be modeled")
        if (
            not math.isfinite(self.credits_per_task_p50)
            or not math.isfinite(self.credits_per_task_p95)
            or self.credits_per_task_p50 <= 0
            or self.credits_per_task_p95 < self.credits_per_task_p50
        ):
            raise CommercialForecastError("scenario prior percentiles are invalid")


def forecast_copilot_studio(
    rate_card: CommercialRateCard,
    events: list[PredictedUsageEvent],
    context: EntitlementContext,
) -> dict:
    """Calculate additive native-meter demand with per-meter entitlement evidence."""
    lines = []
    native_quantities: dict[str, float] = {}
    blocking_unknowns = []
    total = 0.0

    for event in events:
        meter = rate_card.meter(event.meter_id)
        if event.native_unit is not meter.native_unit:
            raise CommercialForecastError(
                f"event {event.event_id} unit does not match meter {meter.meter_id}"
            )
        decision = evaluate_entitlement(meter, context)
        billed_units = event.quantity / meter.unit_size
        gross_credits = billed_units * meter.credits_per_unit
        billable = decision.disposition is BillingDisposition.BILLABLE
        billed_credits = gross_credits if billable else 0.0
        if decision.disposition is BillingDisposition.UNKNOWN_REQUIRES_POLICY:
            blocking_unknowns.append(
                {
                    "meter_id": meter.meter_id,
                    "reason_code": decision.reason_code,
                    "explanation": decision.explanation,
                }
            )
        else:
            total += billed_credits
        unit = meter.native_unit.value
        native_quantities[unit] = native_quantities.get(unit, 0.0) + event.quantity
        lines.append(
            {
                "event": asdict(event),
                "meter": {
                    **asdict(meter),
                    "native_unit": meter.native_unit.value,
                    "evidence_status": meter.evidence_status.value,
                    "effective_from": meter.effective_from.isoformat(),
                    "effective_to": (
                        meter.effective_to.isoformat() if meter.effective_to else None
                    ),
                    "source_retrieved_at": meter.source_retrieved_at.isoformat(),
                },
                "entitlement": {
                    **asdict(decision),
                    "disposition": decision.disposition.value,
                    "evidence_status": decision.evidence_status.value,
                },
                "billed_units": billed_units,
                "gross_copilot_credits": gross_credits,
                "billed_copilot_credits": billed_credits,
            }
        )

    return {
        "schema_version": "1.0",
        "scope": "commercial_meter",
        "status": "needs_clarification" if blocking_unknowns else "complete",
        "evidence_status": EvidenceStatus.MODELED.value,
        "rate_card": {
            "rate_card_id": rate_card.rate_card_id,
            "version": rate_card.version,
            "source_url": rate_card.source_url,
            "source_revision": rate_card.source_revision,
            "source_retrieved_at": rate_card.source_retrieved_at.isoformat(),
        },
        "entitlement_context": asdict(context),
        "native_quantities": native_quantities,
        "lines": lines,
        "total_copilot_credits": None if blocking_unknowns else total,
        "blocking_unknowns": blocking_unknowns,
    }


def compose_hybrid_forecast(commercial: dict, token_subforecast: dict) -> dict:
    """Compose evidence without converting credits to tokens or hiding exclusions."""
    exclusions = list(token_subforecast.get("exclusions") or [])
    if commercial.get("status") != "complete":
        exclusions.append("commercial_entitlement_unresolved")
    token_cost = token_subforecast.get("cost_usd")
    commercial_cost = commercial.get("purchase_economics", {}).get(
        "amortized_cost_usd"
    )
    token_period = token_subforecast.get("billing_period")
    commercial_period = commercial.get("purchase_economics", {}).get(
        "billing_period"
    )
    if commercial_cost is None:
        exclusions.append("commercial_purchase_economics_unavailable")
    if token_cost is None:
        exclusions.append("model_cost_unavailable")
    if token_period not in {"monthly", "annual"}:
        exclusions.append("model_billing_period_unavailable")
    if commercial_period not in {"monthly", "annual"}:
        exclusions.append("commercial_billing_period_unavailable")
    complete_cost = (
        isinstance(token_cost, (int, float))
        and isinstance(commercial_cost, (int, float))
        and token_period in {"monthly", "annual"}
        and commercial_period in {"monthly", "annual"}
        and not exclusions
    )
    annual_model_cost = (
        token_cost * 12 if token_period == "monthly" else token_cost
    )
    annual_commercial_cost = (
        commercial_cost * 12
        if commercial_period == "monthly"
        else commercial_cost
    )
    return {
        "schema_version": "1.0",
        "scope": "hybrid",
        "commercial": commercial,
        "token_subforecast": token_subforecast,
        "normalized_costs": (
            {
                "billing_period": "annual",
                "model_cost_usd": annual_model_cost,
                "commercial_cost_usd": annual_commercial_cost,
            }
            if complete_cost
            else None
        ),
        "total_usd": (
            annual_model_cost + annual_commercial_cost
            if complete_cost
            else None
        ),
        "billing_period": "annual" if complete_cost else None,
        "unpriced_exclusions": sorted(set(exclusions)),
    }


def allocate_purchase_portfolio(
    required_credits: float,
    portfolio: PurchasePortfolio,
    *,
    product: str,
    environment: str,
) -> dict:
    """Allocate credits while retaining distinct retail, cash, and amortized views."""
    if not math.isfinite(required_credits) or required_credits < 0:
        raise CommercialForecastError(
            "required_credits must be finite and non-negative"
        )
    if product != portfolio.product or environment != portfolio.environment:
        raise CommercialForecastError("purchase portfolio scope does not match forecast")

    drawdown = min(required_credits, portfolio.committed_credits)
    unfunded = max(0.0, required_credits - drawdown)
    unused = max(0.0, portfolio.committed_credits - drawdown)
    incremental = (
        0.0
        if unfunded == 0
        else unfunded * portfolio.payg_rate_usd_per_credit
        if portfolio.payg_enabled
        else None
    )
    status = "complete" if unfunded == 0 or portfolio.payg_enabled else "capacity_risk"
    return {
        "schema_version": "1.0",
        "status": status,
        "portfolio_version": portfolio.version,
        "scope_evidence_version": portfolio.scope_evidence_version,
        "billing_period": portfolio.billing_period,
        "retail_cost_usd": required_credits
        * portfolio.payg_rate_usd_per_credit,
        "invoice_committed_cost_usd": portfolio.committed_cost_usd,
        "incremental_cash_cost_usd": incremental,
        "commitment_drawdown_credits": drawdown,
        "unused_commitment_credits": unused,
        "unfunded_credits": unfunded,
        "fixed_allocation_cost_usd": portfolio.fixed_seat_cost_usd,
        "amortized_cost_usd": (
            portfolio.committed_cost_usd
            + portfolio.fixed_seat_cost_usd
            + incremental
            if incremental is not None
            else None
        ),
    }


def forecast_variable_scenario(
    prior: ScenarioPrior,
    *,
    task_count: int,
    seed: int,
    samples: int = 10_000,
) -> dict:
    """Produce a reproducible modeled distribution from an approved scenario prior."""
    if task_count < 1 or samples < 100:
        raise CommercialForecastError("task_count and samples are below minimum")
    z95 = 1.6448536269514722
    mu = math.log(prior.credits_per_task_p50)
    sigma = math.log(prior.credits_per_task_p95 / prior.credits_per_task_p50) / z95
    rng = random.Random(seed)
    totals = sorted(
        sum(rng.lognormvariate(mu, sigma) for _ in range(task_count))
        for _ in range(samples)
    )

    def percentile(fraction: float) -> float:
        return totals[round((len(totals) - 1) * fraction)]

    return {
        "schema_version": "1.0",
        "scope": "commercial_meter_scenario",
        "status": "complete",
        "product": prior.product,
        "segment": prior.segment,
        "prior": {
            **asdict(prior),
            "evidence_status": prior.evidence_status.value,
        },
        "task_count": task_count,
        "seed": seed,
        "samples": samples,
        "mean_copilot_credits": sum(totals) / len(totals),
        "p50_copilot_credits": median(totals),
        "p95_copilot_credits": percentile(0.95),
        "evidence_status": EvidenceStatus.MODELED.value,
        "calibration_status": "uncalibrated",
        "claim": "Modeled percentiles; not a calibrated tail-risk guarantee.",
    }
