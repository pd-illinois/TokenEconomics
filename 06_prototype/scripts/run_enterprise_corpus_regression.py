from __future__ import annotations

import json
import random
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = ROOT / "13_ENTERPRISE_AGENT_REGRESSION_RESULTS.md"
BASE_URL = "http://127.0.0.1:8765"
USERS = 1000
CALLS_PER_USER_PER_DAY = 10


@dataclass(frozen=True)
class Case:
    case_id: str
    description: str


def _http_json(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(f"{BASE_URL}{path}", data=data, method=method, headers=headers)
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _parse_cases(corpus_path: Path) -> list[Case]:
    text = corpus_path.read_text(encoding="utf-8")
    pattern = re.compile(r"^- \*\*((?:A|M|O)\d{2})\:\*\* (.+)$", re.MULTILINE)
    cases = [Case(case_id=m.group(1), description=m.group(2).strip()) for m in pattern.finditer(text)]
    if len(cases) != 34:
        raise ValueError(f"Expected 34 corpus cases, found {len(cases)}")
    return cases


def _build_confirmed_profile(analysis: dict[str, Any]) -> dict[str, Any]:
    quantities = analysis.get("quantities") or {}
    profile: dict[str, Any] = {
        "agent_pattern": (analysis.get("topology") or {}).get("selected"),
        "multi_agent_count": ((analysis.get("agent_count") or {}).get("value") or 1),
        "modalities": analysis.get("modalities") or [],
        "tools": analysis.get("tools") or [],
    }

    for field in ("document_count", "searches_per_call", "workflow_steps"):
        raw = quantities.get(field)
        value = raw.get("value") if isinstance(raw, dict) else raw
        if value is not None:
            profile[field] = value

    return profile


def main() -> int:
    try:
        cases = _parse_cases(CORPUS_PATH)
        catalog = _http_json("GET", "/api/models")
    except (ValueError, HTTPError, URLError, TimeoutError) as exc:
        print(f"Failed to initialize regression run: {exc}", file=sys.stderr)
        return 2

    offerings = catalog.get("offerings") or []
    if not offerings:
        print("No model offerings returned by /api/models", file=sys.stderr)
        return 3

    seed = int(datetime.now(UTC).strftime("%Y%m%d"))
    rng = random.Random(seed)

    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    results: list[dict[str, Any]] = []

    for case in cases:
        print(f"START_CASE={case.case_id}", flush=True)
        model_choice = rng.choice(offerings)
        provider = str(model_choice.get("provider", "")).strip()
        model = str(model_choice.get("model", "")).strip()
        if not provider or not model:
            print(f"Skipping {case.case_id}: invalid model offering {model_choice!r}", file=sys.stderr)
            continue

        report_title = f"{case.case_id} enterprise corpus run {run_id}"
        try:
            report = _http_json("POST", "/api/reports", {"title": report_title})
            analysis = _http_json("POST", "/api/analyze", {"description": case.description})
            confirmed_profile = _build_confirmed_profile(analysis)
            plan = _http_json(
                "POST",
                "/api/plan",
                {
                    "report_id": report["report_id"],
                    "description": case.description,
                    "parameters": {
                        "provider": provider,
                        "model": model,
                        "users": USERS,
                        "calls_per_user_per_day": CALLS_PER_USER_PER_DAY,
                        "analysis_confirmed": True,
                        "confirmed_profile": confirmed_profile,
                    },
                },
            )
            report_loaded = _http_json("GET", f"/api/reports/{report['report_id']}")
        except (HTTPError, URLError, TimeoutError, KeyError) as exc:
            results.append(
                {
                    "case_id": case.case_id,
                    "description": case.description,
                    "provider": provider,
                    "model": model,
                    "status": "failed",
                    "error": str(exc),
                }
            )
            print(f"FAILED_CASE={case.case_id} ERROR={exc}", flush=True)
            continue

        artifacts = report_loaded.get("artifacts") or {}
        plans = artifacts.get("plans") or []
        receipts = artifacts.get("receipts") or []

        results.append(
            {
                "case_id": case.case_id,
                "description": case.description,
                "provider": provider,
                "model": model,
                "report_id": report.get("report_id"),
                "plan_id": plan.get("plan_id"),
                "receipt_id": plan.get("receipt_id"),
                "prediction_id": (plan.get("prediction") or {}).get("prediction_id"),
                "archetype": (plan.get("prediction") or {}).get("archetype"),
                "tokens_mean": ((plan.get("prediction") or {}).get("tokens_per_call") or {}).get("mean"),
                "model_cost_per_call_mean": ((plan.get("prediction") or {}).get("cost_per_call") or {}).get("mean"),
                "monthly_model_cost_mean": ((plan.get("prediction") or {}).get("monthly_cost") or {}).get("mean"),
                "analysis_topology": ((analysis.get("topology") or {}).get("selected")),
                "analysis_confidence": ((analysis.get("topology") or {}).get("confidence")),
                "report_plan_count": len(plans),
                "report_receipt_count": len(receipts),
                "status": plan.get("status", "unknown"),
            }
        )
        print(
            f"COMPLETE_CASE={case.case_id} REPORT={report.get('report_id')} PLAN={plan.get('plan_id')} STATUS={plan.get('status')}",
            flush=True,
        )

    output_dir = ROOT / "06_prototype" / "studio_reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"enterprise_corpus_run_{run_id}.json"
    output_path.write_text(json.dumps({"seed": seed, "results": results}, indent=2), encoding="utf-8")

    completed = [r for r in results if r.get("status") == "complete"]
    failed = [r for r in results if r.get("status") != "complete"]
    bad_cardinality = [
        r for r in completed if r.get("report_plan_count") != 1 or r.get("report_receipt_count") != 1
    ]

    print(f"RUN_ID={run_id}")
    print(f"SEED={seed}")
    print(f"CASES_TOTAL={len(results)}")
    print(f"CASES_COMPLETE={len(completed)}")
    print(f"CASES_FAILED={len(failed)}")
    print(f"BAD_REPORT_CARDINALITY={len(bad_cardinality)}")
    print(f"RESULT_PATH={output_path}")

    if failed:
        print("FAILED_CASES:")
        for row in failed:
            print(f"- {row['case_id']}: {row.get('error', 'unknown error')}")

    return 0 if not failed else 4


if __name__ == "__main__":
    raise SystemExit(main())
