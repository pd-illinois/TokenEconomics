from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import plan_studio
from costgov.planning import PlanStore


def _commercial_result(quantity: float = 2) -> dict:
    return {
        "status": "complete",
        "description": "Employee support agent",
        "intake": {"route": "copilot_studio"},
        "route": {
            "route_id": "copilot_studio",
            "scope": "commercial_meter",
            "evidence_version": "commercial-route.v1",
        },
        "commercial": {
            "status": "complete",
            "total_copilot_credits": quantity,
            "rate_card": {"version": "2026-08-03.1"},
        },
        "purchase": None,
        "token_subforecast": None,
        "hybrid": None,
        "prediction": {},
        "infrastructure": {
            "status": "not_estimated",
            "message": "Infrastructure remains a separate ledger.",
        },
    }


def test_schema_three_receipt_hashes_complete_commercial_evidence(tmp_path):
    store = PlanStore(tmp_path)
    first_session = store.create_session("report-1", "Agent", {})
    _, first = store.complete(first_session, _commercial_result(2))
    second_session = store.create_session("report-1", "Agent", {})
    _, second = store.complete(second_session, _commercial_result(3))

    assert first["schema_version"] == "3.0"
    assert first["route"]["route_id"] == "copilot_studio"
    assert first["commercial"]["total_copilot_credits"] == 2
    assert first["token_subforecast"] is None
    assert first["content_hash"] != second["content_hash"]
    assert store.get_receipt(first_session["plan_id"]) == first


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
    return response.status, json.loads(response.read())


def test_consumption_catalog_endpoint_exposes_versioned_meter_stacks(
    tmp_path, monkeypatch
):
    server, thread, connection = _serve(tmp_path, monkeypatch)
    try:
        connection.request("GET", "/api/consumption-models")
        response = connection.getresponse()
        catalog = json.loads(response.read())

        assert response.status == 200
        assert catalog["catalog_version"] == "consumption-models.v1"
        assert {item["route_id"] for item in catalog["experiences"]} >= {
            "foundry",
            "copilot_studio",
            "cowork",
            "github_copilot",
        }
    finally:
        server.shutdown()
        thread.join()
        connection.close()


def test_github_route_uses_token_derived_credits_without_foundry_predictor(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        plan_studio.McpPredictorClient,
        "analyze",
        lambda *_: (_ for _ in ()).throw(AssertionError("predictor not expected")),
    )
    monkeypatch.setattr(
        plan_studio.McpPredictorClient,
        "predict",
        lambda *_: (_ for _ in ()).throw(AssertionError("predictor not expected")),
    )
    server, thread, connection = _serve(tmp_path, monkeypatch)
    try:
        status, report = _post(connection, "/api/reports", {"title": "GitHub"})
        assert status == 201
        status, result = _post(
            connection,
            "/api/plan",
            {
                "report_id": report["report_id"],
                "description": "Developer coding assistance",
                "parameters": {
                    "route": "github_copilot",
                    "commercial": {
                        "as_of": "2026-08-20",
                        "plan_id": "copilot_business",
                        "model_id": "gpt-5.4",
                        "seat_count": 1,
                        "fixed_seat_cost_usd": 19,
                        "additional_usage_enabled": False,
                        "token_usage": {
                            "input_tokens": 1_000_000,
                            "cached_input_tokens": 0,
                            "cache_write_tokens": 0,
                            "output_tokens": 100_000,
                            "max_input_tokens_per_request": 100_000,
                        },
                    },
                },
            },
        )

        assert status == 201
        assert result["commercial"]["currency"] == "GitHub AI Credits"
        assert result["commercial"]["gross_github_ai_credits"] == 400
        assert result["prediction"] == {}
        receipt = PlanStore(tmp_path / "plans").get_receipt(result["plan_id"])
        assert receipt["schema_version"] == "4.0"
        assert receipt["meter_stack"]["route_id"] == "github_copilot"
    finally:
        server.shutdown()
        thread.join()
        connection.close()


