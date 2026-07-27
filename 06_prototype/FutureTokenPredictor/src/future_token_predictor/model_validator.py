"""Model validation — verifies a model exists before predicting costs.

Cascading validation strategy:
1. Azure AI Foundry model catalog (always checked first, public API)
2. Static provider catalogs (instant, no network)
3. Live provider APIs via live_registry (needs API keys)
4. If not found → suggest closest family model from same provider
5. If nothing matches → return clear error, never assume
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class ValidationStatus(Enum):
    """Result of model validation."""
    VALID = "valid"                          # Exact model found
    VALID_LIVE = "valid_live"                # Found via live provider API
    VALID_CATALOG = "valid_catalog"          # Found in Azure AI model catalog
    SUBSTITUTED = "substituted"              # Not found; using closest family model
    NOT_FOUND = "not_found"                  # Not found anywhere


@dataclass
class ModelValidationResult:
    """Outcome of model validation."""
    status: ValidationStatus
    requested_model: str
    resolved_model: str
    provider: Optional[str] = None
    message: str = ""
    sources_checked: list[str] = field(default_factory=list)
    pricing_url: str = ""
    model_catalog_url: str = ""

    @property
    def is_valid(self) -> bool:
        return self.status in (
            ValidationStatus.VALID,
            ValidationStatus.VALID_LIVE,
            ValidationStatus.VALID_CATALOG,
        )

    @property
    def is_substituted(self) -> bool:
        return self.status == ValidationStatus.SUBSTITUTED

    @property
    def warning(self) -> Optional[str]:
        if self.status == ValidationStatus.SUBSTITUTED:
            return (
                f"Model '{self.requested_model}' was not found. "
                f"Using '{self.resolved_model}' as the closest available model "
                f"from the same provider family. Pricing may differ from the "
                f"actual model if it exists."
            )
        if self.status == ValidationStatus.NOT_FOUND:
            return (
                f"Model '{self.requested_model}' was not found in any provider "
                f"catalog (checked: {', '.join(self.sources_checked)}). "
                f"Cannot estimate costs for an unknown model."
            )
        return None


# ─── Family matching logic ─────────────────────────────────────────

# Maps provider → list of model families ordered by capability (highest first).
# Each family is (prefix/regex, list of known model IDs highest-to-lowest).
_PROVIDER_FAMILIES: dict[str, list[tuple[str, list[str]]]] = {
    "openai": [
        ("gpt-5", ["gpt-5.5", "gpt-5.4", "gpt-5.4-pro", "gpt-5.4-mini", "gpt-5.4-nano",
                    "gpt-5.3-codex", "gpt-5.3-chat", "gpt-5.2", "gpt-5.1", "gpt-5",
                    "gpt-5-pro", "gpt-5-mini", "gpt-5-nano"]),
        ("gpt-4", ["gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano", "gpt-4o", "gpt-4o-mini"]),
        ("o", ["o4-mini", "o3", "o3-pro", "o3-mini", "o1", "o1-mini"]),
    ],
    "azure_openai": [
        ("gpt-5", ["gpt-5.5", "gpt-5.4", "gpt-5.4-pro", "gpt-5.4-mini", "gpt-5.4-nano",
                    "gpt-5.3-codex", "gpt-5.3-chat", "gpt-5.2", "gpt-5.1", "gpt-5",
                    "gpt-5-pro", "gpt-5-mini", "gpt-5-nano"]),
        ("gpt-4", ["gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano", "gpt-4o", "gpt-4o-mini"]),
        ("o", ["o4-mini", "o3", "o3-pro", "o3-mini", "o1", "o1-mini"]),
    ],
    "anthropic": [
        ("claude-opus", ["claude-opus-4.7", "claude-opus-4.6", "claude-opus-4.5", "claude-opus-4.1", "claude-opus-4"]),
        ("claude-sonnet", ["claude-sonnet-4.6", "claude-sonnet-4.5", "claude-sonnet-4"]),
        ("claude-haiku", ["claude-haiku-4.5", "claude-haiku-3.5"]),
        ("claude", ["claude-opus-4.7", "claude-opus-4.6", "claude-opus-4.5", "claude-opus-4.1", "claude-opus-4",
                     "claude-sonnet-4.6", "claude-sonnet-4.5", "claude-sonnet-4", "claude-haiku-4.5", "claude-haiku-3.5"]),
    ],
    "google": [
        ("gemini-2.5", ["gemini-2.5-pro", "gemini-2.5-flash"]),
        ("gemini-2.0", ["gemini-2.0-flash"]),
        ("gemini", ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"]),
    ],
    "mistral": [
        ("mistral", ["mistral-large", "mistral-small"]),
        ("codestral", ["codestral"]),
        ("pixtral", ["pixtral-large"]),
    ],
    "cohere": [
        ("command", ["command-a", "command-r-plus", "command-r"]),
    ],
    "local": [
        ("llama", ["llama-3.1-70b", "llama-3.1-8b"]),
        ("deepseek", ["deepseek-r1"]),
        ("phi", ["phi-4"]),
    ],
}


def _find_family_fallback(
    model: str, provider_hint: Optional[str] = None
) -> Optional[tuple[str, str]]:
    """Find the closest lower-capability model in the same family.

    Returns (resolved_model, provider) or None.
    """
    providers_to_check = (
        [provider_hint] if provider_hint else list(_PROVIDER_FAMILIES.keys())
    )
    # Generate dot↔dash variants so e.g. "claude-opus-4.6" matches the
    # "claude-opus" family prefix despite the dot vs dash difference.
    variants = _version_variants(model)

    for prov in providers_to_check:
        families = _PROVIDER_FAMILIES.get(prov, [])
        for prefix, models_in_family in families:
            if any(v.startswith(prefix) for v in variants):
                # Found the family — return the first (highest-capability)
                # model that actually exists in our catalogs
                for candidate in models_in_family:
                    if candidate == model:
                        continue  # skip the requested model itself
                    if _exists_in_static_catalog(candidate):
                        return candidate, prov
    return None


def _exists_in_static_catalog(model: str) -> bool:
    """Quick check if a model exists in any provider's static catalog."""
    try:
        from future_token_predictor.providers import resolve_provider_for_model
        return resolve_provider_for_model(model) is not None
    except Exception:
        return False


