"""Translate sealed RAG batches into separate billing and response feedback."""

from __future__ import annotations

from datetime import datetime, timezone

from costgov.billing_ingestion import sync_export, validate_source
from costgov.billing_query import matches_query_source, sync_query
from costgov.billing_snapshots import allocate_snapshot
from costgov.response_learning import build_response_observation, summarize_learning
from costgov.studio_lifecycle import digest
from rag.performance_evidence import _observation, _read_batch


def verified_inputs(service, config, plan_id, run_id):
    validate_source(config)
    receipt = service.receipt(plan_id)
    batch = _read_batch(service, receipt, run_id)
    if batch["schema_version"] != "rag-agent-batch.v2" or batch["execution_status"] != "completed":
        raise ValueError("Feedback requires a completed, sealed metrics-only batch")
    if batch["agent"]["agent_name"] != config["agent_name"]:
        raise ValueError("Billing resource binding does not match the deployed workload")
    binding = config.get("agent_binding")
    if (not isinstance(binding, dict) or not binding
            or any(batch["agent"].get(key) != value for key, value in binding.items())
            or not config.get("retrieval_configuration_hash")
            or batch["agent"].get("retrieval_evidence", {}).get("content_hash") != config["retrieval_configuration_hash"]):
        raise ValueError("Billing resource mapping is not pinned to this agent and retrieval configuration")
    return receipt, batch, _observation(service, batch)


def build_batch_feedback(service, config, billing_store, learning_store, plan_id, run_id):
    receipt, batch, usage = verified_inputs(service, config, plan_id, run_id)
    observation = build_response_observation(receipt=receipt, batch=batch, observation=usage)
    run_date = datetime.fromisoformat(batch["started_at"]).astimezone(timezone.utc).date().isoformat()
    candidates = [item for item in billing_store.list()
                  if ((item["source"]["kind"] == "azure_cost_management_export"
                       and item["source"]["source_id"].casefold() == config["export_id"].casefold())
                      or matches_query_source(item, config))
                  and item["period"]["start"][:7] == run_date[:7]
                  and set(item["allowed_resource_ids"]) == {row["resource_id"].lower() for row in config["binding"]["resources"]}]
    snapshot = max(candidates, key=lambda item: (
        datetime.fromisoformat(item["source"].get("delivered_at") or item["retrieved_at"]),
        item["snapshot_id"],
    ), default=None)
    billing = allocate_snapshot(snapshot, binding=config["binding"], run_id=run_id, run_date=run_date) if snapshot else {
        "status": "awaiting_billing_data", "run_date": run_date,
        "reason": "No source billing snapshot has been imported.",
        "totals_by_currency": {}, "allocated_by_currency": {}, "unallocated_by_currency": {},
    }
    records = learning_store.list()
    learning = summarize_learning(records, receipt=receipt, observation=observation)
    learning["forecast_calibration_applied"] = observation["calibration_applied"]
    learning["held_out_evaluations"] = [
        {key: item[key] for key in (
            "evaluation_id", "candidate_id", "target", "status", "held_out_sample_count",
            "before_wape", "after_wape", "content_hash",
        )}
        for item in learning_store.list_evaluations()
        if item["cohort"] == observation["cohort"] and any(
            row["observation_id"] == observation["observation_id"]
            for row in item["held_out_observations"]
        )
    ]
    history = [item for item in service.workspace(plan_id)["records"]
               if item["kind"] == "batch-feedback" and item.get("run_id") == run_id]
    return {
        "schema_version": "studio-batch-feedback-review.v1",
        "classification": "advisory_partial", "plan_id": plan_id, "report_id": receipt["report_id"],
        "receipt_id": receipt["receipt_id"], "receipt_hash": receipt["content_hash"], "run_id": run_id,
        "batch_hash": batch["evidence"]["content_hash"],
        "source_predates_batch": (
            datetime.fromisoformat(snapshot["source"].get("delivered_at") or snapshot["retrieved_at"])
            < datetime.fromisoformat(batch["started_at"])
        ) if snapshot and (snapshot["source"].get("delivered_at")
                           or snapshot["source"]["kind"] == "azure_cost_management_query") else None,
        "billing": billing,
        "billing_source": {
            key: snapshot.get(key) for key in (
                "snapshot_id", "content_hash", "source", "period", "retrieved_at",
                "observed_dates", "totals_by_currency", "row_count",
            )
        } if snapshot else None,
        "binding": {"id": config["binding"]["binding_id"], "revision": config["binding"]["revision"],
                    "content_hash": digest(config["binding"])},
        "usage": {
            key: usage.get(key) for key in (
                "input_tokens", "output_tokens", "input_tokens_mean", "output_tokens_mean",
                "questions_completed", "usage_status", "observed_model_allocation_usd",
            )
        },
        "learning": learning,
        "saved_records": [{"id": item["id"], "created_at": item["created_at"],
                           "content_hash": item["content_hash"], "actor": item["actor"]}
                          for item in reversed(history)],
        "full_task_cost_usd": None, "quality_status": "not_evaluated",
        "operational_promotion": False, "automatic_policy_changes": False,
    }


def save_batch_feedback(service, config, billing_store, learning_store, plan_id, run_id, *,
                        actor, refresh_billing=True, billing_backend="export"):
    receipt, batch, usage = verified_inputs(service, config, plan_id, run_id)
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("An authorized actor is required")
    if billing_backend not in {"export", "query"}:
        raise ValueError("Unsupported billing backend")
    if refresh_billing:
        run_date = datetime.fromisoformat(batch["started_at"]).astimezone(timezone.utc).date().isoformat()
        sync = sync_query if billing_backend == "query" else sync_export
        sync(config, billing_store, run_date=run_date)
    observation = build_response_observation(receipt=receipt, batch=batch, observation=usage)
    learning_store.append(observation)
    review = build_batch_feedback(service, config, billing_store, learning_store, plan_id, run_id)
    snapshot_hash = (review["billing_source"] or {}).get("content_hash")
    key = digest({
        "receipt_hash": receipt["content_hash"], "batch_hash": review["batch_hash"],
        "billing_snapshot_hash": snapshot_hash, "binding_hash": review["binding"]["content_hash"],
        "observation": observation, "learning": review["learning"],
    })
    existing = next((item for item in service.workspace(plan_id)["records"]
                     if item["kind"] == "batch-feedback" and item.get("feedback_key") == key), None)
    if existing:
        return {"review": review, "record": existing, "created": False}
    stored_review = {key: value for key, value in review.items() if key != "saved_records"}
    identity = f"batch-feedback-{key}"
    created = True
    try:
        record = service.append(receipt, "batch-feedback", {
            "id": identity, "actor": actor, "status": "partial", "run_id": run_id, "feedback_key": key,
            "review": stored_review, "operational_promotion": False, "mutation_performed": False,
        })
    except FileExistsError:
        record = service.get(receipt, identity, "batch-feedback")
        if record.get("feedback_key") != key:
            raise ValueError("Existing feedback identity does not match the verified evidence")
        created = False
    return {"review": build_batch_feedback(service, config, billing_store, learning_store, plan_id, run_id),
            "record": record, "created": created}
