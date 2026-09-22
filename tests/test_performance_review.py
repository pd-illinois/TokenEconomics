from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from costgov.performance_review import build_review, content_hash
from costgov.policy_store import LoadedPolicy
from costgov.studio_lifecycle import StudioLifecycle, canonical
from rag import agent_batch as batch
from rag.agent_batch_measurement import allocation
from rag.performance_evidence import build_performance_review


NOW = "2026-09-09T17:00:00Z"
SECRET = "PRIVATE QUESTION ANSWER TOOL BODY MUST NOT ESCAPE"


def measurement():
    return {
        "schema_version": "workload-measurement-policy.v1", "mode": "measurement_only",
        "workload_scope": "studio_batches", "max_questions": 10, "max_output_tokens": 1024,
        "max_elapsed_seconds": 300, "observed_model_cost_stop_usd": .25,
        "expires_at": "2026-09-11T17:00:00Z", "acknowledge_incomplete_costs": True,
        "hard_spend_cap_guaranteed": False, "operational_promotion": False,
    }


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    # No execution or publication helper may be used by this read-only feature.
    monkeypatch.setattr(batch, "execute", lambda *a, **kw: pytest.fail("No dispatch"))
    monkeypatch.setattr(batch, "publish_result", lambda *a, **kw: pytest.fail("No upload"))
    document = {
        "schema_version": "1.0", "policy_id": "policy-test", "version": "1",
        "status": "active", "effective_from": "2020-01-01T00:00:00Z",
        "admission": {"allowed_providers": ["azure_openai"], "allowed_models": ["model-test"],
                      "max_model_cost_per_call_usd": .05, "require_pricing_verified": True},
        "execution": {
            "routing_mode": "balanced", "semantic_cache": {"enabled": False, "score_threshold": .9},
            "budget": {"per_tenant_usd_per_run": .1, "hard_cap_action": "deny"},
            "evaluation": {"min_quality": .8, "min_segment_samples": 10},
        },
        "mutation": {"mode": "manual", "allowed_knobs": []}, "measurement": measurement(),
    }
    provenance = {
        "source": "azure_app_configuration", "endpoint": "https://test.azconfig.io",
        "key": "tokengov:policy", "label": "test", "etag": "etag1",
    }
    loaded = LoadedPolicy(document, provenance)
    forecast = {
        "schema_version": "6.0", "plan_id": "plan-test", "report_id": "report-test",
        "description": SECRET, "prediction": {
            "prediction_id": 17, "model": "model-test", "provider": "azure_openai",
            "pricing_version": "price-v1", "prediction_method": "tier1_heuristic",
            "tokens_per_call": {"text_input": 1200, "document_input": 2560, "image_input": 0,
                                "audio_input": 0, "text_output": 1200},
            "cost_per_call": {"mean": .003424},
            "tool_costs_per_call": {"file_search_cost_usd": .0025, "total_usd": .0025, "is_complete": True},
            "calculation_trace": {"tokens": {"workload_statement": SECRET},
                                  "scale": {"daily_calls": 30, "cache_assumptions": {
                                      "cache_hit_rate_after_first_call": .75, "cost_discount_factor": .96,
                                      "estimated_token_savings_pct": 99}}},
        },
        "infrastructure": {"schema_version": "infrastructure-forecast.v1", "priced_subtotal_usd": 825,
                           "architecture": {"baseline_revision": "secure-single-region.v3"}},
    }
    receipt = {**forecast, "receipt_id": "receipt-test", "content_hash": content_hash(forecast)}
    service = StudioLifecycle(
        tmp_path / "reviews", SimpleNamespace(get_receipt=lambda _: receipt), {}, tmp_path / "runs",
        {"schema_version": "workload-evidence-adapter.v1", "adapter_id": "test", "version": "1",
         "kind": "immutable_run_store", "network_execution": False, "maximum_tasks": 10,
         "required_cost_families": ["direct_token"]},
    )
    return service, receipt, loaded


