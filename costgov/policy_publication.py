"""Reviewed, conditional publication of authoritative TokenGov policy."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from costgov.policy_store import PolicyLoadError, validate_policy

PUBLICATION_EVIDENCE_SCHEMA_VERSION = "policy-publication-evidence.v1"
ALLOWED_POLICY_ROOT = Path("data/policies")


def canonical_json(value: object) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


def content_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load_target_policy(path: Path, *, repository_root: Path) -> dict[str, Any]:
    resolved_root = repository_root.resolve()
    allowed_root = (resolved_root / ALLOWED_POLICY_ROOT).resolve()
    resolved = path.resolve() if path.is_absolute() else (resolved_root / path).resolve()
    if resolved.parent != allowed_root:
        raise ValueError(f"policy path must be a direct child of {ALLOWED_POLICY_ROOT}")
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to load target policy: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("target policy must be a JSON object")
    return validate_policy(raw)


def build_publication_preview(
    *,
    current_policy: Mapping[str, Any],
    current_etag: str,
    target_policy: Mapping[str, Any],
    expected_etag: str,
    action: str,
) -> dict[str, Any]:
    if action not in {"publish", "rollback"}:
        raise ValueError("action must be publish or rollback")
    if not current_etag or current_etag != expected_etag:
        raise ValueError("authoritative policy ETag does not match the reviewed base ETag")
    current = validate_policy(dict(current_policy))
    target = validate_policy(dict(target_policy))
    if current["policy_id"] != target["policy_id"]:
        raise ValueError("target policy_id must match the authoritative policy_id")
    if current["version"] == target["version"]:
        raise ValueError("target policy version must differ from the authoritative version")

    changed_fields = sorted(
        key
        for key in set(current) | set(target)
        if current.get(key) != target.get(key)
    )
    forbidden = set(changed_fields) - {
        "version",
        "effective_from",
        "admission",
        "execution",
        "mutation",
    }
    if forbidden:
        raise ValueError(
            "target changes immutable policy identity fields: " + ", ".join(sorted(forbidden))
        )
    return {
        "schema_version": PUBLICATION_EVIDENCE_SCHEMA_VERSION,
        "action": action,
        "base": {
            "version": current["version"],
            "etag": current_etag,
            "content_hash": content_hash(current),
        },
        "target": {
            "version": target["version"],
            "content_hash": content_hash(target),
        },
        "changed_fields": changed_fields,
        "mutation_performed": False,
    }


@dataclass(frozen=True)
class PublicationResult:
    evidence: dict[str, Any]


def publish_policy(
    *,
    endpoint: str,
    key: str,
    label: str,
    target_policy: Mapping[str, Any],
    expected_etag: str,
    action: str,
    actor: str,
    run_url: str,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    client: object | None = None,
) -> PublicationResult:
    """Publish once with optimistic concurrency and append-only Azure evidence."""
    if not endpoint or not key or not label:
        raise ValueError("endpoint, key, and versioned label are required")
    if not actor or not run_url:
        raise ValueError("actor and reviewed workflow run URL are required")

    if client is None:
        from azure.appconfiguration import AzureAppConfigurationClient
        from azure.identity import DefaultAzureCredential

        client = AzureAppConfigurationClient(endpoint, DefaultAzureCredential())

    current_setting = client.get_configuration_setting(key=key, label=label)
    try:
        current_policy = json.loads(current_setting.value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise PolicyLoadError("authoritative policy contains invalid JSON") from exc
    preview = build_publication_preview(
        current_policy=current_policy,
        current_etag=str(current_setting.etag),
        target_policy=target_policy,
        expected_etag=expected_etag,
        action=action,
    )

    event_id = f"publication-{uuid4().hex}"
    started_at = now().isoformat()
    intent = {
        **preview,
        "event_id": event_id,
        "status": "intent_recorded",
        "started_at": started_at,
        "actor": actor,
        "workflow_run_url": run_url,
    }
    client.add_configuration_setting(
        key=f"tokengov:publication:{event_id}:intent",
        label=label,
        value=canonical_json(intent),
        content_type="application/json",
    )

    from azure.appconfiguration import ConfigurationSetting
    from azure.core import MatchConditions

    replacement = ConfigurationSetting(
        key=key,
        label=label,
        value=canonical_json(target_policy),
        content_type="application/json",
        etag=current_setting.etag,
    )
    client.set_configuration_setting(
        replacement,
        match_condition=MatchConditions.IfNotModified,
    )
    verified = client.get_configuration_setting(key=key, label=label)
    verified_policy = validate_policy(json.loads(verified.value))
    verified_hash = content_hash(verified_policy)
    if verified_hash != preview["target"]["content_hash"]:
        raise RuntimeError("published policy content hash verification failed")

    evidence = {
        **intent,
        "status": "published" if action == "publish" else "rolled_back",
        "completed_at": now().isoformat(),
        "result": {
            "version": verified_policy["version"],
            "etag": str(verified.etag),
            "content_hash": verified_hash,
            "endpoint": endpoint,
            "key": key,
            "label": label,
        },
        "mutation_performed": True,
    }
    client.add_configuration_setting(
        key=f"tokengov:publication:{event_id}:outcome",
        label=label,
        value=canonical_json(evidence),
        content_type="application/json",
    )
    return PublicationResult(evidence)


def github_workflow_identity() -> tuple[str, str]:
    actor = os.environ.get("GITHUB_ACTOR", "")
    server = os.environ.get("GITHUB_SERVER_URL", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    return actor, f"{server}/{repository}/actions/runs/{run_id}"
