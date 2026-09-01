from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import subprocess
import sys
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import plan_studio


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "plan_release_manifest.json"
BUILDER_PATH = ROOT / "scripts" / "build_plan_release.py"


def test_plan_release_manifest_is_explicit_and_excludes_non_plan_surfaces():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert manifest["schema_version"] == "1.0"
    assert manifest["release_gate"] == "TE-008"
    assert manifest["surface"] == "studio-plan-readonly"
    assert manifest["entry_point"] == "plan_studio.py"
    assert manifest["static_entry"] == "studio.html"
    assert manifest["predictor_component"]["path"] == "FutureTokenPredictor"
    assert manifest["predictor_component"]["test_command"] == (
        "python scripts/run_tests.py --all --expect pass"
    )
    included = set(manifest["include"])
    assert {
        "plan_studio.py", "studio.html", "plan_requirements.txt",
        "costgov/commercial_planning.py",
        "costgov/commercial_forecasting.py",
        "costgov/experiment_contracts.py",
        "costgov/policy_candidates.py",
        "costgov/mcp_prediction.py",
        "data/contracts/experiment-manifest.v1.schema.json",
        "data/contracts/policy-candidate.v1.schema.json",
        "data/experiments",
        "data/policy_candidates",
        "data/commercial",
    } <= included
    assert "requirements.txt" not in included
    assert not included & {
        "studio.py",
        "costgov/orchestrator.py",
        "costgov/policy_store.py",
        "costgov/policy_changes.py",
        "costgov/gateway.py",
        "costgov/evaluator.py",
        "costgov/reconciliation.py",
        "rag",
        ".env",
    }


