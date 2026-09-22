import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

import studio
from costgov.consumption_models import consumption_catalog
from costgov.planning import PlanStore
from costgov.reports import ReportStore


ROUTES = [item["route_id"] for item in consumption_catalog()["experiences"]]


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("TOKENECONOMICS_FOUNDRY_ONLY", "true")
    monkeypatch.setattr(studio, "PLAN_STORE_PATH", tmp_path / "plans")
    monkeypatch.setattr(studio, "REPORT_STORE_PATH", tmp_path / "reports")
    monkeypatch.setattr(studio, "REGISTRY_PATH", tmp_path / "runs" / "registry.json")
    def unexpected(*args, **kwargs):
        pytest.fail("Route availability must not invoke prediction or analysis")
    monkeypatch.setattr(studio.McpPredictorClient, "analyze", unexpected)
    monkeypatch.setattr(studio.McpPredictorClient, "predict", unexpected)
    server = ThreadingHTTPServer(("127.0.0.1", 0), studio.StudioHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port)
    def request(method, path, payload=None):
        connection.request(method, path, None if payload is None else json.dumps(payload),
                           {"Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read()
        return response.status, json.loads(raw)
    try:
        yield request
    finally:
        connection.close()
        server.shutdown()
        thread.join()
        server.server_close()


def test_hosted_catalog_keeps_all_options_but_only_foundry_selectable(api):
    status, catalog = api("GET", "/api/consumption-models")
    assert status == 200
    assert catalog["experiences"] == consumption_catalog()["experiences"]
    assert len(catalog["experiences"]) == 9
    assert catalog["studio_availability"]["selectable_routes"] == ["foundry"]
    assert "future release" in catalog["studio_availability"]["planned_message"]


@pytest.mark.parametrize("route", [route for route in ROUTES if route != "foundry"])
def test_hosted_read_only_route_is_rejected_without_creating_evidence(api, route):
    report = ReportStore(studio.REPORT_STORE_PATH).create("Test")
    before = (studio.REPORT_STORE_PATH / report["report_id"] / "report.json").read_bytes()
    status, result = api("POST", "/api/plan", {
        "report_id": report["report_id"], "description": "Example", "parameters": {"route": route},
    })
    assert status == 403
    assert result["code"] == "route_read_only"
    assert "future release" in result["error"]
    assert not studio.PLAN_STORE_PATH.exists()
    assert (studio.REPORT_STORE_PATH / report["report_id"] / "report.json").read_bytes() == before


@pytest.mark.parametrize("parameters", [{}, {"route": "foundry"}, {"route": "cowork"}])
def test_hosted_historical_non_foundry_drafts_remain_readable_and_unchanged(api, parameters):
    report = ReportStore(studio.REPORT_STORE_PATH).create("Historical report")
    plan = PlanStore(studio.PLAN_STORE_PATH).create_session(
        report["report_id"], "Historical forecast", {"route": "included"},
    )
    path = studio.PLAN_STORE_PATH / plan["plan_id"] / "session.json"
    before = path.read_bytes()
    assert api("GET", "/api/plans/" + plan["plan_id"])[0] == 200
    status, result = api("POST", "/api/plan", {
        "report_id": report["report_id"], "plan_id": plan["plan_id"], "parameters": parameters,
    })
    assert status == 403 and result["code"] == "route_read_only"
    assert path.read_bytes() == before


@pytest.mark.parametrize("parameters", [{}, {"route": "foundry"}])
def test_hosted_foundry_forecast_entry_remains_available_without_inference(api, parameters):
    report = ReportStore(studio.REPORT_STORE_PATH).create("Foundry report")
    status, result = api("POST", "/api/plan", {
        "report_id": report["report_id"], "parameters": parameters,
    })
    assert status == 201 and result["status"] == "needs_clarification"


def test_native_local_studio_retains_all_routes(api, monkeypatch):
    monkeypatch.delenv("TOKENECONOMICS_FOUNDRY_ONLY")
    status, catalog = api("GET", "/api/consumption-models")
    assert status == 200
    assert catalog["studio_availability"]["selectable_routes"] == ROUTES
    report = ReportStore(studio.REPORT_STORE_PATH).create("Local report")
    for route in ROUTES:
        status, result = api("POST", "/api/plan", {
            "report_id": report["report_id"], "parameters": {"route": route},
        })
        assert status == 201 and result["status"] == "needs_clarification"
