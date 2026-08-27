"""Live model discovery from provider APIs.

Fetches model lists and capabilities from provider APIs when API keys are
available. Falls back to static catalogs when offline or unauthenticated.

Supported live sources:
- OpenAI:    GET /v1/models         (needs OPENAI_API_KEY)
- Anthropic: GET /v1/models         (needs ANTHROPIC_API_KEY)
- Google:    GET /v1beta/models      (needs GOOGLE_API_KEY / GEMINI_API_KEY)
- Mistral:   GET /v1/models         (needs MISTRAL_API_KEY)
- Azure:     Retail Prices REST API  (free, no auth — pricing only)

Results are cached to disk (~/.future_token_predictor/model_cache/) with a
configurable TTL (default 24 hours).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import httpx

from future_token_predictor.providers.base import ModelInfo, PricingTier

logger = logging.getLogger(__name__)

# --- Configuration ---

CACHE_DIR = Path.home() / ".future_token_predictor" / "model_cache"
CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 hours
HTTP_TIMEOUT = 15.0  # seconds


@dataclass
class LiveModelEntry:
    """A model discovered from a provider API."""

    model_id: str
    provider: str
    context_window: int | None = None
    max_output_tokens: int | None = None
    supports_vision: bool = False
    supports_audio: bool = False
    supports_reasoning: bool = False
    supports_caching: bool = False
    display_name: str | None = None
    owned_by: str | None = None

    def to_model_info(self, fallback: ModelInfo | None = None) -> ModelInfo:
        """Convert to ModelInfo, filling gaps from a static fallback."""
        fb = fallback
        return ModelInfo(
            name=self.model_id,
            provider=self.provider,
            context_window=self.context_window or (fb.context_window if fb else 128_000),
            max_output_tokens=self.max_output_tokens or (fb.max_output_tokens if fb else 8_192),
            supports_vision=self.supports_vision or (fb.supports_vision if fb else False),
            supports_audio=self.supports_audio or (fb.supports_audio if fb else False),
            supports_reasoning=self.supports_reasoning or (fb.supports_reasoning if fb else False),
            supports_caching=self.supports_caching or (fb.supports_caching if fb else False),
            tokenizer=fb.tokenizer if fb else "o200k_base",
            reasoning_multiplier=fb.reasoning_multiplier if fb else 1.0,
        )


@dataclass
class LivePricingEntry:
    """Pricing discovered from Azure Retail Prices API."""

    model_id: str
    input_per_1m: float
    output_per_1m: float
    cached_input_per_1m: float | None = None
    region: str = "eastus"

    def to_pricing_tier(self) -> PricingTier:
        return PricingTier(
            input=self.input_per_1m,
            output=self.output_per_1m,
            cached_input=self.cached_input_per_1m,
        )


# ─── Disk cache ───────────────────────────────────────────────────────

def _cache_path(provider: str) -> Path:
    return CACHE_DIR / f"{provider}_models.json"


def _pricing_cache_path(region: str) -> Path:
    return CACHE_DIR / f"azure_pricing_{region}.json"


def _is_cache_fresh(path: Path, ttl: float = CACHE_TTL_SECONDS) -> bool:
    if not path.exists():
        return False
    age = time.time() - path.stat().st_mtime
    return age < ttl


def _read_cache(path: Path) -> list[dict] | None:
    if not _is_cache_fresh(path):
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_cache(path: Path, data: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ─── OpenAI model fetcher ────────────────────────────────────────────

# Models we care about (filter out fine-tunes, embeddings, whisper, etc.)
_OPENAI_RELEVANT_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")


def fetch_openai_models(
    api_key: str | None = None,
    base_url: str = "https://api.openai.com/v1",
) -> list[LiveModelEntry]:
    """Fetch model list from OpenAI API.

    Note: OpenAI's /v1/models endpoint returns model IDs and owned_by but
    does NOT return context_window or pricing. Capabilities must be merged
    from static catalog.
    """
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        logger.debug("No OPENAI_API_KEY set; skipping live model fetch")
        return []

    cache = _cache_path("openai")
    cached = _read_cache(cache)
    if cached is not None:
        return [LiveModelEntry(**e) for e in cached]

    try:
        resp = httpx.get(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.warning("Failed to fetch OpenAI models: %s", exc)
        return []

    entries: list[LiveModelEntry] = []
    for m in data:
        model_id: str = m.get("id", "")
        if not any(model_id.startswith(p) for p in _OPENAI_RELEVANT_PREFIXES):
            continue
        entries.append(LiveModelEntry(
            model_id=model_id,
            provider="openai",
            owned_by=m.get("owned_by"),
        ))

    _write_cache(cache, [asdict(e) for e in entries])
    logger.info("Fetched %d OpenAI models from API", len(entries))
    return entries


# ─── Anthropic model fetcher ─────────────────────────────────────────

def fetch_anthropic_models(
    api_key: str | None = None,
) -> list[LiveModelEntry]:
    """Fetch model list from Anthropic API.

    Returns model IDs, token limits, and capabilities (vision, thinking, etc.).
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        logger.debug("No ANTHROPIC_API_KEY set; skipping live model fetch")
        return []

    cache = _cache_path("anthropic")
    cached = _read_cache(cache)
    if cached is not None:
        return [LiveModelEntry(**e) for e in cached]

    try:
        resp = httpx.get(
            "https://api.anthropic.com/v1/models",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            },
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.warning("Failed to fetch Anthropic models: %s", exc)
        return []

    entries: list[LiveModelEntry] = []
    for m in data:
        model_id = m.get("id", "")
        caps = m.get("capabilities", {})
        entries.append(LiveModelEntry(
            model_id=model_id,
            provider="anthropic",
            context_window=m.get("max_input_tokens") or None,
            max_output_tokens=m.get("max_tokens") or None,
            supports_vision=caps.get("image_input", {}).get("supported", False),
            supports_reasoning=caps.get("thinking", {}).get("supported", False),
            display_name=m.get("display_name"),
        ))

    _write_cache(cache, [asdict(e) for e in entries])
    logger.info("Fetched %d Anthropic models from API", len(entries))
    return entries


