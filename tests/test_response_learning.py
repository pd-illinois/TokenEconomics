from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from costgov.performance_review import content_hash
from costgov.response_learning import (
    ResponseLearningStore, build_response_observation, response_configuration,
    summarize_learning, validate_response_observation,
)
from rag.performance_evidence import _observation
from test_performance_review import evidence, reseal, saved  # noqa: F401


def make(evidence, *, compatible=False, **kwargs):
    service, receipt, _ = evidence
    receipt = copy.deepcopy(receipt)
    batch = saved(evidence, **kwargs)
    if compatible:
        receipt["response_prediction_contract"] = {
            "schema_version": "response-prediction-contract.v1",
            "measurement_scope": "provider_response",
            "aggregation": "mean_per_completed_response",
            "targets": ["input_tokens_mean", "output_tokens_mean"],
            "prediction_id": receipt["prediction"]["prediction_id"],
            "provider": "azure_openai", "model": batch["agent"]["model"],
            "model_version": batch["agent"]["model_version"],
            "configuration_hash": content_hash(response_configuration(batch)),
            "predicted_input_tokens_mean": 90, "predicted_output_tokens_mean": 25,
        }
        receipt["content_hash"] = content_hash({k: v for k, v in receipt.items() if k not in ("content_hash", "receipt_id")})
        batch["prediction"] = {"receipt_id": receipt["receipt_id"], "content_hash": receipt["content_hash"]}
        reseal(batch)
    return receipt, batch, _observation(service, batch)


def build(args):
    return build_response_observation(receipt=args[0], batch=args[1], observation=args[2])


def test_current_real_shape_is_registered_not_a_calibration_sample(evidence, tmp_path):
    args = make(evidence)
    value = build(args)
    assert value["schema_version"] == "response-usage-observation.v1"
    assert value["eligibility"] == "scope_compatibility_pending"
    assert value["observed"]["input_tokens_mean"] == 100
    assert value["observed"]["output_tokens_mean"] == 30
    assert value["predicted"]["input_tokens_mean"] == 3760
    assert value["configuration"]["measurement"]["max_output_tokens"] == 1024
    assert "PRIVATE QUESTION" not in json.dumps(value)
    store = ResponseLearningStore(tmp_path / "responses")
    assert store.append(value) == (value, True)
    assert store.append(value) == (value, False)
    assert store.get(value["observation_id"]) == value
    assert store.list() == [value]
    summary = summarize_learning(store.list(), receipt=args[0], observation=value)
    assert summary["observation_count"] == 1
    assert summary["eligible_sample_count"] == 0
    assert summary["minimum_samples"] == 10
    assert summary["status"] == "scope_compatibility_pending"
    assert summary["calibration_applied"] is False
    assert summary["before_wape"] is summary["after_wape"] is None


def test_exact_response_contract_eligible_but_not_applied(evidence):
    args = make(evidence, compatible=True)
    value = build(args)
    assert value["eligibility"] == "eligible"
    assert value["predicted"]["output_tokens_mean"] == 25
    summary = summarize_learning([value], receipt=args[0], observation=value)
    assert summary["eligible_sample_count"] == 1
    assert summary["status"] == "insufficient_samples"
    assert summary["calibration_applied"] is False


@pytest.mark.parametrize("status", ["partial", "blocked"])
def test_partial_and_blocked_are_not_zero_actuals(evidence, status):
    value = build(make(evidence, status=status))
    assert value["eligibility"] == "usage_unavailable"
    assert value["observed"]["input_tokens_mean"] is None
    assert value["observed"]["output_tokens_mean"] is None


@pytest.mark.parametrize("invalid", [True, -1, float("nan"), float("inf"), "100"])
def test_invalid_observed_numbers_rejected(evidence, invalid):
    args = make(evidence)
    args[2]["input_tokens_mean"] = invalid
    with pytest.raises(ValueError):
        build(args)


@pytest.mark.parametrize("field", ["run_id", "evidence_hash", "questions_completed", "input_tokens_mean"])
def test_normalized_observation_must_match_sealed_source(evidence, field):
    args = make(evidence)
    args[2][field] = "foreign" if field.endswith("id") or field.endswith("hash") else 99
    with pytest.raises(ValueError):
        build(args)


@pytest.mark.parametrize("field", ["model", "model_version", "configuration_hash", "prediction_id", "aggregation"])
def test_false_compatibility_contract_rejected(evidence, field):
    args = make(evidence, compatible=True)
    args[0]["response_prediction_contract"][field] = "foreign"
    with pytest.raises(ValueError):
        build(args)


