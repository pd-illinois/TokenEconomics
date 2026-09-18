import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from costgov.billing_snapshots import BillingSnapshotStore, normalize_rows
from costgov.response_learning import ResponseLearningStore
from rag.batch_feedback import build_batch_feedback, save_batch_feedback
from test_agent_batch_measurement import verified
from test_performance_review import evidence, persist, reseal, saved


@pytest.fixture
def feedback_context(evidence, tmp_path):
    service, receipt, _ = evidence
    batch = saved(evidence)
    batch["agent"]["retrieval_evidence"] = verified(None)["retrieval_evidence"]
    batch["agent"]["retrieval_mode"] = "managed_mcp_hybrid_verified"
    reseal(batch)
    persist(service, batch)
    config = json.loads((Path(__file__).resolve().parents[1] / "data" / "workload_adapters" /
                         "books-billing.v1.json").read_text(encoding="utf-8"))
    config.update(
        agent_name=batch["agent"]["agent_name"],
        agent_binding={key: batch["agent"][key] for key in config["agent_binding"]},
        retrieval_configuration_hash=batch["agent"]["retrieval_evidence"]["content_hash"],
    )
    return service, receipt, batch, config, BillingSnapshotStore(tmp_path / "billing"), ResponseLearningStore(tmp_path / "learning")


def test_read_is_pure_and_registration_is_independent_from_billing(feedback_context):
    service, receipt, batch, config, billing, learning = feedback_context
    args = service, config, billing, learning, receipt["plan_id"], batch["run_id"]
    review = build_batch_feedback(*args)
    assert review["billing"]["status"] == "awaiting_billing_data"
    assert review["learning"]["eligible_sample_count"] == 0
    assert learning.list() == [] and review["saved_records"] == []
    before_receipt, before_batch = receipt["content_hash"], batch["evidence"]["content_hash"]
    first = save_batch_feedback(*args, actor="operator", refresh_billing=False)
    replay = save_batch_feedback(*args, actor="operator", refresh_billing=False)
    assert first["created"] is True and replay["created"] is False
    assert first["record"] == replay["record"]
    assert len(learning.list()) == 1
    current = build_batch_feedback(*args)
    assert len(current["saved_records"]) == 1
    assert current["learning"]["eligible_sample_count"] == 0
    assert current["learning"]["calibration_applied"] is False
    assert service.receipt(receipt["plan_id"])["content_hash"] == before_receipt
    assert service.registry[batch["run_id"]]["result"]["evidence"]["content_hash"] == before_batch


def test_foreign_agent_resource_mapping_is_rejected(feedback_context):
    service, receipt, batch, config, billing, learning = feedback_context
    config["agent_binding"]["agent_version"] = "other"
    with pytest.raises(ValueError, match="pinned"):
        save_batch_feedback(service, config, billing, learning, receipt["plan_id"], batch["run_id"], actor="operator", refresh_billing=False)
    assert learning.list() == []


def test_modified_batch_is_rejected_before_feedback_writes(feedback_context):
    service, receipt, batch, config, billing, learning = feedback_context
    batch["metrics"][0]["input_tokens"] = 9999
    persist(service, batch)
    with pytest.raises(ValueError):
        save_batch_feedback(service, config, billing, learning, receipt["plan_id"], batch["run_id"], actor="operator", refresh_billing=False)
    assert learning.list() == []


def test_feedback_requires_actual_completed_measurement(evidence, tmp_path):
    service, receipt, _ = evidence
    batch = saved(evidence, status="blocked")
    config = json.loads((Path(__file__).resolve().parents[1] / "data" / "workload_adapters" /
                         "books-billing.v1.json").read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="completed"):
        build_batch_feedback(service, config, BillingSnapshotStore(tmp_path / "billing"),
                             ResponseLearningStore(tmp_path / "learning"), receipt["plan_id"], batch["run_id"])


