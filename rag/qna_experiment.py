"""Workload-owned case/family bindings for prospective response forecasts."""

from __future__ import annotations

from rag.qna_dataset import load_dataset, validate_dataset


def select_cases(dataset, case_ids, split):
    validate_dataset(dataset)
    if (not isinstance(case_ids, list) or not 1 <= len(case_ids) <= 25
            or any(not isinstance(value, str) for value in case_ids)
            or len(set(case_ids)) != len(case_ids) or split not in {"development", "holdout"}):
        raise ValueError("Select 1-25 distinct cases from one experiment partition")
    available = {case["case_id"]: case for case in dataset["cases"]}
    if any(identity not in available for identity in case_ids):
        raise ValueError("Unknown Q&A case")
    cases = [available[identity] for identity in case_ids]
    if any(case["partition"] != split for case in cases):
        raise ValueError("Selected cases cross development/holdout partitions")
    experiment = {
        "schema_version": "response-experiment.v1", "dataset_hash": dataset["content_hash"],
        "case_ids": sorted(case_ids), "family_ids": sorted({case["family_id"] for case in cases}),
        "split": split,
    }
    return cases, experiment


def validate_qna_dispatch(receipt, questions, *, dataset=None):
    experiment = (receipt.get("response_forecast") or {}).get("experiment")
    if experiment is None:
        return None
    dataset = load_dataset() if dataset is None else dataset
    cases, expected = select_cases(dataset, experiment["case_ids"], experiment["split"])
    if experiment != expected:
        raise ValueError("Receipt experiment does not match the versioned dataset/families")
    if len(questions) != len(cases) or set(questions) != {case["question"] for case in cases}:
        raise ValueError("Requested questions differ from the prospective forecast")
    by_question = {case["question"]: case for case in cases}
    return [by_question[question] for question in questions]


def validate_training_experiments(dataset, experiments, *, holdout=None):
    if not experiments:
        raise ValueError("Q&A calibration requires pinned training experiment provenance")
    training_families = set()
    for row in experiments:
        experiment = row["experiment"]
        _, expected = select_cases(dataset, experiment["case_ids"], "development")
        if experiment != expected:
            raise ValueError("Training experiment does not match the development dataset")
        training_families.update(experiment["family_ids"])
    if holdout is not None:
        _, expected = select_cases(dataset, holdout["case_ids"], "holdout")
        if holdout != expected or training_families.intersection(holdout["family_ids"]):
            raise ValueError("Holdout families overlap training or do not match the dataset")
    return training_families


def observation_experiments(learning, plans, observation_ids):
    if not observation_ids or len(set(observation_ids)) != len(observation_ids):
        raise ValueError("Select distinct observation identities")
    available = {row["observation_id"]: row for row in learning.list()}
    result, seen_cases = [], set()
    for identity in observation_ids:
        observation = available.get(identity)
        if observation is None:
            raise ValueError("Unknown observation")
        receipt = plans.get_receipt(observation["plan_id"])
        if (receipt is None or receipt["content_hash"] != observation["receipt_hash"]
                or receipt["receipt_id"] != observation["receipt_id"]):
            raise ValueError("Observation does not match its original receipt")
        experiment = (receipt.get("response_forecast") or {}).get("experiment")
        if not experiment or seen_cases.intersection(experiment["case_ids"]):
            raise ValueError("Pilot observations need distinct cases and prospective experiment metadata")
        seen_cases.update(experiment["case_ids"])
        result.append({"experiment": experiment})
    return result
