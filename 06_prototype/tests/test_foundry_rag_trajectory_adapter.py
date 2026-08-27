from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from costgov.trajectory_contracts import StepKind, TrajectoryStore
from rag.foundry_trajectory_adapter import (
    FoundryAgentConfig,
    FoundryRagTrajectoryAdapter,
    FoundryTrajectoryRequest,
)


def _admission() -> dict:
    return {
        "status": "admitted",
        "execution": {"routing_mode": "balanced"},
        "trajectory_contract": {
            "schema_version": "trajectory-envelope.v1",
            "workload": {
                "workload_id": "books-rag",
                "version": "workload.v1",
            },
            "segment_schema_version": "segment.v1",
            "prediction_binding": {
                "prediction_id": "prediction-001",
                "receipt_id": "receipt-001",
                "schema_version": "5.0",
                "content_hash": "a" * 64,
            },
            "policy_binding": {
                "policy_id": "tokengov-production",
                "version": "2026-08-21.1",
                "content_hash": "b" * 64,
                "source": "azure_app_configuration",
                "label": "production",
                "etag": "etag-foundry-rag-1",
            },
        },
    }


def _request() -> FoundryTrajectoryRequest:
    return FoundryTrajectoryRequest(
        run_id="run-foundry-001",
        report_id="RPT-FOUNDRY-001",
        task_id="task-foundry-001",
        trajectory_id="trajectory-foundry-001",
        trace_id="0123456789abcdef0123456789abcdef",
        segment_id="hard-synthesis",
        segment_version="segment.v1",
        question="Compare the treatment of social class in two indexed books.",
        task_created_at="2026-08-21T15:00:00+00:00",
    )


class _FakeConversations:
    def __init__(self) -> None:
        self.calls = 0

    def create(self):
        self.calls += 1
        return SimpleNamespace(id="conv-foundry-001")


class _FakeResponses:
    def __init__(self) -> None:
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = {
            "id": "resp-foundry-001",
            "status": "completed",
            "model": "rag-agent-runtime-gpt-4-1-mini",
            "output": [
                {
                    "id": "item-tools-001",
                    "type": "mcp_list_tools",
                    "server_label": "books-knowledge-base",
                    "tools": [{"name": "knowledge_base_retrieve"}],
                },
                {
                    "id": "item-retrieval-001",
                    "type": "mcp_call",
                    "server_label": "books-knowledge-base",
                    "name": "knowledge_base_retrieve",
                    "arguments": json.dumps({"query": "social class comparison"}),
                    "output": "sensitive retrieved corpus passage",
                    "status": "completed",
                },
                {
                    "id": "item-message-001",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "The books contrast inherited rank with economic mobility.",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "title": "Indexed source",
                                    "url": "mcp://searchindex/books/source-1",
                                }
                            ],
                        }
                    ],
                },
            ],
            "usage": {
                "input_tokens": 11759,
                "output_tokens": 337,
                "total_tokens": 12096,
            },
        }
        return SimpleNamespace(
            id=payload["id"],
            status=payload["status"],
            output_text=(
                "The books contrast inherited rank with economic mobility."
            ),
            model_dump=lambda: payload,
        )


class _FakeClient:
    def __init__(self) -> None:
        self.conversations = _FakeConversations()
        self.responses = _FakeResponses()


def _evidence(step) -> dict:
    return {item.key: json.loads(item.value_json) for item in step.evidence}


def test_adapter_fails_closed_before_remote_execution(tmp_path):
    client = _FakeClient()
    invalid = _admission()
    invalid["trajectory_contract"]["policy_binding"]["etag"] = ""
    adapter = FoundryRagTrajectoryAdapter(
        client,
        FoundryAgentConfig(
            project_endpoint="https://example.services.ai.azure.com/api/projects/test",
            agent_name="tokengov-books-rag-agent",
            agent_version="2",
        ),
        evidence_status="simulated",
    )

    with pytest.raises(ValueError, match="policy|etag|admitted"):
        adapter.capture(
            request=_request(),
            admission=invalid,
            store=TrajectoryStore(tmp_path),
        )

    assert client.conversations.calls == 0
    assert client.responses.calls == []


