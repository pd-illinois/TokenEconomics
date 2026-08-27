from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from costgov.trajectory_contracts import (
    EvidenceField,
    PolicyBinding,
    PredictionBinding,
    SegmentIdentity,
    StepEvidence,
    StepKind,
    StepStatus,
    TaskIdentity,
    TRAJECTORY_SCHEMA_VERSION,
    TrajectoryEnvelope,
    TrajectoryStore,
    WorkloadIdentity,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "data" / "contracts" / "trajectory-envelope.v1.schema.json"


def test_machine_readable_schema_matches_runtime_contract():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schema_version"]["const"] == (
        TRAJECTORY_SCHEMA_VERSION
    )
    assert set(schema["$defs"]["stepEvidence"]["properties"]["kind"]["enum"]) == {
        kind.value for kind in StepKind
    }
    assert set(schema["$defs"]["stepEvidence"]["properties"]["status"]["enum"]) == {
        status.value for status in StepStatus
    }


def _envelope() -> TrajectoryEnvelope:
    workload = WorkloadIdentity(
        workload_id="customer-support",
        version="workload.v1",
    )
    segment = SegmentIdentity(
        segment_id="hard-synthesis",
        version="segment.v1",
        attributes=(
            EvidenceField.from_value("difficulty", "hard"),
            EvidenceField.from_value("tenant_class", "internal"),
        ),
    )
    task = TaskIdentity(
        task_id="task-001",
        report_id="RPT-20260820-TEST",
        workload=workload,
        segment=segment,
        created_at="2026-08-20T20:00:00+00:00",
    )
    prediction = PredictionBinding(
        prediction_id="prediction-001",
        receipt_id="receipt-001",
        schema_version="4.0",
        content_hash="a" * 64,
    )
    policy = PolicyBinding(
        policy_id="tokengov",
        version="policy.v1",
        content_hash="b" * 64,
        source="azure_app_configuration",
        label="production",
        etag="etag-001",
    )
    steps = (
        StepEvidence(
            step_id="step-001",
            sequence=1,
            kind=StepKind.ITERATION,
            status=StepStatus.COMPLETED,
            operation="agent_turn",
            started_at="2026-08-20T20:00:01+00:00",
            ended_at="2026-08-20T20:00:02+00:00",
        ),
        StepEvidence(
            step_id="step-002",
            sequence=2,
            kind=StepKind.RETRIEVAL,
            status=StepStatus.COMPLETED,
            operation="knowledge_lookup",
            started_at="2026-08-20T20:00:02+00:00",
            ended_at="2026-08-20T20:00:03+00:00",
            parent_step_id="step-001",
            evidence=(EvidenceField.from_value("result_count", 5),),
        ),
        StepEvidence(
            step_id="step-003",
            sequence=3,
            kind=StepKind.TOOL,
            status=StepStatus.FAILED,
            operation="external_action",
            started_at="2026-08-20T20:00:03+00:00",
            ended_at="2026-08-20T20:00:04+00:00",
            parent_step_id="step-001",
        ),
        StepEvidence(
            step_id="step-004",
            sequence=4,
            kind=StepKind.RETRY,
            status=StepStatus.COMPLETED,
            operation="retry_external_action",
            started_at="2026-08-20T20:00:04+00:00",
            ended_at="2026-08-20T20:00:05+00:00",
            parent_step_id="step-003",
            attempt=2,
        ),
        StepEvidence(
            step_id="step-005",
            sequence=5,
            kind=StepKind.MODEL,
            status=StepStatus.COMPLETED,
            operation="response_synthesis",
            started_at="2026-08-20T20:00:05+00:00",
            ended_at="2026-08-20T20:00:06+00:00",
            parent_step_id="step-001",
            evidence=(
                EvidenceField.from_value("input_tokens", 1200),
                EvidenceField.from_value("output_tokens", 180),
            ),
        ),
        StepEvidence(
            step_id="step-006",
            sequence=6,
            kind=StepKind.CACHE,
            status=StepStatus.COMPLETED,
            operation="semantic_cache_write",
            started_at="2026-08-20T20:00:06+00:00",
            ended_at="2026-08-20T20:00:07+00:00",
            parent_step_id="step-001",
        ),
    )
    return TrajectoryEnvelope(
        schema_version="trajectory-envelope.v1",
        trajectory_id="trajectory-001",
        run_id="run-001",
        trace_id="0123456789abcdef0123456789abcdef",
        task=task,
        prediction_binding=prediction,
        policy_binding=policy,
        status="completed",
        started_at="2026-08-20T20:00:01+00:00",
        ended_at="2026-08-20T20:00:07+00:00",
        recorded_at="2026-08-20T20:00:08+00:00",
        steps=steps,
    )


