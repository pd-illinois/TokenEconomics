from copy import deepcopy
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
from uuid import uuid4

import pytest

from rag.foundry_evaluation_transport import (
    PROJECT_ENDPOINT, RUBRIC_EVALUATORS, TransportError, TransportLimits,
    download_evaluation, poll_evaluation, submit_evaluation,
)


@pytest.fixture
def tmp_path():
    path = Path.cwd() / ".pytest_cache" / ("foundry-transport-" + uuid4().hex)
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path)


class FakeClient:
    base_url = PROJECT_ENDPOINT + "/openai/v1/"

    def __init__(self):
        self.calls = []
        self.options = []
        self.fail_stage = None
        self.pages = []
        self.remote_status = "completed"
        self.evals = SimpleNamespace(create=self.create_eval, runs=SimpleNamespace(
            create=self.create_run, retrieve=self.retrieve,
            output_items=SimpleNamespace(list=self.list_items),
        ))

    def with_options(self, **kwargs):
        self.options.append(kwargs)
        return self

    def create_eval(self, **kwargs):
        self.calls.append(("eval", kwargs))
        if self.fail_stage == "eval":
            raise TimeoutError("secret credential and raw answer")
        return {"id": "eval-1"}

    def create_run(self, **kwargs):
        self.calls.append(("run", kwargs))
        if self.fail_stage == "run":
            raise TimeoutError("secret credential and raw answer")
        return {"id": "run-1"}

    def retrieve(self, **kwargs):
        self.calls.append(("retrieve", kwargs))
        return {"id": "run-1", "eval_id": "eval-1", "status": self.remote_status,
                "report_url": "https://ai.azure.com/project/evaluation/run"}

    def list_items(self, **kwargs):
        self.calls.append(("list", kwargs))
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


@pytest.fixture
def rows():
    return [{"case_id": "case-1", "response_id": "response-1", "query": "private question",
             "response": "private answer", "context": "private retrieved evidence"}]


def submit(directory, rows, client, **kwargs):
    return submit_evaluation(directory, rows, consent=True, authorize=lambda: None,
                             client=client, **kwargs)


def output(row, *, output_id="output-1"):
    return {
        "id": output_id, "eval_id": "eval-1", "run_id": "run-1", "status": "pass",
        "datasource_item": dict(row), "datasource_item_id": 0,
        "sample": {"item": {"case_id": "WRONG"}, "usage": {"prompt_tokens": 999}},
        "results": [
            {"name": name, "score": 4, "passed": True, "reason": "private judge reasoning"}
            for name in ("groundedness", "relevance")
        ],
    }


def page(items, **kwargs):
    return {"data": items, "has_more": False, **kwargs}


def test_exact_inline_shapes_and_replay_no_posts(tmp_path, rows):
    client = FakeClient()
    checks = []
    result = submit_evaluation(tmp_path, rows, consent=True,
                               authorize=lambda: checks.append("check"), client=client)
    assert result["status"] == "submitted"
    assert checks == ["check", "check"]
    eval_body, run_body = client.calls[0][1], client.calls[1][1]
    assert eval_body["data_source_config"]["type"] == "custom"
    assert eval_body["testing_criteria"][0]["evaluator_version"] == "17"
    assert eval_body["testing_criteria"][1]["evaluator_version"] == "12"
    assert eval_body["testing_criteria"][0]["data_mapping"]["context"] == "{{item.context}}"
    assert eval_body["testing_criteria"][0]["initialization_parameters"]["deployment_name"] == "rag-agent-runtime-gpt-4-1-mini"
    assert run_body["data_source"] == {
        "type": "jsonl", "source": {"type": "file_content", "content": [{"item": rows[0]}]},
    }
    assert submit(tmp_path, rows, client) == result
    assert len(client.calls) == 2
    assert all(x["max_retries"] == 0 and x["timeout"] <= 30 for x in client.options)


