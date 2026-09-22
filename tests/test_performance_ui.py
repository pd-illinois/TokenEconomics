import json
import re
from pathlib import Path

from test_studio_workflow import _javascript


HTML = (Path(__file__).resolve().parents[1] / "studio.html").read_text(encoding="utf-8")


def _function(start, end):
    return HTML[HTML.index(start):HTML.index(end, HTML.index(start))]


def _review():
    return {
        "schema_version": "studio-performance-review.v1", "classification": "advisory",
        "plan_id": "p1", "report_id": "r1", "receipt_hash": "hash", "receipt_id": "receipt",
        "selected_run_id": "run1", "generated_at": "2026-09-09T18:00:00Z",
        "run_options": [{"run_id": "run1", "started_at": "2026-09-09T15:00:00Z", "execution_status": "completed", "questions_count": 10}],
        "scope": {"status": "partial", "message": "Selected receipt only"},
        "summary": {"execution_status": "completed", "questions_completed": 10, "questions_planned": 10,
                    "input_tokens": 0, "output_tokens": None, "latency_mean_ms": None,
                    "observed_model_allocation_usd": 0.0058328, "quality_status": "not_evaluated",
                    "billing_status": "unavailable", "cloud_status": "published"},
        "comparison": [{"metric": "output", "label": "Output", "expected": 1200, "observed": 133,
                        "unit": "tokens", "status": "not_comparable", "reason": "Different scopes", "variance_pct": None}],
        "findings": [{"code": "output", "severity": "warning", "title": "<img src=x>",
                      "detail": "Review assumptions", "action": "forecast", "action_label": "Revise forecast", "evidence_refs": []}],
        "decision": {"title": "Review assumptions", "explanation": "Not approval", "operational_promotion": False, "automatic_changes": False},
        "policy": {"historical": {"version": "old"}, "current": None, "status": "unavailable"},
        "provenance": {"receipt_hash": "hash"},
    }


DOM = """
const elements=new Map();
const document={getElementById(id){
 if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',disabled:false});
 return elements.get(id);
},querySelectorAll:()=>[]};
const state={plan:{plan_id:'p1',receipt_hash:'hash'},report:{report_id:'r1'}};
const escapeHtml=value=>String(value??'').replaceAll('<','&lt;').replaceAll('>','&gt;');
const number=value=>String(value);
const decimal=value=>String(value);
const preciseMoney=value=>'$'+value;
const humanizeStatus=value=>String(value??'unavailable').replaceAll('_',' ');
const formatDate=value=>value;
const signalCard=(label,value,detail,tone='neutral-tone')=>`<div class="${tone}">${label}: ${value}; ${detail}</div>`;
"""


def test_dashboard_only_exposes_supported_quality_reviews():
    assert 'data-view="govern">Performance &amp; Decisions' in HTML
    assert 'class="tab" data-view="reconcile"' not in HTML
    assert 'id="performance-advanced-govern"' not in HTML
    assert 'id="lifecycle-quality-review" hidden' in HTML
    assert '<details class="panel" id="reconcile">' in HTML
    assert 'id="lifecycle-reconcile-selection"' in HTML
    assert 'id="save-performance-review"' in HTML


def test_policy_navigation_cannot_hide_advanced_governance():
    source = """
let advancedVisible=true;
const advanced={classList:{remove(){advancedVisible=false;}}};
const policy={classList:{remove(){},add(){}}};
const document={
 querySelector:()=>({click(){},classList:{add(){}}}),
 querySelectorAll:selector=>selector.includes('#policy')?[policy]:[policy,advanced],
 getElementById:()=>policy
};
"""
    source += _function("function showGovernPane(", 'document.querySelectorAll(".govern-tab").forEach')
    source += "showGovernPane('changes');"
    assert _javascript(source, "advancedVisible") is True


def test_renderer_uses_real_fields_preserves_missing_and_escapes_findings():
    source = DOM + _function("function performanceNumber(", "function acceptPerformanceResponse(")
    source += "const review=" + json.dumps(_review()) + "; renderPerformanceReview(review, []);"
    result = _javascript(source, "({html:elements.get('performance-content').innerHTML+elements.get('performance-details').innerHTML,history:elements.get('performance-history').innerHTML})")
    assert "$0.0058328" in result["html"]
    assert "0 / Unavailable" in result["html"]
    assert "% difference" not in result["html"]
    assert "&lt;img src=x&gt;" in result["html"] and "<img" not in result["html"]
    assert "No review has been saved" in result["history"]
    assert "fetch(" not in source


