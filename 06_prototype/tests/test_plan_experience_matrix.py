from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import plan_studio
from costgov.planning import PlanStore


CASES_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "plan_experience_cases.v1.json"
)
MODEL_ROUTES = {"foundry", "copilot_studio_byom", "foundry_work_iq"}
FIXTURE = json.loads(CASES_PATH.read_text(encoding="utf-8"))
CASES = FIXTURE["cases"]


def _analysis() -> dict:
    return {
        "schema_version": "1.0",
        "rule_set_version": "nine-case-proof.v1",
        "description_hash": "9" * 64,
        "topology": {
            "selected": "single_call",
            "confidence": "high",
            "alternatives": [],
            "evidence": [{"rule": "explicit_case", "text": "deterministic"}],
        },
        "agent_count": {"value": 1, "source": "explicit", "evidence": []},
        "modalities": ["text"],
        "tools": [],
        "quantities": {},
        "assumptions": [],
        "clarifications": [],
        "exclusions": [],
    }


def _token_result(description: str, parameters: dict) -> dict:
    return {
        "status": "complete",
        "description": description,
        "intake": parameters,
        "prediction": {
            "prediction_id": 915,
            "model": "gpt-4.1",
            "provider": "azure_openai",
            "archetype": "SingleCall_TextOnly",
            "pricing_version": "nine-case-predictor.v1",
            "pricing_verified": True,
            "tokens_per_call": {"total": 1000},
            "cost_per_call": {"mean": 0.01},
            "monthly_cost": {"mean": 30},
            "annual_cost": {"mean": 365},
        },
        "infrastructure": {
            "status": "not_estimated",
            "message": "Infrastructure remains a separate ledger.",
        },
    }


def _post(connection: HTTPConnection, path: str, payload: dict) -> tuple[int, dict]:
    connection.request(
        "POST",
        path,
        json.dumps(payload),
        {"Content-Type": "application/json"},
    )
    response = connection.getresponse()
    return response.status, json.loads(response.read())


def _at_path(document: object, path: str) -> object:
    value = document
    for part in path.split("."):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def _assert_expected(result: dict, expected: dict) -> None:
    for path, value in expected.items():
        actual = _at_path(result, path)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            assert actual == pytest.approx(value), path
        else:
            assert actual == value, path


def _assert_independent_arithmetic(case: dict, result: dict) -> None:
    route = case["route"]
    commercial_input = case["parameters"].get("commercial", {})

    if route in {"included", "agent_builder"}:
        seats = commercial_input["seat_assumptions"]["seats"]
        unit_cost = commercial_input["seat_assumptions"][
            "allocated_monthly_cost_per_user_usd"
        ]
        assert result["purchase"]["fixed_allocation_cost_usd"] == pytest.approx(
            seats * unit_cost
        )

    if route in {"cowork", "work_iq", "foundry_work_iq"}:
        prior = commercial_input["scenario_prior"]
        assert prior["credits_per_task_p50"] == prior["credits_per_task_p95"]
        expected_credits = (
            commercial_input["task_count"] * prior["credits_per_task_p50"]
        )
        assert result["commercial"]["mean_copilot_credits"] == pytest.approx(
            expected_credits
        )
        assert result["commercial"]["p50_copilot_credits"] == pytest.approx(
            expected_credits
        )
        assert result["commercial"]["p95_copilot_credits"] == pytest.approx(
            expected_credits
        )

    if route in {"copilot_studio", "copilot_studio_byom"}:
        line = result["commercial"]["lines"][0]
        event = commercial_input["events"][0]
        independently_billed = (
            event["quantity"]
            / line["meter"]["unit_size"]
            * line["meter"]["credits_per_unit"]
        )
        assert line["gross_copilot_credits"] == pytest.approx(
            independently_billed
        )
        assert result["commercial"]["total_copilot_credits"] == pytest.approx(
            independently_billed
        )

    if route == "github_copilot":
        usage = commercial_input["token_usage"]
        oracle = case["oracle"]
        rate_fields = {
            "input_tokens": "input_usd_per_million",
            "cached_input_tokens": "cached_input_usd_per_million",
            "cache_write_tokens": "cache_write_usd_per_million",
            "output_tokens": "output_usd_per_million",
        }
        independently_priced = sum(
            usage[component] * oracle[rate_field] / 1_000_000
            for component, rate_field in rate_fields.items()
        )
        gross_credits = (
            independently_priced * oracle["github_ai_credits_per_usd"]
        )
        included_credits = (
            commercial_input["seat_count"]
            * oracle["included_ai_credits_per_user_month"]
        )
        additional_credits = max(0, gross_credits - included_credits)
        additional_cost = (
            additional_credits / oracle["github_ai_credits_per_usd"]
        )
        assert result["commercial"]["model_usage_cost_usd"] == pytest.approx(
            independently_priced
        )
        assert result["commercial"]["gross_github_ai_credits"] == pytest.approx(
            gross_credits
        )
        assert result["commercial"][
            "additional_github_ai_credits"
        ] == pytest.approx(additional_credits)
        assert result["commercial"]["modeled_total_cost_usd"] == pytest.approx(
            commercial_input["fixed_seat_cost_usd"] + additional_cost
        )

    purchase = result.get("purchase")
    if purchase is not None:
        portfolio = commercial_input["purchase_portfolio"]
        required = (
            result["commercial"].get("mean_copilot_credits")
            if "mean_copilot_credits" in result["commercial"]
            else result["commercial"]["total_copilot_credits"]
        )
        drawdown = min(required, portfolio["committed_credits"])
        unfunded = max(0, required - drawdown)
        incremental = (
            unfunded * portfolio["payg_rate_usd_per_credit"]
            if portfolio["payg_enabled"]
            else 0
        )
        assert purchase["commitment_drawdown_credits"] == pytest.approx(drawdown)
        assert purchase["unfunded_credits"] == pytest.approx(unfunded)
        assert purchase["retail_cost_usd"] == pytest.approx(
            required * portfolio["payg_rate_usd_per_credit"]
        )
        assert purchase["amortized_cost_usd"] == pytest.approx(
            portfolio["committed_cost_usd"]
            + portfolio["fixed_seat_cost_usd"]
            + incremental
        )

    if route in {"copilot_studio_byom", "foundry_work_iq"}:
        assert result["hybrid"]["total_usd"] == pytest.approx(
            result["token_subforecast"]["cost_usd"]
            + result["purchase"]["amortized_cost_usd"] * 12
        )
        assert result["hybrid"]["billing_period"] == "annual"