def test_one_assessment_can_submit_25_captured_rows(tmp_path, rows):
    client = FakeClient()
    many = [
        {**rows[0], "case_id": f"case-{index}", "response_id": f"response-{index}"}
        for index in range(25)
    ]
    result = submit(tmp_path, many, client)
    assert result["status"] == "submitted"
    assert len(client.calls[1][1]["data_source"]["source"]["content"]) == 25
    with pytest.raises(TransportError, match="row count"):
        submit(tmp_path / "too-many", [*many, {
            **rows[0], "case_id": "case-26", "response_id": "response-26",
        }], FakeClient())


def test_completed_run_exposes_only_valid_foundry_report_url(tmp_path, rows):
    client = FakeClient()
    submit(tmp_path, rows, client)
    result = poll_evaluation(tmp_path, client=client)
    assert result["report_url"] == "https://ai.azure.com/project/evaluation/run"
    client.pages = [page([output(rows[0])])]
    downloaded = download_evaluation(tmp_path, rows, client=client)
    assert downloaded["report_url"] == result["report_url"]


def test_legacy_manifest_shape_and_hash_are_unchanged(tmp_path, rows):
    from rag.foundry_evaluation_transport import _hash

    client = FakeClient()
    result = submit(tmp_path, rows, client)
    expected = {
        "schema_version": "foundry-captured-evaluation-transport.v1",
        "project_endpoint": PROJECT_ENDPOINT,
        "judge_deployment": "rag-agent-runtime-gpt-4-1-mini",
        "judge_model": ["gpt-4.1-mini", "2025-04-14"],
        "evaluators": {"groundedness": "17", "relevance": "12"},
        "thresholds": {"groundedness": 3, "relevance": 3},
        "data_fields": ["case_id", "response_id", "query", "response", "context"],
        "rubric_revisions": {}, "rubric_prompt_revision": None, "content_upload_consent": True,
        "rows": [{"case_id": "case-1", "response_id": "response-1", "content_hash": _hash(rows[0])}],
    }
    assert json.loads((tmp_path / "manifest.json").read_text()) == expected
    assert result["manifest_hash"] == _hash(expected)


def test_explicit_target_is_immutable_and_preflighted_before_each_post(tmp_path, rows):
    from rag.foundry_evaluation_transport import submission_evaluation_target
    from test_qna_evaluation_target import FakeProject, target_binding

    target = target_binding()
    project, client = FakeProject(), FakeClient()
    client.base_url = target["project_endpoint"] + "/openai/v1/"
    result = submit(tmp_path, rows, client, evaluation_target=target, evaluation_project=project)
    assert result["status"] == "submitted"
    assert len(project.calls) == 2
    original = (tmp_path / "manifest.json").read_bytes()
    manifest = json.loads(original)
    assert manifest["evaluation_target"] == target
    assert manifest["project_endpoint"] == target["project_endpoint"]
    assert submission_evaluation_target(tmp_path) == target
    assert submit(tmp_path, rows, client, evaluation_target=target, evaluation_project=project) == result
    assert len(client.calls) == len(project.calls) == 2
    client.pages = [page([output(rows[0])])]
    recovered = []
    assert download_evaluation(tmp_path, client=client, on_verified_row=recovered.append)["status"] == "completed"
    assert recovered == rows
    assert (tmp_path / "manifest.json").read_bytes() == original
    assert "private answer" not in "".join(path.read_text() for path in tmp_path.glob("*.json"))


