"""Pure advisory projection of receipt forecasts and verified observation summaries.

Workload adapters own source validation. This control-plane projection neither
admits execution nor infers acceptance or complete-task economics from usage.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone

from .policy_store import PolicyLoadError, validate_policy


SCHEMA = "studio-performance-review.v1"
POLICY_FIELDS = ("policy_id", "version", "content_hash", "source", "endpoint", "key", "label", "etag")


def mapping(value):
    return value if isinstance(value, dict) else {}


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def identity(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value) else None


def content_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def instant(value):
    if not isinstance(value, (str, datetime)):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def policy_projection(value):
    """Project only bounded identity/provenance, never a policy's free text."""
    value = mapping(value)
    projected = {key: identity(value.get(key)) for key in ("policy_id", "version")}
    sha = value.get("content_hash")
    projected["content_hash"] = sha if isinstance(sha, str) and re.fullmatch(r"[a-f0-9]{64}", sha) else None
    projected["source"] = "azure_app_configuration" if value.get("source") == "azure_app_configuration" else None
    endpoint = value.get("endpoint")
    projected["endpoint"] = endpoint if isinstance(endpoint, str) and re.fullmatch(
        r"https://[A-Za-z0-9.-]+\.azconfig\.(?:io|azure\.us|azure\.cn)/?", endpoint
    ) else None
    for key in ("key", "label", "etag"):
        raw = value.get(key)
        projected[key] = raw if isinstance(raw, str) and re.fullmatch(r'[A-Za-z0-9_.:/+"=-]{1,512}', raw) else None
    return projected if all(projected.values()) else None


def _current_policy(loaded, policy_error):
    if loaded is None or policy_error is not None:
        return None, None
    try:
        document, provenance = loaded.document, loaded.provenance
        validate_policy(document)
        if provenance.get("development_only"):
            return None, None
        projected = policy_projection({
            **{key: provenance.get(key) for key in POLICY_FIELDS},
            "policy_id": document["policy_id"], "version": document["version"],
            "content_hash": content_hash(document),
        })
        return projected, mapping(document.get("measurement")) if projected else None
    except (PolicyLoadError, ValueError, KeyError, TypeError, AttributeError):
        return None, None


def _forecast(receipt):
    prediction = mapping(receipt.get("prediction"))
    tokens = mapping(prediction.get("tokens_per_call"))
    trace = mapping(prediction.get("calculation_trace"))
    scale = mapping(trace.get("scale"))
    cache = mapping(scale.get("cache_assumptions"))
    tools = mapping(prediction.get("tool_costs_per_call"))
    infrastructure = mapping(receipt.get("infrastructure"))
    architecture = mapping(infrastructure.get("architecture"))
    inputs = [number(tokens.get(key)) for key in ("text_input", "document_input", "image_input", "audio_input")]
    # Missing modalities are not observations of zero. Use the known component
    # subtotal, labeling coverage rather than inventing missing token counts.
    known_inputs = [value for value in inputs if value is not None]
    return {
        "classification": "modeled",
        "scope": "model_call_not_complete_task",
        "model": identity(prediction.get("model")),
        "provider": identity(prediction.get("provider")),
        "pricing_version": identity(prediction.get("pricing_version")),
        "prediction_id": (prediction.get("prediction_id") if type(prediction.get("prediction_id")) is int
                          else identity(prediction.get("prediction_id"))),
        "method": identity(prediction.get("prediction_method")),
        "text_input_tokens": number(tokens.get("text_input")),
        "document_input_tokens": number(tokens.get("document_input")),
        "input_tokens": sum(known_inputs) if known_inputs else None,
        "input_components_complete": bool(known_inputs) and len(known_inputs) == len(inputs),
        "output_tokens": number(tokens.get("text_output")),
        "model_cost_per_call_usd": number(mapping(prediction.get("cost_per_call")).get("mean")),
        "generic_file_search_cost_per_call_usd": number(tools.get("file_search_cost_usd")),
        "tool_cost_per_call_usd": number(tools.get("total_usd")),
        "tool_cost_claimed_complete": tools.get("is_complete") if type(tools.get("is_complete")) is bool else None,
        "daily_calls": number(scale.get("daily_calls")),
        "cache_assumptions": {
            key: number(cache.get(key)) for key in (
                "system_prompt_share_of_text_input", "cache_hit_rate_after_first_call",
                "cached_calls_per_day", "cost_discount_factor",
            )
        },
        "infrastructure": {
            "schema_version": identity(infrastructure.get("schema_version")),
            "baseline_revision": identity(architecture.get("baseline_revision")),
            "monthly_priced_subtotal_usd": number(infrastructure.get("priced_subtotal_usd")),
            "scope": "modeled_monthly_infrastructure_not_allocated_to_observed_questions",
            "classification": "modeled_not_calibrated_tail_risk",
        },
    }


