"""Local TokenEconomics Studio API and static application server."""

from __future__ import annotations

import json
import hashlib
import threading
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from costgov.commercial_planning import (
    COMMERCIAL_ROUTES,
    MODEL_ROUTES,
    CommercialPlanClarification,
    attach_foundry_meter_stack,
    build_commercial_result,
)
from costgov.consumption_models import consumption_catalog
from costgov.mcp_prediction import McpPredictionError, McpPredictorClient
from costgov.decision_state import DecisionStateStore
from costgov.governance_decisions import (
    GovernanceEvidenceStore,
    build_candidate_constraint_from_run,
    select_candidate,
)
from costgov.observe_economics import load_observe_economics
from costgov.orchestrator import StudioOrchestrator
from costgov.planning import PlanStore
from costgov.policy_changes import PolicyChangeStore
from costgov.policy_store import PolicyLoadError, load_policy_from_environment
from costgov.reports import ReportStore

ROOT = Path(__file__).resolve().parent
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
_lock = threading.Lock()


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

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self.path = "/studio.html"
            return super().do_GET()
        if path == "/api/policy":
            try:
                loaded = load_policy_from_environment()
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
                    },
                })
            except PolicyLoadError as exc:
                return self._json({"error": str(exc), "code": "policy_unavailable"}, 503)
        if path == "/api/policy-change-requests":
            return self._json({"change_requests": PolicyChangeStore(POLICY_CHANGE_STORE_PATH).list()})
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
        if path == "/api/models":
            try:
                return self._json(McpPredictorClient(ROOT).model_catalog())
            except McpPredictionError as exc:
                return self._json({"error": str(exc), "code": "model_catalog_unavailable"}, 503)
        if path == "/api/consumption-models":
            return self._json(consumption_catalog())
        if path == "/api/runs":
            return self._json({"runs": list(_read_registry().values())})
        if path == "/api/reports":
            return self._json({"reports": ReportStore(REPORT_STORE_PATH).list()})
        if path.startswith("/api/reports/"):
            report_id = path.split("/")[3]
            report = ReportStore(REPORT_STORE_PATH).get(report_id)
            if report:
                plan_store = PlanStore(PLAN_STORE_PATH)
                for artifact in report.get("artifacts", {}).get("govern_handoffs", []):
                    handoff = plan_store.get_govern_handoff(artifact.get("plan_id", ""))
                    if handoff:
                        artifact.update(handoff)
            return self._json(report or {"error": "not_found"}, 200 if report else 404)
        if path == "/api/plans":
            return self._json({"plans": PlanStore(PLAN_STORE_PATH).list()})
        if path.startswith("/api/plans/"):
            plan_id = path.split("/")[3]
            store = PlanStore(PLAN_STORE_PATH)
            resource = store.get_receipt(plan_id) if path.endswith("/receipt") else store.get(plan_id)
            return self._json(resource or {"error": "not_found"}, 200 if resource else 404)
        if path.startswith("/api/runs/"):
            parts = path.strip("/").split("/")
            run_id = parts[2] if len(parts) >= 3 else ""
            run = _read_registry().get(run_id)
            if len(parts) == 4 and parts[3] == "observe":
                if not run:
                    return self._json({"error": "not_found"}, 404)
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

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/govern/decisions":
            try:
                payload = self._read_json()
                run_ids = payload.get("run_ids")
                if (
                    not isinstance(run_ids, list)
                    or not run_ids
                    or any(not isinstance(item, str) or not item for item in run_ids)
                    or len(set(run_ids)) != len(run_ids)
                ):
                    raise ValueError("run_ids must be a non-empty unique string array")
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
                    transitions.extend(state_store.record(constraint))
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
                report_id = str(payload.get("report_id", "")).strip()
                report_store = ReportStore(REPORT_STORE_PATH)
                if not report_id or not report_store.get(report_id):
                    return self._json({"error": "valid report_id is required"}, 400)
                plan_id = str(payload.get("plan_id", "")).strip()
                if plan_id:
                    session = store.get(plan_id)
                    if not session:
                        return self._json({"error": "not_found"}, 404)
                    session = store.resume_session(session, description, parameters)
                    description = session["description"]
                    parameters = session["parameters"]
                else:
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
        if path.startswith("/api/plans/") and path.endswith("/govern-handoff"):
            plan_id = path.split("/")[3]
            try:
                policy = load_policy_from_environment()
                handoff = PlanStore(PLAN_STORE_PATH).create_govern_handoff(plan_id, policy)
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
                    },
                )
                return self._json(handoff, 201)
            except PolicyLoadError as exc:
                return self._json({"error": str(exc), "code": "policy_unavailable"}, 503)
            except KeyError:
                return self._json({"error": "not_found"}, 404)
            except ValueError as exc:
                return self._json({"error": str(exc)}, 409)
        if path == "/api/runs":
            payload = self._read_json()
            report_id = str(payload.get("report_id", "")).strip()
            plan_id = str(payload.get("plan_id", "")).strip()
            report_store = ReportStore(REPORT_STORE_PATH)
            if not report_id or not report_store.get(report_id):
                return self._json({"error": "valid report_id is required"}, 400)
            session = PlanStore(PLAN_STORE_PATH).get(plan_id) if plan_id else None
            admission = session.get("govern_handoff") if session else None
            if not session or session.get("report_id") != report_id:
                return self._json({"error": "an admitted plan in this report is required"}, 400)
            if not admission or admission.get("status") != "admitted":
                return self._json({"error": "policy admission is required before execution"}, 409)
            run_id = str(uuid4())
            binding = {
                "plan_id": plan_id,
                "handoff_id": admission["handoff_id"],
                "policy_id": admission["policy"]["policy_id"],
                "policy_version": admission["policy"]["version"],
                "policy_etag": admission["policy"]["provenance"].get("etag"),
            }
            _set_run(run_id, run_id=run_id, report_id=report_id, status="queued", binding=binding)
            report_store.add_artifact(
                report_id,
                "runs",
                {"id": run_id, "status": "queued", **binding},
            )
            threading.Thread(target=_execute, args=(run_id, report_id, admission), daemon=True).start()
            return self._json(
                {"run_id": run_id, "report_id": report_id, "status": "queued", "binding": binding},
                202,
            )
        return self._json({"error": "not_found"}, 404)

    def _complete_plan(self, store, report_store, session, report_id, result):
        session, receipt = store.complete(session, result)
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
            created_at=receipt["created_at"],
        )
        return self._json(result, 201)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
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
    server = ThreadingHTTPServer(("127.0.0.1", 8765), StudioHandler)
    print("TokenEconomics Studio: http://127.0.0.1:8765")
    server.serve_forever()