@pytest.mark.parametrize("stage", ["eval", "run"])
def test_target_readiness_denial_keeps_unattempted_post_unclaimed(tmp_path, rows, stage):
    from rag.qna_evaluation_target import EvaluationTargetReadinessError
    from test_qna_evaluation_target import FakeProject, target_binding

    target, project, client = target_binding(), FakeProject(), FakeClient()
    client.base_url = target["project_endpoint"] + "/openai/v1"
    original = project.get

    def inventory(*args, **kwargs):
        model = original(*args, **kwargs)
        return {**dict(model), "modelVersion": "wrong"} if (
            stage == "eval" or len(project.calls) == 2) else model

    project.deployments.get = inventory
    with pytest.raises(EvaluationTargetReadinessError, match="evaluation_judge_binding_changed"):
        submit(tmp_path, rows, client, evaluation_target=target, evaluation_project=project)
    assert [name for name, _ in client.calls] == ([] if stage == "eval" else ["eval"])
    assert not (tmp_path / (stage + "-claim.json")).exists()
    assert not (tmp_path / "run-id.json").exists()


@pytest.mark.parametrize("new_submission", [False, True])
def test_resume_cannot_drift_to_another_destination(tmp_path, rows, new_submission):
    from rag.foundry_evaluation_transport import submission_evaluation_target
    from test_qna_evaluation_target import FakeProject, target_binding

    target, client = target_binding(), FakeClient()
    if new_submission:
        client.base_url = target["project_endpoint"] + "/openai/v1"
    submit(tmp_path, rows, client, **(
        {"evaluation_target": target, "evaluation_project": FakeProject()} if new_submission else {}))
    original = (tmp_path / "manifest.json").read_bytes()
    drift = {**target, "project_endpoint": PROJECT_ENDPOINT} if new_submission else target
    with pytest.raises(TransportError, match="differs"):
        submission_evaluation_target(tmp_path, drift)
    client.base_url = drift["project_endpoint"] + "/openai/v1"
    for operation in (poll_evaluation, download_evaluation):
        with pytest.raises(TransportError, match="endpoint"):
            operation(tmp_path, client=client)
    assert len(client.calls) == 2
    assert (tmp_path / "manifest.json").read_bytes() == original


def test_explicit_target_requires_matching_clients_before_any_post(tmp_path, rows):
    from test_qna_evaluation_target import FakeProject, target_binding

    client, target = FakeClient(), target_binding()
    with pytest.raises(TransportError, match="preflight"):
        submit(tmp_path, rows, client, evaluation_target=target)
    with pytest.raises(TransportError, match="endpoint"):
        submit(tmp_path, rows, client, evaluation_target=target, evaluation_project=FakeProject())
    assert not client.calls
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("stage,posts", [("eval", 1), ("run", 2)])
def test_explicit_target_ambiguous_post_cannot_be_retried(tmp_path, rows, stage, posts):
    from test_qna_evaluation_target import FakeProject, target_binding

    target, project, client = target_binding(), FakeProject(), FakeClient()
    client.base_url = target["project_endpoint"] + "/openai/v1"
    client.fail_stage = stage
    kwargs = {"evaluation_target": target, "evaluation_project": project}
    assert submit(tmp_path, rows, client, **kwargs)["status"] == "in_doubt"
    original = {p.name: p.read_bytes() for p in tmp_path.glob("*.json")}
    client.fail_stage = None
    assert submit(tmp_path, rows, client, **kwargs)["status"] == "in_doubt"
    assert len(client.calls) == len(project.calls) == posts
    assert {p.name: p.read_bytes() for p in tmp_path.glob("*.json")} == original


@pytest.mark.parametrize("change", ["endpoint", "model", "missing_binding", "null_binding"])
def test_resume_rejects_contradictory_new_manifest_before_any_get(tmp_path, rows, change):
    from test_qna_evaluation_target import FakeProject, target_binding

    target, client = target_binding(), FakeClient()
    client.base_url = target["project_endpoint"] + "/openai/v1"
    submit(tmp_path, rows, client, evaluation_target=target, evaluation_project=FakeProject())
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    if change == "endpoint":
        manifest["project_endpoint"] = PROJECT_ENDPOINT
    elif change == "model":
        manifest["judge_model"] = ["gpt-4o", "other"]
    elif change == "missing_binding":
        del manifest["evaluation_target"]
    else:
        manifest["evaluation_target"] = None
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(TransportError):
        poll_evaluation(tmp_path, client=client)
    assert len(client.calls) == 2


