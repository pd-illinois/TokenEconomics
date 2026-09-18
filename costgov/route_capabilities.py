"""Versioned control-authority profiles for supported delivery routes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date
from enum import Enum
from typing import Any

from .consumption_models import PRODUCT_METER_STACKS, meter_stack_for

ROUTE_CAPABILITY_SCHEMA_VERSION = "route-capability-profile.v1"
ROUTE_CAPABILITY_RELEASE = "2026-09-02.1"


class ForecastMethod(str, Enum):
    DETERMINISTIC_ALLOCATION = "deterministic_allocation"
    SCENARIO_DISTRIBUTION = "scenario_distribution"
    TRAJECTORY_DISTRIBUTION = "trajectory_distribution"
    COMPOSITE_DISTRIBUTION = "composite_distribution"


class EnforcementScope(str, Enum):
    RUNTIME_ENFORCED = "runtime_enforced"
    CONTROL_PLANE_ENFORCED = "control_plane_enforced"
    PRODUCT_ADMIN_ENFORCED = "product_admin_enforced"
    DESIGN_TIME_ENFORCED = "design_time_enforced"
    EXTERNAL_REQUIREMENT = "external_requirement"
    ADVISORY = "advisory"
    CORRELATION_ONLY = "correlation_only"


_CONTROL_DECLARATIONS = {
    "entitlement": {
        ("commercial_evidence_authority", "entitlement_validation",
         EnforcementScope.CONTROL_PLANE_ENFORCED),
    },
    "seat_allocation": {
        ("commercial_evidence_authority", "seat_allocation",
         EnforcementScope.ADVISORY),
    },
    "product_access": {
        ("microsoft_365_admin", "product_access",
         EnforcementScope.PRODUCT_ADMIN_ENFORCED),
    },
    "spending_limit": {
        ("microsoft_365_cost_management", "spending_limit",
         EnforcementScope.PRODUCT_ADMIN_ENFORCED),
    },
    "native_meter_cap": {
        ("product_meter_authority", "native_meter_capacity",
         EnforcementScope.EXTERNAL_REQUIREMENT),
    },
    "overage_action": {
        ("product_meter_authority", "overage_control",
         EnforcementScope.EXTERNAL_REQUIREMENT),
    },
    "feature_configuration": {
        ("workload_owner", "feature_configuration",
         EnforcementScope.DESIGN_TIME_ENFORCED),
    },
    "api_call_limit": {
        ("workload_adapter", "api_call_limit",
         EnforcementScope.RUNTIME_ENFORCED),
    },
    "model_selection": {
        ("azure_tokengov", "model_routing",
         EnforcementScope.RUNTIME_ENFORCED),
        ("github_copilot_admin", "model_selection",
         EnforcementScope.DESIGN_TIME_ENFORCED),
    },
    "routing": {
        ("azure_tokengov", "model_routing",
         EnforcementScope.RUNTIME_ENFORCED),
    },
    "cache": {
        ("azure_tokengov", "semantic_cache",
         EnforcementScope.RUNTIME_ENFORCED),
    },
    "context": {
        ("azure_tokengov", "context_management",
         EnforcementScope.RUNTIME_ENFORCED),
    },
    "retry": {
        ("azure_tokengov", "retry_control",
         EnforcementScope.RUNTIME_ENFORCED),
    },
    "iteration": {
        ("azure_tokengov", "iteration_control",
         EnforcementScope.RUNTIME_ENFORCED),
    },
    "monetary_budget": {
        ("azure_tokengov", "budget_enforcement",
         EnforcementScope.RUNTIME_ENFORCED),
    },
    "github_budget": {
        ("github_billing", "usage_budget",
         EnforcementScope.PRODUCT_ADMIN_ENFORCED),
    },
}


def _required(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value


def _hash(value: object, field: str) -> str:
    text = _required(value, field)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ValueError(f"{field} must be a lowercase SHA-256 hash")
    return text


def _canonical(value: object) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _meter_stack_hash(route_id: str) -> str:
    return hashlib.sha256(_canonical(meter_stack_for(route_id)).encode()).hexdigest()


@dataclass(frozen=True)
class RouteControl:
    control_id: str
    control_kind: str
    authority: str
    capability: str
    enforcement_scope: EnforcementScope
    target_leg: str

    def __post_init__(self) -> None:
        for field in (
            "control_id",
            "control_kind",
            "authority",
            "capability",
            "target_leg",
        ):
            _required(getattr(self, field), field)
        if not isinstance(self.enforcement_scope, EnforcementScope):
            raise ValueError("control enforcement_scope is invalid")
        declaration = (
            self.authority,
            self.capability,
            self.enforcement_scope,
        )
        if declaration not in _CONTROL_DECLARATIONS.get(self.control_kind, set()):
            raise ValueError(
                f"control {self.control_kind} does not match a supported "
                "authority, capability, and enforcement scope"
            )

    def to_dict(self) -> dict[str, str]:
        result = asdict(self)
        result["enforcement_scope"] = self.enforcement_scope.value
        return result


@dataclass(frozen=True)
class ActualUsageSource:
    source_id: str
    authority: str
    location: str
    granularity: str

    def __post_init__(self) -> None:
        for field in ("source_id", "authority", "location", "granularity"):
            _required(getattr(self, field), field)
        if not self.location.startswith("https://"):
            raise ValueError("actual usage source location must use https")


@dataclass(frozen=True)
class RouteCapabilityProfile:
    schema_version: str
    route_id: str
    version: str
    meter_stack_id: str
    meter_stack_version: str
    meter_stack_content_hash: str
    forecast_method: ForecastMethod
    acceptance_contract: str
    controls: tuple[RouteControl, ...]
    actual_usage_sources: tuple[ActualUsageSource, ...]
    response_actions: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    coverage_gaps: tuple[str, ...]
    effective_from: date
    review_after: date
    source_revisions: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != ROUTE_CAPABILITY_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {ROUTE_CAPABILITY_SCHEMA_VERSION}"
            )
        for field in (
            "route_id",
            "version",
            "meter_stack_id",
            "meter_stack_version",
            "acceptance_contract",
        ):
            _required(getattr(self, field), field)
        _hash(self.meter_stack_content_hash, "meter_stack_content_hash")
        if not isinstance(self.forecast_method, ForecastMethod):
            raise ValueError("forecast_method is invalid")
        if self.review_after < self.effective_from:
            raise ValueError("review_after cannot precede effective_from")
        if not self.controls:
            raise ValueError("route capability profile requires controls")
        control_ids = [item.control_id for item in self.controls]
        if len(control_ids) != len(set(control_ids)):
            raise ValueError("route capability control IDs must be unique")
        for field, values in (
            ("actual_usage_sources", self.actual_usage_sources),
            ("response_actions", self.response_actions),
            ("evidence_requirements", self.evidence_requirements),
            ("source_revisions", self.source_revisions),
        ):
            if not values:
                raise ValueError(f"{field} must not be empty")
        for value in (*self.response_actions, *self.evidence_requirements):
            _required(value, "route capability value")
        if any(not source.startswith("https://") for source in self.source_revisions):
            raise ValueError("source revisions must use https")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "route_id": self.route_id,
            "version": self.version,
            "meter_stack_id": self.meter_stack_id,
            "meter_stack_version": self.meter_stack_version,
            "meter_stack_content_hash": self.meter_stack_content_hash,
            "forecast_method": self.forecast_method.value,
            "acceptance_contract": self.acceptance_contract,
            "controls": [item.to_dict() for item in self.controls],
            "actual_usage_sources": [
                asdict(item) for item in self.actual_usage_sources
            ],
            "response_actions": list(self.response_actions),
            "evidence_requirements": list(self.evidence_requirements),
            "coverage_gaps": list(self.coverage_gaps),
            "effective_from": self.effective_from.isoformat(),
            "review_after": self.review_after.isoformat(),
            "source_revisions": list(self.source_revisions),
        }

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict()).encode()).hexdigest()


M365_LICENSING = (
    "https://learn.microsoft.com/en-us/microsoft-365-copilot/"
    "microsoft-365-copilot-licensing"
)
COPILOT_CREDITS = (
    "https://learn.microsoft.com/en-us/microsoft-365/copilot/"
    "usage-based-billing-overview-copilot-credits"
)
COPILOT_STUDIO = (
    "https://learn.microsoft.com/en-us/microsoft-copilot-studio/"
    "requirements-messages-management"
)
AGENT_BUILDER = (
    "https://learn.microsoft.com/en-us/microsoft-365/copilot/"
    "extensibility/agent-builder"
)
WORK_IQ = (
    "https://learn.microsoft.com/en-us/microsoft-365/copilot/"
    "extensibility/work-iq/api-overview"
)
FOUNDRY = (
    "https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-router"
)
GITHUB_BILLING = (
    "https://docs.github.com/en/copilot/concepts/billing/"
    "usage-based-billing-for-organizations-and-enterprises"
)
GITHUB_BUDGETS = (
    "https://docs.github.com/en/copilot/concepts/billing/"
    "budgets-for-usage-based-billing"
)


def _control(
    control_id: str,
    kind: str,
    authority: str,
    capability: str,
    scope: EnforcementScope,
    target_leg: str,
) -> RouteControl:
    return RouteControl(
        control_id,
        kind,
        authority,
        capability,
        scope,
        target_leg,
    )


def _usage(
    source_id: str,
    authority: str,
    location: str,
    granularity: str,
) -> ActualUsageSource:
    return ActualUsageSource(source_id, authority, location, granularity)


def _profile(
    route_id: str,
    forecast_method: ForecastMethod,
    controls: tuple[RouteControl, ...],
    usage_sources: tuple[ActualUsageSource, ...],
    response_actions: tuple[str, ...],
    evidence_requirements: tuple[str, ...],
    coverage_gaps: tuple[str, ...],
    source_revisions: tuple[str, ...],
) -> RouteCapabilityProfile:
    stack = meter_stack_for(route_id)
    return RouteCapabilityProfile(
        schema_version=ROUTE_CAPABILITY_SCHEMA_VERSION,
        route_id=route_id,
        version=ROUTE_CAPABILITY_RELEASE,
        meter_stack_id=route_id,
        meter_stack_version=stack["version"],
        meter_stack_content_hash=_meter_stack_hash(route_id),
        forecast_method=forecast_method,
        acceptance_contract="acceptance-rule.v1",
        controls=controls,
        actual_usage_sources=usage_sources,
        response_actions=response_actions,
        evidence_requirements=evidence_requirements,
        coverage_gaps=coverage_gaps,
        effective_from=date(2026, 9, 2),
        review_after=date(2026, 12, 1),
        source_revisions=source_revisions,
    )


ENTITLEMENT = _control(
    "entitlement",
    "entitlement",
    "commercial_evidence_authority",
    "entitlement_validation",
    EnforcementScope.CONTROL_PLANE_ENFORCED,
    "commercial",
)
SEAT_ALLOCATION = _control(
    "seat-allocation",
    "seat_allocation",
    "commercial_evidence_authority",
    "seat_allocation",
    EnforcementScope.ADVISORY,
    "subscription",
)
PRODUCT_ACCESS = _control(
    "product-access",
    "product_access",
    "microsoft_365_admin",
    "product_access",
    EnforcementScope.PRODUCT_ADMIN_ENFORCED,
    "commercial",
)
SPENDING_LIMIT = _control(
    "spending-limit",
    "spending_limit",
    "microsoft_365_cost_management",
    "spending_limit",
    EnforcementScope.PRODUCT_ADMIN_ENFORCED,
    "commercial",
)
NATIVE_CAPACITY = _control(
    "native-capacity",
    "native_meter_cap",
    "product_meter_authority",
    "native_meter_capacity",
    EnforcementScope.EXTERNAL_REQUIREMENT,
    "commercial",
)
OVERAGE = _control(
    "overage",
    "overage_action",
    "product_meter_authority",
    "overage_control",
    EnforcementScope.EXTERNAL_REQUIREMENT,
    "commercial",
)
FEATURE_CONFIGURATION = _control(
    "feature-configuration",
    "feature_configuration",
    "workload_owner",
    "feature_configuration",
    EnforcementScope.DESIGN_TIME_ENFORCED,
    "commercial",
)
WORK_IQ_CALL_LIMIT = _control(
    "work-iq-call-limit",
    "api_call_limit",
    "workload_adapter",
    "api_call_limit",
    EnforcementScope.RUNTIME_ENFORCED,
    "work_iq",
)
FOUNDRY_CONTROLS = tuple(
    _control(
        f"foundry-{kind}",
        kind,
        "azure_tokengov",
        capability,
        EnforcementScope.RUNTIME_ENFORCED,
        "foundry",
    )
    for kind, capability in (
        ("model_selection", "model_routing"),
        ("routing", "model_routing"),
        ("cache", "semantic_cache"),
        ("context", "context_management"),
        ("retry", "retry_control"),
        ("iteration", "iteration_control"),
        ("monetary_budget", "budget_enforcement"),
    )
)
GITHUB_MODEL = _control(
    "github-model",
    "model_selection",
    "github_copilot_admin",
    "model_selection",
    EnforcementScope.DESIGN_TIME_ENFORCED,
    "github_copilot",
)
GITHUB_BUDGET = _control(
    "github-budget",
    "github_budget",
    "github_billing",
    "usage_budget",
    EnforcementScope.PRODUCT_ADMIN_ENFORCED,
    "github_copilot",
)

M365_USAGE = _usage(
    "m365-usage",
    "Microsoft 365 administration",
    M365_LICENSING,
    "user_activity",
)
COPILOT_USAGE = _usage(
    "copilot-credit-usage",
    "Microsoft 365 Cost Management",
    COPILOT_CREDITS,
    "credit_consumption",
)
POWER_PLATFORM_USAGE = _usage(
    "power-platform-usage",
    "Power Platform administration",
    COPILOT_STUDIO,
    "environment_agent_feature",
)
AZURE_USAGE = _usage(
    "azure-usage",
    "Azure billing and telemetry",
    FOUNDRY,
    "model_and_resource_meter",
)
GITHUB_USAGE = _usage(
    "github-ai-usage",
    "GitHub billing",
    GITHUB_BILLING,
    "user_model_credit",
)

ROUTE_CAPABILITY_PROFILES = (
    _profile(
        "included",
        ForecastMethod.DETERMINISTIC_ALLOCATION,
        (ENTITLEMENT, SEAT_ALLOCATION),
        (M365_USAGE,),
        ("reassign_seats", "change_route"),
        ("license_assignment", "included_use_eligibility", "acceptance_rule"),
        ("no_task_level_token_or_cost_meter",),
        (M365_LICENSING,),
    ),
    _profile(
        "cowork",
        ForecastMethod.SCENARIO_DISTRIBUTION,
        (ENTITLEMENT, PRODUCT_ACCESS, SPENDING_LIMIT, NATIVE_CAPACITY, OVERAGE),
        (COPILOT_USAGE,),
        ("block_future_admission", "require_approval", "change_task_class"),
        (
            "license_prerequisite",
            "approved_scenario_prior",
            "spending_policy",
            "capacity_and_overage",
            "acceptance_rule",
        ),
        ("no_public_deterministic_per_task_rate", "no_internal_routing_control"),
        (M365_LICENSING, COPILOT_CREDITS),
    ),
    _profile(
        "agent_builder",
        ForecastMethod.DETERMINISTIC_ALLOCATION,
        (ENTITLEMENT, SEAT_ALLOCATION),
        (M365_USAGE,),
        ("retain_included_route", "reassign_seats", "move_to_copilot_studio"),
        ("license_assignment", "internal_product_boundary", "acceptance_rule"),
        ("no_discrete_agent_builder_usage_meter",),
        (M365_LICENSING, AGENT_BUILDER),
    ),
    _profile(
        "copilot_studio",
        ForecastMethod.SCENARIO_DISTRIBUTION,
        (ENTITLEMENT, FEATURE_CONFIGURATION, NATIVE_CAPACITY, OVERAGE),
        (POWER_PLATFORM_USAGE, COPILOT_USAGE),
        ("change_feature_mix", "block_new_invocations", "enable_approved_overage"),
        (
            "rate_card",
            "entitlement",
            "environment_capacity",
            "overage_action",
            "acceptance_rule",
        ),
        ("no_standard_harness_token_routing_control",),
        (M365_LICENSING, COPILOT_CREDITS, COPILOT_STUDIO),
    ),
    _profile(
        "work_iq",
        ForecastMethod.SCENARIO_DISTRIBUTION,
        (ENTITLEMENT, WORK_IQ_CALL_LIMIT, NATIVE_CAPACITY, OVERAGE),
        (COPILOT_USAGE,),
        ("reduce_api_calls", "change_api_family", "block_future_admission"),
        (
            "api_family",
            "rate_or_scenario_prior",
            "capacity_and_overage",
            "acceptance_rule",
        ),
        ("variable_chat_context_rate", "no_work_iq_specific_public_estimator"),
        (COPILOT_CREDITS, WORK_IQ),
    ),
    _profile(
        "foundry",
        ForecastMethod.TRAJECTORY_DISTRIBUTION,
        FOUNDRY_CONTROLS,
        (AZURE_USAGE,),
        ("block_candidate", "stage_reviewed_reversion", "recover_after_review"),
        (
            "released_model",
            "verified_pricing",
            "infrastructure_coverage",
            "acceptance_rule",
        ),
        ("resource_cost_may_require_allocation",),
        (FOUNDRY,),
    ),
    _profile(
        "github_copilot",
        ForecastMethod.SCENARIO_DISTRIBUTION,
        (ENTITLEMENT, SEAT_ALLOCATION, GITHUB_MODEL, GITHUB_BUDGET),
        (GITHUB_USAGE,),
        ("adjust_model_policy", "adjust_budget", "block_additional_usage"),
        (
            "github_plan",
            "seat_assignment",
            "model_pricing",
            "budget_hierarchy",
            "acceptance_rule",
        ),
        ("no_owner_semantic_cache_control", "actions_cost_may_be_unpriced"),
        (GITHUB_BILLING, GITHUB_BUDGETS),
    ),
    _profile(
        "copilot_studio_byom",
        ForecastMethod.COMPOSITE_DISTRIBUTION,
        (
            ENTITLEMENT,
            FEATURE_CONFIGURATION,
            NATIVE_CAPACITY,
            OVERAGE,
            *FOUNDRY_CONTROLS,
        ),
        (POWER_PLATFORM_USAGE, COPILOT_USAGE, AZURE_USAGE),
        ("respond_to_commercial_leg", "respond_to_foundry_leg", "block_task"),
        (
            "copilot_rate_card",
            "foundry_model_pricing",
            "capacity_and_overage",
            "hybrid_dependence_assumption",
            "acceptance_rule",
        ),
        ("no_unified_cross_layer_evaluator", "separate_billing_ledgers"),
        (COPILOT_CREDITS, COPILOT_STUDIO, FOUNDRY),
    ),
    _profile(
        "foundry_work_iq",
        ForecastMethod.COMPOSITE_DISTRIBUTION,
        (*FOUNDRY_CONTROLS, WORK_IQ_CALL_LIMIT, NATIVE_CAPACITY, OVERAGE),
        (AZURE_USAGE, COPILOT_USAGE),
        ("respond_to_foundry_leg", "reduce_work_iq_calls", "block_task"),
        (
            "foundry_model_pricing",
            "work_iq_rate_or_prior",
            "capacity_and_overage",
            "hybrid_dependence_assumption",
            "acceptance_rule",
        ),
        ("work_iq_task_cost_join_may_be_unavailable", "separate_billing_ledgers"),
        (COPILOT_CREDITS, WORK_IQ, FOUNDRY),
    ),
)

_PROFILES_BY_ROUTE = {
    profile.route_id: profile for profile in ROUTE_CAPABILITY_PROFILES
}

if set(_PROFILES_BY_ROUTE) != {
    stack.route_id for stack in PRODUCT_METER_STACKS
}:
    raise RuntimeError("route capability profiles do not match the meter stack catalog")


def route_capability_profile_for(
    route_id: str,
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Resolve one current route profile and fail closed on stale evidence."""
    try:
        profile = _PROFILES_BY_ROUTE[route_id]
    except KeyError as exc:
        raise ValueError(f"unsupported route capability profile: {route_id}") from exc
    effective_date = as_of or date.today()
    if effective_date < profile.effective_from:
        raise ValueError("route capability profile is not yet effective")
    if effective_date > profile.review_after:
        raise ValueError("route capability profile requires review")
    return {**profile.to_dict(), "content_hash": profile.content_hash}


def route_capability_catalog(*, as_of: date | None = None) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "release": ROUTE_CAPABILITY_RELEASE,
        "profiles": [
            route_capability_profile_for(profile.route_id, as_of=as_of)
            for profile in ROUTE_CAPABILITY_PROFILES
        ],
    }
