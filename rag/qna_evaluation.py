"""Strict offline adapter contract, NOT an Azure/Foundry API wire format.

Cloud transports explicitly translate supported API responses into these records.
Every record repeats IDENTITY_FIELDS and includes evaluation_id,
evaluation_run_id, output_item_id, evaluator_id, revision, model, dataset_hash,
acceptance_rules_revision, status, score and optional judge_usage. A completed
score is a finite number on 1..5; citation/abstention graders use that same scale.
Usage is {input_tokens, output_tokens, cost_usd}; unavailable values remain null.
Neither caller-provided records nor content hashes authenticate a cloud source.
"""

from __future__ import annotations

import math
from collections import Counter
from copy import deepcopy

from rag.qna_dataset import content_hash, dataset_hash, text_hash, validate_dataset

IDENTITY_FIELDS = (
    "case_id", "family_id", "segment_id", "run_id", "question", "provider_response_id",
)
EVALUATOR_IDS = (
    "groundedness", "relevance", "correctness", "completeness", "citation", "abstention",
)
DEFAULT_ACCEPTANCE_RULES = {
    "schema_version": "rag-qna-acceptance-rules.v1",
    "revision": "qna-pilot-acceptance.v1",
    "minimum_scores": {name: 4 for name in EVALUATOR_IDS},
    "human_review_required": True,
}


def _number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def _usage(raw):
    raw = raw if isinstance(raw, dict) else {}
    return {
        "input_tokens": raw.get("input_tokens") if type(raw.get("input_tokens")) is int
        and raw["input_tokens"] >= 0 else None,
        "output_tokens": raw.get("output_tokens") if type(raw.get("output_tokens")) is int
        and raw["output_tokens"] >= 0 else None,
        "cost_usd": raw.get("cost_usd") if _number(raw.get("cost_usd")) else None,
    }


def _identity(item):
    return tuple(item.get(key) for key in IDENTITY_FIELDS)


def _total(values):
    return sum(values) if values and all(value is not None for value in values) else None


