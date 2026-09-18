from __future__ import annotations

import copy
import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

import studio
from costgov.planning import PlanStore
from costgov.studio_lifecycle import StudioLifecycle
from costgov.observe_economics import load_verified_run_evidence
from rag import books_playground as playground
from test_route_governance import _receipt
from test_studio_api import _loaded_policy

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def proof(tmp_path):
    plans = PlanStore(tmp_path / "plans")
    session = plans.create_session("report-books", "Books RAG with Luna", {})
    result = _receipt("foundry", ready=False)
    result["intake"] = {"confirmed_profile": {"agent_pattern": "rag_pipeline"}}
    result["prediction"].update(
        model=playground.MODEL, prediction_id="prediction-new",
        cost_per_call={"mean": 0.001},
    )
    _, receipt = plans.complete(session, result)
    policy = _loaded_policy()
    policy.document["admission"]["allowed_models"] = [playground.MODEL]
    config = json.loads((ROOT / "data" / "workload_adapters" / "studio-evidence.v1.json").read_text())
    registry = {}
    service = StudioLifecycle(tmp_path / "lifecycle", plans, registry, tmp_path / "runs", config)
    calls = []

    def search(**kwargs):
        calls.append(("search", kwargs))
        return [{"id": "book-1", "book": "Pride and Prejudice", "content": "Elizabeth Bennet is the protagonist."}]

    def response(**kwargs):
        calls.append(("model", kwargs))
        return {"id": "response-1", "status": "completed", "model": playground.MODEL,
                "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
                          "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                          "output_tokens_details": {"reasoning_tokens": 5}},
                "output": [{"content": [{"text": "Elizabeth Bennet is the protagonist. [1]"}]}]}

    clients = lambda _: (SimpleNamespace(search=search), SimpleNamespace(responses=SimpleNamespace(create=response)))

    def register(key, **updates):
        registry.setdefault(key, {}).update(updates)

    return service, receipt, policy, clients, calls, register


def run(proof, questions=None, **kwargs):
    service, receipt, policy, factory, _, register = proof
    payload = {"questions": questions or [playground.SAMPLES[0]]}
    if "request_id" in kwargs:
        payload["request_id"] = kwargs["request_id"]
    return playground.execute(
        service, receipt["plan_id"], payload,
        policy, "test-evaluator", ROOT, register, client_factory=kwargs.get("factory", factory),
    )


def test_capture_is_exact_receipt_bound_and_evaluate_remains_inconclusive(proof):
    service, receipt, policy, _, calls, _ = proof
    original = copy.deepcopy(receipt)
    result = run(proof)
    assert result["execution_status"] == "completed"
    assert len(calls) == 2
    assert calls[0][1]["query_type"] == "simple"
    assert calls[1][1]["max_output_tokens"] == 512
    assert calls[1][1]["store"] is False
    assert "agent_reference" not in calls[1][1]
    answer = result["answers"][0]
    assert answer["answer"].endswith("[1]")
    assert answer["usage"]["output_tokens_details"]["reasoning_tokens"] == 5
    assert answer["usage"]["embedding_tokens"] == 0
    assert answer["embedding_usage"] == "not_applicable_lexical_retrieval"
    trajectories, outcomes, entries = load_verified_run_evidence(result, service.run_root / result["run_id"])
    assert not outcomes
    assert trajectories[0].prediction_binding.receipt_id == receipt["receipt_id"]
    assert trajectories[0].prediction_binding.content_hash == receipt["content_hash"]
    assert trajectories[0].policy_binding.etag == policy.provenance["etag"]
    assert {entry.meter_family.value for entry in entries} >= {"direct_token", "retrieval", "resource"}
    assert any(entry.cost_coverage.value == "unpriced" and entry.allocated_cost_usd is None for entry in entries)
    assert service.preview(receipt["plan_id"], result["run_id"])["compatible"] is True
    request = service.requirements(receipt["plan_id"], {
        "acceptance": {"definition": "Grounded answer, awaiting a versioned evaluator.",
                       "segments": [{"segment_id": answer["segment_id"], "segment_version": "segment.v1", "rule_hash": None}]},
        "budget": {"budget_usd": 0.02, "breach_tolerance": 0.05, "minimum_samples": 60,
                   "confidence_level": 0.95, "minimum_acceptance_lower_bound": 0.8},
    })
    attached = service.attach(receipt["plan_id"], {"run_id": result["run_id"], "adapter_id": service.config["adapter_id"]})
    evaluated = service.evaluate(receipt["plan_id"], {
        "requirements_id": request["id"], "attachment_id": attached["id"], "mode": "read_only",
    }, policy, "test-evaluator")
    assert evaluated["status"] == "inconclusive"
    assert evaluated["acceptance_definition_bound"] is False
    assert evaluated["complete_task_cost_coverage"] is False
    assert service.plans.get_receipt(receipt["plan_id"]) == original
    persisted = json.loads((service.run_root / result["run_id"] / "result.json").read_text())
    assert persisted["rag_playground"]["answers"] == result["answers"]
    assert service.registry[result["run_id"]]["result"]["rag_playground"] == result["rag_playground"]


