import json

from test_performance_ui import DOM, _function
from test_reconciliation_ui import _context_source
from test_studio_workflow import _javascript


def feedback():
    return {
        "schema_version": "studio-batch-feedback-review.v1", "plan_id": "p1", "report_id": "r1",
        "receipt_hash": "hash", "run_id": "run1", "operational_promotion": False,
        "automatic_policy_changes": False, "billing_source": None,
        "billing": {"status": "awaiting_billing_data"},
        "learning": {"status": "scope_compatibility_pending", "observation_count": 1, "registered": True,
                     "eligible_sample_count": 0, "minimum_samples": 10,
                     "calibration_applied": False, "reason": "Response scope is not proven equivalent."},
        "usage": {"input_tokens": 9262, "output_tokens": 1330, "questions_completed": 10},
        "saved_records": [],
    }


def test_feedback_is_scoped_partial_and_not_an_acceptance_or_cost_claim():
    source = DOM + """
const performanceNumber=value=>value==null?'Unavailable':String(value);
const rate=value=>value==null?'Unavailable':`${value*100}%`;
"""
    source += _function("function renderBatchFeedback(", "async function batchFeedbackAction(")
    result = _javascript(source, "renderBatchFeedback(" + json.dumps(feedback()) + ")")
    assert "Billing not yet reconciled" in result
    assert "Usage feedback recorded" in result
    assert "Response scope is not proven equivalent" in result
    assert "Before WAPE: Unavailable" in result
    assert "9262" in result and "1330" in result
    assert "full task billing" in result.lower() and "No acceptance outcome" in result


def test_completed_batch_loads_new_feedback_without_global_portability():
    source = _context_source()
    source += "const review=" + json.dumps(feedback()) + ";"
    source += """
const calls=[];
const fetch=async url=>{calls.push(url);return {ok:true,json:async()=>({review})};};
"""
    source += _function("async function loadReconciliations(", "function renderBatchFeedback(")
    source += "const renderBatchFeedback=review=>'Current feedback';"
    result = _javascript(source, "loadReconciliations().then(()=>({calls,html:elements.get('reconcile-content').innerHTML}))")
    assert result == {"calls": ["/api/plans/p1/batch-feedback?run_id=run1"], "html": "Current feedback"}


def test_foreign_feedback_cannot_be_rendered():
    source = _context_source()
    foreign = feedback()
    foreign["receipt_hash"] = "foreign"
    source += "const review=" + json.dumps(foreign) + ";"
    source += "const fetch=async()=>({ok:true,json:async()=>({review})});"
    source += _function("async function loadReconciliations(", "function renderBatchFeedback(")
    source += "const renderBatchFeedback=()=>{throw new Error('must not render foreign evidence');};"
    result = _javascript(source, "loadReconciliations().then(()=>elements.get('reconcile-content').innerHTML)")
    assert "does not match" in result and "must not render" not in result


def test_register_and_sync_only_send_the_run_id_to_authorized_endpoints():
    source = _context_source() + """
state.lifecycleCapabilities={csrf_token:'token',read_only_import:true};
const calls=[];
const fetch=async (url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({created:true})};};
const loadReconciliations=async()=>{};
"""
    source += _function("async function batchFeedbackAction(", "async function loadProjectEvidence(")
    result = _javascript(source, "batchFeedbackAction(false).then(()=>batchFeedbackAction(true)).then(()=>calls)")
    assert [item["url"] for item in result] == ["/api/plans/p1/batch-feedback", "/api/plans/p1/batch-billing-sync"]
    for item in result:
        assert json.loads(item["options"]["body"]) == {"run_id": "run1"}
        assert item["options"]["headers"]["X-TokenGov-CSRF"] == "token"


def test_feedback_save_does_not_update_another_selected_run():
    source = _context_source() + """
state.lifecycleCapabilities={csrf_token:'token',read_only_import:true};
let finish;let loads=0;
const fetch=()=>new Promise(resolve=>{finish=resolve;});
const loadReconciliations=async()=>{loads++;};
"""
    source += _function("async function batchFeedbackAction(", "async function loadProjectEvidence(")
    source += """
const pending=batchFeedbackAction(false);
state.performance.selected_run_id='run2';
elements.get('batch-feedback-status').textContent='New batch status';
finish({ok:true,json:async()=>({created:true})});
"""
    result = _javascript(source, "pending.then(()=>({status:elements.get('batch-feedback-status').textContent,loads}))")
    assert result == {"status": "New batch status", "loads": 0}