def reseal(result):
    result["evidence"] = {
        "status": "persisted_locally_cloud_pending",
        "location": f"studio_runs/{result['run_id']}/result.json",
        "content_hash": content_hash({key: value for key, value in result.items() if key != "evidence"}),
        "cloud_status": "publication_tracked_separately" if result["schema_version"].endswith("v2")
                        else "not_published_no_executed_usage",
    }
    return result


def saved(evidence, *, run_id="run-" + "a" * 32, status="completed", version="v2", started=NOW):
    service, receipt, loaded = evidence
    response = {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": SECRET}]}],
                "usage": {"input_tokens": 100, "output_tokens": 30,
                          "input_tokens_details": {"cached_tokens": 20}}}
    metrics = [batch.question_metrics(SECRET, index, response if status != "blocked" else None, latency_ms=50)
               for index in (1, 2)]
    if status == "blocked":
        metrics = [batch.question_metrics(SECRET, index) for index in (1, 2)]
    elif status == "partial":
        metrics[1] = batch.question_metrics(SECRET, 2)
    pricing = {
        "schema_version": "rag-model-allocation-pricing.v1", "catalog_schema": "foundry-model-release.v2",
        "revision": "price-v1", "catalog_hash": "c" * 64, "model": "model-test", "model_version": "2025-01-01",
        "provider": "azure_openai", "rates_per_million": {"input": .4, "cached_input": .1, "output": 1.6, "cache_write": None},
        "allocation_scope": "response_model_only_not_task_total", "price_basis": "catalog_public_list_allocation_not_invoice",
        "cache_write_rule": "separate_rate_when_sourced_otherwise_standard_input",
    }
    allocations = [{"question_number": row["question_number"],
                    "model_identity_bound": row["status"] != "not_dispatched",
                    "model_allocation_usd": allocation(row, pricing, row["status"] != "not_dispatched")} for row in metrics]
    result = {
        "schema_version": f"rag-agent-batch.{version}", "run_id": run_id,
        "request_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "plan_id": receipt["plan_id"],
        "report_id": receipt["report_id"], "execution_status": status, "questions_count": 2,
        "metrics": metrics, "started_at": started, "ended_at": started,
        "agent": {"agent_name": "test-agent", "agent_version": "4", "kind": "prompt", "active": True,
                  "deployment": "test", "model": "model-test", "model_version": "2025-01-01",
                  "managed_books_mcp": True, "retrieval_mode": "managed_mcp_hybrid_unverified"},
        "policy": {**loaded.provenance, "policy_id": loaded.document["policy_id"],
                   "version": loaded.document["version"], "content_hash": content_hash(loaded.document)},
        "prediction": {"receipt_id": receipt["receipt_id"], "content_hash": receipt["content_hash"]},
        "blockers": [{"code": "azure_authority_required", "message": batch._BLOCKERS["azure_authority_required"]}]
                    if status == "blocked" else [],
        "evidence_classification": "blocked" if status == "blocked" else "measured",
        "acceptance_status": "not_evaluated", "operational_promotion": False,
    }
    if version == "v2":
        result.update(measurement_authorization=measurement(), pricing=pricing, allocations=allocations,
                      observed_model_allocation_usd=sum(row["model_allocation_usd"] or 0 for row in allocations),
                      stop_reason="blocked" if status == "blocked" else "completed" if status == "completed"
                                  else "observed_model_cost_stop")
    reseal(result)
    batch.validate_result(result)
    persist(service, result)
    return result


def persist(service, result):
    path = service.run_root / result["run_id"] / "result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(result), encoding="utf-8")
    service.registry[result["run_id"]] = {
        "run_id": result["run_id"], "plan_id": result["plan_id"], "report_id": result["report_id"],
        "result": copy.deepcopy(result),
    }


def review(evidence, **kwargs):
    service, receipt, loaded = evidence
    return build_performance_review(service, receipt["plan_id"], current_policy=loaded,
                                    now=kwargs.pop("now", NOW), **kwargs)


def codes(value):
    return {row["code"] for row in value["findings"]}


