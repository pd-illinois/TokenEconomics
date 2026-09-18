"""Replay-safe route response with bounded, authority-aware hysteresis."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .atomic_publish import publish_immutable

from .route_capabilities import EnforcementScope, ROUTE_CAPABILITY_PROFILES

ROUTE_RESPONSE_SCHEMA_VERSION = "route-response-transition.v1"
_PROFILES = {profile.route_id: profile for profile in ROUTE_CAPABILITY_PROFILES}


def _canonical(value: object) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _required(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value


def _hash(value: object, field: str) -> str:
    text = _required(value, field)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{field} must be a lowercase SHA-256 hash")
    return text


def _utc(value: object, field: str) -> str:
    text = _required(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be an ISO-8601 UTC timestamp")
    return text


class WindowOutcome(str, Enum):
    FAIL = "sufficient_fail"
    PASS = "sufficient_pass"
    INSUFFICIENT = "insufficient"


@dataclass(frozen=True)
class ConstraintWindow:
    window_id: str
    content_hash: str
    outcome: WindowOutcome
    segment_id: str
    segment_version: str
    target_leg: str
    evaluated_at: str

    def __post_init__(self) -> None:
        for field in ("window_id", "segment_id", "segment_version", "target_leg"):
            _required(getattr(self, field), field)
        _hash(self.content_hash, "window content_hash")
        _utc(self.evaluated_at, "window evaluated_at")
        if not isinstance(self.outcome, WindowOutcome):
            raise ValueError("window outcome is invalid")

    @classmethod
    def from_value(
        cls, value: "ConstraintWindow | Mapping[str, Any]"
    ) -> "ConstraintWindow":
        if isinstance(value, cls):
            return value
        values = dict(value)
        values["outcome"] = WindowOutcome(values.get("outcome"))
        return cls(**values)


_LADDERS: dict[str, dict[str, tuple[str, ...]]] = {
    "included": {
        "subscription": ("reassign_seats", "change_route"),
        "commercial": ("change_route",),
    },
    "cowork": {
        "commercial": (
            "block_future_admission",
            "require_approval",
            "change_task_class",
        ),
    },
    "agent_builder": {
        "subscription": (
            "retain_included_route",
            "reassign_seats",
            "move_to_copilot_studio",
        ),
        "commercial": ("move_to_copilot_studio",),
    },
    "copilot_studio": {
        "commercial": (
            "change_feature_mix",
            "block_new_invocations",
            "enable_approved_overage",
        ),
    },
    "work_iq": {
        "work_iq": ("reduce_api_calls", "change_api_family", "block_future_admission"),
        "commercial": ("block_future_admission",),
    },
    "foundry": {
        "foundry": (
            "block_candidate",
            "stage_reviewed_reversion",
            "recover_after_review",
        ),
    },
    "github_copilot": {
        "github_copilot": (
            "adjust_model_policy",
            "adjust_budget",
            "block_additional_usage",
        ),
        "subscription": ("adjust_budget", "block_additional_usage"),
        "commercial": ("adjust_budget", "block_additional_usage"),
    },
    "copilot_studio_byom": {
        "commercial": (
            "respond_to_commercial_leg",
            "block_task",
        ),
        "foundry": (
            "respond_to_foundry_leg",
            "block_task",
        ),
    },
    "foundry_work_iq": {
        "foundry": (
            "respond_to_foundry_leg",
            "block_task",
        ),
        "work_iq": ("reduce_work_iq_calls", "block_task"),
        "commercial": ("block_task",),
    },
}


def response_ladder_for(route_id: str, target_leg: str) -> tuple[str, ...]:
    try:
        profile = _PROFILES[route_id]
        ladder = _LADDERS[route_id][target_leg]
    except KeyError as exc:
        raise ValueError(
            f"unsupported response route or target leg: {route_id}/{target_leg}"
        ) from exc
    declared_legs = {control.target_leg for control in profile.controls}
    if target_leg not in declared_legs:
        raise ValueError("response target leg is not declared by the capability profile")
    if not set(ladder).issubset(profile.response_actions):
        raise RuntimeError("response ladder is not declared by the capability profile")
    return ladder


def _authority_mode(route_id: str, target_leg: str) -> tuple[str, tuple[str, ...]]:
    profile = _PROFILES[route_id]
    controls = tuple(
        control for control in profile.controls if control.target_leg == target_leg
    )
    authorities = tuple(sorted({control.authority for control in controls}))
    if any(control.authority == "azure_tokengov" for control in controls):
        return "azure_reviewed_publication", authorities
    if any(
        control.enforcement_scope
        in {
            EnforcementScope.PRODUCT_ADMIN_ENFORCED,
            EnforcementScope.EXTERNAL_REQUIREMENT,
            EnforcementScope.DESIGN_TIME_ENFORCED,
            EnforcementScope.RUNTIME_ENFORCED,
            EnforcementScope.CONTROL_PLANE_ENFORCED,
        }
        for control in controls
    ):
        return "external_admin_or_application", authorities
    return "advisory", authorities


class RouteResponseStore:
    """Persist immutable transitions and a hash-checked derived state snapshot."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.events = self.root / "events"
        self.snapshot = self.root / "state.json"

    @staticmethod
    def _state_key(
        route_id: str,
        candidate_id: str,
        candidate_version: str,
        segment_id: str,
        segment_version: str,
        target_leg: str,
    ) -> str:
        return ":".join(
            (
                route_id,
                candidate_id,
                candidate_version,
                segment_id,
                segment_version,
                target_leg,
            )
        )

    def _load(self) -> dict[str, Any]:
        if not self.snapshot.exists():
            return {"states": {}, "window_events": {}}
        payload = json.loads(self.snapshot.read_text(encoding="utf-8"))
        expected = payload.pop("content_hash", None)
        if expected != _digest(payload):
            raise ValueError("route response state integrity check failed")
        return payload

    def _write(self, state: Mapping[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = dict(state)
        payload["content_hash"] = _digest(payload)
        temporary = self.root / f".state.{uuid4().hex}.tmp"
        temporary.write_text(
            json.dumps(payload, indent=2, allow_nan=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.snapshot)

    def _append(self, event: Mapping[str, Any]) -> None:
        self.events.mkdir(parents=True, exist_ok=True)
        path = self.events / f"{event['transition_id']}.json"
        temporary = self.events / f".{event['transition_id']}.{uuid4().hex}.tmp"
        try:
            temporary.write_text(
                json.dumps(event, indent=2, allow_nan=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            publish_immutable(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def record(
        self,
        *,
        route_id: str,
        candidate_id: str,
        candidate_version: str,
        candidate_content_hash: str,
        candidate_is_active: bool,
        current_publication_reference: Mapping[str, str] | None,
        window: ConstraintWindow | Mapping[str, Any],
        recorded_at: str,
    ) -> tuple[dict[str, Any], bool]:
        window = ConstraintWindow.from_value(window)
        _required(candidate_id, "candidate_id")
        _required(candidate_version, "candidate_version")
        _hash(candidate_content_hash, "candidate_content_hash")
        _utc(recorded_at, "recorded_at")
        ladder = response_ladder_for(route_id, window.target_leg)
        mode, authorities = _authority_mode(route_id, window.target_leg)
        if candidate_is_active and mode == "azure_reviewed_publication":
            if current_publication_reference is None:
                raise ValueError("active Azure policy requires exact publication provenance")
            for field in ("policy_version", "etag", "content_hash"):
                _required(current_publication_reference.get(field), field)
            _hash(current_publication_reference["content_hash"], "publication content_hash")

        state = self._load()
        replay_key = _digest(
            {
                "route_id": route_id,
                "candidate_id": candidate_id,
                "candidate_version": candidate_version,
                "segment_id": window.segment_id,
                "segment_version": window.segment_version,
                "target_leg": window.target_leg,
                "window_content_hash": window.content_hash,
            }
        )
        prior_transition = state["window_events"].get(replay_key)
        if prior_transition:
            path = self.events / f"{prior_transition}.json"
            existing = json.loads(path.read_text(encoding="utf-8"))
            if (
                existing.get("transition_id") != prior_transition
                or existing.get("content_hash")
                != _digest(
                    {
                        key: value
                        for key, value in existing.items()
                        if key != "content_hash"
                    }
                )
            ):
                raise ValueError("route response event integrity check failed")
            return existing, False

        key = self._state_key(
            route_id,
            candidate_id,
            candidate_version,
            window.segment_id,
            window.segment_version,
            window.target_leg,
        )
        current = state["states"].get(
            key,
            {
                "status": "unobserved",
                "consecutive_failures": 0,
                "consecutive_recoveries": 0,
                "candidate_content_hash": candidate_content_hash,
            },
        )
        if current.get("candidate_content_hash") != candidate_content_hash:
            raise ValueError("candidate version cannot change content hash")
        previous = current["status"]
        recovery_origin = current.get("recovery_origin_status")
        publisher_handoff = None
        if window.outcome is WindowOutcome.INSUFFICIENT:
            status = "inconclusive"
            failures = 0
            recoveries = 0
            reason = "insufficient_window_does_not_count"
        elif window.outcome is WindowOutcome.FAIL:
            failures = min(2, current["consecutive_failures"] + 1)
            recoveries = 0
            if failures < 2:
                status = "breach_observed"
                reason = "awaiting_second_distinct_sufficient_failure"
            elif not candidate_is_active:
                status = "candidate_blocked"
                reason = "inactive_candidate_blocked_not_reverted"
                recovery_origin = status
            elif mode == "azure_reviewed_publication":
                status = "reviewed_reversion_required"
                reason = "active_azure_policy_requires_reviewed_reversion"
                recovery_origin = status
                publisher_handoff = {
                    "handoff_state": "proposed",
                    "requires_review": True,
                    "mutation_performed": False,
                    "least_privilege_publisher_required": True,
                    "current_publication_reference": dict(
                        current_publication_reference or {}
                    ),
                    "candidate_content_hash": candidate_content_hash,
                    "window_content_hashes": [
                        current.get("last_failure_window_hash"),
                        window.content_hash,
                    ],
                }
            elif mode == "external_admin_or_application":
                status = "product_admin_action_required"
                reason = "external_authority_action_required_no_azure_publication"
                recovery_origin = status
            else:
                status = "advisory_recommendation"
                reason = "advisory_action_only_no_publication_authority"
                recovery_origin = status
        else:
            failures = 0
            if recovery_origin or previous in {
                "breach_observed",
                "candidate_blocked",
                "reviewed_reversion_required",
                "product_admin_action_required",
                "advisory_recommendation",
                "recovery_observed",
                "recovery_review_required",
                "recovery_ready",
            }:
                recoveries = min(2, current["consecutive_recoveries"] + 1)
                if recoveries < 2:
                    status = "recovery_observed"
                    reason = "awaiting_second_distinct_sufficient_recovery"
                elif mode == "azure_reviewed_publication" and candidate_is_active:
                    status = "recovery_review_required"
                    reason = "recovery_requires_reviewed_publication"
                    publisher_handoff = {
                        "handoff_state": "proposed",
                        "requires_review": True,
                        "mutation_performed": False,
                        "least_privilege_publisher_required": True,
                        "current_publication_reference": dict(
                            current_publication_reference or {}
                        ),
                        "candidate_content_hash": candidate_content_hash,
                        "window_content_hashes": [
                            current.get("last_recovery_window_hash"),
                            window.content_hash,
                        ],
                    }
                elif mode == "azure_reviewed_publication":
                    status = "recovery_review_required"
                    reason = "inactive_candidate_recovery_requires_admission_review"
                else:
                    status = "recovery_ready"
                    reason = "two_sufficient_recovery_windows_observed"
            else:
                recoveries = min(2, current["consecutive_recoveries"] + 1)
                status = "eligible"
                reason = "sufficient_window_passed"

        event_without_hash = {
            "schema_version": ROUTE_RESPONSE_SCHEMA_VERSION,
            "recorded_at": recorded_at,
            "route_id": route_id,
            "capability_profile": {
                "version": _PROFILES[route_id].version,
                "content_hash": _PROFILES[route_id].content_hash,
            },
            "candidate_id": candidate_id,
            "candidate_version": candidate_version,
            "candidate_content_hash": candidate_content_hash,
            "candidate_is_active": candidate_is_active,
            "segment_id": window.segment_id,
            "segment_version": window.segment_version,
            "target_leg": window.target_leg,
            "window": {
                "id": window.window_id,
                "content_hash": window.content_hash,
                "outcome": window.outcome.value,
                "evaluated_at": window.evaluated_at,
            },
            "previous_status": previous,
            "status": status,
            "reason_code": reason,
            "consecutive_failures": failures,
            "required_failures": 2,
            "consecutive_recoveries": recoveries,
            "required_recoveries": 2,
            "authority_mode": mode,
            "authorities": list(authorities),
            "response_ladder": list(ladder),
            "publisher_handoff": publisher_handoff,
            "azure_mutation_performed": False,
            "historical_evidence_mutated": False,
        }
        transition_id = f"response-{_digest(event_without_hash)[:32]}"
        event = {
            **event_without_hash,
            "transition_id": transition_id,
        }
        event["content_hash"] = _digest(event)
        self._append(event)
        updated = {
            "status": status,
            "consecutive_failures": failures,
            "consecutive_recoveries": recoveries,
            "candidate_content_hash": candidate_content_hash,
            "latest_transition_id": transition_id,
        }
        if recovery_origin:
            updated["recovery_origin_status"] = recovery_origin
        if status == "eligible":
            updated.pop("recovery_origin_status", None)
        if window.outcome is WindowOutcome.FAIL:
            updated["last_failure_window_hash"] = window.content_hash
        elif current.get("last_failure_window_hash"):
            updated["last_failure_window_hash"] = current["last_failure_window_hash"]
        if window.outcome is WindowOutcome.PASS:
            updated["last_recovery_window_hash"] = window.content_hash
        elif current.get("last_recovery_window_hash"):
            updated["last_recovery_window_hash"] = current["last_recovery_window_hash"]
        state["states"][key] = updated
        state["window_events"][replay_key] = transition_id
        self._write(state)
        return event, True

    def get_state(
        self,
        *,
        route_id: str,
        candidate_id: str,
        candidate_version: str,
        segment_id: str,
        segment_version: str,
        target_leg: str,
    ) -> dict[str, Any]:
        state = self._load()
        return dict(
            state["states"].get(
                self._state_key(
                    route_id,
                    candidate_id,
                    candidate_version,
                    segment_id,
                    segment_version,
                    target_leg,
                ),
                {
                    "status": "unobserved",
                    "consecutive_failures": 0,
                    "consecutive_recoveries": 0,
                },
            )
        )
