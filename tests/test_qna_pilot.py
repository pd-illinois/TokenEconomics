from rag.agent_batch import _write_once
from rag.foundry_evaluation_transport import RUBRIC_EVALUATORS, submit_evaluation
from rag.qna_capture_adapter import prepare_evaluation
from rag.qna_dataset import load_dataset
from rag.qna_pilot import _finish, resume_pilot, run_pilot
from rag.response_capture import CapturedResponses
from test_agent_batch_measurement import measured, proof, response, run
from test_foundry_evaluation_transport import FakeClient, output, page
import pytest


def test_default_pilot_never_dispatches_or_uploads_without_consent(measured):
    service, receipt, _, _ = measured
    with pytest.raises(ValueError, match="approval"):
        run_pilot(service, receipt["plan_id"], load_dataset(), [], request_id="unused",
                  loaded=None, actor="operator", root=None, register=None,
                  refresh_service=None, client=None, authorize=None)


@pytest.mark.parametrize("transient_before_upload", [False, True])
@pytest.mark.parametrize("separate_target", [False, True])
def test_operator_runner_wires_capture_submission_and_saved_review(
        measured, monkeypatch, transient_before_upload, separate_target):
    from test_qna_evaluation_target import FakeProject, target_binding

    service, receipt, _, _ = measured
    dataset = load_dataset()
    case = dataset["cases"][0]
    # Prospective binding has separate contract/dispatch tests; this fixture's
    # legacy receipt isolates the runner-to-transport orchestration.
    monkeypatch.setattr("rag.qna_pilot.validate_qna_dispatch", lambda *a, **k: [case])
    client = FakeClient()
    target = target_binding() if separate_target else None
    project = FakeProject() if separate_target else None
    if target:
        client.base_url = target["project_endpoint"] + "/openai/v1"
    original_create = client.create_run

    def create_evaluation_run(**kwargs):
        row = kwargs["data_source"]["source"]["content"][0]["item"]
        raw = output(row)
        raw["results"].extend({"name": name, "score": 4, "passed": True} for name in RUBRIC_EVALUATORS)
        client.pages = [page([raw])]
        return original_create(**kwargs)

    client.evals.runs.create = create_evaluation_run
    payload = {**response(), "id": "response-1", "output": [
        {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "Synthetic answer"}]},
        {"type": "mcp_call", "name": "knowledge_base_retrieve", "output": "Synthetic context"},
    ]}

    def runner(*args, **kwargs):
        return run(measured, questions=args[2]["questions"], create=lambda **_: payload,
                   response_observer=kwargs["response_observer"])

    from rag.qna_readiness import authorize_readiness

    ready = {"ready": True, "blockers": []}
    unavailable = {"ready": False, "blockers": [
        {"code": "agent_read_access_unavailable"}, {"code": "retrieval_mode_unverified"},
    ]}
    statuses = iter([ready, unavailable, ready, ready] if transient_before_upload else [ready] * 3)
    attempts, delays = [], []

    def authorize():
        authorize_readiness(lambda: next(statuses), record=attempts.append, sleep=delays.append)

    result = run_pilot(
        service, receipt["plan_id"], dataset, [case["case_id"]], request_id="fixture-only",
        loaded=None, actor="operator", root=None, register=None, refresh_service=lambda: service,
        client=client, authorize=authorize, consent=True, runner=runner,
        evaluation_target=target, evaluation_project=project,
    )
    assert result["quality_recorded"] and result["created"]
    assert [name for name, _ in client.calls][:2] == ["eval", "run"]
    assert len([name for name, _ in client.calls if name in {"eval", "run"}]) == 2
    assert delays == ([1] if transient_before_upload else [])
    if separate_target:
        assert len(project.calls) == 3


