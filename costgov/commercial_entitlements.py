"""Versioned, fail-closed Copilot entitlement decisions."""

from __future__ import annotations

from .commercial_contracts import (
    BillingDisposition,
    CommercialMeter,
    EntitlementContext,
    EntitlementDecision,
    EvidenceStatus,
)

RULE_SET_VERSION = "copilot-entitlements.2026-08-20.v1"
_M365_COPILOT_SKUS = {"microsoft_365_copilot", "m365_copilot"}
_QUALIFYING_CHANNELS = {
    "microsoft_365_copilot",
    "copilot_chat",
    "teams",
    "sharepoint",
}
_TEST_EXEMPT_METERS = {
    "classic_answer",
    "generative_answer",
    "agent_action",
    "tenant_graph_grounding",
    "agent_flow_actions",
}
_AI_TOOL_METERS = {"basic_ai_tool", "standard_ai_tool", "premium_ai_tool"}


def _decision(
    meter: CommercialMeter,
    disposition: BillingDisposition,
    reason_code: str,
    explanation: str,
    *,
    evidence_status: EvidenceStatus = EvidenceStatus.DOCUMENTED,
) -> EntitlementDecision:
    return EntitlementDecision(
        meter_id=meter.meter_id,
        disposition=disposition,
        reason_code=reason_code,
        explanation=explanation,
        rule_set_version=RULE_SET_VERSION,
        evidence_status=evidence_status,
    )


def evaluate_entitlement(
    meter: CommercialMeter, context: EntitlementContext
) -> EntitlementDecision:
    """Resolve one meter independently so exemptions cannot leak across a task."""
    if not context.evidence_version.strip():
        return _decision(
            meter,
            BillingDisposition.UNKNOWN_REQUIRES_POLICY,
            "entitlement_evidence_version_missing",
            "A versioned entitlement input is required.",
            evidence_status=EvidenceStatus.BLOCKED,
        )

    if (
        context.product_boundary == "copilot_studio_byom"
        and meter.meter_id in _AI_TOOL_METERS
    ):
        return _decision(
            meter,
            BillingDisposition.SEPARATELY_BILLED_BYOM,
            "byom_model_processing_separate",
            "Copilot AI-tool processing is excluded; model inference is billed by the model host.",
        )

    if context.computer_use:
        return _decision(
            meter,
            BillingDisposition.BILLABLE,
            "computer_use_not_included",
            "Computer-using agent actions are not included in the employee entitlement.",
        )

    if context.test_mode:
        if meter.meter_id in _AI_TOOL_METERS:
            return _decision(
                meter,
                BillingDisposition.BILLABLE,
                "ai_tool_in_test_flow_billable",
                "AI tools invoked by a test flow retain their own billable meter.",
            )
        if meter.meter_id in _TEST_EXEMPT_METERS:
            return _decision(
                meter,
                BillingDisposition.TEST_EXEMPT,
                "meter_specific_test_exemption",
                "This meter is exempt for the documented Copilot Studio test path.",
            )

    if context.authenticated is None or (
        context.audience_type.lower() == "b2e" and not context.license_sku
    ):
        return _decision(
            meter,
            BillingDisposition.UNKNOWN_REQUIRES_POLICY,
            "material_identity_evidence_missing",
            "Authentication and license evidence are required because they change billing.",
            evidence_status=EvidenceStatus.BLOCKED,
        )

    qualifies_for_employee_entitlement = (
        context.audience_type.lower() == "b2e"
        and context.authenticated is True
        and (context.license_sku or "").lower() in _M365_COPILOT_SKUS
        and context.identity_mode == "licensed_user"
        and context.channel in _QUALIFYING_CHANNELS
        and context.product_boundary
        in {"copilot_studio", "microsoft_365_copilot_native"}
    )
    if qualifies_for_employee_entitlement:
        if not context.fair_use_assumption:
            return _decision(
                meter,
                BillingDisposition.UNKNOWN_REQUIRES_POLICY,
                "fair_use_assumption_missing",
                "The documented employee inclusion is subject to fair-use limits.",
                evidence_status=EvidenceStatus.BLOCKED,
            )
        if (
            meter.meter_id == "agent_flow_actions"
            and context.trigger_type != "when_agent_calls_flow"
        ):
            return _decision(
                meter,
                BillingDisposition.BILLABLE,
                "agent_flow_trigger_not_included",
                "Only flows triggered by 'When an agent calls the flow' qualify.",
            )
        disposition = (
            BillingDisposition.INCLUDED_FIXED_SUBSCRIPTION
            if context.product_boundary == "microsoft_365_copilot_native"
            else BillingDisposition.ZERO_RATED_BY_ENTITLEMENT
        )
        return _decision(
            meter,
            disposition,
            "qualifying_m365_copilot_employee_use",
            "Authenticated licensed employee usage qualifies for the documented inclusion.",
        )

    return _decision(
        meter,
        BillingDisposition.BILLABLE,
        "no_applicable_entitlement",
        "No documented entitlement or meter-specific exemption applies.",
    )
