import json
from pathlib import Path

from test_studio_workflow import _javascript


HTML = (Path(__file__).resolve().parents[1] / "studio.html").read_text(encoding="utf-8")


def _function(start, end):
    return HTML[HTML.index(start):HTML.index(end, HTML.index(start))]


def test_policy_objects_are_readable_and_active_card_has_no_resubmit():
    start = HTML.index("    const policyValue =")
    formatter = HTML[start:HTML.index("\n", start)]
    source = formatter + """
const target={innerHTML:''};
const document={getElementById:()=>target};
const escapeHtml=value=>String(value??'').replaceAll('<','&lt;').replaceAll('>','&gt;');
const safeUrl=()=>null;
const formatDate=value=>value;
const state={effectivePolicy:{change_control:{review:{configured:true}}},policyChanges:[{
 change_id:'PCR-ACTIVE',status:'active',base_policy:{version:'old'},proposed_version:'new',
 created_at:'now',reason:'Approved',diff:[{path:'measurement',current:null,
 proposed:{max_questions:10,hard_spend_cap_guaranteed:false,note:'<unsafe>'}}],
 proposed_policy:{}
}]};
"""
    source += _function("function renderPolicyChanges()", "function nextPolicyVersion(")
    html = _javascript(source, "(() => {renderPolicyChanges();return target.innerHTML;})()")
    assert "[object Object]" not in html
    assert '"max_questions": 10' in html
    assert '"hard_spend_cap_guaranteed": false' in html
    assert "&lt;unsafe&gt;" in html
    assert "data-submit-policy" not in html
    assert "data-edit-policy" not in html
    assert "Published: Azure read-back matches" in html


def test_playground_is_only_visible_for_saved_foundry_rag():
    source = """
const elements = new Map();
const document = {getElementById(id) {
 if (!elements.has(id)) elements.set(id, {textContent:'',innerHTML:'',hidden:false});
 return elements.get(id);
}};
const state = {lifecycleCapabilities:{release:'full_studio'}};
"""
    source += _function("function updateRagPlayground()", "async function checkRagConnection()")
    result = _javascript(
        source,
        """['single_call','rag_pipeline'].flatMap(topology =>
          ['foundry','copilot_studio'].map(route => {
            state.plan={plan_id:route+topology,receipt_id:'r1',route:{route_id:route},
              confirmed_profile:{agent_pattern:topology}};
            updateRagPlayground();
            return {route,topology,hidden:elements.get('rag-playground').hidden,
              disabled:elements.get('rag-send-question').disabled};
          }))""",
    )
    assert sum(not item["hidden"] for item in result) == 1
    assert next(item for item in result if not item["hidden"])["topology"] == "rag_pipeline"
    assert all(item["disabled"] for item in result)
    forecast_only = _javascript(
        source,
        """(() => {
          state.lifecycleCapabilities={release:'forecast_only'};
          state.plan={plan_id:'p1',receipt_id:'r1',route:{route_id:'foundry'},
            confirmed_profile:{agent_pattern:'rag_pipeline'}};
          updateRagPlayground();
          return elements.get('rag-playground').hidden;
        })()""",
    )
    assert forecast_only is True
    repeat_batch = _javascript(
        source,
        """(() => {
          state.plan={plan_id:'p1',receipt_id:'r1',route:{route_id:'foundry'},
            confirmed_profile:{agent_pattern:'rag_pipeline'}};
          state.ragPlanId='p1'; state.ragConnection={ready:true};
          state.ragQuestions=['Question?'];
          state.report={artifacts:{runs:[{id:'run1',execution_status:'failed_or_partial'}]}};
          updateRagPlayground();
          return {disabled:elements.get('rag-send-question').disabled,
            topology:elements.get('execute-topology').textContent,
            explanation:elements.get('execute-topology-note').textContent};
        })()""",
    )
    assert repeat_batch["disabled"] is False
    assert repeat_batch["topology"] == "rag_pipeline"
    assert "saved Forecast selects the RAG playground" in repeat_batch["explanation"]
    assert "p1" not in repeat_batch["explanation"]
    authorized_limit = _javascript(source, """(() => {
      state.plan={plan_id:'p1',receipt_id:'r1',route:{route_id:'foundry'},
        confirmed_profile:{agent_pattern:'rag_pipeline'}};
      state.ragPlanId='p1'; state.ragConnection={ready:true,measurement_authorization:{max_questions:1}};
      state.ragQuestions=['First?','Second?'];
      updateRagPlayground();
      return {disabled:elements.get('rag-send-question').disabled,
        message:elements.get('rag-batch-summary').textContent};
    })()""")
    assert authorized_limit["disabled"] is True
    assert "at most 1 questions" in authorized_limit["message"]
    uncertain_switch = _javascript(source, """(() => {
      state.ragRequest={id:'retained-id',planId:'p1',questions:['Only in memory?']};
      state.ragSubmissionUncertain=true;
      state.plan={plan_id:'p2',receipt_id:'r2',route:{route_id:'foundry'},
        confirmed_profile:{agent_pattern:'rag_pipeline'}};
      updateRagPlayground();
      const blockedElsewhere=elements.get('rag-send-question').disabled;
      state.plan={...state.plan,plan_id:'p1',receipt_id:'r1'};
      updateRagPlayground();
      return {id:state.ragRequest.id,questions:state.ragQuestions,blockedElsewhere,
        uncertain:state.ragSubmissionUncertain,newDisabled:elements.get('rag-new-attempt').disabled};
    })()""")
    assert uncertain_switch == {
        "id": "retained-id", "questions": ["Only in memory?"], "blockedElsewhere": True,
        "uncertain": True, "newDisabled": True,
    }


