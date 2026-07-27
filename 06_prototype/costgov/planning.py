"""Durable Studio plan sessions and immutable planning receipts."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from typing import TYPE_CHECKING
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .contracts import PlanReceipt

if TYPE_CHECKING:
    from .policy_store import LoadedPolicy

SCHEMA_VERSION = "2.0"
_plan_lock = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class PlanStore:
    """File-backed plan registry with write-once receipt artifacts."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def create_session(self, report_id: str, description: str, parameters: dict) -> dict:
        plan_id = str(uuid4())
        timestamp = _now()
        session = {
            "plan_id": plan_id,
            "report_id": report_id,
            "status": "draft",
            "description": description,
            "parameters": parameters,
            "clarifications": [],
            "created_at": timestamp,
            "updated_at": timestamp,
            "receipt_id": None,
            "receipt_hash": None,
            "govern_handoff": None,
        }
        self._write_session(session)
        return session

    def require_clarification(
        self, session: dict, questions: list[dict], analysis: dict | None = None
    ) -> dict:
        session.update(
            status="needs_clarification",
            clarifications=questions,
            updated_at=_now(),
        )
        if analysis is not None:
            session["analysis"] = analysis
        self._write_session(session)
        return session

    def resume_session(self, session: dict, description: str, parameters: dict) -> dict:
        if session["status"] not in {"draft", "needs_clarification", "failed"}:
            raise ValueError("completed plans cannot be changed")
        session.update(
            status="draft",
            description=description or session["description"],
            parameters={**session.get("parameters", {}), **parameters},
            updated_at=_now(),
        )
        self._write_session(session)
        return session

    def fail(self, session: dict, error: str) -> dict:
        session.update(status="failed", error=error, updated_at=_now())
        self._write_session(session)
        return session

    def complete(self, session: dict, result: dict) -> tuple[dict, dict]:
        created_at = _now()
        intake = result["intake"]
        analysis = intake.get("analysis", {})
        confirmed_profile = intake.get("confirmed_profile", {})
        assumptions = analysis.get("assumptions", [])
        clarifications = analysis.get("clarifications", [])
        exclusions = analysis.get("exclusions", [])
        snapshot = {
            "report_id": session["report_id"],
            "plan_id": session["plan_id"],
            "schema_version": SCHEMA_VERSION,
            "created_at": created_at,
            "description": result["description"],
            "intake": intake,
            "analysis": analysis,
            "confirmed_profile": confirmed_profile,
            "assumptions": assumptions,
            "clarifications": clarifications,
            "exclusions": exclusions,
            "prediction": result["prediction"],
            "infrastructure": result["infrastructure"],
        }
        content_hash = hashlib.sha256(_canonical(snapshot).encode("utf-8")).hexdigest()
        receipt = PlanReceipt(
            receipt_id=f"plan_{content_hash[:20]}",
            report_id=session["report_id"],
            plan_id=session["plan_id"],
            schema_version=SCHEMA_VERSION,
            created_at=created_at,
            description=result["description"],
            intake_json=_canonical(intake),
            analysis_json=_canonical(analysis),
            confirmed_profile_json=_canonical(confirmed_profile),
            assumptions_json=_canonical(assumptions),
            clarifications_json=_canonical(clarifications),
            exclusions_json=_canonical(exclusions),
            prediction_json=_canonical(result["prediction"]),
            infrastructure_json=_canonical(result["infrastructure"]),
            content_hash=content_hash,
        )
        receipt_payload = self._receipt_payload(receipt)
        self._write_receipt(receipt_payload)
        session.update(
            status="complete",
            intake=result["intake"],
            updated_at=created_at,
            receipt_id=receipt.receipt_id,
            receipt_hash=receipt.content_hash,
            clarifications=[],
        )
        self._write_session(session)
        return session, receipt_payload

    def get(self, plan_id: str) -> dict | None:
        path = self.root / plan_id / "session.json"
        with _plan_lock:
            if not path.exists():
                return None
            return json.loads(path.read_text(encoding="utf-8"))

    def list(self) -> list[dict]:
        if not self.root.exists():
            return []
        sessions = [self.get(path.name) for path in self.root.iterdir() if path.is_dir()]
        return sorted(
            (session for session in sessions if session is not None),
            key=lambda session: session["created_at"],
            reverse=True,
        )

    def get_receipt(self, plan_id: str) -> dict | None:
        with _plan_lock:
            session = self.get(plan_id)
            if not session or not session.get("receipt_id"):
                return None
            path = self.root / plan_id / "receipts" / f"{session['receipt_id']}.json"
            return json.loads(path.read_text(encoding="utf-8"))

    def get_govern_handoff(self, plan_id: str) -> dict | None:
        session = self.get(plan_id)
        if not session or not session.get("govern_handoff"):
            return None
        handoff = session["govern_handoff"]
        if "economics" not in handoff:
            receipt = self.get_receipt(plan_id)
            if receipt:
                prediction = receipt["prediction"]
                handoff["economics"] = {
                    "cost_per_call": prediction.get("cost_per_call"),
                    "monthly_cost": prediction.get("monthly_cost"),
                    "annual_cost": prediction.get("annual_cost"),
                    "tokens_per_call": prediction.get("tokens_per_call"),
                }
                session["govern_handoff"] = handoff
                self._write_session(session)
        return handoff

    def create_govern_handoff(self, plan_id: str, policy: LoadedPolicy) -> dict:
        from .policy_store import admit_receipt

        session = self.get(plan_id)
        if not session:
            raise KeyError(plan_id)
        if session["status"] not in {"complete", "handed_off"}:
            raise ValueError("plan must be complete before Govern handoff")
        existing_handoff = self.get_govern_handoff(plan_id)
        if (
            existing_handoff
            and existing_handoff.get("status") in {"admitted", "rejected"}
            and existing_handoff.get("policy")
        ):
            return existing_handoff
        receipt = self.get_receipt(plan_id)
        admission = admit_receipt(receipt, policy)
        prediction = receipt["prediction"]
        handoff = {
            "handoff_id": str(uuid4()),
            "report_id": session["report_id"],
            "status": admission["status"],
            "created_at": _now(),
            "plan_id": plan_id,
            "receipt_id": receipt["receipt_id"],
            "receipt_hash": receipt["content_hash"],
            "prediction_id": prediction.get("prediction_id"),
            "economics": {
                "cost_per_call": prediction.get("cost_per_call"),
                "monthly_cost": prediction.get("monthly_cost"),
                "annual_cost": prediction.get("annual_cost"),
                "tokens_per_call": prediction.get("tokens_per_call"),
            },
            "policy": admission["policy"],
            "checks": admission["checks"],
            "execution": admission["execution"],
            "mutation": admission["mutation"],
            "evaluated_at": admission["evaluated_at"],
            "infrastructure_status": receipt["infrastructure"]["status"],
        }
        session.update(status="handed_off", govern_handoff=handoff, updated_at=_now())
        self._write_session(session)
        return handoff

    def _write_session(self, session: dict) -> None:
        path = self.root / session["plan_id"] / "session.json"
        temporary = path.with_suffix(".tmp")
        with _plan_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(session, indent=2), encoding="utf-8")
            os.replace(temporary, path)

    def _write_receipt(self, receipt: dict) -> None:
        path = self.root / receipt["plan_id"] / "receipts" / f"{receipt['receipt_id']}.json"
        with _plan_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as stream:
                json.dump(receipt, stream, indent=2)

    @staticmethod
    def _receipt_payload(receipt: PlanReceipt) -> dict:
        payload = asdict(receipt)
        payload["intake"] = json.loads(payload.pop("intake_json"))
        payload["analysis"] = json.loads(payload.pop("analysis_json"))
        payload["confirmed_profile"] = json.loads(payload.pop("confirmed_profile_json"))
        payload["assumptions"] = json.loads(payload.pop("assumptions_json"))
        payload["clarifications"] = json.loads(payload.pop("clarifications_json"))
        payload["exclusions"] = json.loads(payload.pop("exclusions_json"))
        payload["prediction"] = json.loads(payload.pop("prediction_json"))
        payload["infrastructure"] = json.loads(payload.pop("infrastructure_json"))
        return payload