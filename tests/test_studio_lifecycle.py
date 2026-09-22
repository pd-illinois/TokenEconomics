from __future__ import annotations

import copy
import json
import threading
from dataclasses import replace
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import studio
from costgov.acceptance_contracts import (
    AcceptanceOutcomeStore, AcceptanceRule, AcceptanceRuleStore,
    ReviewEvidence, ReviewMethod, evaluate_acceptance,
)
from costgov.consumption_models import ConsumptionFamily
from costgov.meter_ledger import CostCoverage, MeterLedgerStore
from costgov.planning import PlanStore
from costgov.policy_store import PolicyLoadError, admit_receipt
from costgov.studio_lifecycle import StudioLifecycle, digest
from costgov.trajectory_contracts import PolicyBinding, PredictionBinding, TrajectoryStore, WorkloadIdentity
from test_meter_ledger import _entry
from test_route_governance import _receipt
from test_studio_api import _loaded_policy
from test_trajectory_contracts import _envelope


@pytest.fixture
def lifecycle(tmp_path):
    plans = PlanStore(tmp_path / "plans")
    session = plans.create_session("report-1", "A portable workload", {})
    result = _receipt("foundry", ready=False)
    result["intake"] = {}
    result["prediction"].update(
        prediction_id="prediction-1", model="gpt-4.1",
        cost_per_call={"mean": 0.001}, monthly_cost={"mean": 1}, annual_cost={"mean": 12},
    )
    _, receipt = plans.complete(session, result)
    policy = _loaded_policy()
    admitted = admit_receipt(receipt, policy)
    assert admitted["status"] == "admitted"
    policy_ref = admitted["policy"]
    binding = PolicyBinding(
        policy_id=policy_ref["policy_id"], version=policy_ref["version"],
        content_hash=policy_ref["content_hash"], source="azure_app_configuration",
        label=policy.provenance["label"], etag=policy.provenance["etag"],
    )
    config = json.loads((Path(__file__).resolve().parents[1] / "data" / "workload_adapters" / "studio-evidence.v1.json").read_text())
    run_id = "run-1"
    root = tmp_path / "runs" / run_id
    rule = AcceptanceRule(
        schema_version="acceptance-rule.v1", rule_id="rule-hard", version="1",
        segment_id="hard-synthesis", segment_version="segment.v1",
        evaluator_id="portable-evaluator", evaluator_version="1",
        evaluator_content_hash="a" * 64, minimum_score=0.9,
        created_at="2026-08-20T20:00:00+00:00",
    )
    AcceptanceRuleStore(root / "acceptance_rules").append(rule)
    run = {
        "run_id": run_id, "report_id": "report-1", "status": "completed",
        "evidence_classification": "measured",
        "trajectory_contract": receipt["trajectory_contract"],
        "trajectory_evidence": [], "acceptance_outcomes": [], "meter_ledger_evidence": [],
    }
    sampling = {"schema_version": "task-sampling-evidence.v1", "representative": True,
                "independent_tasks": True, "population_revision": "fixture.v1", "method": "synthetic test fixture"}
    run["sampling_evidence"] = {**sampling, "content_hash": digest(sampling)}
    for index in range(60):
        original = _envelope()
        task = replace(original.task, task_id=f"task-{index}", report_id="report-1",
                       workload=WorkloadIdentity.from_dict(receipt["trajectory_contract"]["workload"]))
        trajectory = replace(original, trajectory_id=f"trajectory-{index}", run_id=run_id, task=task,
                             prediction_binding=PredictionBinding(
                                 prediction_id="prediction-1", receipt_id=receipt["receipt_id"],
                                 content_hash=receipt["content_hash"], schema_version=receipt["schema_version"]),
                             policy_binding=binding)
        record = TrajectoryStore(root / "trajectories").append(trajectory)
        run["trajectory_evidence"].append({"trajectory_id": trajectory.trajectory_id, "content_hash": record.content_hash})
        outcome = evaluate_acceptance(
            rule, experiment_id="experiment-1", experiment_revision="v1", arm_id="candidate",
            policy_candidate_id="candidate-1", policy_candidate_version="v1", policy_candidate_content_hash="b" * 64,
            task_id=task.task_id, trajectory_id=trajectory.trajectory_id,
            segment_id="hard-synthesis", segment_version="segment.v1",
            automated_review=ReviewEvidence(ReviewMethod.AUTOMATED, "portable-evaluator", f"score-{index}", "1", "c" * 64, score=1),
            evaluated_at="2026-08-20T20:00:08+00:00",
        )
        record = AcceptanceOutcomeStore(root / "acceptance_outcomes").append(outcome)
        run["acceptance_outcomes"].append({"outcome_id": outcome.outcome_id, "content_hash": record.content_hash})
        for family in config["required_cost_families"]:
            entry = _entry(
                entry_id=f"entry-{index}-{family}", task_id=task.task_id,
                trajectory_id=trajectory.trajectory_id, segment_id="hard-synthesis",
                step_id=None, meter_family=ConsumptionFamily(family), product="foundry",
                cost_coverage=CostCoverage.PRICED, allocated_cost_usd=0.001,
                pricing_revision="pricing.v1",
            )
            record = MeterLedgerStore(root / "meter_ledger").append(entry)
            run["meter_ledger_evidence"].append({"entry_id": entry.entry_id, "content_hash": record.content_hash})
    (root / "result.json").write_text(json.dumps(run))
    service = StudioLifecycle(tmp_path / "lifecycle", plans, {run_id: {"status": "completed"}}, tmp_path / "runs", config)
    requirements = {
        "acceptance": {"definition": "Use the exact segment rule, not self-reported quality.",
                       "segments": [{"segment_id": "hard-synthesis", "segment_version": "segment.v1", "rule_hash": rule.content_hash}]},
        "budget": {"budget_usd": 0.02, "breach_tolerance": 0.05, "minimum_samples": 60,
                   "confidence_level": 0.95, "minimum_acceptance_lower_bound": 0.8},
    }
    return service, receipt, policy, requirements, run, root


