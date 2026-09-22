"""Receipt-bound, read-only workload integration for the Studio control plane.

Adapters read trusted server-owned evidence stores, never browser-provided scores,
paths, URLs, or executable code. Authorizations do not dispatch a workload.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .atomic_publish import publish_immutable

from .acceptance_contracts import AcceptanceRuleStore
from .consumption_models import ConsumptionFamily
from .governance_decisions import evaluate_segment_constraint
from .observe_economics import build_observe_economics, load_verified_run_evidence
from .policy_store import admit_receipt
from .route_governance import build_route_candidate, evaluate_route_candidate


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def identifier(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("invalid evidence identifier")
    return value


def _sha(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("an exact lowercase SHA-256 evidence hash is required")
    return value


def _keys(value: object, allowed: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != allowed:
        raise ValueError(f"expected exactly these fields: {', '.join(sorted(allowed))}")
    return value


def _number(value: object, name: str, low: float, high: float) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not low <= value <= high):
        raise ValueError(f"{name} must be finite and between {low} and {high}")
    return value


class StudioLifecycle:
    def __init__(self, root: Path, plans, registry: dict, run_root: Path, config: dict):
        self.root, self.plans, self.registry, self.run_root = root, plans, registry, run_root
        self.config = config
        if not isinstance(config, dict) or config.get("schema_version") != "workload-evidence-adapter.v1":
            raise ValueError("unsupported workload adapter configuration")
        if config.get("kind") != "immutable_run_store" or config.get("network_execution") is not False:
            raise ValueError("only a read-only immutable run-store adapter is supported")
        if not isinstance(config.get("maximum_tasks"), int) or not 1 <= config["maximum_tasks"] <= 10000:
            raise ValueError("adapter maximum_tasks must be between 1 and 10000")
        if not isinstance(config.get("required_cost_families"), list) or not config["required_cost_families"]:
            raise ValueError("adapter must declare material cost coverage")
        if (any(item not in {family.value for family in ConsumptionFamily} for item in config["required_cost_families"])
                or len(set(config["required_cost_families"])) != len(config["required_cost_families"])):
            raise ValueError("adapter cost families must be unique supported native-meter families")
        if not isinstance(config.get("version"), str) or not config["version"].strip():
            raise ValueError("adapter version is required")
        identifier(config.get("adapter_id"))

    def receipt(self, plan_id: str) -> dict:
        receipt = self.plans.get_receipt(identifier(plan_id))
        if not receipt:
            raise KeyError("completed forecast receipt not found")
        snapshot = {k: v for k, v in receipt.items() if k not in {"receipt_id", "content_hash"}}
        if digest(snapshot) != receipt.get("content_hash"):
            raise ValueError("original forecast receipt integrity check failed")
        return receipt

    def append(self, receipt: dict, kind: str, value: dict) -> dict:
        record = {
            "schema_version": f"studio-{kind}.v1",
            "id": f"{kind}-{uuid4().hex}",
            "kind": kind,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "report_id": receipt["report_id"],
            "plan_id": receipt["plan_id"],
            "receipt_id": receipt["receipt_id"],
            "receipt_hash": receipt["content_hash"],
            **value,
        }
        record["content_hash"] = digest(record)
        directory = self.root / identifier(receipt["plan_id"])
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{record['id']}.json"
        pending = directory / f".{record['id']}-{uuid4().hex}.pending"
        try:
            with pending.open("x", encoding="utf-8") as stream:
                stream.write(canonical(record))
                stream.flush()
                os.fsync(stream.fileno())
            publish_immutable(pending, path)
        finally:
            pending.unlink(missing_ok=True)
        return record

    def get(self, receipt: dict, identity: str, kind: str) -> dict:
        path = self.root / identifier(receipt["plan_id"]) / f"{identifier(identity)}.json"
        if not path.exists():
            raise KeyError("lifecycle evidence not found")
        value = json.loads(path.read_text(encoding="utf-8"))
        if (value.get("kind") != kind or value.get("id") != identity
                or value.get("receipt_id") != receipt["receipt_id"]
                or value.get("receipt_hash") != receipt["content_hash"]
                or value.get("plan_id") != receipt["plan_id"]
                or digest({k: v for k, v in value.items() if k != "content_hash"}) != value.get("content_hash")):
            raise ValueError("lifecycle evidence integrity or receipt binding failed")
        return value

    def workspace(self, plan_id: str) -> dict:
        receipt = self.receipt(plan_id)
        records = []
        for path in (self.root / identifier(plan_id)).glob("*.json"):
            raw = json.loads(path.read_text(encoding="utf-8"))
            records.append(self.get(receipt, path.stem, raw["kind"]))
        return {
            "plan_id": plan_id, "receipt_id": receipt["receipt_id"],
            "receipt_hash": receipt["content_hash"],
            "workload": receipt.get("trajectory_contract", {}).get("workload"),
            "records": sorted(records, key=lambda item: item["created_at"]),
            "adapter": self.config,
            "live_execution_supported": False,
        }

    def requirements(self, plan_id: str, payload: dict) -> dict:
        _keys(payload, {"acceptance", "budget"})
        acceptance = _keys(payload["acceptance"], {"definition", "segments"})
        if not isinstance(acceptance["definition"], str) or not 1 <= len(acceptance["definition"].strip()) <= 4000:
            raise ValueError("an explicit acceptance definition is required")
        segments = acceptance["segments"]
        if not isinstance(segments, list) or not 1 <= len(segments) <= 100:
            raise ValueError("define every material segment (1-100)")
        seen = set()
        for segment in segments:
            _keys(segment, {"segment_id", "segment_version", "rule_hash"})
            key = identifier(segment["segment_id"]), segment["segment_version"]
            if not isinstance(key[1], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", key[1]):
                raise ValueError("a versioned segment definition is required")
            if key[0] in seen:
                raise ValueError("material segments must be unique")
            seen.add(key[0])
            # Null explicitly requests a rule that is not yet available; it cannot pass.
            if segment["rule_hash"] is not None:
                _sha(segment["rule_hash"])
        budget = _keys(payload["budget"], {
            "budget_usd", "breach_tolerance", "minimum_samples",
            "confidence_level", "minimum_acceptance_lower_bound",
        })
        _number(budget["budget_usd"], "budget_usd", 0.000000001, 1e9)
        for field in ("breach_tolerance", "confidence_level", "minimum_acceptance_lower_bound"):
            _number(budget[field], field, 0.000001, 0.999999)
        if isinstance(budget["minimum_samples"], bool) or not isinstance(budget["minimum_samples"], int) or not 1 <= budget["minimum_samples"] <= 10000:
            raise ValueError("minimum_samples must be an integer between 1 and 10000")
        return self.append(self.receipt(plan_id), "requirements", {
            **payload, "evidence_classification": "proposed",
            "quality_evidence_supplied": False,
        })

    def _run(self, receipt: dict, run_id: str):
        run_id = identifier(run_id)
        registered = self.registry.get(run_id)
        if not registered or registered.get("status") != "completed":
            raise ValueError("a registered completed server-side run is required")
        directory = (self.run_root / run_id).resolve()
        if not directory.is_relative_to(self.run_root.resolve()):
            raise ValueError("run evidence must remain inside the configured store")
        path = directory / "result.json"
        if not path.is_file() or not path.resolve().is_relative_to(directory):
            raise ValueError("immutable result.json is unavailable; registry summaries are not evidence")
        for child in ("trajectories", "acceptance_outcomes", "acceptance_rules", "meter_ledger"):
            if not (directory / child).resolve().is_relative_to(directory):
                raise ValueError("evidence directories must remain inside the registered run")
        result = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise ValueError("run result must be an object")
        if result.get("run_id") != run_id or result.get("report_id") != receipt["report_id"]:
            raise ValueError("run/report lineage is incompatible with this receipt")
        expected = receipt.get("trajectory_contract", {})
        if result.get("trajectory_contract", {}).get("workload") != expected.get("workload"):
            raise ValueError("workload lineage mismatch: changed workload IDs or versions require a new compatible run")
        trajectories, outcomes, entries = load_verified_run_evidence(result, directory)
        if not trajectories or len(trajectories) > self.config["maximum_tasks"]:
            raise ValueError("run is empty or exceeds the bounded evaluation task limit")
        tasks = {}
        for trajectory in trajectories:
            prediction = trajectory.prediction_binding
            if (trajectory.run_id != run_id or trajectory.task.report_id != receipt["report_id"]
                    or trajectory.task.workload.to_dict() != expected.get("workload")
                    or not prediction
                    or prediction.receipt_id != receipt["receipt_id"]
                    or prediction.content_hash != receipt["content_hash"]
                    or str(prediction.prediction_id) != str(receipt["prediction"].get("prediction_id"))):
                raise ValueError("trajectory does not bind the exact original forecast receipt")
            if trajectory.task.task_id in tasks:
                raise ValueError("duplicate task identity in run evidence")
            tasks[trajectory.task.task_id] = trajectory
        seen = set()
        candidate_fields = (
            "experiment_id", "experiment_revision", "arm_id", "policy_candidate_id",
            "policy_candidate_version", "policy_candidate_content_hash",
        )
        candidate_bindings = {tuple(getattr(item, field) for field in candidate_fields) for item in (*outcomes, *entries)}
        if len(candidate_bindings) > 1:
            raise ValueError("a run cannot pool mixed policy candidate or experiment bindings")
        for outcome in outcomes:
            task = tasks.get(outcome.task_id)
            if (not task or outcome.task_id in seen or outcome.trajectory_id != task.trajectory_id
                    or outcome.segment_id != task.task.segment.segment_id
                    or outcome.segment_version != task.task.segment.version):
                raise ValueError("acceptance outcome task/trajectory/segment binding mismatch")
            seen.add(outcome.task_id)
        outcomes_by_task = {item.task_id: item for item in outcomes}
        for entry in entries:
            task = tasks.get(entry.task_id)
            outcome = outcomes_by_task.get(entry.task_id)
            if (not task or entry.trajectory_id != task.trajectory_id
                    or entry.segment_id != task.task.segment.segment_id
                    or (outcome and any(getattr(entry, field) != getattr(outcome, field) for field in (
                        "experiment_id", "experiment_revision", "arm_id", "policy_candidate_id",
                        "policy_candidate_version", "policy_candidate_content_hash",
                    )))):
                raise ValueError("meter evidence task/trajectory/report binding mismatch")
        return result, trajectories, outcomes, entries

    def _rules_hash(self, outcomes, run_id: str) -> str:
        store = AcceptanceRuleStore(self.run_root / identifier(run_id) / "acceptance_rules")
        sources = []
        for rule_id, version, expected in sorted({
            (item.rule_id, item.rule_version, item.rule_content_hash) for item in outcomes
        }):
            record = store.get(rule_id, version)
            if record and record.content_hash != expected:
                raise ValueError("stored acceptance rule differs from the outcome binding")
            expected_segments = {
                (item.segment_id, item.segment_version) for item in outcomes
                if (item.rule_id, item.rule_version, item.rule_content_hash) == (rule_id, version, expected)
            }
            if record and expected_segments != {(record.rule.segment_id, record.rule.segment_version)}:
                raise ValueError("stored acceptance rule does not match the outcome segment")
            sources.append({"rule_id": rule_id, "version": version, "expected_hash": expected,
                            "source_hash": record.content_hash if record else None})
        return digest(sources)

    def preview(self, plan_id: str, run_id: str) -> dict:
        """Inspect source compatibility without attaching or modifying evidence."""
        receipt = self.receipt(plan_id)
        snapshot = {
            "schema_version": "studio-source-preview.v1",
            "plan_id": receipt["plan_id"], "receipt_id": receipt["receipt_id"],
            "receipt_hash": receipt["content_hash"], "run_id": identifier(run_id),
            "adapter_id": self.config["adapter_id"], "adapter_hash": digest(self.config),
            "writes_performed": False, "billable_execution_permitted": False,
        }
        try:
            result, trajectories, outcomes, entries = self._run(receipt, run_id)
            rule_hash = self._rules_hash(outcomes, run_id)
            rules = AcceptanceRuleStore(self.run_root / run_id / "acceptance_rules")
            segments = []
            for segment_id, version in sorted({
                (item.task.segment.segment_id, item.task.segment.version) for item in trajectories
            }):
                source_rules = []
                for rule_id, rule_version, content_hash in sorted({
                    (item.rule_id, item.rule_version, item.rule_content_hash)
                    for item in outcomes
                    if (item.segment_id, item.segment_version) == (segment_id, version)
                }):
                    record = rules.get(rule_id, rule_version)
                    source_rules.append({
                        "rule_id": rule_id, "version": rule_version,
                        "content_hash": content_hash,
                        "definition_available": record is not None,
                        "definition": record.rule.to_dict() if record else None,
                    })
                segments.append({
                    "segment_id": segment_id, "segment_version": version,
                    "task_count": sum(
                        (item.task.segment.segment_id, item.task.segment.version) == (segment_id, version)
                        for item in trajectories
                    ),
                    "acceptance_count": sum(
                        (item.segment_id, item.segment_version) == (segment_id, version)
                        for item in outcomes
                    ),
                    "rules": source_rules,
                    "suggested_rule_hash": source_rules[0]["content_hash"] if len(source_rules) == 1 and source_rules[0]["definition_available"] else None,
                })
            snapshot.update(
                compatible=True, reason="Exact original receipt and source hashes verified; this is not evaluation or admission.",
                result_hash=digest(result), acceptance_rule_sources_hash=rule_hash,
                evidence_classification=result.get("evidence_classification", "unknown"),
                task_count=len(trajectories), acceptance_count=len(outcomes),
                meter_entry_count=len(entries), segments=segments,
            )
        except (ValueError, OSError, KeyError, TypeError) as exc:
            snapshot.update(compatible=False, reason=str(exc), segments=[])
        return {**snapshot, "preview_hash": digest(snapshot)}

    def attach(self, plan_id: str, payload: dict) -> dict:
        _keys(payload, {"run_id", "adapter_id"})
        if payload["adapter_id"] != self.config["adapter_id"]:
            raise ValueError("unsupported workload adapter")
        receipt = self.receipt(plan_id)
        result, trajectories, outcomes, entries = self._run(receipt, payload["run_id"])
        candidate = next(iter(outcomes or entries), None)
        return self.append(receipt, "attachment", {
            "run_id": result["run_id"], "result_hash": digest(result),
            "adapter_id": self.config["adapter_id"], "adapter_hash": digest(self.config),
            "evidence_classification": result.get("evidence_classification", "unknown"),
            "task_count": len(trajectories), "acceptance_count": len(outcomes),
            "meter_entry_count": len(entries),
            "segments": sorted({item.task.segment.segment_id for item in trajectories}),
            "acceptance_rule_hashes": sorted({item.rule_content_hash for item in outcomes}),
            "acceptance_rule_sources_hash": self._rules_hash(outcomes, result["run_id"]),
            "policy_candidate_binding": {
                field: getattr(candidate, field) for field in (
                    "experiment_id", "experiment_revision", "arm_id", "policy_candidate_id",
                    "policy_candidate_version", "policy_candidate_content_hash",
                )
            } if candidate else None,
            "billable_execution_permitted": False,
        })

    def evaluate(self, plan_id: str, payload: dict, policy, principal: str) -> dict:
        _keys(payload, {"attachment_id", "requirements_id", "mode"})
        if payload["mode"] != "read_only":
            raise ValueError("live evaluation is unsupported; this adapter only evaluates persisted evidence")
        receipt = self.receipt(plan_id)
        attachment = self.get(receipt, payload["attachment_id"], "attachment")
        requirements = self.get(receipt, payload["requirements_id"], "requirements")
        result, trajectories, outcomes, entries = self._run(receipt, attachment["run_id"])
        if (digest(result) != attachment["result_hash"] or digest(self.config) != attachment["adapter_hash"]
                or self._rules_hash(outcomes, result["run_id"]) != attachment["acceptance_rule_sources_hash"]):
            raise ValueError("attached source or adapter changed; create a new immutable attachment")
        # Require Azure to be available even for authorization of the bounded operation.
        admission = admit_receipt(receipt, policy)
        policy_ref = admission["policy"]
        if (policy_ref.get("provenance", {}).get("source") != "azure_app_configuration"
                or not all(policy_ref.get("provenance", {}).get(key) for key in ("etag", "label"))):
            raise ValueError("Azure authority is required for evaluation authorization")
        policy_quality = policy.document["execution"]["evaluation"]
        effective_budget = {**requirements["budget"], "minimum_samples": max(
            requirements["budget"]["minimum_samples"], policy_quality["min_segment_samples"]
        )}
        rules = AcceptanceRuleStore(self.run_root / attachment["run_id"] / "acceptance_rules")
        rule_cache = {}
        by_task = {item.task_id: item for item in outcomes}
        entries_by_task = {}
        for entry in entries:
            entries_by_task.setdefault(entry.task_id, []).append(entry)
        segment_results = []
        defined = {(item["segment_id"], item["segment_version"]) for item in requirements["acceptance"]["segments"]}
        actual = {(item.task.segment.segment_id, item.task.segment.version) for item in trajectories}
        all_rules_bound = defined == actual
        for segment in requirements["acceptance"]["segments"]:
            scoped = [item for item in trajectories if (
                item.task.segment.segment_id, item.task.segment.version
            ) == (segment["segment_id"], segment["segment_version"])]
            decisions, costs, acceptance_hashes, cost_hashes = [], [], [], []
            for trajectory in scoped:
                outcome = by_task.get(trajectory.task.task_id)
                rule = None
                if outcome:
                    rule_key = (outcome.rule_id, outcome.rule_version)
                    if rule_key not in rule_cache:
                        rule_cache[rule_key] = rules.get(*rule_key)
                    rule = rule_cache[rule_key]
                rule_bound = bool(
                    outcome and segment["rule_hash"] and outcome.rule_content_hash == segment["rule_hash"]
                    and rule and rule.content_hash == outcome.rule_content_hash
                    and rule.rule.segment_id == segment["segment_id"]
                    and rule.rule.segment_version == segment["segment_version"]
                    and rule.rule.minimum_score >= policy_quality["min_quality"]
                )
                reviews = list(outcome.reviews) if outcome else []
                supported_review = bool(
                    rule_bound and len(reviews) == 1
                    and reviews[0].method.value == "automated_evaluator"
                    and reviews[0].reviewer_id == rule.rule.evaluator_id
                    and reviews[0].evidence_version == rule.rule.evaluator_version
                    and reviews[0].score is not None
                )
                derived = ("accepted" if reviews[0].score >= rule.rule.minimum_score else "rejected") if supported_review else "inconclusive"
                if outcome and derived != outcome.decision.value:
                    derived = "inconclusive"
                all_rules_bound = all_rules_bound and rule_bound
                decisions.append(derived if trajectory.status == "completed" else "inconclusive")
                task_entries = entries_by_task.get(trajectory.task.task_id, [])
                covered = (bool(task_entries)
                           and set(self.config.get("required_cost_families", [])) <= {item.meter_family.value for item in task_entries}
                           and all(item.cost_coverage.value in {"priced", "not_applicable"} for item in task_entries))
                costs.append(sum(float(item.allocated_cost_usd or 0) for item in task_entries) if covered else None)
                acceptance_hashes.append(digest(outcome.to_dict()) if outcome else digest({"missing_acceptance": trajectory.task.task_id}))
                cost_hashes.append(digest([item.to_dict() for item in task_entries]))
            segment_results.append(evaluate_segment_constraint(
                segment_id=segment["segment_id"], segment_version=segment["segment_version"],
                acceptance_decisions=decisions, allocatable_costs_usd=costs,
                acceptance_evidence_hashes=acceptance_hashes, cost_evidence_hashes=cost_hashes,
                **effective_budget,
            ).to_dict())
        measured = result.get("evidence_classification") in {"measured", "measured_live", "production_validated"}
        complete_cost = defined == actual and all(
            item["expected_allocatable_cost_usd"] is not None
            and "incomplete_priced_cost_coverage" not in item["reason_codes"]
            for item in segment_results
        )
        total = sum(item["sample_count"] for item in segment_results)
        expected_cost = sum(item["expected_allocatable_cost_usd"] * item["sample_count"] for item in segment_results) / total if complete_cost and total else None
        sampling = result.get("sampling_evidence") or {}
        sampling_verified = bool(
            isinstance(sampling, dict)
            and set(sampling) == {"schema_version", "representative", "independent_tasks", "population_revision", "method", "content_hash"}
            and sampling.get("schema_version") == "task-sampling-evidence.v1"
            and sampling.get("representative") is True and sampling.get("independent_tasks") is True
            and all(isinstance(sampling.get(key), str) and sampling[key].strip() for key in ("population_revision", "method"))
            and sampling.get("content_hash") == digest({k: v for k, v in sampling.items() if k != "content_hash"})
        )
        status = "inconclusive"
        if measured and sampling_verified:
            if any(item["outcome"] == "ineligible" for item in segment_results):
                status = "ineligible"
            elif all_rules_bound and complete_cost and all(item["outcome"] == "eligible" for item in segment_results):
                status = "eligible"
        return self.append(receipt, "evaluation", {
            "attachment_id": attachment["id"], "attachment_hash": attachment["content_hash"],
            "requirements_id": requirements["id"], "requirements_hash": requirements["content_hash"],
            "policy_candidate_binding": attachment["policy_candidate_binding"],
            "mode": "read_only", "authorized_principal": principal, "authorization_policy": policy_ref,
            "maximum_tasks": self.config["maximum_tasks"], "billable_execution_permitted": False,
            "evidence_classification": result.get("evidence_classification", "unknown"),
            "acceptance_definition_bound": all_rules_bound,
            "material_segments_complete": defined == actual,
            "complete_task_cost_coverage": complete_cost,
            "expected_complete_task_cost": expected_cost,
            "segments": segment_results,
            "effective_budget": effective_budget,
            "sampling_evidence_verified": sampling_verified,
            "status": status,
            "limits": [
                "Existing acceptance outcomes are evaluated only under their exact original rule hash.",
                "A new prose definition does not relabel existing quality evidence.",
                "Automated review identity and version must match the stored rule. Human-review authority is not integrated by this adapter.",
                "Binomial bounds assume representative independent completed tasks; repeated cases are not proof of calibration.",
                "Priced ledger coverage does not prove that omitted components were captured.",
            ],
        })

    def reassess(self, plan_id: str, payload: dict, policy) -> dict:
        _keys(payload, {"evaluation_id"})
        receipt = self.receipt(plan_id)
        evaluation = self.get(receipt, payload["evaluation_id"], "evaluation")
        attachment = self.get(receipt, evaluation["attachment_id"], "attachment")
        requirements = self.get(receipt, evaluation["requirements_id"], "requirements")
        result, trajectories, outcomes, _ = self._run(receipt, attachment["run_id"])
        if (digest(result) != attachment["result_hash"] or digest(self.config) != attachment["adapter_hash"]
                or evaluation["attachment_hash"] != attachment["content_hash"]
                or evaluation["requirements_hash"] != requirements["content_hash"]
                or self._rules_hash(outcomes, result["run_id"]) != attachment["acceptance_rule_sources_hash"]):
            raise ValueError("evaluation source binding changed")
        segments = evaluation["segments"]
        budget = evaluation["effective_budget"]
        evidence = {}
        if evaluation["acceptance_definition_bound"]:
            evidence["acceptance_rule"] = {
                "state": "satisfied", "authority": "server_verified_acceptance_outcomes",
                "evidence_revision": evaluation["schema_version"],
                "content_hash": evaluation["content_hash"], "reason": None,
            }
        acceptance_segments = []
        for item in segments:
            lower_bound = item["acceptance_wilson_lower_bound"]
            sufficient = item["sample_count"] >= item["minimum_samples"] and lower_bound is not None
            acceptance_segments.append({
                "segment_id": item["segment_id"], "segment_version": item["segment_version"],
                "outcome": ("accepted" if lower_bound >= item["minimum_acceptance_lower_bound"] else "rejected") if sufficient else "inconclusive",
                "sample_count": item["sample_count"], "minimum_samples": item["minimum_samples"],
            })
        constraints = {"acceptance": {"segments": acceptance_segments}}
        if evaluation["complete_task_cost_coverage"]:
            expected = evaluation["expected_complete_task_cost"]
            constraints.update(cost_coverage={"applicable_cost": expected, "priced_cost": expected},
                               expected_complete_task_cost=expected)
        bounds = [item["breach_probability_upper_bound"] for item in segments]
        if bounds and all(item is not None for item in bounds):
            constraints["tail_risk"] = {
                "budget": budget["budget_usd"], "epsilon": budget["breach_tolerance"],
                "breach_probability": max(bounds),
                "evidence_classification": "measured" if evaluation["sampling_evidence_verified"] and evaluation["evidence_classification"] in {"measured", "measured_live", "production_validated"} else "modeled",
            }
        candidate = build_route_candidate(
            receipt, evidence=evidence, constraints=constraints,
            assessment_context={"evaluation_id": evaluation["id"], "evaluation_hash": evaluation["content_hash"]},
        )
        decision = evaluate_route_candidate(candidate)
        admission = admit_receipt(receipt, policy)
        current = admission["policy"]
        expected_policy = {
            "policy_id": current["policy_id"], "version": current["version"],
            "content_hash": current["content_hash"],
            **{key: current["provenance"].get(key) for key in ("source", "label", "etag")},
        }
        policy_matches = evaluation["authorization_policy"] == current and all(
            item.policy_binding and item.policy_binding.to_dict() == expected_policy for item in trajectories
        )
        eligible = (evaluation["status"] == "eligible" and decision["status"] == "eligible"
                    and admission["status"] == "admitted" and policy_matches)
        return self.append(receipt, "reassessment", {
            "evaluation_id": evaluation["id"], "evaluation_hash": evaluation["content_hash"],
            "candidate": candidate, "route_decision": decision, "azure_admission": admission,
            "runtime_policy_matches_current_authority": policy_matches,
            "status": "eligible_for_operational_admission" if eligible else "blocked",
            "billable_execution_permitted": False,
            "original_admission_inherited": False,
        })

    def operational_admission(self, plan_id: str, payload: dict, policy, principal: str) -> dict:
        _keys(payload, {"reassessment_id"})
        receipt = self.receipt(plan_id)
        previous = self.get(receipt, payload["reassessment_id"], "reassessment")
        # Re-read all sources and Azure authority at the privileged boundary.
        latest = self.reassess(plan_id, {"evaluation_id": previous["evaluation_id"]}, policy)
        if latest["status"] != "eligible_for_operational_admission":
            raise ValueError("operational admission blocked by current receipt/evidence/Azure policy checks")
        return self.append(receipt, "operational-admission", {
            "reassessment_id": latest["id"], "reassessment_hash": latest["content_hash"],
            "authorized_principal": principal, "policy": latest["azure_admission"]["policy"],
            "status": "admitted", "execution_dispatch": "unsupported",
            "billable_execution_permitted": False,
            "reason": "Policy binding only; a separately approved bounded runtime adapter is not installed.",
        })

    def compare(self, plan_id: str, payload: dict, policy) -> dict:
        _keys(payload, {"evaluation_ids"})
        identities = payload["evaluation_ids"]
        if not isinstance(identities, list) or not 1 <= len(identities) <= 20 or len(set(identities)) != len(identities):
            raise ValueError("select 1-20 unique evaluation IDs")
        receipt = self.receipt(plan_id)
        evaluations = [self.get(receipt, item, "evaluation") for item in identities]
        if len({item["requirements_hash"] for item in evaluations}) != 1:
            raise ValueError("candidate comparisons require identical acceptance, budget and material segment requirements")
        attachments = [self.get(receipt, item["attachment_id"], "attachment") for item in evaluations]
        if len({item["run_id"] for item in attachments}) != len(attachments):
            raise ValueError("repeated assessments of the same run are not independent candidates")
        assessments = [self.reassess(plan_id, {"evaluation_id": item["id"]}, policy) for item in evaluations]
        eligible = [
            (evaluation, assessment) for evaluation, assessment in zip(evaluations, assessments)
            if evaluation["status"] == "eligible" and assessment["route_decision"]["status"] == "eligible"
        ]
        selected = min(eligible, key=lambda pair: (pair[0]["expected_complete_task_cost"], pair[0]["id"])) if eligible else None
        return self.append(receipt, "comparison", {
            "status": "candidate_selected_for_review" if selected else "no_eligible_candidate",
            "selected_evaluation_id": selected[0]["id"] if selected else None,
            "selected_reassessment_id": selected[1]["id"] if selected else None,
            "candidates": [{
                "evaluation_id": item["id"], "evaluation_hash": item["content_hash"],
                "reassessment_id": assessment["id"], "reassessment_hash": assessment["content_hash"],
                "expected_complete_task_cost": item["expected_complete_task_cost"],
                "evaluation_status": item["status"], "route_status": assessment["route_decision"]["status"],
            } for item, assessment in zip(evaluations, assessments)],
            "selection_method": "least_expected_complete_task_cost_among_eligible_candidates",
            "billable_execution_permitted": False, "policy_publication_performed": False,
        })

    def reconcile(self, plan_id: str, payload: dict) -> dict:
        _keys(payload, {"evaluation_id"})
        receipt = self.receipt(plan_id)
        evaluation = self.get(receipt, payload["evaluation_id"], "evaluation")
        attachment = self.get(receipt, evaluation["attachment_id"], "attachment")
        result, trajectories, outcomes, entries = self._run(receipt, attachment["run_id"])
        if (digest(result) != attachment["result_hash"] or evaluation["attachment_hash"] != attachment["content_hash"]
                or self._rules_hash(outcomes, result["run_id"]) != attachment["acceptance_rule_sources_hash"]):
            raise ValueError("reconciliation source differs from evaluated evidence")
        observed = build_observe_economics(
            run_id=result["run_id"], report_id=receipt["report_id"],
            evidence_classification=result.get("evidence_classification", "unknown"),
            trajectories=trajectories, outcomes=outcomes, entries=entries,
            integrity_verified=True,
        )
        return self.append(receipt, "reconciliation", {
            "evaluation_id": evaluation["id"], "evaluation_hash": evaluation["content_hash"],
            "attachment_id": attachment["id"], "attachment_hash": attachment["content_hash"],
            "run_id": result["run_id"], "prediction_id": receipt["prediction"].get("prediction_id"),
            "evidence_classification": result.get("evidence_classification", "unknown"),
            "observed_economics": observed,
            "complete_task_cost_coverage": evaluation["complete_task_cost_coverage"],
            "status": "read_only_evidence_join",
            "forecast_error": None,
            "forecast_error_reason": "Original forecast is model-call economics; no versioned comparable complete-task distribution is available. Partial allocatable cost is not total task cost.",
            "calibrated_coverage_claim": False,
            "predictor_learning_performed": False,
            "learning_status": "blocked_pending_comparable_complete_task_forecast_and_representative_actuals",
        })