def test_identical_submission_reuses_evidence_and_different_submission_is_blocked(proof):
    result = run(proof)
    assert run(proof) == result
    assert len(proof[4]) == 2
    with pytest.raises(ValueError, match="already reserved"):
        run(proof, ["Another question"])
    assert len(proof[4]) == 2


def test_browser_request_uuid_is_persisted_and_idempotent(proof):
    request_id = "5bcdce6f-2ddd-4a33-89dd-fef37a0b4bb9"
    result = run(proof, request_id=request_id)
    assert result["request_id"] == request_id
    assert result["authorization"]["request_id"] == request_id
    assert run(proof, request_id=request_id) == result
    assert len(proof[4]) == 2
    with pytest.raises(ValueError, match="already reserved"):
        run(proof, ["Changed payload"], request_id=request_id)


@pytest.mark.parametrize("request_id", [None, 3, "", "invalid", "12345678123412341234123456781234"])
def test_malformed_request_id_does_not_reserve_or_execute(proof, request_id):
    with pytest.raises(ValueError, match="UUID"):
        run(proof, request_id=request_id)
    assert not proof[4]
    assert not proof[0].run_root.exists()


def test_concurrent_submission_cannot_double_charge(proof):
    service, _, _, factory, calls, _ = proof
    entered, release = threading.Event(), threading.Event()

    def paused(config):
        entered.set()
        assert release.wait(5)
        return factory(config)

    results = []
    worker = threading.Thread(target=lambda: results.append(run(proof, factory=paused)))
    worker.start()
    assert entered.wait(5)
    try:
        with pytest.raises(ValueError, match="already reserved"):
            run(proof)
    finally:
        release.set()
        worker.join()
    assert len(calls) == 2
    assert len(results) == 1


@pytest.mark.parametrize("payload", [
    {"questions": []}, {"questions": ["a"] * 4}, {"questions": [" "]},
    {"questions": ["x" * 1201]}, {"questions": [False]},
    {"questions": ["a"], "endpoint": "https://example.com"}, {"questions": ["a"], "accepted": True},
])
def test_rejects_unbounded_or_browser_owned_configuration(proof, payload):
    service, receipt, policy, factory, calls, register = proof
    with pytest.raises(ValueError):
        playground.execute(service, receipt["plan_id"], payload, policy, "principal", ROOT, register, client_factory=factory)
    assert not calls
    assert not service.run_root.exists()


@pytest.mark.parametrize("mutation", [
    lambda p: p.provenance.update(source="local"),
    lambda p: p.provenance.update(etag=""),
    lambda p: p.document.update(status="inactive"),
    lambda p: p.document["admission"].update(allowed_models=["gpt-4.1-mini"]),
    lambda p: p.document["admission"].update(max_model_cost_per_call_usd=0.00001),
    lambda p: p.document["execution"]["budget"].update(per_tenant_usd_per_run=0.00001),
    lambda p: p.document["execution"].update(unknown_limit=5),
])
def test_azure_authority_and_budget_fail_closed_before_calls(proof, mutation):
    mutation(proof[2])
    with pytest.raises(ValueError):
        run(proof)
    assert not proof[4]


def test_three_questions_make_at_most_three_model_calls(proof):
    result = run(proof, playground.SAMPLES)
    assert len(result["answers"]) == 3
    assert [kind for kind, _ in proof[4]].count("model") == 3
    assert result["answers"][2]["segment_id"] == "rag-hard"
    with pytest.raises(ValueError):
        run(proof, ["Fourth question"])