def test_model_mismatch_and_corrupt_batch_rejected(evidence):
    args = make(evidence)
    args[1]["agent"]["model"] = "wrong"
    reseal(args[1])
    args[2]["evidence_hash"] = args[1]["evidence"]["content_hash"]
    with pytest.raises(ValueError):
        build(args)
    args = make(evidence)
    args[1]["metrics"][0]["input_tokens"] += 1
    with pytest.raises(ValueError):
        build(args)


@pytest.mark.parametrize("missing", [None, 0])
def test_missing_or_zero_target_not_eligible(evidence, missing):
    args = make(evidence, compatible=True)
    for row in args[1]["metrics"]:
        row["output_tokens"] = missing
    reseal(args[1])
    args = (args[0], args[1], _observation(evidence[0], args[1]))
    value = build(args)
    assert value["eligibility"] != "eligible"
    assert value["observed"]["output_tokens_mean"] == missing


def test_reusing_prediction_across_batches_does_not_multiply_samples(evidence):
    args = make(evidence, compatible=True)
    first = build(args)
    second = build(make(evidence, compatible=True, run_id="run-" + "b" * 32))
    summary = summarize_learning([first, first, second], receipt=args[0], observation=first)
    assert summary["eligible_sample_count"] == 1
    assert summary["observation_count"] == 2


def test_store_atomic_concurrent_idempotence_and_corruption(evidence, tmp_path):
    value = build(make(evidence))
    store = ResponseLearningStore(tmp_path / "responses")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(store.append, [value] * 8))
    assert sum(created for _, created in results) == 1
    path = store.root / (value["observation_id"] + ".json")
    data = json.loads(path.read_text())
    data["observed"]["output_tokens_mean"] = 999
    path.write_text(json.dumps(data))
    for action in (store.list, lambda: store.get(value["observation_id"]), lambda: store.append(value)):
        with pytest.raises(ValueError):
            action()


@pytest.mark.parametrize("bad_id", ["../outside", "..\\outside", "C:\\outside", "response:bad", "", "CON"])
def test_store_path_safety(tmp_path, bad_id):
    with pytest.raises(ValueError):
        ResponseLearningStore(tmp_path / "responses").get(bad_id)


def test_store_detects_rehashed_identity_collision(evidence, tmp_path):
    value = build(make(evidence))
    store = ResponseLearningStore(tmp_path / "responses")
    store.append(value)
    changed = copy.deepcopy(value)
    changed["predicted"]["output_tokens_mean"] = 33
    changed["content_hash"] = content_hash({k: v for k, v in changed.items() if k != "content_hash"})
    with pytest.raises(ValueError, match="collision"):
        store.append(changed)
    changed["observation_id"] = "../escape"
    with pytest.raises(ValueError):
        validate_response_observation(changed)


def test_summary_rejects_foreign_selection_and_tampering(evidence):
    args = make(evidence)
    value = build(args)
    wrong = copy.deepcopy(args[0])
    wrong["content_hash"] = "f" * 64
    with pytest.raises(ValueError):
        summarize_learning([value], receipt=wrong, observation=value)
    value["eligibility"] = "eligible"
    with pytest.raises(ValueError):
        summarize_learning([value], receipt=args[0], observation=value)


@pytest.mark.parametrize("field", ["prompt", "question", "answer", "tool_body"])
def test_store_rejects_content_fields_even_when_rehashed(evidence, tmp_path, field):
    value = build(make(evidence))
    value[field] = "must not persist"
    value["content_hash"] = content_hash({k: v for k, v in value.items() if k != "content_hash"})
    with pytest.raises(ValueError):
        ResponseLearningStore(tmp_path / "responses").append(value)
    assert not (tmp_path / "responses").exists()


def test_allocation_cap_and_configuration_corruption_rejected(evidence):
    args = make(evidence)
    args[2]["observed_model_allocation_usd"] = 123
    with pytest.raises(ValueError):
        build(args)
    args = make(evidence)
    args[2]["measurement"]["max_output_tokens"] = True
    with pytest.raises(ValueError):
        build(args)
    value = build(make(evidence))
    value["configuration"]["agent"]["prompt"] = "secret"
    value["configuration_hash"] = content_hash(value["configuration"])
    value["content_hash"] = content_hash({k: v for k, v in value.items() if k != "content_hash"})
    with pytest.raises(ValueError):
        validate_response_observation(value)