def test_query_snapshot_is_not_presented_as_an_itemized_export():
    review = feedback()
    review["billing_source"] = {
        "row_count": 8, "totals_by_currency": {"USD": 2.5},
        "period": {"start": "2026-09-01", "end": "2026-09-09"},
        "retrieved_at": "2026-09-14T18:00:00Z",
        "source": {"kind": "azure_cost_management_query"},
    }
    review["source_predates_batch"] = False
    source = DOM + """
const performanceNumber=value=>value==null?'Unavailable':String(value);
const rate=value=>value==null?'Unavailable':`${value*100}%`;
"""
    source += _function("function renderBatchFeedback(", "async function batchFeedbackAction(")
    result = _javascript(source, "renderBatchFeedback(" + json.dumps(review) + ")")
    assert "Aggregated billing rows" in result and "Query retrieved:" in result
    assert "not an itemized export" in result
    assert "Export delivered:" not in result and "predates execution" not in result
    assert "Billing finality is not established" in result


def test_quality_panel_keeps_advisory_outcomes_and_unknown_judge_cost_separate():
    review = feedback()
    review["quality"] = {
        "status": "advisory_evaluation", "evaluated_cases": 2,
        "human_review_status": "pending", "reason": "Rubric review is pending.",
        "segments": [{"segment_id": "hard<script>", "accepted": 0, "rejected": 1, "inconclusive": 1}],
        "judge_input_tokens": None, "judge_output_tokens": None, "judge_cost_usd": None,
        "eval_id": "eval-1", "eval_run_id": "eval-run-1", "content_hash": "a" * 64,
        "scores": [{"evaluator_id": "groundedness", "mean_score": 4.5, "scored_responses": 2},
                   {"evaluator_id": "correctness", "mean_score": None, "scored_responses": 0}],
    }
    source = DOM + """
const performanceNumber=value=>value==null?'Unavailable':String(value);
const rate=value=>value==null?'Unavailable':`${value*100}%`;
const nullableMoney=value=>value==null?'Unavailable':String(value);
"""
    source += _function("function renderBatchFeedback(", "async function batchFeedbackAction(")
    result = _javascript(source, "renderBatchFeedback(" + json.dumps(review) + ")")
    assert "Foundry answer quality" in result and "Rubric review is pending" in result
    assert "Evaluation cost: Unavailable" in result
    assert "hard<script>" not in result
    assert "Judge input tokens: Unavailable" in result
    assert "no complete-task cost or operational approval" in result
    assert "Mean score / 5" in result and "4.5" in result
    assert "not a probability of correctness" in result


def test_quality_panel_exposes_explicit_human_review_without_auto_accepting():
    quality = {
        "status": "advisory_evaluation", "acceptance_status": "human_review_in_progress",
        "evaluated_cases": 2, "reviewed_cases": 1, "accepted": 1, "rejected": 0,
        "inconclusive": 0, "acceptance_rate": 1, "reason": "Human review required.",
        "segments": [{"segment_id": "factual", "reviewed": 1, "accepted": 1,
                      "rejected": 0, "inconclusive": 0, "acceptance_rate": 1}],
        "scores": [], "candidates": [{
            "case_id": "qna-f02", "segment_id": "factual",
            "question": "<question>", "expected_answer": "<expected>",
            "scores": {"groundedness": 5, "correctness": 4},
            "automated_recommendation": "accepted", "human_decision": None,
            "response_content_hash": "a" * 64,
        }],
        "report_url": "https://ai.azure.com/project/evaluation/run",
        "judge_input_tokens": None, "judge_output_tokens": None, "judge_cost_usd": None,
        "eval_id": "eval-1", "eval_run_id": "eval-run-1", "content_hash": "b" * 64,
    }
    source = DOM + """
const performanceNumber=value=>value==null?'Unavailable':String(value);
const percent=value=>`${value}%`;
const nullableMoney=value=>value==null?'Unavailable':String(value);
"""
    source += _function("function renderQnaQuality(", "async function batchFeedbackAction(")
    result = _javascript(source, "renderQnaQuality(" + json.dumps(quality) + ")")
    assert "Open in Foundry" in result
    assert "Accept</button>" in result and "Reject</button>" in result
    assert "&lt;question&gt;" in result and "<question>" not in result
    assert "Exact response content is not copied" in result
    assert "100%" in result


def test_registered_forecast_adjustment_and_saved_holdout_comparison_are_distinct():
    review = feedback()
    review["learning"].update(
        forecast_calibration_applied=True,
        held_out_evaluations=[{"target": "output_tokens_mean", "status": "improved",
                              "held_out_sample_count": 3, "before_wape": .5, "after_wape": .1,
                              "evaluation_id": "eval<script>"}],
    )
    source = DOM + """
const performanceNumber=value=>value==null?'Unavailable':String(value);
const rate=value=>value==null?'Unavailable':`${value*100}%`;
"""
    source += _function("function renderBatchFeedback(", "async function batchFeedbackAction(")
    rendered = _javascript(source, "renderBatchFeedback(" + json.dumps(review) + ")")
    assert "Calibration applied to this forecast: Yes" in rendered
    assert "Before WAPE: 50%" in rendered and "After WAPE: 10%" in rendered
    assert "not quality acceptance or production improvement" in rendered
    assert "eval<script>" not in rendered
