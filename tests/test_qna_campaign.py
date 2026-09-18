from copy import deepcopy
from uuid import uuid4

import pytest

from costgov.studio_lifecycle import digest
from rag.qna_campaign import (
    campaign_status,
    create_campaign,
    execute_next_repetition,
    load_campaign,
    resume_pending_evaluation,
    validate_campaign_dispatch,
)
from rag.qna_dataset import load_dataset
from test_agent_batch_measurement import measured, verified
from test_books_playground import proof


def campaign_policy():
    return {
        "schema_version": "workload-measurement-policy.v2",
        "mode": "measurement_only",
        "workload_scope": "studio_campaigns",
        "max_questions": 25,
        "max_output_tokens": 1024,
        "max_elapsed_seconds": 600,
        "observed_model_cost_stop_usd": 0.25,
        "max_campaign_repetitions": 100,
        "max_campaign_questions": 2500,
        "max_evaluation_runs": 100,
        "max_evaluation_rows_per_run": 25,
        "campaign_observed_model_cost_stop_usd": 25,
        "require_explicit_campaign_id": True,
        "expires_at": "2099-01-01T00:00:00Z",
        "acknowledge_incomplete_costs": True,
        "hard_spend_cap_guaranteed": False,
        "operational_promotion": False,
    }


def create(measured, *, repetitions=2, case_count=2):
    service, receipt, loaded, _ = measured
    loaded.document["measurement"] = campaign_policy()
    dataset = load_dataset()
    case_ids = [case["case_id"] for case in dataset["cases"][:case_count]]
    agent = verified({})
    campaign = create_campaign(
        service, receipt["plan_id"], dataset, case_ids,
        repetitions=repetitions, request_id=str(uuid4()),
        loaded=loaded, actor="operator", agent=agent,
    )
    return service, receipt, loaded, dataset, agent, campaign


def result(campaign, repetition, amount=0.001):
    body = {
        "schema_version": "rag-agent-batch.v2",
        "run_id": f"run-{repetition:032x}",
        "execution_status": "completed",
        "stop_reason": "completed",
        "observed_model_allocation_usd": amount,
    }
    body["evidence"] = {"content_hash": digest(body)}
    return {
        "quality_recorded": True,
        "evaluation_id": f"eval-{repetition}",
        "evaluation_run_id": f"eval-run-{repetition}",
        "record_id": f"quality-{repetition}",
        "batch": body,
    }


def test_campaign_is_immutable_idempotent_and_binds_cross_partition_dataset(measured):
    service, receipt, loaded, dataset, agent, campaign = create(
        measured, repetitions=100, case_count=25,
    )
    replay = create_campaign(
        service, receipt["plan_id"], dataset, campaign["case_ids"],
        repetitions=100, request_id=campaign["request_id"],
        loaded=loaded, actor="operator", agent=agent,
    )
    assert replay == campaign == load_campaign(service, campaign["campaign_id"])
    cases = validate_campaign_dispatch(
        campaign, receipt, dataset, campaign["case_ids"], loaded, agent,
    )
    assert len(cases) == 25
    assert {case["partition"] for case in cases} == {"development", "holdout"}
    assert campaign["target_attempts"] == 2500
    assert campaign["cost_scope"] == "response_model_only_not_task_total"
    assert campaign["operational_promotion"] is False


def test_campaign_executes_stable_repetitions_and_reports_attempts_separately(measured):
    service, _, loaded, dataset, agent, campaign = create(measured)
    calls = []

    def runner(**kwargs):
        calls.append(kwargs["request_id"])
        return result(campaign, len(calls))

    first = execute_next_repetition(
        service, campaign["campaign_id"], dataset, loaded=loaded,
        actor="operator", runner=runner, agent=agent,
    )
    second = execute_next_repetition(
        service, campaign["campaign_id"], dataset, loaded=loaded,
        actor="operator", runner=runner, agent=agent,
    )
    assert first["completed_repetitions"] == 1
    assert first["completed_attempts"] == 2
    assert second["status"] == "completed"
    assert second["completed_repetitions"] == 2
    assert second["completed_attempts"] == 4
    assert second["unique_case_count"] == 2
    assert len(set(calls)) == 2
    assert execute_next_repetition(
        service, campaign["campaign_id"], dataset, loaded=loaded,
        actor="operator", runner=lambda **_: pytest.fail("completed campaigns cannot replay"),
        agent=agent,
    ) == second