def test_release_builder_copies_only_allowlisted_files_and_verifies_hashes(tmp_path):
    spec = importlib.util.spec_from_file_location("plan_release_builder", BUILDER_PATH)
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    destination = tmp_path / "release"
    inventory = builder.build(ROOT, MANIFEST, destination)

    copied = {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*") if path.is_file()
    }
    assert "release_inventory.json" in copied
    assert "studio.py" not in copied
    assert ".env" not in copied
    assert not any(path.startswith("studio_reports/") for path in copied)
    assert not any(path.startswith("FutureTokenPredictor/tests/") for path in copied)
    assert inventory["manifest_sha256"]
    assert builder.verify(destination) == []

    result = subprocess.run(
        [sys.executable, "-B", "-c", "import plan_studio"],
        cwd=destination,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    (destination / "studio.html").write_text("tampered", encoding="utf-8")
    assert builder.verify(destination) == ["hash mismatch: studio.html"]


def test_plan_entry_point_has_no_govern_run_or_azure_runtime_imports():
    tree = ast.parse((ROOT / "plan_studio.py").read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )

    forbidden = {
        "costgov.orchestrator",
        "costgov.policy_store",
        "costgov.policy_changes",
        "costgov.azure_integrations",
        "azure.appconfiguration",
    }
    assert not imports & forbidden


def test_studio_html_marks_non_plan_surfaces_read_only_v2():
    html = (ROOT / "studio.html").read_text(encoding="utf-8").lower()

    assert "analyze workload" in html
    assert "confirm profile and estimate" in html
    assert "read-only in v1" in html
    assert "will be enabled in v2" in html
    assert "govern" in html
    assert "benchmark runs" in html
    assert "observed economics" in html
    assert "forecast feedback" in html
    assert "describe the work you want ai to complete" in html
    assert 'class="description-help"' in html
    assert 'role="tooltip"' in html
    assert "selected fields override conflicting values in the description" in html
    assert "how will this work be delivered?" in html
    assert "copilot studio native usage and entitlement" in html
    assert "github copilot token-derived ai credits" in html
    assert "advanced forecast evidence preview" in html
    assert 'data-ui-mode="standard"' in html
    assert 'data-ui-mode="console-light"' in html
    assert 'data-ui-mode="console-dark"' in html
    assert "microsoft fluent appearance" in html
    assert 'html[data-ui-mode="standard"]' in html
    assert 'html[data-ui-mode="console-light"]' in html
    assert 'html[data-ui-mode="console-dark"]' in html
    assert "--cp-accent: #0f6cbd" in html
    assert "--cp-accent: #0f7e54" in html
    assert "--cp-accent: #57e39a" in html
    assert "body.console-ui .view > .toolbar" in html
    assert "tokeneconomics-studio-ui-mode" in html
    assert "body.console-ui" in html
    assert "body.console-ui .flow { isolation: isolate;" in html
    assert (
        "linear-gradient(var(--cp-accent-soft), var(--cp-accent-soft)), "
        "var(--cp-bg-elevated)"
    ) in html


def test_studio_html_exposes_trajectory_contract_evidence():
    html = (ROOT / "studio.html").read_text(encoding="utf-8")

    assert "Trajectory contract" in html
    assert "Workload identity" in html
    assert "Segment schema" in html
    assert "Trajectory evidence" in html
    assert 'plan.schema_version || "5.0"' in html


def test_studio_html_has_route_specific_workload_samples():
    html = (ROOT / "studio.html").read_text(encoding="utf-8")

    for route in {
        "included",
        "cowork",
        "agent_builder",
        "copilot_studio",
        "work_iq",
        "foundry",
        "github_copilot",
        "copilot_studio_byom",
        "foundry_work_iq",
    }:
        assert f"{route}:" in html
    assert "function updateWorkloadExample(route)" in html
    assert "prompt.value === previousGeneratedSample" in html
    assert "Sample: ${example}" in html


def _serve(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_studio, "PLAN_STORE_PATH", tmp_path / "plans")
    monkeypatch.setattr(plan_studio, "REPORT_STORE_PATH", tmp_path / "reports")
    server = ThreadingHTTPServer(("127.0.0.1", 0), plan_studio.PlanStudioHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    return server, thread, HTTPConnection("127.0.0.1", server.server_port)


def _post(connection, path, payload):
    connection.request(
        "POST", path, json.dumps(payload), {"Content-Type": "application/json"}
    )
    response = connection.getresponse()
    return response.status, dict(response.headers), json.loads(response.read())


def _analysis():
    return {
        "schema_version": "1.0",
        "rule_set_version": "enterprise-semantics-test",
        "description_hash": "a" * 64,
        "topology": {
            "selected": "rag_pipeline", "confidence": "medium",
            "alternatives": [],
            "evidence": [{"rule": "retrieval_pipeline", "text": "RAG"}],
        },
        "agent_count": {"value": 1, "source": "defaulted", "evidence": []},
        "modalities": ["text", "document"],
        "tools": ["file_search"],
        "quantities": {},
        "assumptions": [], "clarifications": [], "exclusions": [],
    }


def test_plan_only_api_completes_and_reopens_immutable_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_studio.McpPredictorClient, "analyze", lambda *_: _analysis())
    monkeypatch.setattr(
        plan_studio.McpPredictorClient,
        "predict",
        lambda self, description, parameters: {
            "status": "complete", "description": description, "intake": parameters,
            "prediction": {
                "prediction_id": 17, "model": "gpt-4.1", "provider": "openai",
                "archetype": "RAG_Pipeline", "pricing_verified": True,
                "tokens_per_call": {"total": 1000},
                "cost_per_call": {"mean": 0.01},
                "monthly_cost": {"mean": 30}, "annual_cost": {"mean": 365},
            },
            "infrastructure": {"status": "not_estimated", "message": "Separate ledger"},
        },
    )
    server, thread, connection = _serve(tmp_path, monkeypatch)
    try:
        status, _, report = _post(connection, "/api/reports", {"title": "Release proof"})
        assert status == 201
        status, headers, result = _post(connection, "/api/plan", {
            "report_id": report["report_id"],
            "description": "RAG over contract documents",
            "parameters": {
                "model": "gpt-4.1", "provider": "openai",
                "analysis_confirmed": True,
                "confirmed_profile": {
                    "agent_pattern": "rag_pipeline", "multi_agent_count": 1,
                    "modalities": ["text", "document"], "tools": ["file_search"],
                },
            },
        })
        assert status == 201
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert len(result["receipt_hash"]) == 64
        assert result["schema_version"] == "5.0"
        assert result["trajectory_contract"]["schema_version"] == (
            "trajectory-envelope.v1"
        )

        connection.request("GET", f"/api/plans/{result['plan_id']}/receipt")
        response = connection.getresponse()
        receipt = json.loads(response.read())
        assert response.status == 200
        assert receipt["schema_version"] == "5.0"
        assert receipt["meter_stack"]["route_id"] == "foundry"
        assert receipt["content_hash"] == result["receipt_hash"]
        assert receipt["analysis"]["rule_set_version"] == "enterprise-semantics-test"
    finally:
        server.shutdown(); thread.join(); connection.close()


def test_plan_only_api_rejects_malformed_oversized_and_non_plan_routes(tmp_path, monkeypatch):
    server, thread, connection = _serve(tmp_path, monkeypatch)
    try:
        connection.request("POST", "/api/reports", "[", {"Content-Type": "application/json"})
        malformed = connection.getresponse()
        assert malformed.status == 400
        malformed.read()

        oversized_body = "x" * (plan_studio.MAX_REQUEST_BYTES + 1)
        connection.request("POST", "/api/reports", oversized_body, {"Content-Type": "application/json"})
        oversized = connection.getresponse()
        assert oversized.status == 400
        oversized.read()

        for path in ("/api/policy", "/api/runs", "/api/policy-change-requests"):
            connection.request("GET", path)
            response = connection.getresponse()
            assert response.status == 404
            response.read()
    finally:
        server.shutdown(); thread.join(); connection.close()


def test_plan_only_api_maps_predictor_and_persistence_failures(tmp_path, monkeypatch):
    server, thread, connection = _serve(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(
            plan_studio.McpPredictorClient, "analyze",
            lambda *_: (_ for _ in ()).throw(plan_studio.McpPredictionError("timed out")),
        )
        status, _, failure = _post(connection, "/api/analyze", {"description": "RAG"})
        assert status == 502
        assert failure["code"] == "analysis_unavailable"

        status, _, report = _post(connection, "/api/reports", {"title": "Disk failure"})
        assert status == 201
        monkeypatch.setattr(
            plan_studio.PlanStore, "create_session",
            lambda *_: (_ for _ in ()).throw(OSError("private disk detail")),
        )
        status, _, failure = _post(connection, "/api/plan", {
            "report_id": report["report_id"], "description": "RAG",
            "parameters": {"model": "gpt-4.1"},
        })
        assert status == 507
        assert failure["error"] == "Plan persistence failed"
        assert "private disk detail" not in json.dumps(failure)
    finally:
        server.shutdown(); thread.join(); connection.close()


def test_plan_only_api_handles_bounded_concurrent_plans(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_studio.McpPredictorClient, "analyze", lambda *_: _analysis())
    monkeypatch.setattr(
        plan_studio.McpPredictorClient,
        "predict",
        lambda self, description, parameters: {
            "status": "complete", "description": description, "intake": parameters,
            "prediction": {
                "prediction_id": int(description.rsplit(" ", 1)[-1]),
                "model": "gpt-4.1", "provider": "openai",
                "archetype": "RAG_Pipeline", "pricing_verified": True,
                "tokens_per_call": {"total": 1000},
                "cost_per_call": {"mean": 0.01},
                "monthly_cost": {"mean": 30}, "annual_cost": {"mean": 365},
            },
            "infrastructure": {"status": "not_estimated", "message": "Separate ledger"},
        },
    )
    server, thread, bootstrap = _serve(tmp_path, monkeypatch)
    try:
        status, _, report = _post(bootstrap, "/api/reports", {"title": "Concurrent proof"})
        assert status == 201

        def complete(index):
            connection = HTTPConnection("127.0.0.1", server.server_port)
            try:
                status, _, body = _post(connection, "/api/plan", {
                    "report_id": report["report_id"],
                    "description": f"RAG over contract documents {index}",
                    "parameters": {
                        "model": "gpt-4.1", "provider": "openai",
                        "analysis_confirmed": True,
                        "confirmed_profile": {
                            "agent_pattern": "rag_pipeline", "multi_agent_count": 1,
                            "modalities": ["text", "document"], "tools": ["file_search"],
                        },
                    },
                })
                return status, body
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=8) as executor:
            outcomes = list(executor.map(complete, range(12)))

        assert [status for status, _ in outcomes] == [201] * 12
        plans = [body for _, body in outcomes]
        assert len({plan["plan_id"] for plan in plans}) == 12
        assert len({plan["receipt_id"] for plan in plans}) == 12
        assert len(plan_studio.ReportStore(tmp_path / "reports").get(report["report_id"])["artifacts"]["receipts"]) == 12
    finally:
        server.shutdown(); thread.join(); bootstrap.close()