def _evaluate(fixture):
    service, receipt, policy, requirements, _, _ = fixture
    request = service.requirements(receipt["plan_id"], requirements)
    attachment = service.attach(receipt["plan_id"], {"run_id": "run-1", "adapter_id": service.config["adapter_id"]})
    evaluation = service.evaluate(receipt["plan_id"], {
        "requirements_id": request["id"], "attachment_id": attachment["id"], "mode": "read_only",
    }, policy, "evaluation-principal")
    return request, attachment, evaluation


def test_full_read_only_lifecycle_is_receipt_bound_and_append_only(lifecycle):
    service, receipt, policy, _, _, _ = lifecycle
    before = copy.deepcopy(service.plans.get(receipt["plan_id"]))
    request, attachment, evaluation = _evaluate(lifecycle)
    assert evaluation["status"] == "eligible"
    assert evaluation["expected_complete_task_cost"] == pytest.approx(0.006)
    first = service.reassess(receipt["plan_id"], {"evaluation_id": evaluation["id"]}, policy)
    second = service.reassess(receipt["plan_id"], {"evaluation_id": evaluation["id"]}, policy)
    assert first["id"] != second["id"]
    assert first["status"] == "eligible_for_operational_admission"
    assert first["candidate"]["receipt_hash"] == receipt["content_hash"]
    operational = service.operational_admission(receipt["plan_id"], {"reassessment_id": first["id"]}, policy, "separate-principal")
    assert operational["status"] == "admitted"
    assert operational["billable_execution_permitted"] is False
    joined = service.reconcile(receipt["plan_id"], {"evaluation_id": evaluation["id"]})
    assert joined["forecast_error"] is None
    assert joined["predictor_learning_performed"] is False
    assert service.plans.get_receipt(receipt["plan_id"]) == receipt
    assert service.plans.get(receipt["plan_id"]) == before
    assert service.get(receipt, first["id"], "reassessment") == first


def test_source_preview_supplies_verified_rules_without_writing(lifecycle):
    service, receipt, _, requirements, _, _ = lifecycle
    preview = service.preview(receipt["plan_id"], "run-1")
    assert preview["compatible"] is True
    assert preview["writes_performed"] is False
    assert preview["billable_execution_permitted"] is False
    assert preview["task_count"] == preview["acceptance_count"] == 60
    segment = preview["segments"][0]
    assert segment["suggested_rule_hash"] == requirements["acceptance"]["segments"][0]["rule_hash"]
    assert segment["rules"][0]["definition"]["evaluator_id"] == "portable-evaluator"
    assert not service.root.exists()
    assert service.plans.get_receipt(receipt["plan_id"]) == receipt


