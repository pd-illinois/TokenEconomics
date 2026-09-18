from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from rag.qna_dataset import content_hash, load_dataset, text_hash
from rag.qna_evaluation import (
    DEFAULT_ACCEPTANCE_RULES, EVALUATOR_IDS, IDENTITY_FIELDS, normalize_evaluation,
    validate_evaluation,
)


@pytest.fixture
def inputs():
    dataset = load_dataset(verify_sources=False)
    evaluators = {name: {"revision": f"{name}.v1", "model": "judge-deployment-revision"}
                  for name in EVALUATOR_IDS}
    case = dataset["cases"][0]
    attempt = {
        **{key: case[key] for key in ("case_id", "family_id", "segment_id", "question")},
        "run_id": "agent-run-1", "provider_response_id": "response-1",
        "response_text": "Mr. Collins inherits under the entail.",
        "grounding_context": [{"source_id": "pg1342", "text": "Synthetic test grounding",
                               "content_hash": text_hash("Synthetic test grounding")}],
        "citations": ["pg1342"], "abstained": False,
        "agent_usage": {"input_tokens": 100, "output_tokens": 20, "cost_usd": 0.02},
        "cost_scope": "response_model_only",
    }
    records = records_for(dataset, attempt, evaluators)
    return dataset, [attempt], records, evaluators


def records_for(dataset, attempt, evaluators, *, item="output-1"):
    return [{
        **{key: attempt[key] for key in IDENTITY_FIELDS},
        "evaluation_id": "eval-1", "evaluation_run_id": "eval-run-1",
        "output_item_id": item, "evaluator_id": name, **evaluators[name],
        "dataset_hash": dataset["content_hash"],
        "acceptance_rules_revision": DEFAULT_ACCEPTANCE_RULES["revision"],
        "status": "completed", "score": 5,
        "judge_usage": {"input_tokens": 30, "output_tokens": 2, "cost_usd": 0.001},
    } for name in EVALUATOR_IDS]


def normalize(inputs, **kwargs):
    dataset, attempts, records, evaluators = inputs
    return normalize_evaluation(dataset, attempts, records,
                                evaluation_id="eval-1", evaluation_run_id="eval-run-1",
                                evaluators=evaluators, **kwargs)


def result(inputs):
    return normalize(inputs)["attempts"][0]


def test_proposed_passing_rubric_never_becomes_human_acceptance(inputs):
    report = normalize(inputs)
    item = report["attempts"][0]
    assert item["rubric_outcome"] == "accepted"
    assert item["acceptance_outcome"] == "inconclusive"
    assert item["raw_scores"] == dict.fromkeys(EVALUATOR_IDS, 5)
    assert "human_review_pending" in item["reasons"]
    assert report["operational_admission"] is False
    assert report["heldout_scored_proof"] is False
    assert report["source_authentication"] == "not_established_by_content_hash"
    assert report["costs"]["cost_per_accepted_task_usd"] is None
    assert report["content_hash"] == content_hash(report)


@pytest.mark.parametrize("field", ["score", "usage", "cost"])
def test_rehashed_invalid_persisted_scores_and_costs_are_rejected(inputs, field):
    report = normalize(inputs)
    if field == "score":
        report["attempts"][0]["raw_scores"]["groundedness"] = 100
    elif field == "usage":
        report["judge_usage_evidence"][0]["usage"]["input_tokens"] = -1
    else:
        report["costs"]["judge_total_usd"] = 0
    report["content_hash"] = content_hash(report)
    with pytest.raises(ValueError):
        validate_evaluation(report)


def test_explicit_adapter_records_match_schema_not_azure_wire_fields(inputs):
    path = Path(__file__).parents[1] / "data" / "contracts" / "rag-qna-evaluator-record.v1.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    for record in inputs[2]:
        validator.validate(record)
    record = deepcopy(inputs[2][0])
    record["unknown_wire_field"] = "must be mapped explicitly"
    assert list(validator.iter_errors(record))


