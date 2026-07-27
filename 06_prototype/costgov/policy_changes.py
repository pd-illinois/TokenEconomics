"""Durable draft policy changes with no Azure publication capability."""

from __future__ import annotations

import copy
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .policy_store import LoadedPolicy, validate_policy

EDITABLE_PATHS = {
    "admission.max_model_cost_per_call_usd",
    "admission.require_pricing_verified",
    "admission.require_infrastructure_estimate",
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


class PolicyChangeStore:
    """File-backed proposals that require an external approval and publish pipeline."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()

    def list(self) -> list[dict]:
        if not self.root.exists():
            return []
        proposals = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in self.root.glob("*.json")
        ]
        return sorted(proposals, key=lambda item: item["created_at"], reverse=True)

    def create(self, payload: dict, loaded: LoadedPolicy) -> dict:
        reason = str(payload.get("reason", "")).strip()
        proposed_version = str(payload.get("proposed_version", "")).strip()
        changes = payload.get("changes")
        if not reason:
            raise ValueError("reason is required")
        if not proposed_version or proposed_version == loaded.document["version"]:
            raise ValueError("a new proposed_version is required")
        if not isinstance(changes, dict) or not changes:
            raise ValueError("at least one policy change is required")
        unsupported = sorted(set(changes) - EDITABLE_PATHS)
        if unsupported:
            raise ValueError(f"unsupported policy controls: {', '.join(unsupported)}")

        proposed = copy.deepcopy(loaded.document)
        proposed["version"] = proposed_version
        diff = []
        for path, value in changes.items():
            current = _get(proposed, path)
            if current == value:
                continue
            _set(proposed, path, value)
            diff.append({"path": path, "current": current, "proposed": value})
        if not diff:
            raise ValueError("proposed values do not change the active policy")
        validate_policy(proposed)

        proposal = {
            "change_id": f"PCR-{uuid4().hex[:10].upper()}",
            "status": "draft",
            "created_at": _now(),
            "reason": reason,
            "base_policy": {
                "policy_id": loaded.document["policy_id"],
                "version": loaded.document["version"],
                "etag": loaded.provenance.get("etag"),
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