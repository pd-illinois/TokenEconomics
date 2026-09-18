from __future__ import annotations

import copy
import hashlib
import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from uuid import uuid4

import pytest

import studio
from rag import agent_batch as batch
from test_books_playground import ROOT, proof


@pytest.fixture(autouse=True)
def no_live_batch_publication(monkeypatch):
    monkeypatch.delenv("RAG_BATCH_EVIDENCE_ACCOUNT_URL", raising=False)
    monkeypatch.delenv("RAG_BATCH_EVIDENCE_CONTAINER", raising=False)
    monkeypatch.delenv("TOKENGOV_RAG_AZURE_CLI_SUBSCRIPTION", raising=False)


def agent_config(_):
    return {
        "agent_name": "tokengov-books-rag-agent", "agent_version": "4",
        "kind": "prompt", "active": True,
        "deployment": "rag-agent-runtime-gpt-4-1-mini",
        "model": "gpt-4.1-mini", "model_version": "2025-04-14",
        "managed_books_mcp": True, "retrieval_mode": "managed_mcp_hybrid_unverified",
    }


def test_local_rag_credential_can_pin_the_authenticated_cli_subscription(monkeypatch):
    import azure.identity

    calls = []
    expected = object()
    monkeypatch.setenv(
        "TOKENGOV_RAG_AZURE_CLI_SUBSCRIPTION",
        "a91cc1ba-bd19-43a7-90ea-120794c0fbc6",
    )
    monkeypatch.setattr(
        azure.identity,
        "AzureCliCredential",
        lambda **kwargs: calls.append(kwargs) or expected,
    )
    assert batch._credential() is expected
    assert calls == [{
        "subscription": "a91cc1ba-bd19-43a7-90ea-120794c0fbc6",
        "process_timeout": 40,
    }]


@pytest.mark.parametrize("subscription", ["not-a-uuid", " a91cc1ba "])
def test_local_rag_credential_rejects_invalid_subscription(monkeypatch, subscription):
    monkeypatch.setenv("TOKENGOV_RAG_AZURE_CLI_SUBSCRIPTION", subscription)
    with pytest.raises(ValueError, match="subscription UUID"):
        batch._credential()


@pytest.mark.parametrize("host", ["CONTAINER_APP_NAME", "WEBSITE_INSTANCE_ID"])
def test_hosted_rag_runtime_rejects_local_cli_identity_pin(monkeypatch, host):
    monkeypatch.setenv(host, "hosted-runtime")
    monkeypatch.setenv(
        "TOKENGOV_RAG_AZURE_CLI_SUBSCRIPTION",
        "a91cc1ba-bd19-43a7-90ea-120794c0fbc6",
    )
    with pytest.raises(ValueError, match="local-development only"):
        batch._credential()


def execute(proof, questions=None, request_id=None, **kwargs):
    service, receipt, policy, _, _, register = proof
    return batch.execute(
        service, receipt["plan_id"],
        {"questions": questions or ["A private question?"], "request_id": request_id or str(uuid4())},
        policy, "local-evaluator", ROOT, register, probe=kwargs.get("probe", agent_config),
    )


def test_live_agent_mismatch_and_unenforceable_costs_block_without_clients(proof):
    result = execute(proof)
    assert result["execution_status"] == "blocked"
    assert result["questions_count"] == 1
    assert result["acceptance_status"] == "not_evaluated"
    assert result["operational_promotion"] is False
    assert {item["code"] for item in result["blockers"]} >= {
        "agent_model_mismatch", "measurement_authorization_required", "actual_model_not_allowed",
    }
    assert result["metrics"][0]["status"] == "not_dispatched"
    assert result["metrics"][0]["input_tokens"] is None
    assert not proof[4]
    assert result["evidence"]["cloud_status"] == "publication_tracked_separately"
    assert proof[0].registry[result["run_id"]]["result"] == result


