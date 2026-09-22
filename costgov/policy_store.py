"""Authoritative TokenGov policy loading and validation."""

from __future__ import annotations

import json
import hashlib
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


class PolicyLoadError(RuntimeError):
    """Raised when an authoritative policy cannot be loaded or validated."""


@dataclass(frozen=True)
class LoadedPolicy:
    document: dict[str, Any]
    provenance: dict[str, Any]


def validate_measurement_policy(value: object) -> dict[str, Any]:
    """Validate opt-in measurement scope without changing operational budgets."""
    if isinstance(value, dict) and value.get("schema_version") == "workload-measurement-policy.v2":
        fixed = {
            "schema_version": "workload-measurement-policy.v2",
            "mode": "measurement_only",
            "workload_scope": "studio_campaigns",
            "require_explicit_campaign_id": True,
            "acknowledge_incomplete_costs": True,
            "hard_spend_cap_guaranteed": False,
            "operational_promotion": False,
        }
        limits = {
            "max_questions": 25,
            "max_output_tokens": 4096,
            "max_elapsed_seconds": 1800,
            "max_campaign_repetitions": 100,
            "max_campaign_questions": 2500,
            "max_evaluation_runs": 100,
            "max_evaluation_rows_per_run": 25,
        }
        expected = set(fixed) | set(limits) | {
            "observed_model_cost_stop_usd",
            "campaign_observed_model_cost_stop_usd",
            "expires_at",
        }
        if set(value) != expected:
            raise PolicyLoadError("policy.measurement must match workload-measurement-policy.v2")
        for key, required in fixed.items():
            if type(value[key]) is not type(required) or value[key] != required:
                raise PolicyLoadError(f"policy.measurement.{key} is invalid")
        for key, maximum in limits.items():
            if type(value[key]) is not int or not 1 <= value[key] <= maximum:
                raise PolicyLoadError(f"policy.measurement.{key} is outside the supported bound")
        if value["max_campaign_questions"] > (
            value["max_campaign_repetitions"] * value["max_questions"]
        ):
            raise PolicyLoadError(
                "policy.measurement.max_campaign_questions exceeds the repetition bound"
            )
        if value["max_evaluation_rows_per_run"] > value["max_questions"]:
            raise PolicyLoadError(
                "policy.measurement.max_evaluation_rows_per_run exceeds max_questions"
            )
        for key, maximum in (
            ("observed_model_cost_stop_usd", 5),
            ("campaign_observed_model_cost_stop_usd", 25),
        ):
            threshold = value[key]
            if (
                type(threshold) not in (int, float)
                or not 0 < threshold <= maximum
                or not math.isfinite(threshold)
            ):
                raise PolicyLoadError(
                    f"policy.measurement.{key} must be finite and in (0, {maximum}]"
                )
        if (
            value["campaign_observed_model_cost_stop_usd"]
            < value["observed_model_cost_stop_usd"]
        ):
            raise PolicyLoadError(
                "policy.measurement campaign stop cannot be below the per-execution stop"
            )
        _validate_measurement_expiry(value["expires_at"])
        return value

    fixed = {
        "schema_version": "workload-measurement-policy.v1",
        "mode": "measurement_only",
        "workload_scope": "studio_batches",
        "acknowledge_incomplete_costs": True,
        "hard_spend_cap_guaranteed": False,
        "operational_promotion": False,
    }
    limits = {
        "max_questions": 10,
        "max_output_tokens": 4096,
        "max_elapsed_seconds": 600,
    }
    expected = set(fixed) | set(limits) | {
        "observed_model_cost_stop_usd", "expires_at",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise PolicyLoadError("policy.measurement must match workload-measurement-policy.v1")
    for key, required in fixed.items():
        if type(value[key]) is not type(required) or value[key] != required:
            raise PolicyLoadError(f"policy.measurement.{key} is invalid")
    for key, maximum in limits.items():
        if type(value[key]) is not int or not 1 <= value[key] <= maximum:
            raise PolicyLoadError(f"policy.measurement.{key} is outside the supported bound")
    threshold = value["observed_model_cost_stop_usd"]
    if (
        type(threshold) not in (int, float)
        or not 0 < threshold <= 5
        or not math.isfinite(threshold)
    ):
        raise PolicyLoadError("policy.measurement.observed_model_cost_stop_usd must be finite and in (0, 5]")
    _validate_measurement_expiry(value["expires_at"])
    return value


def _validate_measurement_expiry(expires: object) -> None:
    if not isinstance(expires, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)", expires,
    ):
        raise PolicyLoadError("policy.measurement.expires_at must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(expires.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        raise PolicyLoadError("policy.measurement.expires_at must be a UTC timestamp") from None
    if parsed.utcoffset() != timezone.utc.utcoffset(None):
        raise PolicyLoadError("policy.measurement.expires_at must be a UTC timestamp")


def measurement_authorization(
    loaded: LoadedPolicy, *, now: datetime | None = None,
) -> dict[str, Any]:
    """Require a live, separately reviewed Azure measurement authorization.

    The observed-cost stop applies between questions, not to unknown internal
    charges. Operational execution retains its existing hard-budget semantics.
    """
    provenance = loaded.provenance
    if (
        loaded.document.get("status") != "active"
        or provenance.get("source") != "azure_app_configuration"
        or provenance.get("development_only")
        or not all(
            isinstance(provenance.get(key), str) and provenance[key].strip()
            for key in ("endpoint", "key", "label", "etag")
        )
    ):
        raise PolicyLoadError("Measurement requires active Azure policy with exact provenance")
    validate_policy(loaded.document)
    value = validate_measurement_policy(loaded.document.get("measurement"))
    current = now or datetime.now(timezone.utc)
    if current.utcoffset() is None:
        raise PolicyLoadError("Measurement authorization requires a timezone-aware clock")
    expires = datetime.fromisoformat(value["expires_at"].replace("Z", "+00:00"))
    if expires <= current:
        raise PolicyLoadError("Azure measurement authorization has expired")
    return dict(value)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


_MODEL_ALIASES = {
    "gpt-5-6-luna": "gpt-5.6-luna",
}


def _canonical_model_name(value: object) -> object:
    return _MODEL_ALIASES.get(value, value) if isinstance(value, str) else value


def admit_receipt(receipt: dict[str, Any], loaded: LoadedPolicy) -> dict[str, Any]:
    """Evaluate an immutable Plan receipt against one authoritative policy revision."""
    supported_schemas = {"1.0", "2.0", "3.0", "4.0", "5.0", "6.0"}
    if receipt.get("schema_version") not in supported_schemas:
        raise PolicyLoadError(
            f"unsupported receipt schema: {receipt.get('schema_version')}"
        )
    snapshot = {
        "report_id": receipt["report_id"],
        "plan_id": receipt["plan_id"],
        "schema_version": receipt["schema_version"],
        "created_at": receipt["created_at"],
        "description": receipt["description"],
        "intake": receipt["intake"],
    }
    if receipt["schema_version"] in {"2.0", "3.0", "4.0", "5.0", "6.0"}:
        snapshot.update(
            analysis=receipt["analysis"],
            confirmed_profile=receipt["confirmed_profile"],
            assumptions=receipt["assumptions"],
            clarifications=receipt["clarifications"],
            exclusions=receipt["exclusions"],
        )
    if receipt["schema_version"] in {"3.0", "4.0", "5.0", "6.0"}:
        snapshot.update(
            route=receipt["route"],
            commercial=receipt["commercial"],
            purchase=receipt.get("purchase"),
            token_subforecast=receipt.get("token_subforecast"),
            hybrid=receipt.get("hybrid"),
            acceptance_assumption=receipt.get("acceptance_assumption"),
        )
    if receipt["schema_version"] in {"4.0", "5.0", "6.0"}:
        snapshot["meter_stack"] = receipt["meter_stack"]
    if receipt["schema_version"] in {"5.0", "6.0"}:
        snapshot["trajectory_contract"] = receipt["trajectory_contract"]
    snapshot.update(
        prediction=receipt["prediction"],
        infrastructure=receipt["infrastructure"],
    )
    if {"response_forecast", "response_prediction_contract"} & set(receipt):
        from .response_forecasts import validate_receipt_response

        try:
            validate_receipt_response(receipt)
        except ValueError as exc:
            raise PolicyLoadError("invalid response forecast receipt") from exc
        snapshot.update(
            response_forecast=receipt["response_forecast"],
            response_prediction_contract=receipt["response_prediction_contract"],
        )
    computed_hash = hashlib.sha256(_canonical(snapshot).encode("utf-8")).hexdigest()
    policy = loaded.document
    admission = policy["admission"]
    prediction = receipt["prediction"]
    predicted_model = _canonical_model_name(prediction.get("model"))
    allowed_models = {
        _canonical_model_name(model) for model in admission["allowed_models"]
    }
    checks = [
        {
            "name": "receipt_integrity",
            "passed": computed_hash == receipt["content_hash"],
            "actual": computed_hash,
            "expected": receipt["content_hash"],
        },
        {
            "name": "provider_allowed",
            "passed": prediction.get("provider") in admission["allowed_providers"],
            "actual": prediction.get("provider"),
            "expected": admission["allowed_providers"],
        },
        {
            "name": "model_allowed",
            "passed": predicted_model in allowed_models,
            "actual": prediction.get("model"),
            "expected": admission["allowed_models"],
        },
        {
            "name": "pricing_verified",
            "passed": not admission["require_pricing_verified"] or prediction.get("pricing_verified") is True,
            "actual": prediction.get("pricing_verified"),
            "expected": admission["require_pricing_verified"],
        },
        {
            "name": "model_cost_per_call",
            "passed": prediction.get("cost_per_call", {}).get("mean", float("inf"))
            <= admission["max_model_cost_per_call_usd"],
            "actual": prediction.get("cost_per_call", {}).get("mean"),
            "expected": admission["max_model_cost_per_call_usd"],
        },
    ]
    if "response_forecast" in receipt:
        from .performance_review import content_hash, policy_projection

        authority = policy_projection({
            **loaded.provenance, "policy_id": policy["policy_id"],
            "version": policy["version"], "content_hash": content_hash(policy),
        })
        pinned = receipt["response_forecast"]["configuration"]["policy"]
        checks.append({
            "name": "response_configuration_policy",
            "passed": authority is not None and not loaded.provenance.get("development_only")
            and pinned == authority,
            "actual": pinned,
            "expected": authority,
        })
    infrastructure_policy = policy.get("infrastructure_coverage")
    if infrastructure_policy:
        checks.extend(
            _infrastructure_policy_checks(
                receipt["infrastructure"],
                receipt.get("route", {}).get("route_id"),
                infrastructure_policy,
            )
        )
    else:
        require_infrastructure = admission.get(
            "require_infrastructure_estimate", False
        )
        checks.append({
            "name": "infrastructure_estimated",
            "passed": (
                not require_infrastructure
                or receipt["infrastructure"].get("status") == "estimated"
            ),
            "actual": receipt["infrastructure"].get("status"),
            "expected": "estimated" if require_infrastructure else "not_required",
        })
    admitted = all(check["passed"] for check in checks)
    policy_hash = hashlib.sha256(_canonical(policy).encode("utf-8")).hexdigest()
    return {
        "status": "admitted" if admitted else "rejected",
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "policy": {
            "policy_id": policy["policy_id"],
            "version": policy["version"],
            "schema_version": policy["schema_version"],
            "content_hash": policy_hash,
            "provenance": loaded.provenance,
        },
        "execution": policy["execution"] if admitted else None,
        "mutation": policy["mutation"],
    }


def _required_mapping(value: dict[str, Any], key: str) -> dict[str, Any]:
    child = value.get(key)
    if not isinstance(child, dict):
        raise PolicyLoadError(f"policy.{key} must be an object")
    return child


def _infrastructure_policy_checks(
    infrastructure: Mapping[str, Any],
    route_id: object,
    policy: Mapping[str, Any],
) -> list[dict[str, Any]]:
    applicable = route_id in policy["applicable_routes"]
    if not applicable:
        return [{
            "name": "infrastructure_applicability",
            "passed": infrastructure.get("status") == "not_applicable",
            "actual": infrastructure.get("status"),
            "expected": "not_applicable",
        }]
    lines = infrastructure.get("price_snapshot", {}).get("lines", [])
    exact_meter_evidence = all(
        line.get("evidence_status") != "sourced"
        or bool(line.get("meter_id") and line.get("meter_name"))
        for line in lines
    )
    captured_at = infrastructure.get("price_snapshot", {}).get("captured_at")
    evidence_age_days = float("inf")
    if isinstance(captured_at, str):
        try:
            captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
            evidence_age_days = max(
                0, (datetime.now(timezone.utc) - captured).total_seconds() / 86400
            )
        except ValueError:
            pass
    safeguards = {
        item.get("safeguard_id")
        for item in infrastructure.get("architecture", {}).get("safeguards", [])
        if item.get("status") == "design_satisfied"
    }
    unpriced = [
        *(infrastructure.get("unpriced_material_items") or []),
        *(infrastructure.get("unpriced_material_lines") or []),
    ]
    subtotal = infrastructure.get("priced_subtotal_usd")
    ceiling = policy.get("max_monthly_cost_usd")
    return [
        {
            "name": "infrastructure_applicability",
            "passed": True,
            "actual": route_id,
            "expected": policy["applicable_routes"],
        },
        {
            "name": "infrastructure_confirmed_estimate",
            "passed": (
                not policy["require_confirmed_estimate"]
                or (
                    infrastructure.get("status") == "estimated"
                    and infrastructure.get("confirmed") is True
                )
            ),
            "actual": {
                "status": infrastructure.get("status"),
                "confirmed": infrastructure.get("confirmed"),
            },
            "expected": "confirmed estimated",
        },
        {
            "name": "infrastructure_priced_coverage",
            "passed": infrastructure.get("coverage_ratio", 0)
            >= policy["min_priced_coverage_ratio"],
            "actual": infrastructure.get("coverage_ratio"),
            "expected": policy["min_priced_coverage_ratio"],
        },
        {
            "name": "infrastructure_material_items_priced",
            "passed": policy["allow_material_unpriced_items"] or not unpriced,
            "actual": unpriced,
            "expected": (
                "permitted" if policy["allow_material_unpriced_items"] else []
            ),
        },
        {
            "name": "infrastructure_price_evidence",
            "passed": (
                infrastructure.get("price_snapshot", {}).get("price_type")
                == policy["required_price_type"]
                and infrastructure.get("price_snapshot", {}).get("currency")
                == policy["currency"]
                and (
                    not policy["require_exact_meter_match"] or exact_meter_evidence
                )
            ),
            "actual": {
                "price_type": infrastructure.get("price_snapshot", {}).get(
                    "price_type"
                ),
                "currency": infrastructure.get("price_snapshot", {}).get("currency"),
                "exact_meter_match": exact_meter_evidence,
            },
            "expected": {
                "price_type": policy["required_price_type"],
                "currency": policy["currency"],
                "exact_meter_match": policy["require_exact_meter_match"],
            },
        },
        {
            "name": "infrastructure_price_freshness",
            "passed": evidence_age_days <= policy["max_price_evidence_age_days"],
            "actual": evidence_age_days,
            "expected": policy["max_price_evidence_age_days"],
        },
        {
            "name": "infrastructure_region",
            "passed": infrastructure.get("region") in policy["allowed_regions"],
            "actual": infrastructure.get("region"),
            "expected": policy["allowed_regions"],
        },
        {
            "name": "infrastructure_safeguards",
            "passed": set(policy["required_safeguards"]) <= safeguards,
            "actual": sorted(safeguards),
            "expected": policy["required_safeguards"],
        },
        {
            "name": "infrastructure_monthly_cost",
            "passed": (
                ceiling is None
                or (
                    isinstance(subtotal, (int, float))
                    and not isinstance(subtotal, bool)
                    and subtotal <= ceiling
                )
            ),
            "actual": subtotal,
            "expected": ceiling if ceiling is not None else "no_ceiling",
        },
    ]


def validate_policy(document: dict[str, Any]) -> dict[str, Any]:
    """Validate the fields that directly control admission and execution."""
    required_strings = ("schema_version", "policy_id", "version", "status", "effective_from")
    for key in required_strings:
        if not isinstance(document.get(key), str) or not document[key].strip():
            raise PolicyLoadError(f"policy.{key} must be a non-empty string")
    if document["status"] != "active":
        raise PolicyLoadError(f"policy {document['policy_id']}:{document['version']} is not active")
    try:
        effective_from = datetime.fromisoformat(document["effective_from"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise PolicyLoadError("policy.effective_from must be ISO-8601") from exc
    if effective_from.tzinfo is None:
        raise PolicyLoadError("policy.effective_from must include a timezone")
    if effective_from > datetime.now(timezone.utc):
        raise PolicyLoadError("policy is not effective yet")

    admission = _required_mapping(document, "admission")
    execution = _required_mapping(document, "execution")
    mutation = _required_mapping(document, "mutation")
    for key in ("allowed_providers", "allowed_models"):
        if not isinstance(admission.get(key), list) or not admission[key]:
            raise PolicyLoadError(f"policy.admission.{key} must be a non-empty array")
    ceiling = admission.get("max_model_cost_per_call_usd")
    if not isinstance(ceiling, (int, float)) or ceiling <= 0:
        raise PolicyLoadError("policy.admission.max_model_cost_per_call_usd must be positive")
    if not isinstance(admission.get("require_pricing_verified"), bool):
        raise PolicyLoadError("policy.admission.require_pricing_verified must be boolean")
    infrastructure = document.get("infrastructure_coverage")
    if infrastructure is not None:
        if not isinstance(infrastructure, dict):
            raise PolicyLoadError("policy.infrastructure_coverage must be an object")
        if infrastructure.get("schema_version") != "infrastructure-coverage-policy.v1":
            raise PolicyLoadError(
                "policy.infrastructure_coverage.schema_version is invalid"
            )
        for key in (
            "applicable_routes",
            "allowed_regions",
            "required_safeguards",
        ):
            value = infrastructure.get(key)
            if not isinstance(value, list) or not value or not all(
                isinstance(item, str) and item.strip() for item in value
            ):
                raise PolicyLoadError(
                    f"policy.infrastructure_coverage.{key} must be a non-empty string array"
                )
        for key in (
            "require_confirmed_estimate",
            "allow_material_unpriced_items",
            "require_exact_meter_match",
        ):
            if not isinstance(infrastructure.get(key), bool):
                raise PolicyLoadError(
                    f"policy.infrastructure_coverage.{key} must be boolean"
                )
        ratio = infrastructure.get("min_priced_coverage_ratio")
        if (
            not isinstance(ratio, (int, float))
            or isinstance(ratio, bool)
            or not 0 <= ratio <= 1
        ):
            raise PolicyLoadError(
                "policy.infrastructure_coverage.min_priced_coverage_ratio "
                "must be between 0 and 1"
            )
        if infrastructure.get("required_price_type") != "Consumption":
            raise PolicyLoadError(
                "policy.infrastructure_coverage.required_price_type must be Consumption"
            )
        currency = infrastructure.get("currency")
        if not isinstance(currency, str) or len(currency) != 3:
            raise PolicyLoadError(
                "policy.infrastructure_coverage.currency must be a 3-letter code"
            )
        age = infrastructure.get("max_price_evidence_age_days")
        if not isinstance(age, int) or isinstance(age, bool) or age < 1:
            raise PolicyLoadError(
                "policy.infrastructure_coverage.max_price_evidence_age_days "
                "must be a positive integer"
            )
        infrastructure_ceiling = infrastructure.get("max_monthly_cost_usd")
        if infrastructure_ceiling is not None and (
            not isinstance(infrastructure_ceiling, (int, float))
            or isinstance(infrastructure_ceiling, bool)
            or infrastructure_ceiling <= 0
        ):
            raise PolicyLoadError(
                "policy.infrastructure_coverage.max_monthly_cost_usd must be positive"
            )

    if execution.get("routing_mode") not in {"cost", "balanced", "quality"}:
        raise PolicyLoadError("policy.execution.routing_mode is invalid")
    for key in ("semantic_cache", "budget", "evaluation"):
        _required_mapping(execution, key)
    semantic_cache = execution["semantic_cache"]
    budget = execution["budget"]
    evaluation = execution["evaluation"]
    if not isinstance(semantic_cache.get("enabled"), bool):
        raise PolicyLoadError("policy.execution.semantic_cache.enabled must be boolean")
    score_threshold = semantic_cache.get("score_threshold")
    if not isinstance(score_threshold, (int, float)) or isinstance(score_threshold, bool) or not 0 <= score_threshold <= 1:
        raise PolicyLoadError("policy.execution.semantic_cache.score_threshold must be between 0 and 1")
    run_budget = budget.get("per_tenant_usd_per_run")
    if not isinstance(run_budget, (int, float)) or isinstance(run_budget, bool) or run_budget <= 0:
        raise PolicyLoadError("policy.execution.budget.per_tenant_usd_per_run must be positive")
    if budget.get("hard_cap_action") not in {"deny", "degrade", "require_approval"}:
        raise PolicyLoadError("policy.execution.budget.hard_cap_action is invalid")
    min_quality = evaluation.get("min_quality")
    if not isinstance(min_quality, (int, float)) or isinstance(min_quality, bool) or not 0 <= min_quality <= 1:
        raise PolicyLoadError("policy.execution.evaluation.min_quality must be between 0 and 1")
    min_samples = evaluation.get("min_segment_samples")
    if not isinstance(min_samples, int) or isinstance(min_samples, bool) or min_samples < 1:
        raise PolicyLoadError("policy.execution.evaluation.min_segment_samples must be a positive integer")
    consecutive_breaches = evaluation.get("consecutive_breaches")
    if consecutive_breaches is not None and (
        not isinstance(consecutive_breaches, int)
        or isinstance(consecutive_breaches, bool)
        or consecutive_breaches < 1
    ):
        raise PolicyLoadError("policy.execution.evaluation.consecutive_breaches must be a positive integer")
    if mutation.get("mode") not in {"manual", "evaluation_bound"}:
        raise PolicyLoadError("policy.mutation.mode is invalid")
    allowed_knobs = mutation.get("allowed_knobs")
    if not isinstance(allowed_knobs, list) or not all(isinstance(item, str) for item in allowed_knobs):
        raise PolicyLoadError("policy.mutation.allowed_knobs must be an array of strings")
    if "measurement" in document:
        validate_measurement_policy(document["measurement"])
    return document


def load_policy_from_environment() -> LoadedPolicy:
    """Load policy from Azure by default; local files require an explicit opt-in."""
    source = os.environ.get("TOKENGOV_POLICY_SOURCE", "azure").lower()
    if source == "azure":
        return _load_azure_policy()
    if source == "file":
        path_value = os.environ.get("TOKENGOV_POLICY_FILE")
        if not path_value:
            raise PolicyLoadError("TOKENGOV_POLICY_FILE is required when source=file")
        path = Path(path_value).resolve()
        document = validate_policy(json.loads(path.read_text(encoding="utf-8")))
        content_hash = hashlib.sha256(_canonical(document).encode("utf-8")).hexdigest()
        return LoadedPolicy(
            document=document,
            provenance={
                "source": "local_file",
                "path": str(path),
                "label": f"development:{document['version']}",
                "etag": content_hash,
                "development_only": True,
            },
        )
    raise PolicyLoadError("TOKENGOV_POLICY_SOURCE must be 'azure' or explicitly 'file'")


def _azure_policy_credential():
    from azure.identity import AzureCliCredential, DefaultAzureCredential

    subscription = os.environ.get("TOKENGOV_POLICY_AZURE_CLI_SUBSCRIPTION", "")
    if not subscription:
        return DefaultAzureCredential()
    if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", subscription):
        raise PolicyLoadError("TOKENGOV_POLICY_AZURE_CLI_SUBSCRIPTION must be a subscription UUID")
    if os.environ.get("CONTAINER_APP_NAME") or os.environ.get("WEBSITE_INSTANCE_ID"):
        raise PolicyLoadError(
            "TOKENGOV_POLICY_AZURE_CLI_SUBSCRIPTION is local-development only; "
            "remove it from hosted environments and use the runtime identity"
        )
    return AzureCliCredential(subscription=subscription, process_timeout=20)


def _load_azure_policy() -> LoadedPolicy:
    endpoint = os.environ.get("AZURE_APPCONFIG_ENDPOINT")
    label = os.environ.get("TOKENGOV_POLICY_LABEL")
    key = os.environ.get("TOKENGOV_POLICY_KEY", "tokengov:policy")
    if not endpoint:
        raise PolicyLoadError("AZURE_APPCONFIG_ENDPOINT is required for Azure policy loading")
    if not label:
        raise PolicyLoadError("TOKENGOV_POLICY_LABEL is required for versioned Azure policy loading")

    from azure.appconfiguration import AzureAppConfigurationClient

    try:
        client = AzureAppConfigurationClient(endpoint, _azure_policy_credential())
        setting = client.get_configuration_setting(key=key, label=label)
        if setting.content_type and "json" not in setting.content_type.lower():
            raise PolicyLoadError(f"Azure policy setting has non-JSON content type: {setting.content_type}")
        document = validate_policy(json.loads(setting.value))
    except PolicyLoadError:
        raise
    except Exception as exc:
        raise PolicyLoadError(f"failed to load authoritative policy from Azure: {exc}") from exc

    last_modified = setting.last_modified.isoformat() if setting.last_modified else None
    return LoadedPolicy(
        document=document,
        provenance={
            "source": "azure_app_configuration",
            "endpoint": endpoint,
            "key": key,
            "label": label,
            "etag": str(setting.etag) if setting.etag is not None else None,
            "last_modified": last_modified,
            "content_type": setting.content_type,
        },
    )