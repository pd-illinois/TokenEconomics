"""Explicit in-memory evaluation input; default batch journals stay content-free."""

from __future__ import annotations

import hashlib
import json
import re

from costgov.studio_lifecycle import digest

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_CAPTURE_BYTES = 8 * 1024 * 1024


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", value):
        raise ValueError("Evaluation capture requires an exact response/case identifier")
    return value


class CapturedResponses:
    """Trusted operator callback. No content is written to disk by this class."""

    def __init__(self, cases, *, allow_content_evaluation=False):
        if allow_content_evaluation is not True:
            raise ValueError("Explicit evaluation-content consent is required")
        if not isinstance(cases, list) or not 1 <= len(cases) <= 25:
            raise ValueError("Evaluation capture supports one bounded batch of 1-25 cases")
        self.cases = json.loads(json.dumps(cases, allow_nan=False))
        self.rows = []
        self.byte_count = 0
        identities = set()
        for case in self.cases:
            case_id = _identifier(case["case_id"])
            if case_id in identities or not isinstance(case["question"], str) or not case["question"]:
                raise ValueError("Evaluation cases require unique identities and questions")
            identities.add(case_id)

    def __call__(self, *, run_id, question_number, question, response, metric, provider_request_id):
        if (type(question_number) is not int or question_number != len(self.rows) + 1
                or question_number > len(self.cases)):
            raise ValueError("Evaluation responses must arrive once in case order")
        case = self.cases[question_number - 1]
        if question != case["question"] or metric.get("question_number") != question_number:
            raise ValueError("Evaluation response does not match its requested case")
        run_id = _identifier(run_id)
        if self.rows and self.rows[0]["run_id"] != run_id:
            raise ValueError("Evaluation capture cannot mix runs")
        raw = json.dumps(response, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(raw) > MAX_RESPONSE_BYTES or self.byte_count + len(raw) > MAX_CAPTURE_BYTES:
            raise ValueError("Evaluation response exceeds the bounded in-memory content allowance")
        response_id = _identifier(response.get("id"))
        if any(row["provider_response_id"] == response_id for row in self.rows):
            raise ValueError("A provider response cannot satisfy two evaluation cases")
        if provider_request_id is not None:
            provider_request_id = _identifier(provider_request_id)
        answer, grounding = [], []
        for item in response.get("output", []):
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message" and item.get("role") == "assistant":
                for part in item.get("content", []):
                    if isinstance(part, dict) and part.get("type") == "output_text":
                        if not isinstance(part.get("text"), str):
                            raise ValueError("Malformed response text")
                        answer.append(part["text"])
            if (item.get("type") == "mcp_call" and item.get("name") == "knowledge_base_retrieve"
                    and item.get("error") is None and item.get("output") is not None):
                value = item["output"]
                grounding.append(value if isinstance(value, str) else json.dumps(value, allow_nan=False))
        self.byte_count += len(raw)
        answer_text = "\n".join(answer)
        self.rows.append({
            "case_id": case["case_id"], "run_id": run_id, "question_number": question_number,
            "provider_response_id": response_id, "provider_request_id": provider_request_id,
            "query": question, "response": answer_text, "context": "\n\n".join(grounding),
            "question_hash": hashlib.sha256(question.encode()).hexdigest(),
            "response_text_hash": hashlib.sha256(answer_text.encode()).hexdigest(),
            "response_hash": hashlib.sha256(raw).hexdigest(),
            "metric_hash": digest(metric),
            "grounding_status": "available" if any(text.strip() for text in grounding) else "unavailable",
        })

    def manifest(self, batch, *, dataset_hash):
        if (batch.get("execution_status") != "completed" or len(self.rows) != batch["questions_count"]
                or len(batch["metrics"]) != batch["questions_count"]):
            raise ValueError("Only a complete captured batch can be submitted for evaluation")
        if batch["evidence"]["content_hash"] != digest({k: v for k, v in batch.items() if k != "evidence"}):
            raise ValueError("Batch content integrity mismatch")
        if not isinstance(dataset_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", dataset_hash):
            raise ValueError("An exact dataset hash is required")
        rows = []
        for row, metric in zip(self.rows, batch["metrics"]):
            if row["run_id"] != batch["run_id"] or row["metric_hash"] != digest(metric):
                raise ValueError("Captured response differs from the sealed batch metric")
            rows.append({key: row[key] for key in (
                "case_id", "run_id", "question_number", "provider_response_id",
                "provider_request_id", "response_hash", "metric_hash", "grounding_status",
                "question_hash", "response_text_hash",
            )})
        value = {
            "schema_version": "rag-response-capture.v1", "run_id": batch["run_id"],
            "batch_hash": batch["evidence"]["content_hash"], "dataset_hash": dataset_hash,
            "content_storage": "memory_only_local", "rows": rows,
        }
        return {**value, "content_hash": digest(value)}

    def clear(self):
        self.rows.clear()
        self.cases.clear()
        self.byte_count = 0
