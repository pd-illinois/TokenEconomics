from copy import deepcopy
from collections import Counter
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from rag.qna_dataset import (
    DEFAULT_DATASET, SEGMENTS, dataset_hash, load_dataset, validate_dataset,
)


def seal(dataset):
    dataset["content_hash"] = dataset_hash(dataset)
    return dataset


def test_local_pilot_integrity_and_segment_coverage():
    dataset = load_dataset()
    assert len(dataset["cases"]) == 25
    assert Counter(case["segment_id"] for case in dataset["cases"]) == dict.fromkeys(SEGMENTS, 5)
    assert len(dataset["sources"]) == 5
    assert {case["partition"] for case in dataset["cases"]} == {"development", "holdout"}
    assert {case["expected_answer_status"] for case in dataset["cases"]} == {"proposed"}
    assert {case["human_review_status"] for case in dataset["cases"]} == {"pending"}
    assert dataset["heldout_scored_proof"] is False
    assert dataset["operational_admission"] is False
    assert "keyword-substring" in " ".join(dataset["notes"])


def test_dataset_matches_versioned_schema():
    path = Path(__file__).parents[1] / "data" / "contracts" / "rag-qna-dataset.v1.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(load_dataset(verify_sources=False))


def test_canonical_hash_is_order_independent_but_content_bound():
    dataset = load_dataset(verify_sources=False)
    assert dataset_hash(dict(reversed(list(dataset.items())))) == dataset["content_hash"]
    dataset["cases"][0]["expected_answer"] += " changed"
    with pytest.raises(ValueError, match="hash mismatch"):
        validate_dataset(dataset)


def test_hash_verification_is_not_authentication():
    dataset = load_dataset(verify_sources=False)
    dataset["cases"][0]["expected_answer"] = "A modified but still proposed draft"
    validate_dataset(seal(dataset))
    assert dataset["human_review_status"] == "pending"


@pytest.mark.parametrize("field,value", [
    ("schema_version", "unknown"),
    ("human_review_status", "reviewed"),
    ("heldout_scored_proof", True),
    ("operational_admission", True),
    ("evidence_status", "production-validated"),
])
def test_pilot_cannot_self_promote(field, value):
    dataset = load_dataset(verify_sources=False)
    dataset[field] = value
    with pytest.raises(ValueError):
        validate_dataset(seal(dataset))


def test_shared_question_families_cannot_leak():
    dataset = load_dataset(verify_sources=False)
    families = {}
    for case in dataset["cases"]:
        families.setdefault(case["family_id"], set()).add(case["partition"])
    assert all(len(partitions) == 1 for partitions in families.values())
    dataset["cases"][1]["family_id"] = dataset["cases"][0]["family_id"]
    dataset["cases"][1]["partition"] = "holdout"
    with pytest.raises(ValueError, match="leaks"):
        validate_dataset(seal(dataset))


@pytest.mark.parametrize("mutation", ["id", "question", "source", "review", "segment", "rubric"])
def test_invalid_cases_fail_closed(mutation):
    dataset = load_dataset(verify_sources=False)
    first, second = dataset["cases"][:2]
    if mutation == "id":
        second["case_id"] = first["case_id"]
    elif mutation == "question":
        second["question"] = first["question"].upper()
    elif mutation == "source":
        first["source_locators"][0]["source_id"] = "not-in-corpus"
    elif mutation == "review":
        first["human_review_status"] = "approved"
    elif mutation == "segment":
        first["segment_id"] = "aggregate"
    else:
        first["rubric"] = []
    with pytest.raises(ValueError):
        validate_dataset(seal(dataset))


def test_corrupt_source_bytes_are_detected_without_changing_files():
    dataset = load_dataset(verify_sources=False)
    dataset["sources"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="Source content hash mismatch"):
        validate_dataset(seal(dataset), source_root=DEFAULT_DATASET.parent.parent / "data")


@pytest.mark.parametrize("name", ["../secret", "..\\secret", "nested/book.txt", "nested\\book.txt"])
def test_source_path_traversal_disallowed(name):
    dataset = load_dataset(verify_sources=False)
    dataset["sources"][0]["file"] = name
    with pytest.raises(ValueError, match="basename"):
        validate_dataset(seal(dataset))


def test_case_hash_changes_with_rubric_and_partition():
    original = load_dataset(verify_sources=False)
    dataset = deepcopy(original)
    dataset["cases"][0]["rubric"].append("Additional proposed criterion")
    assert dataset_hash(dataset) != dataset_hash(original)
    dataset = deepcopy(original)
    dataset["cases"][0]["partition"] = "holdout"
    assert dataset_hash(dataset) != dataset_hash(original)
