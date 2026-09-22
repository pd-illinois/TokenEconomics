from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REVIEW_ROOT = PurePosixPath("data/policy_reviews")
POLICY_ROOT = PurePosixPath("data/policies")
REVIEW_FIELDS = {
    "schema_version",
    "change_id",
    "policy_path",
    "expected_etag",
    "base_policy",
    "proposed_version",
    "proposed_content_hash",
    "authoring_mode",
    "created_at",
}


def _canonical(value: object) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


def changed_review_paths(before: str, after: str, *, repository_root: Path) -> list[str]:
    result = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=AM",
            before,
            after,
            "--",
            "data/policy_reviews/*.json",
        ],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]


def resolve_policy_review(
    changed_paths: Iterable[str], *, repository_root: Path = REPOSITORY_ROOT
) -> dict[str, str]:
    review_paths = [
        PurePosixPath(path.replace("\\", "/"))
        for path in changed_paths
        if PurePosixPath(path.replace("\\", "/")).parent == REVIEW_ROOT
        and PurePosixPath(path.replace("\\", "/")).suffix == ".json"
    ]
    if len(review_paths) != 1:
        raise ValueError(
            f"expected exactly one changed policy review manifest, found {len(review_paths)}"
        )

    review_path = review_paths[0]
    manifest = _load_json_object(repository_root / Path(*review_path.parts), "review manifest")
    if set(manifest) != REVIEW_FIELDS:
        raise ValueError("review manifest fields do not match policy-review.v1")
    if manifest.get("schema_version") != "policy-review.v1":
        raise ValueError("review manifest schema_version must be policy-review.v1")

    change_id = manifest.get("change_id")
    if not isinstance(change_id, str) or not re.fullmatch(r"PCR-[A-F0-9]{10}", change_id):
        raise ValueError("review manifest change_id is invalid")
    expected_etag = manifest.get("expected_etag")
    if not isinstance(expected_etag, str) or not expected_etag.strip():
        raise ValueError("review manifest expected_etag must be a non-empty string")

    policy_path_value = manifest.get("policy_path")
    if not isinstance(policy_path_value, str):
        raise ValueError("review manifest policy_path must be a string")
    policy_path = PurePosixPath(policy_path_value)
    if policy_path.parent != POLICY_ROOT or policy_path.suffix != ".json":
        raise ValueError("review manifest policy_path must be a JSON child of data/policies")

    base_policy = manifest.get("base_policy")
    if not isinstance(base_policy, dict) or base_policy.get("etag") != expected_etag:
        raise ValueError("review manifest base policy ETag must match expected_etag")
    proposed_version = manifest.get("proposed_version")
    proposed_hash = manifest.get("proposed_content_hash")
    if not isinstance(proposed_version, str) or not proposed_version.strip():
        raise ValueError("review manifest proposed_version must be a non-empty string")
    if not isinstance(proposed_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", proposed_hash):
        raise ValueError("review manifest proposed_content_hash is invalid")

    policy = _load_json_object(repository_root / Path(*policy_path.parts), "reviewed policy")
    if policy.get("version") != proposed_version:
        raise ValueError("reviewed policy version does not match the review manifest")
    actual_hash = hashlib.sha256(_canonical(policy).encode("utf-8")).hexdigest()
    if actual_hash != proposed_hash:
        raise ValueError("reviewed policy content hash does not match the review manifest")

    return {
        "policy_path": policy_path.as_posix(),
        "expected_etag": expected_etag,
        "change_id": change_id,
        "action": "publish",
    }


def _load_json_object(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to load {description}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{description} must be a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args()

    changed_paths = changed_review_paths(
        args.before, args.after, repository_root=REPOSITORY_ROOT
    )
    outputs = resolve_policy_review(changed_paths)
    with args.github_output.open("a", encoding="utf-8") as output:
        for key, value in outputs.items():
            output.write(f"{key}={value}\n")
    print(json.dumps(outputs, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
