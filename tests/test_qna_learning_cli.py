from pathlib import Path
import runpy
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from test_response_forecasts import prospective, evidence


CLI = Path(__file__).parents[1] / "scripts" / "qna_learning.py"


@pytest.fixture
def operator(tmp_path, monkeypatch):
    import azure.ai.projects
    import azure.identity
    from rag.foundry_evaluation_transport import PROJECT_ENDPOINT
    from test_foundry_evaluation_transport import FakeClient
    from test_qna_evaluation_target import FakeProject, target_binding

    target = target_binding()
    target_path = tmp_path / "target.json"
    target_path.write_text(json.dumps(target))
    agent = {
        "agent_name": "tokengov-books-rag-agent", "agent_version": "4",
        "deployment": "source-only-deployment", "model": "source-model",
        "model_version": "source-version", "retrieval_mode": "managed_mcp_hybrid",
        "retrieval_evidence": {"content_hash": "retrieval-hash"},
    }
    pinned = {key: value for key, value in agent.items() if key != "retrieval_evidence"}
    pinned["retrieval_configuration_hash"] = "retrieval-hash"
    receipt = {"plan_id": "plan-fixture", "response_forecast": {"configuration": {"agent": pinned}}}
    service = SimpleNamespace(run_root=tmp_path, receipt=lambda _: receipt)
    studio = SimpleNamespace(_lifecycle_service=lambda: service, load_policy_from_environment=lambda: object())
    project, client, constructed, dispatched = FakeProject(), FakeClient(), [], []
    client.base_url = target["project_endpoint"] + "/openai/v1"

    @contextmanager
    def credential():
        yield object()

    @contextmanager
    def openai_client(**kwargs):
        assert kwargs["max_retries"] == 0 and kwargs["timeout"] == 30
        assert kwargs["http_client"].follow_redirects is False
        yield client

    project.get_openai_client = openai_client

    @contextmanager
    def factory(**kwargs):
        constructed.append(kwargs)
        assert kwargs["retry_total"] == 0
        assert kwargs["logging_enable"] is kwargs["tracing_enable"] is False
        project._config.endpoint = kwargs["endpoint"]
        client.base_url = kwargs["endpoint"] + "/openai/v1"
        yield project

    def run(*args, **kwargs):
        kwargs["authorize"]()
        dispatched.append(kwargs)
        return {"status": "offline_wiring_only"}

    monkeypatch.setattr("rag.agent_batch._credential", credential)
    monkeypatch.setattr(azure.ai.projects, "AIProjectClient", factory)
    monkeypatch.setattr("rag.agent_batch.connection_status", lambda **_: {
        "ready": True, "blockers": [], "public_config": agent,
    })
    monkeypatch.setattr("rag.qna_pilot.run_pilot", run)
    args = SimpleNamespace(
        command="run", plan_id=receipt["plan_id"], allow_foundry_content=True,
        evaluation_target=str(target_path), case_id=["case-fixture"], request_id="fixture-only",
    )
    return SimpleNamespace(
        args=args, studio=studio, service=service, project=project, client=client,
        constructed=constructed, dispatched=dispatched, agent=agent, target=target,
        execute=runpy.run_path(str(CLI))["execute_pilot_command"], legacy_endpoint=PROJECT_ENDPOINT,
    )


def test_operator_checks_target_judge_not_source_deployment(operator):
    result = operator.execute(operator.args, operator.studio)
    assert result["status"] == "offline_wiring_only"
    assert operator.constructed[0]["endpoint"] == operator.target["project_endpoint"]
    assert operator.dispatched[0]["evaluation_target"] == operator.target
    assert operator.dispatched[0]["evaluation_project"] is operator.project
    assert len(operator.project.calls) == 2
    records = list((operator.service.run_root / "qna_readiness").glob("*.json"))
    assert len(records) == 2
    assert all(json.loads(path.read_text())["evaluation_target"] == operator.target for path in records)


def test_operator_legacy_default_retains_source_judge_gate(operator):
    from rag.qna_readiness import PilotReadinessError

    operator.args.evaluation_target = None
    with pytest.raises(PilotReadinessError, match="judge_binding_changed"):
        operator.execute(operator.args, operator.studio)
    assert operator.constructed[0]["endpoint"] == operator.legacy_endpoint
    assert not operator.project.calls and not operator.dispatched


