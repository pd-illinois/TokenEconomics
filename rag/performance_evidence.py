"""Read-only local RAG evidence adapter for the reusable advisory review."""

from __future__ import annotations

import json

from costgov.performance_review import (
    build_review, content_hash, identity, instant, mapping, number, policy_projection,
)
from costgov.studio_lifecycle import canonical, identifier
from rag.agent_batch import publication_status, validate_result


SUPPORTED = {"rag-agent-batch.v1", "rag-agent-batch.v2"}


def _publication_binding(record, result):
    # An initial pending marker precedes sealing and has no evidence hash.
    # Only a hash-bound record can assert that the sealed result was uploaded.
    return record.get("run_id") == result["run_id"] and (
        record.get("content_hash") == result["evidence"]["content_hash"]
        or record.get("status") == "pending" and record.get("content_hash") is None
    )


def _read_batch(service, receipt, run_id):
    identifier(run_id)
    row = service.registry.get(run_id)
    if not isinstance(row, dict) or not isinstance(row.get("result"), dict):
        raise KeyError("Saved batch evidence not found")
    registered = row["result"]
    if registered.get("schema_version") not in SUPPORTED:
        raise ValueError("No supported batch evidence for selected run")
    try:
        directory = service.run_root / run_id
        path = directory / "result.json"
        if not path.resolve().is_relative_to(service.run_root.resolve()):
            raise ValueError("Batch evidence path is outside the run store")
        saved = json.loads(path.read_text(encoding="utf-8"))
        validate_result(saved)
        if canonical(saved) != canonical(registered):
            raise ValueError("Batch registry and disk evidence differ")
        if (saved.get("run_id") != run_id or saved.get("plan_id") != receipt.get("plan_id")
                or saved.get("report_id") != receipt.get("report_id")
                or saved.get("prediction") != {
                    "receipt_id": receipt.get("receipt_id"), "content_hash": receipt.get("content_hash"),
                }):
            raise ValueError("Batch report, plan or receipt binding mismatch")
        for field in ("run_id", "report_id", "plan_id"):
            if field in row and row[field] != saved[field]:
                raise ValueError("Registry identity does not match sealed batch")
        evidence = mapping(saved.get("evidence"))
        if evidence.get("content_hash") != content_hash({k: v for k, v in saved.items() if k != "evidence"}):
            raise ValueError("A sealed batch evidence hash is required")
        prediction = mapping(receipt.get("prediction"))
        model = prediction.get("model")
        blocked_mismatch = saved["execution_status"] == "blocked" and any(
            item["code"] == "agent_model_mismatch" for item in saved["blockers"]
        )
        if (model is None or saved["agent"]["model"] != model) and not blocked_mismatch:
            raise ValueError("Batch model does not match forecast")
        if prediction.get("provider") not in (None, "azure_openai"):
            raise ValueError("Batch provider does not match forecast")
        pricing = saved.get("pricing")
        if pricing is not None and (
                pricing["model"] != saved["agent"]["model"]
                or pricing["model_version"] != saved["agent"]["model_version"]
                or prediction.get("pricing_version") != pricing["revision"]):
            raise ValueError("Batch model or pricing revision binding mismatch")
        if policy_projection(saved["policy"]) is None:
            raise ValueError("Batch policy provenance is invalid")
        return saved
    except (OSError, TypeError, AttributeError, OverflowError, KeyError) as exc:
        raise ValueError("Saved batch evidence is incomplete or invalid") from exc


def _publication(service, result):
    """A local publication record proves upload state only, never execution."""
    if result["schema_version"] == "rag-agent-batch.v1":
        return {"status": "not_published_no_executed_usage", "run_id": result["run_id"],
                "content_hash": result["evidence"]["content_hash"], "created_at": None}
    directory = service.run_root / result["run_id"] / "publication"
    if not directory.exists():
        return {"status": "pending", "run_id": result["run_id"],
                "content_hash": None, "created_at": None}
    try:
        if not directory.resolve().is_relative_to(service.run_root.resolve()):
            raise ValueError("Publication path outside run store")
        paths = list(directory.glob("*.json"))
        if not paths:
            return {"status": "pending", "run_id": result["run_id"],
                    "content_hash": None, "created_at": None}
        for path in paths:
            if not path.resolve().is_relative_to(service.run_root.resolve()):
                raise ValueError("Publication path outside run store")
            record = json.loads(path.read_text(encoding="utf-8"))
            if not _publication_binding(record, result):
                raise ValueError("Publication evidence does not bind this run")
        status = publication_status(service, result["run_id"])
        if not _publication_binding(status, result):
            raise ValueError("Publication evidence changed while reading")
        return {key: status[key] for key in ("status", "run_id", "content_hash", "created_at")}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {"status": "invalid", "run_id": result["run_id"],
                "content_hash": None, "created_at": None}