def test_resume_recovers_only_hash_verified_content_without_posts(tmp_path, rows):
    client = FakeClient()
    submit(tmp_path, rows, client)
    client.pages = [page([output(rows[0])])]
    recovered = []
    result = download_evaluation(tmp_path, client=client, on_verified_row=recovered.append)
    assert result["status"] == "completed" and recovered == rows
    assert len([call for call in client.calls if call[0] in ("eval", "run")]) == 2
    assert "private answer" not in "".join(path.read_text() for path in tmp_path.glob("*.json"))


def test_resume_rejects_changed_remote_source_content(tmp_path, rows):
    client = FakeClient()
    submit(tmp_path, rows, client)
    changed = {**rows[0], "response": "changed"}
    client.pages = [page([output(changed)])]
    recovered = []
    result = download_evaluation(tmp_path, client=client, on_verified_row=recovered.append)
    assert result["status"] == "incomplete" and not recovered


@pytest.mark.parametrize("stage,posts", [("eval", 1), ("run", 2)])
def test_ambiguous_post_claim_never_replays(tmp_path, rows, stage, posts):
    client = FakeClient()
    client.fail_stage = stage
    assert submit(tmp_path, rows, client)["status"] == "in_doubt"
    client.fail_stage = None
    assert submit(tmp_path, rows, client)["status"] == "in_doubt"
    assert len(client.calls) == posts
    assert "secret credential" not in "".join(p.read_text() for p in tmp_path.glob("*.json"))


def test_explicit_consent_and_live_authority_before_post(tmp_path, rows):
    client = FakeClient()
    with pytest.raises(TransportError):
        submit_evaluation(tmp_path, rows, consent=False, authorize=lambda: None, client=client)

    def blocked():
        raise PermissionError("expired live authority")

    with pytest.raises(PermissionError):
        submit_evaluation(tmp_path, rows, consent=True, authorize=blocked, client=client)
    assert not client.calls
    assert not (tmp_path / "eval-claim.json").exists()


def test_metrics_only_default_cannot_upload_without_operator_opt_in(tmp_path, rows):
    client = FakeClient()
    authorization_calls = []
    with pytest.raises(TransportError, match="content-upload consent"):
        submit_evaluation(
            tmp_path, rows, client=client,
            authorize=lambda: authorization_calls.append("checked"),
        )
    assert not client.calls
    assert not authorization_calls
    assert not list(tmp_path.iterdir())


def test_authority_rechecked_before_second_post_and_can_resume(tmp_path, rows):
    client = FakeClient()
    checks = []

    def authorize():
        checks.append(1)
        if len(checks) == 2:
            raise PermissionError("expired")

    with pytest.raises(PermissionError):
        submit_evaluation(tmp_path, rows, consent=True, authorize=authorize, client=client)
    assert [c[0] for c in client.calls] == ["eval"]
    assert not (tmp_path / "run-claim.json").exists()
    assert submit(tmp_path, rows, client)["status"] == "submitted"
    assert [c[0] for c in client.calls] == ["eval", "run"]


@pytest.mark.parametrize("change", ["row", "judge", "threshold"])
def test_directory_is_immutable_submission_binding(tmp_path, rows, change):
    client = FakeClient()
    submit(tmp_path, rows, client)
    kwargs = {}
    if change == "row":
        rows[0]["response"] += " changed"
    if change == "judge":
        kwargs["judge_deployment"] = "gpt-5-6-luna"
    if change == "threshold":
        kwargs["thresholds"] = {"groundedness": 4, "relevance": 3}
    with pytest.raises(TransportError):
        submit(tmp_path, rows, client, **kwargs)
    assert len(client.calls) == 2