def test_same_uuid_never_resubmits_changed_questions_new_uuid_allows_next_batch(proof):
    request_id = str(uuid4())
    first = execute(proof, request_id=request_id)
    second = execute(proof, ["Changed content"], request_id=request_id,
                     probe=lambda _: pytest.fail("duplicate must not reprobe"))
    assert first == second
    third = execute(proof, request_id=str(uuid4()))
    assert third["run_id"] != first["run_id"]
    assert len(list((proof[0].run_root / "rag_batch_claims").glob("*.json"))) == 2
    assert not proof[4]


def test_restart_repairs_registry_from_immutable_result(proof):
    request_id = str(uuid4())
    first = execute(proof, request_id=request_id)
    proof[0].registry.clear()
    assert execute(proof, request_id=request_id) == first
    assert proof[0].registry[first["run_id"]]["result"] == first


def test_concurrent_uuid_reservation_cannot_duplicate(proof):
    entered, release = threading.Event(), threading.Event()
    results = []
    request_id = str(uuid4())

    def probe(config):
        entered.set()
        assert release.wait(5)
        return agent_config(config)

    worker = threading.Thread(target=lambda: results.append(execute(proof, request_id=request_id, probe=probe)))
    worker.start()
    assert entered.wait(5)
    try:
        with pytest.raises(ValueError, match="reserved"):
            execute(proof, request_id=request_id)
    finally:
        release.set()
        worker.join()
    assert len(results) == 1
    assert not proof[4]


@pytest.mark.parametrize("payload", [
    {}, {"questions": ["q"]}, {"questions": [], "request_id": str(uuid4())},
    {"questions": ["q"] * 26, "request_id": str(uuid4())},
    {"questions": ["q" * 1201], "request_id": str(uuid4())},
    {"questions": [" "], "request_id": str(uuid4())},
    {"questions": [True], "request_id": str(uuid4())},
    {"questions": ["q"], "request_id": "not-a-uuid"},
    {"questions": ["q"], "request_id": str(uuid4()), "endpoint": "https://evil.invalid"},
    {"questions": ["q"], "request_id": str(uuid4()), "accepted": True},
])
def test_invalid_inputs_do_not_create_claims(proof, payload):
    service, receipt, policy, _, _, register = proof
    with pytest.raises(ValueError):
        batch.execute(service, receipt["plan_id"], payload, policy, "principal", ROOT, register, probe=agent_config)
    assert not service.run_root.exists()
    assert not proof[4]


@pytest.mark.parametrize("mutation", [
    lambda p: p.provenance.update(source="local"),
    lambda p: p.provenance.update(etag=""),
    lambda p: p.document.update(status="inactive"),
    lambda p: p.document["admission"].update(allowed_models=["gpt-4.1-mini"]),
    lambda p: p.document["admission"].update(max_model_cost_per_call_usd=0.0000001),
])
def test_azure_admission_fails_closed_before_reservation(proof, mutation):
    mutation(proof[2])
    with pytest.raises(ValueError):
        execute(proof, probe=lambda _: pytest.fail("policy failed before inventory"))
    assert not proof[0].run_root.exists()


def test_ten_questions_are_bounded_but_not_represented_as_ten_model_calls(proof):
    result = execute(proof, [f"Question number {n}?" for n in range(10)])
    assert len(result["metrics"]) == 10
    assert all(row["tool_calls"] is None and row["embedding_tokens"] is None for row in result["metrics"])
    assert not proof[4]


def test_sentinel_content_and_plaintext_fingerprints_never_persist(proof):
    question = "SENTINEL_QUESTION_7a30 What is the password?"
    result = execute(proof, [question])
    sentinel_hashes = (
        hashlib.sha256(question.encode()).hexdigest(),
        hashlib.sha256(json.dumps([question], separators=(",", ":")).encode()).hexdigest(),
    )
    documents = [path.read_text(encoding="utf-8") for path in proof[0].run_root.rglob("*.json")]
    documents.append(json.dumps(proof[0].registry))
    for document in documents:
        assert "SENTINEL_QUESTION" not in document
        assert question not in document
        assert all(value not in document for value in sentinel_hashes)
    assert result["metrics"][0]["input_characters"] == len(question)
    assert result["metrics"][0]["input_words"] == len(question.split())


