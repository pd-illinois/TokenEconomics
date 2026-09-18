from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from rag import agent_batch as batch
from rag import agent_batch_measurement as runtime
from rag import agent_batch_probe as probe
from test_agent_batch import agent_config, provider_response
from test_books_playground import ROOT, proof
from test_route_governance import _receipt


@pytest.fixture(autouse=True)
def no_live_batch_publication(monkeypatch):
    monkeypatch.delenv("RAG_BATCH_EVIDENCE_ACCOUNT_URL", raising=False)
    monkeypatch.delenv("RAG_BATCH_EVIDENCE_CONTAINER", raising=False)
    monkeypatch.delenv("TOKENGOV_RAG_AZURE_CLI_SUBSCRIPTION", raising=False)


def policy():
    return {
        "schema_version": "workload-measurement-policy.v1", "mode": "measurement_only",
        "workload_scope": "studio_batches", "max_questions": 10, "max_output_tokens": 1024,
        "max_elapsed_seconds": 300, "observed_model_cost_stop_usd": 0.25,
        "expires_at": "2099-01-01T00:00:00Z", "acknowledge_incomplete_costs": True,
        "hard_spend_cap_guaranteed": False, "operational_promotion": False,
    }


def configs():
    kb = {"name": "books-knowledge-base-top5", "knowledgeSources": [{"name": "books-source"}],
          "outputMode": "extractiveData", "retrievalReasoningEffort": {"kind": "minimal"},
          "@odata.etag": "kb-1", "retrieveDefaults": {"maxRuntimeInSeconds": 30,
                                                    "maxOutputDocuments": 5, "maxOutputSizeInTokens": 8000}}
    source = {"name": "books-source", "kind": "searchIndex",
              "@odata.etag": "source-1",
              "searchIndexParameters": {"searchIndexName": "books", "queryType": "semantic",
                                        "searchFields": [{"name": "content"}, {"name": "embedding"}]}}
    index = {
        "name": "books",
        "@odata.etag": "index-1",
        "fields": [{"name": "embedding", "type": "Collection(Edm.Single)", "searchable": True,
                    "dimensions": 1536, "vectorSearchProfile": "vector-profile"},
                   {"name": "content", "type": "Edm.String", "searchable": True}],
        "semantic": {"defaultConfiguration": "books-semantic", "configurations": [{
            "name": "books-semantic", "prioritizedFields": {"prioritizedContentFields": [{"fieldName": "content"}]},
        }]},
        "vectorSearch": {
            "algorithms": [{"name": "hnsw", "kind": "hnsw"}],
            "profiles": [{"name": "vector-profile", "algorithm": "hnsw", "vectorizer": "books-vectorizer"}],
            "vectorizers": [{"name": "books-vectorizer", "kind": "azureOpenAI", "azureOpenAIParameters": {
                "resourceUri": "https://test.openai.azure.com", "deploymentId": "text-embed",
                "modelName": "text-embedding-3-small",
            }}],
        },
    }
    return kb, source, index


def verified(_):
    return {
        **agent_config(None), "retrieval_mode": "managed_mcp_hybrid_verified",
        "retrieval_evidence": probe.verify_hybrid(*configs()),
    }


@pytest.fixture
def measured(proof):
    service, _, loaded, _, _, register = proof
    session = service.plans.create_session("report-measurement", "Measured RAG", {})
    forecast = _receipt("foundry", ready=False)
    forecast["intake"] = {"confirmed_profile": {"agent_pattern": "rag_pipeline"}}
    forecast["prediction"].update(model="gpt-4.1-mini", pricing_version="2026-08-25.2",
                                  prediction_id="prediction-measurement", cost_per_call={"mean": 0.001})
    _, receipt = service.plans.complete(session, forecast)
    loaded.document["admission"]["allowed_models"] = ["gpt-4.1-mini"]
    loaded.document["measurement"] = policy()
    return service, receipt, loaded, register


def response():
    return {**provider_response(), "model": "gpt-4.1-mini"}


def run(measured, *, questions=None, request_id=None, create=None, **kwargs):
    service, receipt, loaded, register = measured
    client = SimpleNamespace(responses=SimpleNamespace(create=create or (lambda **_: response())))
    return batch.execute(
        service, receipt["plan_id"], {"request_id": request_id or str(uuid4()), "questions": questions or ["Private?"]},
        loaded, "local-evaluator", ROOT, register, probe=kwargs.pop("probe", verified),
        client_factory=kwargs.pop("client_factory", lambda _: client),
        policy_loader=kwargs.pop("policy_loader", lambda _: loaded), **kwargs,
    )


