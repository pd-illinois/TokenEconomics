from __future__ import annotations

import copy
import builtins
import importlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from costgov.performance_review import content_hash
from costgov.planning import PlanStore
from costgov.policy_store import LoadedPolicy, PolicyLoadError, admit_receipt
from costgov.response_forecasts import (
    build_response_forecast, normalize_response_experiment,
    validate_receipt_response, validate_response_dispatch,
)
from costgov.response_learning import ResponseLearningStore, build_response_observation, response_configuration
from rag.performance_evidence import _observation
from test_performance_review import evidence, reseal, saved  # noqa: F401


@pytest.fixture
def prospective(evidence, tmp_path, monkeypatch):
    template = saved(evidence)
    template["agent"]["retrieval_evidence"] = {"content_hash": "d" * 64}
    configuration = response_configuration(template)
    store = PlanStore(tmp_path / "plans")
    learning = ResponseLearningStore(tmp_path / "learning")
    source = {"schema_version": "response-baseline-source.v1", "revision": "estimator-v1",
              "content_hash": "e" * 64}

    def create(index=0, candidates=None, baseline=None, schema="2.0", experiment=None):
        timestamp = datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(hours=index)
        monkeypatch.setattr("costgov.planning._now", lambda: timestamp.isoformat())
        result = {
            "description": "Prospective response baseline", "intake": {},
            "prediction": {**evidence[1]["prediction"], "prediction_id": f"prospective-{index}",
                           "pricing_verified": True},
            "infrastructure": {"status": "not_estimated"},
        }
        if schema in {"3.0", "5.0", "6.0"}:
            result.update(route={"route_id": "direct"}, commercial={})
        if schema in {"5.0", "6.0"}:
            result["meter_stack"] = {"schema_version": "meter-stack.v1"}
        if schema == "6.0":
            result["infrastructure"]["schema_version"] = "infrastructure-forecast.v1"
        baseline = baseline or {"input_tokens_mean": 20 * (index + 1), "output_tokens_mean": 10 * (index + 1)}
        session = store.create_session("report-prospective", "Response forecast", {})
        session, receipt = store.complete_response_forecast(
            session, result, configuration=configuration, baseline=baseline,
            source=source, candidates=candidates, experiment=experiment,
        )
        batch = copy.deepcopy(template)
        batch.update(plan_id=session["plan_id"], report_id=session["report_id"], run_id=f"run-prospective-{index}")
        batch["prediction"] = {"receipt_id": receipt["receipt_id"], "content_hash": receipt["content_hash"]}
        batch["started_at"] = batch["ended_at"] = (timestamp + timedelta(minutes=10)).isoformat()
        for row in batch["metrics"]:
            row["input_tokens"] = baseline["input_tokens_mean"] * 2
            row["output_tokens"] = baseline["output_tokens_mean"] * 2
        reseal(batch)
        normalized = _observation(evidence[0], batch)
        record = build_response_observation(receipt=receipt, batch=batch, observation=normalized)
        learning.append(record)
        return result, session, receipt, batch, normalized, record

    return create, store, learning, configuration, source


def test_new_receipt_binds_explicit_response_not_generic_tokens(prospective, evidence):
    create, plans, learning, configuration, _ = prospective
    result, session, receipt, _, _, record = create()
    assert receipt["schema_version"] == "2.0"
    assert receipt["prediction"]["tokens_per_call"] == result["prediction"]["tokens_per_call"]
    assert receipt["response_prediction_contract"]["predicted_output_tokens_mean"] == 10
    assert receipt["response_forecast"]["configuration"] == configuration
    assert record["predicted"]["output_tokens_mean"] == 10
    assert record["eligibility"] == "eligible"
    assert record["calibration_applied"] is False
    assert receipt["content_hash"] == content_hash({
        key: value for key, value in receipt.items() if key not in ("receipt_id", "content_hash")
    })
    assert plans.get_receipt(session["plan_id"]) == receipt
    assert learning.list() == [record]
    checks = admit_receipt(receipt, evidence[2])["checks"]
    assert next(check for check in checks if check["name"] == "receipt_integrity")["passed"]


