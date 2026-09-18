"""Immutable response-only evidence; never writes complete-task predictor history.

Advances reconcile -> learn using validated receipts, sealed workload batches and
performance-observation.v1. Measured provider usage is not accepted-task economics,
billing, segment-quality evidence or calibrated tail risk. This control-plane
module grants no execution, acceptance, publication or policy authority.

Future *server-created immutable receipts* may include response_prediction_contract:
    {
      "schema_version": "response-prediction-contract.v1",
      "measurement_scope": "provider_response",
      "aggregation": "mean_per_completed_response",
      "targets": ["input_tokens_mean", "output_tokens_mean"],
      "prediction_id": <same as receipt.prediction.prediction_id>,
      "provider": <provider>, "model": <model>, "model_version": <pinned version>,
      "configuration_hash": content_hash(response_configuration(batch)),
      "predicted_input_tokens_mean": <finite nonnegative number>,
      "predicted_output_tokens_mean": <finite nonnegative number>
    }

The contract must be created at forecast time, included in the validated receipt
hash, and bind exact response targets/configuration. It is NOT a browser boolean
or permission to annotate old receipts after observing actuals. Without it,
generic per-call estimates remain diagnostic, never calibration samples.
Configuration pins agent/retrieval, policy, pricing and measurement output cap.
The adapter remains responsible for validating the workload's own schema.
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from uuid import uuid4

from .atomic_publish import publish_immutable

from .performance_review import content_hash, identity, instant, policy_projection

SCHEMA = "response-usage-observation.v1"
CONTRACT_SCHEMA = "response-prediction-contract.v1"
SCOPE = "provider_response"
AGGREGATION = "mean_per_completed_response"
TARGETS = ("input_tokens_mean", "output_tokens_mean")
MINIMUM_SAMPLES = 10
RECORD_FIELDS = {
    "schema_version", "plan_id", "report_id", "receipt_id", "receipt_hash", "run_id", "batch_hash",
    "started_at", "ended_at", "prediction_id", "provider", "model", "model_version", "archetype",
    "scope", "aggregation", "target", "classification", "predicted_scope", "predicted", "observed",
    "questions_count", "questions_completed", "usage_status", "execution_status",
    "response_model_allocation_usd", "allocation_scope", "configuration", "configuration_hash",
    "response_prediction_contract", "calibration_applied", "historical_forecast_mutated",
    "quality_status", "operational_promotion", "cohort", "eligibility", "observation_id", "content_hash",
}


def _number(value, *, optional=False):
    if value is None and optional:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("Response usage requires finite nonnegative numbers, not booleans")
    return value


def _identity(value, *, optional=False):
    if value is None and optional:
        return None
    if identity(value) is None:
        raise ValueError("Invalid response evidence identity")
    return value


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch("[a-f0-9]{64}", value):
        raise ValueError("Invalid response evidence content hash")
    return value


def _prediction_id(value):
    if type(value) is int and value >= 0:
        return value
    return _identity(value)


def _fields(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError("Unexpected or missing response evidence fields")


def _time(value, *, optional=False):
    if value is None and optional:
        return None
    parsed = instant(value)
    if parsed is None:
        raise ValueError("Response evidence requires timezone-aware timestamps")
    return parsed.isoformat()


def response_configuration(batch: dict) -> dict:
    """Return a content-free, versioned configuration projection for exact binding.

    Hash this return value with the repository's canonical SHA256 content_hash.
    Future forecast producers must know/pin this configuration before execution.
    Missing provenance in blocked attempts is retained as unavailable, never filled
    from a current mutable policy, catalog, or agent.
    """
    try:
        complete = batch["execution_status"] == "completed"
        agent = batch["agent"]
        projected_agent = {
            key: _identity(agent.get(key), optional=not complete)
            for key in ("agent_name", "agent_version", "deployment", "model", "model_version", "retrieval_mode")
        }
        retrieval = agent.get("retrieval_evidence")
        projected_agent["retrieval_configuration_hash"] = (
            _sha(retrieval["content_hash"]) if retrieval is not None else None
        )
        policy = policy_projection(batch["policy"])
        if policy is None and complete:
            raise ValueError("Complete observations require pinned Azure policy provenance")
        source = batch.get("pricing")
        pricing = None
        if source is not None:
            pricing = {key: _identity(source.get(key)) for key in (
                "schema_version", "revision", "catalog_schema", "model", "model_version", "provider",
            )}
            pricing["catalog_hash"] = _sha(source.get("catalog_hash"))
            pricing["rates_per_million"] = {
                key: _number(source["rates_per_million"].get(key), optional=key == "cache_write")
                for key in ("input", "cached_input", "cache_write", "output")
            }
        if complete and pricing is None:
            raise ValueError("Complete observations require pinned pricing")
        measurement = batch.get("measurement_authorization")
        cap = _number(measurement.get("max_output_tokens")) if measurement else None
        if complete and (cap is None or cap <= 0):
            raise ValueError("Complete observations require a positive measurement output cap")
        return {
            "schema_version": "response-configuration.v1", "agent": projected_agent,
            "policy": policy, "pricing": pricing,
            "measurement": {
                "schema_version": _identity(measurement.get("schema_version")) if measurement else None,
                "max_output_tokens": cap,
            },
        }
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Incomplete response configuration") from exc


def _contract(receipt, configuration, prediction):
    value = receipt.get("response_prediction_contract")
    if value is None:
        return None
    expected = {
        "schema_version": CONTRACT_SCHEMA, "measurement_scope": SCOPE,
        "aggregation": AGGREGATION, "targets": list(TARGETS),
        "prediction_id": prediction["prediction_id"], "provider": prediction["provider"],
        "model": prediction["model"], "model_version": configuration["agent"]["model_version"],
        "configuration_hash": content_hash(configuration),
    }
    if not isinstance(value, dict) or set(value) != set(expected) | {
        "predicted_input_tokens_mean", "predicted_output_tokens_mean",
    }:
        raise ValueError("Incomplete response prediction contract")
    if any(value[key] != item or type(value[key]) is not type(item) for key, item in expected.items()):
        raise ValueError("Response prediction contract does not bind this exact scope/configuration")
    return {
        **expected,
        **{f"predicted_{key}": _number(value[f"predicted_{key}"]) for key in TARGETS},
    }


def _eligibility(record):
    if (record["usage_status"] != "complete" or record["execution_status"] != "completed"
            or record["questions_completed"] != record["questions_count"]
            or record["questions_completed"] == 0):
        return "usage_unavailable"
    if record["response_prediction_contract"] is None:
        return "scope_compatibility_pending"
    if any(record["observed"][key] is None or record["observed"][key] <= 0 for key in TARGETS):
        return "target_usage_unavailable"
    if any(record["predicted"][key] is None or record["predicted"][key] <= 0 for key in TARGETS):
        return "nonpositive_prediction"
    return "eligible"


def _observation_id(record):
    return "response-" + content_hash({
        key: record[key] for key in ("prediction_id", "receipt_id", "receipt_hash", "run_id", "batch_hash")
    })


def build_response_observation(*, receipt: dict, batch: dict, observation: dict) -> dict:
    """Project validated adapter inputs; reject identity, usage or scope mismatches.

    Returns response-usage-observation.v1 with observation_id/content_hash,
    receipt/report/plan/run/batch identities, model/version/cohort, pinned
    configuration, numeric predicted/observed dictionaries, eligibility and
    explicit response-only boundaries. No question, prompt, answer or tool body
    is copied. Replays have identical identities; one prediction reused for
    multiple batches is still at most one independent calibration sample.
    """
    try:
        return _build(receipt, batch, observation)
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError("Incomplete response learning evidence") from exc


def _build(receipt, batch, observation):
    if (batch["schema_version"] != "rag-agent-batch.v2"
            or observation["schema_version"] != "performance-observation.v1"
            or observation["source_schema"] != batch["schema_version"]):
        raise ValueError("Unsupported response observation source")
    batch_hash = _sha(batch["evidence"]["content_hash"])
    if batch_hash != content_hash({k: v for k, v in batch.items() if k != "evidence"}):
        raise ValueError("Sealed batch content hash mismatch")
    refs = {key: _identity(receipt[key]) for key in ("plan_id", "report_id", "receipt_id")}
    refs["receipt_hash"] = _sha(receipt["content_hash"])
    if refs["receipt_hash"] != content_hash({
        key: value for key, value in receipt.items() if key not in ("receipt_id", "content_hash")
    }):
        raise ValueError("Receipt content hash does not bind the response forecast contract")
    refs["run_id"] = _identity(batch["run_id"])
    refs["batch_hash"] = batch_hash
    if (any(batch[key] != refs[key] for key in ("plan_id", "report_id"))
            or batch["prediction"] != {"receipt_id": refs["receipt_id"], "content_hash": refs["receipt_hash"]}
            or observation["run_id"] != refs["run_id"] or observation["evidence_hash"] != batch_hash):
        raise ValueError("Response observation receipt/report/run binding mismatch")
    prediction = receipt["prediction"]
    provider, model = _identity(prediction["provider"]), _identity(prediction["model"])
    prediction_id = _prediction_id(prediction["prediction_id"])
    configuration = response_configuration(batch)
    if batch["agent"]["model"] not in (None, model):
        raise ValueError("Response observation model mismatch")
    pricing = configuration["pricing"]
    if pricing and (
        pricing["provider"] != provider or pricing["model"] != model
        or pricing["model_version"] != configuration["agent"]["model_version"]
        or pricing["revision"] != prediction.get("pricing_version")
    ):
        raise ValueError("Response observation pricing/model provenance mismatch")
    normalized_pricing = observation.get("pricing")
    if pricing is None:
        if normalized_pricing is not None:
            raise ValueError("Unexpected normalized response pricing")
    elif not isinstance(normalized_pricing, dict) or any(
        normalized_pricing.get(key) != value for key, value in pricing.items()
    ):
        raise ValueError("Normalized response pricing differs from sealed evidence")
    if normalized_pricing:
        for key, value in normalized_pricing["rates_per_million"].items():
            _number(value, optional=key == "cache_write")
    count = _number(batch["questions_count"])
    if type(count) is not int or count < 1 or len(batch["metrics"]) != count:
        raise ValueError("Invalid question count")
    metrics = [row for row in batch["metrics"] if row["status"] != "not_dispatched"]
    completed = sum(row["status"] == "completed" for row in metrics)
    for key, expected in (("questions_count", count), ("questions_completed", completed)):
        if type(observation[key]) is not int or observation[key] != expected:
            raise ValueError("Response completion count mismatch")
    actual = {}
    for name in ("input_tokens", "output_tokens"):
        values = [
            None if "invalid_provider_usage" in row.get("coverage_notes", [])
            else _number(row.get(name), optional=True) for row in metrics
        ]
        total = sum(values) if values and all(value is not None for value in values) else None
        expected = {name: total, name + "_mean": total / len(metrics) if total is not None else None}
        for key, value in expected.items():
            normalized = _number(observation[key], optional=True)
            if normalized != value:
                raise ValueError("Response usage aggregate does not match sealed batch")
        actual.update(expected)
    usage_status = "unavailable" if not metrics else "complete" if all(
        actual[key] is not None for key in ("input_tokens", "output_tokens")
    ) else "partial"
    if observation["usage_status"] != usage_status or observation["execution_status"] != batch["execution_status"]:
        raise ValueError("Response usage/execution status mismatch")
    times = {}
    for key in ("started_at", "ended_at"):
        times[key] = _time(batch.get(key), optional=not metrics)
        if _time(observation.get(key), optional=not metrics) != times[key]:
            raise ValueError("Response observation time mismatch")
    if times["started_at"] and times["ended_at"] and instant(times["ended_at"]) < instant(times["started_at"]):
        raise ValueError("Response observation end precedes start")
    measurement = observation.get("measurement")
    normalized_cap = _number(measurement.get("max_output_tokens"), optional=True) if measurement else None
    if normalized_cap != configuration["measurement"]["max_output_tokens"]:
        raise ValueError("Response measurement cap mismatch")
    contract = _contract(receipt, configuration, prediction)
    forecast = receipt.get("response_forecast")
    if forecast is not None:
        from .response_forecasts import validate_receipt_response

        validate_receipt_response(receipt)
        if (forecast["configuration"] != configuration or times["started_at"] is None
                or instant(receipt["created_at"]) >= instant(times["started_at"])):
            raise ValueError("Response receipt must predate execution and bind its exact configuration")
    tokens = prediction.get("tokens_per_call", {})
    inputs = [_number(tokens.get(key), optional=True)
              for key in ("text_input", "document_input", "image_input", "audio_input")]
    known = [value for value in inputs if value is not None]
    predicted = {
        "input_tokens_mean": sum(known) if known else None,
        "output_tokens_mean": _number(tokens.get("text_output"), optional=True),
    } if contract is None else {key: contract["predicted_" + key] for key in TARGETS}
    complete = usage_status == "complete" and completed == count and batch["execution_status"] == "completed"
    if not complete:
        actual = {key: None for key in actual}
    allocation = _number(observation.get("observed_model_allocation_usd"), optional=True)
    allocations = [
        _number(row.get("model_allocation_usd"), optional=True)
        for metric, row in zip(batch["metrics"], batch.get("allocations", []))
        if metric["status"] != "not_dispatched"
    ]
    expected_allocation = sum(allocations) if (
        metrics and len(allocations) == len(metrics) and all(value is not None for value in allocations)
    ) else None
    if allocation != expected_allocation:
        raise ValueError("Response model allocation does not match sealed batch")
    record = {
        "schema_version": SCHEMA, **refs, **times,
        "prediction_id": prediction_id, "provider": provider, "model": model,
        "model_version": configuration["agent"]["model_version"],
        "archetype": _identity(prediction.get("archetype"), optional=True),
        "scope": SCOPE, "aggregation": AGGREGATION, "target": list(TARGETS),
        "classification": "measured_response_usage" if complete else "unavailable",
        "predicted_scope": SCOPE if contract else "generic_model_call_unproven",
        "predicted": predicted, "observed": actual, "questions_count": count,
        "questions_completed": completed, "usage_status": usage_status,
        "execution_status": batch["execution_status"],
        "response_model_allocation_usd": allocation,
        "allocation_scope": "response_model_only_not_billed_task_total",
        "configuration": configuration, "configuration_hash": content_hash(configuration),
        "response_prediction_contract": contract,
        "calibration_applied": False, "historical_forecast_mutated": False,
        "quality_status": "not_evaluated", "operational_promotion": False,
    }
    record["cohort"] = {key: record[key] for key in (
        "scope", "aggregation", "provider", "model", "model_version", "archetype", "configuration_hash",
    )}
    if forecast is not None:
        record["response_forecast"] = forecast
        record["calibration_applied"] = forecast["calibration_applied"]
    record["eligibility"] = _eligibility(record)
    record["observation_id"] = _observation_id(record)
    record["content_hash"] = content_hash(record)
    validate_response_observation(record)
    return record


def validate_response_observation(record: dict) -> dict:
    """Validate an immutable observation read from a trusted append-only store."""
    try:
        _fields(record, RECORD_FIELDS | ({"response_forecast"} if "response_forecast" in record else set()))
        if (record["schema_version"] != SCHEMA
                or record["content_hash"] != content_hash({k: v for k, v in record.items() if k != "content_hash"})
                or record["observation_id"] != _observation_id(record)
                or record["scope"] != SCOPE or record["aggregation"] != AGGREGATION
                or record["target"] != list(TARGETS)):
            raise ValueError("Response evidence identity/content integrity check failed")
        for key in ("plan_id", "report_id", "receipt_id", "run_id", "provider", "model"):
            _identity(record[key])
        for key in ("receipt_hash", "batch_hash", "configuration_hash"):
            _sha(record[key])
        _prediction_id(record["prediction_id"])
        _identity(record["model_version"], optional=record["execution_status"] != "completed")
        _identity(record["archetype"], optional=True)
        for key in ("questions_count", "questions_completed"):
            if type(record[key]) is not int or record[key] < 0:
                raise ValueError("Invalid response evidence question counts")
        if record["questions_count"] < 1 or record["questions_completed"] > record["questions_count"]:
            raise ValueError("Invalid response evidence completion count")
        if record["usage_status"] not in ("complete", "partial", "unavailable"):
            raise ValueError("Invalid response usage status")
        if record["execution_status"] not in ("completed", "partial", "blocked"):
            raise ValueError("Invalid response execution status")
        for key in ("started_at", "ended_at"):
            _time(record[key], optional=record["execution_status"] != "completed")
        if record["started_at"] and record["ended_at"] and instant(record["ended_at"]) < instant(record["started_at"]):
            raise ValueError("Response evidence timestamp order mismatch")
        configuration = record["configuration"]
        _fields(configuration, ("schema_version", "agent", "policy", "pricing", "measurement"))
        if configuration["schema_version"] != "response-configuration.v1":
            raise ValueError("Invalid response configuration schema")
        _fields(configuration["agent"], (
            "agent_name", "agent_version", "deployment", "model", "model_version",
            "retrieval_mode", "retrieval_configuration_hash",
        ))
        for key, value in configuration["agent"].items():
            if key == "retrieval_configuration_hash":
                if value is not None:
                    _sha(value)
            else:
                _identity(value, optional=record["execution_status"] != "completed")
        if (configuration["agent"]["model"] not in (None, record["model"])
                or configuration["agent"]["model_version"] != record["model_version"]):
            raise ValueError("Response model/configuration binding mismatch")
        policy = configuration["policy"]
        if policy is not None and policy_projection(policy) != policy:
            raise ValueError("Invalid response policy projection")
        pricing = configuration["pricing"]
        if pricing is not None:
            _fields(pricing, ("schema_version", "revision", "catalog_schema", "model", "model_version",
                              "provider", "catalog_hash", "rates_per_million"))
            for key in ("schema_version", "revision", "catalog_schema", "model", "model_version", "provider"):
                _identity(pricing[key])
            _sha(pricing["catalog_hash"])
            _fields(pricing["rates_per_million"], ("input", "cached_input", "cache_write", "output"))
            for key, value in pricing["rates_per_million"].items():
                _number(value, optional=key == "cache_write")
            if any(pricing[key] != record[key] for key in ("provider", "model", "model_version")):
                raise ValueError("Response pricing binding mismatch")
        _fields(configuration["measurement"], ("schema_version", "max_output_tokens"))
        cap = _number(configuration["measurement"]["max_output_tokens"], optional=True)
        _identity(configuration["measurement"]["schema_version"], optional=cap is None)
        if record["execution_status"] == "completed" and (policy is None or pricing is None or cap is None or cap <= 0):
            raise ValueError("Missing completed response configuration provenance")
        if record["configuration_hash"] != content_hash(record["configuration"]):
            raise ValueError("Response configuration integrity check failed")
        expected_cohort = {key: record[key] for key in (
            "scope", "aggregation", "provider", "model", "model_version", "archetype", "configuration_hash",
        )}
        if record["cohort"] != expected_cohort or record["eligibility"] != _eligibility(record):
            raise ValueError("Response cohort/eligibility integrity check failed")
        for group in ("predicted", "observed"):
            _fields(record[group], TARGETS if group == "predicted" else (*TARGETS, "input_tokens", "output_tokens"))
            for value in record[group].values():
                _number(value, optional=True)
        complete = (record["usage_status"] == "complete" and record["execution_status"] == "completed"
                    and record["questions_completed"] == record["questions_count"])
        if complete:
            for key in TARGETS:
                total = _number(record["observed"][key.removesuffix("_mean")])
                mean = _number(record["observed"][key])
                if not math.isclose(total, mean * record["questions_completed"], rel_tol=1e-12):
                    raise ValueError("Response observed total/mean mismatch")
        elif any(value is not None for value in record["observed"].values()):
            raise ValueError("Incomplete response usage must remain unavailable")
        _number(record["response_model_allocation_usd"], optional=True)
        if record["response_prediction_contract"] is not None:
            contract = _contract(
                {"response_prediction_contract": record["response_prediction_contract"]},
                record["configuration"], record,
            )
            if record["predicted"] != {key: contract["predicted_" + key] for key in TARGETS}:
                raise ValueError("Response forecast target mismatch")
        if (record["predicted_scope"] != (SCOPE if record["response_prediction_contract"] else "generic_model_call_unproven")
                or record["classification"] != ("measured_response_usage" if complete else "unavailable")
                or record["allocation_scope"] != "response_model_only_not_billed_task_total"
                or record["quality_status"] != "not_evaluated"):
            raise ValueError("Response observation scope/classification mismatch")
        applied = False
        if "response_forecast" in record:
            from .response_forecasts import validate_response_forecast

            forecast = validate_response_forecast(
                record["response_forecast"],
                prediction={**record, "pricing_version": configuration["pricing"]["revision"]},
            )
            if (forecast["configuration"] != configuration
                    or forecast["response_prediction_contract"] != record["response_prediction_contract"]):
                raise ValueError("Response observation forecast binding mismatch")
            applied = forecast["calibration_applied"]
        if record["calibration_applied"] is not applied or any(record[key] is not False for key in (
            "historical_forecast_mutated", "operational_promotion",
        )):
            raise ValueError("Observations cannot assert unbound calibration or authority")
        return record
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError("Invalid response usage evidence") from exc


class ResponseLearningStore:
    """Hash-verified append-only local store with atomic, no-clobber publication."""

    def __init__(self, root):
        self.root = Path(root)

    def _path(self, observation_id):
        if not isinstance(observation_id, str) or not re.fullmatch("response-[a-f0-9]{64}", observation_id):
            raise ValueError("Invalid response observation store identifier")
        path = self.root / (observation_id + ".json")
        if path.is_symlink() or not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("Response evidence path escapes store")
        return path

    def get(self, observation_id):
        path = self._path(observation_id)
        try:
            record = validate_response_observation(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError as exc:
            raise KeyError(observation_id) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Unreadable response learning evidence") from exc
        if record["observation_id"] != observation_id:
            raise ValueError("Response evidence filename mismatch")
        return record

    def list(self):
        if not self.root.exists():
            return []
        return [self.get(path.stem) for path in sorted(self.root.glob("*.json"))]

    def append(self, observation):
        payload = json.loads(json.dumps(observation, allow_nan=False))
        validate_response_observation(payload)
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(payload["observation_id"])

        def existing():
            value = self.get(payload["observation_id"])
            if value != payload:
                raise ValueError("Immutable response observation identity collision")
            return value, False

        if path.exists():
            return existing()
        staging = self.root / ("." + uuid4().hex + ".pending")
        try:
            with staging.open("x", encoding="utf-8") as stream:
                json.dump(payload, stream, sort_keys=True, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                publish_immutable(staging, path)
            except FileExistsError:
                return existing()
        finally:
            staging.unlink(missing_ok=True)
        return self.get(payload["observation_id"]), True

    def _artifact_path(self, kind, identifier):
        if kind not in {"candidate", "evaluation", "input"} or not isinstance(identifier, str) or not re.fullmatch(
            ("response" if kind == "input" else kind) + "-[a-f0-9]{64}", identifier
        ):
            raise ValueError("Invalid response learning artifact identity")
        path = self.root / (kind + "s") / (identifier + ".json")
        if path.is_symlink() or not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("Response learning artifact path escapes store")
        return path

    def _append_artifact(self, kind, payload):
        identifier = payload["observation_id" if kind == "input" else kind + "_id"]
        path = self._artifact_path(kind, identifier)
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.parent / ("." + uuid4().hex + ".pending")
        try:
            with staging.open("x", encoding="utf-8") as stream:
                json.dump(payload, stream, sort_keys=True, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                publish_immutable(staging, path)
            except FileExistsError:
                if json.loads(path.read_text(encoding="utf-8")) != payload:
                    raise ValueError("Immutable response learning artifact collision")
        finally:
            staging.unlink(missing_ok=True)
        return payload

    def _read_artifact(self, kind, identifier):
        try:
            value = json.loads(self._artifact_path(kind, identifier).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise KeyError(identifier) from exc
        if (value["content_hash"] != content_hash({k: v for k, v in value.items() if k != "content_hash"})
                or (kind != "input" and identifier != kind + "-" + content_hash({
                    k: v for k, v in value.items() if k not in ("content_hash", kind + "_id")
                }))):
            raise ValueError("Response learning artifact integrity mismatch")
        return value

    def _inputs(self, observation_ids, cohort):
        records = [self.get(identifier) for identifier in observation_ids]
        seen = {key: set() for key in ("prediction_id", "receipt_id", "receipt_hash", "run_id", "batch_hash")}
        for record in records:
            if record["cohort"] != cohort or record["eligibility"] != "eligible":
                raise ValueError("Learning requires eligible exact-cohort observations")
            if any(str(record[key]) in seen[key] for key in seen):
                raise ValueError("Overlapping response learning observations")
            for key in seen:
                seen[key].add(str(record[key]))
        return [_baseline_record(record) for record in records]

    def fit_candidate(self, observation_ids, *, cohort, target="output_tokens_mean"):
        """Fit/register immutable advisory evidence from explicit stored IDs.

        Ten independent positive varying forecasts is an implementation minimum,
        not proof of representative quality, savings or risk calibration.
        """
        from future_token_predictor.history.response_calibration import fit_response_candidate

        records = self._inputs(observation_ids, cohort)
        candidate = fit_response_candidate(records, cohort=cohort, target=target)
        for record in records:
            self._append_artifact("input", record)
        return self._append_artifact("candidate", candidate)

    def _verified_inputs(self, references):
        records = []
        for reference in references:
            value = self._read_artifact("input", reference["observation_id"])
            original = self.get(reference["observation_id"])
            if value != _baseline_record(original) or any(value[key] != expected for key, expected in reference.items()):
                raise ValueError("Learning input differs from immutable baseline/source evidence")
            records.append(value)
        return records

    def get_candidate(self, candidate_id):
        from future_token_predictor.history.response_calibration import fit_response_candidate

        candidate = self._read_artifact("candidate", candidate_id)
        records = self._verified_inputs(candidate["training_observations"])
        expected = fit_response_candidate(records, cohort=candidate["cohort"], target=candidate["target"])
        if candidate != expected:
            raise ValueError("Response candidate differs from its immutable learning inputs")
        return candidate

    def candidate_experiments(self, candidate_id):
        """Return verified original training experiment refs for operator checks.

        Each row has observation_id, observation_hash (the source observation,
        NOT its baseline projection), receipt_id, receipt_hash and experiment.
        Missing prospective experiment refs fail closed; they cannot be supplied
        retrospectively. The workload adapter must verify membership and reject
        case/family leakage before dispatch/evaluation. Distinct forecast IDs
        alone never establish independent question families.
        """
        candidate = self.get_candidate(candidate_id)
        if not candidate["training_observations"]:
            raise ValueError("Candidate has no pinned training experiment evidence")
        result = []
        for reference in candidate["training_observations"]:
            observation = self.get(reference["observation_id"])
            experiment = observation.get("response_forecast", {}).get("experiment")
            if experiment is None:
                raise ValueError("Candidate training observation lacks prospective experiment evidence")
            result.append({
                "observation_id": observation["observation_id"],
                "observation_hash": observation["content_hash"],
                "receipt_id": observation["receipt_id"],
                "receipt_hash": observation["receipt_hash"],
                "experiment": experiment,
            })
        return result

    def evaluate_candidate(self, candidate_id, observation_ids):
        """Append held-out WAPE evidence; never promote or mark a receipt improved.

        Requires independent later observations, with at least three to calculate
        WAPE. Baseline projections ensure already adjusted means are not corrected
        twice. Insufficient, unchanged and worse results remain explicit.
        """
        from future_token_predictor.history.response_calibration import evaluate_response_candidate

        candidate = self.get_candidate(candidate_id)
        records = self._inputs(observation_ids, candidate["cohort"])
        evaluation = evaluate_response_candidate(candidate, records)
        for record in records:
            self._append_artifact("input", record)
        return self._append_artifact("evaluation", evaluation)

    def get_evaluation(self, evaluation_id):
        from future_token_predictor.history.response_calibration import evaluate_response_candidate

        evaluation = self._read_artifact("evaluation", evaluation_id)
        candidate = self.get_candidate(evaluation["candidate_id"])
        expected = evaluate_response_candidate(candidate, self._verified_inputs(evaluation["held_out_observations"]))
        if evaluation != expected:
            raise ValueError("Response evaluation differs from immutable evidence")
        return evaluation

    def list_evaluations(self):
        return [self.get_evaluation(path.stem)
                for path in sorted((self.root / "evaluations").glob("evaluation-*.json"))]


def _baseline_record(record):
    """Separate immutable predictor input, never rewrite the source observation.

    The unchanged predictor v1 consumes predicted + matching v1 contract. Retain
    exact source hashes in this *derived* projection; its hash differs from the
    observation and candidate references bind that projection in inputs/.
    """
    value = json.loads(json.dumps(validate_response_observation(record), allow_nan=False))
    forecast = value.pop("response_forecast", None)
    if forecast is None:
        return value
    value["predicted"] = dict(forecast["baseline"])
    value["response_prediction_contract"] = {
        **value["response_prediction_contract"],
        **{"predicted_" + key: forecast["baseline"][key] for key in TARGETS},
    }
    value["calibration_applied"] = False
    value["baseline_projection"] = {
        "schema_version": "response-learning-baseline.v1",
        "source_observation_hash": record["content_hash"],
        "source_forecast_hash": forecast["content_hash"],
        "basis": "immutable_pre_execution_baseline",
    }
    value["content_hash"] = content_hash({k: v for k, v in value.items() if k != "content_hash"})
    return value


def summarize_learning(records, *, receipt: dict, observation: dict) -> dict:
    """Selected-run registration plus exact-cohort independent sample readiness.

    At ten eligible independent predictions, the isolated predictor fitter
    produces hashed advisory_candidates keyed by input/output_tokens_mean.
    A usable target sets status=advisory_candidate_ready; degenerate/poor fits
    remain explicit. Candidates may support a FUTURE forecast, but no historical
    receipt is re-estimated here. Counts/fits are not held-out proof: this API
    never claims applied calibration or before/after WAPE improvement.
    """
    selected = validate_response_observation(observation)
    if any(selected[key] != receipt.get(key) for key in ("plan_id", "report_id", "receipt_id")) or (
        selected["receipt_hash"] != receipt.get("content_hash")
        or selected["prediction_id"] != receipt.get("prediction", {}).get("prediction_id")
    ):
        raise ValueError("Selected response observation belongs to a different receipt")
    unique = {}
    for record in records:
        validate_response_observation(record)
        key = record["observation_id"]
        if key in unique and unique[key] != record:
            raise ValueError("Conflicting response observation replay")
        unique[key] = record
    cohort = [value for value in unique.values() if value["cohort"] == selected["cohort"]]
    eligible = []
    seen = {key: set() for key in ("prediction_id", "receipt_id", "receipt_hash", "run_id", "batch_hash")}
    for value in sorted(cohort, key=lambda row: (row["ended_at"] or "", row["observation_id"])):
        if value["eligibility"] != "eligible":
            continue
        if any(str(value[key]) in identifiers for key, identifiers in seen.items()):
            continue
        eligible.append(value)
        for key in seen:
            seen[key].add(str(value[key]))
    registered = selected["observation_id"] in unique
    status = "not_registered" if not registered else selected["eligibility"]
    candidates = {}
    if status == "eligible":
        status = "insufficient_samples"
        if len(eligible) >= MINIMUM_SAMPLES:
            from future_token_predictor.history.response_calibration import fit_response_candidate

            candidates = {
                target: fit_response_candidate(
                    [_baseline_record(record) for record in cohort], cohort=selected["cohort"], target=target,
                )
                for target in TARGETS
            }
            states = {candidate["status"] for candidate in candidates.values()}
            status = "advisory_candidate_ready" if "ready" in states else (
                next(iter(states)) if len(states) == 1 else "no_usable_candidate"
            )
    reasons = {
        "not_registered": "This response observation has not been registered.",
        "scope_compatibility_pending": "Registered diagnostic only: the receipt does not bind an exact provider-response mean forecast.",
        "usage_unavailable": "Incomplete response usage is unavailable for calibration, not zero actual usage.",
        "target_usage_unavailable": "Positive, complete target usage is required for calibration and WAPE.",
        "nonpositive_prediction": "Positive varying response forecasts are required for a nondegenerate fit.",
        "insufficient_samples": "At least ten independent exact-scope prediction records are required; repeated batches do not add predictions.",
        "advisory_candidate_ready": "At least one exact-scope response target has a usable advisory fit for a future forecast. No receipt was changed; later independent held-out evidence is required to claim improvement.",
        "degenerate_predictors": "Independent sample count is sufficient, but response predictors do not vary enough for a usable calibration fit.",
        "poor_fit": "The exact-scope response fits do not meet the existing fit-quality gate; no candidate is usable.",
        "no_usable_candidate": "No response target has a usable fit. Inspect the per-target advisory candidate statuses; no calibration was applied.",
    }
    return {
        "schema_version": "response-learning-summary.v1", "status": status,
        "reason": reasons[status], "registered": registered,
        "observation_id": selected["observation_id"], "run_id": selected["run_id"],
        "receipt_id": selected["receipt_id"], "receipt_hash": selected["receipt_hash"],
        "observation_count": len(cohort), "eligible_sample_count": len(eligible),
        "minimum_samples": MINIMUM_SAMPLES, "calibration_applied": False,
        "before_wape": None, "after_wape": None, "scope": SCOPE,
        "advisory_candidates": candidates,
        "aggregation": AGGREGATION, "target": list(TARGETS),
        "full_task_learning": False, "operational_promotion": False,
    }