def independent_records(evidence, *, count=13, constant_predictors=False, constant_actuals=False):
    args = make(evidence, compatible=True)
    records, receipts = [], []
    for index in range(count):
        receipt, batch, _ = copy.deepcopy(args)
        receipt["receipt_id"] = f"receipt-independent-{index}"
        receipt["prediction"]["prediction_id"] = index
        contract = receipt["response_prediction_contract"]
        contract["prediction_id"] = index
        contract["predicted_input_tokens_mean"] = 20 if constant_predictors else 20 * (index + 1)
        contract["predicted_output_tokens_mean"] = 10 if constant_predictors else 10 * (index + 1)
        receipt["content_hash"] = content_hash({k: v for k, v in receipt.items() if k not in ("content_hash", "receipt_id")})
        batch["prediction"] = {"receipt_id": receipt["receipt_id"], "content_hash": receipt["content_hash"]}
        batch["run_id"] = f"run-independent-{index}"
        timestamp = (datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(hours=index)).isoformat()
        batch["started_at"] = batch["ended_at"] = timestamp
        for row in batch["metrics"]:
            row["input_tokens"] = 40 if constant_actuals else 40 * (index + 1)
            row["output_tokens"] = 20 if constant_actuals else 20 * (index + 1)
        reseal(batch)
        records.append(build_response_observation(receipt=receipt, batch=batch, observation=_observation(evidence[0], batch)))
        receipts.append(receipt)
    return records, receipts


def test_root_observations_fit_and_evaluate_without_touching_legacy_history(evidence, monkeypatch):
    from future_token_predictor.history.database import HistoryDatabase
    from future_token_predictor.history.response_calibration import (
        evaluate_response_candidate, fit_response_candidate,
    )

    monkeypatch.setattr(HistoryDatabase, "record_actual", lambda *a, **k: pytest.fail("Legacy history write"))
    records, _ = independent_records(evidence)
    candidate = fit_response_candidate(records[:10], cohort=records[0]["cohort"])
    assert candidate["status"] == "ready"
    proof = evaluate_response_candidate(candidate, records[10:])
    assert proof["status"] == "improved"
    assert proof["before_wape"] == .5
    assert proof["after_wape"] == 0


def test_summary_invokes_scoped_fitter_without_claiming_applied_or_heldout_improvement(evidence, monkeypatch):
    from future_token_predictor.history import response_calibration

    records, receipts = independent_records(evidence, count=10)
    original = response_calibration.fit_response_candidate
    calls = []

    def fit(values, **kwargs):
        calls.append(kwargs["target"])
        return original(values, **kwargs)

    monkeypatch.setattr(response_calibration, "fit_response_candidate", fit)
    summary = summarize_learning(records, receipt=receipts[-1], observation=records[-1])
    assert calls == ["input_tokens_mean", "output_tokens_mean"]
    assert summary["status"] == "advisory_candidate_ready"
    assert summary["eligible_sample_count"] == 10
    assert all(candidate["status"] == "ready" for candidate in summary["advisory_candidates"].values())
    assert all(candidate["factors"]["slope"] == pytest.approx(2)
               for candidate in summary["advisory_candidates"].values())
    assert summary["calibration_applied"] is False
    assert summary["before_wape"] is summary["after_wape"] is None
    assert summarize_learning(records * 2, receipt=receipts[-1], observation=records[-1]) == summary


@pytest.mark.parametrize("options,status", [
    ({"constant_predictors": True}, "degenerate_predictors"),
    ({"constant_actuals": True}, "poor_fit"),
])
def test_summary_reports_actual_fit_gates(evidence, options, status):
    records, receipts = independent_records(evidence, count=10, **options)
    summary = summarize_learning(records, receipt=receipts[-1], observation=records[-1])
    assert summary["status"] == status
    assert all(value["status"] == status for value in summary["advisory_candidates"].values())
    assert summary["calibration_applied"] is False
    assert summary["before_wape"] is summary["after_wape"] is None


def test_summary_never_fits_insufficient_or_scope_pending_records(evidence, monkeypatch):
    from future_token_predictor.history import response_calibration

    monkeypatch.setattr(response_calibration, "fit_response_candidate", lambda *a, **kw: pytest.fail("No eligible cohort"))
    records, receipts = independent_records(evidence, count=9)
    summary = summarize_learning(records, receipt=receipts[-1], observation=records[-1])
    assert summary["status"] == "insufficient_samples"
    assert summary["advisory_candidates"] == {}
    args = make(evidence)
    value = build(args)
    summary = summarize_learning([value] * 10, receipt=args[0], observation=value)
    assert summary["status"] == "scope_compatibility_pending"
    assert summary["eligible_sample_count"] == 0
    assert summary["advisory_candidates"] == {}


def test_adding_scope_contract_to_old_receipt_without_hash_binding_is_rejected(evidence):
    args = make(evidence)
    args[0]["response_prediction_contract"] = make(evidence, compatible=True)[0]["response_prediction_contract"]
    with pytest.raises(ValueError, match="Receipt content hash"):
        build(args)
