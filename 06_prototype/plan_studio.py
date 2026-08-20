"""Plan-only TokenEconomics Studio server for the TE-001.5 release slice."""

from __future__ import annotations

import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from urllib.parse import urlparse

from costgov.commercial_planning import (
    COMMERCIAL_ROUTES,
    MODEL_ROUTES,
    CommercialPlanClarification,
    attach_foundry_meter_stack,
    build_commercial_result,
)
from costgov.consumption_models import consumption_catalog
from costgov.mcp_prediction import McpPredictionError, McpPredictorClient
from costgov.planning import PlanStore
from costgov.reports import ReportStore

ROOT = Path(__file__).resolve().parent
PLAN_STORE_PATH = ROOT / "studio_plans"
REPORT_STORE_PATH = ROOT / "studio_reports"
MAX_REQUEST_BYTES = 256_000


class PlanStudioHandler(SimpleHTTPRequestHandler):
    """Serve only report, analysis, prediction, and receipt endpoints."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self.path = "/studio.html"
            return super().do_GET()
        if path == "/api/models":
            try:
                return self._json(McpPredictorClient(ROOT).model_catalog())
            except McpPredictionError as exc:
                return self._json({"error": str(exc), "code": "model_catalog_unavailable"}, 503)
        if path == "/api/consumption-models":
            return self._json(consumption_catalog())
        if path == "/api/reports":
            return self._json({"reports": ReportStore(REPORT_STORE_PATH).list()})
        if path.startswith("/api/reports/"):
            report = ReportStore(REPORT_STORE_PATH).get(self._resource_id(path, 3))
            return self._json(report or {"error": "not_found"}, 200 if report else 404)
        if path == "/api/plans":
            return self._json({"plans": PlanStore(PLAN_STORE_PATH).list()})
        if path.startswith("/api/plans/"):
            plan_id = self._resource_id(path, 3)
            store = PlanStore(PLAN_STORE_PATH)
            resource = store.get_receipt(plan_id) if path.endswith("/receipt") else store.get(plan_id)
            return self._json(resource or {"error": "not_found"}, 200 if resource else 404)
        return self._json({"error": "not_found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/reports":
            try:
                payload = self._read_json()
                return self._json(
                    ReportStore(REPORT_STORE_PATH).create(str(payload.get("title", ""))),
                    201,
                )
            except (ValueError, json.JSONDecodeError) as exc:
                return self._json({"error": str(exc)}, 400)
        if path.startswith("/api/reports/") and path.endswith("/save"):
            try:
                payload = self._read_json()
                report = ReportStore(REPORT_STORE_PATH).save(
                    self._resource_id(path, 3),
                    title=payload.get("title"),
                    notes=payload.get("notes"),
                )
                return self._json(report)
            except KeyError:
                return self._json({"error": "not_found"}, 404)
            except (ValueError, json.JSONDecodeError) as exc:
                return self._json({"error": str(exc)}, 400)
        if path == "/api/analyze":
            try:
                analysis = McpPredictorClient(ROOT).analyze(
                    str(self._read_json().get("description", ""))
                )
                return self._json(analysis)
            except (ValueError, json.JSONDecodeError) as exc:
                return self._json({"error": str(exc)}, 400)
            except McpPredictionError as exc:
                return self._json({"error": str(exc), "code": "analysis_unavailable"}, 502)
        if path == "/api/plan":
            return self._plan()
        return self._json({"error": "not_found"}, 404)

    def _plan(self):
        store = PlanStore(PLAN_STORE_PATH)
        session = None
        report_store = ReportStore(REPORT_STORE_PATH)
        try:
            payload = self._read_json()
            description = str(payload.get("description", "")).strip()
            parameters = payload.get("parameters") or {}
            if not isinstance(parameters, dict):
                raise ValueError("parameters must be an object")
            report_id = str(payload.get("report_id", "")).strip()
            if not report_id or not report_store.get(report_id):
                return self._json({"error": "valid report_id is required"}, 400)
            plan_id = str(payload.get("plan_id", "")).strip()
            if plan_id:
                session = store.get(plan_id)
                if not session:
                    return self._json({"error": "not_found"}, 404)
                if session.get("report_id") != report_id:
                    return self._json({"error": "plan does not belong to report"}, 409)
                session = store.resume_session(session, description, parameters)
                description, parameters = session["description"], session["parameters"]
            else:
                session = store.create_session(report_id, description, parameters)
                report_store.add_artifact(report_id, "plans", {
                    "id": session["plan_id"], "status": session["status"],
                })

            questions = []
            route = str(parameters.get("route") or "foundry")
            if not description:
                questions.append({"field": "description", "question": "What workload should be forecast?"})
            if route in MODEL_ROUTES and not str(parameters.get("model", "")).strip():
                questions.append({"field": "model", "question": "Which model should be priced?"})
            if questions:
                return self._clarify(store, report_store, session, questions)

            if route in COMMERCIAL_ROUTES and route not in MODEL_ROUTES:
                try:
                    result = build_commercial_result(description, parameters)
                except CommercialPlanClarification as exc:
                    return self._clarify(
                        store,
                        report_store,
                        session,
                        [{"field": exc.field, "question": str(exc)}],
                    )
                return self._complete_plan(
                    store, report_store, session, report_id, result
                )

            analysis = McpPredictorClient(ROOT).analyze(description)
            confirmed = parameters.get("confirmed_profile") or {}
            if not isinstance(confirmed, dict):
                raise ValueError("confirmed_profile must be an object")
            ambiguous = (
                analysis.get("topology", {}).get("confidence") == "low"
                or bool(analysis.get("clarifications"))
            )
            if parameters.get("analysis_confirmed") is not True or (ambiguous and not confirmed):
                question = (
                    " ".join(analysis["clarifications"])
                    if analysis.get("clarifications")
                    else "Review and confirm the inferred workload profile before estimation."
                )
                return self._clarify(
                    store, report_store, session,
                    [{"field": "workload_analysis", "question": question}],
                    analysis,
                )
            if not confirmed:
                confirmed = {
                    "agent_pattern": analysis["topology"]["selected"],
                    "multi_agent_count": analysis["agent_count"]["value"],
                    "modalities": analysis.get("modalities", []),
                    "tools": analysis.get("tools", []),
                }
            parameters = {**parameters, "analysis": analysis, "confirmed_profile": confirmed}
            token_result = McpPredictorClient(ROOT).predict(description, parameters)
            if route in COMMERCIAL_ROUTES:
                try:
                    result = build_commercial_result(
                        description, parameters, token_result=token_result
                    )
                except CommercialPlanClarification as exc:
                    return self._clarify(
                        store,
                        report_store,
                        session,
                        [{"field": exc.field, "question": str(exc)}],
                        analysis,
                    )
            else:
                result = attach_foundry_meter_stack(token_result)
            return self._complete_plan(
                store, report_store, session, report_id, result
            )
        except CommercialPlanClarification as exc:
            if session:
                return self._clarify(
                    store,
                    report_store,
                    session,
                    [{"field": exc.field, "question": str(exc)}],
                )
            return self._json({"status": "failed", "error": str(exc)}, 400)
        except (ValueError, json.JSONDecodeError) as exc:
            if session:
                store.fail(session, str(exc))
            return self._json({"status": "failed", "error": str(exc)}, 400)
        except McpPredictionError as exc:
            if session:
                store.fail(session, str(exc))
            return self._json({"status": "failed", "error": str(exc)}, 502)
        except OSError:
            return self._json({"status": "failed", "error": "Plan persistence failed"}, 507)

    def _complete_plan(self, store, report_store, session, report_id, result):
            session, receipt = store.complete(session, result)
            report_store.add_artifact(report_id, "plans", {
                "id": session["plan_id"], "status": session["status"],
                "prediction_id": receipt["prediction"].get("prediction_id"),
            })
            report_store.add_artifact(report_id, "receipts", {
                "id": receipt["receipt_id"], "plan_id": session["plan_id"],
                "prediction_id": receipt["prediction"].get("prediction_id"),
                "content_hash": receipt["content_hash"],
            })
            result.update(
                report_id=report_id,
                plan_id=session["plan_id"],
                receipt_id=receipt["receipt_id"],
                receipt_hash=receipt["content_hash"],
                created_at=receipt["created_at"],
            )
            return self._json(result, 201)

    def _clarify(self, store, report_store, session, questions, analysis=None):
        session = store.require_clarification(session, questions, analysis=analysis)
        report_store.add_artifact(session["report_id"], "plans", {
            "id": session["plan_id"], "status": session["status"],
        })
        return self._json(session, 201)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("request body exceeds 256000 bytes")
        if length == 0:
            return {}
        payload = json.loads(self.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    @staticmethod
    def _resource_id(path: str, index: int) -> str:
        parts = path.split("/")
        value = parts[index] if len(parts) > index else ""
        if not value or value in {".", ".."} or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in value):
            return "invalid"
        return value

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    message = (
        "plan_studio.py is no longer the primary UI entrypoint. "
        "Start the main Studio server instead: python studio.py"
    )
    print(message)
    sys.exit(1)
