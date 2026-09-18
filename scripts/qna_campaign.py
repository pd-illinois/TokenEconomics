"""Create, inspect, and resume a policy-bound Foundry Q&A campaign."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _read(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON input must be an object")
    return value


def _connection(studio, receipt):
    from rag.agent_batch import connection_status, probe_agent

    loaded = studio.load_policy_from_environment()
    status = connection_status(receipt=receipt, loaded=loaded, probe=probe_agent)
    if not status["ready"]:
        raise ValueError("Campaign connection is not ready: " + ", ".join(
            blocker["code"] for blocker in status["blockers"]
        ))
    return loaded, status["public_config"]


def _register(studio, receipt):
    def register(registry_key, **updates):
        from costgov.reports import ReportStore

        studio._set_run(registry_key, **updates)
        batch = updates.get("result")
        if not batch:
            return
        ReportStore(studio.REPORT_STORE_PATH).add_artifact(
            receipt["report_id"], "runs", {
                "id": registry_key, "status": batch["execution_status"],
                "execution_status": batch["execution_status"],
                "schema_version": batch["schema_version"], "plan_id": receipt["plan_id"],
                "receipt_hash": receipt["content_hash"],
                "evidence_status": batch["evidence"]["status"],
                "cloud_status": batch["evidence"]["cloud_status"],
                "path": f"studio_runs/{registry_key}/result.json",
            },
        )
    return register


def _execute(args, studio, campaign):
    if not args.allow_foundry_content:
        raise ValueError(
            "Explicit approved Foundry retention and judge-cost acknowledgment is required"
        )
    from azure.ai.projects import AIProjectClient
    from openai import DefaultHttpxClient
    from rag.agent_batch import _credential, _write_once, connection_status, probe_agent
    from rag.agent_batch import execute as execute_batch
    from rag.agent_batch_measurement import private_call
    from rag.foundry_evaluation_transport import PROJECT_ENDPOINT
    from rag.qna_campaign import (
        campaign_status, execute_next_repetition, resume_pending_evaluation,
    )
    from rag.qna_dataset import load_dataset
    from rag.qna_evaluation_target import check_evaluation_target, validate_evaluation_target
    from rag.qna_pilot import resume_pilot, run_pilot
    from rag.qna_readiness import authorize_readiness

    service = studio._lifecycle_service()
    receipt = service.receipt(campaign["plan_id"])
    dataset = load_dataset()
    target = validate_evaluation_target(_read(args.evaluation_target)) if args.evaluation_target else None
    endpoint = target["project_endpoint"] if target else PROJECT_ENDPOINT

    with (private_call(), _credential() as credential,
          AIProjectClient(
              endpoint=endpoint, credential=credential, retry_total=0,
              connection_timeout=10, read_timeout=15,
              logging_enable=False, tracing_enable=False,
          ) as project,
          DefaultHttpxClient(follow_redirects=False, timeout=30) as transport,
          project.get_openai_client(
              max_retries=0, timeout=30, http_client=transport,
          ) as client):

        def stable_probe(configuration):
            failure = None
            for attempt in range(5):
                try:
                    result = probe_agent(configuration)
                    if result.get("retrieval_mode") != "managed_mcp_hybrid_verified":
                        raise ValueError("managed retrieval inventory was transiently unavailable")
                    return result
                except Exception as exc:
                    failure = exc
                    if attempt < 4:
                        time.sleep(2)
            raise failure

        def current():
            loaded = studio.load_policy_from_environment()
            status = connection_status(receipt=receipt, loaded=loaded, probe=stable_probe)
            if not status["ready"]:
                return loaded, status, None
            if status["public_config"] != campaign["agent"]:
                return loaded, {"ready": False, "blockers": [
                    {"code": "campaign_agent_binding_changed"},
                ]}, status["public_config"]
            if target is not None:
                target_status = check_evaluation_target(project, target)
                if not target_status["ready"]:
                    return loaded, target_status, status["public_config"]
            return loaded, status, status["public_config"]

        def authorize():
            check_id = uuid4().hex

            def check():
                return current()[1]

            def record(attempt):
                _write_once(
                    service.run_root / "qna_readiness" / f"campaign-{check_id}-{attempt['attempt']}.json",
                    {
                        **attempt, "campaign_id": campaign["campaign_id"],
                        "campaign_hash": campaign["content_hash"],
                        "plan_id": campaign["plan_id"],
                        **({"evaluation_target": target} if target else {}),
                    },
                )

            authorize_readiness(check, record=record)

        active = {"loaded": None, "agent": None}

        def batch_runner(*runner_args, **runner_kwargs):
            return execute_batch(
                *runner_args, **runner_kwargs, probe=stable_probe,
                policy_loader=lambda _: studio.load_policy_from_environment(),
            )

        def one_runner(*, campaign, request_id):
            authorize()
            return run_pilot(
                service, campaign["plan_id"], dataset, campaign["case_ids"],
                request_id=request_id, loaded=active["loaded"], actor="operator-cli",
                root=ROOT, register=_register(studio, receipt),
                refresh_service=studio._lifecycle_service, client=client,
                authorize=authorize, consent=True, evaluation_target=target,
                evaluation_project=project if target else None,
                campaign=campaign, campaign_agent=active["agent"],
                runner=batch_runner,
            )

        def resume_runner(*, run_id):
            return resume_pilot(
                service, campaign["plan_id"], run_id, dataset, client=client,
                actor="operator-cli", consent=True, evaluation_target=target,
            )

        status = campaign_status(service, campaign["campaign_id"])
        executed = 0
        while executed < args.max_repetitions:
            if status["stop_reason"] == "evaluation_pending":
                status = resume_pending_evaluation(
                    service, campaign["campaign_id"], resumer=resume_runner,
                )
                if status["stop_reason"] == "evaluation_pending":
                    break
                continue
            if status["stop_reason"] is not None:
                break
            loaded, readiness, agent = current()
            if not readiness["ready"] or agent is None:
                raise ValueError("Campaign is not ready: " + ", ".join(
                    row["code"] for row in readiness["blockers"]
                ))
            active.update(loaded=loaded, agent=agent)
            status = execute_next_repetition(
                service, campaign["campaign_id"], dataset, loaded=loaded,
                actor="operator-cli", runner=one_runner, agent=agent,
            )
            executed += 1
            if status["stop_reason"] not in (None, "evaluation_pending"):
                break
        return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Create immutable campaign evidence; no inference")
    create.add_argument("--plan-id", required=True)
    create.add_argument("--request-id", required=True)
    create.add_argument("--repetitions", type=int, default=100)
    create.add_argument("--case-id", action="append")
    status = commands.add_parser("status", help="Inspect immutable campaign progress")
    status.add_argument("--campaign-id", required=True)
    run = commands.add_parser("run", help="Execute bounded resumable campaign repetitions")
    run.add_argument("--campaign-id", required=True)
    run.add_argument("--max-repetitions", type=int, default=1)
    run.add_argument("--allow-foundry-content", action="store_true")
    run.add_argument("--evaluation-target")
    args = parser.parse_args(argv)

    import studio
    from rag.qna_campaign import campaign_quality_summary, campaign_status, create_campaign, load_campaign
    from rag.qna_dataset import load_dataset

    service = studio._lifecycle_service()
    dataset = load_dataset()
    if args.command == "create":
        receipt = service.receipt(args.plan_id)
        loaded, agent = _connection(studio, receipt)
        case_ids = args.case_id or [case["case_id"] for case in dataset["cases"]]
        result = create_campaign(
            service, args.plan_id, dataset, case_ids,
            repetitions=args.repetitions, request_id=args.request_id,
            loaded=loaded, actor="operator-cli", agent=agent,
        )
    else:
        campaign = load_campaign(service, args.campaign_id)
        if args.command == "run":
            if not 1 <= args.max_repetitions <= campaign["repetitions"]:
                raise ValueError("max-repetitions is outside the campaign bound")
            result = _execute(args, studio, campaign)
        else:
            result = {
                "campaign": campaign_status(service, args.campaign_id),
                "quality": campaign_quality_summary(
                    service, campaign["plan_id"], args.campaign_id,
                ),
            }
    print(json.dumps(result, indent=2, allow_nan=False))
    return result


if __name__ == "__main__":
    main()
