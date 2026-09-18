"""Explicit operator orchestration; no inference or upload occurs on import."""

from __future__ import annotations

import json

from rag import agent_batch
from rag.foundry_evaluation_transport import (
    EVALUATORS, RUBRIC_EVALUATORS, _validate_endpoint, download_evaluation,
    poll_evaluation, submission_evaluation_target, submit_evaluation,
)
from rag.qna_capture_adapter import attempts_from_rows, prepare_evaluation
from rag.qna_evaluation import DEFAULT_ACCEPTANCE_RULES, IDENTITY_FIELDS, normalize_evaluation
from rag.qna_experiment import validate_qna_dispatch
from rag.qna_review import save_quality_review
from rag.response_capture import CapturedResponses


def _finish(service, plan_id, batch, dataset, directory, manifest, rows, client, actor):
    polled = poll_evaluation(directory, client=client)
    if polled["status"] != "completed":
        return polled
    recovered = []
    downloaded = download_evaluation(directory, rows, client=client, on_verified_row=recovered.append)
    if not downloaded.get("evaluation_id") or not downloaded.get("evaluation_run_id"):
        return {**downloaded, "quality_recorded": False}
    if rows is None:
        rows = recovered
    if len(rows) != batch["questions_count"]:
        return {**downloaded, "quality_recorded": False, "reason": "captured_content_not_recoverable"}
    attempts = attempts_from_rows(dataset, rows, batch, manifest)
    submission = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    model = ":".join([submission["judge_deployment"], *submission["judge_model"]])
    evaluators = {name: {"revision": revision, "model": model}
                  for name, revision in submission["evaluators"].items()}
    lookup = {(row["case_id"], row["provider_response_id"]): row for row in attempts}
    records = []
    for item in downloaded.get("items", []):
        attempt = lookup.get((item.get("case_id"), item.get("response_id")))
        if attempt is None:
            records.append({"status": "failed"})
            continue
        for judge in item["judges"]:
            records.append({
                **{key: attempt[key] for key in IDENTITY_FIELDS},
                "evaluation_id": downloaded["evaluation_id"],
                "evaluation_run_id": downloaded["evaluation_run_id"],
                "output_item_id": item["output_item_id"],
                "evaluator_id": judge["evaluator_id"],
                "revision": judge["evaluator_version"], "model": model,
                "dataset_hash": dataset["content_hash"],
                "acceptance_rules_revision": DEFAULT_ACCEPTANCE_RULES["revision"],
                "status": "completed" if item["status"] == "completed" else "failed",
                "score": judge["score"], "judge_usage": judge["judge_usage"],
            })
    if downloaded["status"] != "completed":
        records.append({"status": "failed"})
    review = normalize_evaluation(
        dataset, attempts, records, evaluation_id=downloaded["evaluation_id"],
        evaluation_run_id=downloaded["evaluation_run_id"], evaluators=evaluators,
    )
    record, created = save_quality_review(
        service, plan_id, batch["run_id"], review, manifest, actor=actor,
        foundry_report_url=downloaded.get("report_url"),
    )
    return {**downloaded, "quality_recorded": True, "record_id": record["id"], "created": created}


def run_pilot(service, plan_id, dataset, case_ids, *, request_id, loaded, actor, root,
              register, refresh_service, client, authorize, consent=False, runner=agent_batch.execute,
              evaluation_target=None, evaluation_project=None, campaign=None,
              campaign_agent=None):
    if consent is not True:
        raise ValueError("Explicit Foundry content-upload/retention approval is required")
    receipt = service.receipt(plan_id)
    by_id = {case["case_id"]: case for case in dataset["cases"]}
    if any(identity not in by_id for identity in case_ids):
        raise ValueError("Unknown pilot case")
    questions = [by_id[identity]["question"] for identity in case_ids]
    if campaign is None:
        cases = validate_qna_dispatch(receipt, questions, dataset=dataset)
    else:
        from rag.qna_campaign import validate_campaign_dispatch

        cases = validate_campaign_dispatch(
            campaign, receipt, dataset, case_ids, loaded, campaign_agent,
        )
    if not cases:
        raise ValueError("Pilot requires a new prospectively bound Q&A forecast")
    authorize()
    if evaluation_target is not None:
        from rag.qna_evaluation_target import authorize_evaluation_target, validate_evaluation_target

        evaluation_target = validate_evaluation_target(evaluation_target)
        _validate_endpoint(client, evaluation_target["project_endpoint"])
        authorize_evaluation_target(evaluation_project, evaluation_target)
    capture = CapturedResponses(cases, allow_content_evaluation=consent)
    try:
        batch = runner(service, plan_id, {"questions": questions, "request_id": request_id},
                       loaded, actor, root, register, response_observer=capture)
        if batch["execution_status"] != "completed":
            return {"status": "blocked_or_incomplete", "run_id": batch["run_id"],
                    "stop_reason": batch["stop_reason"], "evaluation_submitted": False,
                    "batch": batch}
        directory = service.run_root / batch["run_id"] / "qna_evaluation"
        if not capture.rows:
            return {"status": "existing_run_not_reexecuted", "run_id": batch["run_id"],
                    "reason": "Use resume for a previously submitted evaluation; missing content is not reconstructed.",
                    "batch": batch}
        prepared = prepare_evaluation(dataset, capture, batch)
        manifest = prepared["manifest"]
        agent_batch._write_once(directory / "capture.json", manifest)
        for row in prepared["items"]:
            case = by_id[row["case_id"]]
            row.update(expected_answer=case["expected_answer"], rubric=json.dumps({
                "criteria": case["rubric"], "answer_behavior": case["answer_behavior"],
                "review_status": "proposed_human_review_pending",
            }))
        revisions = {name: dataset["revision"] for name in RUBRIC_EVALUATORS}
        submitted = submit_evaluation(
            directory, prepared["items"], consent=consent, authorize=authorize, client=client,
            rubric_revisions=revisions, thresholds=dict.fromkeys([*EVALUATORS, *revisions], 4),
            evaluation_target=evaluation_target, evaluation_project=evaluation_project,
            judge_deployment=(evaluation_target["judge_deployment"] if evaluation_target
                              else "rag-agent-runtime-gpt-4-1-mini"),
        )
        if submitted["status"] != "submitted":
            return {**submitted, "run_id": batch["run_id"], "batch": batch}
        return {**_finish(refresh_service(), plan_id, batch, dataset, directory, manifest,
                          prepared["items"], client, actor), "run_id": batch["run_id"],
                "batch": batch}
    finally:
        capture.clear()


def resume_pilot(service, plan_id, run_id, dataset, *, client, actor, consent=False,
                 evaluation_target=None):
    if consent is not True:
        raise ValueError("Explicit consent is required to retrieve retained evaluation content")
    from rag.performance_evidence import _read_batch

    receipt = service.receipt(plan_id)
    batch = _read_batch(service, receipt, run_id)
    directory = service.run_root / run_id / "qna_evaluation"
    submission_evaluation_target(directory, evaluation_target)
    manifest = json.loads((directory / "capture.json").read_text(encoding="utf-8"))
    if manifest["dataset_hash"] != dataset["content_hash"]:
        raise ValueError("Resume requires the original dataset")
    return _finish(service, plan_id, batch, dataset, directory, manifest, None, client, actor)