def test_real_boundary_request_is_pinned_store_false_no_conversation(measured):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            assert len(list(measured[0].run_root.glob("run-*/questions/*.json"))) == 1
        return response()

    result = run(measured, questions=["a", "b"], create=create)
    assert result["schema_version"] == "rag-agent-batch.v2"
    assert result["execution_status"] == "completed"
    assert result["evidence_classification"] == "measured"
    assert len(calls) == 2
    for call in calls:
        assert set(call) == {"input", "store", "max_output_tokens", "timeout", "extra_body"}
        assert call["store"] is False
        assert call["max_output_tokens"] == 1024
        assert call["extra_body"]["agent_reference"] == {
            "type": "agent_reference", "name": "tokengov-books-rag-agent", "version": "4",
        }
    assert result["acceptance_status"] == "not_evaluated"
    assert result["operational_promotion"] is False
    assert result["metrics"][0]["embedding_tokens"] is None
    assert result["pricing"]["allocation_scope"] == "response_model_only_not_task_total"
    # 80 uncached input at $0.4/M, 20 cached at $0.1/M, 30 output at $1.6/M.
    assert result["allocations"][0]["model_allocation_usd"] == pytest.approx(0.000082)
    assert result["observed_model_allocation_usd"] == pytest.approx(0.000164)
    assert batch.validate_result(result) == result


def test_v2_campaign_policy_supports_one_complete_25_question_batch(measured):
    measured[2].document["measurement"] = {
        "schema_version": "workload-measurement-policy.v2",
        "mode": "measurement_only", "workload_scope": "studio_campaigns",
        "max_questions": 25, "max_output_tokens": 1024,
        "max_elapsed_seconds": 600, "observed_model_cost_stop_usd": 0.25,
        "max_campaign_repetitions": 100, "max_campaign_questions": 2500,
        "max_evaluation_runs": 100, "max_evaluation_rows_per_run": 25,
        "campaign_observed_model_cost_stop_usd": 25,
        "require_explicit_campaign_id": True,
        "expires_at": "2099-01-01T00:00:00Z",
        "acknowledge_incomplete_costs": True,
        "hard_spend_cap_guaranteed": False, "operational_promotion": False,
    }
    calls = []
    result = run(
        measured, questions=[f"question {number}" for number in range(25)],
        create=lambda **kwargs: calls.append(kwargs) or response(),
    )
    assert result["execution_status"] == "completed"
    assert result["questions_count"] == len(calls) == 25
    assert batch.validate_result(result) == result


@pytest.mark.parametrize("mutation", [
    lambda p: p.document.pop("measurement"),
    lambda p: p.document["measurement"].update(expires_at="2000-01-01T00:00:00Z"),
    lambda p: p.document["measurement"].update(max_output_tokens=True),
    lambda p: p.document["measurement"].update(max_questions=11),
    lambda p: p.document["measurement"].update(hard_spend_cap_guaranteed=True),
])
def test_absent_expired_malformed_policy_zero_clients(measured, mutation):
    mutation(measured[2])
    result = run(measured, client_factory=lambda _: pytest.fail("No client allowed"))
    assert result["execution_status"] == "blocked"
    assert all(m["status"] == "not_dispatched" for m in result["metrics"])


def test_authorized_question_bound_zero_calls(measured):
    measured[2].document["measurement"]["max_questions"] = 1
    result = run(measured, questions=["one", "two"], client_factory=lambda _: pytest.fail("No client allowed"))
    assert result["stop_reason"] == "blocked"


def test_opt_in_observer_receives_content_after_metrics_journal_without_persisting_it(measured):
    captured = []

    def observe(**item):
        assert list(measured[0].run_root.glob("run-*/questions/01.json"))
        captured.append(item)
        item["metric"]["input_tokens"] = 999999
        item["response"]["output"] = []

    result = run(measured, questions=["Private evaluation question?"], response_observer=observe)
    assert result["execution_status"] == "completed"
    assert captured[0]["run_id"] == result["run_id"]
    assert captured[0]["question"] == "Private evaluation question?"
    assert captured[0]["question_number"] == 1
    assert result["metrics"][0]["input_tokens"] != 999999
    for path in (measured[0].run_root / result["run_id"]).rglob("*.json"):
        assert "Private evaluation question?" not in path.read_text()
    assert result["acceptance_status"] == "not_evaluated"


def test_observer_failure_stops_later_dispatch_and_never_replays_capture(measured):
    calls = []
    request_id = str(uuid4())

    def capture(**_):
        raise ValueError("Evaluation payload exceeds the bounded content allowance")

    result = run(measured, request_id=request_id, questions=["a", "b"],
                 create=lambda **kw: calls.append(kw) or response(), response_observer=capture)
    assert len(calls) == 1
    assert result["stop_reason"] == "evaluation_capture_failed"
    assert result["execution_status"] == "partial"
    assert result["metrics"][1]["status"] == "not_dispatched"
    replay = run(measured, request_id=request_id, questions=["a", "b"],
                 response_observer=lambda **_: pytest.fail("Replays cannot recapture responses"),
                 client_factory=lambda _: pytest.fail("Replays cannot dispatch"))
    assert replay == result