# ─── Azure AI Foundry model catalog ────────────────────────────────
#
# Comprehensive catalog of models available in Microsoft Foundry.
# Source: Official Microsoft Learn documentation (updated May 2026):
#   - https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure
#   - https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-from-partners
#
# This replaces the broken ai.azure.com/api/catalog/models API call
# (which returns 0 models without authentication).

_AZURE_FOUNDRY_DOCS_URL = "https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure"
_AZURE_FOUNDRY_PARTNERS_URL = "https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-from-partners"

_AZURE_FOUNDRY_MODELS: set[str] = {
    # ── Azure OpenAI models (sold directly by Azure) ──
    # GPT-chat-latest
    "gpt-chat-latest",
    # GPT-5.5
    "gpt-5.5",
    # GPT-5.4 series
    "gpt-5.4", "gpt-5.4-pro", "gpt-5.4-mini", "gpt-5.4-nano",
    # GPT-5.3 series
    "gpt-5.3-codex", "gpt-5.3-chat",
    # GPT-5.2 series
    "gpt-5.2-codex", "gpt-5.2", "gpt-5.2-chat",
    # GPT-5.1 series
    "gpt-5.1", "gpt-5.1-chat", "gpt-5.1-codex", "gpt-5.1-codex-mini", "gpt-5.1-codex-max",
    # GPT-5 series
    "gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-5-chat", "gpt-5-codex", "gpt-5-pro",
    # gpt-oss
    "gpt-oss-120b", "gpt-oss-20b",
    # GPT-4.1 series
    "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
    # computer-use-preview
    "computer-use-preview",
    # o-series reasoning models
    "codex-mini", "o3-pro", "o4-mini", "o3", "o3-mini", "o1", "o1-preview", "o1-mini",
    # GPT-4o / GPT-4 Turbo
    "gpt-4o", "gpt-4o-mini", "gpt-4", "gpt-4-turbo",
    # Embeddings
    "text-embedding-ada-002", "text-embedding-3-large", "text-embedding-3-small",
    # Image generation
    "gpt-image-1", "gpt-image-1-mini", "gpt-image-1.5", "gpt-image-2",
    # Video generation
    "sora", "sora-2",
    # Audio models
    "whisper", "gpt-4o-transcribe", "gpt-4o-mini-transcribe",
    "gpt-4o-transcribe-diarize",
    "tts", "tts-hd", "gpt-4o-mini-tts",
    "gpt-4o-audio-preview", "gpt-4o-realtime-preview",
    "gpt-4o-mini-audio-preview", "gpt-4o-mini-realtime-preview",
    "gpt-audio", "gpt-audio-mini", "gpt-realtime", "gpt-realtime-mini",
    "gpt-audio-1.5", "gpt-realtime-1.5",

    # ── Partners & Community models ──
    # Anthropic Claude
    "claude-mythos-preview",
    "claude-opus-4-7", "claude-opus-4-6", "claude-opus-4-5", "claude-opus-4-1",
    "claude-sonnet-4-6", "claude-sonnet-4-5",
    "claude-haiku-4-5",
    # Cohere
    "cohere-command-r-plus-08-2024", "cohere-command-r-08-2024",
    "cohere-embed-v3-english", "cohere-embed-v3-multilingual",
    # Meta Llama
    "llama-3.2-11b-vision-instruct", "llama-3.2-90b-vision-instruct",
    "meta-llama-3.1-405b-instruct", "meta-llama-3.1-8b-instruct",
    "llama-4-scout-17b-16e-instruct",
    # Microsoft Phi
    "phi-4-mini-instruct", "phi-4-multimodal-instruct", "phi-4",
    "phi-4-reasoning", "phi-4-mini-reasoning",
    # Mistral AI
    "codestral-2501", "ministral-3b", "mistral-small-2503", "mistral-medium-2505",
    "mistralai-mistral-7b-instruct-v01", "mistralai-mistral-7b-instruct-v0-2",
    "mistralai-mixtral-8x7b-instruct-v01", "mistralai-mixtral-8x22b-instruct-v0-1",
    # Nixtla
    "timegen-1",
    # NTT Data
    "tsuzumi-7b",
    # Stability AI
    "stable-diffusion-3.5-large", "stable-image-core", "stable-image-ultra",
}