def provider_response():
    return {
        "id": "SENTINEL_RESPONSE_ID", "status": "completed",
        "usage": {
            "input_tokens": 100, "output_tokens": 30, "total_tokens": 130,
            "input_tokens_details": {"cached_tokens": 20, "cache_write_tokens": 10},
            "output_tokens_details": {"reasoning_tokens": 8},
        },
        "output": [
            {"type": "mcp_call", "name": "knowledge_base_retrieve",
             "arguments": "SENTINEL_ARGUMENTS", "output": "SENTINEL_PASSAGE",
             "usage": {"input_tokens": 9000, "output_tokens": 8000}},
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "SENTINEL_ANSWER three words",
                 "annotations": [{"url": "https://secret.invalid/SENTINEL_CITATION"}]},
            ]},
        ],
    }


def test_metrics_use_cumulative_provider_usage_without_doublecount_or_content():
    response = provider_response()
    row = batch.question_metrics("SENTINEL_QUESTION", 1, response, latency_ms=22)
    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 30
    assert row["cached_input_tokens"] == 20
    assert row["cache_write_input_tokens"] == 10
    assert row["reasoning_output_tokens"] == 8
    assert row["output_words"] == 3
    assert row["retrieval_calls"] == row["tool_calls"] == 1
    assert row["embedding_tokens"] is None
    assert row["embedding_usage_status"] == "unavailable"
    assert "SENTINEL" not in json.dumps(row)
    assert "response_level_cumulative_usage_only" in row["coverage_notes"]


@pytest.mark.parametrize("usage", [None, {}, {"input_tokens": True}, {
    "input_tokens": 100, "output_tokens": 30, "total_tokens": 999,
}])
def test_missing_or_invalid_tokens_never_inferred_from_text(usage):
    response = provider_response()
    response["usage"] = usage
    row = batch.question_metrics("Question", 1, response)
    assert row["input_tokens"] is row["output_tokens"] is None
    assert row["output_words"] == 3


def test_hidden_embeddings_missing_cache_fields_and_reasoning_are_not_zero():
    response = provider_response()
    response["usage"] = {"input_tokens": 100, "output_tokens": 30}
    row = batch.question_metrics("Question", 1, response)
    for key in ("embedding_tokens", "cached_input_tokens", "cache_write_input_tokens", "reasoning_output_tokens"):
        assert row[key] is None


def test_visible_text_counts_do_not_add_separators_between_provider_parts():
    response = provider_response()
    response["output"][1]["content"] = [
        {"type": "output_text", "text": "Hello "},
        {"type": "output_text", "text": "world."},
    ]
    row = batch.question_metrics("Question", 1, response)
    assert row["output_characters"] == len("Hello world.")
    assert row["output_words"] == 2


@pytest.mark.parametrize("extra_path", ["root", "metric", "agent", "prediction", "policy", "evidence"])
def test_storage_projection_rejects_arbitrary_extras(proof, extra_path):
    result = copy.deepcopy(execute(proof))
    target = result if extra_path == "root" else result["metrics"][0] if extra_path == "metric" else result[extra_path]
    target["raw_content"] = "SENTINEL_STORAGE"
    with pytest.raises(ValueError):
        batch.validate_result(result)


def test_probe_exception_details_never_escape():
    def fail(_):
        raise RuntimeError("SENTINEL_TOOL_EXCEPTION with secret content")

    result = batch.connection_status(probe=fail)
    assert result["ready"] is False
    assert result["inference_performed"] is False
    assert result["agent_version"] is None
    assert "SENTINEL" not in json.dumps(result)
    assert "agent_read_access_unavailable" in {item["code"] for item in result["blockers"]}


