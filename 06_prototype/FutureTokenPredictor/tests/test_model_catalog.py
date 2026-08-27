from future_token_predictor.model_catalog import build_model_catalog
from future_token_predictor.models.schemas import Provider
from future_token_predictor.providers import get_provider


def test_catalog_exposes_only_priced_provider_model_offerings():
    catalog = build_model_catalog()

    azure_gpt41 = next(
        offering for offering in catalog["offerings"]
        if offering["provider"] == "azure_openai" and offering["model"] == "gpt-4.1"
    )
    assert azure_gpt41["pricing"]["input"] == 2.0
    assert azure_gpt41["pricing"]["output"] == 8.0
    assert azure_gpt41["context_window"] == 1_048_576
    assert azure_gpt41["key"] == "azure_openai:gpt-4.1"
    assert azure_gpt41["provider_name"] == "Azure OpenAI"
    assert all(offering["pricing"] for offering in catalog["offerings"])
    assert all(item["reason"] == "missing_pricing" for item in catalog["unavailable"])


def test_foundry_release_models_have_exact_current_provider_rates():
    azure_openai = get_provider(Provider.AZURE_OPENAI)
    anthropic = get_provider(Provider.ANTHROPIC)

    assert azure_openai.get_pricing("gpt-5.6-sol").to_dict() == {
        "input": 5.0,
        "output": 30.0,
        "cached_input": 0.5,
        "cache_write": 6.25,
    }
    assert azure_openai.get_pricing("gpt-5.6-terra").to_dict() == {
        "input": 2.0,
        "output": 12.0,
        "cached_input": 0.2,
        "cache_write": 2.5,
    }
    assert azure_openai.get_pricing("gpt-5.6-luna").to_dict() == {
        "input": 0.2,
        "output": 1.2,
        "cached_input": 0.02,
        "cache_write": 0.25,
    }
    assert anthropic.get_pricing("claude-opus-5").to_dict() == {
        "input": 5.0,
        "output": 25.0,
        "cached_input": 0.5,
        "cache_write_5m": 6.25,
        "cache_write_1h": 10.0,
    }
    assert anthropic.get_pricing("claude-sonnet-5").to_dict() == {
        "input": 2.0,
        "output": 10.0,
        "cached_input": 0.2,
        "cache_write_5m": 2.5,
        "cache_write_1h": 4.0,
    }
    assert anthropic.get_pricing("claude-haiku-4-5").to_dict() == {
        "input": 1.0,
        "output": 5.0,
        "cached_input": 0.1,
        "cache_write_5m": 1.25,
        "cache_write_1h": 2.0,
    }