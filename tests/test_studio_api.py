from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import studio
from costgov.governance_decisions import (
    DECISION_CONSTRAINT_SCHEMA_VERSION,
    CandidateConstraintEvidence,
    evaluate_segment_constraint,
)
from costgov.mcp_prediction import McpPredictionError
from costgov.planning import PlanStore
from costgov.policy_store import LoadedPolicy


def _analysis(confidence: str = "high", clarifications: list[str] | None = None) -> dict:
    return {
        "schema_version": "1.0",
        "rule_set_version": "enterprise-semantics-test",
        "description_hash": "a" * 64,
        "topology": {
            "selected": "rag_pipeline",
            "confidence": confidence,
            "alternatives": [],
            "evidence": [{"rule": "retrieval_pipeline", "text": "RAG"}],
        },
        "agent_count": {
            "value": 1,
            "source": "defaulted",
            "evidence": [{"rule": "single_actor_default", "text": "1"}],
        },
        "modalities": ["text", "document"],
        "tools": ["file_search"],
        "assumptions": [],
        "exclusions": [],
        "clarifications": clarifications or [],
    }


def _confirm_analysis(parameters: dict) -> dict:
    return {
        **parameters,
        "analysis_confirmed": True,
        "confirmed_profile": {
            "agent_pattern": "rag_pipeline",
            "multi_agent_count": 1,
            "modalities": ["text", "document"],
            "tools": ["file_search"],
        },
    }


def test_analysis_endpoint_returns_predictor_evidence(monkeypatch):
    monkeypatch.setattr(
        studio.McpPredictorClient,
        "analyze",
        lambda self, description: _analysis(),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), studio.StudioHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request(
            "POST",
            "/api/analyze",
            json.dumps({"description": "RAG workload"}),
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())

        assert response.status == 200
        assert payload["rule_set_version"] == "enterprise-semantics-test"
        assert payload["topology"]["selected"] == "rag_pipeline"
    finally:
        server.shutdown()
        thread.join()