def test_answer_rendering_keeps_missing_usage_unavailable_and_escapes_sources():
    source = """
const elements = new Map();
const document = {getElementById(id) {
 if (!elements.has(id)) elements.set(id, {innerHTML:'',insertAdjacentHTML(_,html){this.innerHTML+=html;}});
 return elements.get(id);
}};
const state = {plan:{plan_id:'p1'}};
const escapeHtml = value => String(value ?? '').replaceAll('<','&lt;').replaceAll('>','&gt;');
const number = value => String(value);
"""
    source += _function("function renderRagResult(result)", "async function runRagQuestion()")
    source += """
renderRagResult({run_id:'run1',execution_status:'failed_or_partial',answers:[{question:'Who?',answer:'<script>bad</script>',status:'failed',error_code:'model_failed',
 sources:[{book:'Book',content:'<img>'}],usage:{input_tokens:0,output_tokens:12}}]});
"""
    result = _javascript(
        source,
        "({answer:elements.get('rag-answer-content').innerHTML,usage:elements.get('rag-usage-content').innerHTML})",
    )
    assert "<script>" not in result["answer"]
    assert "&lt;img&gt;" in result["answer"]
    assert "Unavailable" in result["usage"]
    assert "<td>0</td>" in result["usage"]
    assert "Review recorded usage below" in result["answer"]
    assert "Execution incomplete" in result["answer"]
    assert "not a successful answer" in result["answer"]
    assert "Do not add these detail columns" in result["usage"]


def test_live_submission_binds_receipt_and_csrf_and_uses_idempotency():
    connection = _function("async function checkRagConnection()", "function renderRagBatchResult(")
    assert "/api/rag-batches/connection?plan_id=${encodeURIComponent(planId)}" in connection
    run = _function("async function runRagQuestion()", 'document.getElementById("rag-check-connection").onclick')
    assert "if (state.ragBusy) return;" in run
    assert '"X-TokenGov-CSRF":state.lifecycleCapabilities?.csrf_token' in run
    assert "request_id:state.ragRequest.id" in run
    assert "/api/plans/${encodeURIComponent(planId)}/rag-batches" in run
    assert "questions:state.ragRequest.questions" in run
    assert "ragSubmissionUncertain" in run
    assert "receiptHash !== state.plan?.receipt_hash" in run
    assert "finally" in run


def test_correction_is_receipt_bound_and_does_not_replace_historical_evidence():
    source = """
const elements = new Map();
const document = {getElementById(id) {
 if (!elements.has(id)) elements.set(id, {innerHTML:'',hidden:true});
 return elements.get(id);
}};
const state = {plan:{plan_id:'p1',receipt_hash:'h1'}};
const escapeHtml = value => String(value ?? '').replaceAll('<','&lt;').replaceAll('>','&gt;');
const lifecycleEvidence = records => '<details>Original evidence</details>';
"""
    source += _function("function renderRagCorrections(records)", "function updateRagPlayground()")
    result = _javascript(source, """(() => {
      const record={kind:'rag-correction',plan_id:'p1',receipt_hash:'h1',
        source:{run_id:'run1'},tasks:[{task_id:'t1',provider_completion_verified:true,
          legacy_alias_false_failure_verified:true,model_list_price_allocation_usd:0.00041815,
          within_original_model_reservation:true}]};
      renderRagCorrections([record]);
      const html=elements.get('rag-correction-content').innerHTML;
      renderRagCorrections([{...record,receipt_hash:'different'}]);
      return {html,hidden:elements.get('rag-correction-panel').hidden};
    })()""")
    assert "0.00041815" in result["html"]
    assert "Provider completion verified" in result["html"]
    assert "does not rewrite that history" in result["html"]
    assert result["hidden"] is True


