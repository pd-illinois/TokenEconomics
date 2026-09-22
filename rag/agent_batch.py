"""Metrics-only Studio measurement, never an operational task-spend guarantee."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse
from uuid import UUID, uuid4

from costgov.policy_store import admit_receipt
from costgov.atomic_publish import publish_immutable

SCHEMA = "rag-agent-batch.v1"
LEGACY_MAX_QUESTIONS = 10
MAX_QUESTIONS = 25
MAX_CHARACTERS = 1200
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_COUNTS = {
    "question_number", "input_words", "input_characters", "output_words",
    "output_characters", "input_tokens", "output_tokens", "cached_input_tokens",
    "cache_write_input_tokens", "reasoning_output_tokens", "embedding_tokens",
    "retrieval_calls", "tool_calls", "latency_ms",
}
_NOTES = {
    "not_dispatched", "response_level_cumulative_usage_only",
    "managed_retrieval_internal_usage_unavailable", "provider_usage_unavailable",
    "cache_write_usage_unavailable", "cache_read_usage_unavailable",
    "reasoning_usage_unavailable", "tool_counts_exposed_items_only",
    "invalid_provider_usage", "acceptance_not_evaluated",
}
_BLOCKERS = {
    "agent_read_access_unavailable": "Read-only access to the deployed agent configuration is unavailable.",
    "agent_not_prompt": "The configured deployment is not a supported prompt agent.",
    "agent_not_active": "The pinned deployed agent is not active.",
    "agent_model_mismatch": "The deployed agent model does not match the selected forecast model; create a matching forecast or review the deployed agent configuration.",
    "forecast_required": "Select a saved Foundry RAG forecast to compare its model with the deployed agent.",
    "managed_books_mcp_unverified": "The deployed books Foundry IQ MCP binding could not be verified.",
    "retrieval_mode_unverified": "The managed knowledge-base hybrid retrieval configuration is not independently verified.",
    "hidden_model_cost_bound_unenforceable": "The managed MCP wrapper does not expose an enforceable input/model-call reservation for max_model_cost_per_call_usd or per_tenant_usd_per_run, including hidden retrieval model calls.",
    "azure_authority_required": "Active Azure policy with exact provenance is required.",
    "receipt_policy_rejected": "The immutable forecast does not satisfy the active Azure policy.",
    "receipt_workload_mismatch": "A saved Azure OpenAI Foundry rag_pipeline forecast is required.",
    "actual_model_not_allowed": "The actual deployed agent model is not allowed by the active Azure policy.",
}
_AGENT_FIELDS = {
    "agent_name", "agent_version", "kind", "active", "deployment", "model",
    "model_version", "managed_books_mcp", "retrieval_mode",
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    # Only metrics/configuration projections are hashed, never question content.
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("Invalid batch evidence identifier")
    return value


def _mapping(value):
    if isinstance(value, dict):
        return value
    for method in ("as_dict", "model_dump", "to_dict"):
        dump = getattr(value, method, None)
        if callable(dump):
            result = dump()
            return result if isinstance(result, dict) else {}
    return {}


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2**53 - 1 else None


def question_metrics(question, question_number, response=None, *, latency_ms=None):
    """Consume content in memory, emitting only typed scalar counts.

    Response usage is one cumulative provider observation, not the sum of
    response usage and per-item usage. Hidden MCP embedding/planning usage is
    unavailable even if exposed output contains no tool item.
    """
    payload = _mapping(response)
    metrics = {key: None for key in _COUNTS}
    metrics.update(
        question_number=question_number, input_words=len(question.split()),
        input_characters=len(question), latency_ms=_count(latency_ms),
        embedding_usage_status="unavailable",
        status="not_dispatched" if response is None else "failed_or_partial",
        coverage_notes=["managed_retrieval_internal_usage_unavailable", "acceptance_not_evaluated"],
    )
    if response is None:
        metrics["coverage_notes"].append("not_dispatched")
        return validate_metric(metrics)
    output = payload.get("output")
    items = output if isinstance(output, list) else []
    text = []
    for item in items:
        if not isinstance(item, dict) or item.get("type") != "message" or item.get("role", "assistant") != "assistant":
            continue
        content = item.get("content")
        for part in content if isinstance(content, list) else []:
            if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                text.append(part["text"])
    if isinstance(output, list):
        answer = "".join(text)
        metrics.update(output_words=len(answer.split()), output_characters=len(answer))
        metrics["tool_calls"] = sum(
            isinstance(item, dict) and isinstance(item.get("type"), str)
            and item.get("type") in {"mcp_call", "function_call", "file_search_call", "web_search_call"}
            for item in items
        )
        metrics["retrieval_calls"] = sum(
            isinstance(item, dict) and (
                item.get("type") == "file_search_call"
                or item.get("type") == "mcp_call" and item.get("name") == "knowledge_base_retrieve"
            ) for item in items
        )
        metrics["coverage_notes"].append("tool_counts_exposed_items_only")
    usage = _mapping(payload.get("usage"))
    input_tokens, output_tokens = _count(usage.get("input_tokens")), _count(usage.get("output_tokens"))
    total = _count(usage.get("total_tokens"))
    valid = input_tokens is not None and output_tokens is not None and (
        usage.get("total_tokens") is None or total == input_tokens + output_tokens
    )
    if valid:
        metrics.update(input_tokens=input_tokens, output_tokens=output_tokens)
        details = _mapping(usage.get("input_tokens_details"))
        out_details = _mapping(usage.get("output_tokens_details"))
        cached, written = _count(details.get("cached_tokens")), _count(details.get("cache_write_tokens"))
        reasoning = _count(out_details.get("reasoning_tokens"))
        invalid_partitions = (
            cached is not None and cached > input_tokens
            or written is not None and written > input_tokens
            or cached is not None and written is not None and cached + written > input_tokens
        )
        if invalid_partitions:
            metrics["coverage_notes"].append("invalid_provider_usage")
        else:
            metrics.update(cached_input_tokens=cached, cache_write_input_tokens=written)
        metrics["reasoning_output_tokens"] = reasoning if reasoning is not None and reasoning <= output_tokens else None
        if reasoning is not None and reasoning > output_tokens:
            metrics["coverage_notes"].append("invalid_provider_usage")
        metrics["coverage_notes"].append("response_level_cumulative_usage_only")
    else:
        metrics["coverage_notes"].append("provider_usage_unavailable")
    for field, note in (
        ("cached_input_tokens", "cache_read_usage_unavailable"),
        ("cache_write_input_tokens", "cache_write_usage_unavailable"),
        ("reasoning_output_tokens", "reasoning_usage_unavailable"),
    ):
        if metrics[field] is None:
            metrics["coverage_notes"].append(note)
    metrics["status"] = "completed" if payload.get("status") == "completed" else "failed_or_partial"
    return validate_metric(metrics)


def validate_metric(metric):
    if not isinstance(metric, dict) or set(metric) != _COUNTS | {"embedding_usage_status", "status", "coverage_notes"}:
        raise ValueError("Metric fields must match the metrics-only allowlist")
    if any(value is not None and _count(value) is None for key, value in metric.items() if key in _COUNTS):
        raise ValueError("Metric counts must be nonnegative integers or null")
    if metric["question_number"] not in range(1, MAX_QUESTIONS + 1):
        raise ValueError("Question number is outside the batch limit")
    if metric["embedding_usage_status"] != "unavailable" or metric["embedding_tokens"] is not None:
        raise ValueError("Managed embedding usage is unavailable, not zero")
    if metric["status"] not in {"not_dispatched", "completed", "failed_or_partial"}:
        raise ValueError("Unsupported metric status")
    if not isinstance(metric["coverage_notes"], list) or any(note not in _NOTES for note in metric["coverage_notes"]):
        raise ValueError("Metric coverage notes must use the fixed allowlist")
    return metric


def _validate_agent(agent):
    if not isinstance(agent, dict) or set(agent) != _AGENT_FIELDS:
        raise ValueError("Agent evidence must match the metrics-only allowlist")
    for key in ("agent_name", "deployment", "model", "model_version"):
        if agent[key] is not None:
            _identifier(agent[key])
    if agent["agent_version"] is not None and (
        not isinstance(agent["agent_version"], str)
        or not re.fullmatch(r"[1-9][0-9]*", agent["agent_version"])
    ):
        raise ValueError("Exact deployed-agent version required")
    if agent["kind"] not in {"prompt", "unsupported", "unverified"}:
        raise ValueError("Unsupported deployed-agent kind")
    if type(agent["active"]) is not bool or type(agent["managed_books_mcp"]) is not bool:
        raise ValueError("Agent configuration flags must be boolean")
    if agent["retrieval_mode"] != "managed_mcp_hybrid_unverified":
        raise ValueError("Retrieval mode is not verified")
    return agent


def _validate_result_v1(result):
    """Reject extra fields at every content-bearing evidence boundary."""
    expected = {
        "schema_version", "run_id", "request_id", "plan_id", "report_id",
        "execution_status", "questions_count", "metrics", "agent", "policy",
        "prediction", "blockers", "started_at", "ended_at",
        "evidence_classification", "acceptance_status", "operational_promotion",
    }
    if not isinstance(result, dict) or set(result) != expected | ({"evidence"} if "evidence" in result else set()):
        raise ValueError("Batch evidence must match the metrics-only allowlist")
    for key in ("run_id", "plan_id", "report_id"):
        _identifier(result[key])
    if not isinstance(result["request_id"], str) or str(UUID(result["request_id"])) != result["request_id"]:
        raise ValueError("Batch request identity must be canonical")
    if (result["schema_version"] != SCHEMA or result["execution_status"] != "blocked"
            or result["evidence_classification"] != "blocked"
            or result["acceptance_status"] != "not_evaluated"
            or result["operational_promotion"] is not False):
        raise ValueError("Unsupported execution or acceptance evidence")
    if _count(result["questions_count"]) not in range(1, LEGACY_MAX_QUESTIONS + 1):
        raise ValueError("Batch evidence exceeds question limit")
    if not isinstance(result["metrics"], list) or len(result["metrics"]) != result["questions_count"]:
        raise ValueError("Batch question metrics are incomplete")
    for number, metric in enumerate(result["metrics"], 1):
        validate_metric(metric)
        if metric["question_number"] != number or metric["status"] != "not_dispatched":
            raise ValueError("Blocked attempts cannot contain dispatched usage")
    _validate_agent(result["agent"])
    policy = result["policy"]
    if not isinstance(policy, dict) or set(policy) != {
        "policy_id", "version", "content_hash", "source", "etag", "label", "endpoint", "key",
    }:
        raise ValueError("Policy evidence fields are not allowlisted")
    for key in ("policy_id", "version"):
        _identifier(policy[key])
    if policy["source"] != "azure_app_configuration":
        raise ValueError("Azure policy authority required")
    for key in ("etag", "label", "endpoint", "key"):
        if not isinstance(policy[key], str) or not 1 <= len(policy[key]) <= 512:
            raise ValueError("Exact policy provenance required")
    prediction = result["prediction"]
    if not isinstance(prediction, dict) or set(prediction) != {"receipt_id", "content_hash"}:
        raise ValueError("Prediction evidence fields are not allowlisted")
    _identifier(prediction["receipt_id"])
    for value in (policy["content_hash"], prediction["content_hash"]):
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
            raise ValueError("Versioned evidence hash required")
    if not isinstance(result["blockers"], list) or not result["blockers"]:
        raise ValueError("Blocked attempts require explicit blockers")
    for blocker in result["blockers"]:
        if (not isinstance(blocker, dict) or set(blocker) != {"code", "message"}
                or blocker["code"] not in _BLOCKERS
                or blocker["message"] != _BLOCKERS[blocker["code"]]):
            raise ValueError("Blockers must use fixed messages, never exception payloads")
    for field in ("started_at", "ended_at"):
        if not isinstance(result[field], str) or datetime.fromisoformat(result[field]).utcoffset() != timezone.utc.utcoffset(None):
            raise ValueError("UTC evidence timestamps required")
    if "evidence" in result:
        evidence = result["evidence"]
        if not isinstance(evidence, dict) or set(evidence) != {"status", "location", "content_hash", "cloud_status"}:
            raise ValueError("Storage evidence fields are not allowlisted")
        if (evidence["status"] != "persisted_locally_cloud_pending"
                or evidence["cloud_status"] != "not_published_no_executed_usage"
                or evidence["location"] != f"studio_runs/{result['run_id']}/result.json"
                or evidence["content_hash"] != _digest({key: value for key, value in result.items() if key != "evidence"})):
            raise ValueError("Batch evidence integrity verification failed")
    return result


def configuration():
    endpoint = os.environ.get(
        "RAG_BATCH_PROJECT_ENDPOINT",
        "https://ai-account-xbk6ickycmp22.services.ai.azure.com/api/projects/ai-project-tokeneconomics-te003",
    )
    parsed = urlparse(endpoint)
    if (parsed.scheme != "https" or not (parsed.hostname or "").endswith(".services.ai.azure.com")
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.port not in {None, 443}
            or not re.fullmatch(r"/api/projects/[A-Za-z0-9_-]+", parsed.path)):
        raise ValueError("Batch project endpoint must be a server-configured Azure project HTTPS endpoint")
    version = os.environ.get("RAG_BATCH_AGENT_VERSION", "").strip()
    if version and not re.fullmatch(r"[1-9][0-9]*", version):
        raise ValueError("Batch agent version must be an exact positive version number")
    return {
        "project_endpoint": endpoint,
        "agent_name": _identifier(os.environ.get("RAG_BATCH_AGENT_NAME", "tokengov-books-rag-agent")),
        "agent_version": version or None,
    }


def _credential():
    from azure.identity import AzureCliCredential, DefaultAzureCredential, ManagedIdentityCredential

    hosted = os.environ.get("CONTAINER_APP_NAME") or os.environ.get("WEBSITE_INSTANCE_ID")
    subscription = os.environ.get("TOKENGOV_RAG_AZURE_CLI_SUBSCRIPTION", "").strip()
    if subscription:
        if not re.fullmatch(
            r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", subscription
        ):
            raise ValueError("TOKENGOV_RAG_AZURE_CLI_SUBSCRIPTION must be a subscription UUID")
        if hosted:
            raise ValueError(
                "TOKENGOV_RAG_AZURE_CLI_SUBSCRIPTION is local-development only; "
                "hosted runtimes must use managed identity"
            )
        return AzureCliCredential(subscription=subscription, process_timeout=40)
    if hosted:
        return ManagedIdentityCredential(client_id=os.environ.get("AZURE_CLIENT_ID") or None)
    return DefaultAzureCredential(exclude_interactive_browser_credential=True)


def _probe_agent_inventory(config):
    """Read inventory only. Never create a response, conversation, or session."""
    from azure.ai.projects import AIProjectClient

    with _credential() as credential:
        with AIProjectClient(
            endpoint=config["project_endpoint"], credential=credential,
            retry_total=0, connection_timeout=10, read_timeout=15, logging_enable=False,
        ) as project:
            agent = _mapping(project.agents.get(config["agent_name"]))
            latest = _mapping(_mapping(agent.get("versions")).get("latest"))
            version = config["agent_version"] or latest.get("version")
            if not isinstance(version, str) or not re.fullmatch(r"[1-9][0-9]*", version):
                raise ValueError("Agent version could not be resolved")
            pinned = _mapping(project.agents.get_version(config["agent_name"], version))
            definition = _mapping(pinned.get("definition"))
            deployment = _identifier(definition.get("model"))
            model = _mapping(project.deployments.get(deployment))
            tools = definition.get("tools", [])
            mcp = [
                item for item in tools if isinstance(item, dict) and item.get("type") == "mcp"
                and re.fullmatch(
                    r"https://search-xbk6ickycmp22\.search\.windows\.net/knowledgebases/books-knowledge-base(?:-[A-Za-z0-9-]+)?/mcp\?api-version=[0-9A-Za-z-]+",
                    item.get("server_url", ""),
                )
                and item.get("project_connection_id")
            ]
            return {
                "agent_name": config["agent_name"], "agent_version": version,
                "kind": "prompt" if definition.get("kind") == "prompt" else "unsupported",
                "active": agent.get("state") == "enabled" and pinned.get("status") == "active",
                "deployment": deployment, "model": _identifier(model.get("modelName")),
                "model_version": _identifier(model.get("modelVersion")),
                "managed_books_mcp": len(mcp) == 1 and len(tools) == 1,
                "retrieval_mode": "managed_mcp_hybrid_unverified",
            }


def evidence_destination():
    account = os.environ.get("RAG_BATCH_EVIDENCE_ACCOUNT_URL", "").strip()
    container = os.environ.get("RAG_BATCH_EVIDENCE_CONTAINER", "").strip()
    if account and not re.fullmatch(r"https://[a-z0-9]{3,24}\.blob\.core\.windows\.net", account):
        raise ValueError("Evidence destination must use an Azure Blob account URL without credentials")
    if container and not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])", container):
        raise ValueError("Evidence container name is invalid")
    if bool(account) != bool(container):
        raise ValueError("Both evidence account URL and existing container are required")
    return {
        "status": "configured_not_published" if account else "pending_cloud_configuration",
        "location": f"{account}/{container}" if account else None,
        "local_location": "studio_runs/<run_id>/result.json",
        "write_mode": "create_only_no_overwrite",
        "authentication": "managed_identity_production_default_credential_local",
    }


def _connection_status_v1(*, probe=None, receipt=None):
    config = configuration()
    blockers = []
    expected_model = None
    if receipt is not None:
        if (receipt.get("route", {}).get("route_id") != "foundry"
                or receipt.get("confirmed_profile", {}).get("agent_pattern") != "rag_pipeline"
                or receipt.get("prediction", {}).get("provider") != "azure_openai"):
            raise ValueError(_BLOCKERS["receipt_workload_mismatch"])
        expected_model = _identifier(receipt.get("prediction", {}).get("model"))
    else:
        blockers.append("forecast_required")
    agent = {
        "agent_name": config["agent_name"], "agent_version": config["agent_version"],
        "kind": "unverified", "active": False, "deployment": None, "model": None,
        "model_version": None, "managed_books_mcp": False,
        "retrieval_mode": "managed_mcp_hybrid_unverified",
    }
    try:
        agent = _validate_agent((probe or probe_agent)(config))
    except Exception:
        blockers.append("agent_read_access_unavailable")
    else:
        if agent["kind"] != "prompt":
            blockers.append("agent_not_prompt")
        if not agent["active"]:
            blockers.append("agent_not_active")
        if expected_model is not None and agent["model"] != expected_model:
            blockers.append("agent_model_mismatch")
        if not agent["managed_books_mcp"]:
            blockers.append("managed_books_mcp_unverified")
    blockers.extend(["retrieval_mode_unverified", "hidden_model_cost_bound_unenforceable"])
    return {
        "schema_version": SCHEMA, "configured": True, "ready": False,
        "status": "blocked", "message": " ".join(_BLOCKERS[code] for code in blockers),
        "blockers": [{"code": code, "message": _BLOCKERS[code]} for code in blockers],
        "agent_name": agent["agent_name"], "agent_version": agent["agent_version"],
        "model": agent["model"], "expected_model": expected_model,
        "retrieval_mode": agent["retrieval_mode"], "public_config": agent,
        "limits": {
            "maximum_questions": LEGACY_MAX_QUESTIONS,
            "maximum_question_characters": MAX_CHARACTERS,
            "maximum_concurrent_questions": 1, "maximum_attempts": 1,
            "automatic_billable_retries": 0, "repeated_explicit_batches": True,
            "hidden_internal_model_calls_bounded": False,
        },
        "privacy": {
            "studio_evidence": "metrics_only_no_content_or_question_fingerprints",
            "provider_retention": "Not a zero-retention guarantee. A future supported invocation must use store=False, no conversation and disabled content tracing; managed service retention remains a separate boundary.",
        },
        "evidence": evidence_destination(), "read_only": True,
        "inference_performed": False, "authorization_required": "evaluation",
        "operational_promotion": False,
    }


def _write_once(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.parent / f".staging-{uuid4().hex}"
    try:
        with staging.open("x", encoding="utf-8") as stream:
            stream.write(_canonical(payload))
            stream.flush()
            os.fsync(stream.fileno())
        publish_immutable(staging, path)
    finally:
        staging.unlink(missing_ok=True)


def _validate_payload(payload):
    if not isinstance(payload, dict) or set(payload) != {"questions", "request_id"}:
        raise ValueError("Batch accepts only questions and request_id")
    request_id = payload["request_id"]
    try:
        if not isinstance(request_id, str) or str(UUID(request_id)) != request_id.lower():
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError("request_id must be a canonical UUID") from None
    questions = payload["questions"]
    if (not isinstance(questions, list) or not 1 <= len(questions) <= MAX_QUESTIONS
            or any(not isinstance(question, str) or not question.strip()
                   or len(question) > MAX_CHARACTERS for question in questions)):
        raise ValueError("Provide 1-25 nonempty questions, each at most 1200 characters")
    return request_id.lower(), questions


def _policy_binding(receipt, loaded):
    assessment = admit_receipt(receipt, loaded)
    reference = assessment["policy"]
    provenance = reference["provenance"]
    if (loaded.document.get("status") != "active"
            or provenance.get("source") != "azure_app_configuration"
            or not all(provenance.get(key) for key in ("etag", "label", "endpoint", "key"))):
        raise ValueError(_BLOCKERS["azure_authority_required"])
    if any(not check["passed"] for check in assessment["checks"]):
        raise ValueError(_BLOCKERS["receipt_policy_rejected"])
    return {
        "policy_id": loaded.document["policy_id"],
        "version": loaded.document["version"],
        "content_hash": reference["content_hash"],
        "source": "azure_app_configuration",
        "etag": provenance["etag"], "label": provenance["label"],
        "endpoint": provenance["endpoint"], "key": provenance["key"],
    }


def _execute_v1(service, plan_id, payload, loaded, principal, root, register, *, probe=None):
    """Persist an idempotent blocked batch, without billable runtime dispatch.

    A UUID names an immutable attempt, not its plaintext input fingerprint.
    Reusing it returns the original attempt even if the browser changed input.
    A different explicit UUID is required for a subsequent batch.
    """
    request_id, questions = _validate_payload(payload)
    plan_id = _identifier(plan_id)
    receipt = service.receipt(plan_id)
    if (receipt.get("route", {}).get("route_id") != "foundry"
            or receipt.get("confirmed_profile", {}).get("agent_pattern") != "rag_pipeline"
            or receipt.get("prediction", {}).get("provider") != "azure_openai"):
        raise ValueError(_BLOCKERS["receipt_workload_mismatch"])
    policy = _policy_binding(receipt, loaded)
    if not principal:
        raise ValueError("Separate evaluation authorization is required")
    claims = service.run_root / "rag_batch_claims"
    claim = claims / f"{request_id}.json"
    run_id = f"run-{uuid4().hex}"
    try:
        _write_once(claim, {
            "schema_version": "rag-agent-batch-claim.v1",
            "request_id": request_id, "run_id": run_id, "plan_id": plan_id,
            "created_at": _now(),
        })
    except FileExistsError:
        try:
            previous = json.loads(claim.read_text(encoding="utf-8"))
            if previous["plan_id"] != plan_id:
                raise ValueError("Request identity belongs to a different forecast")
            saved = validate_result(json.loads(
                (service.run_root / _identifier(previous["run_id"]) / "result.json").read_text(encoding="utf-8")
            ))
            if saved["request_id"] != request_id or saved["plan_id"] != plan_id:
                raise ValueError("Batch evidence identity mismatch")
            register(
                saved["run_id"], run_id=saved["run_id"], report_id=saved["report_id"],
                plan_id=plan_id, status="blocked", execution_status="blocked", result=saved,
            )
            return saved
        except (OSError, KeyError, json.JSONDecodeError):
            raise ValueError("Batch identity is already reserved; it will not be retried") from None
    started_at = _now()
    connection = _connection_status_v1(probe=probe, receipt=receipt)
    blockers = [item["code"] for item in connection["blockers"]]
    model = connection["model"]
    if model and model not in loaded.document["admission"]["allowed_models"]:
        blockers.append("actual_model_not_allowed")
    result = {
        "schema_version": SCHEMA, "run_id": run_id, "request_id": request_id,
        "plan_id": plan_id, "report_id": _identifier(receipt["report_id"]),
        "execution_status": "blocked", "questions_count": len(questions),
        "metrics": [question_metrics(question, index + 1) for index, question in enumerate(questions)],
        "agent": connection["public_config"], "policy": policy,
        "prediction": {"receipt_id": receipt["receipt_id"], "content_hash": receipt["content_hash"]},
        "blockers": [{"code": code, "message": _BLOCKERS[code]} for code in blockers],
        "started_at": started_at, "ended_at": _now(), "evidence_classification": "blocked",
        "acceptance_status": "not_evaluated", "operational_promotion": False,
    }
    validate_result(result)
    evidence_hash = _digest(result)
    result["evidence"] = {
        "status": "persisted_locally_cloud_pending",
        "location": f"studio_runs/{run_id}/result.json", "content_hash": evidence_hash,
        "cloud_status": "not_published_no_executed_usage",
    }
    validate_result(result)
    _write_once(service.run_root / run_id / "result.json", result)
    register(
        run_id, run_id=run_id, report_id=result["report_id"], plan_id=plan_id,
        status="blocked", execution_status="blocked", result=result,
    )
    return result


def probe_agent(config):
    from rag.agent_batch_probe import probe
    return probe(config)


def connection_status(*, probe=None, receipt=None, loaded=None):
    from rag.agent_batch_measurement import connection
    return connection(probe=probe, receipt=receipt, loaded=loaded)


def validate_result(result):
    if isinstance(result, dict) and result.get("schema_version") == SCHEMA:
        return _validate_result_v1(result)
    from rag.agent_batch_measurement import validate_result_v2
    return validate_result_v2(result)


def execute(service, plan_id, payload, loaded, principal, root, register, *,
            probe=None, client_factory=None, publisher=None, policy_loader=None,
            response_observer=None):
    from rag.agent_batch_measurement import execute_batch
    return execute_batch(service, plan_id, payload, loaded, principal, root, register,
                         probe=probe, client_factory=client_factory, publisher=publisher,
                         policy_loader=policy_loader, response_observer=response_observer)


def publication_status(service, run_id):
    from rag.agent_batch_measurement import publication_status as status
    return status(service, run_id)


def publish_result(service, run_id, *, publisher=None):
    from rag.agent_batch_measurement import publish_result as publish
    return publish(service, run_id, publisher=publisher)