def test_success_is_bound_safe_read_only_advisory(evidence):
    service, receipt, _ = evidence
    run = saved(evidence)
    before = {str(path): path.read_bytes() for path in service.run_root.rglob("*") if path.is_file()}
    result = review(evidence)
    assert result["schema_version"] == "studio-performance-review.v1"
    assert result["selected_run_id"] == run["run_id"]
    assert result["receipt_hash"] == receipt["content_hash"]
    assert result["summary"]["questions_completed"] == 2
    assert result["summary"]["input_tokens"] == 200
    assert result["summary"]["output_tokens"] == 60
    assert result["summary"]["latency_mean_ms"] == 50
    assert result["summary"]["observed_model_allocation_usd"] == pytest.approx(.000164)
    assert result["summary"]["total_task_cost_usd"] is None
    assert result["summary"]["quality_status"] == "not_evaluated"
    assert result["summary"]["billing_status"] == "unavailable"
    assert result["summary"]["cloud_status"] == "pending"
    assert result["provenance"]["publication"]["created_at"] is None
    assert result["policy"]["status"] == "same"
    assert result["decision"]["operational_promotion"] is False
    assert result["decision"]["automatic_changes"] is False
    assert next(ref for ref in result["decision"]["evidence_refs"] if ref["kind"] == "run")["content_hash"] == run["evidence"]["content_hash"]
    assert {"output_assumption_differs", "forecast_output_exceeds_recorded_cap", "generic_tool_pricing_uncertain",
            "quality_evidence_unavailable", "historical_measurement_near_expiry"} <= codes(result)
    assert all(item["variance_pct"] is None for item in result["comparison"])
    assert all(item["status"] != "comparable" for item in result["comparison"])
    assert SECRET not in json.dumps(result, allow_nan=False)
    assert "estimated_token_savings_pct" not in json.dumps(result)
    assert not service.root.exists()
    assert before == {str(path): path.read_bytes() for path in service.run_root.rglob("*") if path.is_file()}
    assert result == review(evidence)


def test_bound_foundry_quality_projects_human_review_pending(evidence, monkeypatch):
    run = saved(evidence)
    monkeypatch.setattr("rag.qna_review.quality_summary", lambda *_: {
        "status": "advisory_evaluation",
        "evaluated_cases": 2,
        "human_review_status": "pending",
        "segments": [],
        "scores": [],
    })
    result = review(evidence, run_id=run["run_id"])
    assert result["summary"]["quality_status"] == "human_review_pending"
    assert result["quality"]["evaluated_cases"] == 2
    quality = next(row for row in result["comparison"] if row["metric"] == "quality")
    assert quality["observed"] is None
    assert quality["unit"] == "% accepted"
    assert quality["status"] == "partially_comparable"
    assert "quality_evidence_unavailable" not in codes(result)
    assert "quality_human_review_pending" in codes(result)
    assert result["summary"]["accepted_tasks"] is None


def test_human_acceptance_populates_accepted_task_quality(evidence, monkeypatch):
    run = saved(evidence)
    monkeypatch.setattr("rag.qna_review.quality_summary", lambda *_: {
        "status": "advisory_evaluation", "evaluated_cases": 5,
        "human_review_status": "pending", "acceptance_status": "human_reviewed",
        "reviewed_cases": 5, "pending_cases": 0, "accepted": 4, "rejected": 1,
        "inconclusive": 0, "acceptance_rate": .8, "segments": [],
        "candidates": [], "scores": [], "report_url": "https://ai.azure.com/evaluation",
    })
    result = review(evidence, run_id=run["run_id"])
    assert result["summary"]["quality_status"] == "human_reviewed"
    assert result["summary"]["accepted_tasks"] == 4
    quality = next(row for row in result["comparison"] if row["metric"] == "quality")
    assert quality["expected"] == 80
    assert quality["observed"] == 80
    assert quality["unit"] == "% accepted"
    assert "quality_human_review_complete" in codes(result)


