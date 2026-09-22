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
from costgov.studio_lifecycle import StudioLifecycle, digest


REAL_REVIEW = studio._performance_review


@pytest.fixture
def api(tmp_path, monkeypatch):
    base = {"plan_id": "p1", "report_id": "report1"}
    receipt = {**base, "receipt_id": "receipt1", "content_hash": digest(base)}
    config = json.loads((Path(__file__).resolve().parents[1] / "data" / "workload_adapters" / "studio-evidence.v1.json").read_text())
    service = StudioLifecycle(
        tmp_path / "reviews", SimpleNamespace(get_receipt=lambda identity: receipt if identity == "p1" else None),
        {}, tmp_path / "runs", config,
    )
    review = {
        "schema_version": "studio-performance-review.v1", "classification": "advisory",
        "plan_id": "p1", "report_id": "report1", "receipt_id": "receipt1",
        "receipt_hash": receipt["content_hash"], "selected_run_id": "run1",
        "decision": {"status": "review_required", "operational_promotion": False, "automatic_changes": False},
    }
    calls = []

    def build(svc, plan_id, run_id):
        svc.receipt(plan_id)
        calls.append((plan_id, run_id))
        if run_id not in (None, "run1"):
            raise ValueError("Wrong receipt or altered batch")
        return copy.deepcopy(review)

    monkeypatch.setattr(studio, "_lifecycle_service", lambda: service)
    monkeypatch.setattr(studio, "_performance_review", build)
    monkeypatch.setenv("TOKENGOV_EVALUATION_ALLOW_LOCAL", "true")
    monkeypatch.delenv("TOKENGOV_LIFECYCLE_AUTHENTICATED_INGRESS", raising=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), studio.StudioHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"

    def request(method="GET", path="/api/plans/p1/performance", payload=None, authorized=True):
        headers = {"Content-Type": "application/json"}
        if authorized:
            headers.update({"Origin": origin, "X-TokenGov-CSRF": studio._lifecycle_request_token})
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request(method, path, body=json.dumps(payload) if payload is not None else None, headers=headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    yield request, service, calls, review
    server.shutdown()
    thread.join()
    server.server_close()


def test_get_is_read_only_and_returns_no_invented_history(api):
    request, service, calls, review = api
    status, payload = request()
    assert status == 200 and payload["review"] == review
    assert payload["saved_reviews"] == []
    assert not service.root.exists()
    assert calls == [("p1", None)]


def test_no_runs_and_unavailable_authority_remain_explicit(api, monkeypatch):
    request, service, _, _ = api
    from costgov.policy_store import PolicyLoadError

    def unavailable():
        raise PolicyLoadError("Private credential diagnostic must not leak")

    monkeypatch.setattr(studio, "_performance_review", REAL_REVIEW)
    monkeypatch.setattr(studio, "load_policy_from_environment", unavailable)
    status, payload = request()
    assert status == 200
    review = payload["review"]
    assert review["selected_run_id"] is None
    assert review["summary"]["input_tokens"] is None
    assert review["summary"]["observed_model_allocation_usd"] is None
    assert review["policy"]["status"] == "unavailable"
    assert review["decision"]["status"] == "awaiting_evidence"
    assert review["decision"]["operational_promotion"] is False
    assert "Private credential" not in json.dumps(payload)
    assert not service.root.exists()


@pytest.mark.parametrize("query", ["?run_id=../other", "?run_id=run1&run_id=run2", "?score=1"])
def test_query_only_accepts_one_identifier(api, query):
    request, _, calls, _ = api
    assert request(path="/api/plans/p1/performance" + query)[0] == 400
    assert not calls


def test_incompatible_evidence_does_not_return_an_advisory_decision(api):
    request, service, _, _ = api
    status, payload = request(path="/api/plans/p1/performance?run_id=other")
    assert status == 409
    assert "review" not in payload and not service.root.exists()


def test_snapshot_requires_separate_authorization_and_rejects_browser_findings(api):
    request, service, calls, _ = api
    path = "/api/plans/p1/performance-reviews"
    assert request("POST", path, {"run_id": "run1"}, authorized=False)[0] == 403
    assert request("POST", path, {"run_id": "run1", "decision": {"approve": True}})[0] == 400
    assert not calls and not service.root.exists()


def test_saved_advisory_reviews_are_immutable_and_reopen_with_actor_and_sources(api):
    request, service, calls, review = api
    path = "/api/plans/p1/performance-reviews"
    status, first = request("POST", path, {"run_id": "run1"})
    assert status == 201
    record = first["record"]
    assert record["schema_version"] == "studio-performance-decision.v1"
    assert record["actor"] == "local-operator"
    assert record["status"] == "advisory"
    assert record["operational_promotion"] is False and record["mutation_performed"] is False
    assert record["review"] == review
    stored = service.root / "p1" / f"{record['id']}.json"
    before = stored.read_bytes()
    status, second = request("POST", path, {"run_id": "run1"})
    assert status == 201 and second["record"]["id"] != record["id"]
    assert stored.read_bytes() == before
    status, reopened = request()
    assert status == 200 and len(reopened["saved_reviews"]) == 2
    assert all(item["review"]["receipt_hash"] == review["receipt_hash"] for item in reopened["saved_reviews"])
    assert len(calls) == 3


def test_tampered_history_is_not_served_as_verified(api):
    request, service, _, _ = api
    _, saved = request("POST", "/api/plans/p1/performance-reviews", {"run_id": "run1"})
    path = service.root / "p1" / f"{saved['record']['id']}.json"
    record = json.loads(path.read_text())
    record["status"] = "approved"
    path.write_text(json.dumps(record))
    status, payload = request()
    assert status == 409 and "saved_reviews" not in payload


def test_human_qna_decision_requires_authorization_and_server_owned_evidence(api, monkeypatch):
    request, _, _, _ = api
    calls = []

    def record(service, plan_id, payload, actor):
        calls.append((plan_id, payload, actor))
        return {"id": "acceptance-1"}, {"status": "human_reviewed"}

    monkeypatch.setattr(studio, "_record_qna_acceptance", record)
    path = "/api/plans/p1/qna-acceptance"
    payload = {
        "run_id": "run1", "case_id": "qna-f01", "decision": "accepted",
        "reason_code": "reviewer_confirmed",
    }
    assert request("POST", path, payload, authorized=False)[0] == 403
    assert request("POST", path, {**payload, "quality_hash": "browser"})[0] == 400
    status, result = request("POST", path, payload)
    assert status == 201 and result["record"]["id"] == "acceptance-1"
    assert calls == [("p1", payload, "local-operator")]


def test_report_retirement_endpoint_preserves_manifest(api, monkeypatch, tmp_path):
    request, _, _, _ = api
    root = tmp_path / "reports"
    monkeypatch.setattr(studio, "REPORT_STORE_PATH", root)
    report = studio.ReportStore(root).create("Historical assessment")
    before = (root / report["report_id"] / "report.json").read_bytes()
    path = f"/api/reports/{report['report_id']}/retire"
    assert request("POST", path, {"reason": "Replacement assessment"}, authorized=False)[0] == 403
    status, event = request("POST", path, {"reason": "Replacement assessment"})
    assert status == 201 and event["report_id"] == report["report_id"]
    assert studio.ReportStore(root).list() == []
    assert (root / report["report_id"] / "report.json").read_bytes() == before
