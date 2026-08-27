from __future__ import annotations

from pathlib import Path
import sys

from costgov.model_release import find_released_offering, load_model_release


ROOT = Path(__file__).resolve().parents[1]
PREDICTOR_SRC = ROOT / "FutureTokenPredictor" / "src"
sys.path.insert(0, str(PREDICTOR_SRC))

from future_token_predictor.models.schemas import Provider  # noqa: E402
from future_token_predictor.providers import get_provider  # noqa: E402


def test_foundry_model_release_is_versioned_cited_and_provider_limited():
    catalog = load_model_release(ROOT)

    assert catalog["schema_version"] == "foundry-model-release.v2"
    assert catalog["release_version"] == "2026-08-25.2"
    assert catalog["default_key"] == "azure_openai:gpt-5.6-luna"
    assert {item["provider"] for item in catalog["offerings"]} == {
        "azure_openai", "anthropic",
    }
    models = {item["model"] for item in catalog["offerings"]}
    assert len(models) >= 75
    assert {
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-chat-latest",
        "gpt-5.5",
        "gpt-5.4-pro",
        "gpt-5.3-codex",
        "gpt-5.2-chat",
        "gpt-5.1-codex-max",
        "gpt-5-pro",
        "gpt-oss-120b",
        "gpt-4.1",
        "o3-pro",
        "codex-mini",
        "gpt-4o",
        "gpt-4.5-preview",
        "computer-use-preview",
        "text-embedding-3-large",
        "gpt-image-2",
        "dall-e-3",
        "sora-2",
        "gpt-audio-1.5",
        "gpt-realtime-2.1-mini",
        "gpt-live-transcribe",
        "whisper",
        "gpt-4o-mini-tts",
        "claude-mythos-5",
        "claude-fable-5",
        "claude-mythos-preview",
        "claude-opus-5",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-opus-4-5",
        "claude-sonnet-5",
        "claude-sonnet-4-6",
        "claude-sonnet-4-5",
        "claude-haiku-4-5",
    }.issubset(models)
    for offering in catalog["offerings"]:
        assert offering["modality_group"] in {
            "text", "embeddings", "image", "video", "audio", "specialized",
        }
        assert offering["evidence"]["status"] in {"verified", "unavailable"}
        assert offering["pricing_url"].startswith("https://")
        assert offering["model_catalog_url"].startswith("https://")
        if offering["selector_eligible"]:
            assert offering["coordinator_capable"] is True
            assert offering["evidence"]["status"] == "verified"
            assert offering["pricing"]["input"] >= 0
            assert offering["pricing"]["output"] >= 0


def test_foundry_model_release_pins_exact_luna_and_sonnet_prices():
    catalog = load_model_release(ROOT)

    luna = find_released_offering(catalog, "azure_openai", "gpt-5.6-luna")
    assert luna["pricing"] == {
        "input": 0.2,
        "cached_input": 0.02,
        "cache_write": 0.25,
        "output": 1.2,
    }
    sonnet = find_released_offering(catalog, "anthropic", "claude-sonnet-5")
    assert sonnet["pricing"] == {
        "input": 2.0,
        "cached_input": 0.2,
        "cache_write_5m": 2.5,
        "cache_write_1h": 4.0,
        "output": 10.0,
    }


def test_every_selector_model_resolves_to_exact_predictor_pricing():
    catalog = load_model_release(ROOT)

    for offering in catalog["offerings"]:
        if not offering["selector_eligible"]:
            continue
        provider = get_provider(Provider(offering["provider"]))
        assert provider.get_model_info(offering["model"]) is not None, offering["key"]
        pricing = provider.get_pricing(offering["model"])
        assert pricing is not None, offering["key"]
        assert pricing.to_dict() == offering["pricing"], offering["key"]