def _check_azure_ai_catalog(model: str) -> bool:
    """Check if a model exists in the Azure AI Foundry model catalog.

    This is the PRIMARY validation source — always checked first.

    Cascade:
    1. Live public catalog API (fast single-model lookup, no auth)
    2. Hardcoded catalog set (offline fallback)

    Each check tries dot↔dash version variants so ``claude-opus-4.6``
    matches catalog entry ``claude-opus-4-6`` and vice versa.

    Matching strategy (applied for each variant):
    a. Exact match after normalization
    b. Prefix match (e.g., "gpt-5.4" matches "gpt-5.4-mini")
    c. Catalog-as-prefix (e.g., "gpt-5.4-turbo" matches "gpt-5.4")
    """
    normalized = _normalize_model_id(model)
    candidates = _version_variants(normalized)

    # --- Try live Foundry catalog API first (fast, single-model lookup) ---
    try:
        from future_token_predictor.providers.foundry_catalog import check_model_exists
        for candidate in candidates:
            if check_model_exists(candidate):
                return True
    except Exception as exc:
        logger.debug("Live Foundry catalog check failed, falling back to hardcoded: %s", exc)

    # --- Hardcoded catalog fallback (offline / air-gapped) ---
    for candidate in candidates:
        # Exact match
        if candidate in _AZURE_FOUNDRY_MODELS:
            return True

        # Prefix match: user's model is a prefix of a catalog entry
        for entry in _AZURE_FOUNDRY_MODELS:
            if entry.startswith(candidate + "-"):
                return True

        # Catalog-as-prefix: a catalog entry is a prefix of user's model
        for entry in _AZURE_FOUNDRY_MODELS:
            if candidate.startswith(entry + "-"):
                return True

    return False


