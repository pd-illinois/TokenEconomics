import json

import pytest

from test_performance_ui import DOM, HTML, _function, _review
from test_studio_workflow import _javascript


def _evidence():
    return {
        "reconciliations": [{
            "schema_version": "reconciliation-evidence.v1",
            "reconciliation_id": "reconciliation1", "content_hash": "reconciliation-hash",
            "report_id": "r1", "run_id": "run1",
            "prediction_reference": {"id": "receipt", "content_hash": "hash"},
        }],
        "learningProofs": [{
            "schema_version": "learning-evidence.v1", "learning_proof_id": "learning1",
            "reconciliation_reference": {"id": "reconciliation1", "content_hash": "reconciliation-hash"},
        }],
        "portabilityProofs": [{"workload": {"workload_id": "workiq"}}],
    }


def _context_source():
    return (DOM + "state.plan.receipt_id='receipt';state.performance=" + json.dumps(_review()) + ";"
            + _function("function reconciliationContext(", "async function fetchReconciliationEvidence("))


def test_selected_evidence_joins_receipt_report_run_and_learning_hash():
    source = _context_source() + "const evidence=" + json.dumps(_evidence()) + ";"
    result = _javascript(source, "selectReconciliationEvidence(evidence,reconciliationContext())")
    assert len(result["reconciliations"]) == len(result["learningProofs"]) == 1
    assert result["portabilityProofs"] == []


@pytest.mark.parametrize("field,value", [
    ("report_id", "foreign"), ("run_id", "other-run"), ("schema_version", "unsupported"),
    ("prediction_reference", {"id": "other-receipt", "content_hash": "hash"}),
    ("prediction_reference", {"id": "receipt", "content_hash": "other-hash"}),
    ("prediction_reference", None),
])
def test_foreign_or_incomplete_records_never_show_as_selected_batch_evidence(field, value):
    evidence = _evidence()
    evidence["reconciliations"][0][field] = value
    source = _context_source() + "const evidence=" + json.dumps(evidence) + ";"
    result = _javascript(source, "selectReconciliationEvidence(evidence,reconciliationContext())")
    assert result == {"reconciliations": [], "learningProofs": [], "portabilityProofs": []}


@pytest.mark.parametrize("reference", [
    None, {"id": "reconciliation1"}, {"id": "other", "content_hash": "reconciliation-hash"},
    {"id": "reconciliation1", "content_hash": "different"},
])
def test_learning_requires_the_exact_reconciliation_hash(reference):
    evidence = _evidence()
    evidence["learningProofs"][0]["reconciliation_reference"] = reference
    source = _context_source() + "const evidence=" + json.dumps(evidence) + ";"
    result = _javascript(source, "selectReconciliationEvidence(evidence,reconciliationContext()).learningProofs")
    assert result == []


@pytest.mark.parametrize("change", [
    "state.performance=null", "state.performanceLoading=true", "state.report.report_id='other'",
    "state.plan.receipt_hash='new'", "state.plan.receipt_id='other'", "state.plan.plan_id='other'",
    "state.performance.selected_run_id=null",
])
def test_loading_and_context_switches_prevent_reuse_of_previous_batch(change):
    assert _javascript(_context_source() + change + ";", "reconciliationContext()") is None


def _loader_source():
    return (_context_source() + "state.performance.summary.execution_status='blocked';"
            + _function("async function loadReconciliations(", "async function loadProjectEvidence("))


def test_scoped_empty_state_does_not_claim_billing_or_learning_has_happened():
    source = _loader_source() + """
const fetchReconciliationEvidence=async()=>({reconciliations:[],learningProofs:[],portabilityProofs:[]});
const renderReconciliationEvidence=()=> '';
"""
    result = _javascript(source, "loadReconciliations().then(()=>elements.get('reconcile-content').innerHTML)")
    assert "Billing not yet reconciled" in result
    assert "Forecast learning not yet available" in result
    assert "Response-model allocation is not a complete-task bill" in result


def test_failed_evidence_read_is_not_presented_as_an_empty_history():
    source = _loader_source() + """
const fetchReconciliationEvidence=async()=>{throw new Error('storage offline');};
"""
    result = _javascript(source, "loadReconciliations().then(()=>elements.get('reconcile-content').innerHTML)")
    assert "storage offline" in result and "not a confirmed absence" in result
    assert "Billing not yet reconciled" not in result