@pytest.mark.parametrize("status,version", [("blocked", "v1"), ("blocked", "v2"), ("partial", "v2")])
def test_supported_attempts_do_not_fabricate_success(evidence, status, version):
    saved(evidence, status=status, version=version)
    result = review(evidence)
    assert result["summary"]["execution_status"] == status
    assert result["summary"]["questions_completed"] == (1 if status == "partial" else 0)
    assert result["summary"]["input_tokens"] == (100 if status == "partial" else None)
    assert result["summary"]["observed_model_allocation_usd"] == (pytest.approx(.000082) if status == "partial" else None)
    assert result["summary"]["accepted_tasks"] is None
    assert "execution_incomplete" in codes(result)


def test_no_runs_preserves_forecast_context(evidence):
    result = review(evidence)
    assert result["selected_run_id"] is None and result["run_options"] == []
    assert result["scope"]["status"] == "no_supported_run_evidence"
    assert result["summary"]["questions_completed"] is None
    assert result["forecast"]["input_tokens"] == 3760
    assert result["forecast"]["output_tokens"] == 1200
    assert result["forecast"]["daily_calls"] == 30
    assert result["forecast"]["infrastructure"]["baseline_revision"] == "secure-single-region.v3"
    assert result["policy"]["current"] is not None
    assert "no_supported_run_evidence" in codes(result)


def test_newest_completed_preferred_and_runs_never_combined(evidence):
    complete = saved(evidence, started="2026-09-08T12:00:00Z")
    attempt = saved(evidence, run_id="run-" + "b" * 32, status="partial")
    result = review(evidence)
    assert result["selected_run_id"] == complete["run_id"]
    assert [item["run_id"] for item in result["run_options"]] == [attempt["run_id"], complete["run_id"]]
    assert result["summary"]["input_tokens"] == 200
    assert review(evidence, run_id=attempt["run_id"])["summary"]["input_tokens"] == 100


def test_newest_attempt_fallback(evidence):
    saved(evidence, status="blocked", started="2026-09-08T12:00:00Z")
    newest = saved(evidence, run_id="run-" + "b" * 32, status="partial")
    assert review(evidence)["selected_run_id"] == newest["run_id"]


def test_invalid_newer_completed_run_is_excluded_not_summed(evidence):
    service = evidence[0]
    old = saved(evidence, started="2026-09-08T12:00:00Z")
    new = saved(evidence, run_id="run-" + "b" * 32)
    new["metrics"][0]["input_tokens"] = 999
    persist(service, new)
    result = review(evidence)
    assert result["selected_run_id"] == old["run_id"]
    assert result["summary"]["input_tokens"] == 200
    assert result["provenance"]["excluded_run_count"] == 1


@pytest.mark.parametrize("field", ["run_id", "plan_id", "report_id"])
def test_registry_metadata_cannot_rebind_sealed_evidence(evidence, field):
    run = saved(evidence)
    evidence[0].registry[run["run_id"]][field] = "wrong"
    with pytest.raises(ValueError):
        review(evidence, run_id=run["run_id"])


@pytest.mark.parametrize("change", [
    lambda r: r.update(report_id="wrong-report"),
    lambda r: r.update(plan_id="wrong-plan"),
    lambda r: r["prediction"].update(receipt_id="wrong-receipt"),
    lambda r: r["prediction"].update(content_hash="0" * 64),
    lambda r: r["agent"].update(model="wrong-model"),
    lambda r: r["pricing"].update(model="wrong-model"),
    lambda r: r["pricing"].update(model_version="wrong-version"),
    lambda r: r["pricing"].update(revision="wrong-prices"),
    lambda r: r.update(answer=SECRET),
    lambda r: r["metrics"][0].update(question=SECRET),
    lambda r: r["agent"].update(tool_body=SECRET),
    lambda r: r["policy"].update(unknown=SECRET),
    lambda r: r["policy"].update(etag=SECRET),
    lambda r: r.pop("started_at"),
    lambda r: r.update(started_at=None),
    lambda r: r.pop("request_id"),
    lambda r: r.update(pricing=None),
])
def test_selected_invalid_binding_or_shape_fails(evidence, change):
    run = saved(evidence)
    change(run)
    persist(evidence[0], reseal(run))
    with pytest.raises((ValueError, KeyError)):
        review(evidence, run_id=run["run_id"])


