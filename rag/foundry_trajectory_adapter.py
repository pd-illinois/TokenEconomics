"""Translate Microsoft Foundry response items into trajectory-envelope.v1."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from costgov.trajectory_contracts import (
    EvidenceField,
    SegmentIdentity,
    StepEvidence,
    StepKind,
    StepStatus,
    TaskIdentity,
    TrajectoryContractBinding,
    TrajectoryEnvelope,
    TrajectoryRecord,
    TrajectoryStore,
)

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _stable_id(value: object, field: str) -> str:
    text = _required_text(value, field)
    if not _ID_PATTERN.fullmatch(text):
        raise ValueError(f"{field} must be a stable identifier")
    return text


def _utc_text(value: object, field: str) -> str:
    text = _required_text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(
        parsed
    ):
        raise ValueError(f"{field} must be an ISO-8601 UTC timestamp")
    return text


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("adapter clock must return a timezone-aware timestamp")
    return value.astimezone(timezone.utc).isoformat()


def _dump(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, Mapping):
            return dict(dumped)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        dumped = to_dict()
        if isinstance(dumped, Mapping):
            return dict(dumped)
    raise TypeError("Foundry response must expose model_dump() or a mapping")


def _sha256(value: object) -> str:
    if isinstance(value, str):
        encoded = value.encode("utf-8")
    else:
        encoded = json.dumps(
            value,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _step_id(response_id: str, sequence: int, item_type: str) -> str:
    digest = _sha256(f"{response_id}:{sequence}:{item_type}")[:24]
    return f"step-{digest}"


def _status(value: object) -> StepStatus:
    if value in {None, "completed", "succeeded"}:
        return StepStatus.COMPLETED
    if value in {"failed", "incomplete", "cancelled"}:
        return StepStatus.FAILED
    if value in {"queued", "in_progress", "started"}:
        return StepStatus.STARTED
    raise ValueError(f"unsupported Foundry output status: {value}")


def _evidence(**values: object) -> tuple[EvidenceField, ...]:
    return tuple(
        EvidenceField.from_value(key, value) for key, value in values.items()
    )


def _citation_list(item: Mapping[str, Any]) -> list[dict[str, str]]:
    citations: list[dict[str, str]] = []
    for content in item.get("content") or []:
        if not isinstance(content, Mapping):
            continue
        for annotation in content.get("annotations") or []:
            if not isinstance(annotation, Mapping):
                continue
            url = annotation.get("url")
            if not isinstance(url, str) or not url:
                continue
            title = annotation.get("title")
            citations.append(
                {
                    "title": title if isinstance(title, str) else "",
                    "url": url,
                }
            )
    return citations


def _message_text(item: Mapping[str, Any]) -> str:
    chunks: list[str] = []
    for content in item.get("content") or []:
        if not isinstance(content, Mapping):
            continue
        text = content.get("text")
        if isinstance(text, str):
            chunks.append(text)
    return "\n".join(chunks)


@dataclass(frozen=True)
class FoundryAgentConfig:
    project_endpoint: str
    agent_name: str
    agent_version: str

    def __post_init__(self) -> None:
        endpoint = _required_text(self.project_endpoint, "project_endpoint")
        if not endpoint.startswith("https://"):
            raise ValueError("project_endpoint must use HTTPS")
        _required_text(self.agent_name, "agent_name")
        _required_text(self.agent_version, "agent_version")


@dataclass(frozen=True)
class FoundryTrajectoryRequest:
    run_id: str
    report_id: str
    task_id: str
    trajectory_id: str
    trace_id: str
    segment_id: str
    segment_version: str
    question: str
    task_created_at: str

    def __post_init__(self) -> None:
        for field in (
            "run_id",
            "report_id",
            "task_id",
            "trajectory_id",
            "trace_id",
            "segment_id",
            "segment_version",
        ):
            _stable_id(getattr(self, field), field)
        _required_text(self.question, "question")
        _utc_text(self.task_created_at, "task_created_at")


@dataclass(frozen=True)
class FoundryTrajectoryCapture:
    conversation_id: str
    response_id: str
    response_text: str
    record: TrajectoryRecord


class FoundryRagTrajectoryAdapter:
    """Invoke one admitted Foundry agent task and persist generic evidence."""

    def __init__(
        self,
        openai_client: object,
        config: FoundryAgentConfig,
        *,
        evidence_status: str = "measured_live",
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.openai = openai_client
        self.config = config
        self.evidence_status = _required_text(
            evidence_status, "evidence_status"
        )
        self.clock = clock

    @staticmethod
    def _binding(admission: object) -> TrajectoryContractBinding:
        if not isinstance(admission, Mapping):
            raise ValueError("an admitted policy binding is required")
        if admission.get("status") != "admitted" or not admission.get(
            "execution"
        ):
            raise ValueError("an admitted policy binding is required")
        binding = TrajectoryContractBinding.from_dict(
            admission.get("trajectory_contract")
        )
        if binding.prediction_binding is None or binding.policy_binding is None:
            raise ValueError(
                "execution requires prediction and policy trajectory bindings"
            )
        return binding

    def capture(
        self,
        *,
        request: FoundryTrajectoryRequest,
        admission: object,
        store: TrajectoryStore,
    ) -> FoundryTrajectoryCapture:
        binding = self._binding(admission)
        if request.segment_version != binding.segment_schema_version:
            raise ValueError(
                "request segment_version must match the admitted trajectory "
                "segment schema version"
            )
        task = TaskIdentity(
            task_id=request.task_id,
            report_id=request.report_id,
            workload=binding.workload,
            segment=SegmentIdentity(
                segment_id=request.segment_id,
                version=request.segment_version,
                attributes=(
                    EvidenceField.from_value(
                        "reference_workload", "foundry_rag"
                    ),
                ),
            ),
            created_at=request.task_created_at,
        )
        started_at = _timestamp(self.clock())
        if datetime.fromisoformat(
            request.task_created_at.replace("Z", "+00:00")
        ) > datetime.fromisoformat(started_at):
            raise ValueError(
                "task_created_at must not follow trajectory start"
            )

        conversation = self.openai.conversations.create()
        conversation_id = _required_text(
            getattr(conversation, "id", None), "Foundry conversation id"
        )
        response = self.openai.responses.create(
            conversation=conversation_id,
            input=request.question,
            extra_body={
                "agent_reference": {
                    "type": "agent_reference",
                    "name": self.config.agent_name,
                    "version": self.config.agent_version,
                }
            },
        )
        response_payload = _dump(response)
        response_id = _required_text(
            response_payload.get("id") or getattr(response, "id", None),
            "Foundry response id",
        )
        response_status = response_payload.get("status") or getattr(
            response, "status", None
        )
        if response_status != "completed":
            raise RuntimeError(
                "Foundry response must be terminal and completed before "
                "trajectory persistence"
            )
        ended_at = _timestamp(self.clock())

        raw_output = response_payload.get("output") or []
        if not isinstance(raw_output, list):
            raise TypeError("Foundry response output must be an array")
        usage = response_payload.get("usage")
        usage = dict(usage) if isinstance(usage, Mapping) else {}
        for field in ("input_tokens", "output_tokens", "total_tokens"):
            token_count = usage.get(field)
            if (
                isinstance(token_count, bool)
                or not isinstance(token_count, int)
                or token_count < 0
            ):
                raise RuntimeError(
                    f"Foundry response usage.{field} is required"
                )
        model = response_payload.get("model")
        response_text = getattr(response, "output_text", None)
        if not isinstance(response_text, str):
            response_text = "\n".join(
                text
                for item in raw_output
                if isinstance(item, Mapping)
                for text in [_message_text(item)]
                if text
            )

        root_step_id = _step_id(response_id, 1, "agent_response")
        steps: list[StepEvidence] = [
            StepEvidence(
                step_id=root_step_id,
                sequence=1,
                kind=StepKind.ITERATION,
                status=StepStatus.COMPLETED,
                operation="foundry_agent_response",
                started_at=started_at,
                ended_at=ended_at,
                evidence=_evidence(
                    evidence_status=self.evidence_status,
                    provider="microsoft_foundry",
                    conversation_id=conversation_id,
                    response_id=response_id,
                    agent_name=self.config.agent_name,
                    agent_version=self.config.agent_version,
                    response_status=response_status,
                    model_token_status="provider_reported",
                    resource_meter_status="unavailable",
                    resource_meter_cost_usd=None,
                    timing_scope="complete_response",
                ),
            )
        ]

        for item in raw_output:
            if not isinstance(item, Mapping):
                raise TypeError("Foundry output items must be objects")
            steps.append(
                self._translate_item(
                    item=item,
                    sequence=len(steps) + 1,
                    root_step_id=root_step_id,
                    response_id=response_id,
                    model=model,
                    usage=usage,
                    started_at=started_at,
                    ended_at=ended_at,
                )
            )
        step_kinds = {step.kind for step in steps}
        if StepKind.RETRIEVAL not in step_kinds or StepKind.MODEL not in step_kinds:
            raise RuntimeError(
                "Foundry RAG proof requires completed retrieval and model "
                "response items"
            )

        recorded_at = _timestamp(self.clock())
        envelope = TrajectoryEnvelope(
            schema_version=binding.schema_version,
            trajectory_id=request.trajectory_id,
            run_id=request.run_id,
            trace_id=request.trace_id,
            task=task,
            prediction_binding=binding.prediction_binding,
            policy_binding=binding.policy_binding,
            status="completed",
            started_at=started_at,
            ended_at=ended_at,
            recorded_at=recorded_at,
            steps=tuple(steps),
        )
        record = store.append(envelope)
        return FoundryTrajectoryCapture(
            conversation_id=conversation_id,
            response_id=response_id,
            response_text=response_text,
            record=record,
        )

    def _translate_item(
        self,
        *,
        item: Mapping[str, Any],
        sequence: int,
        root_step_id: str,
        response_id: str,
        model: object,
        usage: Mapping[str, Any],
        started_at: str,
        ended_at: str,
    ) -> StepEvidence:
        item_type = str(item.get("type") or "unknown")
        item_status = _status(item.get("status"))
        if item_status is StepStatus.STARTED:
            raise RuntimeError(
                "completed Foundry response contains a non-terminal output item"
            )
        common = {
            "provider_item_id": item.get("id"),
            "provider_item_type": item_type,
            "timing_scope": "complete_response",
        }

        if item_type == "mcp_list_tools":
            tools = item.get("tools") or []
            tool_names = [
                tool.get("name")
                for tool in tools
                if isinstance(tool, Mapping)
                and isinstance(tool.get("name"), str)
            ]
            kind = StepKind.TOOL
            operation = "mcp_list_tools"
            values = {
                **common,
                "server_label": item.get("server_label"),
                "tool_names": tool_names,
            }
        elif item_type == "mcp_call":
            operation = str(item.get("name") or "mcp_call")
            kind = (
                StepKind.RETRIEVAL
                if operation == "knowledge_base_retrieve"
                else StepKind.TOOL
            )
            provider_output = item.get("output")
            values = {
                **common,
                "server_label": item.get("server_label"),
                "tool_name": operation,
                "arguments_sha256": _sha256(item.get("arguments") or ""),
                "provider_output_sha256": _sha256(provider_output or ""),
                "operation_count": 1,
                "resource_meter_status": "unavailable",
                "resource_meter_cost_usd": None,
            }
        elif item_type == "message":
            kind = StepKind.MODEL
            operation = "response_synthesis"
            values = {
                **common,
                "role": item.get("role"),
                "model": model,
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "model_cost_status": "unpriced",
                "model_cost_usd": None,
                "response_text": _message_text(item),
                "citations": _citation_list(item),
            }
        else:
            kind = StepKind.TOOL
            operation = f"foundry_{item_type}"
            values = common

        return StepEvidence(
            step_id=_step_id(response_id, sequence, item_type),
            sequence=sequence,
            kind=kind,
            status=item_status,
            operation=operation,
            started_at=started_at,
            ended_at=(
                ended_at
                if item_status
                in {
                    StepStatus.COMPLETED,
                    StepStatus.FAILED,
                    StepStatus.SKIPPED,
                }
                else None
            ),
            parent_step_id=root_step_id,
            evidence=_evidence(**values),
        )


def create_foundry_openai_client(
    config: FoundryAgentConfig,
    credential: object | None = None,
):
    """Create the official Foundry project/OpenAI client with Entra ID."""

    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
        raise RuntimeError(
            "azure-ai-projects and azure-identity are required for live capture"
        ) from exc

    resolved_credential = credential or DefaultAzureCredential()
    project = AIProjectClient(
        endpoint=config.project_endpoint,
        credential=resolved_credential,
    )
    return project.get_openai_client()
