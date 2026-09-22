"""Explicit workload-owned destination for captured-response evaluation.

The versioned binding is operator intent, not deployment or grading proof. A
read-only project deployment GET must verify it before dispatch/upload. This
does not change the source agent, policy authority, acceptance or cost claims.
"""

from collections.abc import Mapping

from rag.foundry_evaluation_transport import JUDGES, PROJECT_ENDPOINT, TransportError

TARGET_SCHEMA_VERSION = "qna-evaluation-target.v1"
EVALUATION_PROJECT_ENDPOINT = (
    "https://ai-eval-xbk6ickycmp22.services.ai.azure.com"
    "/api/projects/ai-project-tokengov-eval"
)
_FIELDS = {"schema_version", "project_endpoint", "judge_deployment", "judge_model", "judge_sku"}


class EvaluationTargetReadinessError(PermissionError):
    """Content-free target inventory denial, never permission to retry a POST."""


def validate_evaluation_target(value):
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise TransportError("Evaluation target requires exactly the versioned destination fields")
    if (value["schema_version"] != TARGET_SCHEMA_VERSION
            or value["project_endpoint"] not in (PROJECT_ENDPOINT, EVALUATION_PROJECT_ENDPOINT)):
        raise TransportError("Evaluation target version or project endpoint is not allowlisted")
    deployment = value["judge_deployment"]
    if not isinstance(deployment, str) or deployment not in JUDGES:
        raise TransportError("Evaluation target judge deployment is not allowlisted")
    if (value["judge_model"] != list(JUDGES[deployment])
            or value["judge_sku"] != "GlobalStandard"
            or (value["project_endpoint"] == EVALUATION_PROJECT_ENDPOINT
                and deployment != "rag-agent-runtime-gpt-4-1-mini")):
        raise TransportError("Evaluation target judge binding is not allowlisted")
    return {**value, "judge_model": list(value["judge_model"])}


def check_evaluation_target(project, target):
    """One bounded inventory GET, with SDK retries and content logging disabled.

    AIProjectClient stores its actual endpoint in _config.endpoint. Check that
    before the GET, rather than trusting a caller-supplied endpoint annotation.
    ModelDeployment is an SDK Mapping with modelName/modelVersion/sku wire keys.
    Inventory verification is not a claim that a grader can execute successfully.
    """
    target = validate_evaluation_target(target)
    if getattr(getattr(project, "_config", None), "endpoint", None) != target["project_endpoint"]:
        return {"ready": False, "blockers": [{"code": "evaluation_project_endpoint_mismatch"}]}
    try:
        model = project.deployments.get(
            target["judge_deployment"], retry_total=0, connection_timeout=10,
            read_timeout=15, logging_enable=False, tracing_enable=False,
        )
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        code = ("evaluation_judge_read_denied" if status in (401, 403)
                else "evaluation_judge_read_unavailable")
        return {"ready": False, "blockers": [{"code": code}]}
    if (not isinstance(model, Mapping)
            or model.get("type") != "ModelDeployment"
            or model.get("name") != target["judge_deployment"]
            or [model.get("modelName"), model.get("modelVersion")] != target["judge_model"]
            or model.get("modelPublisher") != "OpenAI"
            or not isinstance(model.get("sku"), Mapping)
            or model["sku"].get("name") != target["judge_sku"]
            or model.get("connectionName")):
        return {"ready": False, "blockers": [{"code": "evaluation_judge_binding_changed"}]}
    return {"ready": True, "blockers": []}


def authorize_evaluation_target(project, target):
    status = check_evaluation_target(project, target)
    if not status["ready"]:
        raise EvaluationTargetReadinessError(
            "Evaluation target readiness denied: " + status["blockers"][0]["code"],
        )
