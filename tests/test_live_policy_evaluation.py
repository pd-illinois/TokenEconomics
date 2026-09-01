from __future__ import annotations

from costgov.policy_candidates import PolicyCandidate
from rag.foundry_trajectory_adapter import _retrieved_document_count
from rag.provision_retrieval_arms import ARMS, _knowledge_base_body
from rag.run_live_policy_evaluation import _build_tasks, _retrieval_limit, _score


def test_live_task_set_has_sixty_per_material_segment() -> None:
    golden = {
        "cases": [
            {
                "id": "e1",
                "difficulty": "easy",
                "question": "q",
                "must_include": ["a"],
            },
            {
                "id": "h1",
                "difficulty": "hard",
                "question": "q",
                "must_include": ["b"],
            },
        ]
    }
    tasks = _build_tasks(golden)
    assert len([item for item in tasks if item["segment_id"] == "rag-easy"]) == 60
    assert len([item for item in tasks if item["segment_id"] == "rag-hard"]) == 60


def test_explicit_ground_truth_score_is_not_acceptance_probability() -> None:
    score, evidence = _score(
        "Walton writes letters containing Victor's story.",
        ["walton", "letters", "victor"],
    )
    assert score == 1.0
    assert evidence["matched"] == ["walton", "letters", "victor"]


def test_retrieval_arms_set_distinct_output_document_limits() -> None:
    assert [
        _knowledge_base_body(arm)["retrieveDefaults"]["maxOutputDocuments"]
        for arm in ARMS
    ] == [4, 1]
    assert _retrieved_document_count("Retrieved 1 document.") == 1
    assert (
        _retrieved_document_count(
            "Retrieved 2 documents.Retrieved 2 documents."
        )
        == 2
    )


def test_candidate_limit_uses_enforced_knowledge_base_control() -> None:
    candidate = PolicyCandidate.from_dict(
        {
            "schema_version": "policy-candidate.v1",
            "candidate_id": "candidate",
            "version": "1",
            "status": "proposed",
            "created_at": "2026-09-01T00:00:00+00:00",
            "experiment_id": "experiment",
            "experiment_revision": "1",
            "meter_stack_id": "stack",
            "meter_stack_version": "1",
            "meter_stack_content_hash": "a" * 64,
            "controls": [
                {
                    "control_id": "retrieval",
                    "kind": "retrieval",
                    "path": "execution.retrieval.max_output_documents",
                    "value": 1,
                    "authority": "workload_adapter",
                    "capability": "retrieval_configuration",
                    "enforcement_scope": "runtime_enforced",
                }
            ],
        }
    )
    assert _retrieval_limit(candidate) == 1