def test_main_studio_commercial_route_skips_model_predictor(tmp_path, monkeypatch):
    monkeypatch.setattr(studio, "PLAN_STORE_PATH", tmp_path / "plans")
    monkeypatch.setattr(studio, "REPORT_STORE_PATH", tmp_path / "reports")
    monkeypatch.setattr(
        studio.McpPredictorClient,
        "analyze",
        lambda *_: (_ for _ in ()).throw(AssertionError("predictor not expected")),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), studio.StudioHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        report = _create_report(connection, "Commercial route")
        connection.request(
            "POST",
            "/api/plan",
            json.dumps(
                {
                    "report_id": report["report_id"],
                    "description": "Public support agent",
                    "parameters": {
                        "route": "copilot_studio",
                        "commercial": {
                            "as_of": "2026-08-20",
                            "events": [
                                {"meter_id": "generative_answer", "quantity": 1}
                            ],
                            "entitlement": {
                                "user_segment": "customers",
                                "audience_type": "b2c",
                                "authenticated": False,
                                "identity_mode": "anonymous",
                                "license_sku": None,
                                "channel": "website",
                                "trigger_type": "interactive",
                                "product_boundary": "copilot_studio",
                                "evidence_version": "entitlement-input.v1",
                            },
                        },
                    },
                }
            ),
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        result = json.loads(response.read())

        assert response.status == 201
        assert result["commercial"]["total_copilot_credits"] == 2
        assert result["token_subforecast"] is None
        receipt = PlanStore(tmp_path / "plans").get_receipt(result["plan_id"])
        assert receipt["schema_version"] == "5.0"
        assert receipt["meter_stack"]["route_id"] == "copilot_studio"
    finally:
        server.shutdown()
        thread.join()


def _loaded_policy() -> LoadedPolicy:
    return LoadedPolicy(
        {
            "schema_version": "1.0",
            "policy_id": "tokengov-production",
            "version": "2026-07-20.1",
            "status": "active",
            "effective_from": "2026-01-01T00:00:00Z",
            "admission": {
                "allowed_providers": ["azure_openai"],
                "allowed_models": ["gpt-4.1"],
                "require_pricing_verified": True,
                "max_model_cost_per_call_usd": 0.02,
            },
            "execution": {
                "routing_mode": "balanced",
                "semantic_cache": {"enabled": True, "score_threshold": 0.83},
                "budget": {"per_tenant_usd_per_run": 5.0, "hard_cap_action": "degrade"},
                "evaluation": {"min_quality": 0.8, "min_segment_samples": 2},
            },
            "mutation": {
                "mode": "evaluation_bound",
                "allowed_knobs": ["routing.mode", "semantic_cache.score_threshold"],
            },
        },
        {
            "source": "azure_app_configuration",
            "endpoint": "https://test.azconfig.io",
            "key": "tokengov:policy",
            "label": "production",
            "etag": "etag-1",
        },
    )


def _create_report(connection: HTTPConnection, title: str = "Test report") -> dict:
    connection.request(
        "POST",
        "/api/reports",
        json.dumps({"title": title}),
        {"Content-Type": "application/json"},
    )
    response = connection.getresponse()
    assert response.status == 201
    return json.loads(response.read())


def test_report_can_be_saved_and_reopened(tmp_path, monkeypatch):
    monkeypatch.setattr(studio, "REPORT_STORE_PATH", tmp_path / "reports")
    server = ThreadingHTTPServer(("127.0.0.1", 0), studio.StudioHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        report = _create_report(connection, "Initial title")
        connection.request(
            "POST",
            f"/api/reports/{report['report_id']}/save",
            json.dumps({"title": "Saved assessment", "notes": "Review-ready"}),
            {"Content-Type": "application/json"},
        )
        save_response = connection.getresponse()
        saved = json.loads(save_response.read())
        assert save_response.status == 200
        assert saved["title"] == "Saved assessment"

        connection.request("GET", f"/api/reports/{report['report_id']}")
        reopen_response = connection.getresponse()
        reopened = json.loads(reopen_response.read())
        assert reopened["report_id"] == report["report_id"]
        assert reopened["title"] == "Saved assessment"
        assert reopened["notes"] == "Review-ready"
    finally:
        server.shutdown()
        thread.join()


def test_run_endpoint_returns_queued_run(tmp_path, monkeypatch):
    monkeypatch.setattr(studio, "REGISTRY_PATH", tmp_path / "registry.json")
    monkeypatch.setattr(studio, "REPORT_STORE_PATH", tmp_path / "reports")
    monkeypatch.setattr(studio, "PLAN_STORE_PATH", tmp_path / "plans")
    monkeypatch.setattr(studio, "_execute", lambda run_id, report_id, admission: None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), studio.StudioHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        report = _create_report(connection)
        plan_id = "plan-admitted"
        plan_path = tmp_path / "plans" / plan_id
        plan_path.mkdir(parents=True)
        (plan_path / "session.json").write_text(json.dumps({
            "plan_id": plan_id,
            "report_id": report["report_id"],
            "govern_handoff": {
                "handoff_id": "handoff-1",
                "status": "admitted",
                "plan_id": plan_id,
                "receipt_id": "receipt-1",
                "policy": {
                    "policy_id": "tokengov-production",
                    "version": "2026-07-20.1",
                    "provenance": {"etag": "etag-1"},
                },
                "execution": {},
            },
        }), encoding="utf-8")
        connection.request(
            "POST",
            "/api/runs",
            json.dumps({"report_id": report["report_id"], "plan_id": plan_id}),
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())

        assert response.status == 202
        assert payload["status"] == "queued"
        assert payload["report_id"] == report["report_id"]
        assert payload["binding"]["policy_etag"] == "etag-1"
        assert studio._read_registry()[payload["run_id"]]["run_id"] == payload["run_id"]
    finally:
        server.shutdown()
        thread.join()


def test_observe_endpoint_is_read_only_and_requires_completed_run(
    tmp_path, monkeypatch
):
    registry_path = tmp_path / "studio_runs" / "registry.json"
    monkeypatch.setattr(studio, "REGISTRY_PATH", registry_path)
    registry_path.parent.mkdir()
    registry_path.write_text(
        json.dumps(
            {
                "run-complete": {
                    "run_id": "run-complete",
                    "status": "completed",
                    "result": {
                        "run_id": "run-complete",
                        "report_id": "report-1",
                        "status": "completed",
                    },
                },
                "run-queued": {
                    "run_id": "run-queued",
                    "status": "queued",
                },
            }
        ),
        encoding="utf-8",
    )
    expected = {
        "schema_version": "accepted-task-economics.v1",
        "run_id": "run-complete",
    }
    called = {}

    def project(result, run_root):
        called["result"] = result
        called["run_root"] = run_root
        return expected

    monkeypatch.setattr(studio, "load_observe_economics", project)
    server = ThreadingHTTPServer(("127.0.0.1", 0), studio.StudioHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/api/runs/run-complete/observe")
        response = connection.getresponse()
        payload = json.loads(response.read())

        assert response.status == 200
        assert payload == expected
        assert called["result"]["run_id"] == "run-complete"
        assert called["run_root"] == registry_path.parent / "run-complete"

        connection.request("GET", "/api/runs/run-queued/observe")
        queued_response = connection.getresponse()
        queued = json.loads(queued_response.read())
        assert queued_response.status == 409
        assert queued["code"] == "run_not_completed"
    finally:
        server.shutdown()
        thread.join()


def test_govern_decision_endpoint_persists_comparison_without_policy_mutation(
    tmp_path, monkeypatch
):
    registry_path = tmp_path / "studio_runs" / "registry.json"
    report_path = tmp_path / "reports"
    evidence_path = tmp_path / "governance"
    state_path = tmp_path / "decision-state"
    monkeypatch.setattr(studio, "REGISTRY_PATH", registry_path)
    monkeypatch.setattr(studio, "REPORT_STORE_PATH", report_path)
    monkeypatch.setattr(studio, "GOVERNANCE_EVIDENCE_STORE_PATH", evidence_path)
    monkeypatch.setattr(studio, "DECISION_STATE_STORE_PATH", state_path)
    registry_path.parent.mkdir()
    registry_path.write_text(
        json.dumps(
            {
                "run-expensive": {
                    "status": "completed",
                    "result": {
                        "run_id": "run-expensive",
                        "report_id": "RPT-GOVERN",
                        "status": "completed",
                    },
                },
                "run-cheap": {
                    "status": "completed",
                    "result": {
                        "run_id": "run-cheap",
                        "report_id": "RPT-GOVERN",
                        "status": "completed",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    report = studio.ReportStore(report_path).create("Govern comparison")
    old_report_id = report["report_id"]
    report["report_id"] = "RPT-GOVERN"
    (report_path / old_report_id).rename(report_path / report["report_id"])
    (report_path / report["report_id"] / "report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )

    def constraint(run_result, _run_root):
        candidate_id = (
            "cheap-candidate"
            if run_result["run_id"] == "run-cheap"
            else "expensive-candidate"
        )
        cost = 0.01 if candidate_id == "cheap-candidate" else 0.015
        segment = evaluate_segment_constraint(
            segment_id="hard",
            segment_version="segment.v1",
            acceptance_decisions=["accepted"] * 60,
            allocatable_costs_usd=[cost] * 60,
            acceptance_evidence_hashes=["a" * 64] * 60,
            cost_evidence_hashes=["b" * 64] * 60,
        )
        return CandidateConstraintEvidence(
            schema_version=DECISION_CONSTRAINT_SCHEMA_VERSION,
            constraint_id=f"constraint-{run_result['run_id']}",
            experiment_id="rag-policy-comparison",
            experiment_revision="experiment.v1",
            arm_id=candidate_id,
            candidate_id=candidate_id,
            candidate_version="candidate.v1",
            candidate_content_hash=(
                "c" * 64 if candidate_id == "cheap-candidate" else "d" * 64
            ),
            observation_unit="completed_task",
            evaluated_at="2026-09-01T00:00:00+00:00",
            evidence_classification="measured",
            probability_model="one-sided exact test",
            segments=(segment,),
        )

    monkeypatch.setattr(
        studio, "build_candidate_constraint_from_run", constraint
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), studio.StudioHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request(
            "POST",
            "/api/govern/decisions",
            json.dumps({"run_ids": ["run-expensive", "run-cheap"]}),
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        decision = json.loads(response.read())
        assert response.status == 201
        assert decision["outcome"] == "selected"
        assert decision["selected_candidate_id"] == "cheap-candidate"
        assert decision["mutation_performed"] is False
        assert len(decision["state_transitions"]) == 2

        connection.request("GET", "/api/govern/decisions")
        list_response = connection.getresponse()
        listed = json.loads(list_response.read())
        assert list_response.status == 200
        assert listed["decisions"][0]["decision_id"] == decision["decision_id"]
        reopened = studio.ReportStore(report_path).get("RPT-GOVERN")
        assert reopened["artifacts"]["govern_decisions"][0]["outcome"] == (
            "selected"
        )
    finally:
        server.shutdown()
        thread.join()


def test_studio_observe_ui_shows_denominators_native_meters_and_segments():
    html = (Path(__file__).resolve().parents[1] / "studio.html").read_text(
        encoding="utf-8"
    )

    assert "accepted-task economics" in html
    assert "accepted / ${number(acceptance.evaluated_tasks)} evaluated" in html
    assert "Inconclusive" in html
    assert "Native meter consumption" in html
    assert 'dimensionTable("Segments"' in html
    assert "Uncovered cost components" in html
    assert "95% breach upper bound" in html
    assert "Evaluate completed runs" in html


def test_plan_endpoint_persists_immutable_receipt_and_govern_handoff(tmp_path, monkeypatch):
    monkeypatch.setattr(studio, "PLAN_STORE_PATH", tmp_path / "plans")
    monkeypatch.setattr(studio, "REPORT_STORE_PATH", tmp_path / "reports")
    monkeypatch.setattr(studio, "load_policy_from_environment", _loaded_policy)
    monkeypatch.setattr(studio.McpPredictorClient, "analyze", lambda self, description: _analysis())
    monkeypatch.setattr(
        studio.McpPredictorClient,
        "predict",
        lambda self, description, parameters: {
            "status": "complete",
            "description": description,
            "intake": parameters,
            "prediction": {
                "prediction_id": 42,
                "provider": "azure_openai",
                "model": "gpt-4.1",
                "agent_model_assignments": [
                    {
                        "agent_id": "researcher",
                        "provider": "openai",
                        "model": "gpt-4.1-mini",
                        "allocation_share": 2 / 3,
                    },
                    {
                        "agent_id": "reviewer",
                        "provider": "anthropic",
                        "model": "claude-sonnet-4",
                        "allocation_share": 1 / 3,
                    },
                ],
                "pricing_verified": True,
                "cost_per_call": {"mean": 0.013},
                "monthly_cost": {"mean": 12.0},
            },
            "infrastructure": {"status": "not_estimated", "message": "Separate ledger"},
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), studio.StudioHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        report = _create_report(connection, "RAG economics")
        body = json.dumps({
            "report_id": report["report_id"],
            "description": "RAG for 1000 users",
            "parameters": _confirm_analysis({
                "model": "gpt-4.1",
                "users": 1000,
                "calls_per_user_per_day": 10,
                "agent_models": [
                    {
                        "agent_id": "researcher",
                        "provider": "openai",
                        "model": "gpt-4.1-mini",
                        "turn_weight": 2,
                    },
                    {
                        "agent_id": "reviewer",
                        "provider": "anthropic",
                        "model": "claude-sonnet-4",
                        "turn_weight": 1,
                    },
                ],
            }),
        })
        connection.request(
            "POST", "/api/plan", body, {"Content-Type": "application/json"}
        )
        response = connection.getresponse()
        payload = json.loads(response.read())

        assert response.status == 201
        assert payload["report_id"] == report["report_id"]
        assert payload["description"] == "RAG for 1000 users"
        assert payload["intake"]["users"] == 1000
        assert payload["prediction"]["prediction_id"] == 42
        assert len(payload["receipt_hash"]) == 64
        assert payload["schema_version"] == "5.0"
        assert payload["trajectory_contract"]["schema_version"] == (
            "trajectory-envelope.v1"
        )

        connection.request("GET", f"/api/plans/{payload['plan_id']}")
        persisted_response = connection.getresponse()
        persisted = json.loads(persisted_response.read())
        assert persisted["status"] == "complete"
        assert persisted["receipt_hash"] == payload["receipt_hash"]
        receipt = studio.PlanStore(tmp_path / "plans").get_receipt(payload["plan_id"])
        assert receipt["schema_version"] == "5.0"
        assert receipt["meter_stack"]["route_id"] == "foundry"
        assert receipt["analysis"]["rule_set_version"] == "enterprise-semantics-test"
        assert receipt["confirmed_profile"]["agent_pattern"] == "rag_pipeline"
        assert receipt["assumptions"] == []
        assert receipt["clarifications"] == []
        assert receipt["exclusions"] == []
        assert receipt["intake"]["analysis"]["rule_set_version"] == "enterprise-semantics-test"
        assert receipt["intake"]["confirmed_profile"]["agent_pattern"] == "rag_pipeline"
        assert receipt["intake"]["agent_models"] == payload["intake"]["agent_models"]
        assert receipt["prediction"]["agent_model_assignments"] == (
            payload["prediction"]["agent_model_assignments"]
        )

        receipt_path = (
            tmp_path / "plans" / payload["plan_id"] / "receipts" /
            f"{payload['receipt_id']}.json"
        )
        receipt_before = receipt_path.read_bytes()
        session_path = tmp_path / "plans" / payload["plan_id"] / "session.json"
        legacy_session = json.loads(session_path.read_text(encoding="utf-8"))
        legacy_session.update(
            status="handed_off",
            govern_handoff={
                "handoff_id": "legacy-handoff",
                "status": "pending_admission",
                "plan_id": payload["plan_id"],
                "receipt_id": payload["receipt_id"],
            },
        )
        session_path.write_text(json.dumps(legacy_session), encoding="utf-8")
        connection.request("POST", f"/api/plans/{payload['plan_id']}/govern-handoff")
        handoff_response = connection.getresponse()
        handoff = json.loads(handoff_response.read())
        assert handoff_response.status == 201
        assert handoff["handoff_id"] != "legacy-handoff"
        assert handoff["receipt_hash"] == payload["receipt_hash"]
        assert handoff["prediction_id"] == 42
        assert handoff["status"] == "admitted"
        assert handoff["economics"]["monthly_cost"]["mean"] == 12.0
        assert handoff["policy"]["policy_id"] == "tokengov-production"
        assert handoff["policy"]["provenance"]["etag"] == "etag-1"
        assert all(check["passed"] for check in handoff["checks"])
        assert receipt_path.read_bytes() == receipt_before

        connection.request("GET", f"/api/reports/{report['report_id']}")
        report_response = connection.getresponse()
        reopened = json.loads(report_response.read())
        assert reopened["artifacts"]["plans"][0]["id"] == payload["plan_id"]
        assert reopened["artifacts"]["plans"][0]["prediction_id"] == 42
        assert reopened["artifacts"]["receipts"][0]["id"] == payload["receipt_id"]
        assert reopened["artifacts"]["govern_handoffs"][0]["id"] == handoff["handoff_id"]
        assert reopened["artifacts"]["govern_handoffs"][0]["checks"] == handoff["checks"]
        assert reopened["artifacts"]["govern_handoffs"][0]["economics"]["monthly_cost"]["mean"] == 12.0
    finally:
        server.shutdown()
        thread.join()


def test_schema_1_receipt_reopens_without_reinterpretation(tmp_path):
    plan_id = "legacy-plan"
    receipt_id = "legacy-receipt"
    plan_path = tmp_path / "plans" / plan_id
    receipt_path = plan_path / "receipts" / f"{receipt_id}.json"
    receipt_path.parent.mkdir(parents=True)
    legacy = {
        "receipt_id": receipt_id,
        "report_id": "legacy-report",
        "plan_id": plan_id,
        "schema_version": "1.0",
        "created_at": "2026-07-20T00:00:00+00:00",
        "description": "Legacy RAG workload",
        "intake": {"model": "gpt-4.1"},
        "prediction": {"prediction_id": 1},
        "infrastructure": {"status": "not_estimated"},
        "content_hash": "historical-hash",
    }
    receipt_path.write_text(json.dumps(legacy), encoding="utf-8")
    (plan_path / "session.json").write_text(json.dumps({
        "plan_id": plan_id,
        "receipt_id": receipt_id,
    }), encoding="utf-8")

    reopened = studio.PlanStore(tmp_path / "plans").get_receipt(plan_id)

    assert reopened == legacy
    assert "analysis" not in reopened
    assert "confirmed_profile" not in reopened


def test_plan_endpoint_does_not_complete_receipt_for_unknown_agent_model(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(studio, "PLAN_STORE_PATH", tmp_path / "plans")
    monkeypatch.setattr(studio, "REPORT_STORE_PATH", tmp_path / "reports")
    monkeypatch.setattr(studio.McpPredictorClient, "analyze", lambda self, description: _analysis())
    monkeypatch.setattr(
        studio.McpPredictorClient,
        "predict",
        lambda *args: (_ for _ in ()).throw(
            McpPredictionError("Unknown model for agent 'reviewer'")
        ),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), studio.StudioHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        report = _create_report(connection)
        connection.request(
            "POST",
            "/api/plan",
            json.dumps({
                "report_id": report["report_id"],
                "description": "Research and review agents collaborate.",
                "parameters": _confirm_analysis({
                    "model": "gpt-4.1",
                    "agent_models": [
                        {
                            "agent_id": "researcher",
                            "provider": "openai",
                            "model": "gpt-4.1-mini",
                        },
                        {
                            "agent_id": "reviewer",
                            "provider": "anthropic",
                            "model": "unknown-model",
                        },
                    ],
                }),
            }),
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())

        assert response.status == 502
        assert payload["status"] == "failed"
        sessions = studio.PlanStore(tmp_path / "plans").list()
        assert len(sessions) == 1
        assert sessions[0]["status"] == "failed"
        assert sessions[0]["receipt_id"] is None
        assert studio.PlanStore(tmp_path / "plans").get_receipt(
            sessions[0]["plan_id"]
        ) is None
    finally:
        server.shutdown()
        thread.join()


def test_plan_endpoint_persists_clarification_without_calling_predictor(tmp_path, monkeypatch):
    monkeypatch.setattr(studio, "PLAN_STORE_PATH", tmp_path / "plans")
    monkeypatch.setattr(studio, "REPORT_STORE_PATH", tmp_path / "reports")
    monkeypatch.setattr(
        studio.McpPredictorClient,
        "predict",
        lambda *args: (_ for _ in ()).throw(AssertionError("predictor should not run")),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), studio.StudioHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        report = _create_report(connection)
        connection.request(
            "POST",
            "/api/plan",
            json.dumps({"report_id": report["report_id"], "description": "RAG workload", "parameters": {}}),
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())

        assert response.status == 201
        assert payload["status"] == "needs_clarification"
        assert payload["clarifications"][0]["field"] == "model"
        assert studio.PlanStore(tmp_path / "plans").get(payload["plan_id"])["status"] == "needs_clarification"
    finally:
        server.shutdown()
        thread.join()


def test_clarification_reply_completes_the_same_plan_session(tmp_path, monkeypatch):
    monkeypatch.setattr(studio, "PLAN_STORE_PATH", tmp_path / "plans")
    monkeypatch.setattr(studio, "REPORT_STORE_PATH", tmp_path / "reports")
    monkeypatch.setattr(studio.McpPredictorClient, "analyze", lambda self, description: _analysis())
    monkeypatch.setattr(
        studio.McpPredictorClient,
        "predict",
        lambda self, description, parameters: {
            "status": "complete",
            "description": description,
            "intake": parameters,
            "prediction": {"prediction_id": 7},
            "infrastructure": {"status": "not_estimated", "message": "Separate ledger"},
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), studio.StudioHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        report = _create_report(connection)
        connection.request(
            "POST",
            "/api/plan",
            json.dumps({"report_id": report["report_id"], "description": "RAG workload", "parameters": {}}),
            {"Content-Type": "application/json"},
        )
        first_response = connection.getresponse()
        first = json.loads(first_response.read())
        connection.request(
            "POST",
            "/api/plan",
            json.dumps({"report_id": report["report_id"], "plan_id": first["plan_id"], "parameters": _confirm_analysis({"model": "gpt-4.1"})}),
            {"Content-Type": "application/json"},
        )
        second_response = connection.getresponse()
        second = json.loads(second_response.read())

        assert second_response.status == 201
        assert second["plan_id"] == first["plan_id"]
        assert second["prediction"]["prediction_id"] == 7
        assert studio.PlanStore(tmp_path / "plans").get(first["plan_id"])["status"] == "complete"
    finally:
        server.shutdown()
        thread.join()


def test_policy_api_exposes_effective_policy_and_creates_review_only_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(studio, "POLICY_CHANGE_STORE_PATH", tmp_path / "policy_changes")
    monkeypatch.setattr(studio, "load_policy_from_environment", _loaded_policy)
    server = ThreadingHTTPServer(("127.0.0.1", 0), studio.StudioHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/api/policy")
        policy_response = connection.getresponse()
        effective = json.loads(policy_response.read())
        assert policy_response.status == 200
        assert effective["policy"]["version"] == "2026-07-20.1"
        assert effective["provenance"]["etag"] == "etag-1"
        assert effective["change_control"]["browser_write_permitted"] is False
        assert len(effective["content_hash"]) == 64

        connection.request(
            "POST",
            "/api/policy-change-requests",
            json.dumps({
                "reason": "Reduce the maximum admitted unit cost.",
                "proposed_version": "2026-07-20.2",
                "changes": {"admission.max_model_cost_per_call_usd": 0.015},
            }),
            {"Content-Type": "application/json"},
        )
        proposal_response = connection.getresponse()
        proposal = json.loads(proposal_response.read())
        assert proposal_response.status == 201
        assert proposal["status"] == "draft"
        assert proposal["publication"]["azure_write_permitted"] is False

        connection.request(
            "POST",
            "/api/policy-change-requests",
            json.dumps({
                "reason": "Invalid quality floor.",
                "proposed_version": "2026-07-20.3",
                "changes": {"execution.evaluation.min_quality": "not-a-number"},
            }),
            {"Content-Type": "application/json"},
        )
        invalid_response = connection.getresponse()
        assert invalid_response.status == 400

        connection.request("GET", "/api/policy-change-requests")
        list_response = connection.getresponse()
        listed = json.loads(list_response.read())
        assert listed["change_requests"][0]["change_id"] == proposal["change_id"]
    finally:
        server.shutdown()
        thread.join()


def test_models_endpoint_returns_provider_specific_priced_offerings(monkeypatch):
    monkeypatch.setattr(
        studio.McpPredictorClient,
        "model_catalog",
        lambda self: {
            "offerings": [{
                "key": "azure_openai:gpt-4.1",
                "model": "gpt-4.1",
                "provider": "azure_openai",
                "pricing": {"input": 2.0, "output": 8.0},
            }],
            "unavailable": [],
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), studio.StudioHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/api/models")
        response = connection.getresponse()
        catalog = json.loads(response.read())

        assert response.status == 200
        assert catalog["offerings"][0]["key"] == "azure_openai:gpt-4.1"
        assert catalog["offerings"][0]["pricing"]["output"] == 8.0
    finally:
        server.shutdown()
        thread.join()


def test_low_confidence_analysis_requires_confirmed_profile_and_creates_no_receipt(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(studio, "PLAN_STORE_PATH", tmp_path / "plans")
    monkeypatch.setattr(studio, "REPORT_STORE_PATH", tmp_path / "reports")
    monkeypatch.setattr(
        studio.McpPredictorClient,
        "analyze",
        lambda self, description: _analysis(
            "low", ["Confirm whether this is one model call or a bounded workflow."]
        ),
    )
    monkeypatch.setattr(
        studio.McpPredictorClient,
        "predict",
        lambda *args: (_ for _ in ()).throw(AssertionError("predictor should not run")),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), studio.StudioHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        report = _create_report(connection)
        connection.request(
            "POST",
            "/api/plan",
            json.dumps({
                "report_id": report["report_id"],
                "description": "Assist with a business process.",
                "parameters": {"model": "gpt-4.1"},
            }),
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())

        assert response.status == 201
        assert payload["status"] == "needs_clarification"
        assert payload["analysis"]["topology"]["confidence"] == "low"
        assert payload["clarifications"][0]["field"] == "workload_analysis"
        assert studio.PlanStore(tmp_path / "plans").get_receipt(payload["plan_id"]) is None
    finally:
        server.shutdown()
        thread.join()