def test_partial_job_record_is_in_doubt(tmp_path, rows):
    client = FakeClient()
    submit(tmp_path, rows, client)
    (tmp_path / "run-id.json").write_text("{")
    assert submit(tmp_path, rows, client)["status"] == "in_doubt"
    assert len(client.calls) == 2


@pytest.mark.parametrize("invalid", ["count", "size", "identity", "duplicate", "extra"])
def test_input_bounds_no_calls(tmp_path, rows, invalid):
    client = FakeClient()
    if invalid == "count":
        rows = rows * 11
    elif invalid == "size":
        rows[0]["context"] = "x" * 131073
    elif invalid == "identity":
        rows[0]["case_id"] = "../private"
    elif invalid == "duplicate":
        rows *= 2
    else:
        rows[0]["api_key"] = "not allowed"
    with pytest.raises(TransportError):
        submit(tmp_path, rows, client)
    assert not client.calls


def test_foreign_endpoint_and_judge_rejected(tmp_path, rows):
    client = FakeClient()
    client.base_url = "https://example.com"
    with pytest.raises(TransportError):
        submit(tmp_path, rows, client)
    client.base_url = PROJECT_ENDPOINT + "/openai/v1"
    with pytest.raises(TransportError):
        submit(tmp_path, rows, client, judge_deployment="gpt-4o")
    assert not client.calls


def test_poll_bound_and_failed_status(tmp_path, rows):
    client = FakeClient()
    submit(tmp_path, rows, client)
    client.remote_status = "in_progress"
    limits = TransportLimits(max_poll_requests=2, poll_interval_seconds=0)
    assert poll_evaluation(tmp_path, client=client, limits=limits)["status"] == "pending"
    assert len([c for c in client.calls if c[0] == "retrieve"]) == 2
    client.remote_status = "failed"
    assert poll_evaluation(tmp_path, client=client)["status"] == "incomplete"
    client.remote_status = "completed"
    result = poll_evaluation(tmp_path, client=client)
    assert result["status"] == "completed" and result["results_downloaded"] is False


def test_missing_ids_poll_and_download_are_in_doubt(tmp_path, rows):
    client = FakeClient()
    client.fail_stage = "run"
    submit(tmp_path, rows, client)
    assert poll_evaluation(tmp_path, client=client)["status"] == "in_doubt"
    assert download_evaluation(tmp_path, rows, client=client)["status"] == "in_doubt"
    assert len(client.calls) == 2


def test_output_hashes_exact_links_without_persisting_private_content(tmp_path, rows):
    client = FakeClient()
    submit(tmp_path, rows, client)
    client.pages = [page([output(rows[0])])]
    result = download_evaluation(tmp_path, rows, client=client)
    assert result["status"] == "completed"
    assert result["items"][0]["case_id"] == "case-1"
    assert len(result["items"][0]["raw_result_hash"]) == 64
    assert result["judge_usage"] == {"input_tokens": None, "output_tokens": None, "cost_usd": None}
    assert result["acceptance"] == "not_assessed"
    disk = "".join(p.read_text() for p in tmp_path.glob("*.json"))
    for text in ("private question", "private answer", "private retrieved evidence", "private judge reasoning", "WRONG"):
        assert text not in disk


