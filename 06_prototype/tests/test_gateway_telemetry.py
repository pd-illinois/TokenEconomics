from __future__ import annotations

import json

from costgov.contracts import ExecutionContext
from costgov.gateway import Gateway
from costgov.telemetry import Telemetry


def _config(cache_enabled=True, budget=1.0, action="degrade"):
    return {
        "semantic_cache": {"enabled": cache_enabled, "score_threshold": 0.8},
        "routing": {"mode": "balanced"},
        "budgets": {
            "per_tenant_usd_per_run": budget,
            "hard_cap_action": action,
        },
        "context": {"prune": True, "max_context_items": 3},
    }


def test_gateway_preserves_execution_identity_and_usage(tmp_path):
    telemetry = Telemetry(sample_rate=1.0)
    gateway = Gateway(_config(cache_enabled=False), telemetry)
    execution = ExecutionContext(
        run_id="run-001",
        prediction_id=42,
        segment="factual_lookup",
        policy_version="policy-v1",
        request_id="request-001",
        trace_id="0123456789abcdef0123456789abcdef",
        report_id="RPT-TEST-001",
    )

    result = gateway.handle("tenant-a", "Where is my order", "easy", execution)

    assert result.request_id == "request-001"
    assert result.trace_id == "0123456789abcdef0123456789abcdef"
    assert result.run_id == "run-001"
    assert result.report_id == "RPT-TEST-001"
    assert result.prediction_id == 42
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert telemetry.sampled[0]["prediction_id"] == 42

    path = tmp_path / "telemetry.jsonl"
    telemetry.dump_jsonl(str(path))
    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["report_id"] == "RPT-TEST-001"
    assert row["policy_version"] == "policy-v1"
    assert row["input_tokens"] == result.input_tokens


def test_cache_hit_keeps_identity_with_zero_new_usage():
    telemetry = Telemetry(sample_rate=0.0)
    gateway = Gateway(_config(cache_enabled=True), telemetry)
    execution = ExecutionContext("run-001", 42, "easy", "policy-v1")

    gateway.handle("tenant-a", "Where is my order", "easy", execution)
    cached = gateway.handle("tenant-a", "Where is my order", "easy", execution)

    assert cached.cache_hit is True
    assert cached.prediction_id == 42
    assert cached.input_tokens == 0
    assert cached.output_tokens == 0


def test_reject_keeps_identity_with_zero_usage():
    telemetry = Telemetry(sample_rate=0.0)
    gateway = Gateway(_config(cache_enabled=False, budget=0.0, action="reject"), telemetry)
    execution = ExecutionContext("run-001", 42, "easy", "policy-v1")

    rejected = gateway.handle("tenant-a", "Where is my order", "easy", execution)

    assert rejected.model == "none"
    assert rejected.prediction_id == 42
    assert rejected.input_tokens == 0
    assert rejected.output_tokens == 0