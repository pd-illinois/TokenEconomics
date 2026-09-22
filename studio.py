"""Local TokenEconomics Studio API and static application server."""

from __future__ import annotations

import json
import hashlib
import math
import os
import re
import secrets
import sys
import threading
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
PREDICTOR_SOURCE = ROOT / "FutureTokenPredictor" / "src"
# Match the CLI/MCP runtime instead of an unrelated global editable installation.
if PREDICTOR_SOURCE.is_dir():
    sys.path.insert(0, str(PREDICTOR_SOURCE))

from costgov.commercial_planning import (
    COMMERCIAL_ROUTES,
    MODEL_ROUTES,
    CommercialPlanClarification,
    attach_foundry_meter_stack,
    build_commercial_result,
)
from costgov.azure_infrastructure import (
    AzureInfrastructureError,
    build_infrastructure_forecast,
    confirm_infrastructure_forecast,
    route_requires_infrastructure,
)
from costgov.consumption_models import consumption_catalog
from costgov.mcp_prediction import McpPredictionError, McpPredictorClient
from costgov.decision_state import DecisionStateStore
from costgov.governance_decisions import (
    GovernanceEvidenceStore,
    build_candidate_constraint_from_run,
    select_candidate,
)
from costgov.github_policy_review import (
    GitHubPolicyReviewClient,
    GitHubPolicyReviewError,
    configured_review_client,
    review_configuration,
)
from costgov.observe_economics import load_observe_economics
from costgov.orchestrator import StudioOrchestrator
from costgov.planning import PlanStore
from costgov.policy_changes import PolicyChangeStore
from costgov.policy_store import PolicyLoadError, load_policy_from_environment
from costgov.reports import ReportStore
from costgov.route_capabilities import route_capability_catalog
from costgov.route_validation import nine_path_validation_matrix
from costgov.reconciliation_evidence import ReconciliationEvidenceStore
from costgov.learning_evidence import LearningEvidenceStore
from costgov.portability_evidence import PortabilityEvidenceStore
from costgov.studio_lifecycle import StudioLifecycle
from costgov.studio_health import StorageHealthCheck, probe_storage

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass
REGISTRY_PATH = ROOT / "studio_runs" / "registry.json"
PLAN_STORE_PATH = ROOT / "studio_plans"
REPORT_STORE_PATH = ROOT / "studio_reports"
POLICY_CHANGE_STORE_PATH = ROOT / "studio_policy_changes"
GOVERNANCE_EVIDENCE_STORE_PATH = ROOT / "studio_governance_evidence"
DECISION_STATE_STORE_PATH = ROOT / "studio_decision_state"
RECONCILIATION_EVIDENCE_STORE_PATH = ROOT / "studio_reconciliation_evidence"
LEARNING_EVIDENCE_STORE_PATH = ROOT / "studio_learning_evidence" / "proofs"
PORTABILITY_EVIDENCE_STORE_PATH = ROOT / "studio_portability_evidence"
LIFECYCLE_STORE_PATH = ROOT / "studio_lifecycle"
WORKLOAD_ADAPTER_PATH = ROOT / "data" / "workload_adapters" / "studio-evidence.v1.json"
BILLING_SOURCE_PATH = ROOT / "data" / "workload_adapters" / "books-billing.v1.json"
BILLING_STORE_PATH = ROOT / "studio_billing_evidence"
RESPONSE_LEARNING_STORE_PATH = ROOT / "studio_response_learning"
_feedback_lock = threading.Lock()
_lock = threading.Lock()
_policy_review_request_token = secrets.token_urlsafe(32)
_lifecycle_request_token = secrets.token_urlsafe(32)
PLANNED_ROUTE_MESSAGE = "Planned for a future release. Only Microsoft Foundry is available in this deployment."
_storage_health = StorageHealthCheck(
    lambda: probe_storage(ROOT, os.environ.get("TOKENECONOMICS_STATE_ROOT")),
)


def _route_is_read_only(route: str) -> bool:
    return os.environ.get("TOKENECONOMICS_FOUNDRY_ONLY", "").lower() == "true" and route != "foundry"


def _studio_consumption_catalog() -> dict:
    catalog = consumption_catalog()
    catalog["studio_availability"] = {
        "schema_version": "studio-route-availability.v1",
        "selectable_routes": [
            item["route_id"] for item in catalog["experiences"]
            if not _route_is_read_only(item["route_id"])
        ],
        "planned_message": PLANNED_ROUTE_MESSAGE,
    }
    return catalog


def _lifecycle_service() -> StudioLifecycle:
    return StudioLifecycle(
        LIFECYCLE_STORE_PATH, PlanStore(PLAN_STORE_PATH), _read_registry(),
        REGISTRY_PATH.parent, json.loads(WORKLOAD_ADAPTER_PATH.read_text(encoding="utf-8")),
    )


def _lifecycle_principal(handler, scope: str) -> str | None:
    """Evaluation and operational authorization have distinct allowlists."""
    if not secrets.compare_digest(handler.headers.get("X-TokenGov-CSRF", ""), _lifecycle_request_token):
        return None
    origin = urlparse(handler.headers.get("Origin", ""))
    if origin.scheme not in {"http", "https"} or origin.netloc != handler.headers.get("Host", ""):
        return None
    prefix = f"TOKENGOV_{scope.upper()}"
    if os.environ.get(f"{prefix}_ALLOW_LOCAL", "").lower() == "true" and handler.client_address[0] in {"127.0.0.1", "::1"}:
        return "local-operator"
    if os.environ.get("TOKENGOV_LIFECYCLE_AUTHENTICATED_INGRESS") != "container_apps":
        return None
    principal = handler.headers.get("X-MS-CLIENT-PRINCIPAL-ID", "").strip()
    allowed = {item.strip() for item in os.environ.get(f"{prefix}_ALLOWED_PRINCIPAL", "").split(",") if item.strip()}
    return principal if principal and principal in allowed else None


def _policy_review_client() -> GitHubPolicyReviewClient:
    return configured_review_client()


def _policy_review_authorized(handler: SimpleHTTPRequestHandler) -> bool:
    request_token = handler.headers.get("X-TokenGov-CSRF", "")
    if not secrets.compare_digest(request_token, _policy_review_request_token):
        return False
    origin = urlparse(handler.headers.get("Origin", ""))
    if (
        origin.scheme not in {"http", "https"}
        or origin.netloc != handler.headers.get("Host", "")
    ):
        return False
    if (
        os.environ.get("TOKENGOV_REVIEW_ALLOW_LOCAL", "").lower() == "true"
        and handler.client_address[0] in {"127.0.0.1", "::1"}
    ):
        return True
    if (
        os.environ.get("TOKENGOV_REVIEW_AUTHENTICATED_INGRESS", "").lower()
        != "container_apps"
    ):
        return False
    allowed = {
        value.strip()
        for value in os.environ.get(
            "TOKENGOV_REVIEW_ALLOWED_PRINCIPAL", ""
        ).split(",")
        if value.strip()
    }
    principal = handler.headers.get("X-MS-CLIENT-PRINCIPAL-ID", "").strip()
    return bool(principal and principal in allowed)


def _read_registry() -> dict:
    if not REGISTRY_PATH.exists():
        return {}
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _write_registry(registry: dict) -> None:
    REGISTRY_PATH.parent.mkdir(exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2), encoding="utf-8")


def _set_run(registry_key: str, **updates) -> None:
    with _lock:
        registry = _read_registry()
        registry.setdefault(registry_key, {}).update(updates)
        _write_registry(registry)