def test_uploaded_questions_and_sample_dataset_obey_batch_limit():
    source = _function("function parseRagQuestions(", "function reviewRagBatch()")
    result = _javascript(source, """(() => {
      const rejected = value => {try {parseRagQuestions(JSON.stringify(value),'json');return false;}catch {return true;}};
      return {plain:parseRagQuestions(' First?\\n\\nSecond? '),
        json:parseRagQuestions('["First?","First?"]','json'),
        rejected:[[],[''],[{}],{questions:['Q?']},Array(11).fill('Q?'),['x'.repeat(1201)],['two\\nlines']].map(rejected)};
    })()""")
    assert result["plain"] == ["First?", "Second?"]
    assert result["json"] == ["First?", "First?"]
    assert all(result["rejected"])
    path = Path(__file__).resolve().parents[1] / "rag" / "sample-questions.gutenberg.json"
    samples = json.loads(path.read_text(encoding="utf-8"))
    assert len(samples) == 10
    assert all(isinstance(q, str) and 0 < len(q) <= 1200 for q in samples)
    handlers = HTML[HTML.index('document.getElementById("rag-question-file").onchange'):]
    assert "file.size > 65536" in handlers
    assert 'localStorage' not in handlers
    assert 'runRagQuestion()' not in handlers


def test_new_batch_renderer_does_not_display_content_and_keeps_unknown_metrics():
    source = """
const elements = new Map();
const document = {getElementById(id) {
 if (!elements.has(id)) elements.set(id, {innerHTML:'',hidden:true});
 return elements.get(id);
}};
const escapeHtml = value => String(value ?? '').replaceAll('<','&lt;').replaceAll('>','&gt;');
const number = value => String(value);
"""
    source += _function("function renderRagBatchResult(", "function renderRagResult(result)")
    result = _javascript(source, """(() => {
      renderRagBatchResult({run_id:'r1',execution_status:'completed',questions_count:1,
        metrics:[{question_number:1,status:'completed',question:'PRIVATE_QUESTION',answer:'PRIVATE_ANSWER',
          input_tokens:123,output_tokens:45,embedding_tokens:null,cached_input_tokens:0,
          coverage_notes:['Embedding provider usage unavailable']}],evidence:{status:'local_pending_cloud'}});
      return elements.get('rag-usage-content').innerHTML;
    })()""")
    assert "PRIVATE_QUESTION" not in result
    assert "PRIVATE_ANSWER" not in result
    assert "Unavailable" in result
    assert "<td>123</td>" in result
    assert "local_pending_cloud" in result


def test_saved_blocked_and_failed_measurements_reopen_without_completed_status():
    source = """
const elements = new Map();
const document = {getElementById(id) {
 if (!elements.has(id)) elements.set(id, {innerHTML:'',textContent:'',hidden:true});
 return elements.get(id);
},querySelector(){return {click(){}};}};
const state = {plan:{plan_id:'p1',receipt_hash:'h1'}};
let rendered = [];
const renderRagBatchResult = result => rendered.push(result.execution_status);
const renderRun = async () => {throw new Error('Measurement is not legacy trajectory evidence');};
const opened = [];
const openPlan = async plan_id => {opened.push(plan_id);state.plan={plan_id,receipt_hash:'h2'};};
let saved;
const fetch = async () => ({ok:true,json:async()=>saved});
"""
    source += _function("async function loadRun(runId)", "function nullableMoney(")
    result = _javascript(source, """(async () => {
      for (const status of ['blocked','partial','failed','failed_or_partial']) {
        saved={status,result:{schema_version:'rag-agent-batch.v2',run_id:'r1',
          plan_id:'p1',prediction:{content_hash:'h1'},metrics:[],execution_status:status}};
        await loadRun('r1');
      }
      saved={status:'blocked',result:{schema_version:'rag-agent-batch.v1',run_id:'historical',
        plan_id:'p1',prediction:{content_hash:'h1'},metrics:[],execution_status:'blocked'}};
      await loadRun('historical');
      saved={status:'blocked',result:{schema_version:'rag-agent-batch.v2',run_id:'r2',
        plan_id:'p1',receipt_hash:'different',metrics:[],execution_status:'blocked'}};
      await loadRun('r2');
      const message=elements.get('run-content').textContent;
      state.report={report_id:'report1'};
      saved={status:'blocked',result:{schema_version:'rag-agent-batch.v2',run_id:'r3',
        plan_id:'p2',report_id:'report1',prediction:{content_hash:'h2'},metrics:[],execution_status:'blocked'}};
      await loadRun('r3');
      return {rendered,message,opened};
    })()""")
    assert result["rendered"] == ["blocked", "partial", "failed", "failed_or_partial", "blocked", "blocked"]
    assert "receipt" in result["message"].lower()
    assert result["opened"] == ["p2"]