def _observation(service, result):
    metrics = [row for row in result["metrics"] if row["status"] != "not_dispatched"]
    allocations = [row for metric, row in zip(result["metrics"], result.get("allocations", []))
                   if metric["status"] != "not_dispatched"]

    def values(field):
        return [None if "invalid_provider_usage" in row["coverage_notes"] and field in {"input_tokens", "output_tokens"}
                else number(row.get(field)) for row in metrics]

    def total(field):
        found = values(field)
        return sum(found) if found and all(value is not None for value in found) else None

    def mean(field):
        value = total(field)
        return value / len(metrics) if value is not None else None

    allocation_values = [number(row["model_allocation_usd"]) for row in allocations]
    known = [value for value in allocation_values if value is not None]
    complete_allocation = bool(metrics) and len(known) == len(metrics)
    allocated = sum(known) if complete_allocation else None
    usage_status = "unavailable" if not metrics else "complete" if all(
        total(field) is not None for field in ("input_tokens", "output_tokens")
    ) else "partial"
    publication = _publication(service, result)
    pricing = mapping(result.get("pricing"))
    authorization = mapping(result.get("measurement_authorization"))
    return {
        "schema_version": "performance-observation.v1",
        "source_schema": result["schema_version"], "run_id": result["run_id"],
        "evidence_hash": result["evidence"]["content_hash"],
        "started_at": instant(result.get("started_at")).isoformat() if instant(result.get("started_at")) else None,
        "ended_at": instant(result.get("ended_at")).isoformat() if instant(result.get("ended_at")) else None,
        "execution_status": result["execution_status"], "questions_count": result["questions_count"],
        "questions_completed": sum(row["status"] == "completed" for row in metrics),
        "input_tokens": total("input_tokens"), "output_tokens": total("output_tokens"),
        "input_tokens_mean": mean("input_tokens"), "output_tokens_mean": mean("output_tokens"),
        "latency_mean_ms": mean("latency_ms"), "usage_status": usage_status,
        "observed_model_allocation_usd": allocated,
        "model_allocation_mean_usd": allocated / len(metrics) if allocated is not None else None,
        "known_model_allocation_subtotal_usd": sum(known) if known else None,
        "allocation_status": "complete_response_coverage" if complete_allocation else "partial" if known else "unavailable",
        "cloud_status": publication["status"], "publication": publication,
        "measurement": {
            "schema_version": identity(authorization.get("schema_version")),
            "expires_at": instant(authorization.get("expires_at")).isoformat() if instant(authorization.get("expires_at")) else None,
            "max_output_tokens": number(authorization.get("max_output_tokens")),
            "max_questions": number(authorization.get("max_questions")),
            "max_elapsed_seconds": number(authorization.get("max_elapsed_seconds")),
            "observed_model_cost_stop_usd": number(authorization.get("observed_model_cost_stop_usd")),
            "hard_spend_cap_guaranteed": False, "operational_promotion": False,
        } if authorization else None,
        "pricing": {
            **{key: identity(pricing.get(key)) for key in (
                "schema_version", "revision", "catalog_schema", "catalog_hash", "model", "model_version", "provider",
            )},
            "rates_per_million": {key: number(mapping(pricing.get("rates_per_million")).get(key))
                                  for key in ("input", "cached_input", "cache_write", "output")},
            "price_basis": "catalog_public_list_allocation_not_invoice",
        } if pricing else None,
    }