def _report_run_summaries(report: dict, registry: dict) -> list[dict]:
    """Project recorded run metadata without rewriting historical report links."""
    summaries = []
    for artifact in report.get("artifacts", {}).get("runs", []):
        summary = dict(artifact)
        registered = registry.get(artifact["id"], {})
        result = registered.get("result", {})
        if registered.get("report_id") == report["report_id"]:
            for field in (
                "created_at", "started_at", "questions_count", "schema_version",
                "execution_status", "evidence_classification",
            ):
                if result.get(field) is not None:
                    summary[field] = result[field]
        summaries.append(summary)
    return summaries


def _portfolio_number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def _portfolio_report_summary(report: dict, registry: dict, plan_store: PlanStore) -> dict:
    """Build a bounded cross-report projection from existing immutable evidence."""
    artifacts = report.get("artifacts", {})
    forecasts = []
    projection_errors = []
    for artifact in artifacts.get("plans", []):
        plan_id = artifact.get("id")
        if not isinstance(plan_id, str) or not plan_id:
            projection_errors.append("invalid_plan_reference")
            continue
        try:
            receipt = plan_store.get_receipt(plan_id)
        except (OSError, ValueError, KeyError):
            projection_errors.append("forecast_evidence_unavailable")
            continue
        if not receipt:
            continue
        prediction = receipt.get("prediction") if isinstance(receipt.get("prediction"), dict) else {}
        annual = prediction.get("annual_cost") if isinstance(prediction.get("annual_cost"), dict) else {}
        monthly = prediction.get("monthly_cost") if isinstance(prediction.get("monthly_cost"), dict) else {}
        tokens = prediction.get("tokens_per_call") if isinstance(prediction.get("tokens_per_call"), dict) else {}
        forecasts.append(
            {
                "plan_id": plan_id,
                "receipt_id": receipt.get("receipt_id"),
                "created_at": receipt.get("created_at"),
                "provider": prediction.get("provider"),
                "model": prediction.get("model"),
                "annual_cost_mean_usd": _portfolio_number(annual.get("mean")),
                "monthly_cost_mean_usd": _portfolio_number(monthly.get("mean")),
                "tokens_per_call": _portfolio_number(tokens.get("total")),
                "classification": "modeled",
                "scope": "forecast_declared_scope",
            }
        )

    runs = _report_run_summaries(report, registry)
    measured_runs = [
        run
        for run in runs
        if str(
            run.get("evidence_classification")
            or run.get("evidence_status")
            or ""
        ).startswith("measured")
    ]
    completed_measured_runs = [
        run
        for run in measured_runs
        if (run.get("execution_status") or run.get("status")) in {"complete", "completed"}
    ]
    evaluated_runs = [
        run
        for run in runs
        if run.get("evaluation_id") or run.get("quality_record_id")
    ]
    failed_runs = [
        run
        for run in runs
        if (run.get("execution_status") or run.get("status"))
        in {"blocked", "failed", "failed_or_partial", "partial"}
    ]
    publication_failures = [
        run for run in runs if run.get("cloud_status") in {"failed", "publication_failed"}
    ]
    handoffs = artifacts.get("govern_handoffs", [])
    blocked_handoffs = [
        handoff
        for handoff in handoffs
        if handoff.get("status") in {"blocked", "rejected"}
    ]
    reconciliations = artifacts.get("reconciliations", [])
    learning_proofs = artifacts.get("learning_proofs", [])
    govern_decisions = artifacts.get("govern_decisions", [])

    readiness_stage = 0
    if forecasts:
        readiness_stage = 1
    if handoffs:
        readiness_stage = 2
    if completed_measured_runs:
        readiness_stage = 3
    if evaluated_runs:
        readiness_stage = 4
    if reconciliations:
        readiness_stage = 5
    if learning_proofs:
        readiness_stage = 6
    readiness_labels = (
        "New",
        "Forecasted",
        "Policy reviewed",
        "Measured",
        "Quality reviewed",
        "Reconciled",
        "Learning evidence",
    )

    annual_values = [
        item["annual_cost_mean_usd"]
        for item in forecasts
        if item["annual_cost_mean_usd"] is not None
    ]
    token_values = [
        item["tokens_per_call"]
        for item in forecasts
        if item["tokens_per_call"] is not None
    ]
    highest_forecast = max(
        (item for item in forecasts if item["annual_cost_mean_usd"] is not None),
        key=lambda item: item["annual_cost_mean_usd"],
        default=None,
    )

    attention = []

    def add_attention(code, severity, title, detail, destination):
        attention.append(
            {
                "code": code,
                "severity": severity,
                "title": title,
                "detail": detail,
                "destination": destination,
            }
        )

    if projection_errors:
        add_attention(
            "portfolio_evidence_partial",
            "warning",
            "Some forecast evidence is unavailable",
            "The portfolio projection excluded invalid or unreadable forecast evidence rather than treating it as zero.",
            "plan",
        )
    if publication_failures:
        add_attention(
            "publication_failed",
            "danger",
            "Evidence publication needs review",
            f"{len(publication_failures)} run(s) record a failed cloud publication state.",
            "runs",
        )
    if failed_runs:
        add_attention(
            "execution_needs_review",
            "danger",
            "Execution attempts need review",
            f"{len(failed_runs)} run(s) are blocked, failed, or partial.",
            "runs",
        )
    if blocked_handoffs:
        add_attention(
            "policy_review_blocked",
            "warning",
            "Policy readiness is blocked",
            f"{len(blocked_handoffs)} forecast handoff(s) are blocked or rejected.",
            "policy",
        )
    if not forecasts:
        add_attention(
            "forecast_required",
            "info",
            "Forecast required",
            "This assessment has no immutable forecast receipt.",
            "plan",
        )
    elif not completed_measured_runs:
        add_attention(
            "measurement_required",
            "warning",
            "Measured execution is not available",
            "Forecast evidence exists, but no completed measured run is available.",
            "runs",
        )
    elif not evaluated_runs:
        add_attention(
            "quality_review_required",
            "warning",
            "Quality evidence is not available",
            "Measured execution exists, but no linked quality review is recorded.",
            "runs",
        )
    elif not reconciliations:
        add_attention(
            "reconciliation_required",
            "info",
            "Reconciliation is pending",
            "Quality-reviewed execution exists, but no report reconciliation is linked.",
            "govern",
        )
    elif not learning_proofs:
        add_attention(
            "learning_evidence_required",
            "info",
            "Learning evidence is pending",
            "Reconciliation exists, but no compatible learning proof is linked.",
            "govern",
        )

    if any(item["severity"] == "danger" for item in attention):
        status = "needs_attention"
    elif readiness_stage >= 5:
        status = "reconciled"
    elif readiness_stage >= 4:
        status = "quality_reviewed"
    elif readiness_stage >= 3:
        status = "measured"
    elif readiness_stage >= 2:
        status = "policy_reviewed"
    elif readiness_stage >= 1:
        status = "forecasted"
    else:
        status = "new"

    return {
        "schema_version": "studio-portfolio-report.v1",
        "classification": "read_only_projection",
        "status": status,
        "readiness": {
            "stage": readiness_stage,
            "stage_count": 6,
            "label": readiness_labels[readiness_stage],
        },
        "counts": {
            "forecasts": len(forecasts),
            "policy_handoffs": len(handoffs),
            "runs": len(runs),
            "measured_runs": len(measured_runs),
            "completed_measured_runs": len(completed_measured_runs),
            "quality_reviewed_runs": len(evaluated_runs),
            "decisions": len(govern_decisions),
            "reconciliations": len(reconciliations),
            "learning_proofs": len(learning_proofs),
        },
        "economics": {
            "classification": "modeled",
            "currency": "USD",
            "annual_cost_mean_min": min(annual_values) if annual_values else None,
            "annual_cost_mean_max": max(annual_values) if annual_values else None,
            "highest_forecast_plan_id": (
                highest_forecast["plan_id"] if highest_forecast else None
            ),
            "tokens_per_call_min": min(token_values) if token_values else None,
            "tokens_per_call_max": max(token_values) if token_values else None,
            "scope": "per_forecast_declared_scope_not_portfolio_actual",
        },
        "evidence": {
            "measured_runs": len(measured_runs),
            "modeled_forecasts": len(forecasts),
            "projection_status": "partial" if projection_errors else "complete",
            "projection_error_codes": sorted(set(projection_errors)),
        },
        "attention": attention,
    }