def test_measurement_results_distinguish_cloud_publication_and_never_imply_acceptance():
    source = """
const elements = new Map();
const document = {getElementById(id) {
 if (!elements.has(id)) elements.set(id, {innerHTML:'',hidden:true});
 return elements.get(id);
}};
const escapeHtml = value => String(value ?? '').replaceAll('<','&lt;').replaceAll('>','&gt;');
const number = value => String(value);
"""
    source += _function("function renderRagBatchResult(", "function renderRagResult(result)")
    result = _javascript(source, """['failed','published'].map(cloud_status => {
      renderRagBatchResult({schema_version:'rag-agent-batch.v2',run_id:'r1',
        execution_status:'failed_or_partial',questions_count:2,metrics:[
          {question_number:1,status:'completed',input_tokens:0,output_tokens:null,
           coverage_notes:['<img src=x onerror=bad()>']},
          {question_number:2,status:'failed',error_code:'<script>bad</script>'}],
        evidence:{status:'persisted_locally',cloud_status,location:'studio_runs/r1/result.json'},
        acceptance_status:'not_evaluated',operational_promotion:false});
      return elements.get('rag-usage-content').innerHTML;
    })""")
    for html, status in zip(result, ["failed", "published"]):
        assert f"Cloud publication:</strong> {status}" in html
        assert "not_evaluated" in html
        assert "not guaranteed" in html
        assert "complete task cost" in html
        assert "<script>" not in html and "<img" not in html
        assert "&lt;img" in html
        assert "<td>0</td>" in html and "Unavailable" in html


def test_measurement_authority_has_policy_navigation_and_no_browser_toggle():
    panel = HTML[HTML.index('id="rag-playground"'):HTML.index('id="lifecycle-run-selection"')]
    assert 'data-workflow-view="policy"' in panel
    assert 'data-workflow-pane="changes"' in panel
    assert "measurement-only" in panel.lower()
    assert "not guaranteed" in panel
    assert 'type="checkbox"' not in panel
    assert 'max_questions' not in panel
    refresh = _function('document.getElementById("refresh-policy-button").addEventListener', 'document.getElementById("policy-proposal-form").addEventListener("change"')
    assert "loadGovernWorkspace()" in refresh
    assert 'method: "POST"' not in refresh
    assert "runRagQuestion" not in refresh


def test_economics_review_hides_inapplicable_acceptance_and_preserves_unknown_totals():
    source = """
const elements=new Map();
const document={getElementById(id){
 if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',hidden:false});
 return elements.get(id);
}};
const escapeHtml=value=>String(value??'');
const number=value=>String(value);
"""
    source += _function("function renderRagBatchResult(", "function renderRagResult(result)")
    result = _javascript(source, """(() => {
      renderRagBatchResult({schema_version:'rag-agent-batch.v2',run_id:'r1',
        execution_status:'partial',questions_count:2,observed_model_allocation_usd:0.001,
        metrics:[{question_number:1,status:'completed',input_tokens:0,output_tokens:4},
                 {question_number:2,status:'not_dispatched',input_tokens:null,output_tokens:null}],
        evidence:{status:'persisted_locally_cloud_pending',cloud_status:'published'}});
      return {content:elements.get('rag-usage-content').innerHTML,
        hidden:elements.get('acceptance-evaluation-workflow').hidden,
        governHidden:elements.get('evaluate-next-govern').hidden,
        nextHidden:elements.get('rag-measurement-next').hidden,
        heading:elements.get('evaluate-heading').textContent,
        action:elements.get('rag-answer-content').innerHTML};
    })()""")
    assert result["hidden"] and not result["governHidden"]
    assert not result["nextHidden"]
    assert result["heading"] == "Review batch economics"
    assert "0 known (1/2 questions)" in result["content"]
    assert "4 known (1/2 questions)" in result["content"]
    assert "persisted_locally_cloud_pending" not in result["content"].split("<summary>Technical evidence details</summary>")[0]
    assert "Saved locally" in result["content"]
    assert "Review batch economics" in result["action"]
    assert "Next: Evaluate this batch" not in result["action"]