def test_small_dollar_comparisons_keep_cost_precision():
    source = DOM + _function("function performanceNumber(", "function renderPerformanceReview(")
    result = _javascript(source, "[performanceValue(0.003424,'USD/model call'),performanceValue(null,'USD'),performanceValue(133,'tokens'),performanceValue(825.39536,'USD/month')]")
    assert result == ["$0.003424", "Unavailable", "133", "$825.40"]


def test_receipt_and_promotion_guards_reject_untrusted_responses():
    source = DOM + "const renderPerformanceReview=()=>{};\n"
    source += _function("function acceptPerformanceResponse(", "async function loadPerformanceReview(")
    source += "const review=" + json.dumps(_review()) + ";"
    result = _javascript(source, """['receipt_hash','report_id','classification','promotion'].map(field=>{
      const wrong=JSON.parse(JSON.stringify(review));
      if(field==='promotion')wrong.decision.operational_promotion=true;else wrong[field]='wrong';
      try{acceptPerformanceResponse({review:wrong,saved_reviews:[]},'p1','hash','run1');return false;}catch{return true;}
    })""")
    assert all(result)


def test_late_review_cannot_replace_another_forecast():
    source = DOM + "const acceptPerformanceResponse=()=>{throw new Error('stale review rendered');};\n"
    source += "const fetch=async()=>{state.plan={plan_id:'p2',receipt_hash:'new'};return {ok:true,json:async()=>({})};};\n"
    source += _function("async function loadPerformanceReview(", "async function savePerformanceReview(")
    result = _javascript(source, "loadPerformanceReview().then(()=>({plan:state.plan.plan_id,html:elements.get('performance-content').textContent}))")
    assert result == {"plan": "p2", "html": ""}


def test_snapshot_failure_is_visible_and_releases_busy_state():
    source = DOM + "state.performance=" + json.dumps(_review()) + ";"
    source += "const fetch=async()=>({ok:false,json:async()=>({error:'Separate authorization required'})});\n"
    source += _function("async function savePerformanceReview(", "function performanceFindingAction(")
    result = _javascript(source, "savePerformanceReview().then(()=>({busy:state.performanceSaving,message:elements.get('performance-status').textContent}))")
    assert result["busy"] is False
    assert "Separate authorization required" in result["message"]
    assert "saved at" not in result["message"]


def test_saving_an_older_selection_cannot_replace_a_newer_review():
    source = DOM + "state.performance=" + json.dumps(_review()) + ";"
    source += """
const acceptPerformanceResponse=()=>{throw new Error('stale save replaced selection');};
const fetch=async()=>{
 state.performanceRequest++;
 state.performance={...state.performance,selected_run_id:'run2'};
 return {ok:true,json:async()=>({})};
};
"""
    source += _function("async function savePerformanceReview(", "function performanceFindingAction(")
    result = _javascript(source, "savePerformanceReview().then(()=>({selected:state.performance.selected_run_id,busy:state.performanceSaving}))")
    assert result == {"selected": "run2", "busy": False}


def test_loading_a_new_batch_disables_saving_the_previous_batch():
    source = DOM + "state.performance=" + json.dumps(_review()) + ";"
    source += """
let savedDuringLoad=false;
const fetch=async()=>{savedDuringLoad=Boolean(state.performance);return {ok:false,json:async()=>({error:'Unavailable'})};};
"""
    source += _function("async function loadPerformanceReview(", "async function savePerformanceReview(")
    result = _javascript(source, "loadPerformanceReview('run2').then(()=>({savedDuringLoad,review:state.performance,loading:state.performanceLoading}))")
    assert result == {"savedDuringLoad": False, "review": None, "loading": False}


def test_finding_prepares_revision_without_writing_or_executing():
    source = DOM + "state.performance=" + json.dumps(_review()) + ";state.planStage='ready';"
    source += """
let destination=null,revisions=0,note=null,focus=null;
document.querySelector=selector=>({click(){destination=selector;},appendChild(value){note=value.textContent;}});
document.createElement=()=>({});
document.getElementById('performance-action-context').remove=()=>{};
const beginPlanRevision=()=>{revisions++;};
const focusWorkflow=value=>{focus=value;};
const fetch=()=>{throw new Error('finding must not dispatch or save');};
"""
    source += _function("function performanceFindingAction(", 'document.getElementById("refresh-performance").onclick')
    source += "performanceFindingAction('forecast','output');"
    result = _javascript(source, "({destination,revisions,note,focus})")
    assert result["destination"] == '.tab[data-view="plan"]'
    assert result["revisions"] == 1 and result["focus"] == "plan-journey"
    assert "not an automatic change" in result["note"]