def test_readonly_connection_never_claims_luna_wrapper_matches(proof):
    result = batch.connection_status(probe=agent_config, receipt=proof[1])
    assert result["model"] == "gpt-4.1-mini"
    assert result["expected_model"] == "gpt-5.6-luna"
    assert result["ready"] is False
    assert result["limits"]["maximum_questions"] == 10
    assert result["limits"]["automatic_billable_retries"] == 0
    assert result["limits"]["hidden_internal_model_calls_bounded"] is False
    assert result["evidence"]["status"] == "pending_cloud_configuration"


def test_matching_forecast_clears_only_model_mismatch(proof, monkeypatch):
    monkeypatch.setenv("RAG_BATCH_EXPECTED_MODEL", "gpt-5.6-luna")
    receipt = copy.deepcopy(proof[1])
    receipt["prediction"]["model"] = "gpt-4.1-mini"
    result = batch.connection_status(probe=agent_config, receipt=receipt)
    assert result["expected_model"] == result["model"] == "gpt-4.1-mini"
    blockers = {item["code"] for item in result["blockers"]}
    assert "agent_model_mismatch" not in blockers
    assert "measurement_authorization_required" in blockers
    assert "hidden_model_cost_bound_unenforceable" not in blockers
    assert result["ready"] is False


def test_connection_without_forecast_does_not_invent_model_mismatch():
    result = batch.connection_status(probe=agent_config)
    assert result["expected_model"] is None
    blockers = {item["code"] for item in result["blockers"]}
    assert "forecast_required" in blockers
    assert "agent_model_mismatch" not in blockers


def test_api_requires_csrf_auth_persists_blocked_attempt_and_reopens(proof, monkeypatch):
    service, receipt, policy, _, _, register = proof
    artifacts = []
    monkeypatch.setattr(studio, "_lifecycle_service", lambda: service)
    monkeypatch.setattr(studio, "load_policy_from_environment", lambda: policy)
    monkeypatch.setattr(studio, "_set_run", register)
    monkeypatch.setattr(studio, "_read_registry", lambda: service.registry)
    monkeypatch.setattr(batch, "probe_agent", agent_config)
    monkeypatch.setattr(studio, "ReportStore", lambda _: SimpleNamespace(add_artifact=lambda *args: artifacts.append(args)))
    monkeypatch.setenv("TOKENGOV_EVALUATION_ALLOW_LOCAL", "true")
    server = ThreadingHTTPServer(("127.0.0.1", 0), studio.StudioHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port)
    body = json.dumps({"request_id": str(uuid4()), "questions": ["SENTINEL_HTTP_QUESTION"]})
    url = f"/api/plans/{receipt['plan_id']}/rag-batches"
    try:
        connection.request("POST", url, body, {"Content-Type": "application/json"})
        response = connection.getresponse()
        assert response.status == 403
        response.read()
        assert not service.registry
        headers = {
            "Content-Type": "application/json", "X-TokenGov-CSRF": studio._lifecycle_request_token,
            "Origin": f"http://127.0.0.1:{server.server_port}",
        }
        connection.request("POST", url, body, headers)
        response = connection.getresponse()
        assert response.status == 201
        result = json.loads(response.read())
        assert result["execution_status"] == "blocked"
        assert "SENTINEL" not in json.dumps(result)
        assert artifacts[0][2]["path"] == result["evidence"]["location"]
        connection.request("GET", f"/api/runs/{result['run_id']}")
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["result"] == result
        connection.request("GET", "/api/rag-batches/connection")
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["ready"] is False
        connection.request("GET", f"/api/rag-batches/connection?plan_id={receipt['plan_id']}")
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["expected_model"] == receipt["prediction"]["model"]
        connection.request("GET", "/api/rag-batches/connection?plan_id=a&plan_id=b")
        response = connection.getresponse()
        assert response.status == 400
        response.read()
    finally:
        connection.close()
        server.shutdown()
        thread.join()
        server.server_close()