def test_prepare_from_evaluate_never_runs_and_retains_unknown_request():
    source = """
let clickCount=0,prepared=0,focused=null;
const button={};
const document={getElementById:id=>id==='rag-prepare-from-evaluate'?button:{},
 querySelector:()=>({click:()=>clickCount++})};
const state={ragOutcomeKnown:true,ragSubmissionUncertain:true};
const prepareNewRagAttempt=()=>prepared++;
const focusWorkflow=id=>focused=id;
"""
    source += _function('document.getElementById("rag-prepare-from-evaluate").onclick', "async function runRagQuestion()")
    result = _javascript(source, """(() => {
      button.onclick(); const uncertain=prepared;
      state.ragSubmissionUncertain=false; button.onclick();
      return {uncertain,prepared,clickCount,focused};
    })()""")
    assert result == {"uncertain": 0, "prepared": 1, "clickCount": 2, "focused": "rag-playground"}
    assert "runRagQuestion(" not in source and "fetch(" not in source


def test_nonmeasurement_paths_restore_acceptance_workflow():
    for start, end in [
        ("async function renderRun()", "function lifecycleSelection"),
        ("function renderRagResult(result)", "function prepareNewRagAttempt()"),
        ("function updateRagPlayground()", "async function checkRagConnection()"),
    ]:
        body = _function(start, end)
        assert 'getElementById("acceptance-evaluation-workflow").hidden = false' in body
        assert 'getElementById("evaluate-next-govern").hidden = false' in body


def test_run_table_orders_recorded_dates_and_keeps_ids_in_details():
    source = """
const elements=new Map();
const document={getElementById(id){
 if(!elements.has(id))elements.set(id,{innerHTML:''}); return elements.get(id);
},querySelectorAll:()=>[]};
const escapeHtml=value=>String(value??'').replaceAll('<','&lt;').replaceAll('>','&gt;');
const formatDate=value=>value;
const humanizeStatus=value=>value.replaceAll('_',' ');
const number=value=>String(value);
"""
    source += _function("function renderReportRuns(", "function selectRouteForPlan(")
    result = _javascript(source, """(() => {
      const runs=[
        {id:'older',started_at:'2026-09-09T11:00:00-04:00',execution_status:'blocked'},
        {id:'unknown',status:'failed'},
        {id:'newer',started_at:'2026-09-09T15:01:00Z',status:'completed',schema_version:'rag-agent-batch.v2',questions_count:10}
      ];
      renderReportRuns({artifacts:{runs}});
      const table=elements.get('report-runs-content').innerHTML;
      renderReportRuns({artifacts:{runs:[]}});
      return {table,empty:elements.get('report-runs-content').innerHTML,ids:runs.map(run=>run.id)};
    })()""")
    table = result["table"]
    assert "<table>" in table and "run-card" not in table
    assert table.index("<code>newer") < table.index("<code>older") < table.index("<code>unknown")
    assert "Review batch economics" in table and "Review blocked attempt" in table
    assert "Not recorded" in table
    assert "<summary>Technical ID</summary><code>newer" in table
    assert result["ids"] == ["older", "unknown", "newer"]
    assert "No saved runs yet" in result["empty"] and "<tr>" not in result["empty"]


