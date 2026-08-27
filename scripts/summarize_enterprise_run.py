from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: summarize_enterprise_run.py <run_json_path>")
        return 2
    path = Path(sys.argv[1])
    data = json.loads(path.read_text(encoding="utf-8"))
    results = data.get("results", [])

    print(f"seed={data.get('seed')}")
    print(f"count={len(results)}")

    distribution = Counter(f"{r['provider']}:{r['model']}" for r in results)
    print("distribution:")
    for key, value in sorted(distribution.items()):
        print(f"{key}={value}")

    print("rows:")
    for r in results:
        print(
            "|".join(
                [
                    r.get("case_id", ""),
                    r.get("provider", ""),
                    r.get("model", ""),
                    r.get("report_id", ""),
                    r.get("plan_id", ""),
                    r.get("receipt_id", ""),
                    str(r.get("prediction_id", "")),
                    str(r.get("analysis_topology", "")),
                    str(r.get("archetype", "")),
                    str(r.get("status", "")),
                    str(r.get("report_plan_count", "")),
                    str(r.get("report_receipt_count", "")),
                ]
            )
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
