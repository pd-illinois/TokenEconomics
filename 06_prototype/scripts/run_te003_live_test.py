"""Create and execute one dedicated TE-003 deployed-RAG proof report."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from costgov.commercial_planning import attach_foundry_meter_stack
from costgov.mcp_prediction import McpPredictorClient
from costgov.planning import PlanStore
from costgov.policy_store import load_policy_from_environment
from costgov.reports import ReportStore
from costgov.trajectory_contracts import TrajectoryStore
from rag.foundry_trajectory_adapter import (
    FoundryAgentConfig,
    FoundryRagTrajectoryAdapter,
    FoundryTrajectoryRequest,
    create_foundry_openai_client,
)

DEFAULT_DESCRIPTION = (
    "A single Microsoft Foundry prompt agent answers 500 product-risk requests "
    "per day. For every request, the agent must call the Foundry IQ knowledge "
    "base through MCP, retrieve controlled documents from Azure AI Search, and "
    "synthesize a cited answer with GPT-5.6 Luna."
)
DEFAULT_QUESTION = (
    "Who created the creature in Frankenstein, and what relationship does the "
    "creature claim to that creator? Cite the source."
)


def build_test_parameters() -> dict:
    """Return explicit selected controls; prose inference cannot override them."""
    return {
        "route": "foundry",
        "model": "gpt-5-6-luna",
        "provider": "azure_openai",
        "users": 100,
        "calls_per_user_per_day": 5,
        "analysis_confirmed": True,
        "confirmed_profile": {
            "agent_pattern": "rag_pipeline",
            "multi_agent_count": 1,
            "modalities": ["text", "document"],
            "tools": ["file_search", "mcp_server", "rag"],
            "document_count": 1,
            "document_pages": 5,
            "searches_per_call": 1,
        },
        "workload_version": "te003-books-rag.v1",
        "segment_schema_version": "segment.v1",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a dedicated schema-5 Plan report, admit it against Azure "
            "App Configuration, invoke the pinned Foundry RAG agent, and reopen "
            "the immutable trajectory."
        )
    )
    parser.add_argument(
        "--project-endpoint",
        default=(
            "https://ai-account-xbk6ickycmp22.services.ai.azure.com/api/projects/"
            "ai-project-tokeneconomics-te003"
        ),
    )
    parser.add_argument("--agent-name", default="tokengov-books-rag-agent")
    parser.add_argument("--agent-version", default="1")
    parser.add_argument(
        "--policy-endpoint",
        default="https://appcs-xbk6ickycmp22.azconfig.io",
    )
    parser.add_argument("--policy-key", default="tokengov:policy")
    parser.add_argument("--policy-label", default="te003-live-v2")
    parser.add_argument("--description", default=DEFAULT_DESCRIPTION)
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--segment-id", default="factual-lookup")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "rag" / "captured_trajectories",
    )
    return parser


def _record_plan_artifacts(
    report_store: ReportStore,
    report_id: str,
    session: dict,
    receipt: dict,
) -> None:
    prediction_id = receipt["prediction"]["prediction_id"]
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


def _record_handoff(
    report_store: ReportStore,
    handoff: dict,
) -> None:
    report_store.add_artifact(
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
            "policy": handoff["policy"],
            "checks": handoff["checks"],
            "evaluated_at": handoff["evaluated_at"],
        },
    )


def main() -> int:
    args = _parser().parse_args()
    os.environ["AZURE_APPCONFIG_ENDPOINT"] = args.policy_endpoint
    os.environ["TOKENGOV_POLICY_KEY"] = args.policy_key
    os.environ["TOKENGOV_POLICY_LABEL"] = args.policy_label
    os.environ["TOKENGOV_POLICY_SOURCE"] = "azure"

    report_store = ReportStore(ROOT / "studio_reports")
    plan_store = PlanStore(ROOT / "studio_plans")
    report = report_store.create("TE-003 deployed Foundry RAG live proof")
    report_id = report["report_id"]
    parameters = build_test_parameters()
    session = plan_store.create_session(report_id, args.description, parameters)
    report_store.add_artifact(
        report_id,
        "plans",
        {"id": session["plan_id"], "status": session["status"]},
    )

    analysis = McpPredictorClient(ROOT).analyze(args.description)
    prediction_parameters = {**parameters, "analysis": analysis}
    result = attach_foundry_meter_stack(
        McpPredictorClient(ROOT).predict(args.description, prediction_parameters)
    )
    session, receipt = plan_store.complete(session, result)
    _record_plan_artifacts(report_store, report_id, session, receipt)

    policy = load_policy_from_environment()
    handoff = plan_store.create_govern_handoff(session["plan_id"], policy)
    _record_handoff(report_store, handoff)
    if handoff["status"] != "admitted":
        failed = [check["name"] for check in handoff["checks"] if not check["passed"]]
        print(json.dumps({"report_id": report_id, "status": "rejected", "failed": failed}, indent=2))
        return 2

    config = FoundryAgentConfig(
        project_endpoint=args.project_endpoint,
        agent_name=args.agent_name,
        agent_version=args.agent_version,
    )
    run_id = f"run-{uuid4().hex}"
    task_id = f"task-{uuid4().hex}"
    trajectory_id = f"trajectory-{uuid4().hex}"
    request = FoundryTrajectoryRequest(
        run_id=run_id,
        report_id=report_id,
        task_id=task_id,
        trajectory_id=trajectory_id,
        trace_id=uuid4().hex,
        segment_id=args.segment_id,
        segment_version="segment.v1",
        question=args.question,
        task_created_at=datetime.now(timezone.utc).isoformat(),
    )
    store = TrajectoryStore(args.output_dir)
    capture = FoundryRagTrajectoryAdapter(
        create_foundry_openai_client(config),
        config,
    ).capture(request=request, admission=handoff, store=store)
    reopened = store.get(trajectory_id)
    if reopened != capture.record:
        raise RuntimeError("persisted trajectory did not reopen with matching evidence")

    report_store.add_artifact(
        report_id,
        "runs",
        {
            "id": run_id,
            "status": "completed",
            "evidence_status": "measured_live",
            "plan_id": session["plan_id"],
            "handoff_id": handoff["handoff_id"],
            "trajectory_id": trajectory_id,
            "conversation_id": capture.conversation_id,
            "response_id": capture.response_id,
            "content_hash": capture.record.content_hash,
        },
    )
    print(
        json.dumps(
            {
                "report_id": report_id,
                "plan_id": session["plan_id"],
                "receipt_id": receipt["receipt_id"],
                "handoff_id": handoff["handoff_id"],
                "policy_version": handoff["policy"]["version"],
                "policy_etag": handoff["policy"]["provenance"]["etag"],
                "run_id": run_id,
                "trajectory_id": trajectory_id,
                "conversation_id": capture.conversation_id,
                "response_id": capture.response_id,
                "content_hash": capture.record.content_hash,
                "reopened": True,
                "evidence_status": "measured_live",
                "response_text": capture.response_text,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
