"""Durable policy review requests with append-only review events."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .policy_store import LoadedPolicy, validate_policy

_STORE_LOCK = threading.Lock()

EDITABLE_PATHS = {
    "admission.allowed_providers",
    "admission.allowed_models",
    "admission.max_model_cost_per_call_usd",
    "admission.require_pricing_verified",
    "admission.require_infrastructure_estimate",
    "infrastructure_coverage",
    "measurement",
    "execution.routing_mode",
    "execution.budget.per_tenant_usd_per_run",
    "execution.budget.hard_cap_action",
    "execution.evaluation.min_quality",
    "execution.evaluation.min_segment_samples",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get(document: dict, path: str):
    value = document
    for key in path.split("."):
        value = value[key]
    return value


def _set(document: dict, path: str, value) -> None:
    parent = document
    keys = path.split(".")
    for key in keys[:-1]:
        parent = parent[key]
    parent[keys[-1]] = value


def _diff(current: object, proposed: object, prefix: str = "") -> list[dict]:
    if isinstance(current, dict) and isinstance(proposed, dict):
        rows = []
        for key in sorted(set(current) | set(proposed)):
            path = f"{prefix}.{key}" if prefix else key
            rows.extend(_diff(current.get(key), proposed.get(key), path))
        return rows
    if current == proposed:
        return []
    return [{"path": prefix, "current": current, "proposed": proposed}]


def _canonical_hash(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _create_template(loaded: LoadedPolicy) -> dict:
    """Return conservative controls for authoring a replacement from scratch."""
    current = loaded.document
    template = {
        "schema_version": current["schema_version"],
        "policy_id": current["policy_id"],
        "version": current["version"],
        "status": "active",
        "effective_from": _now(),
        "admission": {
            "allowed_providers": list(current["admission"]["allowed_providers"]),
            "allowed_models": list(current["admission"]["allowed_models"]),
            "require_pricing_verified": True,
            "require_infrastructure_estimate": True,
            "max_model_cost_per_call_usd": current["admission"][
                "max_model_cost_per_call_usd"
            ],
        },
        "execution": {
            "routing_mode": "quality",
            "semantic_cache": {"enabled": False, "score_threshold": 1.0},
            "budget": {
                "per_tenant_usd_per_run": current["execution"]["budget"][
                    "per_tenant_usd_per_run"
                ],
                "hard_cap_action": "deny",
            },
            "evaluation": {
                "min_quality": current["execution"]["evaluation"]["min_quality"],
                "min_segment_samples": current["execution"]["evaluation"][
                    "min_segment_samples"
                ],
                "consecutive_breaches": 2,
            },
        },
        "mutation": {"mode": "manual", "allowed_knobs": []},
    }
    template["infrastructure_coverage"] = copy.deepcopy(
        current.get("infrastructure_coverage")
        or {
            "schema_version": "infrastructure-coverage-policy.v1",
            "applicable_routes": [
                "foundry",
                "copilot_studio_byom",
                "foundry_work_iq",
            ],
            "require_confirmed_estimate": True,
            "min_priced_coverage_ratio": 1.0,
            "allow_material_unpriced_items": False,
            "required_price_type": "Consumption",
            "currency": "USD",
            "require_exact_meter_match": True,
            "max_price_evidence_age_days": 30,
            "allowed_regions": ["eastus"],
            "required_safeguards": [
                "managed_identity",
                "least_privilege_rbac",
                "vnet_integration",
                "private_endpoint_subnet",
                "private_dns",
                "restricted_public_access",
                "encryption",
                "centralized_monitoring",
                "zone_redundancy_where_supported",
            ],
            "max_monthly_cost_usd": None,
        }
    )
    return template


class PolicyChangeStore:
    """File-backed proposals that require an external approval and publish pipeline."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._lock = _STORE_LOCK

    def _event_root(self, change_id: str) -> Path:
        return self.root / "events" / change_id

    def _events(self, change_id: str) -> list[dict]:
        root = self._event_root(change_id)
        if not root.exists():
            return []
        return sorted(
            (json.loads(path.read_text(encoding="utf-8")) for path in root.glob("*.json")),
            key=lambda item: item["created_at"],
        )

    def _materialize(self, proposal: dict, active_policy: dict | None = None) -> dict:
        result = copy.deepcopy(proposal)
        events = self._events(result["change_id"])
        result["events"] = events
        if events:
            latest = events[-1]
            result["status"] = latest["status"]
            if latest.get("review"):
                result["review"] = latest["review"]
        if (
            result["status"] in {"draft", "pending"}
            and active_policy
            and _canonical_hash(active_policy)
            == _canonical_hash(result["proposed_policy"])
        ):
            result["status"] = "active"
        return result

    def list(self, *, active_policy: dict | None = None) -> list[dict]:
        if not self.root.exists():
            return []
        proposals = [
            self._materialize(
                json.loads(path.read_text(encoding="utf-8")),
                active_policy,
            )
            for path in self.root.glob("*.json")
        ]
        return sorted(
            (
                proposal
                for proposal in proposals
                if proposal["status"] not in {"deleted", "retired"}
            ),
            key=lambda item: item["created_at"],
            reverse=True,
        )

    def get(self, change_id: str, *, active_policy: dict | None = None) -> dict:
        if not re.fullmatch(r"PCR-[A-F0-9]{10}", change_id):
            raise KeyError(change_id)
        path = self.root / f"{change_id}.json"
        if not path.is_file():
            raise KeyError(change_id)
        return self._materialize(
            json.loads(path.read_text(encoding="utf-8")),
            active_policy,
        )

    def create(self, payload: dict, loaded: LoadedPolicy) -> dict:
        reason = str(payload.get("reason", "")).strip()
        proposed_version = str(payload.get("proposed_version", "")).strip()
        authoring_mode = str(payload.get("authoring_mode", "edit")).strip().lower()
        raw_supersedes_change_id = payload.get("supersedes_change_id")
        supersedes_change_id = (
            ""
            if raw_supersedes_change_id is None
            else str(raw_supersedes_change_id).strip()
        )
        changes = payload.get("changes")
        if authoring_mode not in {"create", "edit"}:
            raise ValueError("authoring_mode must be create or edit")
        if not reason:
            raise ValueError("reason is required")
        if not proposed_version or proposed_version == loaded.document["version"]:
            raise ValueError("a new proposed_version is required")
        if not isinstance(changes, dict) or not changes:
            raise ValueError("at least one policy change is required")
        unsupported = sorted(set(changes) - EDITABLE_PATHS)
        if unsupported:
            raise ValueError(f"unsupported policy controls: {', '.join(unsupported)}")

        if supersedes_change_id:
            try:
                previous = self.get(supersedes_change_id)
            except KeyError as exc:
                raise ValueError("superseded policy draft was not found") from exc
            if previous["status"] != "draft":
                raise ValueError("only a draft change request can be edited")
        proposed = (
            _create_template(loaded)
            if authoring_mode == "create"
            else copy.deepcopy(loaded.document)
        )
        proposed["version"] = proposed_version
        for path, value in changes.items():
            _set(proposed, path, value)
        diff = _diff(loaded.document, proposed)
        meaningful_diff = [
            item
            for item in diff
            if item["path"] not in {"version", "effective_from"}
        ]
        if not meaningful_diff:
            raise ValueError("proposed values do not change the active policy")
        validate_policy(proposed)

        proposal = {
            "change_id": f"PCR-{uuid4().hex[:10].upper()}",
            "status": "draft",
            "created_at": _now(),
            "authoring_mode": authoring_mode,
            "supersedes_change_id": supersedes_change_id or None,
            "reason": reason,
            "base_policy": {
                "policy_id": loaded.document["policy_id"],
                "version": loaded.document["version"],
                "etag": loaded.provenance.get("etag"),
                "content_hash": _canonical_hash(loaded.document),
            },
            "proposed_version": proposed_version,
            "diff": diff,
            "proposed_policy": proposed,
            "publication": {
                "mode": "external_review_required",
                "azure_write_permitted": False,
            },
        }
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{proposal['change_id']}.json"
        temporary = path.with_suffix(".tmp")
        with self._lock:
            temporary.write_text(json.dumps(proposal, indent=2), encoding="utf-8")
            os.replace(temporary, path)
        return proposal

    def record_review(self, change_id: str, review: dict) -> dict:
        with self._lock:
            proposal = self.get(change_id)
            if proposal["status"] != "draft":
                raise ValueError("only a draft change request can be sent for approval")
            event = {
                "event_id": f"policy-review-{uuid4().hex}",
                "change_id": change_id,
                "created_at": _now(),
                "status": "pending",
                "review": review,
            }
            root = self._event_root(change_id)
            root.mkdir(parents=True, exist_ok=True)
            path = root / (
                f"{event['created_at'].replace(':', '-')}-{event['event_id']}.json"
            )
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(event, indent=2), encoding="utf-8")
            os.replace(temporary, path)
        return self.get(change_id)

    def delete_draft(self, change_id: str) -> dict:
        """Hide a draft through an append-only tombstone without erasing evidence."""
        with self._lock:
            proposal = self.get(change_id)
            if proposal["status"] != "draft":
                raise ValueError("only a draft policy can be deleted")
            event = {
                "event_id": f"policy-delete-{uuid4().hex}",
                "change_id": change_id,
                "created_at": _now(),
                "status": "deleted",
            }
            root = self._event_root(change_id)
            root.mkdir(parents=True, exist_ok=True)
            path = root / (
                f"{event['created_at'].replace(':', '-')}-{event['event_id']}.json"
            )
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(event, indent=2), encoding="utf-8")
            os.replace(temporary, path)
        return self.get(change_id)

    def retire_pending(self, change_id: str) -> dict:
        """Hide a stale pending request while preserving its review evidence."""
        with self._lock:
            proposal = self.get(change_id)
            if proposal["status"] != "pending":
                raise ValueError("only a pending policy request can be retired")
            event = {
                "event_id": f"policy-retire-{uuid4().hex}",
                "change_id": change_id,
                "created_at": _now(),
                "status": "retired",
                "reason": "removed_from_active_workspace",
            }
            root = self._event_root(change_id)
            root.mkdir(parents=True, exist_ok=True)
            path = root / (
                f"{event['created_at'].replace(':', '-')}-{event['event_id']}.json"
            )
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(event, indent=2), encoding="utf-8")
            os.replace(temporary, path)
        return self.get(change_id)