@pytest.mark.parametrize("failure,code", [
    ("judge", "evaluation_judge_binding_changed"),
    ("denied", "evaluation_judge_read_denied"),
    ("not_deployed", "evaluation_judge_read_unavailable"),
    ("unreachable", "evaluation_judge_read_unavailable"),
    ("source", "response_configuration_changed"),
])
def test_operator_records_distinct_readiness_failure_without_dispatch(operator, failure, code):
    from rag.qna_readiness import PilotReadinessError

    if failure == "judge":
        operator.project.model["modelVersion"] = "wrong"
    elif failure == "denied":
        operator.project.error = PermissionError("secret credential and raw content")
        operator.project.error.status_code = 403
    elif failure == "not_deployed":
        operator.project.error = RuntimeError("synthetic missing deployment")
        operator.project.error.status_code = 404
    elif failure == "unreachable":
        operator.project.error = TimeoutError("secret synthetic network diagnostic")
    else:
        operator.agent["agent_version"] = "5"
    with pytest.raises(PilotReadinessError, match=code):
        operator.execute(operator.args, operator.studio)
    assert not operator.dispatched
    assert len(operator.project.calls) <= 1
    records = list((operator.service.run_root / "qna_readiness").glob("*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert record["blocker_codes"] == [code] and not record["will_retry"]
    assert "secret" not in records[0].read_text()


@pytest.mark.parametrize("new_submission", [False, True])
def test_operator_resume_chooses_manifest_not_current_target_default(operator, monkeypatch, new_submission):
    from rag.foundry_evaluation_transport import submit_evaluation

    directory = operator.service.run_root / "run-fixture" / "qna_evaluation"
    rows = [{"case_id": "case-1", "response_id": "response-1",
             "query": "synthetic", "response": "synthetic", "context": "synthetic"}]
    if not new_submission:
        operator.client.base_url = operator.legacy_endpoint + "/openai/v1"
    submit_evaluation(directory, rows, client=operator.client, consent=True, authorize=lambda: None, **(
        {"evaluation_target": operator.target, "evaluation_project": operator.project}
        if new_submission else {}))
    original = (directory / "manifest.json").read_bytes()
    monkeypatch.setattr("rag.performance_evidence._read_batch", lambda *a: {"run_id": "run-fixture"})
    resumed = []
    monkeypatch.setattr("rag.qna_pilot.resume_pilot", lambda *a, **k: resumed.append(k))
    operator.args.command, operator.args.run_id = "resume", "run-fixture"
    operator.args.evaluation_target = None
    operator.project.calls.clear()
    operator.execute(operator.args, operator.studio)
    expected = operator.target if new_submission else None
    assert resumed[0]["evaluation_target"] == expected
    assert operator.constructed[0]["endpoint"] == (
        expected["project_endpoint"] if expected else operator.legacy_endpoint)
    assert not operator.project.calls
    assert (directory / "manifest.json").read_bytes() == original


def test_operator_resume_drift_fails_before_client_construction(operator, monkeypatch):
    from rag.foundry_evaluation_transport import TransportError, submit_evaluation

    operator.client.base_url = operator.legacy_endpoint + "/openai/v1"
    directory = operator.service.run_root / "run-fixture" / "qna_evaluation"
    submit_evaluation(directory, [{"case_id": "case-1", "response_id": "response-1",
                                   "query": "test", "response": "test", "context": "test"}],
                      client=operator.client, consent=True, authorize=lambda: None)
    monkeypatch.setattr("rag.performance_evidence._read_batch", lambda *a: {"run_id": "run-fixture"})
    operator.args.command, operator.args.run_id = "resume", "run-fixture"
    with pytest.raises(TransportError, match="differs"):
        operator.execute(operator.args, operator.studio)
    assert not operator.constructed and not operator.project.calls


def test_cli_documents_explicit_target_and_default_resume(capsys):
    with pytest.raises(SystemExit) as caught:
        runpy.run_path(str(CLI))["main"](["run", "--help"])
    assert caught.value.code == 0
    assert "--evaluation-target" in capsys.readouterr().out


def test_dataset_command_verifies_25_proposed_cases_without_cloud(capsys):
    result = runpy.run_path(str(CLI))["main"](["dataset"])
    assert len(result["cases"]) == 25
    assert len({row["segment_id"] for row in result["cases"]}) == 5
    assert result["human_review_status"] == "pending"
    assert result["live_execution"] is False
    assert "question" not in capsys.readouterr().out


def test_operator_input_size_is_bounded(tmp_path):
    path = tmp_path / "oversized.json"
    path.write_text(" " * (2 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="exceeds"):
        runpy.run_path(str(CLI))["read_json"](path)


def test_forecast_command_persists_new_case_bound_receipt(prospective, tmp_path, monkeypatch, capsys):
    import studio
    from rag.qna_dataset import load_dataset

    create, plans, learning, configuration, source = prospective
    new_result = create()[0]
    new_result["prediction"]["prediction_id"] = "cli-new-prediction"
    draft = plans.create_session("report-prospective", "Fresh CLI forecast", {})
    monkeypatch.setattr(studio, "PLAN_STORE_PATH", plans.root)
    monkeypatch.setattr(studio, "RESPONSE_LEARNING_STORE_PATH", learning.root)
    values = {
        "result": new_result, "configuration": configuration,
        "baseline": {"baseline": {"input_tokens_mean": 40, "output_tokens_mean": 20}, "source": source},
    }
    for key, value in values.items():
        (tmp_path / f"{key}.json").write_text(json.dumps(value))
    case = load_dataset()["cases"][0]
    arguments = ["forecast", "--plan-id", draft["plan_id"], "--case-id", case["case_id"],
                 "--split", case["partition"]]
    for key in values:
        arguments.extend(["--" + key, str(tmp_path / f"{key}.json")])
    _, receipt = runpy.run_path(str(CLI))["main"](arguments)
    assert receipt["response_forecast"]["experiment"]["case_ids"] == [case["case_id"]]
    assert plans.get_receipt(draft["plan_id"]) == receipt
