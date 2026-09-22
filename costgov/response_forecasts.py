"""Server-owned prospective response means, not task costs or quality authority.

Advances predict -> reconcile -> learn. Consumes probed response-configuration.v1
and optional immutable predictor candidates; emits modeled response evidence.
The caller must probe the authority/configuration before creating a NEW result.
This module does not probe Azure, authorize execution, or infer response semantics
from generic tokens_per_call. Minimum sample counts are not statistical proof.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

from .performance_review import content_hash, instant
from .response_learning import (
    AGGREGATION, CONTRACT_SCHEMA, SCOPE, TARGETS,
    _fields, _identity, _number, _prediction_id, _sha, response_configuration,
)

FORECAST_SCHEMA = "response-forecast.v1"
SOURCE_SCHEMA = "response-baseline-source.v1"
EXPERIMENT_SCHEMA = "response-experiment.v1"


def normalize_response_experiment(experiment: dict) -> dict:
    """Pin existing dataset membership references, never manufacture independence.

    case_ids and family_ids are nonempty, duplicate-free identity sets, not
    aligned pairs. The workload adapter must verify both sets against the
    hash-pinned dataset and requested cases, and enforce split/family separation.
    Their normalized order has no sampling or independence semantics.
    """
    expected = {"dataset_hash", "case_ids", "family_ids", "split"}
    if isinstance(experiment, dict) and "schema_version" in experiment:
        expected.add("schema_version")
    _fields(experiment, expected)
    if experiment.get("schema_version", EXPERIMENT_SCHEMA) != EXPERIMENT_SCHEMA:
        raise ValueError("Unsupported response experiment evidence schema")
    if experiment["split"] not in ("development", "holdout"):
        raise ValueError("Response experiment split must be development or holdout")
    normalized = {
        "schema_version": EXPERIMENT_SCHEMA,
        "dataset_hash": _sha(experiment["dataset_hash"]),
        "split": experiment["split"],
    }
    for key in ("case_ids", "family_ids"):
        values = experiment[key]
        if not isinstance(values, list) or not values:
            raise ValueError("Response experiment requires nonempty case and family identity lists")
        identifiers = [_identity(value) for value in values]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Response experiment identities must be duplicate-free")
        normalized[key] = sorted(identifiers)
    return normalized


def validate_configuration(configuration):
    """Validate a complete, content-free server probe projection (not authority)."""
    try:
        _fields(configuration, ("schema_version", "agent", "policy", "pricing", "measurement"))
        agent = dict(configuration["agent"])
        retrieval_hash = _sha(agent.pop("retrieval_configuration_hash"))
        agent["retrieval_evidence"] = {"content_hash": retrieval_hash}
        reconstructed = response_configuration({
            "execution_status": "completed", "agent": agent,
            "policy": configuration["policy"], "pricing": configuration["pricing"],
            "measurement_authorization": configuration["measurement"],
        })
        if reconstructed != configuration:
            raise ValueError("Response configuration must be the exact server projection")
        cap = configuration["measurement"]["max_output_tokens"]
        if type(cap) is not int or cap < 1:
            raise ValueError("Response output cap must be a positive integer")
        return configuration
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Incomplete prospective response configuration") from exc


def _means(value, cap):
    _fields(value, TARGETS)
    if any(_number(value[key]) <= 0 for key in TARGETS):
        raise ValueError("Response forecast means must be positive and finite")
    if value["output_tokens_mean"] > cap:
        raise ValueError("Response output forecast exceeds the probed output cap")
    return value


def build_response_forecast(*, prediction, configuration, baseline, source, candidates=None, experiment=None):
    """Build exact response evidence; only PlanStore's explicit intent persists it.

    baseline contains input_tokens_mean/output_tokens_mean. source must contain
    schema_version=response-baseline-source.v1, revision and content_hash of the
    server-owned prospective estimation inputs/method. It must not describe actuals.
    candidates is an optional target -> registered predictor candidate mapping.
    experiment optionally pins dataset_hash, case_ids, family_ids and split
    before execution. It does not change configuration cohorts or certify
    question independence; the workload adapter must verify dataset membership.
    """
    from future_token_predictor.history.response_calibration import forecast_response

    configuration = validate_configuration(configuration)
    _fields(source, ("schema_version", "revision", "content_hash"))
    if source["schema_version"] != SOURCE_SCHEMA:
        raise ValueError("An explicit prospective response baseline source is required")
    _identity(source["revision"])
    _sha(source["content_hash"])
    pricing = configuration["pricing"]
    agent = configuration["agent"]
    if (prediction["provider"] != pricing["provider"]
            or prediction["model"] != agent["model"]
            or pricing["model"] != agent["model"]
            or pricing["model_version"] != agent["model_version"]
            or prediction.get("pricing_version") != pricing["revision"]):
        raise ValueError("Response forecast model/pricing provenance mismatch")
    cohort = {
        "scope": SCOPE, "aggregation": AGGREGATION,
        "provider": _identity(prediction["provider"]), "model": _identity(prediction["model"]),
        "model_version": _identity(agent["model_version"]),
        "archetype": _identity(prediction.get("archetype"), optional=True),
        "configuration_hash": content_hash(configuration),
    }
    cap = configuration["measurement"]["max_output_tokens"]
    baseline = _means(baseline, cap)
    applied, selected = {}, dict(baseline)
    candidates = {} if candidates is None else candidates
    if not isinstance(candidates, dict) or set(candidates) - set(TARGETS):
        raise ValueError("Unsupported response candidate target")
    for target, candidate in candidates.items():
        if candidate.get("target") != target:
            raise ValueError("Response candidate target mismatch")
        if any(str(row["prediction_id"]) == str(prediction["prediction_id"])
               for row in candidate["training_observations"]):
            raise ValueError("A future prediction must not reuse a training prediction")
        applied[target] = forecast_response(candidate, predicted_mean=baseline[target], cohort=cohort)
        selected[target] = applied[target]["predicted_mean"]
    _means(selected, cap)
    contract = {
        "schema_version": CONTRACT_SCHEMA, "measurement_scope": SCOPE,
        "aggregation": AGGREGATION, "targets": list(TARGETS),
        "prediction_id": _prediction_id(prediction["prediction_id"]),
        **{key: cohort[key] for key in ("provider", "model", "model_version", "configuration_hash")},
        **{"predicted_" + key: selected[key] for key in TARGETS},
    }
    evidence = {
        "schema_version": FORECAST_SCHEMA, "classification": "modeled",
        "configuration": configuration, "cohort": cohort, "baseline": baseline,
        "baseline_source": source, "candidates": candidates, "adjustments": applied,
        "selected": selected, "response_prediction_contract": contract,
        "calibration_applied": bool(applied), "held_out_improvement_proven": False,
        "quality_acceptance": False, "operational_promotion": False,
        "historical_forecast_mutated": False, "full_task_learning": False,
    }
    if experiment is not None:
        evidence["experiment"] = normalize_response_experiment(experiment)
    evidence["content_hash"] = content_hash(evidence)
    return copy.deepcopy(evidence)


def validate_response_forecast(value, *, prediction):
    """Recompute all bindings and candidate applications, not merely the hash."""
    try:
        expected = build_response_forecast(
            prediction=prediction, configuration=value["configuration"],
            baseline=value["baseline"], source=value["baseline_source"], candidates=value["candidates"],
            experiment=value.get("experiment"),
        )
        if value != expected or content_hash(value) != content_hash(expected):
            raise ValueError("Response forecast evidence integrity mismatch")
        return value
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid prospective response forecast") from exc


def validate_receipt_response(receipt):
    """Optional extension on historical receipt versions; both fields are atomic."""
    present = {"response_forecast", "response_prediction_contract"} & set(receipt)
    if not present:
        return
    if present != {"response_forecast", "response_prediction_contract"}:
        raise ValueError("Response receipt requires both forecast evidence and contract")
    forecast = validate_response_forecast(receipt["response_forecast"], prediction=receipt["prediction"])
    if content_hash(receipt["response_prediction_contract"]) != content_hash(forecast["response_prediction_contract"]):
        raise ValueError("Receipt response contract differs from selected forecast")
    created = instant(receipt.get("created_at"))
    if created is None:
        raise ValueError("Prospective response receipt requires a creation timestamp")
    for candidate in forecast["candidates"].values():
        trained = instant(candidate["trained_through"])
        if trained is None or trained >= created:
            raise ValueError("Response receipt must be later than candidate training")


def validate_response_dispatch(
    receipt: dict, *, configuration: dict, now: str | datetime | None = None,
) -> dict | None:
    """Reject response configuration drift BEFORE the first provider dispatch.

    Advances predict -> admit -> execute binding checks, not execution authority.
    Pass the immutable PlanStore receipt and response_configuration(server_probe)
    built from the current agent/retrieval, authoritative policy, catalog pricing
    and effective measurement cap. Call after the existing policy authorization
    gate and immediately before inference; a ValueError must prevent dispatch.
    Neither arguments nor ``now`` may originate from browser-controlled assertions.

    Returns a detached exact response-prediction-contract.v1, or None for a
    hash-valid historical receipt with no response intent. No receipt is upgraded,
    written, or admitted here. Probe freshness, policy expiry, quality acceptance
    and separately authorized execution remain the runner's responsibilities.
    """
    try:
        _identity(receipt["receipt_id"])
        expected_hash = _sha(receipt["content_hash"])
        snapshot = {key: value for key, value in receipt.items() if key not in ("receipt_id", "content_hash")}
        if content_hash(snapshot) != expected_hash:
            raise ValueError("Response dispatch receipt content integrity mismatch")
        validate_receipt_response(receipt)
        if "response_forecast" not in receipt:
            return None
        if (receipt.get("schema_version") not in {"2.0", "3.0", "4.0", "5.0", "6.0"}
                or receipt["receipt_id"] != "plan_" + expected_hash[:20]):
            raise ValueError("Response dispatch requires an immutable PlanStore receipt")
        current = validate_configuration(configuration)
        forecast = receipt["response_forecast"]
        if content_hash(current) != content_hash(forecast["configuration"]):
            raise ValueError("Response dispatch configuration drift from immutable forecast")
        dispatch_at = instant(datetime.now(timezone.utc) if now is None else now)
        if dispatch_at is None or instant(receipt["created_at"]) >= dispatch_at:
            raise ValueError("Response forecast must precede timezone-aware dispatch time")
        return copy.deepcopy(receipt["response_prediction_contract"])
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError("Invalid response dispatch binding") from exc