@pytest.mark.parametrize("fault", [
    "missing", "failed", "foreign_id", "missing_identity", "content_changed",
    "duplicate_judge", "wrong_version", "nan", "bool_score", "pass_mismatch",
    "duplicate_output", "wrong_job", "sample_only", "ambiguous_nested",
])
def test_incomplete_evidence_never_passes(tmp_path, rows, fault):
    client = FakeClient()
    submit(tmp_path, rows, client)
    raw = output(rows[0])
    outputs = [raw]
    if fault == "missing":
        raw["results"].pop()
    elif fault == "failed":
        raw["results"][0]["error"] = {"message": "private service error"}
    elif fault == "foreign_id":
        raw["datasource_item"]["case_id"] = "foreign"
    elif fault == "missing_identity":
        raw["datasource_item"].pop("response_id")
    elif fault == "content_changed":
        raw["datasource_item"]["response"] += " changed"
    elif fault == "duplicate_judge":
        raw["results"][1] = deepcopy(raw["results"][0])
    elif fault == "wrong_version":
        raw["results"][0]["evaluator_version"] = "999"
    elif fault == "nan":
        raw["results"][0]["score"] = float("nan")
    elif fault == "bool_score":
        raw["results"][0]["score"] = True
    elif fault == "pass_mismatch":
        raw["results"][0]["score"] = 1
    elif fault == "duplicate_output":
        outputs.append(deepcopy(raw))
    elif fault == "wrong_job":
        raw["run_id"] = "foreign"
    elif fault == "sample_only":
        raw["sample"]["item"] = raw.pop("datasource_item")
    else:
        raw["datasource_item"]["item"] = dict(rows[0])
    client.pages = [page(outputs)]
    result = download_evaluation(tmp_path, rows, client=client)
    assert result["status"] == "incomplete"
    assert result["acceptance"] == "not_assessed"


def test_failed_grade_is_completed_not_missing_judge(tmp_path, rows):
    client = FakeClient()
    submit(tmp_path, rows, client)
    raw = output(rows[0])
    raw["status"] = "fail"
    raw["results"][0].update(score=1, passed=False)
    client.pages = [page([raw])]
    result = download_evaluation(tmp_path, rows, client=client)
    assert result["status"] == "completed"
    assert result["items"][0]["judges"][0]["passed"] is False


def test_explicit_pagination_and_out_of_order_identity(tmp_path, rows):
    rows.append({**rows[0], "case_id": "case-2", "response_id": "response-2"})
    client = FakeClient()
    submit(tmp_path, rows, client)
    second = output(rows[1], output_id="output-2")
    second["datasource_item"] = {"item": second["datasource_item"]}
    client.pages = [
        page([second], has_more=True, last_id="output-2"),
        page([output(rows[0])]),
    ]
    result = download_evaluation(tmp_path, rows, client=client)
    assert result["status"] == "completed"
    assert result["items"][0]["case_id"] == "case-2"
    assert client.calls[-1][1]["after"] == "output-2"


def test_missing_outputs_and_page_limit(tmp_path, rows):
    client = FakeClient()
    submit(tmp_path, rows, client)
    client.pages = [page([])]
    result = download_evaluation(tmp_path, rows, client=client)
    assert result["missing_outputs"] == [{"case_id": "case-1", "response_id": "response-1"}]
    client.pages = [page([output(rows[0])], has_more=True, last_id="output-1")]
    result = download_evaluation(tmp_path, rows, client=client, limits=TransportLimits(max_pages=1))
    assert result["status"] == "incomplete" and result["pagination_complete"] is False


def test_download_oversized_response_and_changed_source(tmp_path, rows):
    client = FakeClient()
    submit(tmp_path, rows, client)
    client.pages = [page([output(rows[0])], unexpected="x" * 10000)]
    result = download_evaluation(tmp_path, rows, client=client,
                                  limits=TransportLimits(max_response_bytes=1000))
    assert result["reason"] == "response_size_limit"
    rows[0]["query"] = "changed"
    with pytest.raises(TransportError):
        download_evaluation(tmp_path, rows, client=client)


def test_identical_download_is_create_only_idempotent(tmp_path, rows):
    client = FakeClient()
    submit(tmp_path, rows, client)
    client.pages = [page([output(rows[0])]), page([output(rows[0])])]
    first = download_evaluation(tmp_path, rows, client=client)
    second = download_evaluation(tmp_path, rows, client=client)
    assert first == second
    assert len(list(tmp_path.glob("results-*.json"))) == 1


