from __future__ import annotations

import json

import pytest

from costgov.portability_evidence import (
    PortabilityEvidenceStore,
    build_portability_proof,
)


def _proof():
    return build_portability_proof(
        run_id="run-1",
        report_id="report-1",
        workload_id="calendar-count",
        workload_version="v1",
        trajectory_reference={"id": "t1", "content_hash": "a" * 64},
        acceptance_reference={"id": "a1", "content_hash": "b" * 64},
        meter_references=[{"id": "m1", "content_hash": "c" * 64}],
        observed_task_count=1,
        accepted_task_count=1,
        meter_summary=[
            {
                "meter_id": "workiq_operation",
                "native_currency": "workiq_operation",
                "quantity": 1,
                "evidence_status": "measured",
            }
        ],
    )


def test_portability_proof_preserves_boundaries_without_rag_fields():
    proof = _proof()

    assert proof["rag_specific_core_fields_required"] is False
    assert proof["contract_amendments_required"] == []
    assert proof["claims"]["hybrid_meter_conversion_performed"] is False
    assert proof["claims"]["copilot_credits_inferred"] is False


def test_portability_store_is_idempotent_and_integrity_checked(tmp_path):
    store = PortabilityEvidenceStore(tmp_path)
    proof = _proof()

    first, created = store.append(proof)
    second, created_again = store.append(proof)

    assert created is True
    assert created_again is False
    assert first == second
    assert store.list() == [proof]

    path = tmp_path / f"{proof['portability_proof_id']}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["accepted_task_count"] = 0
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        store.list()
