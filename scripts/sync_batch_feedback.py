"""Explicit operator entry point for scheduled or on-demand batch feedback."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "FutureTokenPredictor" / "src"))

import studio


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--sync-billing", action="store_true",
                        help="Refresh the selected billing source; otherwise register usage using imported billing.")
    parser.add_argument("--billing-source", choices=("export", "query"), default="export",
                        help="Explicit billing source for --sync-billing; query requires configured scope read access.")
    args = parser.parse_args()
    if args.billing_source == "query" and not args.sync_billing:
        parser.error("--billing-source query requires --sync-billing")
    result = studio._record_batch_feedback(
        studio._lifecycle_service(), args.plan_id, args.run_id,
        "operator-cli", refresh_billing=args.sync_billing, billing_backend=args.billing_source,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