def test_finding_from_another_receipt_does_not_navigate():
    source = DOM + "state.performance=" + json.dumps(_review()) + ";state.plan.receipt_hash='different';"
    source += "document.querySelector=()=>{throw new Error('stale finding navigated');};"
    source += _function("function performanceFindingAction(", 'document.getElementById("refresh-performance").onclick')
    source += "performanceFindingAction('forecast','output');"
    result = _javascript(source, "elements.get('performance-status').textContent")
    assert "no longer matches" in result


def test_obsolete_readiness_and_resolution_ui_are_removed_not_just_hidden():
    for obsolete in ["renderGovernReadiness", "handoffToGovern", "renderGovernResolution",
                     "govern-resolution", "forecast-govern-readiness", "workflow-footer",
                     "recheck-govern-button", "download-govern-checklist",
                     "performance-provenance", "Evidence references and scope"]:
        assert obsolete not in HTML
    assert 'id="admit-lifecycle-evidence"' not in HTML
    assert 'id="compare-lifecycle-evidence"' not in HTML


def test_token_bars_use_one_zero_based_scale_and_keep_labels():
    source = DOM + _function("function performanceNumber(", "function renderPerformanceReview(")
    rows = [
        {"metric": "input_tokens", "expected": 3760, "observed": 940, "unit": "tokens", "status": "partially_comparable"},
        {"metric": "output_tokens", "expected": 1200, "observed": 0, "unit": "tokens", "status": "partially_comparable"},
    ]
    result = _javascript(source, "performanceComparisonBars(" + json.dumps(rows) + ")")
    widths = [float(value) for value in re.findall(r'style="width:([\d.]+)%"', result)]
    assert widths == [100, 25, 1200 / 3760 * 100, 0]
    assert "<strong>0</strong>" in result and "Zero-based scale: 0 to 3760 tokens" in result
    assert "NaN" not in result and "Infinity" not in result


def test_missing_measurements_do_not_draw_zero_bars_or_invent_axes():
    source = DOM + _function("function performanceNumber(", "function renderPerformanceReview(")
    row = {"metric": "input_tokens", "expected": None, "observed": None, "unit": "tokens", "status": "unavailable"}
    result = _javascript(source, "performanceComparisonBars([" + json.dumps(row) + "])")
    assert "performance-bar " not in result
    assert result.count("<strong>Unavailable</strong>") == 2
    assert "No numeric scale is available" in result


def test_incompatible_pairs_have_no_visual_or_percent_comparison():
    review = _review()
    review["comparison"][0].update(metric="output_tokens", variance_pct=-88.9)
    source = DOM + _function("function performanceNumber(", "function acceptPerformanceResponse(")
    source += "renderPerformanceReview(" + json.dumps(review) + ", []);"
    result = _javascript(source, "elements.get('performance-content').innerHTML")
    assert "Scopes are not comparable" in result
    assert 'class="performance-bar ' not in result
    assert "% difference" not in result


def test_completion_ring_is_execution_only_and_has_no_unknown_denominator():
    source = DOM + _function("function performanceNumber(", "function renderPerformanceReview(")
    result = _javascript(source, """[
      performanceCompletionChart({questions_planned:10,questions_completed:7}),
      performanceCompletionChart({questions_planned:1,questions_completed:0}),
      performanceCompletionChart({questions_planned:null,questions_completed:0}),
      performanceCompletionChart({questions_planned:0,questions_completed:0}),
      performanceCompletionChart({questions_planned:1,questions_completed:2})
    ]""")
    assert "--completion:70%" in result[0] and "not quality acceptance" in result[0]
    assert "3 not completed / not dispatched" in result[0]
    assert "--completion:0%" in result[1]
    assert all("performance-completion-ring" not in value for value in result[2:])


def test_dashboard_colors_and_compact_findings_retain_explanations():
    review = _review()
    review["summary"]["execution_status"] = "blocked"
    review["findings"].append({"code":"critical","severity":"critical","title":"Invalid source",
                               "detail":"Cannot verify source","action":"evidence","action_label":"Inspect","evidence_refs":[]})
    source = DOM + _function("function performanceNumber(", "function acceptPerformanceResponse(")
    source += "renderPerformanceReview(" + json.dumps(review) + ", []);"
    result = _javascript(source, "elements.get('performance-content').innerHTML")
    assert "danger-tone" in result and 'performance-state success' in result
    assert "Gray: unavailable" in result
    details = _javascript(source, "elements.get('performance-details').innerHTML")
    assert details.index('performance-finding critical') < details.index('performance-finding warning')
    assert "<details><summary>Invalid source</summary><p>Cannot verify source</p></details>" in details
    assert '<details class="panel"><summary><strong>Detailed expected-versus-observed comparison</strong></summary>' in result