def test_snapshot_selection_is_export_month_scope_and_delivery_bound(feedback_context):
    service, receipt, batch, config, billing, learning = feedback_context
    resource_ids = [item["resource_id"] for item in config["binding"]["resources"]]

    def add(revision, *, export=None, month="09", delivery="09", retrieval="09", resources=None):
        return billing.append(normalize_rows(
            [], source={"kind": "azure_cost_management_export", "source_id": export or config["export_id"],
                        "revision": revision, "content_hash": "a" * 64,
                        "delivered_at": f"2026-09-{delivery}T10:00:00Z"},
            period_start=f"2026-{month}-01", period_end=f"2026-{month}-09",
            retrieved_at=f"2026-09-{retrieval}T20:00:00Z", allowed_resource_ids=resources or resource_ids,
        ))[0]

    expected = add("correct")
    add("foreign", export="/subscriptions/foreign")
    add("wrong-month", month="08", delivery="10")
    add("wrong-scope", resources=resource_ids[:1], delivery="11")
    add("older-source-imported-later", delivery="08", retrieval="12")
    result = build_batch_feedback(service, config, billing, learning, receipt["plan_id"], batch["run_id"])
    assert result["billing_source"]["snapshot_id"] == expected["snapshot_id"]
    assert result["billing"]["status"] == "awaiting_billing_data"
    assert result["source_predates_batch"] is True


def test_concurrent_registrations_persist_one_observation_and_one_feedback(feedback_context):
    service, receipt, batch, config, billing, learning = feedback_context

    def register(_):
        return save_batch_feedback(service, config, billing, learning,
                                   receipt["plan_id"], batch["run_id"],
                                   actor="operator", refresh_billing=False)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(register, range(4)))
    assert sum(item["created"] for item in results) == 1
    assert len({item["record"]["id"] for item in results}) == 1
    assert len(learning.list()) == 1
    assert len([item for item in service.workspace(receipt["plan_id"])["records"]
                if item["kind"] == "batch-feedback"]) == 1


def test_new_verified_query_updates_review_without_rewriting_previous_feedback(feedback_context):
    from datetime import datetime, timezone
    from costgov.billing_query import sync_query

    service, receipt, batch, config, billing, learning = feedback_context
    config["query"] = {
        "schema_version": "studio-billing-query-source.v1",
        "scope": config["export_id"].split("/providers/")[0],
        "api_version": "2023-11-01", "grouping": "Meter",
    }
    args = service, config, billing, learning, receipt["plan_id"], batch["run_id"]
    original = save_batch_feedback(*args, actor="operator", refresh_billing=False)
    page = {"properties": {
        "columns": [{"name": name} for name in ("Cost", "UsageDate", "ResourceId", "Meter", "Currency")],
        "rows": [[2.5, 20260909, config["binding"]["resources"][0]["resource_id"], "Model input", "USD"]],
        "nextLink": None,
    }}
    source, _ = sync_query(
        config, billing, run_date="2026-09-09",
        read_page=lambda *_: json.dumps(page).encode(),
        now=datetime(2026, 9, 14, tzinfo=timezone.utc),
    )
    updated = save_batch_feedback(*args, actor="operator", refresh_billing=False)
    replay = save_batch_feedback(*args, actor="operator", refresh_billing=False)
    assert updated["created"] and not replay["created"]
    assert updated["review"]["billing_source"]["snapshot_id"] == source["snapshot_id"]
    assert updated["review"]["source_predates_batch"] is False
    assert updated["review"]["full_task_cost_usd"] is None
    assert updated["review"]["billing"]["unallocated_by_currency"] == {"USD": 2.5}
    assert updated["review"]["learning"]["eligible_sample_count"] == 0
    assert len(learning.list()) == 1
    assert service.get(receipt, original["record"]["id"], "batch-feedback") == original["record"]


def test_query_refresh_is_explicit_and_failures_do_not_record_usage(feedback_context, monkeypatch):
    service, receipt, batch, config, billing, learning = feedback_context
    calls = []

    def fail_query(*args, **kwargs):
        calls.append("query")
        raise ValueError("Query access unavailable")

    def never_export(*args, **kwargs):
        raise AssertionError("No export fallback is authorized")

    monkeypatch.setattr("rag.batch_feedback.sync_query", fail_query)
    monkeypatch.setattr("rag.batch_feedback.sync_export", never_export)
    with pytest.raises(ValueError, match="access unavailable"):
        save_batch_feedback(service, config, billing, learning, receipt["plan_id"], batch["run_id"],
                            actor="operator", refresh_billing=True, billing_backend="query")
    assert calls == ["query"]
    assert learning.list() == [] and billing.list() == []
    assert not [item for item in service.workspace(receipt["plan_id"])["records"]
                if item["kind"] == "batch-feedback"]
