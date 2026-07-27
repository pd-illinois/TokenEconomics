from future_token_predictor.model_catalog import build_model_catalog


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