def normalize_evaluation(
    dataset, attempts, records, *, evaluation_id, evaluation_run_id,
    evaluators, acceptance_rules=None,
):
    """Join expected attempts to adapter-normalized grader results, fail closed.

    Each attempt supplies IDENTITY_FIELDS, response_text, grounding_context
    [{source_id,text,content_hash}], citations [source_id], abstained (bool),
    agent_usage, and cost_scope ('response_model_only' or 'complete_trajectory').
    Distinct provider response IDs allow retries. All attempts and all supplied
    judge records contribute to cost coverage, including failures and orphans.
    An output_item_id denotes one Foundry evaluated output; its graders share it.
    A duplicate (output_item_id,evaluator_id) or an item reused by two attempts
    invalidates the affected evaluation. This function performs no I/O.
    """
    validate_dataset(dataset)
    if not all(isinstance(value, str) and value.strip()
               for value in (evaluation_id, evaluation_run_id)):
        raise ValueError("Explicit Foundry evaluation and run identities required")
    rules = deepcopy(acceptance_rules or DEFAULT_ACCEPTANCE_RULES)
    if (rules.get("schema_version") != DEFAULT_ACCEPTANCE_RULES["schema_version"]
            or not isinstance(rules.get("revision"), str) or not rules["revision"].strip()
            or rules.get("human_review_required") is not True
            or set(rules.get("minimum_scores", {})) != set(EVALUATOR_IDS)
            or any(not _number(value) or not 1 <= value <= 5
                   for value in rules["minimum_scores"].values())):
        raise ValueError("Invalid or unsafe acceptance rules")
    if (set(evaluators) != set(EVALUATOR_IDS)
            or any(not isinstance(config, dict)
                   or not all(isinstance(config.get(key), str) and config[key].strip()
                              for key in ("revision", "model"))
                   for config in evaluators.values())):
        raise ValueError("Pin all six evaluator revisions and models")
    if not isinstance(attempts, list) or not isinstance(records, list):
        raise ValueError("Attempts and records must be lists")
    cases = {case["case_id"]: case for case in dataset["cases"]}
    identities, provider_ids = set(), set()
    for attempt in attempts:
        if not isinstance(attempt, dict) or not all(
            isinstance(attempt.get(key), str) and attempt[key].strip()
            for key in IDENTITY_FIELDS
        ):
            raise ValueError("Invalid expected attempt identity")
        case = cases.get(attempt["case_id"])
        if case is None or any(attempt[key] != case[key] for key in (
            "family_id", "segment_id", "question",
        )):
            raise ValueError("Attempt does not exactly match dataset")
        identity = _identity(attempt)
        if identity in identities or attempt["provider_response_id"] in provider_ids:
            raise ValueError("Duplicate attempt/provider response identity")
        identities.add(identity)
        provider_ids.add(attempt["provider_response_id"])
        if attempt.get("cost_scope") not in ("response_model_only", "complete_trajectory"):
            raise ValueError("Explicit agent cost scope required")
    # Malformed external records are retained as unresolved evidence, never passes.
    external_errors = []
    groups = {identity: [] for identity in identities}
    item_owners = {}
    duplicates = Counter()
    for record in records:
        if not isinstance(record, dict):
            external_errors.append("malformed_record")
            continue
        allowed = set(IDENTITY_FIELDS) | {
            "evaluation_id", "evaluation_run_id", "output_item_id", "evaluator_id",
            "revision", "model", "dataset_hash", "acceptance_rules_revision",
            "status", "score", "judge_usage",
        }
        if set(record) - allowed:
            external_errors.append("unexpected_record_fields")
        identity = _identity(record)
        if not all(isinstance(value, str) for value in identity) or identity not in groups:
            external_errors.append("unmatched_record_identity")
            continue
        groups[identity].append(record)
        item, grader = record.get("output_item_id"), record.get("evaluator_id")
        if isinstance(item, str) and item and isinstance(grader, str):
            duplicates[(item, grader)] += 1
            item_owners.setdefault(item, set()).add(identity)
    results = []
    source_ids = {source["source_id"] for source in dataset["sources"]}
    digest = dataset_hash(dataset)
    for attempt in attempts:
        identity = _identity(attempt)
        case = cases[attempt["case_id"]]
        reasons = list(external_errors)
        contexts = attempt.get("grounding_context")
        context_ids = set()
        if not isinstance(contexts, list) or not contexts:
            reasons.append("missing_grounding_context")
        else:
            for context in contexts:
                if (not isinstance(context, dict) or not isinstance(context.get("source_id"), str)
                        or context["source_id"] not in source_ids
                        or not isinstance(context.get("text"), str) or not context["text"].strip()
                        or context.get("content_hash") != text_hash(context["text"])):
                    reasons.append("invalid_grounding_context")
                else:
                    context_ids.add(context["source_id"])
            if case["answer_behavior"] == "answer" and not {
                locator["source_id"] for locator in case["source_locators"]
            }.issubset(context_ids):
                reasons.append("missing_required_source_context")
        if not isinstance(attempt.get("response_text"), str) or not attempt["response_text"].strip():
            reasons.append("missing_response")
        if type(attempt.get("abstained")) is not bool:
            reasons.append("missing_abstention_evidence")
        citations = attempt.get("citations")
        citation_failure = False
        if not isinstance(citations, list) or not all(isinstance(c, str) for c in citations):
            reasons.append("missing_citation_evidence")
        else:
            citation_failure = any(c not in context_ids for c in citations)
            if case["answer_behavior"] == "answer" and not citations:
                citation_failure = True
        scores, evidence = {}, []
        found = Counter()
        output_ids = set()
        for record in groups[identity]:
            grader = record.get("evaluator_id")
            if not isinstance(grader, str) or grader not in evaluators:
                reasons.append("unexpected_evaluator")
                continue
            found[grader] += 1
            item = record.get("output_item_id")
            if not isinstance(item, str) or not item.strip():
                reasons.append("missing_output_item_id")
            else:
                output_ids.add(item)
                if duplicates[(item, grader)] != 1 or len(item_owners[item]) != 1:
                    reasons.append("duplicate_output_item")
            expected = {
                "evaluation_id": evaluation_id, "evaluation_run_id": evaluation_run_id,
                "dataset_hash": digest, "acceptance_rules_revision": rules["revision"],
                "revision": evaluators[grader]["revision"], "model": evaluators[grader]["model"],
            }
            if any(record.get(key) != value for key, value in expected.items()):
                reasons.append("evaluator_provenance_mismatch")
            score = record.get("score")
            if record.get("status") != "completed":
                reasons.append("judge_failure")
            if not _number(score) or not 1 <= score <= 5:
                reasons.append("missing_or_invalid_score")
                score = None
            scores[grader] = score
            evidence.append({
                "evaluator_id": grader, "output_item_id": item,
                "revision": record.get("revision"), "model": record.get("model"),
                "status": record.get("status"), "score": score,
                "judge_usage": _usage(record.get("judge_usage")),
            })
        if any(found[name] != 1 for name in EVALUATOR_IDS):
            reasons.append("missing_or_duplicate_evaluator")
        if len(output_ids) != 1:
            reasons.append("inconsistent_output_item_identity")
        behavior_failure = (
            case["answer_behavior"] == "abstain" and attempt.get("abstained") is not True
            or case["answer_behavior"] == "answer" and attempt.get("abstained") is True
        )
        if reasons:
            rubric_outcome = "inconclusive"
        elif (citation_failure or behavior_failure
              or any(scores[name] < rules["minimum_scores"][name] for name in EVALUATOR_IDS)):
            rubric_outcome = "rejected"
        else:
            rubric_outcome = "accepted"
        if citation_failure:
            reasons.append("citation_check_failed")
        if behavior_failure:
            reasons.append("answer_behavior_failed")
        # This schema describes proposed pilot rubrics only, never human acceptance.
        acceptance = "rejected" if rubric_outcome == "rejected" else "inconclusive"
        reasons.append("human_review_pending")
        results.append({
            **{key: attempt[key] for key in IDENTITY_FIELDS if key != "question"},
            "question_hash": text_hash(attempt["question"]),
            "partition": case["partition"], "rubric_outcome": rubric_outcome,
            "acceptance_outcome": acceptance, "raw_scores": scores,
            "expected_answer_behavior": case["answer_behavior"],
            "abstained": attempt.get("abstained") if type(attempt.get("abstained")) is bool else None,
            "citations": list(citations) if isinstance(citations, list)
            and all(isinstance(c, str) for c in citations) else None,
            "reasons": sorted(set(reasons)), "evaluator_evidence": evidence,
            "response_content_hash": text_hash(attempt["response_text"])
            if isinstance(attempt.get("response_text"), str) else None,
            "grounding_content_hashes": [c["content_hash"] for c in
                                        (contexts if isinstance(contexts, list) else [])
                                        if isinstance(c, dict) and isinstance(c.get("content_hash"), str)],
            "grounding_sources": [
                {"source_id": c["source_id"], "content_hash": c["content_hash"]}
                for c in (contexts if isinstance(contexts, list) else [])
                if isinstance(c, dict) and isinstance(c.get("source_id"), str)
                and isinstance(c.get("content_hash"), str)
            ],
            "agent_usage": _usage(attempt.get("agent_usage")),
            "cost_scope": attempt["cost_scope"],
        })
    segments = []
    for segment in sorted({case["segment_id"] for case in dataset["cases"]}):
        segment_cases = [case for case in dataset["cases"] if case["segment_id"] == segment]
        observed = [result for result in results if result["segment_id"] == segment]
        segments.append({
            "segment_id": segment, "expected_case_count": len(segment_cases),
            "observed_case_count": len({item["case_id"] for item in observed}),
            "attempt_count": len(observed),
            "missing_case_ids": sorted({case["case_id"] for case in segment_cases}
                                       - {item["case_id"] for item in observed}),
            "acceptance_counts": {outcome: sum(item["acceptance_outcome"] == outcome
                                              for item in observed)
                                  for outcome in ("accepted", "rejected", "inconclusive")},
            "rubric_counts": {outcome: sum(item["rubric_outcome"] == outcome for item in observed)
                              for outcome in ("accepted", "rejected", "inconclusive")},
        })
    agent_costs = [_usage(attempt.get("agent_usage"))["cost_usd"] for attempt in attempts]
    judge_costs = [_usage(record.get("judge_usage") if isinstance(record, dict) else None)["cost_usd"]
                   for record in records]
    report = {
        "schema_version": "rag-qna-evaluation.v1",
        "evaluation_id": evaluation_id, "evaluation_run_id": evaluation_run_id,
        "dataset_id": dataset["dataset_id"], "dataset_revision": dataset["revision"],
        "dataset_hash": digest, "segment_revision": dataset["segment_revision"],
        "family_revision": dataset["family_revision"],
        "acceptance_rules": rules, "acceptance_rules_hash": content_hash(rules),
        "evaluators": deepcopy(evaluators),
        "evidence_status": "advisory", "human_review_status": "pending",
        "operational_admission": False, "heldout_scored_proof": False,
        "source_authentication": "not_established_by_content_hash",
        "external_errors": sorted(set(external_errors)),
        "attempts": results, "segments": segments,
        "summary": {
            "status": "advisory",
            "quality_status": "human_review_pending",
            "attempt_count": len(results),
            "expected_case_count": len(cases),
            "observed_case_count": len({item["case_id"] for item in results}),
            "missing_case_count": len(cases) - len({item["case_id"] for item in results}),
            "complete_dataset_coverage": len({item["case_id"] for item in results}) == len(cases),
            "acceptance_counts": {
                outcome: sum(item["acceptance_outcome"] == outcome for item in results)
                for outcome in ("accepted", "rejected", "inconclusive")
            },
            "rubric_counts": {
                outcome: sum(item["rubric_outcome"] == outcome for item in results)
                for outcome in ("accepted", "rejected", "inconclusive")
            },
        },
        "judge_usage_evidence": [
            {
                "record_index": index,
                **{key: record.get(key) if isinstance(record, dict)
                   and isinstance(record.get(key), str) else None
                   for key in ("evaluation_id", "evaluation_run_id", "output_item_id",
                               "evaluator_id", "provider_response_id")},
                "usage": _usage(record.get("judge_usage") if isinstance(record, dict) else None),
            }
            for index, record in enumerate(records)
        ],
        "costs": {
            "scope": "partial_agent_and_supplied_judge_records_not_billed_task_total",
            "includes_failed_rejected_inconclusive_attempts": True,
            "agent_attempt_count": len(attempts), "judge_record_count": len(records),
            "agent_total_usd": _total(agent_costs), "judge_total_usd": _total(judge_costs),
            "known_agent_subtotal_usd": _total([c for c in agent_costs if c is not None]),
            "known_judge_subtotal_usd": _total([c for c in judge_costs if c is not None]),
            "agent_unknown_cost_count": sum(c is None for c in agent_costs),
            "judge_unknown_cost_count": sum(c is None for c in judge_costs),
            "cost_per_accepted_task_usd": None,
            "cost_per_accepted_task_reason": "no_human_reviewed_accepted_tasks",
        },
    }
    report["content_hash"] = content_hash(report)
    return report