def test_denial_return_value_never_authorizes(tmp_path, rows):
    client = FakeClient()
    result = submit_evaluation(tmp_path, rows, consent=True, authorize=lambda: False, client=client)
    assert result["status"] == "in_doubt"
    assert not client.calls and not (tmp_path / "eval-claim.json").exists()


def test_remote_created_but_id_persistence_fails_never_replayed(tmp_path, rows, monkeypatch):
    import rag.foundry_evaluation_transport as transport
    client = FakeClient()
    original = transport._record_job

    def fail(*args, **kwargs):
        raise OSError("disk failure with private information")

    monkeypatch.setattr(transport, "_record_job", fail)
    assert submit(tmp_path, rows, client)["status"] == "in_doubt"
    monkeypatch.setattr(transport, "_record_job", original)
    assert submit(tmp_path, rows, client)["status"] == "in_doubt"
    assert len(client.calls) == 1


def test_corrupt_id_records_block_reads(tmp_path, rows):
    client = FakeClient()
    submit(tmp_path, rows, client)
    (tmp_path / "eval-id.json").write_text("{")
    assert poll_evaluation(tmp_path, client=client)["status"] == "in_doubt"
    assert download_evaluation(tmp_path, rows, client=client)["status"] == "in_doubt"
    assert len(client.calls) == 2


def test_transport_timeout_can_only_be_tightened(tmp_path, rows):
    client = FakeClient()
    with pytest.raises(TransportError):
        submit(tmp_path, rows, client, limits=TransportLimits(request_timeout_seconds=31))
    assert not client.calls
    assert submit(tmp_path, rows, client, limits=TransportLimits(max_elapsed_seconds=1))["status"] == "submitted"
    assert all(option["timeout"] <= 1 for option in client.options)


def test_same_response_cannot_be_reused_under_two_cases(tmp_path, rows):
    client = FakeClient()
    rows.append({**rows[0], "case_id": "case-2"})
    with pytest.raises(TransportError):
        submit(tmp_path, rows, client)
    assert not client.calls


def test_download_requires_verified_terminal_completion(tmp_path, rows):
    client = FakeClient()
    submit(tmp_path, rows, client)
    client.remote_status = "running"
    result = download_evaluation(tmp_path, rows, client=client)
    assert result["status"] == "incomplete"
    assert not any(kind == "list" for kind, _ in client.calls)


def test_contradictory_aggregate_grade_is_incomplete(tmp_path, rows):
    client = FakeClient()
    submit(tmp_path, rows, client)
    raw = output(rows[0])
    raw["results"][0].update(score=1, passed=False)
    client.pages = [page([raw])]
    result = download_evaluation(tmp_path, rows, client=client)
    assert result["status"] == "incomplete"
    assert result["items"][0]["reason"] == "aggregate_grade_mismatch"


@pytest.mark.parametrize("http_status", [200, 503])
def test_installed_sdk_wire_shapes_and_zero_http_retries(tmp_path, rows, http_status):
    httpx = pytest.importorskip("httpx")
    openai = pytest.importorskip("openai")
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append((request.method, request.url.path, body))
        if http_status != 200:
            return httpx.Response(http_status, json={"error": {"message": "unavailable"}})
        identifier = "run-1" if request.url.path.endswith("/runs") else "eval-1"
        return httpx.Response(200, json={"id": identifier})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        with openai.OpenAI(base_url=PROJECT_ENDPOINT + "/openai/v1",
                           api_key="unit-test-placeholder", http_client=http_client) as client:
            result = submit(tmp_path, rows, client)
    if http_status == 503:
        assert result["status"] == "in_doubt" and len(requests) == 1
    else:
        assert result["status"] == "submitted" and len(requests) == 2
        assert requests[0][0] == requests[1][0] == "POST"
        criteria = requests[0][2]["testing_criteria"]
        assert criteria[0]["evaluator_name"] == "builtin.groundedness"
        assert criteria[0]["evaluator_version"] == "17"
        assert requests[1][2]["data_source"]["source"]["content"] == [{"item": rows[0]}]