def test_late_response_cannot_replace_another_batchs_reconciliation():
    source = _loader_source() + """
let finish;
const fetchReconciliationEvidence=()=>new Promise(resolve=>{finish=resolve;});
const renderReconciliationEvidence=()=>{throw new Error('stale render');};
const pending=loadReconciliations();
state.performance.selected_run_id='run2';
elements.get('reconcile-content').innerHTML='New batch';
finish({reconciliations:[],learningProofs:[],portabilityProofs:[]});
"""
    assert _javascript(source, "pending.then(()=>elements.get('reconcile-content').innerHTML)") == "New batch"


def test_open_reconciliation_reloads_only_after_batch_review_finishes_loading():
    source = _context_source() + "const review=" + json.dumps(_review()) + ";"
    source += """
const contexts=[];
document.getElementById('reconcile').open=true;
const fetch=async()=>({ok:true,json:async()=>({review,saved_reviews:[]})});
const acceptPerformanceResponse=payload=>{state.performance=payload.review;};
const loadReconciliations=async()=>{contexts.push(reconciliationContext());};
"""
    source += _function("async function loadPerformanceReview(", "async function savePerformanceReview(")
    result = _javascript(source, "loadPerformanceReview('run1').then(()=>contexts)")
    assert result == [{"reportId": "r1", "receiptId": "receipt", "receiptHash": "hash", "runId": "run1"}]


def test_only_project_history_requests_optional_portability_evidence():
    source = _function("async function fetchReconciliationEvidence(", "async function loadReconciliations(")
    source = """
const calls=[];
const fetch=async url=>{calls.push(url);return {ok:true,json:async()=>({
reconciliations:[],learning_proofs:[],portability_proofs:[]})};};
""" + source
    result = _javascript(source, "fetchReconciliationEvidence().then(()=>calls)")
    assert result == ["/api/reconcile", "/api/learning-proofs"]
    result = _javascript(source, "fetchReconciliationEvidence(true).then(()=>calls)")
    assert result == ["/api/reconcile", "/api/learning-proofs", "/api/portability-proofs"]


@pytest.mark.parametrize("before,after,expected", [
    (1.215786, 1.215786, "121.6%"), (0, 0, "0%"), (None, None, "Unavailable"),
    (-1, -1, "Unavailable"),
])
def test_wape_retains_values_above_100_and_never_renders_completion_bars(before, after, expected):
    evidence = {"reconciliations": [], "portabilityProofs": [], "learningProofs": [{
        "before_error": before, "after_error": after, "result": "unchanged", "sample_count": 120,
        "predictor_write_reference": {"writer_outcome": {"calibration_applied_to_after_forecast": False}},
    }]}
    source = DOM + """
const rate=value=>value==null?'Unavailable':`${+(value*100).toFixed(1)}%`;
const evidenceDetails=(title,content)=>`<details><summary>${title}</summary>${content}</details>`;
const progressBar=()=>{throw new Error('WAPE must not use a capped progress bar');};
"""
    source += _function("function renderReconciliationEvidence(", "async function renderRun()")
    result = _javascript(source, "renderReconciliationEvidence(" + json.dumps(evidence) + ")")
    assert expected in result and "popovertarget=\"wape-help\"" in result
    assert "bar-list" not in result and "Calibration not applied" in result


def test_wape_help_is_keyboard_and_touch_operable_and_explains_scope():
    assert 'id="wape-help" class="wape-help" popover="auto"' in HTML
    assert 'aria-label="What is WAPE?" popovertarget="wape-help"' in HTML
    assert 'popovertargetaction="hide"' in HTML
    for explanation in ["Weighted Absolute Percentage Error", "220 tokens", "120% WAPE",
                        "not dollar cost or answer quality", "undefined when total actual usage is zero"]:
        assert explanation in HTML
    assert 'id="project-evidence-history"' not in HTML.split('id="studio-shell"', 1)[0]
    dashboard = HTML.split('<section class="view" id="govern">', 1)[1].split('<section class="view" id="runs">', 1)[0]
    assert dashboard.index('id="project-evidence-history"') > dashboard.index('id="reconcile-content"')
    assert HTML.count('id="project-evidence-history"') == 1