def test_batch_coverage_is_deduplicated_plain_language_with_raw_details():
    source = """
const elements=new Map();
const document={getElementById(id){
 if(!elements.has(id))elements.set(id,{innerHTML:'',hidden:true}); return elements.get(id);
}};
const escapeHtml=value=>String(value??'').replaceAll('<','&lt;').replaceAll('>','&gt;');
const number=value=>String(value);
"""
    source += _function("function renderRagBatchResult(", "function renderRagResult(result)")
    result = _javascript(source, """(() => {
      renderRagBatchResult({run_id:'long-hex-id',execution_status:'completed',
        schema_version:'rag-agent-batch.v2',questions_count:10,
        metrics:Array.from({length:10},(_,i)=>({question_number:i+1,status:'completed',
          coverage_notes:['managed_retrieval_internal_usage_unavailable','acceptance_not_evaluated',
            'tool_counts_exposed_items_only','response_level_cumulative_usage_only']}))});
      return {content:elements.get('rag-usage-content').innerHTML,
        playground:elements.get('rag-answer-content').innerHTML,
        visible:!elements.get('observe').hidden};
    })()""")
    business, technical = result["content"].split("<summary>Technical evidence details</summary>")
    assert business.count("Search and embedding usage") == 1
    assert business.count("All 10 questions") == 4
    assert "managed_retrieval_internal_usage_unavailable" not in business
    assert "long-hex-id" not in business
    assert "managed_retrieval_internal_usage_unavailable" in technical
    assert "Answer quality was not evaluated" in business
    assert "<table>" not in result["playground"]
    assert result["visible"]


def test_rag_review_stays_empty_until_selection_even_when_capabilities_arrive_later():
    source = """
const elements=new Map();
const document={getElementById(id){
 if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',hidden:false});
 return elements.get(id);
}};
const state={plan:{plan_id:'p1',receipt_id:'r1',route:{route_id:'foundry'},
 confirmed_profile:{agent_pattern:'rag_pipeline'}}};
"""
    source += _function("function updateRagPlayground()", "async function checkRagConnection()")
    result = _javascript(source, """(() => {
      updateRagPlayground();
      const before=elements.get('observe').hidden;
      state.lifecycleCapabilities={release:'full_studio'};
      updateRagPlayground();
      return {before,after:elements.get('observe').hidden,
        importHidden:elements.get('historical-evidence-import').hidden,
        playgroundHidden:elements.get('rag-playground').hidden};
    })()""")
    assert result == {"before": True, "after": True, "importHidden": True, "playgroundHidden": False}


def test_connection_check_is_read_only_and_reports_escaped_distinct_blockers():
    source = """
const elements = new Map();
const document = {getElementById(id) {
 if (!elements.has(id)) elements.set(id, {textContent:'',innerHTML:'',disabled:false});
 return elements.get(id);
}};
const state = {plan:{plan_id:'p1'}};
const updateRagPlayground = () => {};
const calls=[];
const fetch=async (url, options) => {
 calls.push({url,options});
 return {ok:true,json:async()=>({ready:false,configuration_ready:true,
   retrieval_mode:'managed_mcp_hybrid_verified',blockers:[
   {code:'measurement_authorization_expired',message:'Expired Azure authorization'},
   {code:'evidence_configuration_missing',message:'Missing Azure storage <img>'}]})};
};
"""
    source += _function("async function checkRagConnection()", "function renderRagBatchResult(")
    result = _javascript(source, """(async () => {
      await checkRagConnection();
      return {calls,text:elements.get('rag-connection-status').textContent,
        html:elements.get('rag-connection-status').innerHTML,ready:state.ragConnection.ready};
    })()""")
    assert len(result["calls"]) == 1
    assert result["calls"][0]["url"] == "/api/rag-batches/connection?plan_id=p1"
    assert "options" not in result["calls"][0]
    assert result["html"] == ""
    assert "Hybrid configuration verified" in result["text"]
    assert "not runtime proof" in result["text"]
    assert "retrieval_mode_unverified" not in result["text"]
    assert "measurement_authorization_expired" in result["text"]
    assert "evidence_configuration_missing" in result["text"]
    assert result["ready"] is False
    assert "Read-only configuration: ready" in result["text"]
    assert "Execution readiness: blocked" in result["text"]


def test_published_measurement_is_not_mislabeled_as_pending_review():
    source = """
const elements = new Map();
const document = {getElementById(id) {
 if (!elements.has(id)) elements.set(id, {textContent:'',disabled:false});
 return elements.get(id);
}};
const state = {plan:{plan_id:'p1'}};
const updateRagPlayground = () => {};
const fetch = async () => ({ok:true,json:async()=>({
  ready:true,configuration_ready:true,retrieval_mode:'managed_mcp_hybrid_verified',
  measurement_authorization:{max_questions:10,max_output_tokens:1024,
    max_elapsed_seconds:300,observed_model_cost_stop_usd:0.25,
    expires_at:'2026-09-10T22:00:00Z'},blockers:[]
})});
"""
    source += _function("async function checkRagConnection()", "function renderRagBatchResult(")
    text = _javascript(source, """(async () => {
      await checkRagConnection();
      return elements.get('rag-connection-status').textContent;
    })()""")
    assert "Published measurement authorization is present" in text
    assert "review and publication required" not in text
    assert "separate evaluation permission is still required" in text