def test_source_preview_does_not_invent_missing_acceptance(lifecycle):
    service, receipt, _, _, run, root = lifecycle
    run["acceptance_outcomes"] = []
    (root / "result.json").write_text(json.dumps(run))
    preview = service.preview(receipt["plan_id"], "run-1")
    assert preview["compatible"] is True
    assert preview["acceptance_count"] == 0
    assert preview["segments"][0]["suggested_rule_hash"] is None
    assert preview["segments"][0]["rules"] == []
    assert not service.root.exists()


def test_source_preview_explains_incompatible_lineage(lifecycle):
    service, receipt, _, _, run, root = lifecycle
    run["trajectory_contract"] = copy.deepcopy(run["trajectory_contract"])
    run["trajectory_contract"]["workload"]["version"] = "different-workload.v2"
    (root / "result.json").write_text(json.dumps(run))
    preview = service.preview(receipt["plan_id"], "run-1")
    assert preview["compatible"] is False
    assert "workload lineage mismatch" in preview["reason"]
    assert preview["segments"] == []
    assert not service.root.exists()


@pytest.mark.parametrize("failing", ["budget", "acceptance"])
def test_known_constraint_failure_is_not_reported_as_inconclusive(lifecycle, failing):
    service, receipt, policy, requirements, _, _ = lifecycle
    if failing == "budget":
        requirements["budget"]["budget_usd"] = 0.001
    else:
        requirements["budget"]["minimum_acceptance_lower_bound"] = 0.99
    _, _, evaluation = _evaluate(lifecycle)
    assert evaluation["status"] == "ineligible"
    assessment = service.reassess(receipt["plan_id"], {"evaluation_id": evaluation["id"]}, policy)
    checks = {item["name"]: item["outcome"] for item in assessment["route_decision"]["checks"]}
    assert assessment["status"] == "blocked"
    assert checks["segment_acceptance"] == ("passed" if failing == "budget" else "failed")
    assert checks["monetary_tail_risk"] == ("failed" if failing == "budget" else "passed")


@pytest.mark.parametrize("field", ["workload", "prediction"])
def test_lineage_mismatch_does_not_inherit_admission(lifecycle, field):
    service, receipt, _, _, run, root = lifecycle
    if field == "workload":
        run["trajectory_contract"] = copy.deepcopy(run["trajectory_contract"])
        run["trajectory_contract"]["workload"]["version"] = "plan-workload.v2"
        (root / "result.json").write_text(json.dumps(run))
    else:
        changed = copy.deepcopy(receipt)
        changed["prediction"]["prediction_id"] = "different-forecast"
        changed["content_hash"] = digest({k: v for k, v in changed.items() if k not in {"receipt_id", "content_hash"}})
        service.plans.get_receipt = lambda _: changed
    with pytest.raises(ValueError, match="lineage|exact original"):
        service.attach(receipt["plan_id"], {"run_id": "run-1", "adapter_id": service.config["adapter_id"]})


@pytest.mark.parametrize("missing", ["acceptance_outcomes", "meter_ledger_evidence", "sampling_evidence"])
def test_missing_evidence_stays_inconclusive(lifecycle, missing):
    service, receipt, policy, _, run, root = lifecycle
    run.pop(missing)
    (root / "result.json").write_text(json.dumps(run))
    _, _, evaluation = _evaluate(lifecycle)
    assert evaluation["status"] == "inconclusive"
    decision = service.reassess(receipt["plan_id"], {"evaluation_id": evaluation["id"]}, policy)
    assert decision["status"] == "blocked"


def test_missing_material_segment_is_not_hidden_by_aggregate(lifecycle):
    lifecycle[3]["acceptance"]["segments"].append({"segment_id": "missing", "segment_version": "segment.v1", "rule_hash": None})
    _, _, evaluation = _evaluate(lifecycle)
    assert evaluation["status"] == "inconclusive"
    assert evaluation["material_segments_complete"] is False