def validate_evaluation(report, *, dataset=None):
    """Validate content-free advisory evidence; integrity is not authentication.

    Optionally bind to an already validated dataset and exact case identities.
    Returns the supplied report without mutating it; invalid evidence raises
    ValueError. This does not authorize persistence, cloud access or promotion.
    """
    if (not isinstance(report, dict) or report.get("schema_version") != "rag-qna-evaluation.v1"
            or report.get("content_hash") != content_hash(report)):
        raise ValueError("Invalid Q&A evaluation schema or content hash")
    if (report.get("evidence_status") != "advisory"
            or report.get("human_review_status") != "pending"
            or report.get("operational_admission") is not False
            or report.get("heldout_scored_proof") is not False):
        raise ValueError("Pilot evaluation cannot claim reviewed acceptance or promotion")
    forbidden = {"question", "response_text", "grounding_context", "rationale", "explanation"}

    def check_content(value):
        if isinstance(value, dict):
            if forbidden.intersection(value):
                raise ValueError("Raw content is forbidden in persistent Q&A evidence")
            for child in value.values():
                check_content(child)
        elif isinstance(value, list):
            for child in value:
                check_content(child)

    check_content(report)
    attempts, segments, summary = report.get("attempts"), report.get("segments"), report.get("summary")
    if (not isinstance(attempts, list) or not isinstance(segments, list)
            or not isinstance(summary, dict) or summary.get("status") != "advisory"
            or summary.get("quality_status") != "human_review_pending"):
        raise ValueError("Invalid Q&A summary")
    cases = None
    if dataset is not None:
        validate_dataset(dataset)
        if report.get("dataset_hash") != dataset_hash(dataset):
            raise ValueError("Evaluation dataset hash mismatch")
        cases = {case["case_id"]: case for case in dataset["cases"]}
    identities, response_ids = set(), set()
    for attempt in attempts:
        if not isinstance(attempt, dict) or not all(
            isinstance(attempt.get(key), str) and attempt[key].strip()
            for key in (*[key for key in IDENTITY_FIELDS if key != "question"], "question_hash")
        ):
            raise ValueError("Invalid persisted attempt identity")
        identity = tuple(attempt[key] for key in IDENTITY_FIELDS if key != "question")
        if identity in identities or attempt["provider_response_id"] in response_ids:
            raise ValueError("Duplicate persisted attempt")
        identities.add(identity)
        response_ids.add(attempt["provider_response_id"])
        rubric = attempt.get("rubric_outcome")
        if (rubric not in ("accepted", "rejected", "inconclusive")
                or attempt.get("acceptance_outcome") !=
                ("rejected" if rubric == "rejected" else "inconclusive")
                or "human_review_pending" not in attempt.get("reasons", [])):
            raise ValueError("Invalid pilot acceptance outcome")
        if cases is not None:
            case = cases.get(attempt["case_id"])
            if (case is None or any(attempt[key] != case[key]
                                    for key in ("family_id", "segment_id", "partition"))
                    or attempt["question_hash"] != text_hash(case["question"])):
                raise ValueError("Persisted attempt does not match dataset")
    observed = len({attempt["case_id"] for attempt in attempts})
    expected = len(cases) if cases is not None else summary.get("expected_case_count")
    if type(expected) is not int or expected < observed:
        raise ValueError("Invalid expected case count")
    required_summary = {
        "attempt_count": len(attempts), "expected_case_count": expected,
        "observed_case_count": observed, "missing_case_count": expected - observed,
        "complete_dataset_coverage": observed == expected,
    }
    if any(summary.get(key) != value for key, value in required_summary.items()):
        raise ValueError("Incorrect Q&A summary counts")
    for name, field in (("acceptance_counts", "acceptance_outcome"), ("rubric_counts", "rubric_outcome")):
        counts = {outcome: sum(item[field] == outcome for item in attempts)
                  for outcome in ("accepted", "rejected", "inconclusive")}
        if summary.get(name) != counts:
            raise ValueError("Incorrect Q&A outcome counts")
    segment_ids = set()
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("segment_id"), str):
            raise ValueError("Invalid segment evidence")
        segment_id = segment["segment_id"]
        if segment_id in segment_ids:
            raise ValueError("Duplicate segment evidence")
        segment_ids.add(segment_id)
        items = [item for item in attempts if item["segment_id"] == segment_id]
        if (segment.get("attempt_count") != len(items)
                or segment.get("observed_case_count") != len({item["case_id"] for item in items})):
            raise ValueError("Incorrect segment counts")
        for name, field in (("acceptance_counts", "acceptance_outcome"), ("rubric_counts", "rubric_outcome")):
            if segment.get(name) != {outcome: sum(item[field] == outcome for item in items)
                                     for outcome in ("accepted", "rejected", "inconclusive")}:
                raise ValueError("Incorrect segment outcome counts")
        if cases is not None:
            expected_ids = {case["case_id"] for case in cases.values() if case["segment_id"] == segment_id}
            if (segment.get("expected_case_count") != len(expected_ids)
                    or segment.get("missing_case_ids") != sorted(expected_ids - {item["case_id"] for item in items})):
                raise ValueError("Incorrect segment coverage")
    expected_segments = {case["segment_id"] for case in cases.values()} if cases is not None else set(
        ("factual", "synthesis", "cross-book", "ambiguous", "unanswerable")
    )
    if segment_ids != expected_segments:
        raise ValueError("Missing or unexpected quality segment")
    costs = report.get("costs")
    if (not isinstance(costs, dict) or costs.get("cost_per_accepted_task_usd") is not None
            or costs.get("cost_per_accepted_task_reason") != "no_human_reviewed_accepted_tasks"):
        raise ValueError("Pilot cannot claim cost per accepted task")
    evaluators = report.get("evaluators")
    if not isinstance(evaluators, dict) or set(evaluators) != set(EVALUATOR_IDS):
        raise ValueError("Invalid evaluator provenance")
    for attempt in attempts:
        scores = attempt.get("raw_scores")
        if (not isinstance(scores, dict) or set(scores) != set(EVALUATOR_IDS)
                or any(value is not None and (not _number(value) or not 1 <= value <= 5)
                       for value in scores.values())):
            raise ValueError("Invalid persisted evaluator scores")
    judge_records = report.get("judge_usage_evidence")
    if not isinstance(judge_records, list):
        raise ValueError("Missing judge usage coverage")
    for row in [*attempts, *judge_records]:
        usage = row.get("agent_usage") if "agent_usage" in row else row.get("usage")
        if not isinstance(usage, dict) or usage != _usage(usage):
            raise ValueError("Invalid persisted usage")
    agent_costs = [row["agent_usage"]["cost_usd"] for row in attempts]
    judge_costs = [row["usage"]["cost_usd"] for row in judge_records]
    if (costs.get("agent_total_usd") != _total(agent_costs)
            or costs.get("judge_total_usd") != _total(judge_costs)
            or costs.get("agent_attempt_count") != len(attempts)
            or costs.get("judge_record_count") != len(judge_records)):
        raise ValueError("Persisted cost totals differ from their evidence")
    return report
