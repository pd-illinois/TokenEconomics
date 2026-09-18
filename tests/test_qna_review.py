from copy import deepcopy

import pytest

from costgov.studio_lifecycle import digest
from rag.qna_dataset import content_hash
from rag.qna_capture_adapter import prepare_evaluation
from rag.qna_evaluation import normalize_evaluation
from rag.qna_review import quality_summary, save_quality_review
from rag.qna_acceptance import acceptance_review, record_human_acceptance
from rag.response_capture import CapturedResponses
from test_agent_batch_measurement import measured, proof, response, run
from test_qna_evaluation import inputs, records_for


def saved_inputs(measured, inputs):
    dataset, attempts, _, evaluators = deepcopy(inputs)
    case = dataset["cases"][0]
    capture = CapturedResponses([case], allow_content_evaluation=True)
    batch = run(measured, questions=[case["question"]],
                create=lambda **_: {**response(), "id": "response-1"}, response_observer=capture)
    attempt = attempts[0]
    attempt.update(run_id=batch["run_id"], response_text=capture.rows[0]["response"])
    records = records_for(dataset, attempt, evaluators)
    report = normalize_evaluation(dataset, attempts, records, evaluation_id="eval-1",
                                  evaluation_run_id="eval-run-1", evaluators=evaluators)
    manifest = capture.manifest(batch, dataset_hash=dataset["content_hash"])
    return report, manifest, batch


def test_capture_adapter_does_not_invent_citations_or_ground_truth_context(measured, inputs):
    dataset = inputs[0]
    case = dataset["cases"][0]
    capture = CapturedResponses([case], allow_content_evaluation=True)
    batch = run(measured, questions=[case["question"]],
                create=lambda **_: {**response(), "id": "response-1"}, response_observer=capture)
    prepared = prepare_evaluation(dataset, capture, batch)
    assert prepared["items"][0]["response_id"] == "response-1"
    attempt = prepared["attempts"][0]
    assert attempt["grounding_context"] == []
    assert attempt["citations"] is None and attempt["abstained"] is None
    assert attempt["agent_usage"]["input_tokens"] == batch["metrics"][0]["input_tokens"]
    assert attempt["agent_usage"]["cost_usd"] == batch["allocations"][0]["model_allocation_usd"]


def test_forecast_configuration_guard_precedes_inference(measured, monkeypatch):
    def reject(*args, **kwargs):
        raise ValueError("Response forecast differs from current configuration")

    monkeypatch.setattr("costgov.response_forecasts.validate_response_dispatch", reject)
    calls = []
    batch = run(measured, create=lambda **kwargs: calls.append(kwargs))
    assert not calls
    assert batch["stop_reason"] == "response_forecast_mismatch"


def test_linked_quality_is_immutable_advisory_and_replay_safe(measured, inputs):
    service, receipt, _, _ = measured
    report, manifest, batch = saved_inputs(measured, inputs)
    original = deepcopy(batch)
    record, created = save_quality_review(
        service, receipt["plan_id"], batch["run_id"], report, manifest, actor="operator",
    )
    assert created
    assert save_quality_review(service, receipt["plan_id"], batch["run_id"],
                               report, manifest, actor="operator") == (record, False)
    summary = quality_summary(service, receipt["plan_id"], batch["run_id"])
    assert summary["evaluated_cases"] == 1
    assert summary["human_review_status"] == "pending"
    assert summary["acceptance_status"] == "human_review_pending"
    assert summary["reviewed_cases"] == 0
    assert summary["candidates"][0]["question"] == inputs[0]["cases"][0]["question"]
    assert summary["judge_input_tokens"] == 180
    assert summary["status"] == "advisory_evaluation"
    assert batch == original and batch["acceptance_status"] == "not_evaluated"


def test_human_acceptance_is_append_only_and_drives_review_summary(measured, inputs):
    service, receipt, _, _ = measured
    report, manifest, batch = saved_inputs(measured, inputs)
    save_quality_review(
        service, receipt["plan_id"], batch["run_id"], report, manifest, actor="operator",
        foundry_report_url="https://ai.azure.com/project/evaluation/run",
    )

    record, review = record_human_acceptance(
        service, receipt["plan_id"], batch["run_id"], report["attempts"][0]["case_id"],
        "accepted", "reviewer_confirmed", actor="reviewer-1",
    )

    assert record["kind"] == "qna-acceptance"
    assert record["outcome"]["decision"] == "accepted"
    assert record["outcome"]["reviews"][-1]["method"] == "human_review"
    assert review["status"] == "human_reviewed"
    assert review["accepted"] == 1 and review["acceptance_rate"] == 1
    assert review["report_url"] == "https://ai.azure.com/project/evaluation/run"
    summary = quality_summary(service, receipt["plan_id"], batch["run_id"])
    assert summary["status"] == "advisory_evaluation"
    assert summary["acceptance_status"] == "human_reviewed"
    assert summary["accepted"] == 1

    second, changed = record_human_acceptance(
        service, receipt["plan_id"], batch["run_id"], report["attempts"][0]["case_id"],
        "rejected", "incorrect_answer", actor="reviewer-2",
    )
    assert second["id"] != record["id"]
    assert changed["rejected"] == 1 and changed["accepted"] == 0
    assert len([
        item for item in service.workspace(receipt["plan_id"])["records"]
        if item["kind"] == "qna-acceptance"
    ]) == 2


def test_human_acceptance_rejects_browser_selected_provenance(measured, inputs):
    service, receipt, _, _ = measured
    report, manifest, batch = saved_inputs(measured, inputs)
    save_quality_review(service, receipt["plan_id"], batch["run_id"],
                        report, manifest, actor="operator")
    with pytest.raises(ValueError, match="Reason code"):
        record_human_acceptance(
            service, receipt["plan_id"], batch["run_id"], report["attempts"][0]["case_id"],
            "accepted", "incorrect_answer", actor="reviewer",
        )
    with pytest.raises(KeyError):
        record_human_acceptance(
            service, receipt["plan_id"], batch["run_id"], "unknown-case",
            "inconclusive", "insufficient_evidence", actor="reviewer",
        )


@pytest.mark.parametrize("mutation", ["answer", "case", "metric", "raw_content"])
def test_quality_must_match_original_capture_without_raw_content(measured, inputs, mutation):
    service, receipt, _, _ = measured
    report, manifest, batch = saved_inputs(measured, inputs)
    if mutation == "answer":
        report["attempts"][0]["response_content_hash"] = "b" * 64
        report["content_hash"] = content_hash(report)
    elif mutation == "case":
        manifest["rows"][0]["case_id"] = "different-case"
    elif mutation == "metric":
        manifest["rows"][0]["metric_hash"] = "c" * 64
    else:
        manifest["rows"][0]["raw_answer"] = "Do not persist"
    manifest["content_hash"] = digest({k: v for k, v in manifest.items() if k != "content_hash"})
    with pytest.raises(ValueError):
        save_quality_review(service, receipt["plan_id"], batch["run_id"],
                            report, manifest, actor="operator")
    assert quality_summary(service, receipt["plan_id"], batch["run_id"]) == {"status": "not_evaluated"}
