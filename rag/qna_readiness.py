"""Bounded retries of read-only readiness probes, never of billable requests."""

from __future__ import annotations

from datetime import datetime, timezone
import time

from costgov.policy_store import PolicyLoadError


class PilotReadinessError(ValueError):
    """A denied readiness gate with content-free diagnostic history."""

    def __init__(self, attempts):
        self.attempts = attempts
        codes = attempts[-1]["blocker_codes"]
        super().__init__("Pilot readiness denied: " + ", ".join(codes))


def authorize_readiness(check, *, record, sleep=time.sleep):
    """Keep the caller's capture alive during at most three inventory GET attempts.

    Only failed inventory access is retryable. Policy failures, configuration
    drift and explicit denials stop immediately, even alongside an access failure.
    The check and record callbacks must never dispatch inference or evaluation.
    """
    attempts = []
    for index in range(3):
        try:
            status = check()
        except PolicyLoadError:
            status = {"ready": False, "blockers": [{"code": "policy_unavailable"}]}
        if (not isinstance(status, dict) or type(status.get("ready")) is not bool
                or not isinstance(status.get("blockers"), list)):
            raise ValueError("Invalid server-owned readiness result")
        codes = sorted({row["code"] for row in status["blockers"]})
        if status["ready"] and codes:
            raise ValueError("Ready result must not contain blockers")
        if not status["ready"] and not codes:
            codes = ["readiness_unavailable"]
        retryable = (
            "agent_read_access_unavailable" in codes
            and set(codes) <= {"agent_read_access_unavailable", "retrieval_mode_unverified"}
        )
        attempt = {
            "schema_version": "qna-readiness-attempt.v1",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "attempt": index + 1, "ready": status["ready"], "blocker_codes": codes,
            "will_retry": retryable and index < 2,
            "billable_request_retried": False,
        }
        record(attempt)
        attempts.append(attempt)
        if status["ready"]:
            return
        if not attempt["will_retry"]:
            raise PilotReadinessError(attempts)
        sleep(index + 1)
