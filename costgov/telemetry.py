"""
Telemetry sink (control plane).

In-memory + optional JSONL, the stand-in for App Insights / Log Analytics / Langfuse.
Holds per-request records, supports per-tenant cost attribution (FinOps), and keeps a
*sampled* stream for continuous evaluation (mirrors Foundry continuous eval sampling).
"""

from __future__ import annotations
import json
import logging
import random

# When App Insights is wired (azure_integrations.init_app_insights), this flips True and
# each request is emitted as a "costgov.request" log record -> App Insights customDimensions,
# which the Azure Monitor Workbook (dashboard/workbook.json) queries.
EMIT = False
_logger = logging.getLogger("costgov")


class Telemetry:
    def __init__(self, sample_rate: float, seed: int = 7):
        self.records = []
        self.sampled = []           # (question, difficulty, answer_text, model)
        self.sample_rate = sample_rate
        self._rng = random.Random(seed)

    def record(self, res, sampled_meta: dict) -> None:
        self.records.append(res)
        if EMIT:
            _logger.info("costgov.request", extra={
                "tenant": res.tenant, "model": res.model,
                "cost_usd": res.cost_usd, "latency_ms": res.latency_ms,
                "cache_hit": int(res.cache_hit), "degraded": int(res.degraded),
                "difficulty": sampled_meta.get("difficulty", ""),
                "request_id": res.request_id, "trace_id": res.trace_id,
                "report_id": res.report_id, "run_id": res.run_id,
                "prediction_id": res.prediction_id,
                "segment": res.segment, "policy_version": res.policy_version,
                "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
                "cached_tokens": res.cached_tokens,
                "reasoning_tokens": res.reasoning_tokens,
                "document_tokens": res.document_tokens,
                "task_id": res.task_id,
                "trajectory_id": res.trajectory_id,
                "workload_id": res.workload_id,
                "workload_version": res.workload_version,
                "segment_id": res.segment_id,
                "segment_version": res.segment_version,
                "prediction_receipt_id": res.prediction_receipt_id,
                "prediction_receipt_hash": res.prediction_receipt_hash,
                "policy_id": res.policy_id,
                "policy_hash": res.policy_hash,
                "policy_source": res.policy_source,
                "policy_label": res.policy_label,
                "policy_etag": res.policy_etag,
                "policy_candidate_id": res.policy_candidate_id,
                "policy_candidate_version": res.policy_candidate_version,
                "policy_candidate_content_hash": res.policy_candidate_content_hash,
            })
        # continuous eval only looks at a sampled fraction of live traffic
        if self._rng.random() < self.sample_rate:
            self.sampled.append({
                "question": res.question,
                "difficulty": sampled_meta["difficulty"],
                "answer_text": res.answer_text,
                "model": res.model,
                "request_id": res.request_id,
                "trace_id": res.trace_id,
                "report_id": res.report_id,
                "run_id": res.run_id,
                "prediction_id": res.prediction_id,
                "segment": res.segment,
                "policy_version": res.policy_version,
                "task_id": res.task_id,
                "trajectory_id": res.trajectory_id,
                "workload_id": res.workload_id,
                "workload_version": res.workload_version,
                "segment_id": res.segment_id,
                "segment_version": res.segment_version,
                "prediction_receipt_id": res.prediction_receipt_id,
                "prediction_receipt_hash": res.prediction_receipt_hash,
                "policy_id": res.policy_id,
                "policy_hash": res.policy_hash,
                "policy_source": res.policy_source,
                "policy_label": res.policy_label,
                "policy_etag": res.policy_etag,
                "policy_candidate_id": res.policy_candidate_id,
                "policy_candidate_version": res.policy_candidate_version,
                "policy_candidate_content_hash": res.policy_candidate_content_hash,
            })

    def tenant_cost(self, tenant: str) -> float:
        return round(sum(r.cost_usd for r in self.records if r.tenant == tenant), 6)

    def total_cost(self) -> float:
        return round(sum(r.cost_usd for r in self.records), 6)

    def avg_latency(self) -> float:
        return round(sum(r.latency_ms for r in self.records) / len(self.records), 1) if self.records else 0.0

    def dump_jsonl(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for r in self.records:
                fh.write(json.dumps(r.__dict__) + "\n")