@pytest.mark.parametrize("schema", ["2.0", "3.0", "5.0", "6.0"])
def test_supported_receipt_hash_surfaces_include_extension(prospective, evidence, schema):
    _, session, receipt, *_ = prospective[0](schema=schema)
    assert receipt["schema_version"] == schema
    assert prospective[1].get_receipt(session["plan_id"]) == receipt
    checks = admit_receipt(receipt, evidence[2])["checks"]
    assert next(check for check in checks if check["name"] == "receipt_integrity")["passed"]


@pytest.mark.parametrize("field", ["etag", "development_only", "source"])
def test_admission_rejects_changed_or_nonauthoritative_probed_policy(prospective, evidence, field):
    receipt = prospective[0]()[2]
    loaded = evidence[2]
    assert admit_receipt(receipt, loaded)["status"] == "admitted"
    provenance = {**loaded.provenance, field: True if field == "development_only" else "changed"}
    decision = admit_receipt(receipt, LoadedPolicy(loaded.document, provenance))
    assert decision["status"] == "rejected"
    assert next(check for check in decision["checks"] if check["name"] == "response_configuration_policy")["passed"] is False


@pytest.mark.parametrize("bad", [0, -1, True, "10", float("inf"), float("nan"), 1025])
def test_baseline_requires_positive_finite_cap_bounded_means(prospective, bad):
    create, plans, _, _, _ = prospective
    with pytest.raises(ValueError):
        create(baseline={"input_tokens_mean": 20, "output_tokens_mean": bad})
    assert all(not row["receipt_id"] for row in plans.list())


@pytest.mark.parametrize("field", ["retrieval", "policy", "pricing", "cap", "source", "model"])
def test_configuration_and_source_are_required(prospective, field):
    _, _, _, config, source = prospective
    config = copy.deepcopy(config)
    prediction = {"prediction_id": 1, "model": "model-test", "provider": "azure_openai", "pricing_version": "price-v1"}
    if field == "retrieval":
        config["agent"]["retrieval_configuration_hash"] = None
    elif field == "policy":
        config["policy"]["source"] = "local_file"
    elif field == "pricing":
        config["pricing"]["revision"] = "different"
    elif field == "cap":
        config["measurement"]["max_output_tokens"] = 10.5
    elif field == "source":
        source = {"schema_version": "actuals.v1", "revision": "v1", "content_hash": "e" * 64}
    else:
        prediction["model"] = "different"
    with pytest.raises(ValueError):
        build_response_forecast(prediction=prediction, configuration=config,
                                baseline={"input_tokens_mean": 20, "output_tokens_mean": 10}, source=source)


def test_retrofit_clone_reuse_and_implicit_intent_rejected(prospective):
    create, plans, _, config, source = prospective
    result, session, receipt, *_ = create()
    before = plans.get_receipt(session["plan_id"])
    kwargs = dict(configuration=config, baseline={"input_tokens_mean": 20, "output_tokens_mean": 10}, source=source)
    with pytest.raises(ValueError, match="fresh draft"):
        plans.complete_response_forecast(session, result, **kwargs)
    draft = plans.create_session("new-report", "New", {})
    with pytest.raises(ValueError, match="receipt clone"):
        plans.complete_response_forecast(draft, receipt, **kwargs)
    with pytest.raises(ValueError, match="new prediction"):
        plans.complete_response_forecast(draft, result, **kwargs)
    with pytest.raises(ValueError, match="explicit"):
        plans.complete(draft, {**result, "response_prediction_contract": receipt["response_prediction_contract"]})
    assert plans.get_receipt(session["plan_id"]) == before


@pytest.mark.parametrize("mutation", ["contract", "baseline", "missing", "candidate"])
def test_receipt_validation_rejects_rehashed_response_tampering(prospective, evidence, mutation):
    receipt = copy.deepcopy(prospective[0]()[2])
    if mutation == "contract":
        receipt["response_prediction_contract"]["predicted_output_tokens_mean"] += 1
    elif mutation == "baseline":
        receipt["response_forecast"]["baseline"]["output_tokens_mean"] += 1
    elif mutation == "missing":
        del receipt["response_forecast"]
    else:
        receipt["response_forecast"]["calibration_applied"] = True
    receipt["content_hash"] = content_hash({
        k: v for k, v in receipt.items() if k not in ("receipt_id", "content_hash")
    })
    with pytest.raises((ValueError, PolicyLoadError)):
        admit_receipt(receipt, evidence[2])


