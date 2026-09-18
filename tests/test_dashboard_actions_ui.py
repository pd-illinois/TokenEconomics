import copy
import json

from test_performance_ui import DOM, _function, _review
from test_studio_workflow import _javascript


def _workspace():
    return {
        "plan_id": "p1", "report_id": "r1", "receipt_hash": "hash",
        "records": [{
            "schema_version": "studio-evaluation.v1", "kind": "evaluation",
            "id": "evaluation-1", "plan_id": "p1", "report_id": "r1", "receipt_hash": "hash",
            "created_at": "2026-09-09T18:00:00Z", "status": "inconclusive",
        }],
    }


def _evaluation_source():
    return DOM + _function("function supportedLifecycleEvaluations(", "function renderLifecycleGovernance(")


def test_quality_controls_require_a_supported_evaluation_for_exact_workspace():
    workspace = _workspace()
    source = _evaluation_source()
    result = _javascript(source, "supportedLifecycleEvaluations(" + json.dumps(workspace) + ").map(item=>item.id)")
    assert result == ["evaluation-1"]


def test_metrics_only_and_foreign_or_unversioned_records_are_not_action_inputs():
    workspace = _workspace()
    valid = workspace["records"][0]
    for field, value in [
        ("schema_version", "rag-agent-batch.v2"), ("schema_version", "studio-evaluation.v2"),
        ("kind", "attachment"), ("plan_id", "other"), ("report_id", "other"),
        ("receipt_hash", "other"), ("id", ""),
    ]:
        record = copy.deepcopy(valid)
        record[field] = value
        workspace["records"].append(record)
    result = _javascript(_evaluation_source(), "supportedLifecycleEvaluations(" + json.dumps(workspace) + ").map(item=>item.id)")
    assert result == ["evaluation-1"]


def test_stale_or_empty_workspaces_cannot_keep_quality_actions_available():
    workspaces = [None, {}, {**_workspace(), "records": []}]
    for field in ("plan_id", "report_id", "receipt_hash"):
        stale = _workspace()
        stale[field] = "different"
        workspaces.append(stale)
    result = _javascript(_evaluation_source(), json.dumps(workspaces) + ".map(workspace=>supportedLifecycleEvaluations(workspace).length)")
    assert result == [0] * len(workspaces)


def test_forecast_switch_rejects_prior_quality_records():
    source = _evaluation_source() + "state.plan={plan_id:'p2',receipt_hash:'new'};"
    result = _javascript(source, "supportedLifecycleEvaluations(" + json.dumps(_workspace()) + ").length")
    assert result == 0


def test_existing_workspace_contract_can_omit_report_when_each_record_is_bound():
    workspace = _workspace()
    del workspace["report_id"]
    result = _javascript(_evaluation_source(), "supportedLifecycleEvaluations(" + json.dumps(workspace) + ").length")
    assert result == 1
    del workspace["records"][0]["report_id"]
    result = _javascript(_evaluation_source(), "supportedLifecycleEvaluations(" + json.dumps(workspace) + ").length")
    assert result == 0


def _controls_source():
    source = DOM + _function("function lifecycleSelection(", "async function loadSourcePreview(")
    source += _function("function lifecycleActionButtons(", "async function loadLifecycle(")
    source += "state.lifecycle=" + json.dumps(_workspace()) + ";"
    return source


def test_supported_quality_controls_select_real_records_and_dispatch_existing_action():
    source = _controls_source() + """
const calls=[];
const lifecycleAction=(...args)=>calls.push(args);
renderLifecycleGovernance(state.lifecycle);
document.getElementById('reassess-lifecycle-evidence').onclick();
"""
    result = _javascript(source, """({
      qualityHidden:elements.get('lifecycle-quality-review').hidden,
      reconcileHidden:elements.get('lifecycle-reconcile-actions').hidden,
      disabled:elements.get('reassess-lifecycle-evidence').disabled,
      labels:elements.get('lifecycle-evaluation-selection').innerHTML,calls
    })""")
    assert result["qualityHidden"] is False and result["reconcileHidden"] is False
    assert result["disabled"] is False
    assert "Review 1" in result["labels"] and "2026-09-09" in result["labels"]
    assert result["calls"] == [["reassessments", {"evaluation_id": "evaluation-1"}, "lifecycle-govern-status"]]


def test_invalid_selections_and_loading_disable_quality_actions():
    source = _controls_source() + """
renderLifecycleGovernance(state.lifecycle);
elements.get('lifecycle-evaluation-selection').value='foreign-evaluation';
elements.get('lifecycle-reconcile-selection').value='foreign-evaluation';
lifecycleActionButtons(false);
const invalid=['reassess-lifecycle-evidence','reconcile-lifecycle-evidence'].map(id=>elements.get(id).disabled);
elements.get('lifecycle-evaluation-selection').value='evaluation-1';
elements.get('lifecycle-reconcile-selection').value='evaluation-1';
state.lifecycleLoading=true;
lifecycleActionButtons(false);
"""
    result = _javascript(source, "({invalid,loading:['reassess-lifecycle-evidence','reconcile-lifecycle-evidence'].map(id=>elements.get(id).disabled)})")
    assert result == {"invalid": [True, True], "loading": [True, True]}


def test_failed_lifecycle_refresh_clears_prior_quality_controls():
    source = _controls_source() + """
state.plan.receipt_id='receipt';
state.lifecycleCapabilities={read_only_import:true};
renderLifecycleGovernance(state.lifecycle);
const updateRagPlayground=()=>{};
const renderRagCorrections=()=>{};
const fetch=async()=>{throw new Error('evidence unavailable');};
"""
    source += _function("async function loadLifecycle(", "async function lifecycleAction(")
    result = _javascript(source, """loadLifecycle().then(()=>({
      lifecycle:state.lifecycle,loading:state.lifecycleLoading,
      qualityHidden:elements.get('lifecycle-quality-review').hidden,
      reconcileHidden:elements.get('lifecycle-reconcile-actions').hidden,
      disabled:elements.get('reconcile-lifecycle-evidence').disabled,
      status:elements.get('lifecycle-reconcile-status').textContent
    }))""")
    assert result["lifecycle"] is None and result["loading"] is False
    assert result["qualityHidden"] and result["reconcileHidden"] and result["disabled"]
    assert "evidence unavailable" in result["status"]


def test_unavailable_finding_action_explains_without_navigating_or_mutating():
    review = _review()
    review["findings"][0].update(action="evidence")
    source = DOM + "state.performance=" + json.dumps(review) + ";"
    source += """
document.querySelector=()=>{throw new Error('unsupported navigation');};
const fetch=()=>{throw new Error('unsupported mutation');};
"""
    source += _function("function performanceFindingAction(", 'document.getElementById("refresh-performance").onclick')
    source += "performanceFindingAction('evidence','output');"
    message = _javascript(source, "elements.get('performance-status').textContent")
    assert message