def test_failed_provider_call_is_persisted_and_never_retried(proof):
    _, _, _, factory, calls, _ = proof

    def failed(config):
        search, model = factory(config)

        def fail(**kwargs):
            calls.append(("model", kwargs))
            raise RuntimeError("secret Authorization: bearer do-not-expose")

        model.responses.create = fail
        return search, model

    result = run(proof, playground.SAMPLES, factory=failed)
    assert result["execution_status"] == "failed_or_partial"
    assert len(result["answers"]) == 1
    assert result["answers"][0]["usage"] is None
    assert "do-not-expose" not in json.dumps(result)
    assert run(proof, playground.SAMPLES) == result
    assert len(calls) == 2
    assert proof[0].preview(proof[1]["plan_id"], result["run_id"])["compatible"]


@pytest.mark.parametrize("changes", [
    {"status": "incomplete"},
    {"usage": None},
    {"model": "gpt-4.1-mini"},
    {"usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 2}},
])
def test_invalid_provider_result_cannot_become_success(proof, changes):
    factory = proof[3]

    def altered(config):
        search, model = factory(config)
        original = model.responses.create
        model.responses.create = lambda **kwargs: {**original(**kwargs), **changes}
        return search, model

    result = run(proof, playground.SAMPLES, factory=altered)
    assert result["execution_status"] == "failed_or_partial"
    assert result["answers"][0]["status"] == "failed"
    assert len(result["answers"]) == 1
    assert len(proof[4]) == 2
    assert result["acceptance_outcomes"] == []


@pytest.mark.parametrize("model", [
    "gpt-5-6-luna", "gpt-5.6-luna", "gpt-5.6-luna-2026-07-09",
])
def test_exact_configured_alias_and_pinned_model_versions_are_supported(proof, model):
    factory = proof[3]

    def resolved(config):
        search, client = factory(config)
        original = client.responses.create
        client.responses.create = lambda **kwargs: {**original(**kwargs), "model": model}
        return search, client

    result = run(proof, factory=resolved)
    assert result["execution_status"] == "completed"
    trajectories, _, _ = load_verified_run_evidence(result, proof[0].run_root / result["run_id"])
    evidence = {item.key: json.loads(item.value_json)
                for step in trajectories[0].steps if step.operation == "foundry_direct_response"
                for item in step.evidence}
    identity = evidence["resolved_model_identity"]
    assert identity["provider_model"] == model
    assert identity["canonical_model"] == playground.MODEL
    assert identity["catalog_hash"] == result["authorization"]["pricing"]["catalog_hash"]
    assert result["authorization"]["policy"]["content_hash"] == trajectories[0].policy_binding.content_hash


@pytest.mark.parametrize("model", [
    "gpt-5-6-luna-other", "gpt-5.6-luna-2026-12-31", "gpt-4.1-mini", "another-deployment",
])
def test_unbound_models_remain_failed_and_cannot_use_luna_prices(proof, model):
    factory = proof[3]

    def wrong(config):
        search, client = factory(config)
        original = client.responses.create
        client.responses.create = lambda **kwargs: {**original(**kwargs), "model": model}
        return search, client

    result = run(proof, factory=wrong)
    assert result["execution_status"] == "failed_or_partial"
    assert result["answers"][0]["error_code"] == "provider_model_binding_mismatch"
    _, _, entries = load_verified_run_evidence(result, proof[0].run_root / result["run_id"])
    assert all(entry.allocated_cost_usd is None and entry.cost_coverage.value == "unpriced"
               for entry in entries if entry.meter_family.value == "direct_token")


def test_live_shape_cache_write_usage_is_disjoint_and_correctly_priced(proof):
    factory = proof[3]
    reported = {
        "input_tokens": 1198, "output_tokens": 99, "total_tokens": 1297,
        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 1195},
        "output_tokens_details": {"reasoning_tokens": 61},
    }

    def observed(config):
        search, client = factory(config)
        original = client.responses.create
        client.responses.create = lambda **kwargs: {
            **original(**kwargs), "model": "gpt-5-6-luna", "usage": copy.deepcopy(reported),
        }
        return search, client

    result = run(proof, factory=observed)
    assert result["execution_status"] == "completed"
    _, _, entries = load_verified_run_evidence(result, proof[0].run_root / result["run_id"])
    tokens = {entry.meter_id: entry for entry in entries if entry.meter_family.value == "direct_token"}
    assert tokens["model_input_uncached"].quantity == 3
    assert tokens["model_input_cache_read"].quantity == 0
    assert tokens["model_input_cache_write"].quantity == 1195
    assert tokens["model_output"].quantity == 99
    assert sum(entry.quantity for entry in tokens.values()) == 1297
    assert sum(entry.allocated_cost_usd for entry in tokens.values()) == pytest.approx(0.00041815)
    assert tokens["model_input_cache_write"].allocated_cost_usd == pytest.approx(0.00029875)
    assert result["authorization"]["reserved_model_cost_per_task_usd"] == pytest.approx(0.0021144)
    assert result["authorization"]["reserved_input_usd_per_million"] == 0.25
    assert result["authorization"]["token_cost_rule"] == playground.TOKEN_COST_RULE