def test_comparison_is_immediately_after_completion_grid_before_findings():
    source = DOM + _function("function performanceNumber(", "function acceptPerformanceResponse(")
    source += "renderPerformanceReview(" + json.dumps(_review()) + ", []);"
    result = _javascript(source, "elements.get('performance-content').innerHTML")
    assert result.index("Batch completion") < result.index("Detailed expected-versus-observed comparison")
    assert "Findings and next actions" not in result
    assert result.rstrip().endswith("</details>")
    assert re.search(r'</section>\s*</div>\s*<details class="panel"><summary><strong>Detailed expected-versus-observed comparison', result)
    dashboard = HTML.split('<section class="view" id="govern">', 1)[1].split('<section class="view" id="runs">', 1)[0]
    assert re.search(r'<div id="performance-content"></div>\s*<details class="panel" id="reconcile">', dashboard)
    assert dashboard.index('id="reconcile"') < dashboard.index('id="performance-details"') < dashboard.index('id="performance-history"')
    assert '<summary><strong>Billing reconciliation and forecast learning</strong></summary>' in dashboard
    assert "<th>Observed vs forecast</th>" in result


def test_direction_covers_above_below_same_and_zero_without_division():
    source = DOM + _function("function performanceNumber(", "function renderPerformanceReview(")
    result = _javascript(source, """[
      [100,120], [100,80], [100,100], [0,0], [0,1], [1,0], [1,1.000001]
    ].map(([expected,observed])=>performanceDirection({expected,observed,status:'comparable'}))""")
    for output, direction in zip(result, ["above", "below", "same", "same", "above", "below", "above"]):
        assert f'performance-direction {direction}' in output
        assert "NaN" not in output and "Infinity" not in output
        assert "Diagnostic only" not in output
    assert "&uarr;" in result[0] and "&darr;" in result[1]
    assert "Observed equals forecast" in result[2]


def test_direction_withholds_unknown_invalid_and_incompatible_values():
    source = DOM + _function("function performanceNumber(", "function renderPerformanceReview(")
    result = _javascript(source, """[
      {expected:100,observed:null,status:'partially_comparable'},
      {expected:null,observed:0,status:'comparable'},
      {expected:100,observed:NaN,status:'comparable'},
      {expected:100,observed:Infinity,status:'comparable'},
      {expected:100,observed:-1,status:'comparable'},
      {expected:100,observed:'80',status:'comparable'},
      {expected:100,observed:80,status:'not_comparable'}
    ].map(performanceDirection)""")
    assert all('class="performance-direction"' in item for item in result)
    assert all("Unavailable" in item for item in result[:-1])
    assert "Not comparable" in result[-1]


def test_partial_direction_is_explicitly_diagnostic_without_variance():
    review = _review()
    review["comparison"][0].update(status="partially_comparable", variance_pct=-88.9)
    source = DOM + _function("function performanceNumber(", "function acceptPerformanceResponse(")
    source += "renderPerformanceReview(" + json.dumps(review) + ", []);"
    result = _javascript(source, "elements.get('performance-content').innerHTML")
    assert 'performance-direction below' in result and '<small>Diagnostic only</small>' in result
    assert "% difference" not in result


def test_unsupported_evidence_actions_keep_limitations_without_a_dead_end():
    review = _review()
    review["findings"] = [{"code":"quality","severity":"warning","title":"Quality is not evaluated",
                          "detail":"An evaluator must produce acceptance outcomes.",
                          "action":"evidence","action_label":"Inspect evidence","evidence_refs":[{"id":"internal-ref"}]}]
    source = DOM + _function("function performanceNumber(", "function acceptPerformanceResponse(")
    source += "renderPerformanceReview(" + json.dumps(review) + ", []);"
    result = _javascript(source, "elements.get('performance-details').innerHTML")
    assert "Quality is not evaluated" in result and "An evaluator must produce" in result
    assert "Inspect evidence" not in result and 'data-performance-action="evidence"' not in result
    assert "internal-ref" not in result and "<pre" not in result


def test_saved_review_history_is_readable_without_raw_provenance_dump():
    review = _review()
    history = [{"created_at":"2026-09-09T18:00:00Z","actor":"local-operator","id":"technical-id",
                "content_hash":"technical-hash","review":review}]
    source = DOM + _function("function performanceNumber(", "function acceptPerformanceResponse(")
    source += "renderPerformanceReview(" + json.dumps(review) + "," + json.dumps(history) + ");"
    result = _javascript(source, "elements.get('performance-history').innerHTML")
    assert "local-operator" in result and "Review assumptions" in result
    assert "technical-hash" not in result and "technical-id" not in result and "<pre" not in result