def test_nine_case_fixture_covers_every_catalog_route_once():
    assert FIXTURE["schema_version"] == "1.0"
    assert len(CASES) == 9
    assert {case["route"] for case in CASES} == {
        "included",
        "cowork",
        "agent_builder",
        "copilot_studio",
        "work_iq",
        "foundry",
        "github_copilot",
        "copilot_studio_byom",
        "foundry_work_iq",
    }


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case_id"])
def test_plan_experience_has_correct_end_to_end_arithmetic(
    case, tmp_path, monkeypatch
):
    analyze_calls: list[str] = []
    predict_calls: list[str] = []

    def analyze(_self, description):
        analyze_calls.append(description)
        return _analysis()

    def predict(_self, description, parameters):
        predict_calls.append(description)
        return _token_result(description, parameters)

    monkeypatch.setattr(plan_studio, "PLAN_STORE_PATH", tmp_path / "plans")
    monkeypatch.setattr(plan_studio, "REPORT_STORE_PATH", tmp_path / "reports")
    monkeypatch.setattr(plan_studio.McpPredictorClient, "analyze", analyze)
    monkeypatch.setattr(plan_studio.McpPredictorClient, "predict", predict)

    server = ThreadingHTTPServer(("127.0.0.1", 0), plan_studio.PlanStudioHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port)
    try:
        status, report = _post(
            connection,
            "/api/reports",
            {"title": f"Nine-case proof: {case['case_id']}"},
        )
        assert status == 201
        parameters = {
            "route": case["route"],
            **case["parameters"],
        }
        if case["route"] in MODEL_ROUTES:
            parameters.update(
                {
                    "model": "gpt-4.1",
                    "provider": "azure_openai",
                    "analysis_confirmed": True,
                    "confirmed_profile": {
                        "agent_pattern": "single_call",
                        "multi_agent_count": 1,
                        "modalities": ["text"],
                        "tools": [],
                    },
                }
            )
        status, result = _post(
            connection,
            "/api/plan",
            {
                "report_id": report["report_id"],
                "description": case["description"],
                "parameters": parameters,
            },
        )

        assert status == 201, case["case_id"]
        assert result["status"] == "complete", case["case_id"]
        assert result["route"]["route_id"] == case["route"]
        assert result["meter_stack"]["route_id"] == case["route"]
        assert result["meter_stack"]["version"] == "consumption-models.v1"
        _assert_expected(result, case["expected"])
        _assert_independent_arithmetic(case, result)

        receipt = PlanStore(tmp_path / "plans").get_receipt(result["plan_id"])
        assert receipt["schema_version"] == "4.0"
        assert receipt["content_hash"] == result["receipt_hash"]
        assert receipt["meter_stack"] == result["meter_stack"]
        assert receipt["commercial"] == result["commercial"]
        assert receipt["prediction"] == result["prediction"]

        expected_calls = [case["description"]] if case["route"] in MODEL_ROUTES else []
        assert analyze_calls == expected_calls
        assert predict_calls == expected_calls
    finally:
        server.shutdown()
        thread.join()
        connection.close()