def test_mixed_cache_read_write_partitions_do_not_double_count(proof):
    prices = playground._prices(ROOT)
    usage = {"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100,
             "input_tokens_details": {"cached_tokens": 400, "cache_write_tokens": 300},
             "output_tokens_details": {"reasoning_tokens": 60}}
    components = playground.token_price_components(usage, prices)
    assert [(meter, quantity) for meter, quantity, _, _ in components] == [
        ("model_input_uncached", 300), ("model_input_cache_read", 400),
        ("model_input_cache_write", 300), ("model_output", 100),
    ]
    total = sum(quantity * prices["rates_per_million"][price] / 1e6 for _, quantity, price, _ in components)
    assert total == pytest.approx(0.000263)


@pytest.mark.parametrize("details", [
    None, {}, {"cached_tokens": 0}, {"cache_write_tokens": 0},
    {"cached_tokens": -1, "cache_write_tokens": 0},
    {"cached_tokens": False, "cache_write_tokens": 0},
    {"cached_tokens": 0, "cache_write_tokens": 1.5},
    {"cached_tokens": 99, "cache_write_tokens": 2},
    {"cached_tokens": 0, "cache_write_tokens": 0, "unknown_charge_tokens": 2},
])
def test_ambiguous_cache_details_are_measured_but_unpriced(proof, details):
    factory = proof[3]

    def ambiguous(config):
        search, client = factory(config)
        original = client.responses.create

        def response(**kwargs):
            value = original(**kwargs)
            value["usage"]["input_tokens_details"] = details
            return value
        client.responses.create = response
        return search, client

    result = run(proof, factory=ambiguous)
    _, _, entries = load_verified_run_evidence(result, proof[0].run_root / result["run_id"])
    entry = next(item for item in entries if item.meter_id == "model_input")
    assert entry.quantity == 100
    assert entry.evidence_status.value == "measured"
    assert entry.cost_coverage.value == "unpriced"
    assert entry.allocated_cost_usd is None
    assert "partitions" in entry.unavailable_reason


@pytest.mark.parametrize("limit", ["per_call", "per_run"])
def test_cache_write_reservation_blocks_limits_that_old_input_rate_would_pass(proof, limit):
    if limit == "per_call":
        proof[2].document["admission"]["max_model_cost_per_call_usd"] = 0.00195
    else:
        proof[2].document["execution"]["budget"]["per_tenant_usd_per_run"] = 0.0059
    with pytest.raises(ValueError, match="reservation"):
        run(proof)
    assert not proof[4]
    assert not proof[0].run_root.exists()


def test_missing_cache_price_is_not_substituted_with_ordinary_input_rate():
    prices = playground._prices(ROOT)
    prices["rates_per_million"].pop("cache_write")
    usage = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
             "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 90}}
    components = playground.token_price_components(usage, prices)
    write = next(item for item in components if item[0] == "model_input_cache_write")
    assert write[1] == 90
    assert write[2] is None
    assert "price" in write[3]


def test_correction_assessment_is_append_only_and_idempotent(proof, monkeypatch):
    result = run(proof)
    service, receipt = proof[:2]
    directory = service.run_root / result["run_id"]
    original_files = {path: path.read_bytes() for path in directory.rglob("*.json")}
    claim = service.run_root / "rag_playground_claims" / f"{receipt['report_id']}.json"
    original_claim = claim.read_bytes()
    original_registry = copy.deepcopy(service.registry)
    monkeypatch.setattr(playground, "clients", lambda _: pytest.fail("correction cannot use network clients"))
    correction = playground.append_correction_assessment(
        service, receipt["plan_id"], result["run_id"], ROOT, principal="test-correction-operator",
    )
    assert correction["schema_version"] == "studio-rag-correction.v1"
    assert correction["correction_schema_version"] == "books-run-correction.v1"
    assert correction["source"]["result_content_hash"] == result["content_hash"]
    assert correction["source"]["trajectory_evidence"] == result["trajectory_evidence"]
    assert correction["execution_performed"] is False
    assert correction["new_task_count"] == 0
    assert correction["acceptance_reinterpreted"] is False
    assert correction["acceptance_outcome_count"] == 0
    assert correction["tasks"][0]["provider_completion_verified"] is True
    assert correction["tasks"][0]["legacy_alias_false_failure_verified"] is False
    assert playground.append_correction_assessment(
        service, receipt["plan_id"], result["run_id"], ROOT, principal="test-correction-operator",
    ) == correction
    assert all(path.read_bytes() == content for path, content in original_files.items())
    assert claim.read_bytes() == original_claim
    assert service.registry == original_registry
    assert len(list(service.root.rglob("*.json"))) == 1


