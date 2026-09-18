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
import hashlib
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
    # Keep test writes project-local while respecting the builder's source boundary.
    source = tmp_path / "source"
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for relative in manifest["include"]:
        builder._copy_entry(ROOT, relative, source)
    predictor = manifest["predictor_component"]
    for relative in predictor["include"]:
        builder._copy_entry(ROOT / predictor["path"], relative, source / predictor["path"])
    (source / ".env").write_text("EXCLUDED_FIXTURE=true")
    inventory = builder.build(source, MANIFEST, destination)

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


def test_studio_html_gates_lifecycle_on_server_release_capabilities():
    html = (ROOT / "studio.html").read_text(encoding="utf-8").lower()

    assert "analyze workload" in html
    assert "confirm profile and estimate" in html
    assert "forecast-only release" in html
    assert "state.lifecyclecapabilities.read_only_import" in html
    assert "govern" in html
    assert "use a supported workload below" in html
    assert 'state.lifecyclecapabilities?.release === "full_studio"' in html
    assert "evaluate acceptance and economics" in html
    assert "billing reconciliation and forecast learning" in html
    assert "describe the work you want ai to complete" in html
    assert 'class="description-help"' in html
    assert 'role="tooltip"' in html
    assert "selected fields override conflicting values in the description" in html
    assert 'id="plan-policy-context"' in html
    assert 'aria-label="general information"' in html
    assert "<caption>general information</caption>" in html
    assert "<strong>active report</strong>" in html
    assert "<strong>forecast</strong>" in html
    assert "<strong>policy</strong>" in html
    assert "request change" in html
    assert 'id="proposal-providers"' in html
    assert 'id="proposal-models"' in html
    assert 'id="proposal-provider-options"' in html
    assert 'id="proposal-model-options"' in html
    assert "function renderpolicysupplypickers" in html
    assert "state.modelcatalog.foreach" in html
    assert 'data-policy-supply="${kind}"' in html
    assert "plan cannot publish policy" in html
    assert "how will this work be delivered?" in html
    assert "copilot studio native usage and entitlement" in html
    assert "github copilot token-derived ai credits" in html
    assert "advanced forecast evidence preview" not in html
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
    assert '<body>' in html
    assert 'data-evidence-mode=' not in html
    assert "tokeneconomics-studio-evidence-mode" not in html
    assert 'class="panel wide evidence-details"' in html
    assert ".signal-grid" in html
    assert ".admission-banner" in html
    assert ".donut" in html
def test_studio_plan_keeps_foundry_evidence_visible_and_simplifies_chrome():
    html = (ROOT / "studio.html").read_text(encoding="utf-8")

    assert 'class="report-workspace"' in html
    assert 'class="report-workspace-table"' in html
    assert 'aria-label="General Information"' in html
    assert "<caption>General Information</caption>" in html
    assert "<th>Context</th><th>Selection</th><th>State</th><th>Actions</th>" in html
    assert 'class="report-actions"' in html
    assert 'id="plan-library"' in html
    assert "AI workload forecast" not in html
    assert 'class="flow"' not in html
    assert "function signalLegend()" in html
    assert "Verified or cleared" in html
    assert "Modeled, incomplete, or needs review" in html
    assert "Blocked or ineligible" in html
    assert 'const essentialEvidence = { essential: true, open: true };' in html
    assert 'data-detail="${detailType}"' in html
    assert "renderHybridFoundryEvidence(plan)" in html
    assert "Non-model tool-charge evidence" in html
    assert "Workload, assumptions, and provenance" in html
    assert "function canonicalModelName(model)" in html
    assert "Select at least one allowed provider and model." in html
    assert "Send for approval" in html
    assert "Open publication workflow" in html
    assert "merge the pull request, open the publication workflow" in html
    assert "Create policy" in html
    assert "Edit active policy" in html
    assert "Delete draft" in html
    assert "Delete stale request" in html
    assert "Enter a business reason before saving the draft." in html
    assert 'submit.textContent = "Saving..."' in html
    assert 'method: "DELETE"' in html
    assert '"X-TokenGov-CSRF": reviewConfig.csrf_token' in html
    assert 'submit.textContent = "Sending..."' in html
    assert "min-height: 132px" in html
    assert "font-size: 24px" in html
    assert "${escapeHtml(policy.policy_id)}" in html
    assert 'const reviewConfig = state.effectivePolicy?.change_control?.review || {};' in html
    assert "Local GitHub CLI" in html
    assert "--control-height: 36px" in html
    assert "--control-font-size: 13px" in html
    assert "body.console-ui .secondary { text-transform: uppercase" not in html
    assert "Approved PR merges invoke the protected workflow." in html
    assert "Open GitHub approval workflow" not in html
    assert "TOKENGOV_APPROVAL_URL" not in html
    assert "Outside approved model policy." in html
    assert "choose an approved model or propose a change in Policy before execution" in html
    assert "Send to Govern" not in html
    assert "Describe the work you want AI to complete" in html


