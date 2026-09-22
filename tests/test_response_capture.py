import json

import pytest

from costgov.studio_lifecycle import digest
from rag.qna_capture_adapter import NO_RETRIEVED_CONTEXT, prepare_evaluation
from rag.response_capture import CapturedResponses


def capture():
    return CapturedResponses([{"case_id": "case-1", "question": "Who?"}], allow_content_evaluation=True)


def observed():
    return {
        "run_id": "run-1", "question_number": 1, "question": "Who?",
        "provider_request_id": "req-1", "metric": {"question_number": 1, "input_tokens": 100},
        "response": {"id": "resp-1", "output": [
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Darcy."}]},
            {"type": "mcp_call", "name": "knowledge_base_retrieve", "output": "Actual retrieved passage."},
        ]},
    }


def test_content_capture_requires_explicit_consent():
    with pytest.raises(ValueError, match="consent"):
        CapturedResponses([])


def test_manifest_links_exact_response_without_storing_content():
    collector = capture()
    item = observed()
    collector(**item)
    batch = {"run_id": "run-1", "execution_status": "completed", "questions_count": 1,
             "metrics": [item["metric"]]}
    batch["evidence"] = {"content_hash": digest(batch)}
    manifest = collector.manifest(batch, dataset_hash="a" * 64)
    assert manifest["rows"][0]["provider_request_id"] == "req-1"
    assert manifest["rows"][0]["provider_response_id"] == "resp-1"
    assert "Actual retrieved passage" not in json.dumps(manifest)
    assert "Darcy" not in json.dumps(manifest)
    assert collector.rows[0]["context"] == "Actual retrieved passage."
    collector.clear()
    assert collector.rows == [] and collector.byte_count == 0


@pytest.mark.parametrize("mutation", ["question", "identity", "order", "size"])
def test_invalid_capture_is_rejected(mutation):
    item = observed()
    if mutation == "question":
        item["question"] = "Different question"
    elif mutation == "identity":
        item["response"].pop("id")
    elif mutation == "order":
        item["question_number"] = 2
    else:
        item["response"]["unexpected"] = "x" * (2 * 1024 * 1024)
    with pytest.raises(ValueError):
        capture()(**item)


def test_missing_grounding_is_not_reconstructed():
    item = observed()
    item["response"]["output"].pop()
    collector = capture()
    collector(**item)
    assert collector.rows[0]["grounding_status"] == "unavailable"
    assert collector.rows[0]["context"] == ""
    batch = {"run_id": "run-1", "execution_status": "completed", "questions_count": 1,
             "metrics": [item["metric"]],
             "allocations": [{"question_number": 1, "model_allocation_usd": None}]}
    batch["evidence"] = {"content_hash": digest(
        {key: value for key, value in batch.items() if key != "evidence"}
    )}
    dataset = {
        "schema_version": "rag-qna-dataset.v1",
        "dataset_id": "test", "revision": "1", "segment_revision": "1",
        "family_revision": "1", "evidence_status": "proposed",
        "human_review_status": "pending", "heldout_scored_proof": False,
        "operational_admission": False,
        "sources": [{"source_id": "source-1", "file": "source.txt", "sha256": "a" * 64}],
        "cases": [],
    }
    for index, segment in enumerate(("factual", "synthesis", "cross-book", "ambiguous", "unanswerable")):
        dataset["cases"].append({
            "case_id": "case-1" if index == 0 else f"case-{index + 1}",
            "family_id": f"family-{index + 1}", "segment_id": segment,
            "partition": "development", "question": "Who?" if index == 0 else f"Question {index}",
            "expected_answer": "Expected", "human_review_status": "pending",
            "expected_answer_status": "proposed",
            "answer_behavior": "abstain" if segment == "unanswerable" else "clarify" if segment == "ambiguous" else "answer",
            "rubric": ["Use supplied evidence."],
            "source_locators": [{"source_id": "source-1", "locator": "chapter"}],
        })
    from rag.qna_dataset import content_hash
    dataset["content_hash"] = content_hash(dataset)
    prepared = prepare_evaluation(dataset, collector, batch)
    assert prepared["items"][0]["context"] == NO_RETRIEVED_CONTEXT
    assert prepared["attempts"][0]["grounding_context"] == []
