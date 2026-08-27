from __future__ import annotations

import importlib.util
from pathlib import Path

from costgov.mcp_prediction import McpPredictorClient


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_te003_live_test.py"


def _module():
    spec = importlib.util.spec_from_file_location("run_te003_live_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_te003_profile_pins_deployed_rag_without_overriding_general_plan_models():
    module = _module()
    parameters = module.build_test_parameters()

    assert parameters["model"] == "gpt-5-6-luna"
    assert parameters["users"] * parameters["calls_per_user_per_day"] == 500
    assert parameters["confirmed_profile"]["agent_pattern"] == "rag_pipeline"
    assert parameters["confirmed_profile"]["searches_per_call"] == 1
    assert module._parser().parse_args([]).policy_label == "te003-live-v2"


def test_te003_selected_controls_override_conflicting_description_values():
    parameters = _module().build_test_parameters()
    arguments, intake = McpPredictorClient._build_arguments(
        "A GPT-5.4 assistant for 2,000 users makes 9,000 requests per day.",
        parameters,
    )

    assert arguments["model"] == "gpt-5-6-luna"
    assert arguments["users"] == 100
    assert arguments["calls_per_user_per_day"] == 5
    assert intake["agent_pattern"] == "rag_pipeline"