@pytest.mark.parametrize("target", ["disk", "registry", "hash"])
def test_tampering_never_becomes_measurement(evidence, target):
    service = evidence[0]
    run = saved(evidence)
    if target == "registry":
        service.registry[run["run_id"]]["result"]["metrics"][0]["input_tokens"] = 101
    else:
        if target == "hash":
            run["evidence"]["content_hash"] = "0" * 64
        else:
            run["metrics"][0]["input_tokens"] = 101
        (service.run_root / run["run_id"] / "result.json").write_text(canonical(run), encoding="utf-8")
    with pytest.raises(ValueError):
        review(evidence, run_id=run["run_id"])
    result = review(evidence)
    assert result["selected_run_id"] is None and result["summary"]["input_tokens"] is None
    assert result["provenance"]["excluded_run_count"] == 1


def test_invalid_receipt_hash_rejected(evidence):
    evidence[1]["prediction"]["cost_per_call"]["mean"] = 999
    with pytest.raises(ValueError, match="receipt integrity"):
        review(evidence)


def test_registry_only_and_unrelated_records_do_not_contaminate(evidence):
    service = evidence[0]
    run = saved(evidence)
    service.registry["unrelated"] = {"plan_id": "different", "result": {"secret": SECRET}}
    service.registry["bad"] = {"plan_id": evidence[1]["plan_id"], "result": {
        "schema_version": "rag-agent-batch.v2", "questions_count": 500, "secret": SECRET}}
    result = review(evidence)
    assert result["selected_run_id"] == run["run_id"] and result["summary"]["input_tokens"] == 200
    assert result["provenance"]["excluded_run_count"] == 1
    assert SECRET not in json.dumps(result)


def test_unsupported_runs_are_not_empty_success(evidence):
    service, receipt, _ = evidence
    service.registry["conventional"] = {"plan_id": receipt["plan_id"], "result": {"schema_version": "other.v1"}}
    result = review(evidence)
    assert result["scope"]["status"] == "no_supported_run_evidence"
    assert result["provenance"]["unsupported_run_count"] == 1
    with pytest.raises(ValueError):
        review(evidence, run_id="conventional")
    with pytest.raises((ValueError, KeyError)):
        review(evidence, run_id="../escape")
    with pytest.raises(KeyError):
        review(evidence, run_id="missing")


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens", "cached_input_tokens"])
def test_missing_usage_stays_unknown_not_zero(evidence, field):
    run = saved(evidence)
    run["metrics"][0][field] = None
    run["allocations"][0]["model_allocation_usd"] = None
    run["observed_model_allocation_usd"] = run["allocations"][1]["model_allocation_usd"]
    persist(evidence[0], reseal(run))
    result = review(evidence)
    assert result["summary"]["observed_model_allocation_usd"] is None
    assert result["summary"]["known_model_allocation_subtotal_usd"] == pytest.approx(.000082)
    assert result["summary"]["allocation_status"] == "partial"
    if field != "cached_input_tokens":
        assert result["summary"][field] is None
        assert "usage_coverage_incomplete" in codes(result)


def test_unbound_response_model_cannot_claim_cost(evidence):
    run = saved(evidence)
    for row in run["allocations"]:
        row.update(model_identity_bound=False, model_allocation_usd=None)
    run["observed_model_allocation_usd"] = 0
    persist(evidence[0], reseal(run))
    result = review(evidence)
    assert result["summary"]["observed_model_allocation_usd"] is None
    assert result["summary"]["known_model_allocation_subtotal_usd"] is None
    assert "response_allocation_incomplete" in codes(result)


def test_blocked_unknown_prices_are_not_zero_cost(evidence):
    run = saved(evidence, status="blocked")
    run["pricing"] = None
    persist(evidence[0], reseal(run))
    result = review(evidence)
    assert result["summary"]["observed_model_allocation_usd"] is None
    assert result["provenance"]["pricing"] is None


def test_invalid_usage_partitions_withhold_usage_and_cost(evidence):
    run = saved(evidence)
    run["metrics"][0]["coverage_notes"].append("invalid_provider_usage")
    run["allocations"][0]["model_allocation_usd"] = None
    run["observed_model_allocation_usd"] = run["allocations"][1]["model_allocation_usd"]
    persist(evidence[0], reseal(run))
    result = review(evidence)
    assert result["summary"]["input_tokens"] is None
    assert result["summary"]["output_tokens"] is None
    assert result["summary"]["observed_model_allocation_usd"] is None


