import json

import pytest

from costgov.reports import ReportStore


def test_retirement_hides_report_without_changing_historical_manifest(tmp_path):
    store = ReportStore(tmp_path)
    report = store.create("Old books RAG")
    path = tmp_path / report["report_id"] / "report.json"
    before = path.read_bytes()
    event = store.retire(report["report_id"], reason="User requested a fresh report")
    assert store.list() == []
    assert store.get(report["report_id"]) == report
    assert path.read_bytes() == before
    assert event["schema_version"] == "report-retirement.v1"
    assert store.retire(report["report_id"], reason="Again") == event
    assert json.loads((path.parent / "retired.json").read_text()) == event


def test_retirement_requires_existing_report_and_reason(tmp_path):
    store = ReportStore(tmp_path)
    with pytest.raises(KeyError):
        store.retire("missing", reason="User requested")
    with pytest.raises(ValueError):
        store.retire("missing", reason=" ")