def test_non_model_commercial_route_does_not_require_predictor_or_model(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        plan_studio.McpPredictorClient,
        "analyze",
        lambda *_: (_ for _ in ()).throw(AssertionError("predictor not expected")),
    )
    monkeypatch.setattr(
        plan_studio.McpPredictorClient,
        "predict",
        lambda *_: (_ for _ in ()).throw(AssertionError("predictor not expected")),
    )
    server, thread, connection = _serve(tmp_path, monkeypatch)
    try:
        status, report = _post(connection, "/api/reports", {"title": "Commercial"})
        assert status == 201
        status, result = _post(
            connection,
            "/api/plan",
            {
                "report_id": report["report_id"],
                "description": "Public support agent",
                "parameters": {
                    "route": "copilot_studio",
                    "commercial": {
                        "as_of": "2026-08-20",
                        "events": [
                            {"meter_id": "generative_answer", "quantity": 2},
                            {"meter_id": "agent_action", "quantity": 1},
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
            },
        )

        assert status == 201
        assert result["route"]["route_id"] == "copilot_studio"
        assert result["commercial"]["total_copilot_credits"] == 9
        assert result["token_subforecast"] is None
        receipt = PlanStore(tmp_path / "plans").get_receipt(result["plan_id"])
        assert receipt["schema_version"] == "4.0"
        assert receipt["meter_stack"]["route_id"] == "copilot_studio"
    finally:
        server.shutdown()
        thread.join()
        connection.close()


def test_unknown_entitlement_stops_before_receipt(tmp_path, monkeypatch):
    server, thread, connection = _serve(tmp_path, monkeypatch)
    try:
        _, report = _post(connection, "/api/reports", {"title": "Unknown"})
        status, result = _post(
            connection,
            "/api/plan",
            {
                "report_id": report["report_id"],
                "description": "Employee agent",
                "parameters": {
                    "route": "copilot_studio",
                    "commercial": {
                        "as_of": "2026-08-20",
                        "events": [
                            {"meter_id": "generative_answer", "quantity": 1}
                        ],
                        "entitlement": {
                            "user_segment": "employees",
                            "audience_type": "b2e",
                            "authenticated": None,
                            "identity_mode": "unknown",
                            "license_sku": None,
                            "channel": "teams",
                            "trigger_type": "interactive",
                            "product_boundary": "copilot_studio",
                            "evidence_version": "entitlement-input.v1",
                        },
                    },
                },
            },
        )

        assert status == 201
        assert result["status"] == "needs_clarification"
        assert result["clarifications"][0]["field"] == "commercial_entitlement"
        assert PlanStore(tmp_path / "plans").get_receipt(result["plan_id"]) is None
    finally:
        server.shutdown()
        thread.join()
        connection.close()


def test_purchase_scope_mismatch_requests_clarification_without_receipt(
    tmp_path, monkeypatch
):
    server, thread, connection = _serve(tmp_path, monkeypatch)
    try:
        _, report = _post(connection, "/api/reports", {"title": "Scope mismatch"})
        status, result = _post(
            connection,
            "/api/plan",
            {
                "report_id": report["report_id"],
                "description": "Public support agent",
                "parameters": {
                    "route": "copilot_studio",
                    "commercial": {
                        "as_of": "2026-08-20",
                        "events": [{"meter_id": "generative_answer", "quantity": 1}],
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
                        "purchase_portfolio": {
                            "version": "purchase.v1",
                            "product": "Wrong product",
                            "environment": "default",
                            "committed_credits": 100,
                            "committed_cost_usd": 10,
                            "payg_enabled": True,
                            "payg_rate_usd_per_credit": 0.01,
                            "fixed_seat_cost_usd": 0,
                            "scope_evidence_version": "scope.v1",
                            "billing_period": "monthly",
                        },
                    },
                },
            },
        )

        assert status == 201
        assert result["status"] == "needs_clarification"
        assert result["clarifications"][0]["field"] == "purchase_portfolio"
        assert PlanStore(tmp_path / "plans").get_receipt(result["plan_id"]) is None
    finally:
        server.shutdown()
        thread.join()
        connection.close()


def test_github_capacity_risk_stops_before_receipt(tmp_path, monkeypatch):
    server, thread, connection = _serve(tmp_path, monkeypatch)
    try:
        _, report = _post(connection, "/api/reports", {"title": "Capacity"})
        status, result = _post(
            connection,
            "/api/plan",
            {
                "report_id": report["report_id"],
                "description": "High-volume GitHub Copilot usage",
                "parameters": {
                    "route": "github_copilot",
                    "commercial": {
                        "as_of": "2026-08-20",
                        "plan_id": "copilot_business",
                        "model_id": "gpt-5.4",
                        "seat_count": 1,
                        "fixed_seat_cost_usd": 19,
                        "additional_usage_enabled": False,
                        "token_usage": {
                            "input_tokens": 10_000_000,
                            "cached_input_tokens": 0,
                            "cache_write_tokens": 0,
                            "output_tokens": 1_000_000,
                            "max_input_tokens_per_request": 100_000,
                        },
                    },
                },
            },
        )

        assert status == 201
        assert result["status"] == "needs_clarification"
        assert result["clarifications"][0]["field"] == "commercial_capacity"
        assert PlanStore(tmp_path / "plans").get_receipt(result["plan_id"]) is None
    finally:
        server.shutdown()
        thread.join()
        connection.close()