@pytest.mark.parametrize("change", [
    lambda p: p.document["execution"]["budget"].update(per_tenant_usd_per_run=.2),
    lambda p: p.provenance.update(etag="etag2"),
    lambda p: p.provenance.update(label="another-label"),
])
def test_exact_current_policy_change_is_not_historical_noncompliance(evidence, change):
    saved(evidence)
    change(evidence[2])
    result = review(evidence)
    assert result["policy"]["status"] == "changed"
    assert "current_policy_changed" in codes(result)
    assert "not evidence of historical noncompliance" in next(
        row["detail"] for row in result["findings"] if row["code"] == "current_policy_changed")
    assert result["decision"]["operational_promotion"] is False


@pytest.mark.parametrize("change", [
    lambda p: p.provenance.update(source="local_file"),
    lambda p: p.provenance.update(development_only=True),
    lambda p: p.provenance.pop("etag"),
    lambda p: p.document.update(status="inactive"),
])
def test_current_authority_unavailable(evidence, change):
    saved(evidence)
    change(evidence[2])
    result = review(evidence)
    assert result["policy"]["status"] == "unavailable"
    assert result["policy"]["current"] is None
    assert "current_policy_unavailable" in codes(result)
    assert result["summary"]["questions_completed"] == 2


def test_policy_errors_are_not_leaked(evidence):
    saved(evidence)
    result = review(evidence, policy_error=RuntimeError(SECRET))
    assert result["policy"]["current"] is None
    assert SECRET not in json.dumps(result)
    assert build_performance_review(evidence[0], evidence[1]["plan_id"], now=NOW)["policy"]["current"] is None


def test_expiry_affects_future_authorization_not_historical_counts(evidence):
    saved(evidence)
    result = build_performance_review(evidence[0], evidence[1]["plan_id"], current_policy=evidence[2],
                                      now="2026-09-12T00:00:00Z")
    assert "historical_measurement_expired" in codes(result)
    assert "current_measurement_expired" not in codes(result)
    assert result["policy"]["measurement_expiry"]["historical"] == result["policy"]["measurement_expiry"]["current"]
    assert result["summary"]["questions_completed"] == 2
    assert result["policy"]["status"] == "same"


def test_same_binding_and_expiry_produce_one_business_finding(evidence):
    saved(evidence)
    result = review(evidence)
    expiry = [row for row in result["findings"] if row["code"].endswith("measurement_near_expiry")]
    assert len(expiry) == 1
    assert "recorded and current" in expiry[0]["detail"]
    provenance = result["policy"]["measurement_expiry"]
    assert set(provenance) == {"historical", "current"}
    assert provenance["historical"] == provenance["current"]


def test_different_policy_bindings_preserve_both_expiry_findings(evidence):
    saved(evidence)
    evidence[2].provenance["etag"] = "changed-etag"
    result = review(evidence)
    assert {"historical_measurement_near_expiry", "current_measurement_near_expiry"} <= codes(result)


def test_same_policy_with_distinct_recorded_grant_expiry_keeps_both(evidence):
    run = saved(evidence)
    run["measurement_authorization"]["expires_at"] = "2026-09-10T17:00:00Z"
    persist(evidence[0], reseal(run))
    result = review(evidence)
    assert result["policy"]["status"] == "same"
    assert {"historical_measurement_near_expiry", "current_measurement_near_expiry"} <= codes(result)