@pytest.mark.parametrize("field", IDENTITY_FIELDS)
def test_wrong_exact_record_identity_never_passes(inputs, field):
    inputs[2][0][field] = "wrong-identity"
    item = result(inputs)
    assert item["rubric_outcome"] == "inconclusive"
    assert "unmatched_record_identity" in item["reasons"]


@pytest.mark.parametrize("field", [
    "evaluation_id", "evaluation_run_id", "dataset_hash",
    "acceptance_rules_revision", "revision", "model",
])
def test_wrong_grader_provenance_never_passes(inputs, field):
    inputs[2][0][field] = "wrong-revision"
    item = result(inputs)
    assert item["rubric_outcome"] == "inconclusive"
    assert "evaluator_provenance_mismatch" in item["reasons"]


@pytest.mark.parametrize("score", [None, True, 0, 6, -1, "5", float("nan"), float("inf")])
def test_missing_or_invalid_score_never_passes(inputs, score):
    inputs[2][0]["score"] = score
    item = result(inputs)
    assert item["rubric_outcome"] == "inconclusive"
    assert item["raw_scores"]["groundedness"] is None


@pytest.mark.parametrize("contexts", [None, [], {}, "not-context", [{"source_id": []}]])
def test_missing_or_malformed_context_never_passes(inputs, contexts):
    inputs[1][0]["grounding_context"] = contexts
    assert result(inputs)["rubric_outcome"] == "inconclusive"


def test_context_hash_mismatch_never_passes(inputs):
    inputs[1][0]["grounding_context"][0]["text"] += " altered"
    assert "invalid_grounding_context" in result(inputs)["reasons"]


def test_cross_book_requires_both_book_contexts(inputs):
    dataset, attempts, records, evaluators = inputs
    case = next(case for case in dataset["cases"] if case["case_id"] == "qna-x01")
    attempts[0].update({key: case[key] for key in ("case_id", "family_id", "segment_id", "question")})
    records[:] = records_for(dataset, attempts[0], evaluators)
    assert "missing_required_source_context" in result(inputs)["reasons"]
    assert result(inputs)["rubric_outcome"] == "inconclusive"


def test_unmapped_wire_fields_are_inconclusive(inputs):
    inputs[2][0]["unknown_wire_field"] = "not part of adapter contract"
    assert result(inputs)["rubric_outcome"] == "inconclusive"


def test_missing_evaluator_never_passes(inputs):
    inputs[2].pop()
    assert "missing_or_duplicate_evaluator" in result(inputs)["reasons"]


def test_failed_judge_cannot_pass_even_with_five(inputs):
    inputs[2][0]["status"] = "failed"
    assert result(inputs)["rubric_outcome"] == "inconclusive"
    assert "judge_failure" in result(inputs)["reasons"]


def test_rejection_separate_from_raw_score(inputs):
    inputs[2][0]["score"] = 2
    item = result(inputs)
    assert item["rubric_outcome"] == "rejected"
    assert item["acceptance_outcome"] == "rejected"
    assert item["raw_scores"]["groundedness"] == 2


@pytest.mark.parametrize("citations", [[], ["pg84"], ["untrusted-source"]])
def test_citations_must_reference_supplied_grounding(inputs, citations):
    inputs[1][0]["citations"] = citations
    assert result(inputs)["rubric_outcome"] == "rejected"
    assert "citation_check_failed" in result(inputs)["reasons"]


def test_duplicate_evaluator_output_is_inconclusive_and_cost_retained(inputs):
    inputs[2].append(deepcopy(inputs[2][0]))
    item = result(inputs)
    assert item["rubric_outcome"] == "inconclusive"
    assert "duplicate_output_item" in item["reasons"]
    assert normalize(inputs)["costs"]["judge_total_usd"] == pytest.approx(0.007)


def test_multiple_output_items_for_one_response_are_not_silently_combined(inputs):
    inputs[2][0]["output_item_id"] = "different-output"
    assert "inconsistent_output_item_identity" in result(inputs)["reasons"]


