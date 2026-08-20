"""Route-first construction of Plan-side commercial forecast evidence."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from .commercial_contracts import (
    BillingDisposition,
    EntitlementContext,
    EvidenceStatus,
    PredictedUsageEvent,
)
from .commercial_forecasting import (
    CommercialForecastError,
    PurchasePortfolio,
    ScenarioPrior,
    allocate_purchase_portfolio,
    compose_hybrid_forecast,
    forecast_copilot_studio,
    forecast_variable_scenario,
)
from .commercial_rate_cards import load_copilot_studio_rate_card
from .consumption_models import meter_stack_for
from .github_forecasting import (
    GitHubTokenUsage,
    forecast_github_copilot,
    load_github_rate_card,
)

COMMERCIAL_ROUTES = {
    "included",
    "agent_builder",
    "copilot_studio",
    "cowork",
    "work_iq",
    "github_copilot",
    "copilot_studio_byom",
    "foundry_work_iq",
}
MODEL_ROUTES = {"foundry", "copilot_studio_byom", "foundry_work_iq"}
ROUTE_EVIDENCE_VERSION = "commercial-route.v2"


class CommercialPlanClarification(CommercialForecastError):
    """Raised when material evidence must be supplied before a receipt exists."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def _required_object(value: object, field: str) -> dict:
    if not isinstance(value, dict):
        raise CommercialPlanClarification(field, f"{field} must be an object")
    return value


def _portfolio(raw: object) -> PurchasePortfolio | None:
    if raw is None:
        return None
    values = _required_object(raw, "purchase_portfolio")
    try:
        return PurchasePortfolio(**values)
    except TypeError as exc:
        raise CommercialPlanClarification(
            "purchase_portfolio", f"Purchase portfolio is incomplete: {exc}"
        ) from exc


def _allocate(
    credits: float | None,
    portfolio: PurchasePortfolio | None,
    *,
    product: str,
    environment: str,
) -> dict | None:
    if portfolio is None or credits is None:
        return None
    try:
        return allocate_purchase_portfolio(
            credits, portfolio, product=product, environment=environment
        )
    except CommercialForecastError as exc:
        raise CommercialPlanClarification("purchase_portfolio", str(exc)) from exc


def _copilot_studio_forecast(
    commercial_input: dict, *, route: str
) -> tuple[dict, dict | None]:
    try:
        forecast_date = date.fromisoformat(str(commercial_input["as_of"]))
        context_values = _required_object(
            commercial_input.get("entitlement"), "commercial_entitlement"
        )
        context_values = {
            **context_values,
            "product_boundary": (
                "copilot_studio_byom"
                if route == "copilot_studio_byom"
                else context_values.get("product_boundary")
            ),
        }
        context = EntitlementContext(**context_values)
        rate_card = load_copilot_studio_rate_card(as_of=forecast_date)
        raw_events = commercial_input.get("events")
        if not isinstance(raw_events, list) or not raw_events:
            raise CommercialPlanClarification(
                "commercial_events", "At least one native usage event is required."
            )
        events = []
        for index, raw in enumerate(raw_events):
            values = _required_object(raw, "commercial_events")
            meter = rate_card.meter(str(values.get("meter_id", "")))
            events.append(
                PredictedUsageEvent(
                    event_id=str(values.get("event_id") or f"event-{index + 1}-{uuid4()}"),
                    feature=meter.feature,
                    native_unit=meter.native_unit,
                    quantity=float(values["quantity"]),
                    meter_id=meter.meter_id,
                    disposition=BillingDisposition.UNKNOWN_REQUIRES_POLICY,
                    evidence_status=EvidenceStatus.MODELED,
                    quantity_source=str(
                        values.get("quantity_source") or "user_confirmed.v1"
                    ),
                )
            )
    except KeyError as exc:
        raise CommercialPlanClarification(
            "commercial", f"Missing commercial input: {exc.args[0]}"
        ) from exc
    except TypeError as exc:
        raise CommercialPlanClarification(
            "commercial_entitlement", f"Entitlement input is incomplete: {exc}"
        ) from exc
    except ValueError as exc:
        raise CommercialPlanClarification("commercial", str(exc)) from exc

    forecast = forecast_copilot_studio(rate_card, events, context)
    if forecast["status"] != "complete":
        reasons = "; ".join(
            item["explanation"] for item in forecast["blocking_unknowns"]
        )
        raise CommercialPlanClarification("commercial_entitlement", reasons)
    portfolio = _portfolio(commercial_input.get("purchase_portfolio"))
    environment = str(commercial_input.get("environment") or "default")
    purchase = _allocate(
        forecast["total_copilot_credits"],
        portfolio,
        product="Microsoft Copilot Studio",
        environment=environment,
    )
    if purchase is not None:
        forecast = {**forecast, "purchase_economics": purchase}
    return forecast, purchase


