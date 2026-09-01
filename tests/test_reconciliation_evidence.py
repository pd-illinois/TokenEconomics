from __future__ import annotations

import csv
import gzip
from pathlib import Path

from costgov.reconciliation_evidence import (
    ReconciliationEvidenceStore,
    load_actual_cost_export,
)


def test_actual_cost_export_keeps_only_explicit_resources(tmp_path: Path) -> None:
    path = tmp_path / "actual.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "ResourceId",
                "CostInBillingCurrency",
                "BillingCurrencyCode",
                "Date",
                "ServiceName",
                "MeterName",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "ResourceId": "/subscriptions/s/resourceGroups/rg/providers/p/r1",
                "CostInBillingCurrency": "1.25",
                "BillingCurrencyCode": "USD",
                "Date": "2026-09-01",
                "ServiceName": "Azure AI services",
                "MeterName": "GPT-4.1 Mini Input",
            }
        )
        writer.writerow(
            {
                "ResourceId": "/subscriptions/s/resourceGroups/other/providers/p/r2",
                "CostInBillingCurrency": "50",
                "BillingCurrencyCode": "USD",
                "Date": "2026-09-01",
                "ServiceName": "Other",
                "MeterName": "Other",
            }
        )
    rows = load_actual_cost_export(
        path,
        source_export_id="export-1",
        allowed_resource_ids=["/subscriptions/s/resourceGroups/rg/providers/p/r1"],
    )
    assert len(rows) == 1
    assert rows[0].cost == 1.25
    assert rows[0].source_row_hash


def test_reconciliation_store_is_persistently_idempotent(tmp_path: Path) -> None:
    evidence = {
        "idempotency_key": "a" * 64,
        "content_hash": "b" * 64,
        "status": "partial",
    }
    store = ReconciliationEvidenceStore(tmp_path)
    first, created = store.append(evidence)
    second, created_again = ReconciliationEvidenceStore(tmp_path).append(evidence)
    assert created is True
    assert created_again is False
    assert first == second