@pytest.mark.parametrize("mutation", ["time", "configuration"])
def test_observation_checks_preexecution_time_and_exact_probe(prospective, mutation):
    _, _, receipt, batch, normalized, _ = prospective[0]()
    if mutation == "time":
        batch["started_at"] = batch["ended_at"] = receipt["created_at"]
        normalized["started_at"] = normalized["ended_at"] = receipt["created_at"]
    else:
        batch["agent"]["retrieval_evidence"]["content_hash"] = "f" * 64
    reseal(batch)
    normalized["evidence_hash"] = batch["evidence"]["content_hash"]
    with pytest.raises(ValueError):
        build_response_observation(receipt=receipt, batch=batch, observation=normalized)


def test_fit_apply_heldout_and_refit_keep_original_baseline(prospective):
    create, plans, learning, _, _ = prospective
    records = [create(index)[-1] for index in range(10)]
    ids = [row["observation_id"] for row in records]
    originals = {path: path.read_bytes() for path in plans.root.rglob("*.json")}
    candidate = learning.fit_candidate(ids, cohort=records[0]["cohort"])
    assert candidate["status"] == "ready" and candidate["sample_count"] == 10
    assert candidate["calibration_applied"] is False
    assert learning.get_candidate(candidate["candidate_id"]) == candidate
    later = [create(index, {"output_tokens_mean": candidate})[-1] for index in range(10, 13)]
    assert all(row["calibration_applied"] is True for row in later)
    assert later[0]["predicted"]["output_tokens_mean"] == pytest.approx(220)
    evaluation = learning.evaluate_candidate(candidate["candidate_id"], [row["observation_id"] for row in later])
    assert evaluation["status"] == "improved"
    assert evaluation["before_wape"] == .5
    assert evaluation["after_wape"] == 0
    assert learning.get_evaluation(evaluation["evaluation_id"]) == evaluation
    assert learning.list_evaluations() == [evaluation]
    assert learning.evaluate_candidate(candidate["candidate_id"], [row["observation_id"] for row in later]) == evaluation
    assert all(path.read_bytes() == value for path, value in originals.items())
    refitted = learning.fit_candidate(ids + [row["observation_id"] for row in later], cohort=records[0]["cohort"])
    assert refitted["factors"]["slope"] == pytest.approx(2)
    projection = learning._read_artifact("input", later[0]["observation_id"])
    assert projection["predicted"]["output_tokens_mean"] == 110
    assert projection["baseline_projection"]["source_observation_hash"] == later[0]["content_hash"]
    assert learning.get(later[0]["observation_id"])["predicted"]["output_tokens_mean"] == 220


def test_learning_minima_degeneracy_and_overlap_fail_closed(prospective):
    create, _, learning, _, _ = prospective
    records = [create(index)[-1] for index in range(13)]
    ids = [row["observation_id"] for row in records]
    cohort = records[0]["cohort"]
    insufficient = learning.fit_candidate(ids[:9], cohort=cohort)
    assert insufficient["status"] == "insufficient_samples"
    assert learning.get_candidate(insufficient["candidate_id"]) == insufficient
    candidate = learning.fit_candidate(ids[:10], cohort=cohort)
    assert learning.evaluate_candidate(candidate["candidate_id"], ids[10:12])["status"] == "insufficient_held_out_samples"
    for overlaps in (ids[:3], [ids[10], ids[10], ids[11]]):
        with pytest.raises(ValueError, match="[Oo]verlap"):
            learning.evaluate_candidate(candidate["candidate_id"], overlaps)
    with pytest.raises(ValueError, match="Overlapping"):
        learning.fit_candidate(ids[:10] + [ids[0]], cohort=cohort)
    late_candidate = learning.fit_candidate(ids[3:], cohort=cohort)
    with pytest.raises(ValueError, match="later"):
        learning.evaluate_candidate(late_candidate["candidate_id"], ids[:3])
    # Independent IDs with constant predictors cannot produce usable candidates.
    constant = [create(index, baseline={"input_tokens_mean": 20, "output_tokens_mean": 10})[-1]
                for index in range(20, 30)]
    assert learning.fit_candidate([r["observation_id"] for r in constant], cohort=cohort)["status"] == "degenerate_predictors"


