from __future__ import annotations

import hashlib
import json
import shutil

import pytest

from costgov.orchestrator import StudioOrchestrator
from costgov.governance_decisions import (
    ConstraintOutcome,
    build_candidate_constraint_from_run,
)
from costgov.observe_economics import load_observe_economics
from costgov.policy_candidates import (
    POLICY_CANDIDATE_SCHEMA_VERSION,
    CandidateStatus,
    PolicyCandidate,
    PolicyControl,
)


def _experiment_binding(source):
    controls = (
        PolicyControl.from_value(
            "routing-mode",
            "routing",
            "execution.routing_mode",
            "balanced",
            authority="azure_tokengov",
            capability="model_routing",
            enforcement_scope="runtime_enforced",
        ),
        PolicyControl.from_value(
            "semantic-cache",
            "cache",
            "execution.semantic_cache.enabled",
            True,
            authority="azure_tokengov",
            capability="semantic_cache",
            enforcement_scope="runtime_enforced",
        ),
        PolicyControl.from_value(
            "context-pruning",
            "context",
            "execution.context.prune",
            True,
            authority="azure_tokengov",
            capability="context_management",
            enforcement_scope="runtime_enforced",
        ),
        PolicyControl.from_value(
            "evaluation-sample-rate",
            "evaluation",
            "evaluation.sample_rate",
            1.0,
            authority="azure_tokengov",
            capability="evaluation_gate",
            enforcement_scope="control_plane_enforced",
        ),
    )
    candidate = PolicyCandidate(
        schema_version=POLICY_CANDIDATE_SCHEMA_VERSION,
        candidate_id="support-simulation-candidate",
        version="v1",
        status=CandidateStatus.PROPOSED,
        created_at="2026-08-31T18:00:00+00:00",
        experiment_id="rag-policy-comparison",
        experiment_revision="2026-08-31.1",
        meter_stack_id="foundry-meter-stack",
        meter_stack_version="consumption-models.v1",
        meter_stack_content_hash=hashlib.sha256(
            (source / "costgov/consumption_models.py").read_bytes()
        ).hexdigest(),
        controls=controls,
    )
    manifest = json.loads(
        (source / "data/experiments/rag-policy-comparison.v1.json").read_text()
    )
    factor_paths = {control.path for control in controls}
    for arm in manifest["arms"]:
        arm["factors"] = [
            factor for factor in arm["factors"] if factor["path"] in factor_paths
        ]
    candidate_arm = next(
        arm for arm in manifest["arms"] if arm["arm_id"] == "governed-candidate"
    )
    candidate_arm["policy_candidate"] = {
        "category": "policy_candidate",
        "evidence_id": candidate.candidate_id,
        "revision": candidate.version,
        "applicability": "applicable",
        "status": "proposed",
        "authority": "TokenEconomics test candidate",
        "content_hash": candidate.content_hash,
        "location": "inline:test",
        "reason": None,
    }
    return {
        "arm_id": "governed-candidate",
        "manifest": manifest,
        "candidate": candidate.to_dict(),
    }


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
        "experiment_binding": _experiment_binding(source),
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
    assert result["forecast"]["policy_candidate_id"] == (
        "support-simulation-candidate"
    )
    assert {
        item["policy_candidate_id"] for item in result["evaluation_outcomes"]
    } == {"support-simulation-candidate"}
    assert len(result["acceptance_outcomes"]) == 120
    assert {item["decision"] for item in result["acceptance_outcomes"]} == {
        "accepted"
    }
    assert len(result["meter_ledger_evidence"]) == 360
    assert {item["status"] for item in result["meter_reconciliation"]} == {
        "matched",
        "incomplete",
    }
    constraint = build_candidate_constraint_from_run(
        result,
        runtime / "studio_runs" / "integration-test-run",
    )
    assert constraint.outcome is ConstraintOutcome.INCONCLUSIVE
    assert {item.segment_id for item in constraint.segments} == {"easy", "hard"}
    assert all(
        "incomplete_priced_cost_coverage" in item.reason_codes
        for item in constraint.segments
    )
    hard = next(item for item in constraint.segments if item.segment_id == "hard")
    assert "insufficient_segment_samples" in hard.reason_codes
    assert {
        item["comparison_basis"] for item in result["meter_reconciliation"]
    } == {
        "derived_gateway_telemetry_self_consistency",
        "sampled_evaluator_count_self_consistency",
        "coverage_check_no_source_quantity",
    }
    assert any(
        item["meter_family"] == "evaluation"
        and item["cost_coverage_complete"] is False
        for item in result["meter_aggregates"]
    )
    observe = load_observe_economics(
        result, runtime / "studio_runs" / "integration-test-run"
    )
    assert observe["evidence_classification"] == "simulated"
    assert observe["overall"]["operational_completion"]["completed_tasks"] == 120
    assert observe["overall"]["acceptance"]["accepted"] == 120
    assert observe["overall"]["acceptance"]["inconclusive"] == 0
    assert observe["overall"]["quality"]["scored_tasks"] == 120
    assert observe["overall"]["economics"]["coverage_status"] == "incomplete"
    assert observe["evidence_basis"]["integrity_verified"] is True
    assert len(observe["dimensions"]["segments"]) == 2
    assert observe["dimensions"]["meter_stacks"][0]["meter_stack_id"] == (
        "foundry-meter-stack"
    )

    telemetry_path = (
        runtime / "studio_runs" / "integration-test-run" / "telemetry.jsonl"
    )
    telemetry_row = json.loads(telemetry_path.read_text(encoding="utf-8").splitlines()[0])
    assert telemetry_row["report_id"] == "RPT-TEST-001"
    assert telemetry_row["task_id"]
    assert telemetry_row["trajectory_id"]
    assert telemetry_row["policy_etag"] == "etag-1"
    assert telemetry_row["policy_candidate_id"] == (
        "support-simulation-candidate"
    )


def test_orchestrator_fails_closed_for_unverifiable_experiment_binding(tmp_path):
    source = __import__("pathlib").Path(__file__).resolve().parents[1]
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    for name in ("config.json", "workload.json", "golden_set.json"):
        shutil.copy2(source / name, runtime / name)

    admission = {
        "status": "admitted",
        "execution": {"routing_mode": "balanced"},
        "policy": {"policy_id": "active", "version": "v1"},
        "trajectory_contract": {"policy_binding": {"content_hash": "a" * 64}},
        "experiment_binding": {
            "arm_id": "candidate",
            "candidate": {"candidate_id": "unverified"},
        },
    }

    with pytest.raises(ValueError, match="experiment_binding"):
        StudioOrchestrator(runtime).run("run-invalid-binding", "RPT-INVALID", admission)