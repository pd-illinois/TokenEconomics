from copy import deepcopy
from types import SimpleNamespace

import pytest
from azure.ai.projects.models import ModelDeployment

from rag.foundry_evaluation_transport import PROJECT_ENDPOINT, TransportError
from rag.qna_evaluation_target import (
    EVALUATION_PROJECT_ENDPOINT, TARGET_SCHEMA_VERSION, EvaluationTargetReadinessError,
    authorize_evaluation_target, check_evaluation_target, validate_evaluation_target,
)


def target_binding():
    return {
        "schema_version": TARGET_SCHEMA_VERSION,
        "project_endpoint": EVALUATION_PROJECT_ENDPOINT,
        "judge_deployment": "rag-agent-runtime-gpt-4-1-mini",
        "judge_model": ["gpt-4.1-mini", "2025-04-14"],
        "judge_sku": "GlobalStandard",
    }


class FakeProject:
    def __init__(self, target=None):
        self.target = target or target_binding()
        self._config = SimpleNamespace(endpoint=self.target["project_endpoint"])
        self.calls = []
        self.error = None
        self.model = ModelDeployment({
            "type": "ModelDeployment", "name": self.target["judge_deployment"],
            "modelName": self.target["judge_model"][0],
            "modelVersion": self.target["judge_model"][1], "modelPublisher": "OpenAI",
            "sku": {"name": self.target["judge_sku"], "capacity": 100},
        })
        self.deployments = SimpleNamespace(get=self.get)

    def get(self, name, **kwargs):
        self.calls.append((name, kwargs))
        if self.error:
            raise self.error
        return self.model


def test_exact_sdk_model_deployment_read_is_bounded_and_independent_of_agent():
    project = FakeProject()
    assert check_evaluation_target(project, target_binding()) == {"ready": True, "blockers": []}
    assert project.calls == [("rag-agent-runtime-gpt-4-1-mini", {
        "retry_total": 0, "connection_timeout": 10, "read_timeout": 15,
        "logging_enable": False, "tracing_enable": False,
    })]


@pytest.mark.parametrize("field,value", [
    ("schema_version", "qna-evaluation-target.v2"),
    ("project_endpoint", "https://foreign.example/api/projects/eval"),
    ("project_endpoint", EVALUATION_PROJECT_ENDPOINT + "?api_key=secret"),
    ("project_endpoint", EVALUATION_PROJECT_ENDPOINT + "/"),
    ("project_endpoint", EVALUATION_PROJECT_ENDPOINT.replace("https://", "https://user:secret@")),
    ("judge_model", ["gpt-4.1-mini", "new-version"]),
    ("judge_model", ["gpt-4o", "2025-04-14"]),
    ("judge_deployment", "unknown"),
    ("judge_sku", "Standard"),
    ("credential", "secret"),
])
def test_target_intent_rejects_unknown_or_credential_bearing_values(field, value):
    target = {**target_binding(), field: value}
    with pytest.raises(TransportError) as caught:
        validate_evaluation_target(target)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("field,value", [
    ("type", "OtherDeployment"), ("name", "other"),
    ("modelName", "gpt-4o"), ("modelVersion", "unknown"),
    ("modelPublisher", "other"), ("sku", {"name": "Standard"}),
    ("connectionName", "foreign-project-connection"),
])
def test_inventory_drift_is_explicit_denial(field, value):
    project = FakeProject()
    project.model = {**dict(project.model), field: value}
    with pytest.raises(EvaluationTargetReadinessError, match="evaluation_judge_binding_changed"):
        authorize_evaluation_target(project, target_binding())
    assert len(project.calls) == 1


@pytest.mark.parametrize("status,code", [
    (401, "evaluation_judge_read_denied"), (403, "evaluation_judge_read_denied"),
    (404, "evaluation_judge_read_unavailable"), (429, "evaluation_judge_read_unavailable"),
    (None, "evaluation_judge_read_unavailable"),
])
def test_denied_inventory_never_retries_or_exposes_exception_content(status, code):
    project = FakeProject()
    project.error = RuntimeError("secret credential raw content")
    project.error.status_code = status
    with pytest.raises(EvaluationTargetReadinessError, match=code) as caught:
        authorize_evaluation_target(project, target_binding())
    assert "secret" not in str(caught.value)
    assert len(project.calls) == 1


def test_wrong_project_is_rejected_before_credential_use():
    project = FakeProject()
    project._config.endpoint = PROJECT_ENDPOINT
    with pytest.raises(EvaluationTargetReadinessError, match="evaluation_project_endpoint_mismatch"):
        authorize_evaluation_target(project, target_binding())
    assert project.calls == []


def test_validation_copies_binding_without_mutating_caller():
    target = target_binding()
    original = deepcopy(target)
    validated = validate_evaluation_target(target)
    validated["judge_model"][0] = "modified"
    assert target == original