def test_adjustment_over_cap_and_wrong_cohort_are_not_published(prospective):
    create, plans, learning, _, _ = prospective
    records = [create(index)[-1] for index in range(10)]
    candidate = learning.fit_candidate([r["observation_id"] for r in records], cohort=records[0]["cohort"])
    with pytest.raises(ValueError, match="output cap"):
        create(11, {"output_tokens_mean": candidate},
               baseline={"input_tokens_mean": 20, "output_tokens_mean": 700})
    assert len([row for row in plans.list() if row["receipt_id"]]) == 10
    candidate["cohort"]["configuration_hash"] = "f" * 64
    with pytest.raises(ValueError):
        create(12, {"output_tokens_mean": candidate})


def test_both_targets_apply_with_exact_candidate_provenance(prospective):
    create, _, learning, _, _ = prospective
    records = [create(index)[-1] for index in range(10)]
    candidates = {
        target: learning.fit_candidate([r["observation_id"] for r in records], cohort=records[0]["cohort"], target=target)
        for target in ("input_tokens_mean", "output_tokens_mean")
    }
    receipt = create(10, candidates)[2]
    forecast = receipt["response_forecast"]
    assert forecast["selected"] == {"input_tokens_mean": 440, "output_tokens_mean": 220}
    for target, adjustment in forecast["adjustments"].items():
        assert adjustment["candidate_hash"] == candidates[target]["content_hash"]
        assert adjustment["candidate_id"] == candidates[target]["candidate_id"]
        assert adjustment["held_out_improvement_proven"] is False
    earlier = copy.deepcopy(receipt)
    earlier["created_at"] = candidates["output_tokens_mean"]["trained_through"]
    with pytest.raises(ValueError, match="later"):
        validate_receipt_response(earlier)


def test_candidate_and_projection_corruption_rejected(prospective):
    create, _, learning, _, _ = prospective
    records = [create(index)[-1] for index in range(10)]
    candidate = learning.fit_candidate([r["observation_id"] for r in records], cohort=records[0]["cohort"])
    path = learning._artifact_path("input", records[0]["observation_id"])
    data = json.loads(path.read_text())
    data["predicted"]["output_tokens_mean"] = 999
    data["content_hash"] = content_hash({k: v for k, v in data.items() if k != "content_hash"})
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="immutable"):
        learning.get_candidate(candidate["candidate_id"])


def test_legacy_receipt_remains_without_response_semantics(prospective):
    create, plans, _, _, _ = prospective
    result = create()[0]
    result["prediction"]["prediction_id"] = "ordinary-new"
    session = plans.create_session("legacy-report", "Generic call", {})
    _, receipt = plans.complete(session, result)
    assert "response_forecast" not in receipt
    assert "response_prediction_contract" not in receipt
    assert validate_receipt_response(receipt) is None


def test_predispatch_returns_detached_exact_contract_without_mutating_receipt(prospective):
    _, _, receipt, batch, *_ = prospective[0]()
    before = copy.deepcopy(receipt)
    contract = validate_response_dispatch(
        receipt, configuration=response_configuration(batch), now=batch["started_at"],
    )
    assert contract == receipt["response_prediction_contract"]
    contract["predicted_output_tokens_mean"] += 1
    assert receipt == before
    assert validate_response_dispatch(receipt, configuration=response_configuration(batch)) == before["response_prediction_contract"]


@pytest.mark.parametrize("path,value", [
    (("agent", "agent_version"), "changed"),
    (("agent", "deployment"), "changed"),
    (("agent", "model_version"), "changed"),
    (("agent", "retrieval_configuration_hash"), "f" * 64),
    (("policy", "etag"), "changed"),
    (("policy", "content_hash"), "f" * 64),
    (("policy", "source"), "local_file"),
    (("pricing", "revision"), "changed"),
    (("pricing", "catalog_hash"), "f" * 64),
    (("measurement", "max_output_tokens"), 512),
])
def test_predispatch_blocks_configuration_drift_before_provider_call(prospective, path, value):
    _, _, receipt, batch, *_ = prospective[0]()
    configuration = response_configuration(batch)
    configuration[path[0]][path[1]] = value
    dispatched = []

    def runner():
        validate_response_dispatch(receipt, configuration=configuration, now=batch["started_at"])
        dispatched.append(True)

    with pytest.raises(ValueError):
        runner()
    assert dispatched == []


