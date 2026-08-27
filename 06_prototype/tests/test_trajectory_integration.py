from __future__ import annotations

import json
import shutil

from costgov.orchestrator import StudioOrchestrator
from costgov.planning import PlanStore
from costgov.policy_store import LoadedPolicy
from costgov.trajectory_contracts import TrajectoryStore


def _policy() -> LoadedPolicy:
    return LoadedPolicy(
        document={
            "schema_version": "1.0",
            "policy_id": "tokengov-production",
            "version": "2026-08-20.1",
            "status": "active",
            "effective_from": "2026-08-20T00:00:00Z",
            "admission": {
                "allowed_providers": ["azure_openai"],
                "allowed_models": ["gpt-4.1"],
                "require_pricing_verified": True,
                "max_model_cost_per_call_usd": 0.02,
            },
            "execution": {
                "routing_mode": "balanced",
                "semantic_cache": {"enabled": True, "score_threshold": 0.83},
                "budget": {
                    "per_tenant_usd_per_run": 5.0,
                    "hard_cap_action": "degrade",
                },
                "evaluation": {"min_quality": 0.8, "min_segment_samples": 2},
            },
            "mutation": {
                "mode": "evaluation_bound",
                "allowed_knobs": [
                    "routing.mode",
                    "semantic_cache.score_threshold",
                ],
            },
        },
        provenance={
            "source": "azure_app_configuration",
            "endpoint": "https://test.azconfig.io",
            "key": "tokengov:policy",
            "label": "production",
            "etag": "etag-trajectory-1",
        },
    )


def _result() -> dict:
    return {
        "status": "complete",
        "description": "Framework-neutral trajectory integration",
        "intake": {
            "route": "foundry",
            "analysis": {},
            "confirmed_profile": {},
        },
        "route": {
            "route_id": "foundry",
            "scope": "model",
            "evidence_version": "commercial-route.v2",
        },
        "meter_stack": {
            "route_id": "foundry",
            "catalog_version": "consumption-models.v1",
            "layers": [],
        },
        "commercial": None,
        "purchase": None,
        "token_subforecast": None,
        "hybrid": None,
        "acceptance_assumption": None,
        "prediction": {
            "prediction_id": 42,
            "provider": "azure_openai",
            "model": "gpt-4.1",
            "pricing_verified": True,
            "cost_per_call": {"mean": 0.013},
            "monthly_cost": {"mean": 12.0},
        },
        "infrastructure": {
            "status": "not_estimated",
            "message": "Separate ledger",
        },
    }


def test_plan_to_reconciliation_preserves_trajectory_contract(tmp_path):
    source = __import__("pathlib").Path(__file__).resolve().parents[1]
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    for name in ("config.json", "workload.json", "golden_set.json"):
        shutil.copy2(source / name, runtime / name)

    plans = PlanStore(tmp_path / "plans")
    session = plans.create_session(
        "RPT-TRAJECTORY-001",
        "Framework-neutral trajectory integration",
        {"route": "foundry"},
    )
    _, receipt = plans.complete(session, _result())
    handoff = plans.create_govern_handoff(session["plan_id"], _policy())

    assert receipt["schema_version"] == "5.0"
    assert receipt["trajectory_contract"]["schema_version"] == (
        "trajectory-envelope.v1"
    )
    assert handoff["trajectory_contract"]["workload"] == (
        receipt["trajectory_contract"]["workload"]
    )
    assert handoff["trajectory_contract"]["prediction_binding"] == {
        "prediction_id": "42",
        "receipt_id": receipt["receipt_id"],
        "schema_version": "5.0",
        "content_hash": receipt["content_hash"],
    }
    assert handoff["trajectory_contract"]["policy_binding"]["etag"] == (
        "etag-trajectory-1"
    )

    result = StudioOrchestrator(runtime).run(
        "run-trajectory-001",
        "RPT-TRAJECTORY-001",
        handoff,
    )

    assert result["trajectory_contract"]["schema_version"] == (
        "trajectory-envelope.v1"
    )
    assert len(result["trajectory_evidence"]) == result["observed"]["requests"]
    assert len(result["evaluation_outcomes"]) == result["observed"]["requests"]
    first = result["trajectory_evidence"][0]
    telemetry_path = runtime / "studio_runs" / result["run_id"] / "telemetry.jsonl"
    telemetry = [
        json.loads(line)
        for line in telemetry_path.read_text(encoding="utf-8").splitlines()
    ]
    telemetry_first = next(
        item for item in telemetry if item["task_id"] == first["task_id"]
    )
    evaluation_first = next(
        item
        for item in result["evaluation_outcomes"]
        if item["task_id"] == first["task_id"]
    )

    assert telemetry_first["trajectory_id"] == first["trajectory_id"]
    assert telemetry_first["workload_id"] == (
        receipt["trajectory_contract"]["workload"]["workload_id"]
    )
    assert telemetry_first["prediction_receipt_id"] == receipt["receipt_id"]
    assert telemetry_first["policy_etag"] == "etag-trajectory-1"
    assert evaluation_first["trajectory_id"] == first["trajectory_id"]
    assert evaluation_first["segment_id"] == telemetry_first["segment_id"]
    assert first["task_id"] in {
        task_id
        for item in result["reconciliation"]
        for task_id in item["task_ids"]
    }

    store = TrajectoryStore(
        runtime / "studio_runs" / result["run_id"] / "trajectories"
    )
    reopened = store.get(first["trajectory_id"])
    assert reopened is not None
    assert reopened.content_hash == first["content_hash"]
    assert reopened.envelope.task.task_id == first["task_id"]
    assert reopened.envelope.policy_binding.etag == "etag-trajectory-1"