def test_correction_can_identify_legacy_alias_failure_without_rewriting_it(proof, monkeypatch):
    original_config = playground.configuration
    original_resolver = playground.resolve_model_identity

    def legacy_config():
        config = original_config()
        config.pop("adapter_revision")
        return config

    def legacy_resolver(provider_model, config, prices):
        return None if provider_model == "gpt-5-6-luna" else original_resolver(provider_model, config, prices)

    factory = proof[3]

    def alias(config):
        search, client = factory(config)
        original = client.responses.create
        client.responses.create = lambda **kwargs: {**original(**kwargs), "model": "gpt-5-6-luna"}
        return search, client

    with monkeypatch.context() as legacy:
        legacy.setattr(playground, "configuration", legacy_config)
        legacy.setattr(playground, "resolve_model_identity", legacy_resolver)
        source = run(proof, factory=alias)
    assert source["execution_status"] == "failed_or_partial"
    correction = playground.append_correction_assessment(
        proof[0], proof[1]["plan_id"], source["run_id"], ROOT, principal="test-correction-operator",
    )
    assert correction["historical_execution_status"] == "failed_or_partial"
    assert correction["tasks"][0]["historical_trajectory_status"] == "failed"
    assert correction["tasks"][0]["legacy_alias_false_failure_verified"] is True
    assert correction["tasks"][0]["model_list_price_allocation_usd"] == pytest.approx(0.000044)
    assert json.loads((proof[0].run_root / source["run_id"] / "result.json").read_text()) == source
    assert len(proof[4]) == 2


def test_correction_rejects_changed_source_without_appending(proof):
    source = run(proof)
    path = proof[0].run_root / source["run_id"] / "result.json"
    edited = copy.deepcopy(source)
    edited["answers"][0]["answer"] = "Modified after execution."
    path.write_text(json.dumps(edited))
    with pytest.raises(ValueError, match="integrity"):
        playground.append_correction_assessment(
            proof[0], proof[1]["plan_id"], source["run_id"], ROOT, principal="test-correction-operator",
        )
    assert not proof[0].root.exists()


def test_correction_rejects_changed_pricing_catalog(proof, monkeypatch):
    source = run(proof)
    prices = playground._prices(ROOT)
    prices["catalog_hash"] = "a" * 64
    monkeypatch.setattr(playground, "_prices", lambda _: prices)
    with pytest.raises(ValueError, match="exact original pricing"):
        playground.append_correction_assessment(
            proof[0], proof[1]["plan_id"], source["run_id"], ROOT, principal="test-correction-operator",
        )
    assert not proof[0].root.exists()


def test_replay_checks_immutable_result_hash(proof):
    result = run(proof)
    path = proof[0].run_root / result["run_id"] / "result.json"
    edited = copy.deepcopy(result)
    edited["answers"][0]["answer"] = "changed"
    path.write_text(json.dumps(edited))
    with pytest.raises(ValueError, match="integrity"):
        run(proof)
    assert len(proof[4]) == 2


def test_unknown_required_infrastructure_is_not_waived_for_evaluation(proof):
    policy = proof[2]
    policy.document["infrastructure_coverage"] = {
        "applicable_routes": ["foundry"], "require_confirmed_estimate": True,
        "allow_material_unpriced_items": False, "require_exact_meter_match": True,
        "min_priced_coverage_ratio": 1, "required_price_type": "Consumption",
        "currency": "USD", "allowed_regions": ["eastus"],
        "required_safeguards": [], "max_price_evidence_age_days": 30,
        "max_monthly_cost_usd": 1000,
    }
    with pytest.raises(ValueError, match="receipt violates Azure policy"):
        run(proof)
    assert not proof[4]


def test_long_retrieval_is_bounded_before_model_submission(proof):
    _, _, _, factory, calls, _ = proof

    def large(config):
        search, model = factory(config)
        search.search = lambda **_: [{"id": "x", "book": "A book", "content": "Ω" * 20000}] * 4
        return search, model

    result = run(proof, factory=large)
    assert result["execution_status"] == "completed"
    sent = next(kwargs for kind, kwargs in calls if kind == "model")
    assert len((sent["input"] + sent["instructions"]).encode("utf-8")) + 256 <= 6000