@pytest.mark.parametrize("now", ["2026-09-01T00:00:00Z", "2026-08-31T23:59:59Z",
                                 "2026-09-01T00:10:00", "invalid", True])
def test_predispatch_rejects_nonprospective_or_invalid_time(prospective, now):
    _, _, receipt, batch, *_ = prospective[0]()
    with pytest.raises(ValueError, match="dispatch time"):
        validate_response_dispatch(receipt, configuration=response_configuration(batch), now=now)


@pytest.mark.parametrize("mutation", ["strip_both", "strip_contract", "strip_forecast", "receipt_id", "description"])
def test_predispatch_blocks_tampered_receipt_or_stripped_intent(prospective, mutation):
    _, _, receipt, batch, *_ = prospective[0]()
    if mutation == "strip_both":
        del receipt["response_forecast"]
        del receipt["response_prediction_contract"]
    elif mutation == "strip_contract":
        del receipt["response_prediction_contract"]
    elif mutation == "strip_forecast":
        del receipt["response_forecast"]
    else:
        receipt[mutation] = "tampered"
    with pytest.raises(ValueError):
        validate_response_dispatch(receipt, configuration=response_configuration(batch), now=batch["started_at"])


def test_predispatch_historical_receipt_retains_no_response_intent(prospective):
    create, plans, _, _, _ = prospective
    result = create()[0]
    result["prediction"]["prediction_id"] = "historical-no-response"
    session = plans.create_session("historical-report", "Generic", {})
    _, receipt = plans.complete(session, result)
    before = copy.deepcopy(receipt)
    assert validate_response_dispatch(receipt, configuration=None) is None
    assert receipt == before


def test_predispatch_adjusted_contract_uses_candidate_bound_means(prospective):
    create, _, learning, _, _ = prospective
    records = [create(index)[-1] for index in range(10)]
    candidate = learning.fit_candidate([r["observation_id"] for r in records], cohort=records[0]["cohort"])
    _, _, receipt, batch, *_ = create(10, {"output_tokens_mean": candidate})
    contract = validate_response_dispatch(receipt, configuration=response_configuration(batch), now=batch["started_at"])
    assert contract["predicted_output_tokens_mean"] == 220
    assert receipt["response_forecast"]["baseline"]["output_tokens_mean"] == 110


def experiment_refs(**overrides):
    return {
        "dataset_hash": "a" * 64, "case_ids": ["case-2", "case-1"],
        "family_ids": ["family-1"], "split": "development", **overrides,
    }


def test_experiment_refs_are_prospectively_sealed_without_changing_cohort(prospective):
    create, plans, learning, _, _ = prospective
    supplied = experiment_refs()
    _, session, receipt, batch, _, record = create(experiment=supplied)
    expected = {
        "schema_version": "response-experiment.v1", "dataset_hash": "a" * 64,
        "case_ids": ["case-1", "case-2"], "family_ids": ["family-1"], "split": "development",
    }
    assert receipt["response_forecast"]["experiment"] == expected
    assert record["response_forecast"]["experiment"] == expected
    assert record["schema_version"] == "response-usage-observation.v1"
    assert learning.get(record["observation_id"]) == record
    validate_response_dispatch(receipt, configuration=response_configuration(batch), now=batch["started_at"])
    supplied["case_ids"].append("case-3")
    assert plans.get_receipt(session["plan_id"])["response_forecast"]["experiment"] == expected
    _, _, holdout, _, _, later = create(
        1, experiment=experiment_refs(case_ids=["case-3"], family_ids=["family-2"], split="holdout"),
    )
    assert holdout["response_forecast"]["cohort"] == receipt["response_forecast"]["cohort"]
    assert later["cohort"] == record["cohort"]


@pytest.mark.parametrize("overrides", [
    {"dataset_hash": "invalid"}, {"case_ids": []}, {"family_ids": []},
    {"case_ids": ["case-1", "case-1"]}, {"family_ids": ["family-1", "family-1"]},
    {"case_ids": ["question text is not an identity"]}, {"family_ids": [True]},
    {"case_ids": "case-1"}, {"split": "test"}, {"split": True},
    {"schema_version": "unknown.v1"}, {"prompt": "forbidden"},
])
def test_invalid_experiment_refs_fail_before_receipt_publication(prospective, overrides):
    create, plans, _, _, _ = prospective
    with pytest.raises(ValueError):
        create(experiment=experiment_refs(**overrides))
    assert all(not row["receipt_id"] for row in plans.list())