# ─── Google Gemini model fetcher ─────────────────────────────────────

_GEMINI_RELEVANT = ("gemini",)


def fetch_google_models(
    api_key: str | None = None,
) -> list[LiveModelEntry]:
    """Fetch model list from Google Generative AI API.

    Returns model IDs, input/output token limits, and thinking support.
    """
    key = api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        logger.debug("No GOOGLE_API_KEY/GEMINI_API_KEY set; skipping live model fetch")
        return []

    cache = _cache_path("google")
    cached = _read_cache(cache)
    if cached is not None:
        return [LiveModelEntry(**e) for e in cached]

    entries: list[LiveModelEntry] = []
    next_page: str | None = None
    base = "https://generativelanguage.googleapis.com/v1beta/models"

    try:
        while True:
            params: dict[str, str] = {"key": key, "pageSize": "100"}
            if next_page:
                params["pageToken"] = next_page

            resp = httpx.get(base, params=params, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            body = resp.json()

            for m in body.get("models", []):
                name: str = m.get("name", "")
                base_id: str = m.get("baseModelId", name.removeprefix("models/"))
                if not any(base_id.startswith(p) for p in _GEMINI_RELEVANT):
                    continue
                entries.append(LiveModelEntry(
                    model_id=base_id,
                    provider="google",
                    context_window=m.get("inputTokenLimit"),
                    max_output_tokens=m.get("outputTokenLimit"),
                    supports_reasoning=m.get("thinking", False),
                    display_name=m.get("displayName"),
                ))

            next_page = body.get("nextPageToken")
            if not next_page:
                break
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.warning("Failed to fetch Google models: %s", exc)
        return []

    # Deduplicate by base model ID (API returns versioned variants)
    seen: set[str] = set()
    deduped: list[LiveModelEntry] = []
    for e in entries:
        if e.model_id not in seen:
            seen.add(e.model_id)
            deduped.append(e)
    entries = deduped

    _write_cache(cache, [asdict(e) for e in entries])
    logger.info("Fetched %d Google models from API", len(entries))
    return entries


# ─── Mistral model fetcher ───────────────────────────────────────────

def fetch_mistral_models(
    api_key: str | None = None,
) -> list[LiveModelEntry]:
    """Fetch model list from Mistral API."""
    key = api_key or os.environ.get("MISTRAL_API_KEY")
    if not key:
        logger.debug("No MISTRAL_API_KEY set; skipping live model fetch")
        return []

    cache = _cache_path("mistral")
    cached = _read_cache(cache)
    if cached is not None:
        return [LiveModelEntry(**e) for e in cached]

    try:
        resp = httpx.get(
            "https://api.mistral.ai/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.warning("Failed to fetch Mistral models: %s", exc)
        return []

    entries: list[LiveModelEntry] = []
    for m in data:
        model_id = m.get("id", "")
        caps = m.get("capabilities", {})
        entries.append(LiveModelEntry(
            model_id=model_id,
            provider="mistral",
            context_window=m.get("max_context_length"),
            max_output_tokens=m.get("max_output_tokens") or m.get("default_model_temperature"),
            supports_vision=caps.get("vision", False) if isinstance(caps, dict) else False,
            display_name=m.get("name"),
            owned_by=m.get("owned_by"),
        ))

    _write_cache(cache, [asdict(e) for e in entries])
    logger.info("Fetched %d Mistral models from API", len(entries))
    return entries


# ─── Azure Retail Prices API (pricing only, free, no auth) ───────────

_AZURE_OPENAI_MODEL_MAP = {
    # Azure meterName fragment → canonical model ID
    # GPT-5.5
    "gpt-5.5": "gpt-5.5",
    # GPT-5.4 family
    "gpt-5.4-pro": "gpt-5.4-pro",
    "gpt-5.4-mini": "gpt-5.4-mini",
    "gpt-5.4-nano": "gpt-5.4-nano",
    "gpt-5.4": "gpt-5.4",
    # GPT-5.3
    "gpt-5.3-codex": "gpt-5.3-codex",
    "gpt-5.3-chat": "gpt-5.3-chat",
    "gpt-5.3": "gpt-5.3-codex",
    # GPT-5.2
    "gpt-5.2-codex": "gpt-5.2-codex",
    "gpt-5.2-chat": "gpt-5.2-chat",
    "gpt-5.2": "gpt-5.2",
    # GPT-5.1
    "gpt-5.1-codex-max": "gpt-5.1-codex-max",
    "gpt-5.1-codex-mini": "gpt-5.1-codex-mini",
    "gpt-5.1-codex": "gpt-5.1-codex",
    "gpt-5.1-chat": "gpt-5.1-chat",
    "gpt-5.1": "gpt-5.1",
    # GPT-5 family
    "gpt-5-pro": "gpt-5-pro",
    "gpt-5-mini": "gpt-5-mini",
    "gpt-5-nano": "gpt-5-nano",
    "gpt-5-chat": "gpt-5-chat",
    "gpt-5-codex": "gpt-5-codex",
    "gpt-5": "gpt-5",
    # GPT-4.1 family
    "gpt-4.1-mini": "gpt-4.1-mini",
    "gpt-4.1-nano": "gpt-4.1-nano",
    "gpt-4.1": "gpt-4.1",
    # GPT-4o
    "gpt-4o-mini": "gpt-4o-mini",
    "gpt-4o": "gpt-4o",
    # o-series
    "o3-pro": "o3-pro",
    "o3-mini": "o3-mini",
    "o3": "o3",
    "o4-mini": "o4-mini",
    "o1-mini": "o1-mini",
    "o1": "o1",
    "codex-mini": "codex-mini",
    # Image
    "gpt-image-1.5": "gpt-image-1.5",
    "gpt-image-1-mini": "gpt-image-1-mini",
    "gpt-image-1": "gpt-image-1",
    "gpt-image-2": "gpt-image-2",
    # Audio
    "gpt-audio-1.5": "gpt-audio-1.5",
    "gpt-audio-mini": "gpt-audio-mini",
    "gpt-audio": "gpt-audio",
    "gpt-realtime-1.5": "gpt-realtime-1.5",
    "gpt-realtime-mini": "gpt-realtime-mini",
    "gpt-realtime": "gpt-realtime",
    # Open source
    "gpt-oss-120b": "gpt-oss-120b",
    "gpt-oss-20b": "gpt-oss-20b",
    # CUA
    "computer-use": "computer-use-preview",
}


def fetch_azure_pricing(
    region: str = "eastus",
) -> list[LivePricingEntry]:
    """Fetch Azure OpenAI pricing from the Azure Retail Prices API.

    This API is free and requires no authentication.
    Returns pricing per 1M tokens for input and output.
    """
    cache = _pricing_cache_path(region)
    cached = _read_cache(cache)
    if cached is not None:
        return [LivePricingEntry(**e) for e in cached]

    base_url = "https://prices.azure.com/api/retail/prices"
    odata_filter = (
        "serviceName eq 'Azure OpenAI Service' "
        f"and armRegionName eq '{region}' "
        "and priceType eq 'Consumption'"
    )

    all_items: list[dict] = []
    next_page: str | None = None

    try:
        while True:
            if next_page:
                resp = httpx.get(next_page, timeout=HTTP_TIMEOUT)
            else:
                resp = httpx.get(
                    base_url,
                    params={"$filter": odata_filter},
                    timeout=HTTP_TIMEOUT,
                )
            resp.raise_for_status()
            body = resp.json()
            all_items.extend(body.get("Items", []))
            next_page = body.get("NextPageLink")
            if not next_page:
                break
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.warning("Failed to fetch Azure pricing: %s", exc)
        return []

    # Parse meter names → pricing entries
    # Azure meters look like "GPT-4o Input Tokens" / "GPT-4o Output Tokens"
    # unitOfMeasure is typically "1K Tokens" → price is per 1K tokens
    model_prices: dict[str, dict[str, float]] = {}

    for item in all_items:
        meter_name: str = item.get("meterName", "").lower()
        product_name: str = item.get("productName", "").lower()
        unit_price: float = item.get("unitPrice", 0.0)
        unit_of_measure: str = item.get("unitOfMeasure", "")

        if unit_price <= 0:
            continue

        # Determine model from meter or product name
        matched_model: str | None = None
        for fragment, model_id in _AZURE_OPENAI_MODEL_MAP.items():
            if fragment.lower() in meter_name or fragment.lower() in product_name:
                matched_model = model_id
                break

        if not matched_model:
            continue

        if matched_model not in model_prices:
            model_prices[matched_model] = {}

        # Normalize to per-1M tokens
        # Azure typically reports per 1K tokens
        if "1k" in unit_of_measure.lower() or "1,000" in unit_of_measure:
            price_per_1m = unit_price * 1000
        elif "1m" in unit_of_measure.lower() or "1,000,000" in unit_of_measure:
            price_per_1m = unit_price
        else:
            # Assume per 1K tokens as default
            price_per_1m = unit_price * 1000

        if "input" in meter_name:
            if "cached" in meter_name:
                model_prices[matched_model]["cached_input"] = price_per_1m
            else:
                model_prices[matched_model]["input"] = price_per_1m
        elif "output" in meter_name:
            model_prices[matched_model]["output"] = price_per_1m

    entries: list[LivePricingEntry] = []
    for model_id, prices in model_prices.items():
        if "input" in prices and "output" in prices:
            entries.append(LivePricingEntry(
                model_id=model_id,
                input_per_1m=prices["input"],
                output_per_1m=prices["output"],
                cached_input_per_1m=prices.get("cached_input"),
                region=region,
            ))

    _write_cache(cache, [asdict(e) for e in entries])
    logger.info("Fetched Azure pricing for %d models in %s", len(entries), region)
    return entries


# ─── Unified interface ───────────────────────────────────────────────

_FETCHERS = {
    "openai": fetch_openai_models,
    "anthropic": fetch_anthropic_models,
    "google": fetch_google_models,
    "mistral": fetch_mistral_models,
}


def fetch_live_models(provider: str) -> list[LiveModelEntry]:
    """Fetch live models for a specific provider. Returns [] on failure."""
    fetcher = _FETCHERS.get(provider)
    if not fetcher:
        return []
    try:
        return fetcher()
    except Exception as exc:
        logger.warning("Unexpected error fetching %s models: %s", provider, exc)
        return []


def invalidate_cache(provider: str | None = None) -> None:
    """Delete cached model/pricing data to force a fresh fetch."""
    if provider:
        path = _cache_path(provider)
        if path.exists():
            path.unlink()
    else:
        # Clear all caches
        if CACHE_DIR.exists():
            for f in CACHE_DIR.iterdir():
                if f.is_file() and f.suffix == ".json":
                    f.unlink()
