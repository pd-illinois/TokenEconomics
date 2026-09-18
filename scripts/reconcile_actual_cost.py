"""Persist immutable complete-task reconciliation against an ActualCost export."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from costgov.reconciliation_evidence import (
    ReconciliationEvidenceStore,
    build_reconciliation_evidence,
    load_actual_cost_export,
)
from costgov.reports import ReportStore


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--actual-cost-file", required=True)
    parser.add_argument("--source-export-id", required=True)
    parser.add_argument("--allowed-resource-id", action="append", required=True)
    parser.add_argument("--required-billing-resource-id", action="append", default=[])
    parser.add_argument("--decision-id", required=True)
    parser.add_argument("--decision-hash", required=True)
    parser.add_argument("--forecast-p50-usd", type=float, required=True)
    parser.add_argument("--forecast-p95-usd", type=float, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    run_root = ROOT / "studio_runs" / args.run_id
    result_path = run_root / "result.json"
    if not result_path.exists():
        raise ValueError(f"completed run result does not exist: {args.run_id}")
    run_result = json.loads(result_path.read_text(encoding="utf-8"))
    if run_result.get("status") != "completed":
        raise ValueError("reconciliation requires a completed run")

    actuals = load_actual_cost_export(
        args.actual_cost_file,
        source_export_id=args.source_export_id,
        allowed_resource_ids=args.allowed_resource_id,
    )
    progress = json.loads(
        (run_root / "live-progress.json").read_text(encoding="utf-8")
    )
    segments = {key.split(":", 1)[0] for key in progress["tasks"]}
    percentiles = {
        segment: {
            "modeled_p50_usd": args.forecast_p50_usd,
            "modeled_p95_usd": args.forecast_p95_usd,
        }
        for segment in segments
    }
    evidence = build_reconciliation_evidence(
        run_result=run_result,
        run_root=run_root,
        budget_usd=0.02,
        forecast_percentiles_by_segment=percentiles,
        billing_actuals=actuals,
        required_billing_resource_ids=args.required_billing_resource_id,
        prediction_reference={
            "id": run_result["prediction_receipt_id"],
            "content_hash": run_result["prediction_receipt_hash"],
        },
        decision_reference={
            "id": args.decision_id,
            "content_hash": args.decision_hash,
        },
    )
    stored, created = ReconciliationEvidenceStore(
        ROOT / "studio_reconciliation_evidence"
    ).append(evidence)
    ReportStore(ROOT / "studio_reports").add_artifact(
        run_result["report_id"],
        "reconciliations",
        {
            "id": stored["reconciliation_id"],
            "content_hash": stored["content_hash"],
            "status": stored["status"],
            "created_at": stored["created_at"],
        },
    )
    print(
        json.dumps(
            {
                "reconciliation_id": stored["reconciliation_id"],
                "content_hash": stored["content_hash"],
                "status": stored["status"],
                "created": created,
                "billing_rows": stored["billing"]["row_count"],
                "billing_total": stored["billing"]["total_cost"],
                "missing_evidence": stored["missing_evidence"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