@pytest.mark.parametrize("field", ["dataset_hash", "case_ids", "family_ids", "split"])
def test_experiment_tampering_fails_even_with_rehashed_receipt(prospective, field):
    _, _, receipt, batch, *_ = prospective[0](experiment=experiment_refs())
    changed = receipt["response_forecast"]["experiment"]
    changed[field] = {
        "dataset_hash": "b" * 64, "case_ids": ["other-case"],
        "family_ids": ["other-family"], "split": "holdout",
    }[field]
    receipt["content_hash"] = content_hash({
        k: v for k, v in receipt.items() if k not in ("receipt_id", "content_hash")
    })
    receipt["receipt_id"] = "plan_" + receipt["content_hash"][:20]
    with pytest.raises(ValueError, match="integrity"):
        validate_response_dispatch(receipt, configuration=response_configuration(batch), now=batch["started_at"])


def test_candidate_experiments_exposes_original_family_refs_not_new_forecast_ids(prospective):
    create, _, learning, _, _ = prospective
    # Deliberately repeated cases/families: different forecast IDs must not hide
    # this overlap from the workload adapter's independent family/split checks.
    records = [create(index, experiment=experiment_refs())[-1] for index in range(10)]
    candidate = learning.fit_candidate([r["observation_id"] for r in records], cohort=records[0]["cohort"])
    refs = learning.candidate_experiments(candidate["candidate_id"])
    assert len(refs) == 10
    assert {family for row in refs for family in row["experiment"]["family_ids"]} == {"family-1"}
    assert {case for row in refs for case in row["experiment"]["case_ids"]} == {"case-1", "case-2"}
    for row, record in zip(refs, records):
        assert row["observation_hash"] == record["content_hash"]
        assert row["receipt_hash"] == record["receipt_hash"]
        assert row["experiment"]["split"] == "development"
    refs[0]["experiment"]["family_ids"].append("must-not-persist")
    assert learning.candidate_experiments(candidate["candidate_id"])[0]["experiment"]["family_ids"] == ["family-1"]


def test_candidate_experiments_rejects_missing_historical_refs_without_retrofit(prospective):
    create, _, learning, _, _ = prospective
    records = [create(index)[-1] for index in range(10)]
    before = {path: path.read_bytes() for path in learning.root.glob("*.json")}
    candidate = learning.fit_candidate([r["observation_id"] for r in records], cohort=records[0]["cohort"])
    with pytest.raises(ValueError, match="lacks prospective"):
        learning.candidate_experiments(candidate["candidate_id"])
    assert all(path.read_bytes() == contents for path, contents in before.items())
    assert all("experiment" not in row["response_forecast"] for row in learning.list())


def test_normalized_experiment_can_be_reused_without_relabeling():
    value = normalize_response_experiment(experiment_refs())
    assert normalize_response_experiment(value) == value
    assert value["case_ids"] == ["case-1", "case-2"]


def test_forecast_only_replay_keeps_response_evidence_without_runtime_imports(prospective, monkeypatch):
    import costgov.planning as planning

    create, plans, _, _, _ = prospective
    result, session, receipt, *_ = create(experiment=experiment_refs())
    original_import = builtins.__import__
    forbidden = {
        "response_forecasts", "response_learning", "performance_review", "policy_store", "studio_lifecycle",
    }

    def forecast_only_import(name, *args, **kwargs):
        if name.rsplit(".", 1)[-1] in forbidden:
            pytest.fail(f"Forecast-only replay imported excluded runtime module: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", forecast_only_import)
    importlib.reload(planning)
    readonly = planning.PlanStore(plans.root)
    replayed = readonly.get_receipt(session["plan_id"])
    assert replayed == receipt
    assert json.loads(json.dumps(replayed))["response_forecast"]["experiment"] == receipt["response_forecast"]["experiment"]
    result["prediction"]["prediction_id"] = "forecast-only-new-generic"
    draft = readonly.create_session("forecast-only", "Generic forecast", {})
    _, ordinary = readonly.complete(draft, result)
    assert "response_forecast" not in ordinary