def test_studio_ui_v1_restore_point_is_immutable():
    backup = ROOT / "docs" / "archive" / "studio-ui" / "2026-09-01-v1" / "studio.html"

    assert backup.is_file()
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == (
        "607d95685a8462be93a6e3962aa262ff5aedc9a64b1d850634055af04772d5d0"
    )


def test_studio_home_uses_theme_aware_filterable_report_directory():
    html = (ROOT / "studio.html").read_text(encoding="utf-8")

    assert 'class="home-appearance"' in html
    assert 'class="sidebar-appearance"' in html
    assert '<header><div><h1 id="title"' in html
    assert '<header><div><h1 id="title"' in html and (
        '<div class="appearance-switch"' not in html.split("<header>", 1)[1].split("</header>", 1)[0]
    )
    assert 'class="home-hero"' in html
    assert 'class="home-banner"' in html
    assert 'class="new-report-panel"' in html
    assert 'class="report-table"' in html
    assert 'data-report-sort="updated_at"' in html
    assert 'data-report-filter="report_id"' in html
    assert 'data-report-filter="outcome"' in html
    assert 'id="report-search"' in html
    assert "function renderReportDirectory()" in html
    assert 'if (button.dataset.view === "home") return;' in html
    assert 'class="report-card"' not in html


def test_studio_navigation_uses_status_tones_dots_and_persistent_plan_selector():
    html = (ROOT / "studio.html").read_text(encoding="utf-8")

    assert ".report-directory input {" in html
    assert "border-radius: var(--fl-radius)" in html
    assert ".status-signal.success-tone" in html
    assert ".status-signal.warning-tone" in html
    assert ".status-signal.danger-tone" in html
    assert "function humanizeStatus(value)" in html
    assert "function statusTone(value)" in html
    assert "radial-gradient(circle, var(--cp-border) 1px, var(--cp-bg) 1px)" in html
    assert 'id="plan-library"' in html
    assert 'id="plan-selection"' in html
    assert "function renderPlanLibrary(plans, selectedPlanId)" in html
    assert "plans[plans.length - 1]" in html
    assert "renderPlanLibrary(state.report?.artifacts?.plans || [], planId);" in html


def test_studio_removes_legacy_handoff_controls_without_enabling_execution():
    html = (ROOT / "studio.html").read_text(encoding="utf-8")
    assert "async function handoffToGovern" not in html
    assert 'id="retry-govern-handoff-button"' not in html
    assert 'id="rag-send-question" disabled' in html
    assert "performanceFindingAction" in html
    assert "state.ragSubmissionUncertain" in html


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


def test_forecast_only_release_does_not_expose_lifecycle_subresources(tmp_path, monkeypatch):
    server, thread, connection = _serve(tmp_path, monkeypatch)
    try:
        plan = plan_studio.PlanStore(tmp_path / "plans").create_session("report-1", "Forecast", {})
        connection.request("GET", "/api/lifecycle")
        response = connection.getresponse()
        capabilities = json.loads(response.read())
        assert response.status == 200
        assert capabilities["read_only_import"] is False
        assert capabilities["bounded_evaluation"] is False
        for suffix in ("lifecycle", "run-preview/run-1", "govern-candidate"):
            connection.request("GET", f"/api/plans/{plan['plan_id']}/{suffix}")
            response = connection.getresponse()
            assert response.status == 404
            assert json.loads(response.read())["code"] == "forecast_only_release"
        for action in ("requirements", "attachments", "evaluations", "reassessments", "operational-admissions", "comparisons", "reconciliations"):
            status, _, body = _post(connection, f"/api/plans/{plan['plan_id']}/{action}", {})
            assert status == 404
            assert body["error"] == "not_found"
    finally:
        server.shutdown()
        thread.join()
        connection.close()
        server.server_close()


def test_plan_only_api_completes_and_reopens_immutable_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_studio, "route_requires_infrastructure", lambda _route: False)
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
    monkeypatch.setattr(plan_studio, "route_requires_infrastructure", lambda _route: False)
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