def test_measurement_allocation_and_publication_require_exact_evidence_hash():
    source = """
const elements = new Map();
const document = {getElementById(id) {
 if (!elements.has(id)) elements.set(id, {innerHTML:'',hidden:true});
 return elements.get(id);
}};
const escapeHtml = value => String(value ?? '').replaceAll('<','&lt;').replaceAll('>','&gt;');
const number = value => String(value);
"""
    source += _function("function renderRagBatchResult(", "function renderRagResult(result)")
    result = _javascript(source, """['wrong','h1',null].map(hash => {
      const batch={schema_version:'rag-agent-batch.v2',run_id:'r1',execution_status:'failed_or_partial',
        observed_model_allocation_usd:0.00041815,stop_reason:'observed_model_cost_stop',
        allocations:[{question_number:1,model_identity_bound:true,model_allocation_usd:0.00041815},
          {question_number:2,model_identity_bound:false,model_allocation_usd:null}],
        measurement_authorization:{max_questions:10,max_output_tokens:1024,max_elapsed_seconds:300,
          observed_model_cost_stop_usd:0.25,expires_at:'2026-09-10T22:00:00Z'},
        evidence:{status:'persisted_locally_cloud_pending',content_hash:'h1',
          cloud_status:'publication_tracked_separately'},metrics:[]};
      renderRagBatchResult(batch,{schema_version:'rag-batch-publication.v1',
        run_id:'r1',content_hash:hash,status:hash===null?'pending':'published'});
      return elements.get('rag-usage-content').innerHTML;
    })""")
    assert "Cloud publication:</strong> published" not in result[0]
    assert "Cloud publication:</strong> published" in result[1]
    assert "Cloud publication:</strong> pending" in result[2]
    assert "0.00041815" in result[1]
    assert "Unavailable" in result[1]
    assert "observed_model_cost_stop" in result[1]
    assert "1024 output tokens" in result[1]
    assert "unknown allocations are not zero-cost tasks" in result[1]


def test_measurement_proposals_explain_review_without_enabling_execution():
    source = """
const target={innerHTML:''};
const document={getElementById(){return target;}};
const escapeHtml=value=>String(value??'').replaceAll('<','&lt;').replaceAll('>','&gt;');
const safeUrl=()=>null;
const formatDate=value=>value;
const policyValue=value=>JSON.stringify(value);
const state={effectivePolicy:{change_control:{review:{configured:true}}},policyChanges:[
 {change_id:'PCR-MEASURE',status:'draft',base_policy:{version:'active'},proposed_version:'proposal',
  created_at:'now',diff:[],reason:'Collect metrics',proposed_policy:{measurement:{
    schema_version:'workload-measurement-policy.v1',mode:'measurement_only',workload_scope:'studio_batches',
    max_questions:10,max_output_tokens:1024,max_elapsed_seconds:300,observed_model_cost_stop_usd:0.25,
    expires_at:'<unsafe>',acknowledge_incomplete_costs:true,hard_spend_cap_guaranteed:false,operational_promotion:false}}}]};
"""
    source += _function("function renderPolicyChanges()", "function nextPolicyVersion(")
    result = _javascript(source, "(() => {renderPolicyChanges();return target.innerHTML;})()")
    assert "Measurement authorization proposal" in result
    assert "not permission to run" in result
    assert "workload-measurement-policy.v1" in result
    assert "hard_spend_cap_guaranteed" in result
    assert "&lt;unsafe&gt;" in result
    assert "PCR-MEASURE" in result


