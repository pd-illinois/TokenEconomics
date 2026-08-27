"""Capture one admitted Microsoft Foundry RAG trajectory."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from costgov.trajectory_contracts import TrajectoryStore
from rag.foundry_trajectory_adapter import (
    FoundryAgentConfig,
    FoundryRagTrajectoryAdapter,
    FoundryTrajectoryRequest,
    create_foundry_openai_client,
)


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Invoke the deployed Foundry RAG agent only after validating an "
            "admitted TokenEconomics trajectory binding."
        )
    )
    parser.add_argument(
        "--admission",
        required=True,
        type=Path,
        help="JSON file containing an admitted Govern handoff.",
    )
    parser.add_argument("--question", required=True)
    parser.add_argument("--report-id", required=True)
    parser.add_argument("--segment-id", required=True)
    parser.add_argument("--segment-version", default="segment.v1")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "rag" / "captured_trajectories",
    )
    parser.add_argument("--run-id")
    parser.add_argument("--task-id")
    parser.add_argument("--trajectory-id")
    parser.add_argument("--trace-id")
    parser.add_argument(
        "--project-endpoint",
        default=os.environ.get("FOUNDRY_PROJECT_ENDPOINT"),
    )
    parser.add_argument(
        "--agent-name",
        default=os.environ.get("FOUNDRY_AGENT_NAME"),
    )
    parser.add_argument(
        "--agent-version",
        default=os.environ.get("FOUNDRY_AGENT_VERSION"),
    )
    return parser


def _resolved(value: str | None, prefix: str) -> str:
    return value or f"{prefix}-{uuid4().hex}"


def main() -> int:
    load_dotenv(ROOT / ".env")
    parser = _argument_parser()
    args = parser.parse_args()
    if not args.admission.is_file():
        parser.error(f"admission file does not exist: {args.admission}")

    config = FoundryAgentConfig(
        project_endpoint=args.project_endpoint,
        agent_name=args.agent_name,
        agent_version=args.agent_version,
    )
    admission = json.loads(args.admission.read_text(encoding="utf-8"))
    created_at = datetime.now(timezone.utc).isoformat()
    request = FoundryTrajectoryRequest(
        run_id=_resolved(args.run_id, "run"),
        report_id=args.report_id,
        task_id=_resolved(args.task_id, "task"),
        trajectory_id=_resolved(args.trajectory_id, "trajectory"),
        trace_id=args.trace_id or uuid4().hex,
        segment_id=args.segment_id,
        segment_version=args.segment_version,
        question=args.question,
        task_created_at=created_at,
    )

    client = create_foundry_openai_client(config)
    capture = FoundryRagTrajectoryAdapter(client, config).capture(
        request=request,
        admission=admission,
        store=TrajectoryStore(args.output_dir),
    )
    print(
        json.dumps(
            {
                "evidence_status": "measured_live",
                "trajectory_id": capture.record.envelope.trajectory_id,
                "task_id": capture.record.envelope.task.task_id,
                "conversation_id": capture.conversation_id,
                "response_id": capture.response_id,
                "content_hash": capture.record.content_hash,
                "response_text": capture.response_text,
                "store": str(args.output_dir.resolve()),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
