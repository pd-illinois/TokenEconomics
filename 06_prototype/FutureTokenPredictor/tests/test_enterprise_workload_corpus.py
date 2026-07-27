"""Versioned enterprise workload semantic regression contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from future_token_predictor.classifier import analyze_workload, classify
from future_token_predictor.predictor import predict


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "enterprise_workloads.v1.json"
CLASSIFIER_KEYS = (
    "CLASSIFIER_API_KEY",
    "CLASSIFIER_ENDPOINT",
    "CLASSIFIER_MODEL",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_CLASSIFIER_DEPLOYMENT",
    "OPENAI_API_KEY",
)


@pytest.fixture(scope="module")
def corpus() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_enterprise_corpus_is_complete_and_reviewable(corpus):
    assert corpus["schema_version"] == "1.0"
    assert corpus["corpus_version"] == "2026-07-24.1"
    assert len(corpus["cases"]) == 34
    assert len({case["id"] for case in corpus["cases"]}) == 34

    for case in corpus["cases"]:
        assert case["description"].startswith(f"{case['title']}:")
        assert case["expected_topology"] in {
            "single_call", "rag_pipeline", "react_agent", "workflow",
            "multi_agent", "code_exec",
        }
        assert case["minimum_modalities"]
        assert isinstance(case["minimum_tools"], list)
        assert case["agent_count"]["minimum"] >= 1
        assert case["agent_count"]["source"] in {
            "explicit", "inferred_roles", "defaulted", "user_confirmed",
        }
        assert len(case["rationale"].strip()) >= 20


def test_workload_analysis_is_versioned_and_evidence_backed(monkeypatch):
    for key in CLASSIFIER_KEYS:
        monkeypatch.delenv(key, raising=False)

    analysis = analyze_workload(
        "Research agents gather evidence, writer agents draft, and reviewer "
        "agents validate the report."
    )

    assert analysis.schema_version == "1.0"
    assert analysis.rule_set_version
    assert len(analysis.description_hash) == 64
    assert analysis.topology.selected.value == "multi_agent"
    assert analysis.topology.confidence in {"high", "medium", "low"}
    assert analysis.topology.evidence
    assert analysis.agent_count.value == 3
    assert analysis.agent_count.source == "inferred_roles"
    assert analysis.agent_count.evidence


def test_conflicting_autonomous_role_signals_expose_alternative(monkeypatch):
    for key in CLASSIFIER_KEYS:
        monkeypatch.delenv(key, raising=False)

    analysis = analyze_workload(
        "Autonomous research agents and review agents collaborate continuously, "
        "adapt their plan, and validate results."
    )

    assert analysis.topology.selected.value == "multi_agent"
    assert "react_agent" in [item.value for item in analysis.topology.alternatives]
    assert analysis.clarifications


def test_analysis_exposes_explicit_quantities_and_field_evidence(monkeypatch):
    for key in CLASSIFIER_KEYS:
        monkeypatch.delenv(key, raising=False)

    analysis = analyze_workload(
        "A six-step workflow reviews 3 documents of 12 pages each, analyzes "
        "4 scanned images, processes 90 seconds of audio, and performs 2 retrieval searches."
    )

    assert analysis.quantities["workflow_steps"].value == 6
    assert analysis.quantities["workflow_steps"].source == "explicit"
    assert analysis.quantities["document_count"].value == 3
    assert analysis.quantities["pages_per_document"].value == 12
    assert analysis.quantities["image_count"].value == 4
    assert analysis.quantities["audio_duration_seconds"].value == 90
    assert analysis.quantities["searches_per_call"].value == 2
    assert all(item.source == "explicit" for item in analysis.quantities.values())
    assert analysis.modality_evidence["document"]
    assert analysis.modality_evidence["image_input"]
    assert analysis.modality_evidence["audio_input"]
    assert analysis.tool_evidence["file_search"]


def test_analysis_exposes_cost_material_quantity_defaults(monkeypatch):
    for key in CLASSIFIER_KEYS:
        monkeypatch.delenv(key, raising=False)

    analysis = analyze_workload(
        "A workflow analyzes scanned contract documents and recorded audio "
        "using retrieval before generating a report."
    )

    assert analysis.quantities["document_count"].source == "defaulted"
    assert analysis.quantities["pages_per_document"].source == "defaulted"
    assert analysis.quantities["image_count"].source == "defaulted"
    assert analysis.quantities["audio_duration_seconds"].source == "defaulted"
    assert analysis.quantities["searches_per_call"].source == "defaulted"
    assert analysis.assumptions
    assert any("quantity" in item.lower() for item in analysis.clarifications)


@pytest.mark.parametrize("case_index", range(34))
def test_enterprise_corpus_topology_and_agent_count(
    corpus, case_index, monkeypatch
):
    for key in CLASSIFIER_KEYS:
        monkeypatch.delenv(key, raising=False)

    case = corpus["cases"][case_index]
    profile = classify(case["description"])

    assert profile.agent_pattern.value == case["expected_topology"], case["id"]
    assert profile.multi_agent_count >= case["agent_count"]["minimum"], case["id"]
    actual_modalities = {item.value for item in profile.modalities}
    actual_tools = {item.value for item in profile.tools}
    assert set(case["minimum_modalities"]) <= actual_modalities, case["id"]
    assert set(case["minimum_tools"]) <= actual_tools, case["id"]


@pytest.mark.parametrize("case_index", range(34))
def test_enterprise_corpus_prediction_arithmetic(corpus, case_index, monkeypatch):
    for key in CLASSIFIER_KEYS:
        monkeypatch.delenv(key, raising=False)

    case = corpus["cases"][case_index]
    result = predict(
        description=case["description"],
        enable_tier2=False,
        enable_tier3=False,
    )

    expected_archetype = {
        "single_call": "SingleCall_TextOnly",
        "rag_pipeline": "RAG_Pipeline",
        "react_agent": "ReAct_Agent",
        "workflow": "Workflow",
        "multi_agent": "MultiAgent",
        "code_exec": "CodeExecution_Agent",
    }[case["expected_topology"]]
    assert result.archetype == expected_archetype, case["id"]
    assert result.tokens_per_call.total > 0, case["id"]
    assert result.tokens_p5 <= result.tokens_p50 <= result.tokens_p95, case["id"]
    assert result.cost_per_call.ci_95_low <= result.cost_per_call.mean, case["id"]
    assert result.cost_per_call.mean <= result.cost_per_call.ci_95_high, case["id"]
    assert result.calculation_trace["cost_per_call"]["result_usd"] == pytest.approx(
        result.cost_per_call.mean, abs=1e-6
    ), case["id"]


@pytest.mark.parametrize(
    ("model", "provider"),
    [
        ("gpt-4.1", "openai"),
        ("claude-sonnet-4", "anthropic"),
        ("gemini-2.5-flash", "google"),
    ],
)
def test_representative_enterprise_model_matrix(corpus, model, provider, monkeypatch):
    for key in CLASSIFIER_KEYS:
        monkeypatch.delenv(key, raising=False)

    description = corpus["cases"][13]["description"]
    profile = classify(description)
    profile.model = model
    profile.provider = type(profile.provider)(provider)
    result = predict(profile=profile, enable_tier2=False, enable_tier3=False)

    assert result.archetype == "MultiAgent"
    assert result.model == model
    assert result.provider == provider
    assert result.pricing_verified is True
    assert result.cost_per_call.mean > 0
