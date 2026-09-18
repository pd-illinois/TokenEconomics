"""Offline, proposed Q&A pilot evidence; hashes establish integrity, not trust."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

SCHEMA = "rag-qna-dataset.v1"
DEFAULT_DATASET = Path(__file__).parent / "evaluation" / "qna-pilot.v1.json"
SEGMENTS = ("factual", "synthesis", "cross-book", "ambiguous", "unanswerable")


def content_hash(value):
    """SHA256 of canonical UTF-8 JSON, excluding only the top-level content_hash."""
    body = {key: item for key, item in value.items() if key != "content_hash"}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def dataset_hash(dataset):
    return content_hash(dataset)


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def validate_dataset(dataset, *, source_root=None):
    """Validate a v1 proposed pilot; optionally check local source file bytes.

    No authentication or semantic truth validation is implied. A reviewed dataset
    needs a new contract, rather than changing this pilot's pending review flag.
    """
    if not isinstance(dataset, dict) or dataset.get("schema_version") != SCHEMA:
        raise ValueError("Unsupported Q&A dataset schema")
    if dataset.get("content_hash") != dataset_hash(dataset):
        raise ValueError("Dataset content hash mismatch")
    for key in ("dataset_id", "revision", "segment_revision", "family_revision"):
        if not _nonempty(dataset.get(key)):
            raise ValueError(f"Missing dataset {key}")
    if (dataset.get("evidence_status") != "proposed"
            or dataset.get("human_review_status") != "pending"
            or dataset.get("heldout_scored_proof") is not False
            or dataset.get("operational_admission") is not False):
        raise ValueError("Pilot cannot claim human review, scored proof or admission")
    sources = dataset.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("Missing sources")
    source_ids = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("Invalid source")
        source_id = source.get("source_id")
        digest = source.get("sha256", "")
        if (not _nonempty(source_id) or source_id in source_ids
                or not isinstance(digest, str) or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)):
            raise ValueError("Invalid or duplicate source identity/hash")
        source_ids.add(source_id)
        filename = source.get("file", "")
        if not _nonempty(filename) or Path(filename).name != filename or "\\" in filename:
            raise ValueError("Source file must be a local basename")
        if source_root is not None:
            root = Path(source_root).resolve()
            path = (root / filename).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Source escaped corpus root")
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError(f"Source content hash mismatch: {source_id}")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Missing cases")
    ids, questions, families, segments = set(), set(), {}, set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Invalid case")
        for key in ("case_id", "family_id", "segment_id", "question", "expected_answer"):
            if not _nonempty(case.get(key)):
                raise ValueError(f"Missing case {key}")
        if case["case_id"] in ids or case["question"].strip().casefold() in questions:
            raise ValueError("Duplicate case/question")
        ids.add(case["case_id"])
        questions.add(case["question"].strip().casefold())
        segment = case["segment_id"]
        if segment not in SEGMENTS:
            raise ValueError("Unknown segment")
        segments.add(segment)
        partition = case.get("partition")
        if partition not in ("development", "holdout"):
            raise ValueError("Unknown partition")
        previous = families.setdefault(case["family_id"], partition)
        if previous != partition:
            raise ValueError("Question family leaks across development/holdout")
        if (case.get("human_review_status") != "pending"
                or case.get("expected_answer_status") != "proposed"):
            raise ValueError("Draft case must not claim human-reviewed ground truth")
        if case.get("answer_behavior") not in ("answer", "clarify", "abstain"):
            raise ValueError("Unknown answer behavior")
        if (segment == "unanswerable" and case["answer_behavior"] != "abstain"
                or segment == "ambiguous" and case["answer_behavior"] != "clarify"):
            raise ValueError("Unsafe answer behavior")
        rubric = case.get("rubric")
        if not isinstance(rubric, list) or not rubric or not all(map(_nonempty, rubric)):
            raise ValueError("Missing proposed rubric")
        locators = case.get("source_locators")
        if not isinstance(locators, list) or not locators:
            raise ValueError("Missing source scope/locators")
        for locator in locators:
            if (not isinstance(locator, dict) or not _nonempty(locator.get("source_id"))
                    or locator["source_id"] not in source_ids or not _nonempty(locator.get("locator"))):
                raise ValueError("Unknown source or missing locator")
    if segments != set(SEGMENTS):
        raise ValueError("Pilot must retain every quality segment")
    return dataset


def load_dataset(path=None, *, verify_sources=True):
    """Load immutable pilot metadata; source verification uses rag/data only."""
    dataset = json.loads(Path(path or DEFAULT_DATASET).read_text(encoding="utf-8"))
    return validate_dataset(
        dataset, source_root=Path(__file__).parent / "data" if verify_sources else None,
    )
