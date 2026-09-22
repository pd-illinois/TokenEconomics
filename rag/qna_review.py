"""Bind content-free Foundry quality reports to existing immutable Studio runs."""

from __future__ import annotations

from urllib.parse import urlparse

from costgov.studio_lifecycle import digest
from rag.qna_evaluation import validate_evaluation


def _bound(report, batch):
    validate_evaluation(report)
    if not report["attempts"]:
        raise ValueError("A quality review requires actual evaluated attempts")
    identities = set()
    for attempt in report["attempts"]:
        if attempt["run_id"] != batch["run_id"]:
            raise ValueError("Quality evidence belongs to a different run")
        key = attempt["provider_response_id"]
        if key in identities:
            raise ValueError("Quality evidence repeats a provider response")
        identities.add(key)
    return identities


def _validate_capture(report, batch, capture):
    identities = _bound(report, batch)
    if (not isinstance(capture, dict) or capture.get("schema_version") != "rag-response-capture.v1"
            or set(capture) != {"schema_version", "run_id", "batch_hash", "dataset_hash",
                                "content_storage", "rows", "content_hash"}
            or capture["content_storage"] != "memory_only_local"
            or capture.get("content_hash") != digest({k: v for k, v in capture.items() if k != "content_hash"})
            or capture.get("run_id") != batch["run_id"]
            or capture.get("batch_hash") != batch["evidence"]["content_hash"]
            or capture.get("dataset_hash") != report["dataset_hash"]):
        raise ValueError("Quality evidence requires an exact batch/dataset capture binding")
    rows = capture.get("rows", [])
    if (not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)
            or any(not isinstance(row.get("provider_response_id"), str) for row in rows)
            or len(rows) != batch["questions_count"]
            or len(identities) != len(rows)
            or identities != {row.get("provider_response_id") for row in rows}):
        raise ValueError("Quality output does not cover the captured response identities")
    for row, metric in zip(rows, batch["metrics"]):
        if (set(row) != {"case_id", "run_id", "question_number", "provider_response_id",
                         "provider_request_id", "response_hash", "metric_hash", "grounding_status",
                         "question_hash", "response_text_hash"}
                or row["run_id"] != batch["run_id"]
                or type(row["question_number"]) is not int
                or row["metric_hash"] != digest(metric)
                or row["question_number"] != metric["question_number"]):
            raise ValueError("Quality capture is not bound to the original batch metrics")
    by_response = {row["provider_response_id"]: row for row in rows}
    for attempt in report["attempts"]:
        row = by_response[attempt["provider_response_id"]]
        if (row["case_id"] != attempt["case_id"]
                or row["question_hash"] != attempt["question_hash"]
                or row["response_text_hash"] != attempt["response_content_hash"]):
            raise ValueError("Quality case does not match the captured response")


def _report_url(value):
    if value is None:
        return None
    parsed = urlparse(value)
    if (not isinstance(value, str) or parsed.scheme != "https"
            or parsed.hostname not in {"ai.azure.com", "foundry.azure.com"}
            or parsed.username or parsed.password):
        raise ValueError("Foundry report URL is invalid")
    return value


def save_quality_review(
    service, plan_id, run_id, report, capture_manifest, *, actor, foundry_report_url=None,
):
    from rag.performance_evidence import _read_batch

    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("Quality evidence requires an authorized actor")
    receipt = service.receipt(plan_id)
    batch = _read_batch(service, receipt, run_id)
    capture = capture_manifest
    _validate_capture(report, batch, capture)
    report_url = _report_url(foundry_report_url)
    key = digest({"receipt": receipt["content_hash"], "batch": batch["evidence"]["content_hash"],
                  "quality": report["content_hash"], "capture": capture["content_hash"],
                  "foundry_report_url": report_url})
    identity = "qna-evaluation-" + key
    for record in service.workspace(plan_id)["records"]:
        if record["kind"] == "qna-evaluation" and record["id"] == identity:
            return record, False
    try:
        record = service.append(receipt, "qna-evaluation", {
            "id": identity, "actor": actor, "run_id": run_id,
            "batch_hash": batch["evidence"]["content_hash"],
            "review": report, "capture": capture,
            "foundry_report_url": report_url,
            "operational_promotion": False,
        })
    except FileExistsError:
        record = service.get(receipt, identity, "qna-evaluation")
        if (record.get("review") != report or record.get("capture") != capture
                or record.get("foundry_report_url") != report_url):
            raise ValueError("Conflicting immutable quality record")
        return record, False
    return record, True


def quality_summary(service, plan_id, run_id):
    from rag.performance_evidence import _read_batch

    receipt = service.receipt(plan_id)
    batch = _read_batch(service, receipt, run_id)
    records = [row for row in service.workspace(plan_id)["records"]
               if row["kind"] == "qna-evaluation" and row.get("run_id") == run_id]
    if not records:
        return {"status": "not_evaluated"}
    latest = max(records, key=lambda row: (row["created_at"], row["id"]))
    if latest.get("batch_hash") != batch["evidence"]["content_hash"]:
        raise ValueError("Quality review batch binding mismatch")
    report = latest["review"]
    _validate_capture(report, batch, latest["capture"])
    usage = [item["usage"] for item in report["judge_usage_evidence"]]

    def total(key):
        return sum(item[key] for item in usage) if usage and all(
            item[key] is not None for item in usage) else None

    scores = []
    for grader in report["evaluators"]:
        values = [attempt["raw_scores"].get(grader) for attempt in report["attempts"]]
        values = [value for value in values if value is not None]
        scores.append({"evaluator_id": grader, "scored_responses": len(values),
                       "mean_score": sum(values) / len(values) if values else None})
    summary = {
        "status": "advisory_evaluation",
        "reason": "Proposed rubric; human review is pending. Missing grading evidence remains inconclusive.",
        "evaluated_cases": report["summary"]["observed_case_count"],
        "human_review_status": report["human_review_status"],
        "segments": [{"segment_id": row["segment_id"], **row["acceptance_counts"]}
                     for row in report["segments"]],
        "judge_input_tokens": total("input_tokens"), "judge_output_tokens": total("output_tokens"),
        "judge_cost_usd": report["costs"]["judge_total_usd"],
        "eval_id": report["evaluation_id"], "eval_run_id": report["evaluation_run_id"],
        "report_url": _report_url(latest.get("foundry_report_url")),
        "content_hash": report["content_hash"],
        "scores": scores, "expected_cases": report["summary"]["expected_case_count"],
    }
    from rag.qna_acceptance import acceptance_review

    human = acceptance_review(service, plan_id, run_id)
    summary.update({
        key: human[key] for key in (
            "reviewed_cases", "pending_cases", "accepted", "rejected", "inconclusive",
            "acceptance_rate", "segments", "candidates",
        )
    })
    summary["acceptance_status"] = human["status"]
    return summary
