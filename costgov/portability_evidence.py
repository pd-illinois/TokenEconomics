"""Generic, append-only evidence that a second workload reuses core contracts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .atomic_publish import publish_immutable

PORTABILITY_EVIDENCE_SCHEMA_VERSION = "portability-evidence.v1"


def _canonical(value: object) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def build_portability_proof(
    *,
    run_id: str,
    report_id: str,
    workload_id: str,
    workload_version: str,
    trajectory_reference: Mapping[str, str],
    acceptance_reference: Mapping[str, str],
    meter_references: list[Mapping[str, str]],
    observed_task_count: int,
    accepted_task_count: int,
    meter_summary: list[Mapping[str, Any]],
) -> dict[str, Any]:
    if observed_task_count < 1:
        raise ValueError("observed_task_count must be positive")
    if not 0 <= accepted_task_count <= observed_task_count:
        raise ValueError("accepted_task_count is invalid")
    proof = {
        "schema_version": PORTABILITY_EVIDENCE_SCHEMA_VERSION,
        "run_id": run_id,
        "report_id": report_id,
        "workload": {"workload_id": workload_id, "version": workload_version},
        "evidence_classification": "measured_live",
        "trajectory_reference": dict(trajectory_reference),
        "acceptance_reference": dict(acceptance_reference),
        "meter_references": [dict(item) for item in meter_references],
        "observed_task_count": observed_task_count,
        "accepted_task_count": accepted_task_count,
        "meter_summary": [dict(item) for item in meter_summary],
        "contract_amendments_required": [],
        "rag_specific_core_fields_required": False,
        "workload_adapter_location": "scripts/record_workiq_portability_proof.py",
        "authority_boundaries": {
            "runtime": "Microsoft Work IQ",
            "quality_ground_truth": "Microsoft Graph calendarView",
            "commercial": "unavailable",
            "azure_policy": "correlated observation only; no authority over Work IQ",
        },
        "claims": {
            "hybrid_meter_conversion_performed": False,
            "copilot_credits_inferred": False,
            "entitlement_inferred": False,
            "decision_grade_segment_evidence": False,
        },
        "remaining_gap": (
            "One measured portability task proves contract reuse, not sufficient "
            "segment evidence for automated Govern admission."
        ),
    }
    proof["portability_proof_id"] = f"portability-{_hash(proof)[:32]}"
    proof["content_hash"] = _hash(proof)
    return proof


class PortabilityEvidenceStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def append(self, proof: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        payload = dict(proof)
        proof_id = payload.get("portability_proof_id")
        if not isinstance(proof_id, str) or not proof_id:
            raise ValueError("portability_proof_id is required")
        calculated = _hash(
            {key: value for key, value in payload.items() if key != "content_hash"}
        )
        if payload.get("content_hash") != calculated:
            raise ValueError("portability proof content hash is invalid")
        path = self.root / f"{proof_id}.json"
        self.root.mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing != payload:
                raise ValueError("portability proof identity collision")
            return existing, False
        temporary = self.root / f".{proof_id}.{uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, allow_nan=False, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            publish_immutable(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return payload, True

    def list(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        proofs = []
        for path in self.root.glob("portability-*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            calculated = _hash(
                {key: value for key, value in payload.items() if key != "content_hash"}
            )
            if (
                path.stem != payload.get("portability_proof_id")
                or payload.get("content_hash") != calculated
            ):
                raise ValueError("portability evidence integrity check failed")
            proofs.append(payload)
        return sorted(proofs, key=lambda item: item["portability_proof_id"], reverse=True)