@pytest.mark.parametrize("published", [False, True])
def test_initial_unsealed_pending_marker_is_not_invalid_evidence(evidence, published):
    service = evidence[0]
    run = saved(evidence)
    directory = service.run_root / run["run_id"] / "publication"
    directory.mkdir()
    pending = {"schema_version": "rag-batch-publication.v1", "run_id": run["run_id"],
               "status": "pending", "content_hash": None, "created_at": NOW, "destination": None}
    (directory / "001.json").write_text(json.dumps(pending), encoding="utf-8")
    if published:
        record = {**pending, "status": "published", "content_hash": run["evidence"]["content_hash"],
                  "destination": "https://teststore.blob.core.windows.net/batches"}
        (directory / "002.json").write_text(json.dumps(record), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in directory.glob("*.json")}
    result = review(evidence)
    assert result["summary"]["cloud_status"] == ("published" if published else "pending")
    assert result["provenance"]["publication"]["content_hash"] == (run["evidence"]["content_hash"] if published else None)
    assert result["summary"]["questions_completed"] == 2
    assert "publication_evidence_invalid" not in codes(result)
    assert before == {path.name: path.read_bytes() for path in directory.glob("*.json")}


@pytest.mark.parametrize("change", [
    {"content_hash": "0" * 64},
    {"run_id": "wrong-run"},
    {"status": "published"},
    {"body": SECRET},
])
def test_invalid_pending_history_is_not_hidden_by_valid_publication(evidence, change):
    service = evidence[0]
    run = saved(evidence)
    directory = service.run_root / run["run_id"] / "publication"
    directory.mkdir()
    base = {"schema_version": "rag-batch-publication.v1", "run_id": run["run_id"],
            "status": "pending", "content_hash": None, "created_at": NOW, "destination": None}
    (directory / "001.json").write_text(json.dumps({**base, **change}), encoding="utf-8")
    published = {**base, "status": "published", "content_hash": run["evidence"]["content_hash"],
                 "destination": "https://teststore.blob.core.windows.net/batches"}
    (directory / "002.json").write_text(json.dumps(published), encoding="utf-8")
    result = review(evidence)
    assert result["summary"]["cloud_status"] == "invalid"
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize("tamper", [None, "hash", "run", "extra"])
def test_publication_is_independently_bound_and_not_execution_proof(evidence, tamper):
    service = evidence[0]
    run = saved(evidence, status="partial")
    directory = service.run_root / run["run_id"] / "publication"
    directory.mkdir()
    record = {"schema_version": "rag-batch-publication.v1", "run_id": run["run_id"],
              "status": "published", "content_hash": run["evidence"]["content_hash"],
              "created_at": NOW, "destination": "https://teststore.blob.core.windows.net/batches"}
    if tamper == "hash":
        record["content_hash"] = "0" * 64
    elif tamper == "run":
        record["run_id"] = "run-" + "b" * 32
    elif tamper == "extra":
        record["body"] = SECRET
    (directory / "001.json").write_text(json.dumps(record), encoding="utf-8")
    result = review(evidence)
    assert result["summary"]["cloud_status"] == ("invalid" if tamper else "published")
    assert result["summary"]["execution_status"] == "partial"
    assert result["summary"]["questions_completed"] == 1
    assert SECRET not in json.dumps(result)
    if tamper:
        assert "publication_evidence_invalid" in codes(result)


@pytest.mark.parametrize("prediction", [
    {},
    {"model": "another-model", "cost_per_call": {"mean": .5}, "tokens_per_call": {"text_input": 12, "text_output": 8}},
    {"tokens_per_call": {"text_input": False, "text_output": -1}, "cost_per_call": {"mean": None}},
    {"tokens_per_call": {"text_input": 0, "text_output": 0}, "cost_per_call": {"mean": 0}},
])
def test_alternative_forecasts_no_invented_defaults(evidence, prediction):
    receipt = copy.deepcopy(evidence[1])
    receipt["prediction"] = prediction
    receipt["infrastructure"] = {}
    result = build_review(receipt, now=NOW)
    assert result["forecast"]["model"] == prediction.get("model")
    assert result["forecast"]["daily_calls"] is None
    assert result["forecast"]["infrastructure"]["monthly_priced_subtotal_usd"] is None
    assert all(row["observed"] is None and row["variance_pct"] is None for row in result["comparison"])
    json.dumps(result, allow_nan=False)


def test_naive_review_clock_rejected(evidence):
    with pytest.raises(ValueError, match="timezone-aware"):
        review(evidence, now="2026-09-09")
