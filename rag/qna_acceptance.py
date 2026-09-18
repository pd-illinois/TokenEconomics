"""Human acceptance for immutable Foundry-graded Q&A responses."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import uuid4

from costgov.acceptance_contracts import (
    ACCEPTANCE_RULE_SCHEMA_VERSION,
    AcceptanceDecision,
    AcceptanceOutcome,
    AcceptanceRule,
    ReviewEvidence,
    ReviewMethod,
    evaluate_acceptance,
)
from costgov.studio_lifecycle import digest
from rag.qna_dataset import load_dataset
from rag.qna_evaluation import EVALUATOR_IDS, validate_evaluation

REVIEW_REASON_CODES = {
    "accepted": {"reviewer_confirmed"},
    "rejected": {"incorrect_answer", "insufficient_grounding", "citation_failure", "unsafe_behavior"},
    "inconclusive": {"insufficient_evidence", "grader_disagreement", "needs_subject_matter_review"},
}


def _quality_record(service, plan_id, run_id):
    records = [
        row for row in service.workspace(plan_id)["records"]
        if row["kind"] == "qna-evaluation" and row.get("run_id") == run_id
    ]
    if not records:
        raise KeyError("Foundry quality evidence not found")
    record = max(records, key=lambda row: (row["created_at"], row["id"]))
    validate_evaluation(record["review"])
    return record


def _acceptance_records(service, plan_id, run_id, quality_record):
    records = []
    for record in service.workspace(plan_id)["records"]:
        if record["kind"] != "qna-acceptance" or record.get("run_id") != run_id:
            continue
        if (record.get("quality_record_id") != quality_record["id"]
                or record.get("quality_record_hash") != quality_record["content_hash"]):
            raise ValueError("Human acceptance is bound to different quality evidence")
        outcome = AcceptanceOutcome.from_dict(record.get("outcome"))
        rule = AcceptanceRule.from_dict(record.get("rule"))
        if (outcome.rule_id != rule.rule_id or outcome.rule_version != rule.version
                or outcome.rule_content_hash != rule.content_hash
                or outcome.task_id != record.get("case_id")
                or outcome.trajectory_id != record.get("trajectory_id")):
            raise ValueError("Human acceptance evidence binding is invalid")
        records.append((record, outcome))
    return records


def _latest_by_case(records):
    latest = {}
    for record, outcome in records:
        current = latest.get(outcome.task_id)
        if current is None or (record["created_at"], record["id"]) > (
                current[0]["created_at"], current[0]["id"]):
            latest[outcome.task_id] = (record, outcome)
    return latest


def _dataset_for(report):
    dataset = load_dataset(verify_sources=False)
    if (report.get("dataset_id") != dataset["dataset_id"]
            or report.get("dataset_revision") != dataset["revision"]
            or report.get("dataset_hash") != dataset["content_hash"]):
        raise ValueError("Quality evidence does not match the configured review dataset")
    return dataset


def _candidate(attempt, case, latest):
    values = [attempt["raw_scores"].get(name) for name in EVALUATOR_IDS]
    complete = all(type(value) in (int, float) for value in values)
    decision = latest[1].decision.value if latest else None
    return {
        "case_id": attempt["case_id"],
        "family_id": attempt["family_id"],
        "segment_id": attempt["segment_id"],
        "partition": attempt["partition"],
        "run_id": attempt["run_id"],
        "provider_response_id": attempt["provider_response_id"],
        "question": case["question"],
        "expected_answer": case["expected_answer"],
        "expected_answer_status": case["expected_answer_status"],
        "rubric": list(case["rubric"]),
        "response_content_hash": attempt["response_content_hash"],
        "scores": dict(attempt["raw_scores"]),
        "automated_recommendation": attempt["rubric_outcome"],
        "automated_evidence_complete": complete,
        "evidence_notes": list(attempt["reasons"]),
        "human_decision": decision,
        "human_reason_code": latest[0].get("reviewer_reason_code") if latest else None,
        "reviewed_at": latest[0]["created_at"] if latest else None,
        "reviewer_id": latest[0]["actor"] if latest else None,
    }


def acceptance_review(service, plan_id, run_id):
    """Return trusted review candidates and the latest human decision per case."""
    from rag.performance_evidence import _read_batch

    receipt = service.receipt(plan_id)
    batch = _read_batch(service, receipt, run_id)
    quality = _quality_record(service, plan_id, run_id)
    report = quality["review"]
    dataset = _dataset_for(report)
    cases = {case["case_id"]: case for case in dataset["cases"]}
    latest = _latest_by_case(_acceptance_records(service, plan_id, run_id, quality))
    candidates = [
        _candidate(attempt, cases[attempt["case_id"]], latest.get(attempt["case_id"]))
        for attempt in report["attempts"]
    ]
    decisions = {value: sum(row["human_decision"] == value for row in candidates)
                 for value in ("accepted", "rejected", "inconclusive")}
    reviewed = sum(row["human_decision"] is not None for row in candidates)
    segments = []
    for segment_id in sorted({row["segment_id"] for row in candidates}):
        rows = [row for row in candidates if row["segment_id"] == segment_id]
        segment_reviewed = sum(row["human_decision"] is not None for row in rows)
        accepted = sum(row["human_decision"] == "accepted" for row in rows)
        segments.append({
            "segment_id": segment_id,
            "evaluated": len(rows),
            "reviewed": segment_reviewed,
            "accepted": accepted,
            "rejected": sum(row["human_decision"] == "rejected" for row in rows),
            "inconclusive": sum(row["human_decision"] == "inconclusive" for row in rows),
            "acceptance_rate": accepted / segment_reviewed if segment_reviewed else None,
        })
    return {
        "schema_version": "rag-qna-human-review.v1",
        "plan_id": plan_id,
        "report_id": receipt["report_id"],
        "receipt_hash": receipt["content_hash"],
        "run_id": run_id,
        "batch_hash": batch["evidence"]["content_hash"],
        "quality_record_id": quality["id"],
        "quality_record_hash": quality["content_hash"],
        "evaluation_id": report["evaluation_id"],
        "evaluation_run_id": report["evaluation_run_id"],
        "report_url": quality.get("foundry_report_url"),
        "expected_cases": report["summary"]["expected_case_count"],
        "evaluated_cases": len(candidates),
        "reviewed_cases": reviewed,
        "pending_cases": len(candidates) - reviewed,
        "accepted": decisions["accepted"],
        "rejected": decisions["rejected"],
        "inconclusive": decisions["inconclusive"],
        "acceptance_rate": decisions["accepted"] / reviewed if reviewed else None,
        "status": "human_reviewed" if candidates and reviewed == len(candidates)
        else "human_review_in_progress" if reviewed else "human_review_pending",
        "segments": segments,
        "candidates": candidates,
        "operational_promotion": False,
    }


def record_human_acceptance(
    service, plan_id, run_id, case_id, decision, reason_code, *, actor,
):
    """Append a human decision using only server-resolved provenance."""
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("An authenticated reviewer is required")
    try:
        selected = AcceptanceDecision(decision)
    except ValueError as exc:
        raise ValueError("Decision must be accepted, rejected, or inconclusive") from exc
    if reason_code not in REVIEW_REASON_CODES[selected.value]:
        raise ValueError("Reason code is not valid for the selected decision")
    review = acceptance_review(service, plan_id, run_id)
    candidate = next((row for row in review["candidates"] if row["case_id"] == case_id), None)
    if candidate is None:
        raise KeyError("Evaluated case not found")
    receipt = service.receipt(plan_id)
    from rag.performance_evidence import _read_batch

    batch = _read_batch(service, receipt, run_id)
    quality = _quality_record(service, plan_id, run_id)
    report = quality["review"]
    minimum = min(report["acceptance_rules"]["minimum_scores"].values()) / 5
    rule = AcceptanceRule(
        schema_version=ACCEPTANCE_RULE_SCHEMA_VERSION,
        rule_id=f"qna-{candidate['segment_id']}-human-review",
        version=report["acceptance_rules"]["revision"],
        segment_id=candidate["segment_id"],
        segment_version=report["segment_revision"],
        evaluator_id="foundry-six-grader-review",
        evaluator_version=report["acceptance_rules"]["revision"],
        evaluator_content_hash=report["acceptance_rules_hash"],
        minimum_score=minimum,
        created_at=quality["created_at"],
    )
    score_values = [candidate["scores"].get(name) for name in EVALUATOR_IDS]
    automated = None
    if all(type(value) in (int, float) for value in score_values):
        automated = ReviewEvidence(
            method=ReviewMethod.AUTOMATED,
            reviewer_id="microsoft-foundry",
            evidence_id=quality["id"],
            evidence_version=report["evaluation_run_id"],
            evidence_content_hash=quality["content_hash"],
            score=min(score_values) / 5,
        )
    human_payload = {
        "case_id": case_id, "run_id": run_id, "quality_record_hash": quality["content_hash"],
        "decision": selected.value, "reason_code": reason_code, "reviewer_id": actor,
    }
    human = ReviewEvidence(
        method=ReviewMethod.HUMAN,
        reviewer_id=actor,
        evidence_id=f"qna-human-review-{uuid4().hex}",
        evidence_version="studio-qna-review.v1",
        evidence_content_hash=digest(human_payload),
        decision=selected,
    )
    policy = batch["policy"]
    outcome = evaluate_acceptance(
        rule,
        experiment_id=report["dataset_id"],
        experiment_revision=report["dataset_revision"],
        arm_id="measured-foundry-response",
        policy_candidate_id=policy["policy_id"],
        policy_candidate_version=policy["version"],
        policy_candidate_content_hash=policy["content_hash"],
        task_id=case_id,
        trajectory_id=f"{run_id}:{candidate['provider_response_id']}",
        segment_id=candidate["segment_id"],
        segment_version=report["segment_revision"],
        automated_review=automated,
        human_review=human,
        evaluated_at=datetime.now(timezone.utc).isoformat(),
    )
    record = service.append(receipt, "qna-acceptance", {
        "actor": actor,
        "run_id": run_id,
        "case_id": case_id,
        "trajectory_id": outcome.trajectory_id,
        "quality_record_id": quality["id"],
        "quality_record_hash": quality["content_hash"],
        "reviewer_reason_code": reason_code,
        "rule": rule.to_dict(),
        "outcome": outcome.to_dict(),
        "operational_promotion": False,
    })
    return record, acceptance_review(service, plan_id, run_id)
