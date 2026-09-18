"""Resumable, policy-bound repetition campaigns for the Q&A reference workload.

This is workload-specific control-plane orchestration. It advances
execute -> evaluate -> reconcile while preserving each batch and Foundry
evaluation as independently immutable evidence.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from costgov.policy_store import measurement_authorization
from costgov.studio_lifecycle import digest
from rag import agent_batch
from rag.qna_dataset import validate_dataset
from rag.qna_evaluation import validate_evaluation

SCHEMA_VERSION = "rag-qna-campaign.v1"
_CAMPAIGN_ID = re.compile(r"campaign-[a-f0-9]{32}")


def _canonical_uuid(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value.lower():
            raise ValueError
    except (AttributeError, ValueError):
        raise ValueError("campaign request_id must be a canonical UUID") from None
    return value.lower()


def _campaign_id(value):
    if not isinstance(value, str) or not _CAMPAIGN_ID.fullmatch(value):
        raise ValueError("Invalid campaign identity")
    return value


def _root(service, campaign_id):
    return service.run_root / "rag_campaigns" / _campaign_id(campaign_id)


def _sealed(value):
    body = {key: item for key, item in value.items() if key != "content_hash"}
    return {**body, "content_hash": digest(body)}


def validate_campaign(value):
    if (not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION
            or value.get("content_hash") != digest({
                key: item for key, item in value.items() if key != "content_hash"
            })):
        raise ValueError("Invalid campaign schema or content hash")
    required = {
        "schema_version", "campaign_id", "request_id", "created_at", "actor",
        "report_id", "plan_id", "receipt_id", "receipt_hash", "dataset_id",
        "dataset_revision", "dataset_hash", "case_ids", "repetitions",
        "target_attempts", "evaluation_runs", "evaluation_rows_per_run",
        "policy", "measurement_authorization", "agent", "cost_scope",
        "operational_promotion", "content_hash",
    }
    if set(value) != required:
        raise ValueError("Campaign fields invalid")
    _campaign_id(value["campaign_id"])
    _canonical_uuid(value["request_id"])
    if not all(isinstance(value[key], str) and value[key].strip() for key in (
        "created_at", "actor", "report_id", "plan_id", "receipt_id",
        "dataset_id", "dataset_revision",
    )):
        raise ValueError("Campaign identities are required")
    if datetime.fromisoformat(value["created_at"]).utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError("Campaign creation time must be UTC")
    if not all(isinstance(value[key], str) and re.fullmatch(r"[a-f0-9]{64}", value[key])
               for key in ("receipt_hash", "dataset_hash")):
        raise ValueError("Campaign evidence hashes are invalid")
    case_ids = value["case_ids"]
    if (not isinstance(case_ids, list) or not 1 <= len(case_ids) <= 25
            or len(set(case_ids)) != len(case_ids)
            or any(not isinstance(item, str) or not item for item in case_ids)):
        raise ValueError("Campaign requires 1-25 unique case identities")
    for key in ("repetitions", "target_attempts", "evaluation_runs", "evaluation_rows_per_run"):
        if type(value[key]) is not int or value[key] < 1:
            raise ValueError("Campaign counts must be positive integers")
    if (value["target_attempts"] != len(case_ids) * value["repetitions"]
            or value["evaluation_runs"] != value["repetitions"]
            or value["evaluation_rows_per_run"] != len(case_ids)):
        raise ValueError("Campaign counts do not match cases and repetitions")
    authorization = measurement_authorization_projection(value["measurement_authorization"])
    if (value["repetitions"] > authorization["max_campaign_repetitions"]
            or value["target_attempts"] > authorization["max_campaign_questions"]
            or value["evaluation_runs"] > authorization["max_evaluation_runs"]
            or value["evaluation_rows_per_run"] > authorization["max_evaluation_rows_per_run"]
            or len(case_ids) > authorization["max_questions"]):
        raise ValueError("Campaign exceeds its measurement authorization")
    policy = value["policy"]
    if (not isinstance(policy, dict)
            or set(policy) != {"policy_id", "version", "content_hash", "source",
                               "etag", "label", "endpoint", "key"}
            or policy["source"] != "azure_app_configuration"
            or not all(isinstance(policy[key], str) and policy[key].strip() for key in policy)):
        raise ValueError("Campaign policy provenance is invalid")
    if not isinstance(value["agent"], dict) or not value["agent"]:
        raise ValueError("Campaign requires a pinned deployed-agent projection")
    if value["cost_scope"] != "response_model_only_not_task_total":
        raise ValueError("Campaign cost scope must remain explicitly incomplete")
    if value["operational_promotion"] is not False:
        raise ValueError("Measurement campaigns cannot grant operational promotion")
    return value


def measurement_authorization_projection(value):
    from costgov.policy_store import validate_measurement_policy

    authorization = validate_measurement_policy(value)
    if authorization["schema_version"] != "workload-measurement-policy.v2":
        raise ValueError("Campaigns require workload-measurement-policy.v2")
    return authorization


def create_campaign(
    service, plan_id, dataset, case_ids, *, repetitions, request_id,
    loaded, actor, agent,
):
    """Create one immutable campaign and deterministic repetition requests."""
    validate_dataset(dataset)
    receipt = service.receipt(plan_id)
    policy = agent_batch._policy_binding(receipt, loaded)
    authorization = measurement_authorization(loaded)
    measurement_authorization_projection(authorization)
    request_id = _canonical_uuid(request_id)
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("Campaign creation requires an authorized actor")
    available = {case["case_id"]: case for case in dataset["cases"]}
    if (not isinstance(case_ids, list) or any(item not in available for item in case_ids)
            or len(set(case_ids)) != len(case_ids)):
        raise ValueError("Campaign cases must exactly match the versioned dataset")
    campaign_claim = service.run_root / "rag_campaign_claims" / f"{request_id}.json"
    if campaign_claim.exists():
        claim = json.loads(campaign_claim.read_text(encoding="utf-8"))
        return load_campaign(service, claim["campaign_id"])
    campaign_id = "campaign-" + uuid4().hex
    value = _sealed({
        "schema_version": SCHEMA_VERSION, "campaign_id": campaign_id,
        "request_id": request_id, "created_at": datetime.now(timezone.utc).isoformat(),
        "actor": actor, "report_id": receipt["report_id"], "plan_id": receipt["plan_id"],
        "receipt_id": receipt["receipt_id"], "receipt_hash": receipt["content_hash"],
        "dataset_id": dataset["dataset_id"], "dataset_revision": dataset["revision"],
        "dataset_hash": dataset["content_hash"], "case_ids": list(case_ids),
        "repetitions": repetitions, "target_attempts": len(case_ids) * repetitions,
        "evaluation_runs": repetitions, "evaluation_rows_per_run": len(case_ids),
        "policy": policy, "measurement_authorization": authorization,
        "agent": json.loads(json.dumps(agent, allow_nan=False)),
        "cost_scope": "response_model_only_not_task_total",
        "operational_promotion": False,
    })
    validate_campaign(value)
    agent_batch._write_once(_root(service, campaign_id) / "manifest.json", value)
    try:
        agent_batch._write_once(campaign_claim, {
            "schema_version": "rag-qna-campaign-claim.v1",
            "request_id": request_id, "campaign_id": campaign_id,
            "manifest_hash": value["content_hash"],
        })
    except FileExistsError:
        existing = json.loads(campaign_claim.read_text(encoding="utf-8"))
        if existing.get("campaign_id") != campaign_id:
            raise ValueError("Campaign request identity is already reserved") from None
    return value


def load_campaign(service, campaign_id):
    path = _root(service, campaign_id) / "manifest.json"
    try:
        return validate_campaign(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Campaign manifest is unavailable") from exc


def validate_campaign_dispatch(campaign, receipt, dataset, case_ids, loaded, agent):
    validate_campaign(campaign)
    validate_dataset(dataset)
    if (campaign["plan_id"] != receipt["plan_id"]
            or campaign["receipt_id"] != receipt["receipt_id"]
            or campaign["receipt_hash"] != receipt["content_hash"]
            or campaign["dataset_hash"] != dataset["content_hash"]
            or campaign["case_ids"] != list(case_ids)
            or campaign["policy"] != agent_batch._policy_binding(receipt, loaded)
            or campaign["measurement_authorization"] != measurement_authorization(loaded)
            or campaign["agent"] != agent):
        raise ValueError("Campaign dispatch evidence changed")
    available = {case["case_id"]: case for case in dataset["cases"]}
    return [available[case_id] for case_id in case_ids]


def _repetition_paths(service, campaign_id, repetition):
    directory = _root(service, campaign_id) / "repetitions" / f"{repetition:03d}"
    return directory / "claim.json", directory / "result.json", directory / "evaluation.json"


def _read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Campaign repetition evidence is unavailable") from exc
    if not isinstance(value, dict):
        raise ValueError("Campaign repetition evidence is invalid")
    return value


def campaign_status(service, campaign_id):
    campaign = load_campaign(service, campaign_id)
    rows, observed = [], 0.0
    unknown_cost = False
    stop_reason = None
    for repetition in range(1, campaign["repetitions"] + 1):
        claim_path, result_path, evaluation_path = _repetition_paths(
            service, campaign_id, repetition,
        )
        if not claim_path.exists():
            continue
        claim = _read_json(claim_path)
        if (claim.get("schema_version") != "rag-qna-campaign-repetition-claim.v1"
                or claim.get("campaign_id") != campaign_id
                or claim.get("campaign_hash") != campaign["content_hash"]
                or claim.get("repetition") != repetition):
            raise ValueError("Campaign repetition claim is invalid")
        if not result_path.exists():
            batch_claim = service.run_root / "rag_batch_claims" / f"{claim['request_id']}.json"
            if batch_claim.exists():
                rows.append({**claim, "status": "in_doubt"})
                stop_reason = "repetition_in_doubt"
                break
            rows.append({**claim, "status": "preflight_retryable"})
            continue
        result = _read_json(result_path)
        if (result.get("schema_version") != "rag-qna-campaign-repetition.v1"
                or result.get("campaign_id") != campaign_id
                or result.get("campaign_hash") != campaign["content_hash"]
                or result.get("repetition") != repetition
                or result.get("content_hash") != digest({
                    key: item for key, item in result.items() if key != "content_hash"
                })):
            raise ValueError("Campaign repetition result is invalid")
        amount = result["observed_model_allocation_usd"]
        if amount is None:
            unknown_cost = True
        else:
            if (isinstance(amount, bool) or not isinstance(amount, (int, float))
                    or not math.isfinite(amount) or amount < 0):
                raise ValueError("Campaign observed allocation is invalid")
            observed += amount
        if result["execution_status"] == "completed" and result["quality_recorded"] is not True:
            if not evaluation_path.exists():
                rows.append({**result, "status": "evaluation_pending"})
                stop_reason = "evaluation_pending"
                break
            evaluation = _read_json(evaluation_path)
            if (evaluation.get("schema_version") != "rag-qna-campaign-evaluation.v1"
                    or evaluation.get("campaign_id") != campaign_id
                    or evaluation.get("campaign_hash") != campaign["content_hash"]
                    or evaluation.get("repetition") != repetition
                    or evaluation.get("run_id") != result["run_id"]
                    or evaluation.get("quality_recorded") is not True
                    or evaluation.get("content_hash") != digest({
                        key: item for key, item in evaluation.items() if key != "content_hash"
                    })):
                raise ValueError("Campaign evaluation completion is invalid")
            result = {
                **result, "quality_recorded": True,
                "evaluation_id": evaluation["evaluation_id"],
                "evaluation_run_id": evaluation["evaluation_run_id"],
                "record_id": evaluation["record_id"], "status": "completed",
            }
        rows.append(result)
        if result["execution_status"] != "completed":
            stop_reason = "repetition_incomplete"
            break
        if result["quality_recorded"] is not True:
            stop_reason = "evaluation_incomplete"
            break
    completed = sum(row.get("status") == "completed" for row in rows)
    if stop_reason is None and unknown_cost:
        stop_reason = "campaign_cost_unknown"
    if (stop_reason is None
            and observed >= campaign["measurement_authorization"]["campaign_observed_model_cost_stop_usd"]
            and completed < campaign["repetitions"]):
        stop_reason = "campaign_observed_model_cost_stop"
    if stop_reason is None and completed == campaign["repetitions"]:
        stop_reason = "completed"
    return {
        "schema_version": "rag-qna-campaign-status.v1",
        "campaign_id": campaign_id, "campaign_hash": campaign["content_hash"],
        "status": ("completed" if stop_reason == "completed" else
                   "paused" if stop_reason == "evaluation_pending" else
                   "stopped" if stop_reason else "ready"),
        "stop_reason": stop_reason, "completed_repetitions": completed,
        "target_repetitions": campaign["repetitions"],
        "completed_attempts": completed * len(campaign["case_ids"]),
        "target_attempts": campaign["target_attempts"],
        "unique_case_count": len(campaign["case_ids"]),
        "observed_model_allocation_usd": observed if not unknown_cost else None,
        "cost_scope": campaign["cost_scope"], "repetitions": rows,
        "operational_promotion": False,
    }


def execute_next_repetition(
    service, campaign_id, dataset, *, loaded, actor, runner, agent,
):
    """Claim and execute one repetition. A missing result after claim is terminal."""
    campaign = load_campaign(service, campaign_id)
    status = campaign_status(service, campaign_id)
    if status["stop_reason"] is not None:
        return status
    if (campaign["policy"] != agent_batch._policy_binding(service.receipt(campaign["plan_id"]), loaded)
            or campaign["measurement_authorization"] != measurement_authorization(loaded)
            or campaign["agent"] != agent):
        raise ValueError("Campaign authority or deployed agent changed")
    repetition = status["completed_repetitions"] + 1
    request_id = str(UUID(bytes=bytes.fromhex(digest({
        "campaign_hash": campaign["content_hash"], "repetition": repetition,
    })[:32])))
    claim_path, result_path, _ = _repetition_paths(service, campaign_id, repetition)
    if claim_path.exists():
        claim = _read_json(claim_path)
        if (claim.get("schema_version") != "rag-qna-campaign-repetition-claim.v1"
                or claim.get("campaign_id") != campaign_id
                or claim.get("campaign_hash") != campaign["content_hash"]
                or claim.get("repetition") != repetition
                or claim.get("request_id") != request_id):
            raise ValueError("Campaign repetition claim is invalid")
    else:
        claim = {
            "schema_version": "rag-qna-campaign-repetition-claim.v1",
            "campaign_id": campaign_id, "campaign_hash": campaign["content_hash"],
            "repetition": repetition, "request_id": request_id,
            "claimed_at": datetime.now(timezone.utc).isoformat(),
        }
        agent_batch._write_once(claim_path, claim)
    outcome = runner(campaign=campaign, request_id=request_id)
    batch = outcome.get("batch")
    if not isinstance(batch, dict):
        raise ValueError("Campaign runner must return immutable batch evidence")
    result = _sealed({
        "schema_version": "rag-qna-campaign-repetition.v1",
        "campaign_id": campaign_id, "campaign_hash": campaign["content_hash"],
        "repetition": repetition, "request_id": request_id,
        "run_id": batch["run_id"], "batch_hash": batch["evidence"]["content_hash"],
        "execution_status": batch["execution_status"],
        "stop_reason": batch["stop_reason"],
        "observed_model_allocation_usd": batch["observed_model_allocation_usd"],
        "quality_recorded": outcome.get("quality_recorded") is True,
        "evaluation_id": outcome.get("evaluation_id"),
        "evaluation_run_id": outcome.get("evaluation_run_id"),
        "record_id": outcome.get("record_id"),
        "status": ("completed" if batch["execution_status"] == "completed"
                   and outcome.get("quality_recorded") is True else "incomplete"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    agent_batch._write_once(result_path, result)
    return campaign_status(service, campaign_id)


def resume_pending_evaluation(service, campaign_id, *, resumer):
    """Resume the one submitted evaluation that blocks further repetitions."""
    if not callable(resumer):
        raise ValueError("Campaign evaluation resumer must be callable")
    campaign = load_campaign(service, campaign_id)
    status = campaign_status(service, campaign_id)
    if status["stop_reason"] != "evaluation_pending":
        return status
    pending = status["repetitions"][-1]
    repetition = pending["repetition"]
    _, _, evaluation_path = _repetition_paths(service, campaign_id, repetition)
    outcome = resumer(run_id=pending["run_id"])
    if outcome.get("quality_recorded") is not True:
        return campaign_status(service, campaign_id)
    completion = _sealed({
        "schema_version": "rag-qna-campaign-evaluation.v1",
        "campaign_id": campaign_id, "campaign_hash": campaign["content_hash"],
        "repetition": repetition, "run_id": pending["run_id"],
        "quality_recorded": True,
        "evaluation_id": outcome.get("evaluation_id"),
        "evaluation_run_id": outcome.get("evaluation_run_id"),
        "record_id": outcome.get("record_id"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    agent_batch._write_once(evaluation_path, completion)
    return campaign_status(service, campaign_id)


def campaign_quality_summary(service, plan_id, campaign_id):
    campaign = load_campaign(service, campaign_id)
    if campaign["plan_id"] != plan_id:
        raise ValueError("Campaign belongs to a different forecast")
    runs = {
        row["run_id"]: row for row in campaign_status(service, campaign_id)["repetitions"]
        if row.get("status") == "completed"
    }
    records = [
        row for row in service.workspace(plan_id)["records"]
        if row["kind"] == "qna-evaluation" and row.get("run_id") in runs
    ]
    latest = {}
    for record in records:
        validate_evaluation(record["review"])
        run_id = record["run_id"]
        if record.get("batch_hash") != runs[run_id]["batch_hash"]:
            raise ValueError("Campaign quality evidence batch binding mismatch")
        if run_id not in latest or (record["created_at"], record["id"]) > (
                latest[run_id]["created_at"], latest[run_id]["id"]):
            latest[run_id] = record
    attempts = [
        attempt for record in latest.values() for attempt in record["review"]["attempts"]
    ]
    if any(attempt["run_id"] not in runs for attempt in attempts):
        raise ValueError("Campaign quality attempt belongs to another run")
    segments = []
    case_segments = {
        case["case_id"]: case["segment_id"] for case in dataset_cases(dataset=None, campaign=campaign)
    }
    for segment_id in sorted(set(case_segments.values())):
        observed = [attempt for attempt in attempts if attempt["segment_id"] == segment_id]
        segments.append({
            "segment_id": segment_id,
            "unique_case_count": len({attempt["case_id"] for attempt in observed}),
            "attempt_count": len(observed),
            "rubric_counts": {
                outcome: sum(item["rubric_outcome"] == outcome for item in observed)
                for outcome in ("accepted", "rejected", "inconclusive")
            },
            "acceptance_counts": {
                outcome: sum(item["acceptance_outcome"] == outcome for item in observed)
                for outcome in ("accepted", "rejected", "inconclusive")
            },
        })
    return {
        "schema_version": "rag-qna-campaign-quality.v1",
        "campaign_id": campaign_id, "campaign_hash": campaign["content_hash"],
        "status": ("advisory_evaluation" if latest else "not_evaluated"),
        "evaluated_runs": len(latest), "expected_evaluation_runs": campaign["evaluation_runs"],
        "unique_case_count": len({attempt["case_id"] for attempt in attempts}),
        "expected_unique_case_count": len(campaign["case_ids"]),
        "attempt_count": len(attempts), "expected_attempt_count": campaign["target_attempts"],
        "segments": segments, "human_review_status": "pending",
        "operational_promotion": False,
    }


def dataset_cases(*, dataset, campaign):
    if dataset is None:
        from rag.qna_dataset import load_dataset

        dataset = load_dataset()
    validate_dataset(dataset)
    if dataset["content_hash"] != campaign["dataset_hash"]:
        raise ValueError("Campaign dataset is unavailable or changed")
    available = {case["case_id"]: case for case in dataset["cases"]}
    return [available[case_id] for case_id in campaign["case_ids"]]
