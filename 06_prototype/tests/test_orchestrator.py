from __future__ import annotations

import json
import shutil

from costgov.orchestrator import StudioOrchestrator


def test_studio_orchestrator_runs_closed_loop(tmp_path):
    source = __import__("pathlib").Path(__file__).resolve().parents[1]
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    for name in ("config.json", "workload.json", "golden_set.json"):
        shutil.copy2(source / name, runtime / name)
    orchestrator = StudioOrchestrator(runtime)

    admission = {
        "handoff_id": "handoff-1",
        "plan_id": "plan-1",
        "receipt_id": "receipt-1",
        "status": "admitted",
        "policy": {
            "policy_id": "tokengov-production",
            "version": "2026-07-20.1",
            "schema_version": "1.0",
            "content_hash": "policy-hash",
            "provenance": {
                "source": "azure_app_configuration",
                "label": "production",
                "etag": "etag-1",
            },
        },
        "execution": {
            "routing_mode": "balanced",
            "semantic_cache": {"enabled": True, "score_threshold": 0.83},
            "budget": {"per_tenant_usd_per_run": 5.0, "hard_cap_action": "degrade"},
            "evaluation": {"min_quality": 0.8, "min_segment_samples": 2},
        },
        "trajectory_contract": {
            "schema_version": "trajectory-envelope.v1",
            "workload": {
                "workload_id": "support-workload",
                "version": "support-workload-v1",
            },
            "segment_schema_version": "segment.v1",
            "prediction_binding": {
                "prediction_id": "prediction-1",
                "receipt_id": "receipt-1",
                "schema_version": "5.0",
                "content_hash": "a" * 64,
            },
            "policy_binding": {
                "policy_id": "tokengov-production",
                "version": "2026-07-20.1",
                "content_hash": "b" * 64,
                "source": "azure_app_configuration",
                "label": "production",
                "etag": "etag-1",
            },
        },
    }
    result = orchestrator.run("integration-test-run", "RPT-TEST-001", admission)

    assert result["status"] == "completed"
    assert result["report_id"] == "RPT-TEST-001"
    assert result["policy"]["policy_id"] == "tokengov-production"
    assert result["policy"]["provenance"]["etag"] == "etag-1"
    assert len(result["forecast"]["forecasts"]) == 2
    assert result["observed"]["requests"] == 120
    assert result["observed"]["quality"] >= result["policy"]["quality_floor"]
    assert {item["status"] for item in result["reconciliation"]} == {"recorded"}
    assert len(result["trajectory_evidence"]) == 120
    assert len(result["evaluation_outcomes"]) == 120

    telemetry_path = (
        runtime / "studio_runs" / "integration-test-run" / "telemetry.jsonl"
    )
    telemetry_row = json.loads(telemetry_path.read_text(encoding="utf-8").splitlines()[0])
    assert telemetry_row["report_id"] == "RPT-TEST-001"
    assert telemetry_row["task_id"]
    assert telemetry_row["trajectory_id"]
    assert telemetry_row["policy_etag"] == "etag-1"