def test_trajectory_envelope_round_trips_every_core_step_kind():
    envelope = _envelope()

    reopened = TrajectoryEnvelope.from_dict(
        json.loads(envelope.to_canonical_json())
    )

    assert reopened == envelope
    assert {step.kind for step in reopened.steps} == set(StepKind)
    assert reopened.task.segment.segment_id == "hard-synthesis"
    assert reopened.prediction_binding.receipt_id == "receipt-001"
    assert reopened.policy_binding.etag == "etag-001"


def test_trajectory_store_is_append_only_and_detects_tampering(tmp_path):
    envelope = _envelope()
    store = TrajectoryStore(tmp_path)

    created = store.append(envelope)
    reopened = store.get(envelope.trajectory_id)

    assert reopened == created
    assert reopened is not None
    assert reopened.envelope == envelope
    assert len(reopened.content_hash) == 64
    with pytest.raises(FileExistsError):
        store.append(envelope)

    path = next(tmp_path.glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["envelope"]["status"] = "failed"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        store.get(envelope.trajectory_id)


def test_trajectory_store_resolves_concurrent_duplicate_append_without_loss(
    tmp_path,
):
    envelope = _envelope()
    store = TrajectoryStore(tmp_path)
    barrier = threading.Barrier(2)

    def append_once():
        barrier.wait()
        try:
            return store.append(envelope)
        except FileExistsError:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: append_once(), range(2)))

    assert outcomes.count("duplicate") == 1
    assert sum(outcome != "duplicate" for outcome in outcomes) == 1
    assert store.get(envelope.trajectory_id).envelope == envelope
    assert list(tmp_path.glob("*.tmp")) == []


def test_trajectory_store_hashes_filesystem_unsafe_but_stable_ids(tmp_path):
    envelope = replace(_envelope(), trajectory_id="trajectory:provider:001")
    store = TrajectoryStore(tmp_path)

    created = store.append(envelope)

    assert store.get(envelope.trajectory_id) == created
    assert all(":" not in path.name for path in tmp_path.glob("*.json"))


@pytest.mark.parametrize(
    "replacement, message",
    [
        ({"schema_version": "trajectory-envelope.v2"}, "schema_version"),
        ({"trajectory_id": "../escape"}, "trajectory_id"),
        ({"started_at": "2026-08-20T20:00:01"}, "UTC"),
        ({"ended_at": "2026-08-20T19:59:59+00:00"}, "ended_at"),
    ],
)
def test_trajectory_envelope_rejects_invalid_identity_or_time(
    replacement, message
):
    values = _envelope().to_dict()
    values.update(replacement)

    with pytest.raises(ValueError, match=message):
        TrajectoryEnvelope.from_dict(values)


def test_trajectory_envelope_rejects_duplicate_or_orphaned_steps():
    values = _envelope().to_dict()
    values["steps"][1]["step_id"] = values["steps"][0]["step_id"]
    values["steps"][1]["parent_step_id"] = None
    with pytest.raises(ValueError, match="step_id"):
        TrajectoryEnvelope.from_dict(values)

    values = _envelope().to_dict()
    values["steps"][1]["parent_step_id"] = "missing-step"
    with pytest.raises(ValueError, match="parent"):
        TrajectoryEnvelope.from_dict(values)


def test_policy_binding_requires_fail_closed_authority_provenance():
    values = _envelope().to_dict()
    values["policy_binding"]["etag"] = ""

    with pytest.raises(ValueError, match="etag"):
        TrajectoryEnvelope.from_dict(values)