def _scenario_forecast(commercial_input: dict, *, route: str) -> tuple[dict, dict | None]:
    prior_values = _required_object(
        commercial_input.get("scenario_prior"), "scenario_prior"
    )
    try:
        prior_values = {
            **prior_values,
            "evidence_status": EvidenceStatus(prior_values["evidence_status"]),
        }
        prior = ScenarioPrior(**prior_values)
        forecast = forecast_variable_scenario(
            prior,
            task_count=int(commercial_input["task_count"]),
            seed=int(commercial_input["seed"]),
            samples=int(commercial_input.get("samples", 10_000)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CommercialPlanClarification("scenario_prior", str(exc)) from exc
    portfolio = _portfolio(commercial_input.get("purchase_portfolio"))
    environment = str(commercial_input.get("environment") or "default")
    purchase = _allocate(
        forecast["mean_copilot_credits"],
        portfolio,
        product=prior.product,
        environment=environment,
    )
    if purchase is not None:
        forecast = {**forecast, "purchase_economics": purchase}
    return forecast, purchase


def _github_forecast(commercial_input: dict) -> tuple[dict, None]:
    try:
        forecast_date = date.fromisoformat(str(commercial_input["as_of"]))
        usage_values = _required_object(
            commercial_input.get("token_usage"), "github_token_usage"
        )
        forecast = forecast_github_copilot(
            load_github_rate_card(as_of=forecast_date),
            model_id=str(commercial_input["model_id"]),
            plan_id=str(commercial_input["plan_id"]),
            seat_count=int(commercial_input["seat_count"]),
            usage=GitHubTokenUsage(**usage_values),
            fixed_seat_cost_usd=float(commercial_input["fixed_seat_cost_usd"]),
            additional_usage_enabled=bool(
                commercial_input.get("additional_usage_enabled", False)
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CommercialPlanClarification("github_copilot", str(exc)) from exc
    return forecast, None


def build_commercial_result(
    description: str,
    parameters: dict,
    *,
    token_result: dict | None = None,
) -> dict:
    """Build complete route evidence; callers persist only successful results."""
    route = str(parameters.get("route") or "foundry")
    if route not in COMMERCIAL_ROUTES:
        raise CommercialPlanClarification("route", f"Unsupported route: {route}")
    commercial_input = _required_object(parameters.get("commercial"), "commercial")

    if route in {"copilot_studio", "copilot_studio_byom"}:
        commercial, purchase = _copilot_studio_forecast(
            commercial_input, route=route
        )
    elif route in {"cowork", "work_iq", "foundry_work_iq"}:
        commercial, purchase = _scenario_forecast(commercial_input, route=route)
    elif route == "github_copilot":
        commercial, purchase = _github_forecast(commercial_input)
    else:
        portfolio = _portfolio(commercial_input.get("purchase_portfolio"))
        if portfolio is None:
            raise CommercialPlanClarification(
                "purchase_portfolio",
                "Included routes require versioned fixed-seat allocation evidence.",
            )
        commercial = {
            "schema_version": "1.0",
            "scope": "fixed_subscription",
            "status": "complete",
            "evidence_status": EvidenceStatus.MODELED.value,
            "total_copilot_credits": 0.0,
        }
        purchase = _allocate(
            0,
            portfolio,
            product=portfolio.product,
            environment=portfolio.environment,
        )
        commercial["purchase_economics"] = purchase

    if commercial.get("status") == "capacity_risk" or (
        purchase is not None and purchase.get("status") == "capacity_risk"
    ):
        raise CommercialPlanClarification(
            "commercial_capacity",
            "Forecast demand exceeds configured capacity and no overage source is enabled.",
        )

    token_subforecast = None
    hybrid = None
    prediction = {}
    if route in MODEL_ROUTES:
        if token_result is None:
            raise CommercialPlanClarification(
                "model", "This hybrid route requires a model subforecast."
            )
        prediction = token_result["prediction"]
        token_subforecast = {
            "schema_version": "1.0",
            "scope": "model_invocation",
            "prediction_id": prediction.get("prediction_id"),
            "provider": prediction.get("provider"),
            "model": prediction.get("model"),
            "pricing_version": prediction.get("pricing_version"),
            "prediction": prediction,
            "cost_usd": (prediction.get("annual_cost") or {}).get("mean"),
            "billing_period": "annual",
            "exclusions": list(
                (token_result.get("intake", {}).get("analysis") or {}).get(
                    "exclusions", []
                )
            ),
        }
        hybrid = compose_hybrid_forecast(commercial, token_subforecast)

    return {
        "status": "complete",
        "description": description,
        "intake": parameters,
        "route": {
            "route_id": route,
            "scope": "hybrid" if route in MODEL_ROUTES else "commercial_meter",
            "evidence_version": ROUTE_EVIDENCE_VERSION,
        },
        "meter_stack": meter_stack_for(route),
        "commercial": commercial,
        "purchase": purchase,
        "token_subforecast": token_subforecast,
        "hybrid": hybrid,
        "prediction": prediction,
        "infrastructure": (
            token_result["infrastructure"]
            if token_result is not None
            else {
                "status": "not_estimated",
                "message": "Infrastructure remains a separate ledger.",
            }
        ),
    }


def attach_foundry_meter_stack(token_result: dict) -> dict:
    """Add product-meter evidence without changing predictor calculations."""
    return {
        **token_result,
        "route": {
            "route_id": "foundry",
            "scope": "model_invocation",
            "evidence_version": ROUTE_EVIDENCE_VERSION,
        },
        "meter_stack": meter_stack_for("foundry"),
        "commercial": None,
        "purchase": None,
        "token_subforecast": None,
        "hybrid": None,
    }