def test_campaign_observed_cost_stop_blocks_the_next_repetition(measured):
    service, _, loaded, dataset, agent, campaign = create(measured, repetitions=3)
    loaded.document["measurement"]["observed_model_cost_stop_usd"] = 0.0005
    loaded.document["measurement"]["campaign_observed_model_cost_stop_usd"] = 0.001
    campaign = create_campaign(
        service, campaign["plan_id"], dataset, campaign["case_ids"],
        repetitions=3, request_id=str(uuid4()), loaded=loaded,
        actor="operator", agent=agent,
    )
    calls = []

    def runner(**_):
        calls.append(True)
        return result(campaign, len(calls), amount=0.0006)

    execute_next_repetition(
        service, campaign["campaign_id"], dataset, loaded=loaded,
        actor="operator", runner=runner, agent=agent,
    )
    stopped = execute_next_repetition(
        service, campaign["campaign_id"], dataset, loaded=loaded,
        actor="operator", runner=runner, agent=agent,
    )
    assert len(calls) == 2
    assert stopped["status"] == "stopped"
    assert stopped["stop_reason"] == "campaign_observed_model_cost_stop"
    assert stopped["observed_model_allocation_usd"] == pytest.approx(0.0012)
    assert execute_next_repetition(
        service, campaign["campaign_id"], dataset, loaded=loaded,
        actor="operator", runner=lambda **_: pytest.fail("stop must prevent dispatch"),
        agent=agent,
    ) == stopped


def test_preflight_failure_before_batch_claim_is_safely_retryable(measured):
    service, _, loaded, dataset, agent, campaign = create(measured)
    with pytest.raises(RuntimeError, match="synthetic crash"):
        execute_next_repetition(
            service, campaign["campaign_id"], dataset, loaded=loaded,
            actor="operator", runner=lambda **_: (_ for _ in ()).throw(
                RuntimeError("synthetic crash")
            ), agent=agent,
        )
    status = campaign_status(service, campaign["campaign_id"])
    assert status["status"] == "ready"
    assert status["stop_reason"] is None
    recovered = execute_next_repetition(
        service, campaign["campaign_id"], dataset, loaded=loaded,
        actor="operator", runner=lambda **_: result(campaign, 1), agent=agent,
    )
    assert recovered["completed_repetitions"] == 1


def test_batch_claim_without_result_is_in_doubt_and_never_replayed(measured):
    service, _, loaded, dataset, agent, campaign = create(measured)
    with pytest.raises(RuntimeError, match="synthetic crash"):
        execute_next_repetition(
            service, campaign["campaign_id"], dataset, loaded=loaded,
            actor="operator", runner=lambda **kwargs: (
                (service.run_root / "rag_batch_claims").mkdir(parents=True, exist_ok=True),
                (service.run_root / "rag_batch_claims" / f"{kwargs['request_id']}.json").write_text(
                    "{}", encoding="utf-8",
                ),
                (_ for _ in ()).throw(RuntimeError("synthetic crash")),
            )[-1], agent=agent,
        )
    status = campaign_status(service, campaign["campaign_id"])
    assert status["status"] == "stopped"
    assert status["stop_reason"] == "repetition_in_doubt"
    assert execute_next_repetition(
        service, campaign["campaign_id"], dataset, loaded=loaded,
        actor="operator", runner=lambda **_: pytest.fail("in-doubt work cannot replay"),
        agent=agent,
    ) == status


def test_submitted_evaluation_can_resume_without_reexecuting_batch(measured):
    service, _, loaded, dataset, agent, campaign = create(measured)
    outcome = result(campaign, 1)
    outcome["quality_recorded"] = False
    outcome["evaluation_id"] = None
    outcome["evaluation_run_id"] = None
    outcome["record_id"] = None
    paused = execute_next_repetition(
        service, campaign["campaign_id"], dataset, loaded=loaded,
        actor="operator", runner=lambda **_: outcome, agent=agent,
    )
    assert paused["status"] == "paused"
    assert paused["stop_reason"] == "evaluation_pending"
    resumed = resume_pending_evaluation(
        service, campaign["campaign_id"], resumer=lambda **kwargs: {
            "quality_recorded": True, "evaluation_id": "eval-1",
            "evaluation_run_id": "eval-run-1", "record_id": "quality-1",
            "run_id": kwargs["run_id"],
        },
    )
    assert resumed["status"] == "ready"
    assert resumed["completed_repetitions"] == 1
    assert resumed["completed_attempts"] == 2


def test_campaign_rejects_policy_or_agent_drift_before_dispatch(measured):
    service, receipt, loaded, dataset, agent, campaign = create(measured)
    changed = deepcopy(agent)
    changed["agent_version"] = "different"
    with pytest.raises(ValueError, match="changed"):
        validate_campaign_dispatch(
            campaign, receipt, dataset, campaign["case_ids"], loaded, changed,
        )
