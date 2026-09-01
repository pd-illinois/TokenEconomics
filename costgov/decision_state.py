"""Durable hysteresis state for safe block, reversion, and reviewed recovery."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .governance_decisions import (
    CandidateConstraintEvidence,
    ConstraintOutcome,
)

DECISION_STATE_SCHEMA_VERSION = "evaluation-decision-state.v1"


def _canonical(value: object) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


class DecisionStateStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.events = self.root / "events"
        self.snapshot = self.root / "state.json"

    def get(
        self, candidate_id: str, candidate_version: str, segment_id: str
    ) -> dict:
        state = self._load()
        return state.get(
            self._key(candidate_id, candidate_version, segment_id),
            self._initial(candidate_id, candidate_version, segment_id),
        )

    def record(
        self,
        evidence: CandidateConstraintEvidence,
        *,
        required_breaches: int = 2,
        required_recoveries: int = 2,
        last_verified_policy: dict | None = None,
        recorded_at: str | None = None,
    ) -> list[dict]:
        if required_breaches < 1 or required_recoveries < 1:
            raise ValueError("hysteresis thresholds must be positive")
        state = self._load()
        transitions = []
        for segment in evidence.segments:
            key = self._key(
                evidence.candidate_id,
                evidence.candidate_version,
                segment.segment_id,
            )
            current = state.get(
                key,
                self._initial(
                    evidence.candidate_id,
                    evidence.candidate_version,
                    segment.segment_id,
                ),
            )
            if current.get("latest_constraint_content_hash") == evidence.content_hash:
                continue
            previous = current["status"]
            if segment.outcome is ConstraintOutcome.INCONCLUSIVE:
                current.update(
                    status="admission_blocked",
                    consecutive_breaches=0,
                    consecutive_recoveries=0,
                )
                reason = "insufficient_evidence_blocks_admission"
            elif segment.outcome is ConstraintOutcome.INELIGIBLE:
                breaches = current["consecutive_breaches"] + 1
                current.update(
                    status=(
                        "revert_required"
                        if breaches >= required_breaches
                        else "breach_observed"
                    ),
                    consecutive_breaches=breaches,
                    consecutive_recoveries=0,
                )
                reason = (
                    "consecutive_breach_threshold_reached"
                    if breaches >= required_breaches
                    else "awaiting_consecutive_breach_evidence"
                )
            else:
                recoveries = current["consecutive_recoveries"] + 1
                if previous in {
                    "breach_observed",
                    "revert_required",
                    "reverted",
                    "recovery_observed",
                    "recovery_review_required",
                }:
                    status = (
                        "recovery_review_required"
                        if recoveries >= required_recoveries
                        else "recovery_observed"
                    )
                    reason = (
                        "reviewed_recovery_admission_required"
                        if recoveries >= required_recoveries
                        else "awaiting_consecutive_recovery_evidence"
                    )
                else:
                    status = "eligible"
                    reason = "segment_constraints_satisfied"
                current.update(
                    status=status,
                    consecutive_breaches=0,
                    consecutive_recoveries=recoveries,
                )
            timestamp = recorded_at or datetime.now(timezone.utc).isoformat()
            event = {
                "schema_version": DECISION_STATE_SCHEMA_VERSION,
                "event_id": f"decision-state-{uuid4().hex}",
                "recorded_at": timestamp,
                "candidate_id": evidence.candidate_id,
                "candidate_version": evidence.candidate_version,
                "candidate_content_hash": evidence.candidate_content_hash,
                "segment_id": segment.segment_id,
                "constraint_id": evidence.constraint_id,
                "constraint_content_hash": evidence.content_hash,
                "previous_status": previous,
                "status": current["status"],
                "reason_code": reason,
                "consecutive_breaches": current["consecutive_breaches"],
                "required_breaches": required_breaches,
                "consecutive_recoveries": current["consecutive_recoveries"],
                "required_recoveries": required_recoveries,
                "response_ladder": [
                    "block_candidate_admission",
                    "revert_to_last_verified_eligible_policy",
                    "require_reviewed_recovery_admission",
                ],
                "last_verified_policy": last_verified_policy,
            }
            event["content_hash"] = hashlib.sha256(
                _canonical(event).encode()
            ).hexdigest()
            self._append_event(event)
            current["latest_event_id"] = event["event_id"]
            current["latest_event_content_hash"] = event["content_hash"]
            current["latest_constraint_content_hash"] = evidence.content_hash
            current["updated_at"] = timestamp
            state[key] = current
            transitions.append(event)
        self._write(state)
        return transitions

    def mark_reverted(
        self,
        candidate_id: str,
        candidate_version: str,
        segment_id: str,
        *,
        publication_evidence_id: str,
        publication_evidence_hash: str,
    ) -> dict:
        state = self._load()
        key = self._key(candidate_id, candidate_version, segment_id)
        current = state.get(key)
        if current is None or current["status"] != "revert_required":
            raise ValueError("segment is not awaiting reversion")
        current.update(
            status="reverted",
            publication_evidence_id=publication_evidence_id,
            publication_evidence_hash=publication_evidence_hash,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        state[key] = current
        self._write(state)
        return current

    @staticmethod
    def _key(candidate_id: str, candidate_version: str, segment_id: str) -> str:
        return f"{candidate_id}:{candidate_version}:{segment_id}"

    @staticmethod
    def _initial(
        candidate_id: str, candidate_version: str, segment_id: str
    ) -> dict:
        return {
            "schema_version": DECISION_STATE_SCHEMA_VERSION,
            "candidate_id": candidate_id,
            "candidate_version": candidate_version,
            "segment_id": segment_id,
            "status": "unobserved",
            "consecutive_breaches": 0,
            "consecutive_recoveries": 0,
        }

    def _load(self) -> dict:
        if not self.snapshot.exists():
            return {}
        payload = json.loads(self.snapshot.read_text(encoding="utf-8"))
        content_hash = payload.pop("content_hash", None)
        if hashlib.sha256(_canonical(payload).encode()).hexdigest() != content_hash:
            raise ValueError("decision state integrity check failed")
        return payload["states"]

    def _write(self, states: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": DECISION_STATE_SCHEMA_VERSION,
            "states": states,
        }
        payload["content_hash"] = hashlib.sha256(
            _canonical(payload).encode()
        ).hexdigest()
        temporary = self.root / f".state.{uuid4().hex}.tmp"
        temporary.write_text(
            json.dumps(payload, indent=2, allow_nan=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.snapshot)

    def _append_event(self, event: dict) -> None:
        self.events.mkdir(parents=True, exist_ok=True)
        path = self.events / f"{hashlib.sha256(event['event_id'].encode()).hexdigest()}.json"
        temporary = self.events / f".{path.stem}.{uuid4().hex}.tmp"
        try:
            temporary.write_text(
                json.dumps(event, indent=2, allow_nan=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