def test_partial_task_pricing_never_becomes_complete_cost(lifecycle):
    service, receipt, policy, _, run, root = lifecycle
    run["meter_ledger_evidence"] = [item for item in run["meter_ledger_evidence"] if item["entry_id"].endswith("direct_token")]
    (root / "result.json").write_text(json.dumps(run))
    _, _, evaluation = _evaluate(lifecycle)
    assert evaluation["complete_task_cost_coverage"] is False
    assert evaluation["expected_complete_task_cost"] is None


def test_tampered_sources_and_records_are_rejected(lifecycle):
    service, receipt, policy, _, run, root = lifecycle
    request, attachment, evaluation = _evaluate(lifecycle)
    run["evidence_classification"] = "simulated"
    (root / "result.json").write_text(json.dumps(run))
    with pytest.raises(ValueError, match="binding changed"):
        service.reassess(receipt["plan_id"], {"evaluation_id": evaluation["id"]}, policy)
    path = service.root / receipt["plan_id"] / f"{request['id']}.json"
    data = json.loads(path.read_text())
    data["budget"]["budget_usd"] = 999
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="integrity"):
        service.get(receipt, request["id"], "requirements")


@pytest.mark.parametrize("attack", [
    {"run_id": "https://example.invalid/run", "adapter_id": "studio-immutable-runs"},
    {"run_id": "../other", "adapter_id": "studio-immutable-runs"},
    {"run_id": "run-1", "adapter_id": "studio-immutable-runs", "accepted": True},
    {"run_id": "run-1", "adapter_id": "arbitrary-python-module"},
])
def test_import_rejects_paths_urls_code_and_client_pass(lifecycle, attack):
    with pytest.raises(ValueError):
        lifecycle[0].attach(lifecycle[1]["plan_id"], attack)


def test_current_policy_etag_change_blocks_operational_admission(lifecycle):
    service, receipt, policy, _, _, _ = lifecycle
    _, _, evaluation = _evaluate(lifecycle)
    original = service.reassess(receipt["plan_id"], {"evaluation_id": evaluation["id"]}, policy)
    changed = replace(policy, provenance={**policy.provenance, "etag": "new-etag"})
    with pytest.raises(ValueError, match="current receipt/evidence/Azure"):
        service.operational_admission(receipt["plan_id"], {"reassessment_id": original["id"]}, changed, "publisher")


def test_simulated_evidence_cannot_promote(lifecycle):
    service, receipt, policy, _, run, root = lifecycle
    run["evidence_classification"] = "simulated"
    (root / "result.json").write_text(json.dumps(run))
    _, _, evaluation = _evaluate(lifecycle)
    assert evaluation["status"] == "inconclusive"
    assert service.reassess(receipt["plan_id"], {"evaluation_id": evaluation["id"]}, policy)["status"] == "blocked"


def test_missing_rule_and_unverified_scores_cannot_create_a_pass(lifecycle):
    service, receipt, _, _, _, root = lifecycle
    for path in (root / "acceptance_rules").glob("*.json"):
        path.unlink()
    _, _, evaluation = _evaluate(lifecycle)
    assert evaluation["acceptance_definition_bound"] is False
    assert evaluation["segments"][0]["accepted_count"] == 0
    assert evaluation["status"] == "inconclusive"


def test_an_accepted_label_without_a_review_is_not_quality_evidence(lifecycle):
    _, _, _, _, run, root = lifecycle
    store = AcceptanceOutcomeStore(root / "acceptance_outcomes")
    replacements = []
    for reference in run["acceptance_outcomes"]:
        original = store.get(reference["outcome_id"]).outcome
        unsupported = replace(original, outcome_id=f"unsupported-{original.outcome_id}", reviews=())
        saved = store.append(unsupported)
        replacements.append({"outcome_id": unsupported.outcome_id, "content_hash": saved.content_hash})
    run["acceptance_outcomes"] = replacements
    (root / "result.json").write_text(json.dumps(run))
    _, _, evaluation = _evaluate(lifecycle)
    assert evaluation["segments"][0]["accepted_count"] == 0
    assert evaluation["status"] == "inconclusive"