def test_reused_output_identity_for_distinct_attempts_fails(inputs):
    dataset, attempts, records, evaluators = inputs
    retry = deepcopy(attempts[0])
    retry["provider_response_id"] = "retry-response"
    attempts.append(retry)
    records.extend(records_for(dataset, retry, evaluators))
    assert all(item["rubric_outcome"] == "inconclusive" for item in normalize(inputs)["attempts"])


def test_retries_count_as_attempts_not_additional_cases(inputs):
    dataset, attempts, records, evaluators = inputs
    retry = deepcopy(attempts[0])
    retry["provider_response_id"] = "retry-response"
    retry["agent_usage"]["cost_usd"] = 0.03
    attempts.append(retry)
    records.extend(records_for(dataset, retry, evaluators, item="output-retry"))
    records[0]["score"] = 1
    report = normalize(inputs)
    segment = next(item for item in report["segments"] if item["segment_id"] == "factual")
    assert segment["expected_case_count"] == 5
    assert segment["observed_case_count"] == 1
    assert segment["attempt_count"] == 2
    assert len(segment["missing_case_ids"]) == 4
    assert segment["rubric_counts"] == {"accepted": 1, "rejected": 1, "inconclusive": 0}
    assert report["costs"]["agent_total_usd"] == pytest.approx(0.05)
    assert report["costs"]["judge_total_usd"] == pytest.approx(0.012)
    assert report["costs"]["cost_per_accepted_task_usd"] is None


def test_empty_evidence_keeps_all_segments_and_unknown_costs(inputs):
    inputs[1].clear()
    inputs[2].clear()
    report = normalize(inputs)
    assert len(report["segments"]) == 5
    assert all(item["expected_case_count"] == 5 for item in report["segments"])
    assert all(item["observed_case_count"] == 0 for item in report["segments"])
    assert report["costs"]["agent_total_usd"] is None
    assert report["costs"]["judge_total_usd"] is None
    assert report["costs"]["known_agent_subtotal_usd"] is None
    assert report["costs"]["known_judge_subtotal_usd"] is None


def test_unknown_usage_is_null_not_zero_and_separate(inputs):
    inputs[1][0]["agent_usage"] = {"input_tokens": True, "output_tokens": -1}
    inputs[2][0]["judge_usage"] = {"cost_usd": None}
    report = normalize(inputs)
    assert report["attempts"][0]["agent_usage"] == {
        "input_tokens": None, "output_tokens": None, "cost_usd": None,
    }
    assert report["costs"]["agent_total_usd"] is None
    assert report["costs"]["judge_total_usd"] is None
    assert report["costs"]["judge_unknown_cost_count"] == 1
    assert report["costs"]["known_judge_subtotal_usd"] == pytest.approx(0.005)


def test_deliberate_abstention_requires_grounding_and_graders(inputs):
    dataset, attempts, records, evaluators = inputs
    case = next(case for case in dataset["cases"] if case["case_id"] == "qna-u01")
    attempts[0].update({key: case[key] for key in ("case_id", "family_id", "segment_id", "question")})
    attempts[0].update(response_text="No email address is given in the novel.",
                       citations=[], abstained=True)
    records[:] = records_for(dataset, attempts[0], evaluators)
    assert result(inputs)["rubric_outcome"] == "accepted"
    assert result(inputs)["acceptance_outcome"] == "inconclusive"
    attempts[0]["abstained"] = False
    assert result(inputs)["rubric_outcome"] == "rejected"
    attempts[0]["abstained"] = True
    attempts[0]["grounding_context"] = []
    assert result(inputs)["rubric_outcome"] == "inconclusive"


def test_answerable_case_cannot_pass_by_abstaining(inputs):
    inputs[1][0]["abstained"] = True
    assert result(inputs)["rubric_outcome"] == "rejected"