def _normalize_model_id(model: str) -> str:
    """Normalize model ID for comparison (lowercase, strip provider prefixes)."""
    m = model.lower().strip()
    # Strip common prefixes from catalog entries
    for prefix in ("azure/", "openai/", "anthropic/", "google/", "meta/", "mistral/"):
        if m.startswith(prefix):
            m = m[len(prefix):]
    return m


def _version_variants(model: str) -> list[str]:
    """Generate dot↔dash variants for version-number positions.

    Handles the naming inconsistency where the same model uses dots in one
    catalog (e.g. ``claude-opus-4.6``) but dashes in another
    (``claude-opus-4-6``).  Only swaps separators inside ``\\d[.-]\\d``
    patterns so names like ``gpt-4o-mini`` are never corrupted.

    Returns a list of unique variants (always includes the original).
    """
    variants = {model}

    # dot→dash: "4.6" → "4-6"
    dash_variant = re.sub(r'(\d)\.(\d)', r'\1-\2', model)
    variants.add(dash_variant)

    # dash→dot: "4-6" → "4.6" (only digit-dash-digit)
    dot_variant = re.sub(r'(\d)-(\d)', r'\1.\2', model)
    variants.add(dot_variant)

    return list(variants)


# ─── Main validation function ──────────────────────────────────────

def validate_model(
    model: str,
    provider_hint: Optional[str] = None,
) -> ModelValidationResult:
    """Validate that a model exists and can be priced.

    Cascading checks:
    1. Azure AI Foundry model catalog (always first — public, no auth)
    2. Static provider catalogs (instant, has pricing)
    3. Live provider APIs (if API keys available)
    4. Family fallback (closest known model from same provider)
    5. Error — never assume or pick a random model

    Args:
        model: The model ID to validate (e.g., "claude-opus-6.4").
        provider_hint: Optional provider name to narrow the search.

    Returns:
        ModelValidationResult with status, resolved model, and warnings.
    """
    sources_checked: list[str] = []

    # --- Step 1: Azure AI Foundry model catalog (always first) ---
    sources_checked.append("Azure AI Foundry model catalog")
    foundry_hit = _check_azure_ai_catalog(model)

    if foundry_hit:
        # Model confirmed in Azure Foundry — now find pricing from our catalogs
        from future_token_predictor.providers import resolve_provider_for_model
        result = resolve_provider_for_model(model)
        if result is not None:
            pid, prov = result
            pricing = prov.get_pricing(model)
            return ModelValidationResult(
                status=ValidationStatus.VALID,
                requested_model=model,
                resolved_model=model,
                provider=pid.value,
                message=(
                    f"Model verified in Azure AI Foundry catalog and priced "
                    f"from {pid.value} static catalog."
                ),
                sources_checked=sources_checked,
                pricing_url=prov.pricing_url,
                model_catalog_url=_AZURE_FOUNDRY_DOCS_URL,
            )
        else:
            # Found in Foundry but no local pricing — still valid
            inferred_provider = _infer_provider_from_name(model)
            p_url, c_url = _get_provider_urls(inferred_provider) if inferred_provider else ("", "")
            return ModelValidationResult(
                status=ValidationStatus.VALID_CATALOG,
                requested_model=model,
                resolved_model=model,
                provider=inferred_provider,
                message=(
                    f"Model verified in Azure AI Foundry catalog but not in "
                    f"local pricing catalogs. Cost estimates will use the "
                    f"closest available pricing."
                ),
                sources_checked=sources_checked,
                pricing_url=p_url,
                model_catalog_url=_AZURE_FOUNDRY_DOCS_URL,
            )

    # --- Step 2: Static provider catalogs ---
    from future_token_predictor.providers import resolve_provider_for_model
    result = resolve_provider_for_model(model)
    sources_checked.append("static provider catalogs")

    if result is not None:
        pid, prov = result
        pricing = prov.get_pricing(model)
        if pricing:
            return ModelValidationResult(
                status=ValidationStatus.VALID,
                requested_model=model,
                resolved_model=model,
                provider=pid.value,
                message=f"Model found in {pid.value} static catalog with verified pricing.",
                sources_checked=sources_checked,
                pricing_url=prov.pricing_url,
                model_catalog_url=prov.model_catalog_url,
            )
        else:
            return ModelValidationResult(
                status=ValidationStatus.VALID,
                requested_model=model,
                resolved_model=model,
                provider=pid.value,
                message=f"Model found in {pid.value} catalog (pricing will use live API fallback).",
                sources_checked=sources_checked,
                pricing_url=prov.pricing_url,
                model_catalog_url=prov.model_catalog_url,
            )

    # --- Step 3: Live provider APIs ---
    sources_checked.append("live provider APIs")
    try:
        from future_token_predictor.providers.live_registry import fetch_live_models
        providers_to_scan = (
            [provider_hint] if provider_hint
            else ["openai", "anthropic", "google", "mistral"]
        )
        for prov_name in providers_to_scan:
            live_models = fetch_live_models(prov_name)
            for entry in live_models:
                if entry.model_id == model:
                    p_url, c_url = _get_provider_urls(prov_name)
                    return ModelValidationResult(
                        status=ValidationStatus.VALID_LIVE,
                        requested_model=model,
                        resolved_model=model,
                        provider=prov_name,
                        message=f"Model found via {prov_name} live API.",
                        sources_checked=sources_checked,
                        pricing_url=p_url,
                        model_catalog_url=c_url,
                    )
    except Exception as exc:
        logger.debug("Live API check failed: %s", exc)

    # --- Step 4: Family fallback ---
    sources_checked.append("family fallback matching")
    fallback = _find_family_fallback(model, provider_hint)
    if fallback:
        resolved, prov = fallback
        p_url, c_url = _get_provider_urls(prov)
        return ModelValidationResult(
            status=ValidationStatus.SUBSTITUTED,
            requested_model=model,
            resolved_model=resolved,
            provider=prov,
            message=(
                f"Model '{model}' not found anywhere. "
                f"Substituting '{resolved}' as the closest model in the "
                f"'{prov}' {_get_family_name(model)} family."
            ),
            sources_checked=sources_checked,
            pricing_url=p_url,
            model_catalog_url=c_url,
        )

    # --- Step 5: Not found — never assume ---
    return ModelValidationResult(
        status=ValidationStatus.NOT_FOUND,
        requested_model=model,
        resolved_model=model,
        provider=provider_hint,
        message=(
            f"Model '{model}' was not found in any provider catalog, "
            f"live API, or Azure AI Foundry model catalog. "
            f"Cannot provide cost estimates."
        ),
        sources_checked=sources_checked,
    )