def test_legacy_post_is_retired_even_when_authorized_and_historical_gets_remain(monkeypatch):
    monkeypatch.setenv("TOKENGOV_EVALUATION_ALLOW_LOCAL", "true")
    monkeypatch.setattr(studio, "_lifecycle_service", lambda: pytest.fail("retired endpoint must not access lifecycle"))
    monkeypatch.setattr(studio, "load_policy_from_environment", lambda: pytest.fail("retired endpoint must not load policy"))
    monkeypatch.setattr(playground, "_probe_connection", lambda _: {
        "status": "connected", "read_only": True, "document_count": 2736,
        "inference_performed": False,
    })
    seen, artifacts, registry = [], [], {}

    def persisted_execute(*args):
        seen.append(args)
        result = {
            "run_id": "run-test", "report_id": "report-test", "plan_id": "plan-test",
            "execution_status": "completed", "answers": [],
        }
        registry["run-test"] = {"status": "completed", "result": result}
        return result

    monkeypatch.setattr(playground, "execute", persisted_execute)
    monkeypatch.setattr(studio, "_read_registry", lambda: registry)
    monkeypatch.setattr(studio, "ReportStore", lambda _: SimpleNamespace(
        add_artifact=lambda *args: artifacts.append(args),
    ))
    server = ThreadingHTTPServer(("127.0.0.1", 0), studio.StudioHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/api/rag-playground/connection")
        response = connection.getresponse()
        status = json.loads(response.read())
        assert response.status == 200
        assert status["sample_questions"] == playground.SAMPLES
        assert status["connectivity_verified"] is True
        assert status["ready"] is True
        assert status["message"]
        assert status["limits"]["maximum_questions"] == 3
        assert status["public_config"]["index"] == "books"
        path = "/api/plans/plan-test/rag-playground"
        body = json.dumps({"questions": ["Question"]})
        connection.request("POST", path, body)
        response = connection.getresponse()
        retired = json.loads(response.read())
        assert response.status == 410
        assert retired == {
            "error": "Legacy lexical execution is retired. Use the metrics-only deployed-agent batch endpoint.",
            "code": "rag_playground_retired",
            "successor": "/api/plans/plan-test/rag-batches",
            "execution_performed": False,
            "historical_evidence": "unchanged",
        }
        assert not seen
        assert not registry
        assert not artifacts
        headers = {"Origin": f"http://127.0.0.1:{server.server_port}",
                   "X-TokenGov-CSRF": studio._lifecycle_request_token,
                   "Content-Type": "application/json"}
        connection.request("POST", path, body, headers)
        response = connection.getresponse()
        assert json.loads(response.read()) == retired
        assert response.status == 410
        assert not seen
        assert not registry
        assert not artifacts
        historical = {"status": "completed", "result": {"answers": [{"answer": "Historical answer"}]}}
        registry["run-historical"] = copy.deepcopy(historical)
        connection.request("GET", "/api/runs/run-historical")
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read()) == historical
        assert registry["run-historical"] == historical
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_connection_probe_performs_only_read_only_inventory_calls(monkeypatch):
    seen = []
    search = SimpleNamespace(
        get_document_count=lambda: seen.append("document_count") or 2736,
        close=lambda: None,
    )
    model = SimpleNamespace(
        models=SimpleNamespace(list=lambda: seen.append("models_inventory") or [SimpleNamespace(id=playground.MODEL)]),
        close=lambda: None,
    )
    monkeypatch.setattr(playground, "clients", lambda _: (search, model))
    result = playground.connection_status()
    assert result["ready"] is True
    assert result["connectivity"]["inference_performed"] is False
    assert result["connectivity"]["retrieval_queries_performed"] is False
    assert seen == ["document_count", "models_inventory"]


def test_connection_failure_is_not_ready_and_does_not_expose_secrets(monkeypatch):
    def fail(_):
        raise RuntimeError("Authorization bearer credential-never-expose")
    monkeypatch.setattr(playground, "clients", fail)
    result = playground.connection_status()
    assert result["ready"] is False
    assert result["connectivity_verified"] is False
    assert result["status"] == "blocked"
    assert result["message"]
    assert result["error"] == "authentication_read_access_unavailable"
    assert "credential-never-expose" not in json.dumps(result)