def test_expired_authority_does_not_capture_evaluation_content(measured):
    measured[2].document["measurement"]["expires_at"] = "2000-01-01T00:00:00Z"
    result = run(measured, response_observer=lambda **_: pytest.fail("No evaluation content"))
    assert result["execution_status"] == "blocked"


def test_capture_binds_sdk_request_id_and_response_id_to_sealed_metrics(measured):
    from rag.response_capture import CapturedResponses

    collector = CapturedResponses(
        [{"case_id": "fact-1", "question": "Who?"}], allow_content_evaluation=True,
    )
    sdk_response = SimpleNamespace(
        model_dump=lambda: {**response(), "id": "resp-measured-1"},
        _request_id="req-measured-1",
    )
    result = run(measured, questions=["Who?"], create=lambda **_: sdk_response,
                 response_observer=collector)
    assert result["execution_status"] == "completed"
    manifest = collector.manifest(result, dataset_hash="a" * 64)
    assert manifest["batch_hash"] == result["evidence"]["content_hash"]
    assert manifest["rows"][0]["provider_response_id"] == "resp-measured-1"
    assert manifest["rows"][0]["provider_request_id"] == "req-measured-1"
    assert "req-measured-1" not in json.dumps(result)


def test_observed_stop_is_between_calls_not_a_spend_cap(measured):
    operational_budget = copy.deepcopy(measured[2].document["execution"]["budget"])
    measured[2].document["measurement"]["observed_model_cost_stop_usd"] = 0.000001
    calls = []
    result = run(measured, questions=["one", "two"], create=lambda **kw: calls.append(kw) or response())
    assert len(calls) == 1
    assert result["execution_status"] == "partial"
    assert result["stop_reason"] == "observed_model_cost_stop"
    assert result["observed_model_allocation_usd"] > 0.000001
    assert measured[2].document["execution"]["budget"] == operational_budget


@pytest.mark.parametrize("mutate", [
    lambda r: r.pop("usage"),
    lambda r: r["usage"].pop("input_tokens_details"),
    lambda r: r["usage"]["input_tokens_details"].update(cached_tokens=999),
    lambda r: r["usage"]["output_tokens_details"].update(reasoning_tokens=999),
    lambda r: r.update(model="unknown-provider-alias"),
    lambda r: r.pop("model"),
])
def test_unknown_or_ambiguous_usage_stops_next_call(measured, mutate):
    payload = response()
    mutate(payload)
    calls = []
    result = run(measured, questions=["one", "two"], create=lambda **kw: calls.append(kw) or payload)
    assert len(calls) == 1
    assert result["stop_reason"] == "usage_or_pricing_unavailable"
    assert result["allocations"][0]["model_allocation_usd"] is None
    assert result["metrics"][1]["status"] == "not_dispatched"


@pytest.mark.parametrize("failure", [
    "exception_body", "partial", "tool_failed", "close_failed", "completed_false", "tool_completed_false",
])
def test_metered_failures_preserve_usage_without_retry(measured, failure):
    payload = response()
    calls = []
    if failure == "partial":
        payload["status"] = "incomplete"
    if failure == "tool_failed":
        payload["output"][0]["error"] = "SENTINEL_PROVIDER_ERROR"
    if failure == "completed_false":
        payload["completed"] = False
    if failure == "tool_completed_false":
        payload["output"][0]["completed"] = False

    def create(**kw):
        calls.append(kw)
        if failure == "exception_body":
            exc = RuntimeError("SENTINEL_EXCEPTION")
            exc.body = payload
            raise exc
        return payload

    def close():
        raise RuntimeError("SENTINEL_CLOSE")

    client = SimpleNamespace(responses=SimpleNamespace(create=create), close=close)
    result = run(measured, questions=["one", "two"] if failure != "close_failed" else ["one"],
                 client_factory=lambda _: client)
    assert len(calls) == 1
    assert result["metrics"][0]["input_tokens"] == 100
    assert result["observed_model_allocation_usd"] > 0
    if failure != "close_failed":
        assert result["execution_status"] == "partial"
        assert result["metrics"][1]["status"] == "not_dispatched"
    else:
        assert result["execution_status"] == "completed"
    assert "SENTINEL" not in json.dumps(result)