def _performance_review(service: StudioLifecycle, plan_id: str, run_id: str | None) -> dict:
    from rag.performance_evidence import build_performance_review

    policy, policy_error = None, None
    try:
        policy = load_policy_from_environment()
    except PolicyLoadError:
        policy_error = "Current Azure policy could not be verified. This review cannot authorize execution or policy changes."
    return build_performance_review(
        service, plan_id, run_id=run_id, current_policy=policy, policy_error=policy_error,
    )


def _performance_history(service: StudioLifecycle, plan_id: str) -> list[dict]:
    records = service.workspace(plan_id)["records"]
    return [
        {
            "id": item["id"], "created_at": item["created_at"],
            "content_hash": item["content_hash"], "actor": item["actor"],
            "review": item["review"],
        }
        for item in reversed(records) if item["kind"] == "performance-decision"
    ]


def _batch_feedback_components():
    from costgov.billing_snapshots import BillingSnapshotStore
    from costgov.response_learning import ResponseLearningStore

    return (
        json.loads(BILLING_SOURCE_PATH.read_text(encoding="utf-8")),
        BillingSnapshotStore(BILLING_STORE_PATH),
        ResponseLearningStore(RESPONSE_LEARNING_STORE_PATH),
    )


def _batch_feedback_review(service, plan_id, run_id):
    from rag.batch_feedback import build_batch_feedback
    from rag.qna_review import quality_summary

    return {
        **build_batch_feedback(service, *_batch_feedback_components(), plan_id, run_id),
        "quality": quality_summary(service, plan_id, run_id),
    }


def _record_qna_acceptance(service, plan_id, payload, actor):
    from rag.qna_acceptance import record_human_acceptance

    return record_human_acceptance(
        service, plan_id, payload["run_id"], payload["case_id"],
        payload["decision"], payload["reason_code"], actor=actor,
    )


def _record_batch_feedback(service, plan_id, run_id, actor, *, refresh_billing=False, billing_backend="export"):
    from rag.batch_feedback import save_batch_feedback

    with _feedback_lock:
        return save_batch_feedback(service, *_batch_feedback_components(), plan_id, run_id,
                                   actor=actor, refresh_billing=refresh_billing, billing_backend=billing_backend)


def _execute(run_id: str, report_id: str, admission: dict) -> None:
    try:
        result = StudioOrchestrator(ROOT).run(run_id, report_id, admission)
        _set_run(run_id, status="completed", result=result)
        ReportStore(REPORT_STORE_PATH).add_artifact(
            report_id,
            "runs",
            {"id": run_id, "status": "completed", "path": f"studio_runs/{run_id}/result.json"},
        )
    except Exception as exc:
        _set_run(run_id, status="failed", error=str(exc))
        try:
            ReportStore(REPORT_STORE_PATH).add_artifact(
                report_id,
                "runs",
                {"id": run_id, "status": "failed", "error": str(exc)},
            )
        except KeyError:
            pass


class StudioHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _feedback_runtime_unavailable(self, error):
        self.log_error("Feedback runtime dependency unavailable: %s", error)
        return self._json({
            "error": "Billing, learning or quality runtime dependencies are unavailable. Check the server environment and restart Studio.",
            "code": "feedback_runtime_unavailable",
        }, 503)

    def do_GET(self):
        path = urlparse(self.path).path
        match = re.fullmatch(r"/api/plans/([A-Za-z0-9_-]{1,128})/batch-feedback", path)
        if match:
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            if (set(query) != {"run_id"} or len(query["run_id"]) != 1
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", query["run_id"][0])):
                return self._json({"error": "Select one saved run_id"}, 400)
            try:
                return self._json({"review": _batch_feedback_review(_lifecycle_service(), match.group(1), query["run_id"][0])})
            except KeyError:
                return self._json({"error": "Saved feedback source not found"}, 404)
            except ImportError as exc:
                return self._feedback_runtime_unavailable(exc)
            except (ValueError, OSError):
                return self._json({"error": "Billing, learning or quality evidence could not be verified for this batch."}, 409)
        match = re.fullmatch(r"/api/plans/([A-Za-z0-9_-]{1,128})/performance", path)
        if match:
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            if set(query) - {"run_id"} or len(query.get("run_id", [])) > 1:
                return self._json({"error": "Select at most one saved run"}, 400)
            run_id = query.get("run_id", [None])[0]
            if run_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id):
                return self._json({"error": "Invalid saved run identifier"}, 400)
            try:
                service = _lifecycle_service()
                return self._json({
                    "review": _performance_review(service, match.group(1), run_id),
                    "saved_reviews": _performance_history(service, match.group(1)),
                })
            except KeyError:
                return self._json({"error": "Saved forecast or run evidence not found"}, 404)
            except (ValueError, OSError):
                return self._json({
                    "error": "Performance evidence could not be verified. Inspect the saved forecast and run before drawing conclusions.",
                    "code": "performance_evidence_unavailable",
                }, 409)
        match = re.fullmatch(r"/api/plans/([A-Za-z0-9_-]{1,128})/qna-review", path)
        if match:
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            if (set(query) != {"run_id"} or len(query["run_id"]) != 1
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", query["run_id"][0])):
                return self._json({"error": "Select one saved run_id"}, 400)
            try:
                from rag.qna_acceptance import acceptance_review

                return self._json({"review": acceptance_review(
                    _lifecycle_service(), match.group(1), query["run_id"][0],
                )})
            except KeyError:
                return self._json({"error": "Foundry quality evidence not found"}, 404)
            except (ValueError, OSError):
                return self._json({"error": "Human-review evidence could not be verified"}, 409)
        match = re.fullmatch(r"/api/rag-batches/([A-Za-z0-9_-]{1,128})/publication", path)
        if match:
            run_id = match.group(1)
            run = _read_registry().get(run_id)
            if not run or run.get("result", {}).get("schema_version") != "rag-agent-batch.v2":
                return self._json({"error": "Measurement batch not found"}, 404)
            from rag.agent_batch import publication_status
            try:
                return self._json(publication_status(_lifecycle_service(), run_id))
            except (OSError, ValueError, TypeError, KeyError):
                return self._json({
                    "error": "Cloud publication evidence is unavailable; local persistence does not prove publication",
                    "code": "publication_unavailable",
                }, 503)
        if path == "/api/rag-batches/connection":
            from rag.agent_batch import connection_status
            try:
                query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
                if set(query) - {"plan_id"} or ("plan_id" in query and len(query["plan_id"]) != 1):
                    return self._json({"error": "Provide at most one plan_id"}, 400)
                receipt = _lifecycle_service().receipt(query["plan_id"][0]) if "plan_id" in query else None
                return self._json(connection_status(
                    receipt=receipt, loaded=load_policy_from_environment(),
                ))
            except PolicyLoadError:
                return self._json({
                    "ready": False, "status": "blocked", "code": "policy_unavailable",
                    "error": "Active Azure policy is unavailable. Open Policy to review authority and publication; no measurement execution is authorized.",
                }, 503)
            except KeyError:
                return self._json({"ready": False, "error": "Saved forecast not found"}, 404)
            except (ValueError, TypeError):
                return self._json({
                    "configured": False, "ready": False, "status": "invalid_server_configuration",
                    "message": "The server-owned deployed-agent batch configuration is invalid.",
                    "error": "invalid_server_configuration",
                }, 503)
        if path == "/api/rag-playground/connection":
            from rag.books_playground import connection_status
            try:
                return self._json(connection_status())
            except ValueError:
                return self._json({
                    "configured": False, "ready": False, "status": "invalid_server_configuration",
                    "message": "The server-owned books connector configuration is invalid.",
                    "error": "invalid_server_configuration", "sample_questions": [],
                }, 503)
        if path == "/api/lifecycle":
            return self._json({
                "schema_version": "studio-lifecycle-capabilities.v1",
                "release": "full_studio", "csrf_token": _lifecycle_request_token,
                "read_only_import": True, "bounded_evaluation": True,
                "live_execution": False, "policy_publication": False,
                "evaluation_authorization": "TOKENGOV_EVALUATION_ALLOWED_PRINCIPAL or explicit local opt-in",
                "operational_authorization": "TOKENGOV_OPERATIONAL_ALLOWED_PRINCIPAL or separate local opt-in",
            })
        match = re.fullmatch(r"/api/plans/([A-Za-z0-9_-]{1,128})/lifecycle", path)
        if match:
            try:
                return self._json(_lifecycle_service().workspace(match.group(1)))
            except KeyError as exc:
                return self._json({"error": str(exc), "code": "not_found"}, 404)
            except (ValueError, OSError) as exc:
                return self._json({"error": str(exc), "code": "lifecycle_evidence_unavailable"}, 409)
        match = re.fullmatch(r"/api/plans/([A-Za-z0-9_-]{1,128})/run-preview/([A-Za-z0-9_-]{1,128})", path)
        if match:
            try:
                run = _read_registry().get(match.group(2), {})
                if str(run.get("result", {}).get("schema_version", "")).startswith("rag-agent-batch."):
                    return self._json({
                        "error": "Metrics-only measurement is not a conventional execution envelope or quality acceptance evidence; open the saved batch metrics instead",
                        "code": "measurement_only_no_acceptance",
                    }, 409)
                return self._json(_lifecycle_service().preview(*match.groups()))
            except KeyError as exc:
                return self._json({"error": str(exc), "code": "not_found"}, 404)
            except (ValueError, OSError) as exc:
                return self._json({"error": str(exc), "code": "source_preview_unavailable"}, 409)
        if path in {"/health", "/readyz"}:
            storage = _storage_health.check()
            healthy = storage["status"] == "healthy"
            return self._json({
                "schema_version": "studio-health.v1",
                "status": "healthy" if healthy else "unhealthy",
                "service": "tokeneconomics-studio",
                "evidence_scope": "research_prototype",
                "health_scope": "process_and_persistent_storage",
                "checks": {"persistent_storage": storage},
            }, 200 if healthy else 503)
        if path == "/livez":
            return self._json(
                {
                    "status": "healthy",
                    "service": "tokeneconomics-studio",
                    "evidence_scope": "research_prototype",
                },
            )
        if path == "/":
            self.path = "/studio.html"
            return super().do_GET()
        if path == "/api/policy":
            try:
                loaded = load_policy_from_environment()
                review = review_configuration()
                review["csrf_token"] = _policy_review_request_token
                canonical = json.dumps(
                    loaded.document,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                )
                return self._json({
                    "policy": loaded.document,
                    "provenance": loaded.provenance,
                    "content_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                    "change_control": {
                        "browser_write_permitted": False,
                        "proposal_workflow": "external_review_required",
                        "review": review,
                        "approval_url": os.environ.get(
                            "TOKENGOV_APPROVAL_URL", ""
                        ).strip(),
                        "approval_environment": os.environ.get(
                            "TOKENGOV_APPROVAL_ENVIRONMENT",
                            "tokengov-production",
                        ).strip(),
                    },
                })
            except PolicyLoadError as exc:
                return self._json({"error": str(exc), "code": "policy_unavailable"}, 503)
        if path == "/api/policy-change-requests":
            try:
                active_policy = load_policy_from_environment().document
            except PolicyLoadError:
                active_policy = None
            return self._json(
                {
                    "change_requests": PolicyChangeStore(
                        POLICY_CHANGE_STORE_PATH
                    ).list(active_policy=active_policy)
                }
            )
        if path == "/api/govern/decisions":
            try:
                records = GovernanceEvidenceStore(
                    GOVERNANCE_EVIDENCE_STORE_PATH
                ).list(kind="govern_decision")
                return self._json(
                    {
                        "decisions": [
                            {
                                **record.value.to_dict(),
                                "content_hash": record.content_hash,
                            }
                            for record in records
                        ]
                    }
                )
            except ValueError as exc:
                return self._json(
                    {"error": str(exc), "code": "governance_evidence_invalid"},
                    409,
                )
        if path == "/api/reconcile":
            try:
                return self._json(
                    {
                        "reconciliations": ReconciliationEvidenceStore(
                            RECONCILIATION_EVIDENCE_STORE_PATH
                        ).list()
                    }
                )
            except ValueError as exc:
                return self._json(
                    {"error": str(exc), "code": "reconciliation_evidence_invalid"},
                    409,
                )
        if path == "/api/learning-proofs":
            try:
                return self._json(
                    {
                        "learning_proofs": LearningEvidenceStore(
                            LEARNING_EVIDENCE_STORE_PATH
                        ).list()
                    }
                )
            except ValueError as exc:
                return self._json(
                    {"error": str(exc), "code": "learning_evidence_invalid"},
                    409,
                )
        if path == "/api/portability-proofs":
            try:
                return self._json(
                    {
                        "portability_proofs": PortabilityEvidenceStore(
                            PORTABILITY_EVIDENCE_STORE_PATH
                        ).list()
                    }
                )
            except ValueError as exc:
                return self._json(
                    {"error": str(exc), "code": "portability_evidence_invalid"},
                    409,
                )
        if path == "/api/models":
            try:
                return self._json(McpPredictorClient(ROOT).model_catalog())
            except McpPredictionError as exc:
                return self._json({"error": str(exc), "code": "model_catalog_unavailable"}, 503)
        if path == "/api/consumption-models":
            return self._json(_studio_consumption_catalog())
        if path == "/api/route-capabilities":
            return self._json(route_capability_catalog())
        if path == "/api/nine-path-validation":
            return self._json(nine_path_validation_matrix())
        if path == "/api/runs":
            return self._json({"runs": list(_read_registry().values())})
        if path == "/api/reports":
            try:
                registry = _read_registry()
                plan_store = PlanStore(PLAN_STORE_PATH)
                reports = []
                for report in ReportStore(REPORT_STORE_PATH).list():
                    projected = dict(report)
                    projected["portfolio"] = _portfolio_report_summary(
                        report, registry, plan_store
                    )
                    reports.append(projected)
                return self._json({"reports": reports})
            except OSError as exc:
                return self._json(
                    {"error": str(exc), "code": "state_store_unavailable"}, 503
                )
        if path.startswith("/api/reports/"):
            report_id = path.split("/")[3]
            try:
                report = ReportStore(REPORT_STORE_PATH).get(report_id)
                if report:
                    report["artifacts"]["runs"] = _report_run_summaries(report, _read_registry())
                    plan_store = PlanStore(PLAN_STORE_PATH)
                    for artifact in report.get("artifacts", {}).get("govern_handoffs", []):
                        handoff = plan_store.get_govern_handoff(artifact.get("plan_id", ""))
                        if handoff:
                            artifact.update(handoff)
            except OSError as exc:
                return self._json(
                    {"error": str(exc), "code": "state_store_unavailable"}, 503
                )
            return self._json(report or {"error": "not_found"}, 200 if report else 404)
        if path == "/api/plans":
            try:
                return self._json({"plans": PlanStore(PLAN_STORE_PATH).list()})
            except OSError as exc:
                return self._json(
                    {"error": str(exc), "code": "state_store_unavailable"}, 503
                )
        if path.startswith("/api/plans/"):
            plan_id = path.split("/")[3]
            try:
                store = PlanStore(PLAN_STORE_PATH)
                resource = (
                    store.get_receipt(plan_id)
                    if path.endswith("/receipt")
                    else store.create_govern_candidate(plan_id)
                    if path.endswith("/govern-candidate")
                    else store.get(plan_id)
                )
            except OSError as exc:
                return self._json(
                    {"error": str(exc), "code": "state_store_unavailable"}, 503
                )
            except ValueError as exc:
                return self._json(
                    {"error": str(exc), "code": "govern_candidate_unavailable"}, 409
                )
            return self._json(resource or {"error": "not_found"}, 200 if resource else 404)
        if path.startswith("/api/runs/"):
            parts = path.strip("/").split("/")
            run_id = parts[2] if len(parts) >= 3 else ""
            run = _read_registry().get(run_id)
            if len(parts) == 4 and parts[3] == "observe":
                if not run:
                    return self._json({"error": "not_found"}, 404)
                if str(run.get("result", {}).get("schema_version", "")).startswith("rag-agent-batch."):
                    return self._json({
                        "error": "Metrics-only measurement has no task acceptance projection; open the saved batch metrics instead",
                        "code": "measurement_only_no_acceptance",
                    }, 409)
                if run.get("status") != "completed" or not run.get("result"):
                    return self._json(
                        {
                            "error": "Observe requires a completed run",
                            "code": "run_not_completed",
                        },
                        409,
                    )
                try:
                    projection = load_observe_economics(
                        run["result"], REGISTRY_PATH.parent / run_id
                    )
                    return self._json(projection)
                except ValueError as exc:
                    return self._json(
                        {
                            "error": str(exc),
                            "code": "observe_evidence_invalid",
                        },
                        409,
                    )
            return self._json(run or {"error": "not_found"}, 200 if run else 404)
        return super().do_GET()

    def do_DELETE(self):
        path = urlparse(self.path).path
        match = re.fullmatch(r"/api/policy-change-requests/(PCR-[A-F0-9]{10})", path)
        if not match:
            return self._json({"error": "not_found"}, 404)
        try:
            store = PolicyChangeStore(POLICY_CHANGE_STORE_PATH)
            current = store.get(match.group(1))
            removed = (
                store.delete_draft(match.group(1))
                if current["status"] == "draft"
                else store.retire_pending(match.group(1))
            )
            return self._json(
                {
                    "change_id": removed["change_id"],
                    "status": removed["status"],
                    "removed_at": removed["events"][-1]["created_at"],
                }
            )
        except KeyError:
            return self._json(
                {"error": "policy change request not found", "code": "not_found"},
                404,
            )
        except ValueError as exc:
            return self._json(
                {"error": str(exc), "code": "policy_delete_conflict"},
                409,
            )

    def do_POST(self):
        path = urlparse(self.path).path
        match = re.fullmatch(r"/api/reports/([A-Za-z0-9_-]{1,128})/retire", path)
        if match:
            principal = _lifecycle_principal(self, "evaluation")
            if not principal:
                return self._json({
                    "error": "Separate evaluation authorization and same-origin CSRF are required",
                    "code": "lifecycle_unauthorized",
                }, 403)
            try:
                payload = self._read_json()
                if (not isinstance(payload, dict) or set(payload) != {"reason"}
                        or not isinstance(payload["reason"], str)
                        or not 1 <= len(payload["reason"].strip()) <= 500):
                    return self._json({"error": "A retirement reason is required"}, 400)
                return self._json(
                    ReportStore(REPORT_STORE_PATH).retire(
                        match.group(1), reason=payload["reason"],
                    ),
                    201,
                )
            except KeyError:
                return self._json({"error": "Report not found"}, 404)
            except (ValueError, OSError) as exc:
                return self._json({"error": str(exc), "code": "report_retirement_failed"}, 409)
        match = re.fullmatch(r"/api/plans/([A-Za-z0-9_-]{1,128})/qna-acceptance", path)
        if match:
            principal = _lifecycle_principal(self, "evaluation")
            if not principal:
                return self._json({
                    "error": "Separate evaluation authorization and same-origin CSRF are required",
                    "code": "lifecycle_unauthorized",
                }, 403)
            try:
                payload = self._read_json()
                if (not isinstance(payload, dict)
                        or set(payload) != {"run_id", "case_id", "decision", "reason_code"}
                        or any(not isinstance(payload[key], str) for key in payload)
                        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", payload["run_id"])
                        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", payload["case_id"])
                        or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", payload["reason_code"])):
                    return self._json({"error": "Provide one evaluated case and explicit decision"}, 400)
                record, review = _record_qna_acceptance(
                    _lifecycle_service(), match.group(1), payload, principal,
                )
                return self._json({"record": record, "review": review}, 201)
            except KeyError:
                return self._json({"error": "Evaluated case or Foundry evidence not found"}, 404)
            except (ValueError, OSError) as exc:
                return self._json({"error": str(exc), "code": "qna_acceptance_invalid"}, 409)
        match = re.fullmatch(r"/api/plans/([A-Za-z0-9_-]{1,128})/(batch-feedback|batch-billing-sync)", path)
        if match:
            principal = _lifecycle_principal(self, "evaluation")
            if not principal:
                return self._json({"error": "Separate evaluation authorization and same-origin CSRF are required"}, 403)
            try:
                payload = self._read_json()
                if (not isinstance(payload, dict) or set(payload) != {"run_id"}
                        or not isinstance(payload["run_id"], str)
                        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", payload["run_id"])):
                    return self._json({"error": "Only a saved run_id is accepted; billing sources and allocations are server-owned"}, 400)
                result = _record_batch_feedback(_lifecycle_service(), match.group(1), payload["run_id"], principal,
                                                refresh_billing=match.group(2) == "batch-billing-sync")
                return self._json(result, 201 if result["created"] else 200)
            except KeyError:
                return self._json({"error": "Saved feedback source not found"}, 404)
            except ImportError as exc:
                return self._feedback_runtime_unavailable(exc)
            except (ValueError, OSError):
                return self._json({"error": "Feedback could not be recorded from verified billing and batch evidence. No execution or policy change occurred."}, 409)
        match = re.fullmatch(r"/api/plans/([A-Za-z0-9_-]{1,128})/performance-reviews", path)
        if match:
            principal = _lifecycle_principal(self, "evaluation")
            if not principal:
                return self._json({
                    "error": "Separate evaluation authorization and same-origin CSRF token are required to save a review",
                    "code": "lifecycle_unauthorized",
                }, 403)
            try:
                payload = self._read_json()
                if (not isinstance(payload, dict) or set(payload) != {"run_id"}
                        or not isinstance(payload["run_id"], str)
                        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", payload["run_id"])):
                    return self._json({"error": "Provide one saved run_id; browser findings and scores are not accepted"}, 400)
                service = _lifecycle_service()
                review = _performance_review(service, match.group(1), payload["run_id"])
                if not review.get("selected_run_id"):
                    return self._json({"error": "A verified saved run is required to record a review"}, 409)
                record = service.append(service.receipt(match.group(1)), "performance-decision", {
                    "actor": principal, "status": "advisory",
                    "operational_promotion": False, "mutation_performed": False,
                    "review": review,
                })
                return self._json({
                    "review": review, "record": record,
                    "saved_reviews": _performance_history(service, match.group(1)),
                }, 201)
            except KeyError:
                return self._json({"error": "Saved forecast or run evidence not found"}, 404)
            except (ValueError, OSError):
                return self._json({
                    "error": "The advisory review could not be saved from verified evidence. No execution or policy publication was attempted.",
                    "code": "performance_review_unavailable",
                }, 409)
        match = re.fullmatch(r"/api/plans/([A-Za-z0-9_-]{1,128})/rag-batches", path)
        if match:
            principal = _lifecycle_principal(self, "evaluation")
            if not principal:
                return self._json({
                    "error": "Separate evaluation authorization and same-origin CSRF token are required",
                    "code": "lifecycle_unauthorized",
                }, 403)
            from rag.agent_batch import execute, publication_status
            try:
                service = _lifecycle_service()
                result = execute(
                    service, match.group(1), self._read_json(),
                    load_policy_from_environment(), principal, ROOT, _set_run,
                )
                cloud_status = result["evidence"].get("cloud_status")
                if cloud_status == "publication_tracked_separately":
                    try:
                        cloud_status = publication_status(service, result["run_id"])["status"]
                    except (OSError, ValueError, TypeError, KeyError):
                        cloud_status = "unavailable"
                ReportStore(REPORT_STORE_PATH).add_artifact(
                    result["report_id"], "runs", {
                        "id": result["run_id"], "status": result["execution_status"],
                        "execution_status": result["execution_status"],
                        "schema_version": result.get("schema_version"),
                        "plan_id": result.get("plan_id"),
                        "receipt_hash": result.get("prediction", {}).get("content_hash") or result.get("receipt_hash"),
                        "evidence_status": result["evidence"].get("status"),
                        "cloud_status": cloud_status,
                        "path": result["evidence"]["location"],
                    },
                )
                return self._json(result, 201)
            except PolicyLoadError:
                return self._json({
                    "error": "Active Azure policy is unavailable; no execution authorized",
                    "code": "policy_unavailable",
                }, 503)
            except KeyError:
                return self._json({"error": "Completed forecast receipt not found", "code": "not_found"}, 404)
            except (ValueError, TypeError):
                return self._json({
                    "error": "Batch request, forecast or Azure policy validation failed; no execution authorized",
                    "code": "rag_batch_blocked",
                }, 409)
            except Exception:
                return self._json({
                    "error": "Batch evidence is unavailable; preserve the request identity and do not retry billable execution",
                    "code": "rag_batch_unavailable",
                }, 503)
        match = re.fullmatch(r"/api/plans/([A-Za-z0-9_-]{1,128})/rag-playground", path)
        if match:
            return self._json({
                "error": "Legacy lexical execution is retired. Use the metrics-only deployed-agent batch endpoint.",
                "code": "rag_playground_retired",
                "successor": f"/api/plans/{match.group(1)}/rag-batches",
                "execution_performed": False,
                "historical_evidence": "unchanged",
            }, 410)
        match = re.fullmatch(
            r"/api/plans/([A-Za-z0-9_-]{1,128})/(requirements|attachments|evaluations|reassessments|operational-admissions|reconciliations|comparisons)", path
        )
        if match:
            plan_id, action = match.groups()
            scope = "operational" if action == "operational-admissions" else "evaluation"
            principal = _lifecycle_principal(self, scope)
            if not principal:
                return self._json({"error": f"Separate {scope} authorization and same-origin CSRF token are required", "code": "lifecycle_unauthorized"}, 403)
            try:
                payload = self._read_json()
                if action == "attachments" and isinstance(payload, dict):
                    run = _read_registry().get(payload.get("run_id"), {})
                    if str(run.get("result", {}).get("schema_version", "")).startswith("rag-agent-batch."):
                        return self._json({
                            "error": "Metrics-only batches cannot be imported as conventional execution or acceptance evidence",
                            "code": "measurement_only_no_acceptance",
                        }, 409)
                service = _lifecycle_service()
                if action == "requirements":
                    result = service.requirements(plan_id, payload)
                elif action == "attachments":
                    result = service.attach(plan_id, payload)
                elif action == "reconciliations":
                    result = service.reconcile(plan_id, payload)
                else:
                    policy = load_policy_from_environment()
                    if action == "evaluations":
                        result = service.evaluate(plan_id, payload, policy, principal)
                    elif action == "reassessments":
                        result = service.reassess(plan_id, payload, policy)
                    elif action == "comparisons":
                        result = service.compare(plan_id, payload, policy)
                    else:
                        result = service.operational_admission(plan_id, payload, policy, principal)
                return self._json(result, 201)
            except PolicyLoadError as exc:
                return self._json({"error": str(exc), "code": "policy_unavailable"}, 503)
            except KeyError as exc:
                return self._json({"error": str(exc), "code": "not_found"}, 404)
            except (ValueError, TypeError, OSError) as exc:
                return self._json({"error": str(exc), "code": "lifecycle_evidence_invalid"}, 409)
        if (
            path.startswith("/api/policy-change-requests/")
            and path.endswith("/submit")
        ):
            change_id = path.split("/")[3]
            if not _policy_review_authorized(self):
                return self._json(
                    {
                        "error": (
                            "Policy review submission requires an authenticated "
                            "authorized Studio principal."
                        ),
                        "code": "policy_review_unauthorized",
                    },
                    403,
                )
            store = PolicyChangeStore(POLICY_CHANGE_STORE_PATH)
            try:
                proposal = store.get(change_id)
                review = _policy_review_client().create_review(proposal)
                return self._json(store.record_review(change_id, review), 201)
            except KeyError:
                return self._json(
                    {"error": "policy change request not found", "code": "not_found"},
                    404,
                )
            except ValueError as exc:
                return self._json(
                    {"error": str(exc), "code": "policy_review_conflict"},
                    409,
                )
            except GitHubPolicyReviewError as exc:
                return self._json(
                    {"error": str(exc), "code": "github_review_failed"},
                    503,
                )
        if path == "/api/govern/decisions":
            if not _lifecycle_principal(self, "evaluation"):
                return self._json({"error": "Separate evaluation authorization is required", "code": "lifecycle_unauthorized"}, 403)
            try:
                payload = self._read_json()
                run_ids = payload.get("run_ids")
                active_candidate_ids = payload.get("active_candidate_ids", [])
                if active_candidate_ids:
                    raise ValueError("the browser cannot assert operationally active candidates")
                if (
                    not isinstance(run_ids, list)
                    or not run_ids
                    or any(not isinstance(item, str) or not item for item in run_ids)
                    or len(set(run_ids)) != len(run_ids)
                ):
                    raise ValueError("run_ids must be a non-empty unique string array")
                if (
                    not isinstance(active_candidate_ids, list)
                    or any(
                        not isinstance(item, str) or not item
                        for item in active_candidate_ids
                    )
                ):
                    raise ValueError("active_candidate_ids must be a string array")
                active_candidate_ids = set(active_candidate_ids)
                registry = _read_registry()
                selected_runs = []
                report_ids = set()
                for run_id in run_ids:
                    run = registry.get(run_id)
                    if (
                        not run
                        or run.get("status") != "completed"
                        or not run.get("result")
                    ):
                        raise ValueError(
                            f"completed immutable run evidence is required: {run_id}"
                        )
                    selected_runs.append(run["result"])
                    report_ids.add(run["result"].get("report_id"))
                if len(report_ids) != 1 or None in report_ids:
                    raise ValueError("candidate runs must belong to one report")

                evidence_store = GovernanceEvidenceStore(
                    GOVERNANCE_EVIDENCE_STORE_PATH
                )
                constraints = []
                transitions = []
                state_store = DecisionStateStore(DECISION_STATE_STORE_PATH)
                for run_result in selected_runs:
                    constraint = build_candidate_constraint_from_run(
                        run_result,
                        REGISTRY_PATH.parent / run_result["run_id"],
                    )
                    existing = evidence_store.get(constraint.constraint_id)
                    if existing is None:
                        evidence_store.append(constraint)
                    elif existing.content_hash != constraint.content_hash:
                        raise ValueError(
                            "stored constraint differs from verified run evidence"
                        )
                    constraints.append(constraint)
                    transitions.extend(
                        state_store.record(
                            constraint,
                            candidate_is_active=(
                                constraint.candidate_id in active_candidate_ids
                            ),
                        )
                    )
                decision = select_candidate(
                    constraints,
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
                stored = evidence_store.append(decision)
                report_id = next(iter(report_ids))
                ReportStore(REPORT_STORE_PATH).add_artifact(
                    report_id,
                    "govern_decisions",
                    {
                        "id": decision.decision_id,
                        "content_hash": stored.content_hash,
                        "outcome": decision.outcome.value,
                        "selected_candidate_id": decision.selected_candidate_id,
                        "created_at": decision.created_at,
                    },
                )
                return self._json(
                    {
                        **decision.to_dict(),
                        "content_hash": stored.content_hash,
                        "state_transitions": transitions,
                    },
                    201,
                )
            except json.JSONDecodeError as exc:
                return self._json({"error": str(exc)}, 400)
            except ValueError as exc:
                return self._json({"error": str(exc)}, 409)
        if path == "/api/policy-change-requests":
            try:
                loaded_policy = load_policy_from_environment()
            except PolicyLoadError as exc:
                return self._json({"error": str(exc), "code": "policy_unavailable"}, 503)
            try:
                proposal = PolicyChangeStore(POLICY_CHANGE_STORE_PATH).create(
                    self._read_json(),
                    loaded_policy,
                )
                return self._json(proposal, 201)
            except (PolicyLoadError, ValueError, json.JSONDecodeError) as exc:
                return self._json({"error": str(exc)}, 400)
        if path == "/api/reports":
            try:
                payload = self._read_json()
                return self._json(
                    ReportStore(REPORT_STORE_PATH).create(str(payload.get("title", ""))),
                    201,
                )
            except json.JSONDecodeError as exc:
                return self._json({"error": str(exc)}, 400)
        if path.startswith("/api/reports/") and path.endswith("/save"):
            report_id = path.split("/")[3]
            try:
                payload = self._read_json()
                report = ReportStore(REPORT_STORE_PATH).save(
                    report_id,
                    title=payload.get("title"),
                    notes=payload.get("notes"),
                )
                return self._json(report)
            except KeyError:
                return self._json({"error": "not_found"}, 404)
        if path == "/api/analyze":
            try:
                payload = self._read_json()
                analysis = McpPredictorClient(ROOT).analyze(
                    str(payload.get("description", ""))
                )
                return self._json(analysis)
            except (ValueError, json.JSONDecodeError) as exc:
                return self._json({"error": str(exc)}, 400)
            except McpPredictionError as exc:
                return self._json({"error": str(exc), "code": "analysis_unavailable"}, 502)
        if path == "/api/plan":
            store = PlanStore(PLAN_STORE_PATH)
            session = None
            try:
                payload = self._read_json()
                description = str(payload.get("description", "")).strip()
                parameters = payload.get("parameters") or {}
                if not isinstance(parameters, dict) or any(key in parameters for key in ("govern_evidence", "govern_constraints")):
                    raise ValueError("browser-supplied governance evidence is not accepted; use verified lifecycle attachments")
                report_id = str(payload.get("report_id", "")).strip()
                report_store = ReportStore(REPORT_STORE_PATH)
                if not report_id or not report_store.get(report_id):
                    return self._json({"error": "valid report_id is required"}, 400)
                plan_id = str(payload.get("plan_id", "")).strip()
                if plan_id:
                    session = store.get(plan_id)
                    if not session:
                        return self._json({"error": "not_found"}, 404)
                    if _route_is_read_only(str(session.get("parameters", {}).get("route") or "foundry")) or _route_is_read_only(
                        str(parameters.get("route") or session.get("parameters", {}).get("route") or "foundry")
                    ):
                        return self._json({"error": PLANNED_ROUTE_MESSAGE, "code": "route_read_only"}, 403)
                    session = store.resume_session(session, description, parameters)
                    description = session["description"]
                    parameters = session["parameters"]
                else:
                    if _route_is_read_only(str(parameters.get("route") or "foundry")):
                        return self._json({"error": PLANNED_ROUTE_MESSAGE, "code": "route_read_only"}, 403)
                    session = store.create_session(report_id, description, parameters)
                    report_store.add_artifact(
                        report_id,
                        "plans",
                        {"id": session["plan_id"], "status": session["status"]},
                    )
                questions = []
                route = str(parameters.get("route") or "foundry")
                if not description:
                    questions.append({"field": "description", "question": "What workload should be forecast?"})
                if route in MODEL_ROUTES and not str(parameters.get("model", "")).strip():
                    questions.append({"field": "model", "question": "Which model should be priced?"})
                if questions:
                    session = store.require_clarification(session, questions)
                    report_store.add_artifact(
                        report_id,
                        "plans",
                        {"id": session["plan_id"], "status": session["status"]},
                    )
                    return self._json(session, 201)
                if route in COMMERCIAL_ROUTES and route not in MODEL_ROUTES:
                    result = build_commercial_result(description, parameters)
                    return self._complete_plan(
                        store, report_store, session, report_id, result
                    )
                analysis = McpPredictorClient(ROOT).analyze(description)
                confirmed_profile = parameters.get("confirmed_profile") or {}
                analysis_confirmed = parameters.get("analysis_confirmed") is True
                material_ambiguity = (
                    analysis.get("topology", {}).get("confidence") == "low"
                    or bool(analysis.get("clarifications"))
                )
                if not analysis_confirmed or (material_ambiguity and not confirmed_profile):
                    analysis_questions = [{
                        "field": "workload_analysis",
                        "question": (
                            "Review and confirm the inferred workload profile before estimation."
                            if not analysis.get("clarifications")
                            else " ".join(analysis["clarifications"])
                        ),
                    }]
                    session = store.require_clarification(
                        session, analysis_questions, analysis=analysis
                    )
                    report_store.add_artifact(
                        report_id,
                        "plans",
                        {"id": session["plan_id"], "status": session["status"]},
                    )
                    return self._json(session, 201)
                if not confirmed_profile:
                    confirmed_profile = {
                        "agent_pattern": analysis["topology"]["selected"],
                        "multi_agent_count": analysis["agent_count"]["value"],
                        "modalities": analysis.get("modalities", []),
                        "tools": analysis.get("tools", []),
                    }
                parameters = {
                    **parameters,
                    "analysis": analysis,
                    "confirmed_profile": confirmed_profile,
                }
                token_result = McpPredictorClient(ROOT).predict(
                    description, parameters
                )
                result = (
                    build_commercial_result(
                        description, parameters, token_result=token_result
                    )
                    if route in COMMERCIAL_ROUTES
                    else attach_foundry_meter_stack(token_result)
                )
                if route_requires_infrastructure(route):
                    draft = session.get("infrastructure_draft")
                    if parameters.get("infrastructure_confirmed") is True:
                        if not draft:
                            raise ValueError(
                                "infrastructure must be reviewed before confirmation"
                            )
                        result["infrastructure"] = confirm_infrastructure_forecast(
                            draft,
                            str(parameters.get("infrastructure_forecast_hash") or ""),
                        )
                    else:
                        draft = build_infrastructure_forecast(description, parameters)
                        session = store.require_infrastructure_review(
                            session, draft, parameters
                        )
                        report_store.add_artifact(
                            report_id,
                            "plans",
                            {"id": session["plan_id"], "status": session["status"]},
                        )
                        return self._json(session, 201)
                return self._complete_plan(
                    store, report_store, session, report_id, result
                )
            except CommercialPlanClarification as exc:
                if session:
                    session = store.require_clarification(
                        session,
                        [{"field": exc.field, "question": str(exc)}],
                    )
                    report_store.add_artifact(
                        session["report_id"],
                        "plans",
                        {"id": session["plan_id"], "status": session["status"]},
                    )
                    return self._json(session, 201)
                return self._json({"status": "failed", "error": str(exc)}, 400)
            except (ValueError, json.JSONDecodeError) as exc:
                if session:
                    store.fail(session, str(exc))
                    report_store.add_artifact(
                        session["report_id"],
                        "plans",
                        {"id": session["plan_id"], "status": "failed", "error": str(exc)},
                    )
                return self._json({"status": "failed", "error": str(exc)}, 400)
            except McpPredictionError as exc:
                if session:
                    store.fail(session, str(exc))
                    report_store.add_artifact(
                        session["report_id"],
                        "plans",
                        {"id": session["plan_id"], "status": "failed", "error": str(exc)},
                    )
                return self._json({"status": "failed", "error": str(exc)}, 502)
            except AzureInfrastructureError as exc:
                if session:
                    session = store.require_clarification(
                        session,
                        [{"field": "azure_infrastructure", "question": str(exc)}],
                    )
                    return self._json(session, 201)
                return self._json({"status": "failed", "error": str(exc)}, 502)
        if path.startswith("/api/plans/") and path.endswith("/govern-handoff"):
            plan_id = path.split("/")[3]
            try:
                plan_store = PlanStore(PLAN_STORE_PATH)
                route_handoff = plan_store.create_route_govern_handoff(plan_id)
                if (
                    route_handoff["status"] == "eligible"
                    and route_handoff["route_id"] in MODEL_ROUTES
                ):
                    policy = load_policy_from_environment()
                    handoff = plan_store.create_govern_handoff(plan_id, policy)
                    handoff.update(
                        candidate_id=route_handoff["candidate_id"],
                        candidate_hash=route_handoff["candidate_hash"],
                        route_id=route_handoff["route_id"],
                        capability_profile=route_handoff["capability_profile"],
                        readiness=route_handoff["readiness"],
                        route_decision=route_handoff["route_decision"],
                    )
                else:
                    handoff = route_handoff
                ReportStore(REPORT_STORE_PATH).add_artifact(
                    handoff["report_id"],
                    "govern_handoffs",
                    {
                        "id": handoff["handoff_id"],
                        "report_id": handoff["report_id"],
                        "plan_id": handoff["plan_id"],
                        "receipt_id": handoff["receipt_id"],
                        "receipt_hash": handoff["receipt_hash"],
                        "prediction_id": handoff["prediction_id"],
                        "status": handoff["status"],
                        "economics": handoff["economics"],
                        "policy": handoff["policy"],
                        "checks": handoff["checks"],
                        "execution": handoff["execution"],
                        "mutation": handoff["mutation"],
                        "evaluated_at": handoff["evaluated_at"],
                        "infrastructure_status": handoff["infrastructure_status"],
                        "candidate_id": handoff.get("candidate_id"),
                        "candidate_hash": handoff.get("candidate_hash"),
                        "route_id": handoff.get("route_id"),
                        "readiness": handoff.get("readiness"),
                        "route_decision": handoff.get("route_decision"),
                    },
                )
                return self._json(handoff, 201)
            except PolicyLoadError as exc:
                return self._json({"error": str(exc), "code": "policy_unavailable"}, 503)
            except OSError as exc:
                return self._json(
                    {"error": str(exc), "code": "state_store_unavailable"}, 503
                )
            except KeyError:
                return self._json({"error": "not_found"}, 404)
            except ValueError as exc:
                return self._json({"error": str(exc)}, 409)
        if path == "/api/runs":
            return self._json({
                "error": "Live dispatch is not supported by the read-only workload adapter. Import an approved runner's evidence through Execute.",
                "code": "execution_adapter_unavailable",
            }, 409)
        return self._json({"error": "not_found"}, 404)

    def _complete_plan(self, store, report_store, session, report_id, result):
        session, receipt = store.complete(session, result)
        candidate = store.create_govern_candidate(session["plan_id"])
        prediction_id = receipt["prediction"].get("prediction_id")
        report_store.add_artifact(
            report_id,
            "plans",
            {
                "id": session["plan_id"],
                "status": session["status"],
                "prediction_id": prediction_id,
            },
        )
        report_store.add_artifact(
            report_id,
            "receipts",
            {
                "id": receipt["receipt_id"],
                "plan_id": session["plan_id"],
                "prediction_id": prediction_id,
                "content_hash": receipt["content_hash"],
            },
        )
        result.update(
            report_id=report_id,
            plan_id=session["plan_id"],
            receipt_id=receipt["receipt_id"],
            receipt_hash=receipt["content_hash"],
            schema_version=receipt["schema_version"],
            trajectory_contract=receipt["trajectory_contract"],
            govern_candidate={
                "candidate_id": candidate["candidate_id"],
                "content_hash": candidate["content_hash"],
            },
            capability_profile=candidate["capability_profile"],
            readiness=candidate["readiness"],
            created_at=receipt["created_at"],
        )
        return self._json(result, 201)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 256_000:
            raise ValueError("request body exceeds the Studio limit")
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length))

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    host = os.environ.get("TOKENECONOMICS_HOST", "127.0.0.1")
    port = int(os.environ.get("TOKENECONOMICS_PORT", "8765"))
    server = ThreadingHTTPServer((host, port), StudioHandler)
    print(f"TokenEconomics Studio: http://{host}:{port}")
    server.serve_forever()