def test_measurement_draft_details_are_preserved_when_editing_other_controls():
    form = _function("function openProposalForm(", 'document.getElementById("create-policy-button")')
    submit = _function('document.getElementById("policy-proposal-form").addEventListener("submit"', "function ")
    assert "state.proposalMeasurement" in form
    assert "proposal-measurement-details" in form
    assert "payload.changes.measurement = state.proposalMeasurement" in submit
    source = """
const elements=new Map();
const document={getElementById(id){if(!elements.has(id))elements.set(id,{});return elements.get(id);}};
const active={version:'active',admission:{allowed_providers:[],allowed_models:[],
  max_model_cost_per_call_usd:1,require_pricing_verified:true,require_infrastructure_estimate:true},
  execution:{budget:{per_tenant_usd_per_run:5},routing_mode:'quality',
    evaluation:{min_quality:0.9,min_segment_samples:5}}};
const state={effectivePolicy:{policy:active},ragConnection:{ready:false}};
const nextPolicyVersion=()=> 'next';
const renderPolicySupplyPickers=()=>{};
const showGovernPane=()=>{};
"""
    source += form
    result = _javascript(source, """(() => {
      const proposed={...active,measurement:{mode:'measurement_only',max_questions:2}};
      openProposalForm(null,'edit',{change_id:'PCR-1',proposed_policy:proposed});
      const retained=JSON.stringify(state.proposalMeasurement);
      const details=elements.get('proposal-measurement-details').textContent;
      const independent=state.proposalMeasurement!==proposed.measurement;
      openProposalForm();
      return {retained,details,independent,reset:state.proposalMeasurement,ready:state.ragConnection.ready};
    })()""")
    assert json.loads(result["retained"]) == {"mode": "measurement_only", "max_questions": 2}
    assert "measurement_only" in result["details"]
    assert result["independent"] is True
    assert result["reset"] is None
    assert result["ready"] is False


def test_failed_posts_keep_uuid_and_inputs_until_explicit_new_attempt():
    source = """
const elements=new Map();
const document={getElementById(id){if(!elements.has(id))elements.set(id,{textContent:'',value:'Question?',disabled:false});return elements.get(id);}};
const state={plan:{plan_id:'p1',receipt_hash:'h1'},report:{report_id:'report1'},
 ragConnection:{ready:true},ragQuestions:['Question?'],lifecycleCapabilities:{csrf_token:'token'}};
let uuidCount=0, postCount=0;const requests=[];
const crypto={randomUUID(){return `uuid-${++uuidCount}`;}};
const setPlanStage=()=>{};const updateRagPlayground=()=>{};
const renderRagBatchResult=()=>{};const loadRun=async()=>{};
const loadLifecycle=async()=>{throw new Error('Metrics-only submission must not import conventional envelopes');};
const fetch=async(url,options)=>{
 if(!options)return {ok:true,json:async()=>({report_id:'report1'})};
 requests.push(JSON.parse(options.body));postCount++;
 if(postCount===1)throw new Error('Network outcome unknown');
 if(postCount===2)return {ok:false,json:async()=>({error:'Evidence unavailable'})};
 return {ok:true,json:async()=>({schema_version:'rag-agent-batch.v2',run_id:'r1',
   plan_id:'p1',prediction:{content_hash:'h1'},execution_status:'completed'})};
};
"""
    source += _function("function parseRagQuestions(", "function reviewRagBatch()")
    source += _function("function reviewRagBatch()", "function updateRagPlayground()")
    source += _function("function prepareNewRagAttempt()", "async function runRagQuestion()")
    source += _function("async function runRagQuestion()", 'document.getElementById("rag-check-connection").onclick')
    result = _javascript(source, """(async()=>{
      await runRagQuestion();
      const firstUncertain=state.ragSubmissionUncertain;
      prepareNewRagAttempt();
      document.getElementById('rag-question').value='Changed?';reviewRagBatch();
      await runRagQuestion();
      const secondUncertain=state.ragSubmissionUncertain;
      await runRagQuestion();
      await runRagQuestion();
      const beforeNew={uuidCount,postCount,id:state.ragRequest.id};
      prepareNewRagAttempt();
      await runRagQuestion();
      return {firstUncertain,secondUncertain,beforeNew,uuidCount,requests};
    })()""")
    assert result["firstUncertain"] is True
    assert result["secondUncertain"] is True
    assert result["beforeNew"] == {"uuidCount": 1, "postCount": 3, "id": "uuid-1"}
    assert [item["request_id"] for item in result["requests"]] == ["uuid-1"] * 3 + ["uuid-2"]
    assert all(item["questions"] == ["Question?"] for item in result["requests"][:3])
    assert result["uuidCount"] == 2


def test_metrics_only_runs_are_not_conventional_sources_or_question_diagnostics():
    lifecycle = _function("async function loadLifecycle()", "async function lifecycleAction(")
    assert '.startsWith("rag-agent-batch.")' in lifecycle
    assert "item.schema_version" in lifecycle
    request_ui = _function("function parseRagQuestions(", "bindWorkflowNavigation();")
    assert "localStorage" not in request_ui
    assert "sessionStorage" not in request_ui
    assert "console." not in request_ui
    assert "sendBeacon" not in request_ui
