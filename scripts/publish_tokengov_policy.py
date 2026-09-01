from __future__ import annotations

import argparse
import json
from pathlib import Path

from costgov.policy_publication import (
    github_workflow_identity,
    load_target_policy,
    publish_policy,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("policy_path")
    parser.add_argument("--expected-etag", required=True)
    parser.add_argument("--action", choices=("publish", "rollback"), required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, default=Path("publication-evidence.json"))
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    repository_root = Path(__file__).resolve().parents[1]
    target = load_target_policy(Path(args.policy_path), repository_root=repository_root)
    if args.validate_only:
        print(json.dumps({"valid": True, "version": target["version"]}, indent=2))
        return 0

    actor, run_url = github_workflow_identity()
    result = publish_policy(
        endpoint=args.endpoint,
        key=args.key,
        label=args.label,
        target_policy=target,
        expected_etag=args.expected_etag,
        action=args.action,
        actor=actor,
        run_url=run_url,
    )
    args.output.write_text(json.dumps(result.evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result.evidence["result"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
