from copy import deepcopy
from types import SimpleNamespace

import pytest

from rag.qna_dataset import load_dataset
from rag.qna_experiment import (
    observation_experiments, select_cases, validate_qna_dispatch, validate_training_experiments,
)


def test_question_and_family_identity_are_bound_before_dispatch():
    dataset = load_dataset(verify_sources=False)
    case = dataset["cases"][0]
    cases, experiment = select_cases(dataset, [case["case_id"]], case["partition"])
    receipt = {"response_forecast": {"experiment": experiment}}
    assert validate_qna_dispatch(receipt, [case["question"]], dataset=dataset) == cases
    with pytest.raises(ValueError, match="differ"):
        validate_qna_dispatch(receipt, ["An unrelated question"], dataset=dataset)
    changed = deepcopy(receipt)
    changed["response_forecast"]["experiment"]["family_ids"] = ["fabricated-family"]
    with pytest.raises(ValueError, match="dataset/families"):
        validate_qna_dispatch(changed, [case["question"]], dataset=dataset)


def test_training_and_holdout_families_cannot_be_relabeled():
    dataset = load_dataset(verify_sources=False)
    development = next(case for case in dataset["cases"] if case["partition"] == "development")
    holdout = next(case for case in dataset["cases"] if case["partition"] == "holdout")
    _, training = select_cases(dataset, [development["case_id"]], "development")
    _, later = select_cases(dataset, [holdout["case_id"]], "holdout")
    assert validate_training_experiments(dataset, [{"experiment": training}], holdout=later)
    with pytest.raises(ValueError, match="partitions"):
        select_cases(dataset, [holdout["case_id"]], "development")
    with pytest.raises(ValueError, match="partitions"):
        validate_training_experiments(dataset, [{"experiment": later}])


def test_generic_historical_receipts_do_not_gain_qna_semantics():
    assert validate_qna_dispatch({}, ["Historical question"]) is None


def test_pilot_observation_selection_rejects_repeated_cases_and_foreign_receipts():
    experiment = {"case_ids": ["case-1"]}
    receipt = {"receipt_id": "receipt-1", "content_hash": "a" * 64,
               "response_forecast": {"experiment": experiment}}
    observation = {"observation_id": "obs-1", "plan_id": "plan-1",
                   "receipt_id": "receipt-1", "receipt_hash": "a" * 64}
    learning = SimpleNamespace(list=lambda: [observation, {**observation, "observation_id": "obs-2"}])
    plans = SimpleNamespace(get_receipt=lambda _: receipt)
    assert observation_experiments(learning, plans, ["obs-1"]) == [{"experiment": experiment}]
    with pytest.raises(ValueError, match="distinct cases"):
        observation_experiments(learning, plans, ["obs-1", "obs-2"])
    receipt["content_hash"] = "b" * 64
    with pytest.raises(ValueError, match="original receipt"):
        observation_experiments(learning, plans, ["obs-1"])