def build_performance_review(service, plan_id, run_id=None, current_policy=None, policy_error=None, now=None):
    """Read and verify one local sealed batch; no dispatch, network or storage writes.

    Explicit invalid selections fail. Default selection prefers the newest valid
    completed batch for the exact receipt, then the newest supported attempt.
    Invalid related evidence is disclosed and excluded, never aggregated as zero.
    """
    receipt = service.receipt(plan_id)
    if receipt.get("plan_id") != plan_id:
        raise ValueError("Receipt plan binding mismatch")
    selected = _read_batch(service, receipt, run_id) if run_id is not None else None
    valid = []
    excluded, unsupported = 0, 0
    for candidate_id, row in list(service.registry.items()):
        record = mapping(row)
        result = mapping(record.get("result"))
        if record.get("plan_id") != plan_id and result.get("plan_id") != plan_id:
            continue
        if result.get("schema_version") not in SUPPORTED:
            unsupported += 1
            continue
        try:
            candidate = selected if candidate_id == run_id else _read_batch(service, receipt, candidate_id)
            valid.append(candidate)
        except (KeyError, ValueError):
            excluded += 1
    valid.sort(key=lambda value: (instant(value.get("started_at")).timestamp() if instant(value.get("started_at"))
                                 else float("-inf"), value["run_id"]), reverse=True)
    if selected is None:
        completed = [value for value in valid if value["execution_status"] == "completed"]
        selected = next(iter(completed or valid), None)
    options = [{
        "run_id": value["run_id"],
        "started_at": instant(value.get("started_at")).isoformat() if instant(value.get("started_at")) else None,
        "execution_status": value["execution_status"], "questions_count": value["questions_count"],
    } for value in valid]
    review = build_review(
        receipt, observation=_observation(service, selected) if selected else None, run_options=options,
        historical_policy=selected["policy"] if selected else None,
        current_policy=current_policy, policy_error=policy_error, now=now,
        excluded_run_count=excluded, unsupported_run_count=unsupported,
        tool_applicability="managed_retrieval_not_generic_file_search",
    )
    if selected is None:
        review["quality"] = {"status": "not_evaluated"}
        return review

    from rag.qna_review import quality_summary
    from rag.qna_acceptance import acceptance_review

    quality = quality_summary(service, plan_id, selected["run_id"])
    review["quality"] = quality
    if quality["status"] == "not_evaluated":
        return review

    if "acceptance_status" in quality:
        acceptance = {
            "status": quality["acceptance_status"],
            **{key: quality.get(key) for key in (
                "reviewed_cases", "pending_cases", "accepted", "rejected", "inconclusive",
                "acceptance_rate", "segments", "candidates", "report_url",
            )},
        }
    else:
        try:
            acceptance = acceptance_review(service, plan_id, selected["run_id"])
        except KeyError:
            acceptance = {
                "status": "human_review_pending", "reviewed_cases": 0,
                "pending_cases": quality.get("evaluated_cases", 0), "accepted": 0,
                "rejected": 0, "inconclusive": 0, "acceptance_rate": None,
                "evaluated_cases": quality.get("evaluated_cases", 0),
                "segments": quality.get("segments", []), "candidates": [],
                "report_url": quality.get("report_url"),
            }
    quality.update({
        key: acceptance[key] for key in (
            "reviewed_cases", "pending_cases", "accepted", "rejected", "inconclusive",
            "acceptance_rate", "segments", "candidates", "report_url",
        )
    })
    quality["acceptance_status"] = acceptance["status"]
    review["summary"]["quality_status"] = acceptance["status"]
    review["summary"]["accepted_tasks"] = (
        acceptance["accepted"] if acceptance["reviewed_cases"] else None
    )
    floor = None
    try:
        floor = current_policy.document["execution"]["evaluation"]["min_quality"]
    except (AttributeError, KeyError, TypeError):
        pass
    for row in review["comparison"]:
        if row["metric"] == "quality":
            row.update(
                expected=floor * 100 if type(floor) in (int, float) else None,
                observed=acceptance["acceptance_rate"] * 100
                if acceptance["acceptance_rate"] is not None else None,
                unit="% accepted",
                status="partially_comparable",
                reason=(
                    "Human decisions are explicit accepted-task evidence. This 25-case "
                    "pilot remains below decision-grade per-segment sample requirements."
                ),
            )
    review["findings"] = [
        row for row in review["findings"]
        if row["code"] != "quality_evidence_unavailable"
    ]
    review["findings"].append({
        "code": "quality_human_review_pending" if not acceptance["reviewed_cases"]
        else "quality_human_review_in_progress" if acceptance["pending_cases"]
        else "quality_human_review_complete",
        "severity": "warning",
        "title": "Automated quality evaluated; human acceptance pending"
        if not acceptance["reviewed_cases"] else "Human acceptance review is incomplete"
        if acceptance["pending_cases"] else "Human acceptance outcomes are available",
        "detail": (
            f"{acceptance['reviewed_cases']} of {acceptance.get('evaluated_cases', quality.get('evaluated_cases', 0))} "
            "Foundry-evaluated response(s) have explicit human decisions. "
            "The dataset and expected answers remain proposed, and five cases per "
            "segment are below the active policy's decision-grade sample floor."
        ),
        "action": "evidence",
        "action_label": "Inspect evidence",
        "evidence_refs": review["decision"]["evidence_refs"],
    })
    return review
