import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import studio
from test_performance_api import api  # noqa: F401


@pytest.fixture
def feedback_api(api, monkeypatch):
    request, service, calls, _ = api
    review = {"schema_version": "studio-batch-feedback-review.v1", "plan_id": "p1",
              "run_id": "run1", "billing": {"status": "partial"},
              "learning": {"status": "scope_compatibility_pending"}}

    def read(svc, plan, run):
        calls.append(("read", plan, run))
        return copy.deepcopy(review)

    def record(svc, plan, run, actor, *, refresh_billing):
        calls.append(("write", plan, run, actor, refresh_billing))
        return {"review": review, "record": {"actor": actor}, "created": True}

    monkeypatch.setattr(studio, "_batch_feedback_review", read)
    monkeypatch.setattr(studio, "_record_batch_feedback", record)
    return request, service, calls


def test_read_only_feedback_does_not_fetch_azure_or_record_usage(feedback_api):
    request, service, calls = feedback_api
    status, result = request(path="/api/plans/p1/batch-feedback?run_id=run1")
    assert status == 200 and result["review"]["billing"]["status"] == "partial"
    assert calls == [("read", "p1", "run1")] and not service.root.exists()


@pytest.mark.parametrize("query", ["", "?run_id=", "?run_id=../private", "?run_id=x&run_id=y", "?run_id=x&source=mine"])
def test_feedback_rejects_invalid_or_browser_owned_sources(feedback_api, query):
    request, _, calls = feedback_api
    assert request(path="/api/plans/p1/batch-feedback" + query)[0] == 400
    assert not calls


@pytest.mark.parametrize("action", ["batch-feedback", "batch-billing-sync"])
def test_both_mutations_require_authorization_and_csrf(feedback_api, action):
    request, _, calls = feedback_api
    assert request("POST", f"/api/plans/p1/{action}", {"run_id": "run1"}, authorized=False)[0] == 403
    assert not calls


@pytest.mark.parametrize("extra", [{"allocation": 1}, {"account_url": "https://foreign"}, {"score": 1}, {"refresh_billing": True}])
def test_browser_cannot_supply_allocations_credentials_or_scores(feedback_api, extra):
    request, _, calls = feedback_api
    assert request("POST", "/api/plans/p1/batch-feedback", {"run_id": "run1", **extra})[0] == 400
    assert not calls


def test_usage_registration_is_independent_of_source_refresh(feedback_api):
    request, _, calls = feedback_api
    assert request("POST", "/api/plans/p1/batch-feedback", {"run_id": "run1"})[0] == 201
    assert calls == [("write", "p1", "run1", "local-operator", False)]
    assert request("POST", "/api/plans/p1/batch-billing-sync", {"run_id": "run1"})[0] == 201
    assert calls[-1] == ("write", "p1", "run1", "local-operator", True)


def test_source_failure_is_not_returned_as_success(feedback_api, monkeypatch):
    request, _, _ = feedback_api

    def fail(*args, **kwargs):
        raise ValueError("Internal private source diagnostic")

    monkeypatch.setattr(studio, "_record_batch_feedback", fail)
    status, result = request("POST", "/api/plans/p1/batch-billing-sync", {"run_id": "run1"})
    assert status == 409 and "record" not in result
    assert "Internal private" not in result["error"]


@pytest.mark.parametrize("method,action", [
    ("GET", "batch-feedback"), ("POST", "batch-feedback"), ("POST", "batch-billing-sync"),
])
def test_missing_runtime_returns_explicit_error_without_dropping_connection(feedback_api, monkeypatch, method, action):
    request, _, _ = feedback_api

    def fail(*args, **kwargs):
        raise ModuleNotFoundError(
            "Internal private runtime path",
            name="future_token_predictor.history.response_calibration",
        )

    monkeypatch.setattr(studio, "_batch_feedback_review", fail)
    monkeypatch.setattr(studio, "_record_batch_feedback", fail)
    path = f"/api/plans/p1/{action}"
    if method == "GET":
        path += "?run_id=run1"
    status, result = request(method, path, {"run_id": "run1"} if method == "POST" else None)
    assert status == 503
    assert result["code"] == "feedback_runtime_unavailable"
    assert "restart" in result["error"].lower()
    assert "review" not in result and "record" not in result
    assert "Internal private" not in json.dumps(result)


def test_fresh_studio_process_prefers_checkout_predictor_to_unrelated_install(tmp_path):
    unrelated = tmp_path / "unrelated-install"
    history = unrelated / "future_token_predictor" / "history"
    history.mkdir(parents=True)
    (history.parent / "__init__.py").write_text("", encoding="utf-8")
    (history / "__init__.py").write_text("", encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    environment = {**os.environ, "PYTHONPATH": str(unrelated)}
    code = """
import json
from pathlib import Path
import studio
from future_token_predictor.history import response_calibration
print(json.dumps({"module": str(Path(response_calibration.__file__).resolve())}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=root, env=environment,
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
    expected = root / "FutureTokenPredictor" / "src" / "future_token_predictor" / "history" / "response_calibration.py"
    assert Path(json.loads(result.stdout)["module"]) == expected
