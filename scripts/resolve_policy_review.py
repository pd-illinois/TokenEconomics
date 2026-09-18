"""Resolve one merged policy-review manifest for GitHub Actions."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    changed = subprocess.run(
        ["git", "diff", "--name-only", args.before, args.after],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    manifests = [
        Path(path)
        for path in changed
        if path.startswith("data/policy_reviews/") and path.endswith(".json")
    ]
    if len(manifests) != 1:
        raise ValueError(
            f"expected exactly one merged policy review manifest, found {len(manifests)}"
        )
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    required = {"policy_path", "expected_etag", "change_id"}
    missing = sorted(required - manifest.keys())
    if missing:
        raise ValueError("review manifest missing: " + ", ".join(missing))
    policy_path = Path(manifest["policy_path"])
    if policy_path.parent.as_posix() != "data/policies" or policy_path.suffix != ".json":
        raise ValueError("review policy_path must be a JSON child of data/policies")
    with Path(args.output).open("a", encoding="utf-8") as output:
        output.write(f"policy_path={policy_path.as_posix()}\n")
        output.write(f"expected_etag={manifest['expected_etag']}\n")
        output.write(f"change_id={manifest['change_id']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