def rubric_inputs(rows):
    for row in rows:
        row.update(expected_answer="private expected answer",
                   rubric="private draft rubric: evaluate each named dimension independently")
    return rows, {name: name + ".draft-v1" for name in RUBRIC_EVALUATORS}


def test_optional_six_dimension_rubrics_are_pinned_not_reviewed(tmp_path, rows):
    rows, revisions = rubric_inputs(rows)
    client = FakeClient()
    submit(tmp_path, rows, client, rubric_revisions=revisions)
    criteria = client.calls[0][1]["testing_criteria"]
    assert len(criteria) == 6
    for criterion in criteria[2:]:
        assert criterion["type"] == "score_model"
        assert criterion["model"] == "rag-agent-runtime-gpt-4-1-mini"
        assert criterion["range"] == [1, 5]
        assert criterion["pass_threshold"] == 3
        assert "{{item.expected_answer}}" in criterion["input"][1]["content"]
        assert "{{item.rubric}}" in criterion["input"][1]["content"]
    raw = output(rows[0])
    raw["results"].extend(
        {"type": "score_model", "name": name, "score": 4, "passed": True}
        for name in sorted(revisions)
    )
    client.pages = [page([raw])]
    result = download_evaluation(tmp_path, rows, client=client)
    assert result["status"] == "completed"
    assert {x["evaluator_id"] for x in result["items"][0]["judges"]} == set(revisions) | {"groundedness", "relevance"}
    assert result["acceptance"] == "not_assessed" and result["operational_admission"] is False
    saved = "".join(p.read_text() for p in tmp_path.glob("*.json"))
    assert "private expected answer" not in saved and "private draft rubric" not in saved
    changed = {**revisions, "citation": "citation.draft-v2"}
    with pytest.raises(TransportError):
        submit(tmp_path, rows, client, rubric_revisions=changed)


def test_missing_optional_grader_is_incomplete(tmp_path, rows):
    rows, revisions = rubric_inputs(rows)
    client = FakeClient()
    submit(tmp_path, rows, client, rubric_revisions=revisions)
    client.pages = [page([output(rows[0])])]
    result = download_evaluation(tmp_path, rows, client=client)
    assert result["status"] == "incomplete"
    assert result["items"][0]["reason"] == "missing_or_extra_judges"


def test_unapproved_rubric_name_and_missing_inputs_are_rejected(tmp_path, rows):
    client = FakeClient()
    with pytest.raises(TransportError, match="allowlist"):
        submit(tmp_path, rows, client, rubric_revisions={"arbitrary": "v1"})
    with pytest.raises(TransportError, match="expected_answer"):
        submit(tmp_path, rows, client, rubric_revisions={"correctness": "v1"})
    assert not client.calls


def test_native_score_model_serializes_through_installed_sdk(tmp_path, rows):
    httpx = pytest.importorskip("httpx")
    openai = pytest.importorskip("openai")
    rows, revisions = rubric_inputs(rows)
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "run-1" if request.url.path.endswith("/runs") else "eval-1"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        with openai.OpenAI(base_url=PROJECT_ENDPOINT + "/openai/v1",
                           api_key="unit-test-placeholder", http_client=http_client) as client:
            result = submit(tmp_path, rows, client, rubric_revisions=revisions)
    assert result["status"] == "submitted"
    assert len(requests[0]["testing_criteria"]) == 6
    for criterion in requests[0]["testing_criteria"][2:]:
        assert criterion["type"] == "score_model"
        assert criterion["range"] == [1, 5]
        assert criterion["pass_threshold"] == 3
    assert requests[1]["data_source"]["source"]["content"][0]["item"]["expected_answer"] == "private expected answer"
