"""Versioned product-to-meter-stack contracts for AI Forecasts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum

from .commercial_contracts import EvidenceStatus


CATALOG_VERSION = "consumption-models.v1"


class ConsumptionFamily(str, Enum):
    SUBSCRIPTION = "subscription"
    INCLUDED = "included"
    NATIVE_CREDIT = "native_credit"
    TOKEN_DERIVED_CREDIT = "token_derived_credit"
    DIRECT_TOKEN = "direct_token"
    RESOURCE = "resource"
    RETRIEVAL = "retrieval"
    TOOL = "tool"
    EVALUATION = "evaluation"
    OBSERVABILITY = "observability"


@dataclass(frozen=True)
class MeterLayer:
    layer_id: str
    family: ConsumptionFamily
    unit: str
    currency: str
    authority: str
    source_url: str
    evidence_status: EvidenceStatus = EvidenceStatus.DOCUMENTED
    conditional: bool = False

    def __post_init__(self) -> None:
        required = (
            self.layer_id,
            self.unit,
            self.currency,
            self.authority,
            self.source_url,
        )
        if any(not value.strip() for value in required):
            raise ValueError("meter layer evidence is incomplete")
        if not self.source_url.startswith("https://"):
            raise ValueError("meter layer source_url must use https")


@dataclass(frozen=True)
class ProductMeterStack:
    route_id: str
    experience: str
    display_name: str
    description: str
    version: str
    layers: tuple[MeterLayer, ...]

    def __post_init__(self) -> None:
        required = (
            self.route_id,
            self.experience,
            self.display_name,
            self.description,
            self.version,
        )
        if any(not value.strip() for value in required):
            raise ValueError("meter stack evidence is incomplete")
        if not self.layers:
            raise ValueError("meter stack must contain at least one layer")
        layer_ids = [layer.layer_id for layer in self.layers]
        if len(layer_ids) != len(set(layer_ids)):
            raise ValueError(f"duplicate meter layer in {self.route_id}")


M365_SOURCE = (
    "https://learn.microsoft.com/en-us/microsoft-365/copilot/"
    "microsoft-365-copilot-licensing"
)
COPILOT_CREDIT_SOURCE = (
    "https://learn.microsoft.com/en-us/microsoft-365/copilot/"
    "usage-based-billing-overview-copilot-credits"
)
COPILOT_STUDIO_SOURCE = (
    "https://learn.microsoft.com/en-us/microsoft-copilot-studio/billing-licensing"
)
GITHUB_SOURCE = (
    "https://docs.github.com/en/copilot/concepts/billing/"
    "usage-based-billing-for-organizations-and-enterprises"
)
FOUNDRY_SOURCE = "https://learn.microsoft.com/en-us/azure/foundry/what-is-foundry"


def _layer(
    layer_id: str,
    family: ConsumptionFamily,
    unit: str,
    currency: str,
    authority: str,
    source_url: str,
    *,
    conditional: bool = False,
) -> MeterLayer:
    return MeterLayer(
        layer_id=layer_id,
        family=family,
        unit=unit,
        currency=currency,
        authority=authority,
        source_url=source_url,
        conditional=conditional,
    )


PRODUCT_METER_STACKS = (
    ProductMeterStack(
        route_id="included",
        experience="employee_productivity",
        display_name="Microsoft Copilot for employees",
        description="Chat and in-app assistance grounded in work data.",
        version=CATALOG_VERSION,
        layers=(
            _layer(
                "microsoft_copilot_seat",
                ConsumptionFamily.SUBSCRIPTION,
                "user_month",
                "USD",
                "Microsoft commercial licensing",
                M365_SOURCE,
            ),
            _layer(
                "qualifying_native_use",
                ConsumptionFamily.INCLUDED,
                "experience_use",
                "included",
                "Microsoft entitlement terms",
                M365_SOURCE,
                conditional=True,
            ),
        ),
    ),
    ProductMeterStack(
        route_id="cowork",
        experience="delegated_agentic_work",
        display_name="Copilot Cowork",
        description="Delegate complex, long-running work to Cowork.",
        version=CATALOG_VERSION,
        layers=(
            _layer(
                "microsoft_copilot_prerequisite",
                ConsumptionFamily.SUBSCRIPTION,
                "licensed_user_month",
                "USD",
                "Microsoft commercial licensing",
                M365_SOURCE,
            ),
            _layer(
                "cowork_tasks",
                ConsumptionFamily.NATIVE_CREDIT,
                "Microsoft Copilot Credit",
                "Microsoft Copilot Credits",
                "Microsoft 365 Cost Management",
                COPILOT_CREDIT_SOURCE,
            ),
        ),
    ),
    ProductMeterStack(
        route_id="agent_builder",
        experience="lightweight_knowledge_agent",
        display_name="Agent Builder",
        description="Create a lightweight Microsoft 365 knowledge agent.",
        version=CATALOG_VERSION,
        layers=(
            _layer(
                "agent_builder_access",
                ConsumptionFamily.SUBSCRIPTION,
                "user_month",
                "USD",
                "Microsoft commercial licensing",
                M365_SOURCE,
                conditional=True,
            ),
            _layer(
                "agent_builder_use",
                ConsumptionFamily.INCLUDED,
                "agent_interaction",
                "included_or_payg",
                "Microsoft entitlement terms",
                COPILOT_STUDIO_SOURCE,
                conditional=True,
            ),
        ),
    ),
    ProductMeterStack(
        route_id="copilot_studio",
        experience="low_code_custom_agent",
        display_name="Copilot Studio",
        description="Build and operate a custom agent or workflow.",
        version=CATALOG_VERSION,
        layers=(
            _layer(
                "copilot_studio_events",
                ConsumptionFamily.NATIVE_CREDIT,
                "Microsoft Copilot Credit",
                "Microsoft Copilot Credits",
                "Power Platform and Microsoft 365 administration",
                COPILOT_STUDIO_SOURCE,
            ),
            _layer(
                "qualifying_employee_entitlement",
                ConsumptionFamily.INCLUDED,
                "qualifying_meter_event",
                "zero_rated",
                "Microsoft entitlement terms",
                COPILOT_STUDIO_SOURCE,
                conditional=True,
            ),
        ),
    ),
    ProductMeterStack(
        route_id="work_iq",
        experience="work_iq_api",
        display_name="Custom agent using Work IQ APIs",
        description="Add Microsoft 365 context and actions to another agent.",
        version=CATALOG_VERSION,
        layers=(
            _layer(
                "work_iq_api",
                ConsumptionFamily.NATIVE_CREDIT,
                "Microsoft Copilot Credit",
                "Microsoft Copilot Credits",
                "Microsoft 365 Cost Management",
                COPILOT_CREDIT_SOURCE,
            ),
        ),
    ),
    ProductMeterStack(
        route_id="foundry",
        experience="pro_code_custom_ai",
        display_name="Microsoft Foundry",
        description="Build a pro-code custom AI application or agent.",
        version=CATALOG_VERSION,
        layers=(
            _layer(
                "foundry_model",
                ConsumptionFamily.DIRECT_TOKEN,
                "model_token",
                "USD",
                "Azure model deployment pricing",
                FOUNDRY_SOURCE,
            ),
            _layer(
                "foundry_resources",
                ConsumptionFamily.RESOURCE,
                "tool_or_infrastructure_unit",
                "USD",
                "Azure resource meter",
                FOUNDRY_SOURCE,
                conditional=True,
            ),
        ),
    ),
    ProductMeterStack(
        route_id="github_copilot",
        experience="developer_productivity",
        display_name="GitHub Copilot",
        description="Forecast token-derived GitHub AI Credits for developer use.",
        version=CATALOG_VERSION,
        layers=(
            _layer(
                "github_copilot_seat",
                ConsumptionFamily.SUBSCRIPTION,
                "user_month",
                "USD",
                "GitHub billing",
                GITHUB_SOURCE,
            ),
            _layer(
                "github_ai_credits",
                ConsumptionFamily.TOKEN_DERIVED_CREDIT,
                "GitHub AI Credit",
                "GitHub AI Credits",
                "GitHub billing",
                GITHUB_SOURCE,
            ),
            _layer(
                "github_actions",
                ConsumptionFamily.RESOURCE,
                "GitHub Actions minute",
                "USD",
                "GitHub Actions billing",
                GITHUB_SOURCE,
                conditional=True,
            ),
        ),
    ),
    ProductMeterStack(
        route_id="copilot_studio_byom",
        experience="low_code_agent_with_foundry_model",
        display_name="Copilot Studio with a Foundry model",
        description="Compose Copilot Studio credits with a separate model forecast.",
        version=CATALOG_VERSION,
        layers=(
            _layer(
                "copilot_studio_events",
                ConsumptionFamily.NATIVE_CREDIT,
                "Microsoft Copilot Credit",
                "Microsoft Copilot Credits",
                "Power Platform administration",
                COPILOT_STUDIO_SOURCE,
            ),
            _layer(
                "foundry_model",
                ConsumptionFamily.DIRECT_TOKEN,
                "model_token",
                "USD",
                "Azure model deployment pricing",
                FOUNDRY_SOURCE,
            ),
        ),
    ),
    ProductMeterStack(
        route_id="foundry_work_iq",
        experience="foundry_agent_with_work_iq",
        display_name="Microsoft Foundry with Work IQ APIs",
        description="Compose model usage with separate Work IQ API credits.",
        version=CATALOG_VERSION,
        layers=(
            _layer(
                "foundry_model",
                ConsumptionFamily.DIRECT_TOKEN,
                "model_token",
                "USD",
                "Azure model deployment pricing",
                FOUNDRY_SOURCE,
            ),
            _layer(
                "work_iq_api",
                ConsumptionFamily.NATIVE_CREDIT,
                "Microsoft Copilot Credit",
                "Microsoft Copilot Credits",
                "Microsoft 365 Cost Management",
                COPILOT_CREDIT_SOURCE,
            ),
        ),
    ),
)

_STACKS_BY_ROUTE = {stack.route_id: stack for stack in PRODUCT_METER_STACKS}


def meter_stack_for(route_id: str) -> dict:
    """Return immutable, JSON-ready evidence for one supported delivery route."""
    try:
        stack = _STACKS_BY_ROUTE[route_id]
    except KeyError as exc:
        raise ValueError(f"unsupported consumption route: {route_id}") from exc
    payload = asdict(stack)
    payload["layers"] = [
        {
            **layer,
            "family": layer["family"].value,
            "evidence_status": layer["evidence_status"].value,
        }
        for layer in payload["layers"]
    ]
    return payload


def consumption_catalog() -> dict:
    return {
        "schema_version": "1.0",
        "catalog_version": CATALOG_VERSION,
        "evidence_status": EvidenceStatus.DOCUMENTED.value,
        "experiences": [meter_stack_for(stack.route_id) for stack in PRODUCT_METER_STACKS],
    }
