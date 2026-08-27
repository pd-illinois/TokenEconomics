"""Durable parent reports for Studio artifacts."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

_report_lock = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReportStore:
    """File-backed report manifests that reference child artifacts by ID."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def create(self, title: str = "Untitled economics report") -> dict:
        timestamp = _now()
        report_id = f"RPT-{datetime.now(timezone.utc):%Y%m%d}-{uuid4().hex[:8].upper()}"
        report = {
            "report_id": report_id,
            "title": title.strip() or "Untitled economics report",
            "status": "draft",
            "notes": "",
            "created_at": timestamp,
            "updated_at": timestamp,
            "artifacts": {"plans": [], "receipts": [], "govern_handoffs": [], "runs": []},
        }
        with _report_lock:
            self._write_unlocked(report)
        return report

    def get(self, report_id: str) -> dict | None:
        path = self.root / report_id / "report.json"
        with _report_lock:
            if not path.exists():
                return None
            return json.loads(path.read_text(encoding="utf-8"))

    def list(self) -> list[dict]:
        if not self.root.exists():
            return []
        reports = [self.get(path.name) for path in self.root.iterdir() if path.is_dir()]
        return sorted(
            (report for report in reports if report is not None),
            key=lambda report: report["updated_at"],
            reverse=True,
        )

    def save(self, report_id: str, *, title: str | None = None, notes: str | None = None) -> dict:
        with _report_lock:
            report = self.get(report_id)
            if not report:
                raise KeyError(report_id)
            if title is not None:
                report["title"] = title.strip() or report["title"]
            if notes is not None:
                report["notes"] = notes
            report["updated_at"] = _now()
            self._write_unlocked(report)
            return report

    def add_artifact(self, report_id: str, collection: str, artifact: dict) -> dict:
        with _report_lock:
            report = self.get(report_id)
            if not report:
                raise KeyError(report_id)
            if collection not in report["artifacts"]:
                raise ValueError(f"unknown report artifact collection: {collection}")
            artifact_id = artifact["id"]
            items = report["artifacts"][collection]
            existing = next((item for item in items if item["id"] == artifact_id), None)
            if existing:
                existing.update(artifact)
            else:
                items.append(artifact)
            report["updated_at"] = _now()
            self._write_unlocked(report)
            return report

    def _write_unlocked(self, report: dict) -> None:
        path = self.root / report["report_id"] / "report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        os.replace(temporary, path)