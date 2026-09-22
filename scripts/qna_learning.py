"""Operator-only Q&A dataset inspection and prospective response learning."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "FutureTokenPredictor" / "src"))


def read_json(path):
    path = Path(path)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Operator input exceeds 2 MiB")
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    dataset_command = commands.add_parser("dataset", help="Verify the proposed pilot; no Azure calls")
    dataset_command.add_argument("--dataset")
    fit = commands.add_parser("fit", help="Fit an immutable candidate from eligible observations")
    fit.add_argument("--observation-id", action="append", required=True)
    fit.add_argument("--cohort", required=True, help="JSON file with the exact response cohort")
    fit.add_argument("--target", choices=("input_tokens_mean", "output_tokens_mean"),
                     default="output_tokens_mean")
    holdout = commands.add_parser("holdout", help="Evaluate a candidate on later held-out observations")
    holdout.add_argument("--candidate-id", required=True)
    holdout.add_argument("--observation-id", action="append", required=True)
    forecast = commands.add_parser("forecast", help="Complete a fresh draft with a prospective response contract")
    forecast.add_argument("--plan-id", required=True)
    forecast.add_argument("--result", required=True, help="A genuinely new predictor result JSON")
    forecast.add_argument("--baseline", required=True, help="JSON with baseline and source objects")
    forecast.add_argument("--configuration", required=True, help="Exact response configuration JSON; rechecked before dispatch")
    forecast.add_argument("--case-id", action="append", required=True)
    forecast.add_argument("--split", choices=("development", "holdout"), required=True)
    forecast.add_argument("--input-candidate")
    forecast.add_argument("--output-candidate")
    for command in ("run", "resume"):
        execution = commands.add_parser(command, help="Explicit authorized evaluation of a bound pilot batch")
        execution.add_argument("--plan-id", required=True)
        execution.add_argument("--allow-foundry-content", action="store_true",
                               help="Confirm approved Q&A/context retention and separately budgeted judge calls")
        execution.add_argument(
            "--evaluation-target",
            help=("Explicit qna-evaluation-target.v1 JSON: schema_version, project_endpoint, "
                  "judge_deployment, judge_model [name, version], judge_sku. Run defaults "
                  "to the legacy project; resume uses the immutable submission destination."),
        )
        if command == "run":
            execution.add_argument("--case-id", action="append", required=True)
            execution.add_argument("--request-id", required=True, help="Stable UUID; preserve it on retries")
        else:
            execution.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    if args.command == "dataset":
        from rag.qna_dataset import load_dataset

        dataset = load_dataset(args.dataset)
        result = {
            "dataset_id": dataset["dataset_id"], "content_hash": dataset["content_hash"],
            "human_review_status": dataset["human_review_status"],
            "cases": [{key: row[key] for key in ("case_id", "family_id", "segment_id", "partition")}
                      for row in dataset["cases"]],
            "live_execution": False,
        }
    else:
        import studio
        from costgov.response_learning import ResponseLearningStore

        learning = ResponseLearningStore(studio.RESPONSE_LEARNING_STORE_PATH)
        if args.command in {"fit", "holdout"}:
            from costgov.planning import PlanStore
            from rag.qna_dataset import load_dataset
            from rag.qna_experiment import observation_experiments, validate_training_experiments

            dataset = load_dataset()
            experiments = observation_experiments(
                learning, PlanStore(studio.PLAN_STORE_PATH), args.observation_id,
            )
            if args.command == "fit":
                validate_training_experiments(dataset, experiments)
            else:
                training = learning.candidate_experiments(args.candidate_id)
                for experiment in experiments:
                    validate_training_experiments(dataset, training, holdout=experiment["experiment"])
        if args.command == "fit":
            result = learning.fit_candidate(args.observation_id, cohort=read_json(args.cohort),
                                            target=args.target)
        elif args.command == "holdout":
            result = learning.evaluate_candidate(args.candidate_id, args.observation_id)
        elif args.command == "forecast":
            from costgov.planning import PlanStore
            from rag.qna_dataset import load_dataset
            from rag.qna_experiment import select_cases, validate_training_experiments

            dataset = load_dataset()
            _, experiment = select_cases(dataset, args.case_id, args.split)
            candidates = {}
            for target, identity in (("input_tokens_mean", args.input_candidate),
                                     ("output_tokens_mean", args.output_candidate)):
                if identity:
                    validate_training_experiments(
                        dataset, learning.candidate_experiments(identity),
                        holdout=experiment if args.split == "holdout" else None,
                    )
                    candidates[target] = learning.get_candidate(identity)
            baseline = read_json(args.baseline)
            plans = PlanStore(studio.PLAN_STORE_PATH)
            session = plans.get(args.plan_id)
            if session is None:
                raise ValueError("A saved fresh draft plan is required")
            result = plans.complete_response_forecast(
                session, read_json(args.result), configuration=read_json(args.configuration),
                baseline=baseline["baseline"], source=baseline["source"],
                candidates=candidates, experiment=experiment,
            )
        else:
            result = execute_pilot_command(args, studio)
    print(json.dumps(result, indent=2, allow_nan=False))
    return result


def execute_pilot_command(args, studio):
    if not args.allow_foundry_content:
        raise ValueError("Explicit approved Foundry retention and judge-cost acknowledgment is required")
    from azure.ai.projects import AIProjectClient
    from openai import DefaultHttpxClient
    from rag.agent_batch import _credential, _write_once, connection_status, probe_agent
    from rag.agent_batch_measurement import private_call
    from rag.foundry_evaluation_transport import JUDGES, PROJECT_ENDPOINT, submission_evaluation_target
    from rag.qna_dataset import load_dataset
    from rag.qna_pilot import resume_pilot, run_pilot
    from rag.qna_readiness import authorize_readiness
    from rag.qna_evaluation_target import check_evaluation_target, validate_evaluation_target

    service = studio._lifecycle_service()
    receipt = service.receipt(args.plan_id)
    target_path = getattr(args, "evaluation_target", None)
    target = validate_evaluation_target(read_json(target_path)) if target_path is not None else None
    if args.command == "resume":
        from rag.performance_evidence import _read_batch

        _read_batch(service, receipt, args.run_id)
        target = submission_evaluation_target(
            service.run_root / args.run_id / "qna_evaluation", target,
        )
    endpoint = target["project_endpoint"] if target is not None else PROJECT_ENDPOINT

    def authorize():
        check_id = uuid4().hex
        probe_errors = []

        def probe(configuration):
            try:
                return probe_agent(configuration)
            except Exception as exc:
                probe_errors.append({
                    "exception_type": type(exc).__name__,
                    "http_status": getattr(exc, "status_code", None),
                })
                raise

        def check():
            current = studio.load_policy_from_environment()
            status = connection_status(receipt=receipt, loaded=current, probe=probe)
            if status["ready"]:
                agent = status["public_config"]
                judge = "rag-agent-runtime-gpt-4-1-mini"
                if (target is None and (agent["deployment"] != judge
                        or (agent["model"], agent["model_version"]) != JUDGES[judge])):
                    return {"ready": False, "blockers": [{"code": "judge_binding_changed"}]}
                pinned = receipt["response_forecast"]["configuration"]["agent"]
                if (any(agent[key] != pinned[key] for key in (
                        "agent_name", "agent_version", "deployment", "model",
                        "model_version", "retrieval_mode"))
                        or agent["retrieval_evidence"]["content_hash"]
                        != pinned["retrieval_configuration_hash"]):
                    return {"ready": False, "blockers": [{"code": "response_configuration_changed"}]}
                if target is not None:
                    return check_evaluation_target(project, target)
            return status

        def record(attempt):
            _write_once(
                service.run_root / "qna_readiness" / f"{check_id}-{attempt['attempt']}.json",
                {**attempt, "plan_id": args.plan_id, "probe_errors": list(probe_errors),
                 **({"evaluation_target": target} if target is not None else {})},
            )
            probe_errors.clear()

        authorize_readiness(check, record=record)

    def register(registry_key, **updates):
        from costgov.reports import ReportStore

        studio._set_run(registry_key, **updates)
        batch = updates.get("result")
        if batch:
            ReportStore(studio.REPORT_STORE_PATH).add_artifact(
                receipt["report_id"], "runs",
                {"id": registry_key, "status": batch["execution_status"],
                 "path": f"studio_runs/{registry_key}/result.json"},
            )
            if batch["execution_status"] == "completed":
                from costgov.response_learning import ResponseLearningStore, build_response_observation
                from rag.performance_evidence import _observation

                usage = _observation(studio._lifecycle_service(), batch)
                ResponseLearningStore(studio.RESPONSE_LEARNING_STORE_PATH).append(
                    build_response_observation(receipt=receipt, batch=batch, observation=usage),
                )

    with (private_call(), _credential() as credential,
          AIProjectClient(endpoint=endpoint, credential=credential, retry_total=0,
                          connection_timeout=10, read_timeout=15,
                          logging_enable=False, tracing_enable=False) as project,
          DefaultHttpxClient(follow_redirects=False, timeout=30) as transport,
          project.get_openai_client(max_retries=0, timeout=30, http_client=transport) as client):
        if args.command == "resume":
            return resume_pilot(service, args.plan_id, args.run_id, load_dataset(),
                                client=client, actor="operator-cli", consent=True,
                                evaluation_target=target)
        authorize()
        result = run_pilot(
            service, args.plan_id, load_dataset(), args.case_id, request_id=args.request_id,
            loaded=studio.load_policy_from_environment(), actor="operator-cli", root=ROOT,
            register=register, refresh_service=studio._lifecycle_service,
            client=client, authorize=authorize, consent=True,
            evaluation_target=target, evaluation_project=project if target is not None else None,
        )
        return result


if __name__ == "__main__":
    main()
