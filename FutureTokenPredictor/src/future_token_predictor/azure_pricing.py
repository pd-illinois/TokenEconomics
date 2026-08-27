"""Azure Retail Prices API client with local caching.

Queries https://prices.azure.com/api/retail/prices for Azure OpenAI pricing.
No authentication required — this is a public endpoint.
Caches results locally to minimize API calls and provide offline fallback.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import httpx

from future_token_predictor.models.schemas import DeploymentRegion

# --- Constants ---

AZURE_PRICES_URL = "https://prices.azure.com/api/retail/prices"
CACHE_TTL_SECONDS = 86400  # 24 hours
CACHE_DIR = Path.home() / ".cache" / "future_token_predictor"
CACHE_FILE = CACHE_DIR / "azure_openai_prices.json"


# Offline fallback pricing (May 2026, per 1M tokens, USD)
FALLBACK_PRICING: dict[str, dict[str, float]] = {
    "gpt-4.1": {
        "input": 2.00,
        "cached_input": 0.50,
        "output": 8.00,
    },
    "gpt-4.1-mini": {
        "input": 0.40,
        "cached_input": 0.10,
        "output": 1.60,
    },
    "gpt-4.1-nano": {
        "input": 0.10,
        "cached_input": 0.03,
        "output": 0.40,
    },
    "gpt-4o": {
        "input": 2.50,
        "cached_input": 1.25,
        "output": 10.00,
    },
    "gpt-4o-mini": {
        "input": 0.15,
        "cached_input": 0.075,
        "output": 0.60,
    },
    "gpt-5": {
        "input": 1.25,
        "cached_input": 0.13,
        "output": 10.00,
    },
    "o3": {
        "input": 2.00,
        "cached_input": 0.50,
        "output": 8.00,
    },
    "o4-mini": {
        "input": 1.10,
        "cached_input": 0.28,
        "output": 4.40,
    },
    "gpt-image-1": {
        "input": 5.00,
        "image_input": 10.00,
        "output": 40.00,
    },
    "gpt-4o-audio": {
        "input": 2.50,
        "audio_input": 40.00,
        "output": 10.00,
        "audio_output": 80.00,
    },
    "computer-use-preview": {
        "input": 3.00,
        "output": 12.00,
    },
}

# Deployment type price multipliers (Global=1x baseline, DataZone/Regional cost more)
DEPLOYMENT_MULTIPLIERS: dict[DeploymentRegion, float] = {
    DeploymentRegion.GLOBAL: 1.0,
    DeploymentRegion.DATA_ZONE: 1.0,  # Same as Global for most models
    DeploymentRegion.REGIONAL: 1.0,   # Regional may differ; update from API
}

# Tool pricing (fixed, not model-dependent)
TOOL_PRICING = {
    "file_search_per_1k_calls": 2.50,
    "file_search_storage_per_gb_day": 0.11,
    "code_interpreter_per_session": 0.033,
}


class AzurePricingClient:
    """Client for Azure Retail Prices API with caching."""

    def __init__(self, cache_ttl: int = CACHE_TTL_SECONDS):
        self._cache_ttl = cache_ttl
        self._prices: Optional[dict[str, dict[str, float]]] = None
        self._timestamp: Optional[float] = None

    def get_model_pricing(
        self, model: str, deployment_region: DeploymentRegion = DeploymentRegion.GLOBAL
    ) -> dict[str, float]:
        """Get pricing for a specific model and deployment region.

        Returns dict with keys like 'input', 'output', 'cached_input', etc.
        Values are USD per 1M tokens.
        """
        prices = self._get_cached_or_fetch()
        multiplier = DEPLOYMENT_MULTIPLIERS.get(deployment_region, 1.0)

        model_prices = prices.get(model, FALLBACK_PRICING.get(model, {}))
        if not model_prices:
            # Unknown model — log warning instead of silently using gpt-4.1.
            # Model validation should catch this upstream, but if we get here
            # it means the caller bypassed validation.
            import logging
            logging.getLogger(__name__).warning(
                "No pricing found for model '%s'. "
                "Model validation should have caught this upstream.",
                model,
            )
            model_prices = prices.get("gpt-4.1", FALLBACK_PRICING["gpt-4.1"])

        return {k: v * multiplier for k, v in model_prices.items()}

    def get_tool_pricing(self) -> dict[str, float]:
        """Get non-token tool pricing."""
        return TOOL_PRICING.copy()

    @property
    def pricing_timestamp(self) -> Optional[str]:
        """ISO timestamp of when pricing was last fetched."""
        if self._timestamp:
            from datetime import datetime, timezone
            return datetime.fromtimestamp(self._timestamp, tz=timezone.utc).isoformat()
        return None

    @property
    def is_live(self) -> bool:
        """Whether pricing came from live API (vs cache/fallback)."""
        return self._timestamp is not None

    def _get_cached_or_fetch(self) -> dict[str, dict[str, float]]:
        """Load from memory cache, file cache, or fetch from API."""
        # Memory cache
        if self._prices and self._timestamp:
            if time.time() - self._timestamp < self._cache_ttl:
                return self._prices

        # File cache
        if CACHE_FILE.exists():
            try:
                data = json.loads(CACHE_FILE.read_text())
                if time.time() - data.get("timestamp", 0) < self._cache_ttl:
                    self._prices = data["prices"]
                    self._timestamp = data["timestamp"]
                    return self._prices
            except (json.JSONDecodeError, KeyError):
                pass

        # Fetch from API
        try:
            prices = self._fetch_from_api()
            self._prices = prices
            self._timestamp = time.time()
            self._save_cache()
            return prices
        except Exception:
            # Offline fallback — stamp timestamp so the next call doesn't
            # immediately retry the 30s network fetch.
            self._prices = FALLBACK_PRICING.copy()
            self._timestamp = time.time()
            return self._prices

    def _fetch_from_api(self) -> dict[str, dict[str, float]]:
        """Query Azure Retail Prices API for Azure OpenAI pricing."""
        prices: dict[str, dict[str, float]] = {}
        filter_expr = "serviceName eq 'Azure OpenAI' and priceType eq 'Consumption'"
        next_page: Optional[str] = None
        url = AZURE_PRICES_URL

        with httpx.Client(timeout=30.0) as client:
            while True:
                params = {"$filter": filter_expr}
                if next_page:
                    # Next page URL already includes params
                    resp = client.get(next_page)
                else:
                    resp = client.get(url, params=params)

                resp.raise_for_status()
                data = resp.json()

                for item in data.get("Items", []):
                    self._process_price_item(item, prices)

                next_page = data.get("NextPageLink")
                if not next_page:
                    break

        # Merge with fallback for any missing models
        merged = FALLBACK_PRICING.copy()
        merged.update(prices)
        return merged

    def _process_price_item(
        self, item: dict, prices: dict[str, dict[str, float]]
    ) -> None:
        """Process a single price item from the API response."""
        meter_name = item.get("meterName", "")
        product_name = item.get("productName", "")
        unit_price = item.get("unitPrice", 0)
        sku_name = item.get("skuName", "")

        if unit_price == 0:
            return

        # Extract model name from product/sku
        model = self._extract_model_name(product_name, sku_name)
        if not model:
            return

        if model not in prices:
            prices[model] = {}

        # Classify meter type
        meter_lower = meter_name.lower()
        if "cached" in meter_lower and "input" in meter_lower:
            prices[model]["cached_input"] = unit_price
        elif "input" in meter_lower and "audio" in meter_lower:
            prices[model]["audio_input"] = unit_price
        elif "input" in meter_lower and "image" in meter_lower:
            prices[model]["image_input"] = unit_price
        elif "input" in meter_lower:
            prices[model]["input"] = unit_price
        elif "output" in meter_lower and "audio" in meter_lower:
            prices[model]["audio_output"] = unit_price
        elif "output" in meter_lower and "image" in meter_lower:
            prices[model]["output"] = unit_price  # Image output tokens
        elif "output" in meter_lower:
            prices[model]["output"] = unit_price

    def _extract_model_name(self, product_name: str, sku_name: str) -> Optional[str]:
        """Extract normalized model name from Azure pricing fields."""
        # Common patterns in Azure pricing API
        name_lower = (product_name + " " + sku_name).lower()

        model_patterns = [
            ("gpt-4.1-nano", ["gpt-4.1-nano", "gpt-4.1 nano"]),
            ("gpt-4.1-mini", ["gpt-4.1-mini", "gpt-4.1 mini"]),
            ("gpt-4.1", ["gpt-4.1"]),
            ("gpt-4o-mini", ["gpt-4o-mini", "gpt-4o mini"]),
            ("gpt-4o-audio", ["gpt-4o-audio", "gpt-4o audio"]),
            ("gpt-4o", ["gpt-4o"]),
            ("gpt-5", ["gpt-5"]),
            ("gpt-image-1", ["gpt-image", "dall-e"]),
            ("o4-mini", ["o4-mini", "o4 mini"]),
            ("o3", ["o3"]),
            ("computer-use-preview", ["computer use"]),
        ]

        for model_name, patterns in model_patterns:
            for pattern in patterns:
                if pattern in name_lower:
                    return model_name

        return None

    def _save_cache(self) -> None:
        """Persist pricing to file cache."""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "timestamp": self._timestamp,
            "prices": self._prices,
        }
        CACHE_FILE.write_text(json.dumps(data, indent=2))


# Module-level singleton
_client: Optional[AzurePricingClient] = None


def get_pricing_client() -> AzurePricingClient:
    """Get or create the module-level pricing client."""
    global _client
    if _client is None:
        _client = AzurePricingClient()
    return _client