@pytest.mark.parametrize("denial", [
    "judge_drift", "wrong_project", "read_denied", "wrong_client", "not_deployed", "unreachable",
])
def test_target_must_be_ready_before_source_dispatch(measured, monkeypatch, denial):
    from rag.qna_evaluation_target import EvaluationTargetReadinessError
    from rag.foundry_evaluation_transport import PROJECT_ENDPOINT, TransportError
    from test_qna_evaluation_target import FakeProject, target_binding

    service, receipt, _, _ = measured
    dataset, target, project, client = load_dataset(), target_binding(), FakeProject(), FakeClient()
    case = dataset["cases"][0]
    monkeypatch.setattr("rag.qna_pilot.validate_qna_dispatch", lambda *a, **k: [case])
    client.base_url = target["project_endpoint"] + "/openai/v1"
    if denial == "judge_drift":
        project.model["modelVersion"] = "wrong"
    elif denial == "wrong_project":
        project._config.endpoint = PROJECT_ENDPOINT
    elif denial == "wrong_client":
        client.base_url = PROJECT_ENDPOINT + "/openai/v1"
    elif denial == "not_deployed":
        project.error = RuntimeError("synthetic missing deployment")
        project.error.status_code = 404
    elif denial == "unreachable":
        project.error = TimeoutError("synthetic inaccessible endpoint")
    else:
        project.error = PermissionError("secret")
        project.error.status_code = 403
    dispatched = []
    with pytest.raises((EvaluationTargetReadinessError, TransportError)):
        run_pilot(
            service, receipt["plan_id"], dataset, [case["case_id"]], request_id="fixture-only",
            loaded=None, actor="operator", root=None, register=None, refresh_service=None,
            client=client, authorize=lambda: None, consent=True,
            runner=lambda *a, **k: dispatched.append(True),
            evaluation_target=target, evaluation_project=project,
        )
    assert not dispatched and not client.calls
    assert len(project.calls) <= 1


def test_captured_response_to_six_judges_to_immutable_review_and_resume(measured):
    service, receipt, _, _ = measured
    dataset = load_dataset()
    case = dataset["cases"][0]
    capture = CapturedResponses([case], allow_content_evaluation=True)
    payload = {**response(), "id": "response-1", "output": [
        {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "Synthetic test answer"}]},
        {"type": "mcp_call", "name": "knowledge_base_retrieve", "output": "Synthetic context"},
    ]}
    batch = run(measured, questions=[case["question"]], create=lambda **_: payload, response_observer=capture)
    prepared = prepare_evaluation(dataset, capture, batch)
    rows = prepared["items"]
    rows[0].update(expected_answer=case["expected_answer"], rubric="Proposed test rubric")
    directory = service.run_root / batch["run_id"] / "qna_evaluation"
    _write_once(directory / "capture.json", prepared["manifest"])
    client = FakeClient()
    submit_evaluation(directory, rows, client=client, consent=True, authorize=lambda: None,
                      rubric_revisions={name: "draft.v1" for name in RUBRIC_EVALUATORS},
                      thresholds=dict.fromkeys(["groundedness", "relevance", *RUBRIC_EVALUATORS], 4))
    raw = output(rows[0])
    raw["results"].extend({"name": name, "score": 4, "passed": True} for name in RUBRIC_EVALUATORS)
    client.pages = [page([raw])]
    result = _finish(service, receipt["plan_id"], batch, dataset, directory,
                     prepared["manifest"], rows, client, "operator")
    assert result["quality_recorded"] and result["created"]
    capture.clear()
    client.pages = [page([raw])]
    replay = resume_pilot(service, receipt["plan_id"], batch["run_id"], dataset,
                          client=client, actor="operator", consent=True)
    assert replay["record_id"] == result["record_id"] and not replay["created"]
    assert len([call for call in client.calls if call[0] in ("eval", "run")]) == 2
    record = service.get(receipt, result["record_id"], "qna-evaluation")
    assert record["review"]["summary"]["acceptance_counts"]["accepted"] == 0
    assert record["review"]["costs"]["judge_total_usd"] is None