def build_review(receipt, *, observation=None, run_options=(), historical_policy=None,
                 current_policy=None, policy_error=None, now=None, excluded_run_count=0,
                 unsupported_run_count=0, tool_applicability=None):
    """Build JSON-safe advisory evidence from a validated receipt and adapter summary.

    ``observation`` uses ``performance-observation.v1``; it contains only scalar
    counts, scoped allocations, identifiers, dates and fixed coverage codes.
    It is not an unvalidated runtime result or a browser aggregate.
    """
    clock = instant(now) if now is not None else datetime.now(timezone.utc)
    if clock is None:
        raise ValueError("Review requires a timezone-aware clock")
    if observation is not None and observation.get("schema_version") != "performance-observation.v1":
        raise ValueError("Unsupported performance observation contract")
    observed = observation or {}
    forecast = _forecast(receipt)
    historical = policy_projection(historical_policy)
    current, current_measurement = _current_policy(current_policy, policy_error)
    policy_status = "unavailable" if current is None or historical is None else (
        "same" if all(current[key] == historical[key] for key in POLICY_FIELDS) else "changed"
    )
    run_id = identity(observed.get("run_id"))
    refs = [{
        "kind": "receipt", "id": identity(receipt.get("receipt_id")),
        "content_hash": receipt.get("content_hash"),
    }]
    if run_id:
        refs.append({"kind": "run", "id": run_id, "content_hash": observed["evidence_hash"]})
    for label, binding in (("historical_policy", historical), ("current_policy", current)):
        if binding:
            refs.append({"kind": label, "id": binding["policy_id"], **binding})
    findings = []

    def finding(code, title, detail, action="evidence", severity="warning"):
        findings.append({
            "code": code, "severity": severity, "title": title, "detail": detail,
            "action": action,
            "action_label": {"forecast": "Review forecast assumptions", "policy": "Review policy",
                             "execute": "Prepare another measurement", "evidence": "Inspect evidence",
                             None: None}[action],
            "evidence_refs": [dict(ref) for ref in refs],
        })

    if not run_id:
        finding("no_supported_run_evidence", "No supported batch evidence",
                "No valid saved batch is bound to this exact forecast receipt. Conventional runs remain in advanced review.",
                "execute", "info")
    if excluded_run_count:
        finding("invalid_run_evidence_excluded", "Some evidence could not be verified",
                "Invalid or incompatible records were excluded, not counted as zero usage.")
    if observed.get("execution_status") in {"blocked", "partial", "failed"}:
        finding("execution_incomplete", "This attempt did not complete",
                "Review the saved attempt before another separately authorized measurement. Completion is not acceptance.",
                "execute")
    if current is None:
        finding("current_policy_unavailable", "Current Azure policy is unavailable",
                "Historical evidence remains inspectable; this review does not authorize another execution.", "policy")
    elif policy_status == "changed":
        finding("current_policy_changed", "Current policy differs from the recorded binding",
                "Identity, content hash or Azure provenance changed. This is not evidence of historical noncompliance.",
                "policy")

    measurement = mapping(observed.get("measurement"))
    expiries = {}
    for label, grant in (("historical", measurement), ("current", current_measurement or {})):
        expires = instant(grant.get("expires_at"))
        if expires:
            remaining = (expires - clock).total_seconds()
            status = "expired" if remaining <= 0 else "near_expiry" if remaining <= 72 * 3600 else "active"
            expiries[label] = {"expires_at": expires.isoformat(), "status": status}
            if label == "current" and policy_status == "same" and expiries.get("historical") == expiries[label]:
                continue
            if status != "active":
                scope = "recorded and current" if policy_status == "same" and expires == instant(
                    mapping(current_measurement).get("expires_at")
                ) else label
                finding(f"{label}_measurement_{status}",
                        "Measurement grant expired" if status == "expired" else "Measurement grant nearing expiry",
                        f"The {scope} grant must be reviewed before future measurement. This does not invalidate historical observations.",
                        "policy")
    output = number(observed.get("output_tokens_mean"))
    expected_output = forecast["output_tokens"]
    if expected_output is not None and output is not None and abs(output - expected_output) > max(1, expected_output * .25):
        finding("output_assumption_differs", "Output assumption differs materially",
                "Modeled output per call differs from the observed response average. This is an assumption diagnostic, not savings or evidence of equivalent quality.",
                "forecast")
    cap = number(measurement.get("max_output_tokens"))
    if cap is not None and expected_output is not None and expected_output > cap:
        finding("forecast_output_exceeds_recorded_cap", "Forecast output exceeds the recorded response cap",
                "The modeled mean output is above the measurement max-output limit. Review the forecast scope and bound; do not infer achieved quality.",
                "forecast")
    if tool_applicability == "managed_retrieval_not_generic_file_search" and (
            forecast["generic_file_search_cost_per_call_usd"] is not None):
        finding("generic_tool_pricing_uncertain", "Generic file-search pricing may not apply",
                "The receipt models generic file search. Managed knowledge-base retrieval has a different unverified billing scope; the receipt's complete-tool-cost flag does not prove full tool cost.",
                "forecast")
    if run_id and observed.get("usage_status") != "complete":
        finding("usage_coverage_incomplete", "Response usage is incomplete",
                "Unavailable or invalid usage remains null. Any known subtotal excludes unobserved usage and cannot represent total cost.")
    if run_id and observed.get("allocation_status") != "complete_response_coverage":
        finding("response_allocation_incomplete", "Response-model allocation is incomplete",
                "Missing usage partitions, model identity or sourced prices prevent a complete response-model allocation. Any known subtotal is not the batch's total bill.")
    if run_id and observed.get("cloud_status") == "invalid":
        finding("publication_evidence_invalid", "Cloud publication could not be verified",
                "Publication evidence did not validate against this exact run and evidence hash. Local execution observations remain separate from upload state.")
    finding("retrieval_embedding_billing_unavailable", "Complete task billing is unavailable",
            "Retrieval, embedding, hidden calls, evaluation and infrastructure allocation are not fully attributed. Known response-model allocation is catalog-priced, not an invoice.")
    finding("quality_evidence_unavailable", "Keep measuring before optimizing",
            "Quality and segment-level acceptance are not evaluated here. Completed questions do not establish acceptance, quality preservation or permission to optimize.")

    comparison = []

    def compare(metric, label, expected, actual, unit, reason):
        comparison.append({
            "metric": metric, "label": label, "expected": expected, "observed": actual, "unit": unit,
            "status": "partially_comparable" if expected is not None and actual is not None else (
                "not_comparable" if expected is not None or actual is not None else "unavailable"),
            "reason": reason, "variance_pct": None,
        })

    diagnostic = "Diagnostic only: modeled per-call scope and cumulative response scope are not proven equivalent; no percent variance."
    compare("input_tokens", "Input tokens: modeled call / observed response mean",
            forecast["input_tokens"], number(observed.get("input_tokens_mean")), "tokens", diagnostic)
    compare("output_tokens", "Output tokens: modeled call / observed response mean",
            expected_output, output, "tokens", diagnostic)
    compare("model_allocation_usd", "Model cost: modeled call / observed response mean",
            forecast["model_cost_per_call_usd"], number(observed.get("model_allocation_mean_usd")), "USD", diagnostic)
    compare("tool_cost_usd", "Modeled tool cost per call",
            forecast["tool_cost_per_call_usd"], None, "USD/call",
            "Managed retrieval/tool billing is unavailable; generic pricing applicability is unverified.")
    compare("infrastructure_monthly_usd", "Modeled monthly infrastructure",
            forecast["infrastructure"]["monthly_priced_subtotal_usd"], None, "USD/month",
            "Monthly infrastructure is separate from per-question allocation; no observed resource allocation.")
    compare("task_cost_usd", "Complete task cost", None, None, "USD/task",
            "Neither a complete-task forecast comparison nor complete observed cost is available.")
    compare("quality", "Accepted-task quality", None, None, "acceptance",
            "No evaluated segment acceptance outcomes; completed-question counts are not quality.")
    summary = {
        "execution_status": observed.get("execution_status", "no_supported_run_evidence"),
        "questions_planned": observed.get("questions_count"),
        "questions_completed": observed.get("questions_completed"),
        "input_tokens": observed.get("input_tokens"), "output_tokens": observed.get("output_tokens"),
        "latency_mean_ms": observed.get("latency_mean_ms"),
        "observed_model_allocation_usd": observed.get("observed_model_allocation_usd"),
        "allocation_scope": "response_model_only_not_task_total",
        "quality_status": "not_evaluated", "billing_status": "unavailable",
        "cloud_status": observed.get("cloud_status", "unavailable"),
        "total_task_cost_usd": None, "accepted_tasks": None,
        "usage_status": observed.get("usage_status", "unavailable"),
        "allocation_status": observed.get("allocation_status", "unavailable"),
        "known_model_allocation_subtotal_usd": observed.get("known_model_allocation_subtotal_usd"),
    }
    return {
        "schema_version": SCHEMA, "classification": "advisory", "generated_at": clock.isoformat(),
        "plan_id": identity(receipt.get("plan_id")), "report_id": identity(receipt.get("report_id")),
        "receipt_id": identity(receipt.get("receipt_id")), "receipt_hash": receipt.get("content_hash"),
        "selected_run_id": run_id, "run_options": list(run_options),
        "scope": {
            "status": "response_model_only" if run_id else "no_supported_run_evidence",
            "message": "One verified saved batch; response observations are not complete-task economics." if run_id else
                       "Forecast and policy context only; no supported verified batch for this receipt.",
        },
        "summary": summary, "forecast": forecast, "comparison": comparison, "findings": findings,
        "decision": {
            "status": "review_required" if run_id else "awaiting_evidence",
            "title": "Review assumptions and keep measuring" if run_id else "Collect receipt-bound measurement",
            "explanation": "This evidence supports advisory review only. Obtain segment quality/acceptance and full cost evidence before considering optimization; future execution requires current authorization.",
            "operational_promotion": False, "automatic_changes": False, "evidence_refs": refs,
        },
        "policy": {"historical": historical, "current": current, "status": policy_status,
                   "measurement_expiry": expiries},
        "provenance": {
            "receipt_schema": identity(receipt.get("schema_version")),
            "observation_schema": observed.get("schema_version"),
            "run_schema": observed.get("source_schema"), "run_evidence_hash": observed.get("evidence_hash"),
            "started_at": observed.get("started_at"), "ended_at": observed.get("ended_at"),
            "publication": observed.get("publication"),
            "pricing": observed.get("pricing"),
            "measurement": measurement or None,
            "excluded_run_count": excluded_run_count, "unsupported_run_count": unsupported_run_count,
            "comparison_scope": "single_run_not_cross_run_aggregate",
            "tail_risk_status": "not_calibrated", "production_validated": False,
        },
    }