def test_plain_exception_marks_unknown_billable_usage_and_does_not_retry(measured):
    def fail(**_):
        raise RuntimeError("SENTINEL_ERROR")
    result = run(measured, questions=["a", "b"], create=fail)
    assert result["stop_reason"] == "provider_call_failed"
    assert result["metrics"][0]["status"] == "failed_or_partial"
    assert result["metrics"][0]["input_tokens"] is None
    assert result["metrics"][1]["status"] == "not_dispatched"
    assert result["failure_cost_note"] == "provider_failure_usage_may_be_charged"


def test_sdk_unwrapped_error_retains_sibling_metered_usage(measured):
    import httpx
    from openai import OpenAI

    payload = {**response(), "error": {"message": "SENTINEL_PRIVATE_ERROR", "type": "server_error"}}
    with OpenAI(api_key="offline-test-placeholder", max_retries=0) as client:
        error = client._make_status_error_from_response(httpx.Response(
            500, json=payload,
            request=httpx.Request("POST", "https://example.invalid/responses"),
        ))
    assert error.body == payload["error"]
    calls = []

    def fail(**kwargs):
        calls.append(kwargs)
        raise error

    result = run(measured, questions=["one", "two"], create=fail)
    assert len(calls) == 1
    assert result["metrics"][0]["input_tokens"] == 100
    assert result["metrics"][0]["output_tokens"] == 30
    assert result["allocations"][0]["model_allocation_usd"] == pytest.approx(0.000082)
    assert result["metrics"][1]["status"] == "not_dispatched"
    assert "SENTINEL" not in json.dumps(result)


def test_missing_cache_write_counts_remain_unknown_but_standard_input_is_priceable(measured):
    payload = response()
    payload["usage"]["input_tokens_details"].pop("cache_write_tokens")
    result = run(measured, create=lambda **_: payload)
    assert result["metrics"][0]["cache_write_input_tokens"] is None
    assert result["pricing"]["rates_per_million"]["cache_write"] is None
    assert result["allocations"][0]["model_allocation_usd"] == pytest.approx(0.000082)
    assert result["execution_status"] == "completed"


