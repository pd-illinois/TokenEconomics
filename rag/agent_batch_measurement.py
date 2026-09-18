"""Separately authorized, incomplete-cost measurement of the managed RAG wrapper.

Advances execute/evaluate/reconcile using immutable forecast, Azure policy and
catalog revisions. Completion is not quality acceptance or operational admission.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import os
import re
import threading
import time
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from uuid import uuid4

from costgov.policy_store import PolicyLoadError, measurement_authorization, validate_measurement_policy
from rag import agent_batch as b

SCHEMA = "rag-agent-batch.v2"
BLOCKERS = {
    **b._BLOCKERS,
    "measurement_authorization_required": "An unexpired, separately reviewed Azure measurement policy is required.",
    "measurement_authorization_expired": "The Azure measurement authorization has expired; a newly reviewed authorization is required.",
    "measurement_authorization_invalid": "The Azure measurement authorization or its containing policy is invalid.",
    "measurement_question_limit": "The batch exceeds the separately authorized question limit.",
    "pricing_unavailable": "Exact receipt-bound model catalog pricing is unavailable.",
}
STOPS = {
    "blocked", "completed", "observed_model_cost_stop", "usage_or_pricing_unavailable",
    "response_failed_or_partial", "elapsed_time_limit", "provider_call_failed",
    "client_unavailable", "measurement_authorization_expired",
    "metric_journal_failed",
    "retrieval_evidence_unavailable",
    "policy_changed_or_unavailable", "agent_changed_or_unavailable",
    "evaluation_capture_failed",
    "response_forecast_mismatch",
}
_PRIVATE_CALL = ContextVar("rag_metrics_private_call", default=False)
_PREFLIGHT_LOCK = threading.Lock()
_ACTIVE_PREFLIGHTS = set()


class _PrivateCallFilter(logging.Filter):
    def filter(self, record):
        return not _PRIVATE_CALL.get()


for _name in ("openai._base_client", "azure.ai.projects._patch"):
    logging.getLogger(_name).addFilter(_PrivateCallFilter())


@contextmanager
def private_call():
    """Suppress instrumentation context-locally, including SDK debug bodies."""
    token = _PRIVATE_CALL.set(True)
    context_token = None
    try:
        try:
            from opentelemetry import context
            from opentelemetry.context import _SUPPRESS_INSTRUMENTATION_KEY
            context_token = context.attach(context.set_value(_SUPPRESS_INSTRUMENTATION_KEY, True))
        except ImportError:
            pass
        yield
    finally:
        if context_token is not None:
            context.detach(context_token)
        _PRIVATE_CALL.reset(token)


@contextmanager
def _preflight_reservation(claim):
    key = str(claim.resolve())
    with _PREFLIGHT_LOCK:
        if key in _ACTIVE_PREFLIGHTS:
            raise ValueError("Batch identity is already reserved; it will not be retried")
        _ACTIVE_PREFLIGHTS.add(key)
    try:
        yield
    finally:
        with _PREFLIGHT_LOCK:
            _ACTIVE_PREFLIGHTS.discard(key)


@contextmanager
def clients(config):
    from azure.ai.projects import AIProjectClient
    from openai import DefaultHttpxClient

    with private_call(), ExitStack() as stack:
        credential = stack.enter_context(b._credential())
        project = stack.enter_context(AIProjectClient(
            endpoint=config["project_endpoint"], credential=credential,
            retry_total=0, connection_timeout=10, read_timeout=15,
            logging_enable=False, tracing_enable=False,
        ))
        transport = stack.enter_context(DefaultHttpxClient(
            follow_redirects=False, timeout=config["timeout_seconds"],
        ))
        client = stack.enter_context(project.get_openai_client(
            max_retries=0, timeout=config["timeout_seconds"], http_client=transport,
        ))
        yield client


def _agent(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid agent projection")
    legacy = copy.deepcopy(value)
    retrieval = legacy.pop("retrieval_evidence", None)
    verified = legacy.get("retrieval_mode") == "managed_mcp_hybrid_verified"
    if verified:
        legacy["retrieval_mode"] = "managed_mcp_hybrid_unverified"
    b._validate_agent(legacy)
    if verified:
        from rag.agent_batch_probe import validate_retrieval
        validate_retrieval(retrieval)
        if not value["managed_books_mcp"]:
            raise ValueError("Managed binding required")
    elif retrieval is not None:
        raise ValueError("Unverified retrieval cannot carry verified evidence")
    return value


def _authorization_failure_code(loaded):
    if loaded is None:
        return "measurement_authorization_required"
    document = getattr(loaded, "document", {})
    provenance = getattr(loaded, "provenance", {})
    if (not isinstance(document, dict) or not isinstance(provenance, dict)
            or document.get("status") != "active"
            or provenance.get("source") != "azure_app_configuration"
            or not all(provenance.get(key) for key in ("etag", "label", "endpoint", "key"))):
        return "azure_authority_required"
    if "measurement" not in document:
        return "measurement_authorization_required"
    try:
        authorization = validate_measurement_policy(document["measurement"])
        if datetime.fromisoformat(authorization["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
            return "measurement_authorization_expired"
    except (PolicyLoadError, ValueError):
        return "measurement_authorization_invalid"
    return "measurement_authorization_invalid"


def connection(*, probe=None, receipt=None, loaded=None):
    config = b.configuration()
    # Legacy connection construction is retained only as an inventory projection.
    captured = {}

    def inventory(configuration):
        agent = _agent((probe or b.probe_agent)(configuration))
        captured.update(agent)
        legacy = {key: agent[key] for key in b._AGENT_FIELDS}
        legacy["retrieval_mode"] = "managed_mcp_hybrid_unverified"
        return legacy

    result = b._connection_status_v1(probe=inventory, receipt=receipt)
    if captured:
        result["public_config"] = captured
        result["retrieval_mode"] = captured["retrieval_mode"]
    # V1's reservation blocker describes the retired hard-cap adapter. V2 instead
    # requires the explicit measurement grant; it never claims a hard spend cap.
    codes = [row["code"] for row in result["blockers"]
             if row["code"] != "hidden_model_cost_bound_unenforceable"]
    if captured.get("retrieval_mode") == "managed_mcp_hybrid_verified":
        codes.remove("retrieval_mode_unverified")
    authorization = None
    if (loaded is not None and result["model"]
            and result["model"] not in loaded.document.get("admission", {}).get("allowed_models", [])):
        codes.append("actual_model_not_allowed")
    if loaded is None:
        codes.append("measurement_authorization_required")
    else:
        try:
            authorization = measurement_authorization(loaded)
        except PolicyLoadError:
            codes.append(_authorization_failure_code(loaded))
        else:
            if receipt is not None:
                try:
                    b._policy_binding(receipt, loaded)
                except (PolicyLoadError, ValueError, TypeError, KeyError):
                    codes.append("receipt_policy_rejected")
    result.update(
        schema_version=SCHEMA, ready=not codes, status="connected" if not codes else "blocked",
        measurement_authorized=authorization is not None,
        measurement_authorization_status=("authorized" if authorization is not None else next((
            status for code, status in (
                ("measurement_authorization_required", "absent"),
                ("measurement_authorization_expired", "expired"),
                ("measurement_authorization_invalid", "invalid"),
                ("azure_authority_required", "authority_unavailable"),
            ) if code in codes
        ), "invalid")),
        blockers=[{"code": code, "message": BLOCKERS[code]} for code in codes],
        message=" ".join(BLOCKERS[code] for code in codes) if codes else
        "Read-only managed hybrid configuration verified; measurement only, with incomplete costs.",
        measurement_authorization=authorization,
        configuration_ready=bool(captured) and not any(code in {
            "agent_read_access_unavailable", "agent_not_prompt", "agent_not_active",
            "agent_model_mismatch", "forecast_required", "managed_books_mcp_unverified",
            "retrieval_mode_unverified",
        } for code in codes),
        authorization_required="measurement_only",
        retrieval_evidence_scope="configuration_only_not_per_query_hybrid_proof",
        storage_warnings=([{
            "code": "evidence_destination_unconfigured",
            "message": "Cloud publication is not configured; evidence remains in the durable local outbox.",
        }] if result["evidence"]["location"] is None else []),
    )
    result["privacy"]["provider_retention"] = (
        "Requests use store=False, no conversation, and disabled content tracing. "
        "This is not a zero-retention guarantee; managed-service retention is a separate boundary."
    )
    if authorization is not None:
        result["limits"].update(
            maximum_questions=authorization["max_questions"],
            maximum_output_tokens=authorization["max_output_tokens"],
            maximum_elapsed_seconds=authorization["max_elapsed_seconds"],
            observed_model_cost_stop_usd=authorization["observed_model_cost_stop_usd"],
            hard_spend_cap_guaranteed=False,
        )
    return result


def _money(value):
    return (not isinstance(value, bool) and isinstance(value, (float, int))
            and math.isfinite(value) and value >= 0)


def reload_policy(binding):
    """Reread the exact Azure authority with bounded, read-only SDK operations."""
    from azure.appconfiguration import AzureAppConfigurationClient
    from costgov.policy_store import LoadedPolicy, validate_policy

    if not re.fullmatch(r"https://[A-Za-z0-9-]+\.azconfig\.io/?", binding["endpoint"]):
        raise ValueError("Azure App Configuration authority required")
    if (os.environ.get("TOKENGOV_POLICY_SOURCE", "azure").lower() != "azure"
            or os.environ.get("AZURE_APPCONFIG_ENDPOINT", "").rstrip("/") != binding["endpoint"].rstrip("/")
            or os.environ.get("TOKENGOV_POLICY_LABEL") != binding["label"]
            or os.environ.get("TOKENGOV_POLICY_KEY", "tokengov:policy") != binding["key"]):
        raise ValueError("Configured Azure authority changed")
    with private_call(), b._credential() as credential:
        with AzureAppConfigurationClient(
            binding["endpoint"], credential, retry_total=0, connection_timeout=10,
            read_timeout=15, logging_enable=False, tracing_enable=False,
        ) as client:
            setting = client.get_configuration_setting(key=binding["key"], label=binding["label"])
    if (not isinstance(setting.value, str) or len(setting.value) > 2_000_000
            or setting.content_type and "json" not in setting.content_type.lower()):
        raise ValueError("Azure policy response invalid")
    document = validate_policy(json.loads(setting.value))
    return LoadedPolicy(document=document, provenance={
        "source": "azure_app_configuration", "endpoint": binding["endpoint"],
        "key": binding["key"], "label": binding["label"],
        "etag": str(setting.etag) if setting.etag is not None else None,
    })


def _run_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"run-[a-f0-9]{32}", value):
        raise ValueError("Invalid batch run identity")
    return value


def prices(root, receipt, agent):
    """Same sourced release file as books_playground; never default model prices."""
    catalog = json.loads((root / "data" / "model_catalogs" / "foundry-model-release.v2.json").read_text())
    prediction = receipt["prediction"]
    if prediction.get("pricing_version") != catalog["release_version"]:
        raise ValueError("Receipt pricing revision mismatch")
    rows = [
        row for group in catalog["model_groups"] if group["provider"] == "azure_openai"
        for row in group["models"] if row["model"] == agent["model"]
    ]
    if len(rows) != 1 or agent["model_version"] not in rows[0]["versions"]:
        raise ValueError("Unique catalog model version required")
    row = rows[0]
    if catalog.get("providers", {}).get("azure_openai", {}).get("evidence", {}).get("status") != "verified":
        raise ValueError("Verified catalog price source required")
    source = row.get("evidence", {})
    if not source.get("price_effective_at") or not (source.get("meters") or source.get("meter_family")):
        raise ValueError("Sourced meter evidence required")
    rates = {key: row["pricing"].get(key) for key in ("input", "cached_input", "cache_write", "output")}
    if any(not _money(rates[key]) or rates[key] <= 0 for key in ("input", "cached_input", "output")):
        raise ValueError("Token pricing unavailable")
    if rates["cache_write"] is not None and not _money(rates["cache_write"]):
        raise ValueError("Invalid cache-write price")
    return {
        "schema_version": "rag-model-allocation-pricing.v1",
        "catalog_schema": catalog["schema_version"], "revision": catalog["release_version"],
        "catalog_hash": b._digest(catalog), "model": agent["model"],
        "model_version": agent["model_version"], "provider": "azure_openai",
        "rates_per_million": rates,
        "allocation_scope": "response_model_only_not_task_total",
        "price_basis": "catalog_public_list_allocation_not_invoice",
        "cache_write_rule": "separate_rate_when_sourced_otherwise_standard_input",
    }


def _validate_prices(value):
    fields = {"schema_version", "catalog_schema", "revision", "catalog_hash", "model",
              "model_version", "provider", "rates_per_million", "allocation_scope", "cache_write_rule", "price_basis"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Pricing projection fields invalid")
    if (value["schema_version"] != "rag-model-allocation-pricing.v1"
            or value["catalog_schema"] != "foundry-model-release.v2"
            or value["provider"] != "azure_openai"
            or value["allocation_scope"] != "response_model_only_not_task_total"
            or value["price_basis"] != "catalog_public_list_allocation_not_invoice"
            or value["cache_write_rule"] != "separate_rate_when_sourced_otherwise_standard_input"
            or not re.fullmatch("[a-f0-9]{64}", value["catalog_hash"])):
        raise ValueError("Pricing binding invalid")
    for key in ("revision", "model", "model_version"):
        b._identifier(value[key])
    rates = value["rates_per_million"]
    if not isinstance(rates, dict) or set(rates) != {"input", "cached_input", "cache_write", "output"}:
        raise ValueError("Price rates invalid")
    if any(not _money(rates[key]) or rates[key] <= 0 for key in ("input", "cached_input", "output")):
        raise ValueError("Required rates unavailable")
    if rates["cache_write"] is not None and not _money(rates["cache_write"]):
        raise ValueError("Cache-write rate invalid")


def allocation(metric, pricing, identity_bound):
    if not identity_bound or pricing is None or "invalid_provider_usage" in metric["coverage_notes"]:
        return None
    inp, out, cached, written = (metric[key] for key in (
        "input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens"))
    if inp is None or out is None or cached is None:
        return None
    rates = pricing["rates_per_million"]
    if written is None and rates["cache_write"] is not None:
        return None
    written = written or 0
    if cached + written > inp:
        return None
    return ((inp - cached - written) * rates["input"] + cached * rates["cached_input"]
            + written * (rates["cache_write"] if rates["cache_write"] is not None else rates["input"])
            + out * rates["output"]) / 1_000_000


def validate_result_v2(result):
    if not isinstance(result, dict):
        raise ValueError("Invalid batch evidence")
    added = {"measurement_authorization", "pricing", "allocations", "observed_model_allocation_usd", "stop_reason"}
    if not added <= set(result) or result.get("schema_version") != SCHEMA:
        raise ValueError("Unsupported batch schema")
    _run_id(result.get("run_id"))
    original = copy.deepcopy(result)
    scope_field = {"retrieval_evidence_scope"} if "retrieval_evidence_scope" in result else set()
    if scope_field and result["retrieval_evidence_scope"] != "configuration_only_not_per_query_hybrid_proof":
        raise ValueError("Per-query hybrid execution is not proven by configuration")
    cost_field = {"failure_cost_note"} if "failure_cost_note" in result else set()
    if cost_field and result["failure_cost_note"] is not None and result["failure_cost_note"] != "provider_failure_usage_may_be_charged":
        raise ValueError("Failure cost notes must use fixed codes")
    legacy = {key: value for key, value in original.items() if key not in added | {"evidence"} | scope_field | cost_field}
    if result["execution_status"] not in {"blocked", "completed", "partial", "failed"}:
        raise ValueError("Invalid execution outcome")
    if result["stop_reason"] not in STOPS:
        raise ValueError("Invalid fixed stop code")
    metrics = result["metrics"]
    if not isinstance(metrics, list):
        raise ValueError("Invalid metrics")
    dispatched = [m for m in metrics if m.get("status") != "not_dispatched"]
    if result["evidence_classification"] != ("measured" if dispatched else "blocked"):
        raise ValueError("Invalid evidence classification")
    if result["execution_status"] == "completed" and (
            len(dispatched) != len(metrics) or any(m["status"] != "completed" for m in metrics)
            or result["stop_reason"] != "completed"):
        raise ValueError("Incomplete batch cannot claim completion")
    if scope_field and result["execution_status"] == "completed" and any(
            m.get("retrieval_calls") is None or m["retrieval_calls"] < 1 for m in metrics):
        raise ValueError("Completed RAG batches require exposed retrieval tool evidence")
    if result["execution_status"] == "blocked" and dispatched:
        raise ValueError("Blocked batch cannot contain calls")
    not_dispatched = False
    for number, metric in enumerate(metrics, 1):
        b.validate_metric(metric)
        if metric["question_number"] != number or (not_dispatched and metric["status"] != "not_dispatched"):
            raise ValueError("Sequential metric ordering required")
        if metric["status"] == "not_dispatched" and any(
                metric[key] is not None for key in b._COUNTS - {"question_number", "input_words", "input_characters"}):
            raise ValueError("Undispatched questions cannot contain response measurements")
        not_dispatched |= metric["status"] == "not_dispatched"
    _agent(result["agent"])
    authorization = result["measurement_authorization"]
    if authorization is not None:
        try:
            validate_measurement_policy(authorization)  # Historical expiry is deliberately not reassessed.
        except PolicyLoadError:
            raise ValueError("Invalid measurement authorization projection") from None
    if dispatched and authorization is None:
        raise ValueError("Dispatched evidence requires measurement authorization")
    if dispatched and len(metrics) > authorization["max_questions"]:
        raise ValueError("Authorized question limit exceeded")
    if result["pricing"] is not None:
        _validate_prices(result["pricing"])
    if dispatched and result["pricing"] is None:
        raise ValueError("Dispatch requires pinned pricing")
    allocations = result["allocations"]
    if not isinstance(allocations, list) or len(allocations) != len(metrics):
        raise ValueError("Allocation count mismatch")
    for metric, row in zip(metrics, allocations):
        if not isinstance(row, dict) or set(row) != {"question_number", "model_identity_bound", "model_allocation_usd"}:
            raise ValueError("Allocation allowlist violated")
        if (type(row["model_identity_bound"]) is not bool
                or type(row["question_number"]) is not int or row["question_number"] != metric["question_number"]):
            raise ValueError("Allocation identity invalid")
        if row["model_identity_bound"] and metric["status"] == "not_dispatched":
            raise ValueError("Undispatched questions cannot bind a response model")
        if row["model_allocation_usd"] != allocation(metric, result["pricing"], row["model_identity_bound"]):
            raise ValueError("Allocation arithmetic mismatch")
    observed = sum(row["model_allocation_usd"] or 0 for row in allocations)
    if not _money(result["observed_model_allocation_usd"]) or not math.isclose(
            observed, result["observed_model_allocation_usd"], rel_tol=1e-12, abs_tol=1e-15):
        raise ValueError("Observed allocation mismatch")
    for row in result["blockers"]:
        if (not isinstance(row, dict) or set(row) != {"code", "message"}
                or row["code"] not in BLOCKERS or row["message"] != BLOCKERS[row["code"]]):
            raise ValueError("Blocker allowlist violated")
    # Reuse the unchanged historical validator for all shared identity/provenance fields.
    legacy.update(schema_version=b.SCHEMA, execution_status="blocked", evidence_classification="blocked",
                  blockers=[{"code": "hidden_model_cost_bound_unenforceable",
                             "message": b._BLOCKERS["hidden_model_cost_bound_unenforceable"]}])
    legacy["agent"] = {key: result["agent"][key] for key in b._AGENT_FIELDS}
    legacy["agent"]["retrieval_mode"] = "managed_mcp_hybrid_unverified"
    # The historical validator remains intentionally capped at ten questions.
    # Validate one legacy-sized projection for shared fields; v2 validates every
    # metric and its full authorized count above.
    legacy["metrics"] = [
        {**metric, "status": "not_dispatched"}
        for metric in metrics[:b.LEGACY_MAX_QUESTIONS]
    ]
    legacy["questions_count"] = len(legacy["metrics"])
    b._validate_result_v1(legacy)
    evidence = result.get("evidence")
    if not isinstance(evidence, dict) or set(evidence) != {"status", "location", "content_hash", "cloud_status"}:
        raise ValueError("Evidence projection invalid")
    if (evidence["status"] != "persisted_locally_cloud_pending"
            or evidence["cloud_status"] != "publication_tracked_separately"
            or evidence["location"] != f"studio_runs/{result['run_id']}/result.json"
            or evidence["content_hash"] != b._digest({k: v for k, v in result.items() if k != "evidence"})):
        raise ValueError("Immutable result integrity mismatch")
    return result


def _exception_response(exc):
    # Never serialize exceptions or provider bodies; consume only a response-shaped
    # object in memory. SDK error unwrapping can remove sibling usage from exc.body.
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return response
    if response is not None:
        try:
            envelope = response.json()
        except (ValueError, TypeError, AttributeError):
            envelope = None
        if isinstance(envelope, dict) and any(
            key in envelope for key in ("usage", "model", "output", "status")
        ):
            return envelope
    body = getattr(exc, "body", None)
    return body if isinstance(body, dict) else {}


def _register(register, result):
    register(result["run_id"], run_id=result["run_id"], report_id=result["report_id"],
             plan_id=result["plan_id"], status=result["execution_status"],
             execution_status=result["execution_status"], result=result)


def execute_batch(service, plan_id, payload, loaded, principal, root, register, *,
                  probe=None, client_factory=None, publisher=None, policy_loader=None,
                  response_observer=None):
    if response_observer is not None and not callable(response_observer):
        raise ValueError("The server-owned response observer must be callable")
    request_id, questions = b._validate_payload(payload)
    plan_id = b._identifier(plan_id)
    if not principal:
        raise ValueError("Separate measurement authorization is required")
    claim = service.run_root / "rag_batch_claims" / f"{request_id}.json"
    # Reopening historical evidence does not reauthorize or repeat execution.
    if claim.exists():
        return _existing(service, claim, request_id, plan_id, register)
    with _preflight_reservation(claim):
        if claim.exists():
            return _existing(service, claim, request_id, plan_id, register)
        receipt = service.receipt(plan_id)
        if (receipt.get("route", {}).get("route_id") != "foundry"
                or receipt.get("confirmed_profile", {}).get("agent_pattern") != "rag_pipeline"
                or receipt.get("prediction", {}).get("provider") != "azure_openai"):
            raise ValueError(b._BLOCKERS["receipt_workload_mismatch"])
        policy = b._policy_binding(receipt, loaded)
        run_id = f"run-{uuid4().hex}"
        started_at, started = b._now(), time.monotonic()
        inventory = connection(probe=probe, receipt=receipt, loaded=loaded)
        authorization = inventory["measurement_authorization"]
        blockers = list(inventory["blockers"])
        pricing = None
        if authorization and len(questions) > authorization["max_questions"]:
            blockers.append({"code": "measurement_question_limit", "message": BLOCKERS["measurement_question_limit"]})
        if not blockers:
            try:
                pricing = prices(root, receipt, inventory["public_config"])
                _validate_prices(pricing)
            except Exception:
                blockers.append({"code": "pricing_unavailable", "message": BLOCKERS["pricing_unavailable"]})
        metrics = [b.question_metrics(question, n) for n, question in enumerate(questions, 1)]
        allocations = [{"question_number": n, "model_identity_bound": False, "model_allocation_usd": None}
                       for n in range(1, len(questions) + 1)]
        result = {
            "schema_version": SCHEMA, "run_id": run_id, "request_id": request_id,
            "plan_id": plan_id, "report_id": receipt["report_id"], "execution_status": "blocked",
            "questions_count": len(questions), "metrics": metrics, "agent": inventory["public_config"],
            "policy": policy, "prediction": {"receipt_id": receipt["receipt_id"], "content_hash": receipt["content_hash"]},
            "blockers": blockers, "started_at": started_at, "ended_at": started_at,
            "evidence_classification": "blocked", "acceptance_status": "not_evaluated",
            "operational_promotion": False, "measurement_authorization": authorization,
            "pricing": pricing, "allocations": allocations, "observed_model_allocation_usd": 0,
            "stop_reason": "blocked",
            "retrieval_evidence_scope": "configuration_only_not_per_query_hybrid_proof",
            "failure_cost_note": None,
        }
        _seal(result)
        validate_result_v2(result)
        result.pop("evidence")
        # Reserve the billable request only after all local/preflight evidence
        # is valid, but before creating clients or provider dispatch.
        try:
            b._write_once(claim, {
                "schema_version": "rag-agent-batch-claim.v1", "request_id": request_id,
                "run_id": run_id, "plan_id": plan_id, "created_at": b._now(),
            })
        except FileExistsError:
            return _existing(service, claim, request_id, plan_id, register)
    directory = service.run_root / run_id
    # Persist the privacy-safe outbox before opening any billable client.
    _publication_record(directory, run_id, "pending", None)
    if not blockers:
        config = {**b.configuration(), "timeout_seconds": min(45, authorization["max_elapsed_seconds"])}
        pinned_config = {**b.configuration(), "agent_version": result["agent"]["agent_version"]}
        stack = ExitStack()
        client = None
        try:
            factory_result = (client_factory or clients)(config)
            if hasattr(factory_result, "__enter__"):
                client = stack.enter_context(factory_result)
            else:
                client = factory_result
            result["stop_reason"] = "completed"
            for index, question in enumerate(questions):
                elapsed = time.monotonic() - started
                if elapsed >= authorization["max_elapsed_seconds"]:
                    result["stop_reason"] = "elapsed_time_limit"
                    break
                if result["observed_model_allocation_usd"] >= authorization["observed_model_cost_stop_usd"]:
                    result["stop_reason"] = "observed_model_cost_stop"
                    break
                try:
                    fresh_agent = _agent((probe or b.probe_agent)(pinned_config))
                    if fresh_agent != result["agent"]:
                        raise ValueError("Pinned deployed configuration changed")
                except Exception:
                    result["stop_reason"] = "agent_changed_or_unavailable"
                    break
                try:
                    fresh_policy = (policy_loader or reload_policy)(policy)
                    if b._policy_binding(receipt, fresh_policy) != policy:
                        raise ValueError("Pinned Azure authority changed")
                except Exception:
                    result["stop_reason"] = "policy_changed_or_unavailable"
                    break
                try:
                    current_authorization = measurement_authorization(fresh_policy)
                    if current_authorization != authorization:
                        raise ValueError("Measurement authorization changed")
                except Exception:
                    result["stop_reason"] = "measurement_authorization_expired"
                    break
                try:
                    from costgov.response_forecasts import validate_response_dispatch
                    from costgov.response_learning import response_configuration

                    validate_response_dispatch(receipt, configuration=response_configuration(result))
                    if index == 0:
                        from rag.qna_experiment import validate_qna_dispatch

                        validate_qna_dispatch(receipt, questions)
                except ValueError:
                    result["stop_reason"] = "response_forecast_mismatch"
                    break
                elapsed = time.monotonic() - started
                if elapsed >= authorization["max_elapsed_seconds"]:
                    result["stop_reason"] = "elapsed_time_limit"
                    break
                call_started = time.monotonic()
                response, failed = None, False
                try:
                    with private_call():
                        response = client.responses.create(
                            input=question, store=False, max_output_tokens=authorization["max_output_tokens"],
                            timeout=min(45, authorization["max_elapsed_seconds"] - elapsed),
                            extra_body={"agent_reference": {
                                "type": "agent_reference", "name": result["agent"]["agent_name"],
                                "version": result["agent"]["agent_version"],
                            }},
                        )
                except Exception as exc:
                    response, failed = _exception_response(exc), True
                    result["failure_cost_note"] = "provider_failure_usage_may_be_charged"
                try:
                    payload = b._mapping(response)
                    metric = b.question_metrics(question, index + 1, payload,
                                                latency_ms=int((time.monotonic() - call_started) * 1000))
                except Exception:
                    payload = {}
                    metric = b.question_metrics(question, index + 1, {},
                                                latency_ms=int((time.monotonic() - call_started) * 1000))
                    failed = True
                if failed or payload.get("completed") is False:
                    metric["status"] = "failed_or_partial"
                items = payload.get("output")
                if any(isinstance(item, dict) and (
                        item.get("error") is not None or item.get("completed") is False
                        or isinstance(item.get("status"), str)
                        and item.get("status") in {"failed", "incomplete", "cancelled"})
                       for item in (items if isinstance(items, list) else [])):
                    metric["status"] = "failed_or_partial"
                missing_retrieval = metric["retrieval_calls"] is None or metric["retrieval_calls"] < 1
                missing_retrieval_only = missing_retrieval and metric["status"] == "completed"
                if missing_retrieval_only:
                    metric["status"] = "failed_or_partial"
                metrics[index] = metric
                identity = isinstance(payload.get("model"), str) and payload["model"] in {
                    result["agent"]["model"], result["agent"]["deployment"],
                    f"{result['agent']['model']}-{result['agent']['model_version']}",
                }
                amount = allocation(metric, pricing, identity)
                allocations[index] = {"question_number": index + 1, "model_identity_bound": identity,
                                      "model_allocation_usd": amount}
                result["observed_model_allocation_usd"] += amount or 0
                journal = {
                    "schema_version": "rag-agent-question-metrics.v1", "run_id": run_id,
                    "metric": metric, "allocation": allocations[index],
                }
                # A failed durable journal prevents all subsequent dispatch. The
                # claim is never removed: a crash cannot be retried as inference.
                try:
                    b._write_once(directory / "questions" / f"{index + 1:02d}.json", journal)
                except OSError:
                    result["stop_reason"] = "metric_journal_failed"
                    break
                if response_observer is not None:
                    try:
                        with private_call():
                            response_observer(
                                run_id=run_id, question_number=index + 1,
                                question=question, response=copy.deepcopy(payload),
                                metric=copy.deepcopy(metric),
                                provider_request_id=getattr(response, "_request_id", None),
                            )
                    except (ValueError, OSError):
                        result["stop_reason"] = "evaluation_capture_failed"
                        break
                if failed:
                    result["stop_reason"] = "provider_call_failed"
                    break
                if metric["status"] != "completed":
                    result["stop_reason"] = ("retrieval_evidence_unavailable" if missing_retrieval_only
                                             else "response_failed_or_partial")
                    break
                if amount is None:
                    result["stop_reason"] = "usage_or_pricing_unavailable"
                    break
                if time.monotonic() - started >= authorization["max_elapsed_seconds"]:
                    result["stop_reason"] = "elapsed_time_limit"
                    break
        except OSError:
            # Do not convert a persistence failure into apparent durable success.
            raise ValueError("Metric journal persistence failed; batch will not be retried") from None
        except Exception:
            result["stop_reason"] = "client_unavailable"
        finally:
            try:
                stack.close()
                if client is not None and not hasattr(factory_result, "__enter__") and hasattr(client, "close"):
                    client.close()
            except Exception:
                pass  # Closing a client must not discard already metered usage.
        dispatched = any(row["status"] != "not_dispatched" for row in metrics)
        result["evidence_classification"] = "measured" if dispatched else "blocked"
        result["execution_status"] = (
            "completed" if result["stop_reason"] == "completed"
            else "partial" if dispatched else "failed"
        )
    result["ended_at"] = b._now()
    _seal(result)
    validate_result_v2(result)
    b._write_once(directory / "result.json", result)
    _register(register, result)
    publish_result(service, run_id, publisher=publisher)
    return result


def _seal(result):
    result.pop("evidence", None)
    result["evidence"] = {
        "status": "persisted_locally_cloud_pending", "location": f"studio_runs/{result['run_id']}/result.json",
        "content_hash": b._digest(result), "cloud_status": "publication_tracked_separately",
    }


def _existing(service, claim, request_id, plan_id, register):
    try:
        previous = json.loads(claim.read_text(encoding="utf-8"))
        if (set(previous) != {"schema_version", "request_id", "run_id", "plan_id", "created_at"}
                or previous["schema_version"] != "rag-agent-batch-claim.v1"):
            raise ValueError("Invalid claim projection")
        if previous["request_id"] != request_id or previous["plan_id"] != plan_id:
            raise ValueError("Request identity belongs to a different forecast")
        saved = b.validate_result(json.loads(
            (service.run_root / _run_id(previous["run_id"]) / "result.json").read_text(encoding="utf-8")))
        if (saved["request_id"] != request_id or saved["plan_id"] != plan_id
                or saved["run_id"] != previous["run_id"]):
            raise ValueError("Batch evidence identity mismatch")
        _register(register, saved)
        return saved
    except (OSError, KeyError, json.JSONDecodeError):
        raise ValueError("Batch identity is already reserved; it will not be retried") from None


def _publication_record(directory, run_id, status, content_hash, destination=None):
    record = {"schema_version": "rag-batch-publication.v1", "run_id": run_id,
              "status": status, "content_hash": content_hash, "created_at": b._now(),
              "destination": destination}
    b._write_once(directory / "publication" / f"{time.time_ns()}-{uuid4().hex}.json", record)
    return record


def publication_status(service, run_id):
    directory = service.run_root / _run_id(run_id)
    records = []
    for path in sorted((directory / "publication").glob("*.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(row, dict)
                or set(row) != {"schema_version", "run_id", "status", "content_hash", "created_at", "destination"}
                or row["schema_version"] != "rag-batch-publication.v1" or row["run_id"] != run_id
                or row["status"] not in {"pending", "not_configured", "published", "publication_failed", "conflict"}
                or row["status"] == "published" and (row["content_hash"] is None or row["destination"] is None)
                or row["content_hash"] is not None and not re.fullmatch("[a-f0-9]{64}", row["content_hash"])
                or row["destination"] is not None and not re.fullmatch(
                    r"https://[a-z0-9]{3,24}\.blob\.core\.windows\.net/[a-z0-9][a-z0-9-]{1,61}[a-z0-9]",
                    row["destination"])
                or datetime.fromisoformat(row["created_at"]).utcoffset() != timezone.utc.utcoffset(None)):
            raise ValueError("Publication projection invalid")
        records.append(row)
    published = [row for row in records if row["status"] == "published"]
    return (published or records or [{
        "schema_version": "rag-batch-publication.v1", "run_id": run_id,
        "status": "pending", "content_hash": None, "created_at": b._now(), "destination": None,
    }])[-1]


def _blob_publish(destination, name, data):
    from azure.core.exceptions import ResourceExistsError
    from azure.storage.blob import BlobClient

    account, container = destination["location"].rsplit("/", 1)
    with private_call(), b._credential() as credential:
        with BlobClient(account_url=account, container_name=container, blob_name=name,
                        credential=credential, retry_total=0, connection_timeout=10,
                        read_timeout=15, logging_enable=False, tracing_enable=False) as blob:
            try:
                blob.upload_blob(data, overwrite=False, timeout=15)
            except ResourceExistsError:
                props = blob.get_blob_properties(timeout=15)
                if props.size != len(data):
                    return "conflict"
                existing = blob.download_blob(offset=0, length=len(data), timeout=15).readall()
                return "published" if existing == data else "conflict"
    return "published"


def publish_result(service, run_id, *, publisher=None):
    """Upload-only retry: cannot construct an inference client or invoke a model."""
    directory = service.run_root / _run_id(run_id)
    raw = (directory / "result.json").read_bytes()
    result = b.validate_result(json.loads(raw))
    if result["schema_version"] != SCHEMA:
        return publication_status(service, run_id)  # Historical v1 is not reinterpreted.
    if raw != b._canonical(result).encode("utf-8"):
        raise ValueError("Local batch result must be canonical before publication")
    current = publication_status(service, run_id)
    if current["status"] == "published":
        if current["content_hash"] != result["evidence"]["content_hash"]:
            raise ValueError("Published result identity mismatch")
        return current
    location = None
    try:
        destination = b.evidence_destination()
        location = destination["location"]
        if destination["location"] is None:
            status = "not_configured"
        else:
            data = raw
            status = (publisher or _blob_publish)(destination, result["evidence"]["location"], data)
            if status not in {"published", "conflict"}:
                status = "publication_failed"
    except Exception:
        status = "publication_failed"
    return _publication_record(directory, run_id, status, result["evidence"]["content_hash"], location)