def _infer_provider_from_name(model: str) -> Optional[str]:
    """Best-effort provider inference from model name patterns."""
    m = model.lower()
    if m.startswith("gpt-") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4"):
        return "openai"
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith("gemini"):
        return "google"
    if m.startswith("mistral") or m.startswith("codestral") or m.startswith("pixtral"):
        return "mistral"
    if m.startswith("command"):
        return "cohere"
    if m.startswith("llama") or m.startswith("deepseek") or m.startswith("phi"):
        return "local"
    return None


def _get_family_name(model: str) -> str:
    """Extract the family name from a model ID (e.g., 'gpt-5' from 'gpt-5.4')."""
    # Match common patterns: gpt-5.x, claude-opus-X, gemini-2.x, etc.
    match = re.match(r"^([a-z]+-(?:[a-z]+-)?(?:\d+\.?\d*)?)", model.lower())
    return match.group(1) if match else model


def _get_provider_urls(provider_name: Optional[str]) -> tuple[str, str]:
    """Get (pricing_url, model_catalog_url) for a provider by name string."""
    if not provider_name:
        return ("", "")
    try:
        from future_token_predictor.models.schemas import Provider as ProvEnum
        from future_token_predictor.providers import get_provider
        pid = ProvEnum(provider_name)
        prov = get_provider(pid)
        return (prov.pricing_url, prov.model_catalog_url)
    except (ValueError, KeyError):
        return ("", "")