def test_adapter_rejects_segment_contract_mismatch_before_remote_execution(
    tmp_path,
):
    client = _FakeClient()
    request = _request()
    request = FoundryTrajectoryRequest(
        **{
            **request.__dict__,
            "segment_version": "segment.v2",
        }
    )
    adapter = FoundryRagTrajectoryAdapter(
        client,
        FoundryAgentConfig(
            project_endpoint="https://example.services.ai.azure.com/api/projects/test",
            agent_name="tokengov-books-rag-agent",
            agent_version="2",
        ),
        evidence_status="simulated",
    )

    with pytest.raises(ValueError, match="segment_version"):
        adapter.capture(
            request=request,
            admission=_admission(),
            store=TrajectoryStore(tmp_path),
        )

    assert client.conversations.calls == 0
    assert client.responses.calls == []


def test_adapter_translates_foundry_items_to_immutable_generic_steps(tmp_path):
    client = _FakeClient()
    adapter = FoundryRagTrajectoryAdapter(
        client,
        FoundryAgentConfig(
            project_endpoint="https://example.services.ai.azure.com/api/projects/test",
            agent_name="tokengov-books-rag-agent",
            agent_version="2",
        ),
        evidence_status="simulated",
    )

    capture = adapter.capture(
        request=_request(),
        admission=_admission(),
        store=TrajectoryStore(tmp_path),
    )

    envelope = capture.record.envelope
    assert capture.conversation_id == "conv-foundry-001"
    assert capture.response_id == "resp-foundry-001"
    assert envelope.policy_binding.etag == "etag-foundry-rag-1"
    assert envelope.prediction_binding.receipt_id == "receipt-001"
    assert envelope.task.segment.segment_id == "hard-synthesis"
    assert [step.kind for step in envelope.steps] == [
        StepKind.ITERATION,
        StepKind.TOOL,
        StepKind.RETRIEVAL,
        StepKind.MODEL,
    ]

    root = _evidence(envelope.steps[0])
    retrieval = _evidence(envelope.steps[2])
    model = _evidence(envelope.steps[3])
    assert root["evidence_status"] == "simulated"
    assert root["agent_name"] == "tokengov-books-rag-agent"
    assert root["agent_version"] == "2"
    assert root["resource_meter_status"] == "unavailable"
    assert root["resource_meter_cost_usd"] is None
    assert retrieval["operation_count"] == 1
    assert retrieval["resource_meter_status"] == "unavailable"
    assert len(retrieval["provider_output_sha256"]) == 64
    assert model["input_tokens"] == 11759
    assert model["output_tokens"] == 337
    assert model["total_tokens"] == 12096
    assert model["citations"] == [
        {
            "title": "Indexed source",
            "url": "mcp://searchindex/books/source-1",
        }
    ]
    assert model["model_cost_status"] == "unpriced"
    assert capture.response_text == (
        "The books contrast inherited rank with economic mobility."
    )

    canonical = envelope.to_canonical_json()
    assert "sensitive retrieved corpus passage" not in canonical
    reopened = TrajectoryStore(tmp_path).get(envelope.trajectory_id)
    assert reopened == capture.record

    invocation = client.responses.calls[0]
    assert invocation["conversation"] == "conv-foundry-001"
    assert invocation["input"] == _request().question
    assert invocation["extra_body"]["agent_reference"] == {
        "type": "agent_reference",
        "name": "tokengov-books-rag-agent",
        "version": "2",
    }


def test_adapter_rejects_non_terminal_provider_response(tmp_path):
    client = _FakeClient()
    original_create = client.responses.create

    def incomplete(**kwargs):
        response = original_create(**kwargs)
        response.status = "in_progress"
        payload = response.model_dump()
        payload["status"] = "in_progress"
        response.model_dump = lambda: payload
        return response

    client.responses.create = incomplete
    adapter = FoundryRagTrajectoryAdapter(
        client,
        FoundryAgentConfig(
            project_endpoint="https://example.services.ai.azure.com/api/projects/test",
            agent_name="tokengov-books-rag-agent",
            agent_version="2",
        ),
        evidence_status="simulated",
    )

    with pytest.raises(RuntimeError, match="terminal"):
        adapter.capture(
            request=_request(),
            admission=_admission(),
            store=TrajectoryStore(tmp_path),
        )

    assert list(tmp_path.glob("*.json")) == []
