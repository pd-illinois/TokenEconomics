"""Map captured responses to the pilot without inventing grounding identities."""

from __future__ import annotations

from rag.qna_dataset import validate_dataset

NO_RETRIEVED_CONTEXT = (
    "[No retrieved context was returned by the provider for this response.]"
)


def prepare_evaluation(dataset, capture, batch):
    validate_dataset(dataset)
    manifest = capture.manifest(batch, dataset_hash=dataset["content_hash"])
    items = [{
            "case_id": row["case_id"], "response_id": row["provider_response_id"],
            "query": row["query"], "response": row["response"],
            "context": row["context"] if row["context"].strip() else NO_RETRIEVED_CONTEXT,
        } for row in capture.rows]
    return {"items": items, "attempts": attempts_from_rows(dataset, items, batch, manifest),
            "manifest": manifest}


def attempts_from_rows(dataset, items, batch, manifest):
    validate_dataset(dataset)
    cases = {case["case_id"]: case for case in dataset["cases"]}
    rows = {(row["case_id"], row["response_id"]): row for row in items}
    if (len(rows) != len(items) or len(rows) != batch["questions_count"]
            or len(batch["allocations"]) != batch["questions_count"]):
        raise ValueError("Incomplete or duplicate capture/allocation coverage")
    attempts = []
    for link, metric, allocation in zip(manifest["rows"], batch["metrics"], batch["allocations"]):
        row = rows.get((link["case_id"], link["provider_response_id"]))
        case = cases.get(link["case_id"])
        if row is None or case is None or case["question"] != row["query"]:
            raise ValueError("Captured question does not match the evaluation dataset")
        # A tool's textual output is useful judge input, not proof of source IDs,
        # cited passages or abstention. The strict acceptance adapter keeps these unknown.
        attempts.append({
            **{key: case[key] for key in ("case_id", "family_id", "segment_id", "question")},
            "run_id": batch["run_id"], "provider_response_id": row["response_id"],
            "response_text": row["response"], "grounding_context": [], "citations": None,
            "abstained": None,
            "agent_usage": {
                "input_tokens": metric.get("input_tokens"),
                "output_tokens": metric.get("output_tokens"),
                "cost_usd": allocation.get("model_allocation_usd"),
            },
            "cost_scope": "response_model_only",
        })
    return attempts