def test_foundry_release_preserves_modality_specific_prices_and_unavailable_status():
    catalog = load_model_release(ROOT)

    sora = find_released_offering(catalog, "azure_openai", "sora-2")
    assert sora["selector_eligible"] is False
    assert sora["pricing"] == {
        "standard_per_second": 0.1,
        "pro_per_second": 0.3,
        "pro_high_resolution_per_second": 0.5,
    }
    dalle = find_released_offering(catalog, "azure_openai", "dall-e-3")
    assert dalle["pricing"]["standard_low_resolution_per_100_images"] == 4.0
    mythos_preview = find_released_offering(
        catalog, "anthropic", "claude-mythos-preview"
    )
    assert mythos_preview["coordinator_capable"] is True
    assert mythos_preview["selector_eligible"] is False
    assert mythos_preview["pricing"] == {}
    assert mythos_preview["evidence"]["status"] == "unavailable"


def test_studio_rehydrates_route_before_rendering_an_immutable_receipt():
    html = (ROOT / "studio.html").read_text(encoding="utf-8")

    open_report = html[html.index("async function openReport"):html.index(
        'document.getElementById("save-report-button")'
    )]
    assert open_report.index("await loadConsumptionCatalog()") < open_report.index(
        "await openPlan("
    )
    open_plan_start = html.index("async function openPlan")
    open_plan = html[open_plan_start:html.index(
        'document.getElementById("plan-button")', open_plan_start
    )]
    assert "selectRouteForPlan(receipt);" in open_plan
    assert open_plan.index("selectRouteForPlan(receipt);") < open_plan.index(
        "renderPlan(state.plan);"
    )


def test_studio_rehydrates_all_saved_plan_inputs_before_rendering():
    html = (ROOT / "studio.html").read_text(encoding="utf-8")
    restore_start = html.index("function restorePlanInputs")
    restore = html[restore_start:html.index("async function openPlan", restore_start)]

    assert 'document.getElementById("plan-prompt")' in restore
    assert "plan.intake || {}" in restore
    assert "intake.commercial || {}" in restore
    assert "restoreModelSelection(intake);" in restore
    assert "restoreAgentModels(intake.agent_models || []);" in restore
    for field_id in {
        "plan-seat-count",
        "plan-seat-price",
        "plan-included-environment",
        "plan-studio-meter",
        "plan-studio-quantity",
        "plan-studio-audience",
        "plan-studio-license",
        "plan-studio-channel",
        "plan-studio-trigger",
        "plan-studio-authenticated",
        "plan-ms-committed-credits",
        "plan-ms-committed-cost",
        "plan-ms-fixed-cost",
        "plan-ms-payg",
        "plan-ms-payg-rate",
        "plan-ms-environment",
        "plan-scenario-segment",
        "plan-scenario-tasks",
        "plan-scenario-seed",
        "plan-scenario-p50",
        "plan-scenario-p95",
        "plan-scenario-fixed-cost",
        "plan-scenario-committed-credits",
        "plan-scenario-committed-cost",
        "plan-scenario-payg-rate",
        "plan-scenario-payg",
        "plan-github-plan",
        "plan-github-model",
        "plan-github-seats",
        "plan-github-input",
        "plan-github-cached",
        "plan-github-cache-write",
        "plan-github-output",
        "plan-github-max-input-request",
        "plan-github-seat-cost",
        "plan-github-overage",
    }:
        assert f'"{field_id}"' in restore

    open_plan_start = html.index("async function openPlan")
    open_plan = html[open_plan_start:html.index(
        'document.getElementById("plan-button")', open_plan_start
    )]
    assert "restorePlanInputs(receipt);" in open_plan
    assert open_plan.index("restorePlanInputs(receipt);") < open_plan.index(
        "renderPlan(state.plan);"
    )


def test_studio_groups_full_catalog_but_limits_coordinator_choices():
    html = (ROOT / "studio.html").read_text(encoding="utf-8")
    load_start = html.index("async function loadModelCatalog")
    load = html[load_start:html.index("function addAgentModelRow", load_start)]

    assert "offering.selector_eligible" in load
    assert "offering.modality_group" in load
    assert "offering.coordinator_capable" in load
    assert "offering.evidence?.reason" in load
    assert "renderFoundryModelCatalog()" in load
    assert 'id="foundry-model-catalog"' in html
    assert "Only coordinator-capable models can be selected for forecasts." in html
