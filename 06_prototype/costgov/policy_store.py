"""Authoritative TokenGov policy loading and validation."""

from __future__ import annotations

import json
import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class PolicyLoadError(RuntimeError):
    """Raised when an authoritative policy cannot be loaded or validated."""


@dataclass(frozen=True)
class LoadedPolicy:
    document: dict[str, Any]
    provenance: dict[str, Any]


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def admit_receipt(receipt: dict[str, Any], loaded: LoadedPolicy) -> dict[str, Any]:
    """Evaluate an immutable Plan receipt against one authoritative policy revision."""
    snapshot = {
        "report_id": receipt["report_id"],
        "plan_id": receipt["plan_id"],
        "schema_version": receipt["schema_version"],
        "created_at": receipt["created_at"],
        "description": receipt["description"],
        "intake": receipt["intake"],
    }
    if receipt["schema_version"] == "2.0":
        snapshot.update(
            analysis=receipt["analysis"],
            confirmed_profile=receipt["confirmed_profile"],
            assumptions=receipt["assumptions"],
            clarifications=receipt["clarifications"],
            exclusions=receipt["exclusions"],
        )
    snapshot.update(
        prediction=receipt["prediction"],
        infrastructure=receipt["infrastructure"],
    )
    computed_hash = hashlib.sha256(_canonical(snapshot).encode("utf-8")).hexdigest()
    policy = loaded.document
    admission = policy["admission"]
    prediction = receipt["prediction"]
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
            "passed": prediction.get("model") in admission["allowed_models"],
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
    require_infrastructure = admission.get("require_infrastructure_estimate", False)
    checks.append({
        "name": "infrastructure_estimated",
        "passed": not require_infrastructure or receipt["infrastructure"].get("status") == "estimated",
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
        return LoadedPolicy(
            document=document,
            provenance={"source": "local_file", "path": str(path), "development_only": True},
        )
    raise PolicyLoadError("TOKENGOV_POLICY_SOURCE must be 'azure' or explicitly 'file'")


def _load_azure_policy() -> LoadedPolicy:
    endpoint = os.environ.get("AZURE_APPCONFIG_ENDPOINT")
    label = os.environ.get("TOKENGOV_POLICY_LABEL")
    key = os.environ.get("TOKENGOV_POLICY_KEY", "tokengov:policy")
    if not endpoint:
        raise PolicyLoadError("AZURE_APPCONFIG_ENDPOINT is required for Azure policy loading")
    if not label:
        raise PolicyLoadError("TOKENGOV_POLICY_LABEL is required for versioned Azure policy loading")

    from azure.appconfiguration import AzureAppConfigurationClient
    from azure.identity import DefaultAzureCredential

    try:
        client = AzureAppConfigurationClient(endpoint, DefaultAzureCredential())
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