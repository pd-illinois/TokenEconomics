from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from costgov.policy_publication import (
    build_publication_preview,
    content_hash,
    load_target_policy,
    publish_policy,
)

ROOT = Path(__file__).resolve().parents[1]


def test_publisher_entry_point_runs_from_repository_root() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/publish_tokengov_policy.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--expected-etag" in result.stdout


def _policy(version: str = "2026-08-25.2") -> dict:
    return {
        "schema_version": "1.0",
        "policy_id": "tokengov-te003-live-proof",
        "version": version,
        "status": "active",
        "effective_from": "2026-08-25T00:00:00Z",
        "admission": {
            "allowed_providers": ["azure_openai"],
            "allowed_models": ["gpt-5-6-luna"],
            "require_pricing_verified": False,
            "max_model_cost_per_call_usd": 0.25,
            "require_infrastructure_estimate": False,
        },
        "execution": {
            "routing_mode": "balanced",
            "semantic_cache": {"enabled": False, "score_threshold": 0.83},
            "budget": {"per_tenant_usd_per_run": 5.0, "hard_cap_action": "deny"},
            "evaluation": {
                "min_quality": 0.8,
                "min_segment_samples": 60,
                "consecutive_breaches": 2,
            },
        },
        "mutation": {"mode": "manual", "allowed_knobs": []},
    }


def test_preview_requires_exact_etag_and_preserves_policy_identity() -> None:
    target = _policy("2026-08-31.1")
    target["admission"]["allowed_models"] = ["gpt-4-1-mini"]
    preview = build_publication_preview(
        current_policy=_policy(),
        current_etag="etag-1",
        target_policy=target,
        expected_etag="etag-1",
        action="publish",
    )
    assert preview["base"]["etag"] == "etag-1"
    assert preview["target"]["content_hash"] == content_hash(target)
    assert preview["mutation_performed"] is False

    with pytest.raises(ValueError, match="ETag"):
        build_publication_preview(
            current_policy=_policy(),
            current_etag="etag-2",
            target_policy=target,
            expected_etag="etag-1",
            action="publish",
        )

    invalid = deepcopy(target)
    invalid["policy_id"] = "different-policy"
    with pytest.raises(ValueError, match="policy_id"):
        build_publication_preview(
            current_policy=_policy(),
            current_etag="etag-1",
            target_policy=invalid,
            expected_etag="etag-1",
            action="publish",
        )


def test_target_policy_must_be_directly_under_versioned_policy_directory(
    tmp_path: Path,
) -> None:
    policy_dir = tmp_path / "data" / "policies"
    policy_dir.mkdir(parents=True)
    target = policy_dir / "target.json"
    target.write_text(json.dumps(_policy("2026-08-31.1")), encoding="utf-8")
    assert load_target_policy(target, repository_root=tmp_path)["version"] == "2026-08-31.1"

    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_policy("2026-08-31.2")), encoding="utf-8")
    with pytest.raises(ValueError, match="direct child"):
        load_target_policy(outside, repository_root=tmp_path)


def test_publisher_uses_sdk_setting_objects_and_verifies_result() -> None:
    class Client:
        def __init__(self) -> None:
            self.current = SimpleNamespace(
                value=json.dumps(_policy()), etag="etag-1"
            )
            self.evidence = []

        def get_configuration_setting(self, *, key, label):
            return self.current

        def add_configuration_setting(self, setting):
            self.evidence.append(setting)

        def set_configuration_setting(self, setting, *, match_condition):
            self.current = SimpleNamespace(value=setting.value, etag="etag-2")

    client = Client()
    result = publish_policy(
        endpoint="https://example.azconfig.io",
        key="tokengov:policy",
        label="production",
        target_policy=_policy("2026-08-31.1"),
        expected_etag="etag-1",
        action="publish",
        actor="reviewer",
        run_url="https://github.example/actions/runs/1",
        client=client,
    )

    assert result.evidence["result"]["etag"] == "etag-2"
    assert len(client.evidence) == 2
    assert client.evidence[0].key.endswith(":intent")
    assert client.evidence[1].key.endswith(":outcome")
