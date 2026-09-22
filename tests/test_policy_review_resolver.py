from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.resolve_policy_review import resolve_policy_review

ROOT = Path(__file__).resolve().parents[1]
REVIEW_PATH = "data/policy_reviews/pcr-55409b1613.json"


def test_resolver_uses_exact_merged_review_manifest():
    assert resolve_policy_review([REVIEW_PATH]) == {
        "policy_path": "data/policies/tokengov-te003-live-proof.2026-09-18.campaign.1.json",
        "expected_etag": "XcD-ZEYqOpH5bzP1bwEkKlIBK8A_0sZGN4NcRReKfbc",
        "change_id": "PCR-55409B1613",
        "action": "publish",
    }


def test_resolver_requires_exactly_one_changed_review_manifest():
    with pytest.raises(ValueError, match="exactly one"):
        resolve_policy_review([])
    with pytest.raises(ValueError, match="exactly one"):
        resolve_policy_review([REVIEW_PATH, "data/policy_reviews/pcr-6215c1d3ba.json"])


def test_resolver_rejects_policy_outside_direct_policy_directory(tmp_path: Path):
    repository = tmp_path
    review_dir = repository / "data" / "policy_reviews"
    review_dir.mkdir(parents=True)
    manifest = json.loads((ROOT / REVIEW_PATH).read_text(encoding="utf-8"))
    manifest["policy_path"] = "data/policies/nested/policy.json"
    (review_dir / "review.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="JSON child"):
        resolve_policy_review(
            ["data/policy_reviews/review.json"], repository_root=repository
        )


def test_resolver_rejects_policy_content_not_bound_to_manifest(tmp_path: Path):
    policy_path = (
        ROOT
        / "data"
        / "policies"
        / "tokengov-te003-live-proof.2026-09-18.campaign.1.json"
    )
    manifest = json.loads((ROOT / REVIEW_PATH).read_text(encoding="utf-8"))
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["measurement"]["max_questions"] = 24

    review_dir = tmp_path / "data" / "policy_reviews"
    target_dir = tmp_path / "data" / "policies"
    review_dir.mkdir(parents=True)
    target_dir.mkdir(parents=True)
    (review_dir / "review.json").write_text(json.dumps(manifest), encoding="utf-8")
    (target_dir / policy_path.name).write_text(json.dumps(policy), encoding="utf-8")

    with pytest.raises(ValueError, match="content hash"):
        resolve_policy_review(
            ["data/policy_reviews/review.json"], repository_root=tmp_path
        )


def test_resolver_cli_writes_github_outputs(tmp_path: Path):
    output = tmp_path / "github-output.txt"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/resolve_policy_review.py",
            "--before",
            "9f90069^",
            "--after",
            "9f90069",
            "--github-output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8").splitlines() == [
        "policy_path=data/policies/tokengov-te003-live-proof.2026-09-18.campaign.1.json",
        "expected_etag=XcD-ZEYqOpH5bzP1bwEkKlIBK8A_0sZGN4NcRReKfbc",
        "change_id=PCR-55409B1613",
        "action=publish",
    ]