@pytest.mark.parametrize("output", [[], None, [
    {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Ungrounded answer"}]},
]])
def test_completed_response_without_retrieval_is_not_completed_rag(measured, output):
    payload = response()
    payload["output"] = output
    result = run(measured, questions=["one", "two"], create=lambda **_: payload)
    assert result["metrics"][0]["input_tokens"] == 100
    assert result["metrics"][0]["status"] == "failed_or_partial"
    assert result["allocations"][0]["model_allocation_usd"] > 0
    assert result["execution_status"] == "partial"
    assert result["stop_reason"] == "retrieval_evidence_unavailable"
    assert result["metrics"][1]["status"] == "not_dispatched"
    assert result["retrieval_evidence_scope"] == "configuration_only_not_per_query_hybrid_proof"


def test_configuration_readiness_does_not_prove_per_query_hybrid(measured):
    result = batch.connection_status(probe=verified, receipt=measured[1], loaded=measured[2])
    assert result["ready"] is True
    assert result["configuration_ready"] is result["measurement_authorized"] is True
    assert result["measurement_authorization_status"] == "authorized"
    assert result["retrieval_evidence_scope"] == "configuration_only_not_per_query_hybrid_proof"
    assert result["inference_performed"] is False
    assert result["storage_warnings"][0]["code"] == "evidence_destination_unconfigured"


@pytest.mark.parametrize("mutation,code", [
    (lambda loaded: loaded.document.pop("measurement"), "measurement_authorization_required"),
    (lambda loaded: loaded.document["measurement"].update(expires_at="2000-01-01T00:00:00Z"),
     "measurement_authorization_expired"),
    (lambda loaded: loaded.document["measurement"].update(max_output_tokens=True),
     "measurement_authorization_invalid"),
    (lambda loaded: loaded.provenance.update(source="local"), "azure_authority_required"),
])
def test_connection_distinguishes_authorization_blockers(measured, mutation, code):
    mutation(measured[2])
    result = batch.connection_status(probe=verified, receipt=measured[1], loaded=measured[2])
    assert code in {row["code"] for row in result["blockers"]}
    assert result["ready"] is False
    assert result["configuration_ready"] is True
    assert result["measurement_authorized"] is False
    assert result["measurement_authorization_status"] == {
        "measurement_authorization_required": "absent",
        "measurement_authorization_expired": "expired",
        "measurement_authorization_invalid": "invalid",
        "azure_authority_required": "authority_unavailable",
    }[code]


def test_readonly_configuration_can_be_ready_without_measurement_authorization(measured):
    result = batch.connection_status(probe=verified, receipt=measured[1])
    assert result["configuration_ready"] is True
    assert result["measurement_authorized"] is False
    assert result["ready"] is False


def test_authorized_scope_does_not_make_unverified_configuration_ready(measured):
    result = batch.connection_status(probe=agent_config, receipt=measured[1], loaded=measured[2])
    assert result["configuration_ready"] is False
    assert result["measurement_authorized"] is True
    assert result["ready"] is False


@pytest.mark.parametrize("change", ["etag", "version", "content", "unavailable"])
def test_live_policy_reload_stops_on_change_or_unavailability(measured, change):
    calls = []
    original = copy.deepcopy(measured[2])
    reads = []
    def load(binding):
        reads.append(binding)
        if len(reads) == 1:
            return original
        if change == "unavailable":
            raise RuntimeError("SENTINEL_AUTHORITY_ERROR")
        changed = copy.deepcopy(original)
        if change == "etag":
            changed.provenance["etag"] = "changed-etag"
        elif change == "version":
            changed.document["version"] = "changed-version"
        else:
            changed.document["measurement"]["max_output_tokens"] = 1025
        return changed
    result = run(measured, questions=["a", "b"], policy_loader=load,
                 create=lambda **kw: calls.append(kw) or response())
    assert len(calls) == 1
    assert len(reads) == 2
    assert result["stop_reason"] == "policy_changed_or_unavailable"
    assert result["metrics"][0]["input_tokens"] == 100
    assert result["metrics"][1]["status"] == "not_dispatched"
    assert "SENTINEL" not in json.dumps(result)


@pytest.mark.parametrize("change", ["agent_version", "source_etag", "unavailable"])
def test_fresh_pinned_agent_check_stops_on_drift(measured, change):
    probes, calls = [], []
    def inventory(config):
        probes.append(config)
        value = verified(config)
        if len(probes) == 3:  # Initial inventory, first dispatch, second dispatch.
            if change == "unavailable":
                raise RuntimeError("SENTINEL_INVENTORY_ERROR")
            if change == "agent_version":
                value["agent_version"] = "5"
            else:
                retrieval = value["retrieval_evidence"]
                retrieval["knowledge_source_etag"] = "changed"
                retrieval["content_hash"] = batch._digest({k: v for k, v in retrieval.items() if k != "content_hash"})
        return value
    result = run(measured, questions=["a", "b"], probe=inventory,
                 create=lambda **kw: calls.append(kw) or response())
    assert len(calls) == 1
    assert probes[1]["agent_version"] == probes[2]["agent_version"] == "4"
    assert result["stop_reason"] == "agent_changed_or_unavailable"
    assert result["metrics"][0]["input_tokens"] == 100
    assert result["metrics"][1]["status"] == "not_dispatched"


def test_natural_expiry_without_document_change_stops_before_dispatch(measured, monkeypatch):
    from costgov.policy_store import PolicyLoadError
    original = runtime.measurement_authorization
    checks = []
    def authorize(loaded):
        checks.append(True)
        if len(checks) == 3:  # Connection, first dispatch, second dispatch.
            raise PolicyLoadError("expired")
        return original(loaded)
    monkeypatch.setattr(runtime, "measurement_authorization", authorize)
    result = run(measured, questions=["a", "b"])
    assert result["stop_reason"] == "measurement_authorization_expired"
    assert result["metrics"][1]["status"] == "not_dispatched"


def test_metadata_checks_cannot_consume_time_budget_then_dispatch(measured, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    def load(_):
        clock[0] += 301
        return measured[2]
    result = run(measured, policy_loader=load, create=lambda **_: pytest.fail("Expired time budget"))
    assert result["stop_reason"] == "elapsed_time_limit"
    assert result["metrics"][0]["status"] == "not_dispatched"


def test_cloud_upload_rejects_noncanonical_local_content(measured):
    result = run(measured)
    path = measured[0].run_root / result["run_id"] / "result.json"
    path.write_text(json.dumps(result, indent=2))  # Deliberately corrupt a test-owned artifact.
    with pytest.raises(ValueError, match="canonical"):
        batch.publish_result(measured[0], result["run_id"], publisher=lambda *_: pytest.fail("No upload"))


def test_policy_reload_sdk_has_no_retries_and_checks_exact_authority(measured, monkeypatch):
    import azure.appconfiguration
    captured = {}
    binding = batch._policy_binding(measured[1], measured[2])
    class Context:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
    class Client(Context):
        def __init__(self, endpoint, credential, **kwargs):
            captured.update(kwargs)
            assert endpoint == binding["endpoint"]
        def get_configuration_setting(self, **kwargs):
            assert kwargs == {"key": binding["key"], "label": binding["label"]}
            return SimpleNamespace(value=json.dumps(measured[2].document),
                                   etag=binding["etag"], content_type="application/json")
    monkeypatch.setattr(azure.appconfiguration, "AzureAppConfigurationClient", Client)
    monkeypatch.setattr(batch, "_credential", Context)
    monkeypatch.setenv("TOKENGOV_POLICY_SOURCE", "azure")
    monkeypatch.setenv("AZURE_APPCONFIG_ENDPOINT", binding["endpoint"])
    monkeypatch.setenv("TOKENGOV_POLICY_LABEL", binding["label"])
    monkeypatch.setenv("TOKENGOV_POLICY_KEY", binding["key"])
    fresh = runtime.reload_policy(binding)
    assert batch._policy_binding(measured[1], fresh) == binding
    assert captured["retry_total"] == 0
    assert captured["logging_enable"] is captured["tracing_enable"] is False
    monkeypatch.setenv("TOKENGOV_POLICY_LABEL", "changed")
    with pytest.raises(ValueError, match="authority changed"):
        runtime.reload_policy(binding)


def test_duplicate_uuid_never_invokes_even_if_authorization_has_expired(measured):
    request_id = str(uuid4())
    original = run(measured, request_id=request_id)
    measured[2].document["measurement"]["expires_at"] = "2000-01-01T00:00:00Z"
    measured[0].registry.clear()
    duplicate = run(measured, request_id=request_id, questions=["CHANGED"],
                    probe=lambda _: pytest.fail("no inventory"), create=lambda **_: pytest.fail("no inference"))
    assert duplicate == original
    assert measured[0].registry[original["run_id"]]["result"] == original


def test_metrics_and_hashes_never_contain_question_answer_tool_or_exception(measured, monkeypatch):
    monkeypatch.setenv("RAG_BATCH_EVIDENCE_ACCOUNT_URL", "https://evidenceaccount.blob.core.windows.net")
    monkeypatch.setenv("RAG_BATCH_EVIDENCE_CONTAINER", "metrics")
    uploaded = []
    question = "SENTINEL_QUESTION sensitive test words"
    result = run(measured, questions=[question], publisher=lambda destination, name, data: uploaded.append(data) or "published")
    documents = [p.read_text() for p in measured[0].run_root.rglob("*.json")]
    documents += [str(measured[0].registry)] + [data.decode() for data in uploaded]
    for document in documents:
        assert "SENTINEL" not in document
        assert hashlib.sha256(question.encode()).hexdigest() not in document
    assert result["metrics"][0]["input_words"] == len(question.split())


def test_cloud_failure_outbox_retry_is_upload_only_and_local_result_immutable(measured, monkeypatch):
    monkeypatch.setenv("RAG_BATCH_EVIDENCE_ACCOUNT_URL", "https://evidenceaccount.blob.core.windows.net")
    monkeypatch.setenv("RAG_BATCH_EVIDENCE_CONTAINER", "metrics")
    def fail(*_):
        raise RuntimeError("SENTINEL_CLOUD_ERROR")
    result = run(measured, publisher=fail)
    directory = measured[0].run_root / result["run_id"]
    before = (directory / "result.json").read_bytes()
    assert batch.publication_status(measured[0], result["run_id"])["status"] == "publication_failed"
    published = []
    status = batch.publish_result(measured[0], result["run_id"],
                                 publisher=lambda *args: published.append(args) or "published")
    assert status["status"] == "published"
    assert len(published) == 1
    assert batch.publish_result(measured[0], result["run_id"], publisher=lambda *_: pytest.fail("already published")) == status
    assert (directory / "result.json").read_bytes() == before
    assert len(list((directory / "publication").glob("*.json"))) == 3


def test_journal_failure_stops_and_preserves_metered_usage_in_final(measured, monkeypatch):
    original = batch._write_once
    def write(path, payload):
        if path.parent.name == "questions":
            raise OSError("SENTINEL_DISK")
        return original(path, payload)
    monkeypatch.setattr(batch, "_write_once", write)
    result = run(measured, questions=["a", "b"])
    assert result["stop_reason"] == "metric_journal_failed"
    assert result["metrics"][0]["input_tokens"] == 100
    assert result["metrics"][1]["status"] == "not_dispatched"


@pytest.mark.parametrize("path", ["root", "pricing", "rates", "authorization", "allocation", "retrieval"])
def test_new_boundaries_reject_arbitrary_content_fields(measured, path):
    result = run(measured)
    target = {
        "root": result, "pricing": result["pricing"], "rates": result["pricing"]["rates_per_million"],
        "authorization": result["measurement_authorization"], "allocation": result["allocations"][0],
        "retrieval": result["agent"]["retrieval_evidence"],
    }[path]
    target["content"] = "SENTINEL"
    with pytest.raises(ValueError):
        batch.validate_result(result)


def test_old_v1_validation_and_file_bytes_are_preserved(proof):
    service, receipt, loaded, _, _, register = proof
    result = batch._execute_v1(service, receipt["plan_id"],
                               {"request_id": str(uuid4()), "questions": ["private"]},
                               loaded, "local", ROOT, register, probe=agent_config)
    path = service.run_root / result["run_id"] / "result.json"
    before = path.read_bytes()
    assert batch.validate_result(result) == result
    changed = copy.deepcopy(result)
    changed["agent"]["retrieval_mode"] = "managed_mcp_hybrid_verified"
    with pytest.raises(ValueError):
        batch.validate_result(changed)
    batch.publish_result(service, result["run_id"])
    assert path.read_bytes() == before


def test_independent_hybrid_verification_rejects_lexical_missing_vectorizer_and_wrong_source():
    original = configs()
    assert probe.verify_hybrid(*original)["query_type"] == "semantic"
    for mutation in (
        lambda kb, source, index: source["searchIndexParameters"].update(queryType="simple"),
        lambda kb, source, index: index["vectorSearch"].pop("vectorizers"),
        lambda kb, source, index: source["searchIndexParameters"].update(searchIndexName="other"),
        lambda kb, source, index: index["vectorSearch"]["vectorizers"][0]["azureOpenAIParameters"].update(apiKey="SECRET"),
        lambda kb, source, index: kb.update(outputMode="answerSynthesis"),
    ):
        candidate = copy.deepcopy(original)
        mutation(*candidate)
        with pytest.raises(ValueError):
            probe.verify_hybrid(*candidate)


def test_sdk_retry_and_trace_settings_are_explicit(monkeypatch):
    import azure.ai.projects
    captured = {}
    class Context:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
    class Project(Context):
        def __init__(self, **kwargs):
            captured["project"] = kwargs
        def get_openai_client(self, **kwargs):
            captured["openai"] = kwargs
            return Context()
    monkeypatch.setattr(azure.ai.projects, "AIProjectClient", Project)
    monkeypatch.setattr(batch, "_credential", Context)
    with runtime.clients({"project_endpoint": "https://example.services.ai.azure.com/api/projects/test",
                          "timeout_seconds": 10}):
        assert runtime._PRIVATE_CALL.get() is True
    assert captured["project"]["retry_total"] == 0
    assert captured["project"]["tracing_enable"] is False
    assert captured["project"]["logging_enable"] is False
    assert captured["openai"]["max_retries"] == 0
    assert captured["openai"]["http_client"].follow_redirects is False
    assert not runtime._PRIVATE_CALL.get()


def test_sdk_debug_content_is_suppressed(caplog):
    with caplog.at_level(logging.DEBUG, logger="openai._base_client"):
        with runtime.private_call():
            logging.getLogger("openai._base_client").debug("SENTINEL_QUESTION")
    assert "SENTINEL" not in caplog.text


def test_all_ten_questions_execute_sequentially(measured):
    calls = []
    result = run(measured, questions=[f"q{n}" for n in range(10)],
                 create=lambda **kw: calls.append(kw["input"]) or response())
    assert calls == [f"q{n}" for n in range(10)]
    assert result["execution_status"] == "completed"
    assert len(list(measured[0].run_root.glob("run-*/questions/*.json"))) == 10


def test_concurrent_live_request_uuid_cannot_duplicate_charges(measured):
    entered, release = threading.Event(), threading.Event()
    calls, results = [], []
    request_id = str(uuid4())
    def create(**kwargs):
        calls.append(kwargs)
        entered.set()
        assert release.wait(5)
        return response()
    worker = threading.Thread(target=lambda: results.append(run(measured, request_id=request_id, create=create)))
    worker.start()
    assert entered.wait(5)
    try:
        with pytest.raises(ValueError, match="reserved"):
            run(measured, request_id=request_id, create=lambda **_: pytest.fail("Duplicate call"))
    finally:
        release.set()
        worker.join()
    assert len(calls) == len(results) == 1


def test_time_budget_stops_subsequent_call(measured, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    def create(**_):
        clock[0] += 301
        return response()
    result = run(measured, questions=["a", "b"], create=create)
    assert result["stop_reason"] == "elapsed_time_limit"
    assert result["metrics"][0]["input_tokens"] == 100
    assert result["metrics"][1]["status"] == "not_dispatched"


def test_expiry_is_rechecked_before_each_call(measured):
    def create(**_):
        measured[2].document["measurement"]["expires_at"] = "2000-01-01T00:00:00Z"
        return response()
    result = run(measured, questions=["a", "b"], create=create)
    assert result["stop_reason"] == "policy_changed_or_unavailable"
    assert result["metrics"][1]["status"] == "not_dispatched"
    assert result["measurement_authorization"]["expires_at"] == "2099-01-01T00:00:00Z"


def test_missing_pricing_stops_before_client_creation(measured, monkeypatch):
    def missing(*_):
        raise ValueError("SENTINEL_PRICE")
    monkeypatch.setattr(runtime, "prices", missing)
    result = run(measured, client_factory=lambda _: pytest.fail("No billable client"))
    assert result["execution_status"] == "blocked"
    assert result["pricing"] is None
    assert result["blockers"][-1]["code"] == "pricing_unavailable"


def test_create_only_local_publication_never_overwrites(tmp_path):
    path = tmp_path / "result.json"
    batch._write_once(path, {"first": True})
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        batch._write_once(path, {"second": True})
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".staging-*"))


@pytest.mark.parametrize("same", [True, False])
def test_blob_create_only_conflicts_are_verified_without_overwrite(monkeypatch, same):
    import azure.storage.blob
    from azure.core.exceptions import ResourceExistsError
    captured = {}
    data = b'{"safe":"metrics"}'
    class Context:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
    class Blob(Context):
        def __init__(self, **kwargs):
            captured.update(kwargs)
        def upload_blob(self, payload, **kwargs):
            assert payload == data and kwargs["overwrite"] is False
            raise ResourceExistsError("SENTINEL")
        def get_blob_properties(self, **_):
            return SimpleNamespace(size=len(data))
        def download_blob(self, **kwargs):
            assert kwargs["length"] == len(data)
            return SimpleNamespace(readall=lambda: data if same else b"x" * len(data))
    monkeypatch.setattr(azure.storage.blob, "BlobClient", Blob)
    monkeypatch.setattr(batch, "_credential", Context)
    status = runtime._blob_publish(
        {"location": "https://evidenceaccount.blob.core.windows.net/metrics"}, "studio_runs/run-id/result.json", data)
    assert status == ("published" if same else "conflict")
    assert captured["retry_total"] == 0
    assert captured["logging_enable"] is False


@pytest.mark.parametrize("auth,lexical", [("ProjectManagedIdentity", False), ("ApiKey", False),
                                         ("ProjectManagedIdentity", True)])
def test_probe_reads_real_dependency_chain_and_rejects_missing_hybrid(monkeypatch, auth, lexical):
    import azure.ai.projects
    kb, source, index = configs()
    kb["name"] = "books-knowledge-base-topk1"
    source["searchIndexParameters"].pop("queryType")
    if lexical:
        source["searchIndexParameters"]["searchFields"] = [{"name": "content"}]
    url = probe._SEARCH + "/knowledgebases/books-knowledge-base-topk1/mcp?api-version=2026-08-01-preview"
    connection_name = "books-mcp"
    connection_id = ("/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/test"
                     "/providers/Microsoft.CognitiveServices/accounts/test/projects/test/connections/books-mcp")
    tool = {"type": "mcp", "server_url": url, "project_connection_id": connection_name,
            "require_approval": "never", "allowed_tools": {"tool_names": ["knowledge_base_retrieve"]}}
    class Context:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
    class Project(Context):
        def __init__(self, **_):
            self.agents = SimpleNamespace(
                get=lambda _: {"state": "enabled", "versions": {"latest": {"version": "4"}}},
                get_version=lambda *args: {"status": "active", "definition": {
                    "kind": "prompt", "model": "rag-deployment", "tools": [tool],
                }},
            )
            self.deployments = SimpleNamespace(get=lambda _: {"modelName": "gpt-4.1-mini", "modelVersion": "2025-04-14"})
            self.connections = SimpleNamespace(get=lambda _: {
                "target": url, "credentials": {"type": auth}, "id": connection_id,
            })
    urls = []
    def read(credential, path, scope):
        urls.append(path)
        if path.startswith("https://management.azure.com"):
            return {"properties": {"authType": "ProjectManagedIdentity", "target": url,
                                   "category": "RemoteTool", "audience": "https://search.azure.com/"}}
        if "/knowledgebases/" in path:
            return kb
        if "/knowledgesources/" in path:
            return source
        return index
    monkeypatch.setattr(azure.ai.projects, "AIProjectClient", Project)
    monkeypatch.setattr(batch, "_credential", Context)
    monkeypatch.setattr(probe, "_get", read)
    result = probe.probe(batch.configuration())
    expected = auth == "ProjectManagedIdentity" and not lexical
    assert (result["retrieval_mode"] == "managed_mcp_hybrid_verified") is expected
    if expected:
        assert len(urls) == 4
        assert all("api-version=2026-08-01-preview" in url for url in urls[1:])