def test_acceptance_rule_source_drift_blocks_reassessment(lifecycle):
    service, receipt, policy, _, _, root = lifecycle
    _, _, evaluation = _evaluate(lifecycle)
    next((root / "acceptance_rules").glob("*.json")).unlink()
    with pytest.raises(ValueError, match="binding changed"):
        service.reassess(receipt["plan_id"], {"evaluation_id": evaluation["id"]}, policy)


def test_requirements_revisions_cannot_rewrite_an_evaluation(lifecycle):
    service, receipt, policy, requirements, _, _ = lifecycle
    original, attachment, evaluation = _evaluate(lifecycle)
    requirements["budget"]["budget_usd"] = 1
    revised = service.requirements(receipt["plan_id"], requirements)
    assert revised["id"] != original["id"]
    assert service.get(receipt, evaluation["id"], "evaluation")["requirements_hash"] == original["content_hash"]
    with pytest.raises(ValueError, match="exactly these fields"):
        service.evaluate(receipt["plan_id"], {
            "attachment_id": attachment["id"], "requirements_id": revised["id"], "mode": "read_only", "quality": 1,
        }, policy, "operator")


def test_comparison_selects_only_eligible_exact_requirements(lifecycle):
    service, receipt, policy, _, _, _ = lifecycle
    _, _, evaluation = _evaluate(lifecycle)
    comparison = service.compare(receipt["plan_id"], {"evaluation_ids": [evaluation["id"]]}, policy)
    assert comparison["status"] == "candidate_selected_for_review"
    assert comparison["selected_evaluation_id"] == evaluation["id"]
    assert comparison["policy_publication_performed"] is False
    assert comparison["billable_execution_permitted"] is False


def test_local_policy_cannot_authorize_evaluation(lifecycle):
    service, receipt, policy, _, _, _ = lifecycle
    requirements, attachment, _ = _evaluate(lifecycle)
    with pytest.raises(ValueError, match="Azure authority"):
        service.evaluate(receipt["plan_id"], {
            "attachment_id": attachment["id"], "requirements_id": requirements["id"], "mode": "read_only",
        }, replace(policy, provenance={**policy.provenance, "source": "local_file"}), "operator")


def test_api_evaluation_and_operational_roles_are_separate(lifecycle, monkeypatch):
    service, receipt, policy, requirements, _, _ = lifecycle
    monkeypatch.setattr(studio, "_lifecycle_service", lambda: service)
    monkeypatch.setattr(studio, "load_policy_from_environment", lambda: policy)
    monkeypatch.delenv("TOKENGOV_EVALUATION_ALLOW_LOCAL", raising=False)
    monkeypatch.delenv("TOKENGOV_OPERATIONAL_ALLOW_LOCAL", raising=False)
    monkeypatch.delenv("TOKENGOV_LIFECYCLE_AUTHENTICATED_INGRESS", raising=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), studio.StudioHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port)
    headers = {"Origin": f"http://127.0.0.1:{server.server_port}", "X-TokenGov-CSRF": studio._lifecycle_request_token}
    def post(action, body):
        connection.request("POST", f"/api/plans/{receipt['plan_id']}/{action}", json.dumps(body), headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    try:
        connection.request("GET", f"/api/plans/{receipt['plan_id']}/run-preview/run-1")
        preview_response = connection.getresponse()
        preview = json.loads(preview_response.read())
        assert preview_response.status == 200
        assert preview["compatible"] is True
        assert not service.root.exists()
        assert post("requirements", requirements)[0] == 403
        monkeypatch.setenv("TOKENGOV_EVALUATION_ALLOW_LOCAL", "true")
        assert post("requirements", requirements)[0] == 201
        assert post("operational-admissions", {"reassessment_id": "none"})[0] == 403
        monkeypatch.setattr(studio, "load_policy_from_environment", lambda: (_ for _ in ()).throw(PolicyLoadError("offline")))
        assert post("evaluations", {})[0] == 503
        headers["Origin"] = "https://untrusted.invalid"
        assert post("requirements", requirements)[0] == 403
        connection.request("POST", "/api/runs", "{}")
        response = connection.getresponse()
        assert response.status == 409
        assert json.loads(response.read())["code"] == "execution_adapter_unavailable"
    finally:
        connection.close()
        server.shutdown()
        thread.join()
        server.server_close()
