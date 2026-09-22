from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


HTML = (Path(__file__).resolve().parents[1] / "studio.html").read_text(encoding="utf-8")


def _javascript(source: str, expression: str) -> object:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for Studio JavaScript behavior checks")
    result = subprocess.run(
        [node, "-"],
        input=source + "\nPromise.resolve(" + expression + ").then(value => console.log(JSON.stringify(value))).catch(error => { console.error(error); process.exitCode = 1; });",
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


def _function(start: str, end: str) -> str:
    return HTML[HTML.index(start):HTML.index(end, HTML.index(start))]


def test_all_inline_scripts_parse():
    scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", HTML, re.S)
    assert scripts
    result = _javascript(
        "const vm = require('node:vm'); const scripts = " + json.dumps(scripts) + ";",
        "scripts.map(script => { new vm.Script(script); return true; })",
    )
    assert all(result)


@pytest.mark.parametrize("hosted", [False, True])
def test_delivery_choices_expose_accessible_future_release_states(hosted):
    from costgov.consumption_models import consumption_catalog
    catalog = consumption_catalog()
    catalog["studio_availability"] = {
        "selectable_routes": ["foundry"] if hosted else [
            item["route_id"] for item in catalog["experiences"]
        ],
        "planned_message": "Planned for a future release.",
    }
    source = "const escapeHtml = text => String(text);"
    source += _function("function renderExperienceChoices(", "async function loadConsumptionCatalog(")
    result = _javascript(source, "renderExperienceChoices(" + json.dumps(catalog) + ")")
    assert result.count('name="plan-route"') == 9
    assert result.count(" checked") == 1
    assert result.count(" disabled") == (8 if hosted else 0)
    assert result.count('aria-disabled="true"') == (8 if hosted else 0)
    assert result.count("aria-describedby=") == (8 if hosted else 0)
    assert f'value="{"foundry" if hosted else "included"}" checked' in result
    assert result.count('class="experience-release-note"') == (8 if hosted else 0)


def test_navigation_has_no_legacy_checklist_or_floating_governance_actions():
    assert "governResolutionItems" not in HTML
    assert "govern-resolution-checklist.v1" not in HTML
    assert 'data-workflow-view="resolution"' not in HTML
    assert 'data-workflow-view="evidence"' not in HTML
    assert "workflow-footer" not in HTML
    assert "Recheck historical route evidence" not in HTML


def test_forecast_proceeds_to_policy_without_creating_an_admission():
    source = "let opened=null;const document={querySelector:selector=>({click(){opened=selector;}})};"
    source += _function("async function continueToGovern(", "function bindWorkflowNavigation(")
    result = _javascript(source, "continueToGovern().then(()=>opened)")
    assert result == '.tab[data-view="policy"]'
    assert "fetch(" not in source


@pytest.mark.parametrize("stage,locked", [("describe", False), ("profile", False), ("infrastructure", True), ("ready", True)])
def test_stages_preserve_evidence_and_lock_confirmed_inputs(stage, locked):
    source = """
const elements = new Map();
const document = {getElementById(id) {
  if (!elements.has(id)) elements.set(id, {setAttribute() {}, textContent: '', disabled: false});
  return elements.get(id);
}};
const modelRoutes = new Set(['foundry']);
const selectedRoute = () => 'foundry';
const routeIsReadOnly = () => false;
const state = {plan: {plan_id:'p1', receipt_id:'immutable', infrastructure_draft:{status:'estimated'}},
  report: {artifacts: {plans:[{id:'p1'}]}}, savedPlan: null, planBusy: false};
"""
    source += _function("function setPlanStage", "function beginPlanRevision")
    source += "\nconst before = JSON.stringify(state.plan); setPlanStage(" + json.dumps(stage) + ");"
    result = _javascript(
        source,
        "({locked: elements.get('plan-inputs').disabled, next:elements.get('plan-next-button').textContent,"
        "unchanged:before===JSON.stringify(state.plan), hidden:elements.get('plan-action-row').hidden,"
        "steps:elements.get('plan-steps').innerHTML})",
    )
    assert result["locked"] is locked
    assert result["unchanged"]
    assert result["hidden"] is locked
    assert result["steps"].count('aria-current="step"') == 1
    for index, label in enumerate(
        ["Describe workload", "Review profile", "Review infrastructure", "Forecast ready"], 1
    ):
        assert f'<span class="journey-step-number">{index}</span><span>{label}</span>' in result["steps"]
    current_label = dict(describe="Describe workload", profile="Review profile",
                         infrastructure="Review infrastructure", ready="Forecast ready")[stage]
    assert re.search(r'<li aria-current="step">[^<]*<span[^>]*>\d+</span><span>' + current_label + r'</span></li>', result["steps"])
    if stage == "ready":
        assert result["next"] == "Next: Policy"


def test_existing_forecast_details_and_confirmation_contract_are_preserved():
    for title in [
        "Token estimate and calculation evidence",
        "Model cost forecast evidence",
        "Confirmed workload analysis",
        "Per-agent model allocation",
        "Non-model tool-charge evidence",
        "Workload, assumptions, and provenance",
        "Azure infrastructure estimate",
    ]:
        assert title in HTML
    assert "infrastructure_forecast_hash = state.plan.infrastructure_draft.content_hash" in HTML
    assert "infrastructure_confirmed: false" in HTML
    assert 'if (state.planStage === "ready") return beginPlanRevision();' in HTML
    assert "item.plan_id === state.plan?.plan_id" in HTML
    assert 'const analysis = plan.analysis || intake.analysis;' in HTML
    assert "applyEvidenceMode" not in HTML
    assert 'data-workflow-view="resolution"' not in HTML


def test_policy_is_only_policy_and_evaluation_criteria_stay_accessible():
    policy = HTML.split('<section class="view" id="policy">', 1)[1].split("</section>", 1)[0]
    evaluate = HTML.split('<div id="observe" hidden>', 1)[1].split('<section class="view" id="reconcile">', 1)[0]
    assert "Start a durable workspace" not in HTML
    assert 'aria-label="Evidence detail"' not in HTML
    assert "lifecycle-acceptance-definition" not in policy
    assert "lifecycle-budget" not in policy
    assert "effective-policy-content" in policy
    assert "policy-proposal-form" in policy
    assert 'id="save-lifecycle-requirements"' in evaluate
    assert 'id="lifecycle-budget"' in evaluate


def test_each_workflow_page_begins_with_actions_and_navigation_is_not_floating():
    for page in ["plan", "policy", "runs", "govern"]:
        body = re.split(r'<section class="view(?: active)?" id="' + page + r'">', HTML)[1]
        assert re.match(r'\s*<div class="panel page-actions', body)
        assert re.search(r'<h2 class="page-actions-title"[^>]*>Actions</h2>', body)
    policy = HTML.split('id="policy-actions"', 1)[1].split('id="govern-policy"', 1)[0]
    for control in ["refresh-policy-button", "create-policy-button", "edit-policy-button"]:
        assert f'id="{control}"' in policy
    assert 'data-govern-pane="changes"' in policy
    assert 'data-workflow-view="plan"' in policy and 'data-workflow-view="runs"' in policy
    assert "workflow-footer" not in policy
    execute = HTML.split('id="execute-actions"', 1)[1].split('id="rag-playground"', 1)[0]
    assert 'id="execute-topology"' in execute and 'id="execute-topology-note"' in execute
    assert 'id="evaluate-next-govern"' in execute
    assert 'data-workflow-view="policy"' in execute
    assert HTML.count('id="evaluate-next-govern"') == 1


def test_busy_stage_disables_navigation_and_confirmation():
    source = """
const elements = new Map();
const document = {getElementById(id) {
  if (!elements.has(id)) elements.set(id, {setAttribute() {}});
  return elements.get(id);
}};
const modelRoutes = new Set(['foundry']);
const selectedRoute = () => 'foundry';
const state = {plan: {infrastructure_draft:{status:'estimated'}},
  report:{artifacts:{plans:[{id:'p1'}]}}, planBusy:true};
"""
    source += _function("function routeIsReadOnly(", "function meterStackFor(")
    source += _function("function setPlanStage", "function beginPlanRevision")
    source += "\nsetPlanStage('infrastructure', 'Saving...');"
    result = _javascript(
        source,
        "['plan-next-button','plan-back-button','plan-button','confirm-infrastructure-button',"
        "'open-selected-plan-button','switch-report-button'].map(id=>elements.get(id).disabled)",
    )
    assert all(result)


def test_navigation_uses_approved_lifecycle_with_legacy_ids():
    navigation = re.findall(r'<button class="tab(?: active)?" data-view="([^"]+)">([^<]+)</button>', HTML)
    assert navigation == [
        ("home", "Home"), ("plan", "Forecast"), ("policy", "Policy"),
        ("runs", "Execute &amp; Review"), ("govern", "Performance &amp; Decisions"),
    ]
    policy = HTML[HTML.index('<section class="view" id="policy">'):HTML.index('<section class="view" id="govern">')]
    assert 'id="policy-proposal-form"' in policy
    assert 'id="effective-policy-content"' in policy
    assert "data-workflow-view=\"runs\"" in policy


def test_home_projects_portfolio_kpis_attention_and_decision_matrix():
    home = HTML[HTML.index('<section class="report-gate"'):HTML.index('<div class="shell"')]
    assert 'id="portfolio-kpis"' in home
    assert 'id="portfolio-attention"' in home
    assert 'id="portfolio-matrix"' in home
    assert "Portfolio Decision Matrix" in home
    assert "highest modeled annual forecast" in home.lower()
    assert "not actual portfolio spend or a savings claim" in home
    assert "function renderPortfolio()" in HTML
    assert "Missing values are not plotted as zero" in HTML
    assert "evidence_classification" not in home


@pytest.mark.parametrize("failure", ["network", "authorization", "invalid_json"])
def test_lifecycle_failures_clear_busy_state_without_implying_success(failure):
    source = """
const status = {textContent:''}; const buttons = [];
const document = {getElementById: () => status};
const state = {plan:{plan_id:'p1'}, lifecycle:{plan_id:'p1'},
  lifecycleCapabilities:{read_only_import:true,csrf_token:'token'}, lifecycleBusy:false};
function lifecycleActionButtons(value) { buttons.push(value); }
async function loadLifecycle() { throw new Error('must not refresh failed action'); }
"""
    source += {
        "network": "const fetch = async () => { throw new Error('network unavailable'); };",
        "authorization": "const fetch = async () => ({ok:false,status:403,json:async()=>({code:'lifecycle_unauthorized',error:'Separate authorization required'})});",
        "invalid_json": "const fetch = async () => ({ok:false,status:502,json:async()=>{throw new Error('invalid response');}});",
    }[failure]
    source += _function("async function lifecycleAction(", 'document.getElementById("save-lifecycle-requirements").onclick')
    result = _javascript(source, "(async()=>{await lifecycleAction('evaluations',{mode:'read_only'},'status');return {busy:state.lifecycleBusy,message:status.textContent,buttons};})()")
    assert result["busy"] is False
    assert result["buttons"] == [True, False]
    assert result["message"].startswith("Not completed:")
    assert "No successful evaluation or admission is implied" in result["message"]


def test_stale_action_response_never_relabels_another_forecast():
    source = """
const status = {textContent:''}; const document = {getElementById: () => status};
const state = {plan:{plan_id:'p1'}, lifecycle:{plan_id:'p1'},
  lifecycleCapabilities:{read_only_import:true,csrf_token:'token'}, lifecycleBusy:false};
function lifecycleActionButtons() {}
async function loadLifecycle() {}
const fetch = async () => {state.plan = {plan_id:'p2'}; return {ok:true,json:async()=>({kind:'evaluation',status:'eligible'})};};
"""
    source += _function("async function lifecycleAction(", 'document.getElementById("save-lifecycle-requirements").onclick')
    result = _javascript(source, "(async()=>{await lifecycleAction('evaluations',{},'status');return {busy:state.lifecycleBusy,message:status.textContent,plan:state.plan.plan_id};})()")
    assert result["plan"] == "p2"
    assert result["busy"] is False
    assert "Saved evaluation" not in result["message"]


def test_source_definitions_preserve_missing_rules_and_exact_receipt_binding():
    source = _function("function sourceRequirementsSegments(", "async function loadSourcePreview(")
    source += """
const plan={plan_id:'p1',receipt_hash:'receipt-hash'};
const preview={compatible:true,plan_id:'p1',receipt_hash:'receipt-hash',
segments:[{segment_id:'factual',segment_version:'v1',suggested_rule_hash:null},
{segment_id:'synthesis',segment_version:'v2',suggested_rule_hash:'verified-rule-hash'}]};
"""
    result = _javascript(source, """({
      compatible:sourceRequirementsSegments(preview,plan),
      mismatch:sourceRequirementsSegments(preview,{plan_id:'p2',receipt_hash:'new'}),
      rejected:sourceRequirementsSegments({...preview,compatible:false},plan)
    })""")
    assert result["compatible"] == [
        {"segment_id": "factual", "segment_version": "v1", "rule_hash": None},
        {"segment_id": "synthesis", "segment_version": "v2", "rule_hash": "verified-rule-hash"},
    ]
    assert result["mismatch"] == result["rejected"] == []
    assert 'value="factual-lookup"' not in HTML


@pytest.mark.parametrize("failure", ["network", "wrong_receipt", "malformed"])
def test_source_preview_failure_cannot_enable_attachment_or_rule_copy(failure):
    source = """
const elements = new Map();
const document = {getElementById(id) {
  if (!elements.has(id)) elements.set(id,{value:id==='lifecycle-run-selection'?'run-1':'',disabled:false,textContent:''});
  return elements.get(id);
}};
const state = {plan:{plan_id:'p1',receipt_id:'r1',receipt_hash:'hash-1'},lifecycleCapabilities:{read_only_import:true}};
function lifecycleActionButtons() { throw new Error('failed preview must not enable actions'); }
const number = value => value;
const escapeHtml = value => String(value);
"""
    source += {
        "network": "const fetch=async()=>{throw new Error('network down');};",
        "wrong_receipt": "const fetch=async()=>({ok:true,json:async()=>({schema_version:'studio-source-preview.v1',plan_id:'p1',run_id:'run-1',receipt_hash:'different',compatible:true,segments:[]})});",
        "malformed": "const fetch=async()=>({ok:true,json:async()=>({compatible:true})});",
    }[failure]
    source += _function("async function loadSourcePreview(", "function lifecycleActionButtons(")
    result = _javascript(source, """(async()=>{
      await loadSourcePreview();
      return {preview:state.sourcePreview,attach:document.getElementById('attach-lifecycle-run').disabled,
        copy:document.getElementById('use-source-definitions').disabled,
        message:document.getElementById('lifecycle-source-preview').textContent};
    })()""")
    assert result["preview"] is None
    assert result["attach"] is result["copy"] is True
    assert result["message"].startswith("Source verification unavailable:")