@pytest.mark.parametrize("mutation", ["case", "family", "question", "duplicate", "scope"])
def test_invalid_expected_manifest_is_rejected(inputs, mutation):
    attempt = inputs[1][0]
    if mutation == "duplicate":
        inputs[1].append(deepcopy(attempt))
    elif mutation == "scope":
        attempt.pop("cost_scope")
    else:
        attempt[{"case": "case_id", "family": "family_id", "question": "question"}[mutation]] = "wrong"
    with pytest.raises(ValueError):
        normalize(inputs)


def test_cannot_disable_human_review_requirement(inputs):
    rules = deepcopy(DEFAULT_ACCEPTANCE_RULES)
    rules["human_review_required"] = False
    with pytest.raises(ValueError, match="unsafe acceptance"):
        normalize(inputs, acceptance_rules=rules)


def test_evaluator_model_and_revision_must_be_pinned(inputs):
    inputs[3]["groundedness"].pop("revision")
    with pytest.raises(ValueError, match="Pin all six"):
        normalize(inputs)


def test_unmatched_and_malformed_records_invalidate_and_preserve_cost_scope(inputs):
    orphan = deepcopy(inputs[2][0])
    orphan["provider_response_id"] = "foreign-response"
    inputs[2].extend([orphan, None])
    report = normalize(inputs)
    assert report["attempts"][0]["rubric_outcome"] == "inconclusive"
    assert report["costs"]["judge_record_count"] == 8
    assert report["costs"]["judge_total_usd"] is None
    assert report["costs"]["known_judge_subtotal_usd"] == pytest.approx(0.007)
    assert len(report["judge_usage_evidence"]) == 8
    assert report["judge_usage_evidence"][6]["provider_response_id"] == "foreign-response"


def test_normalization_does_not_mutate_evidence_and_is_deterministic(inputs):
    before = deepcopy(inputs)
    first = normalize(inputs)
    assert inputs == before
    assert normalize(inputs) == first
    first["evaluators"]["groundedness"]["model"] = "tampered"
    assert normalize(inputs)["evaluators"] == inputs[3]


def test_persistent_report_is_content_free_and_validates(inputs):
    report = normalize(inputs)
    assert validate_evaluation(report, dataset=inputs[0]) is report
    assert validate_evaluation(report) is report
    serialized = json.dumps(report)
    assert inputs[1][0]["question"] not in serialized
    assert inputs[1][0]["response_text"] not in serialized
    assert inputs[1][0]["grounding_context"][0]["text"] not in serialized
    assert report["attempts"][0]["question_hash"] == text_hash(inputs[1][0]["question"])
    assert report["summary"] == {
        "status": "advisory", "quality_status": "human_review_pending",
        "attempt_count": 1, "expected_case_count": 25, "observed_case_count": 1,
        "missing_case_count": 24, "complete_dataset_coverage": False,
        "acceptance_counts": {"accepted": 0, "rejected": 0, "inconclusive": 1},
        "rubric_counts": {"accepted": 1, "rejected": 0, "inconclusive": 0},
    }


@pytest.mark.parametrize("mutation", [
    "hash", "promotion", "acceptance", "summary", "segment", "question", "rationale",
    "question_hash", "cost",
])
def test_persistent_validator_rejects_corrupt_or_overclaiming_evidence(inputs, mutation):
    report = normalize(inputs)
    if mutation == "hash":
        report["content_hash"] = "0" * 64
    elif mutation == "promotion":
        report["operational_admission"] = True
    elif mutation == "acceptance":
        report["attempts"][0]["acceptance_outcome"] = "accepted"
    elif mutation == "summary":
        report["summary"]["attempt_count"] = 99
    elif mutation == "segment":
        report["segments"].pop()
    elif mutation in ("question", "rationale"):
        report["attempts"][0][mutation] = "Raw source or answer text"
    elif mutation == "question_hash":
        report["attempts"][0]["question_hash"] = "0" * 64
    else:
        report["costs"]["cost_per_accepted_task_usd"] = 0.02
    if mutation != "hash":
        report["content_hash"] = content_hash(report)
    with pytest.raises(ValueError):
        validate_evaluation(report, dataset=inputs[0])
