"""Versioned Studio catalog for Microsoft Foundry model offerings."""

from __future__ import annotations

import json
from pathlib import Path


RELEASE_PATH = Path("data/model_catalogs/foundry-model-release.v2.json")
ALLOWED_PROVIDERS = {"azure_openai", "anthropic"}
ALLOWED_MODALITIES = {
    "text", "embeddings", "image", "video", "audio", "specialized",
}


def _merge_dicts(*values: dict) -> dict:
    merged: dict = {}
    for value in values:
        for key, item in value.items():
            if isinstance(item, dict) and isinstance(merged.get(key), dict):
                merged[key] = _merge_dicts(merged[key], item)
            else:
                merged[key] = item
    return merged


def _expand_v2(catalog: dict) -> list[dict]:
    providers = catalog.get("providers") or {}
    groups = catalog.get("model_groups") or []
    offerings: list[dict] = []
    for group in groups:
        provider = str(group.get("provider", "")).strip()
        provider_defaults = providers.get(provider) or {}
        group_defaults = group.get("defaults") or {}
        for model_entry in group.get("models") or []:
            offering = _merge_dicts(
                provider_defaults,
                group_defaults,
                {
                    "provider": provider,
                    "modality_group": group.get("modality_group"),
                },
                model_entry,
            )
            model = str(offering.get("model", "")).strip()
            offering["key"] = f"{provider}:{model}"
            if "selector_eligible" not in offering:
                pricing = offering.get("pricing") or {}
                offering["selector_eligible"] = bool(
                    offering.get("coordinator_capable")
                    and offering.get("evidence", {}).get("status") == "verified"
                    and isinstance(pricing.get("input"), (int, float))
                    and isinstance(pricing.get("output"), (int, float))
                )
            offerings.append(offering)
    return offerings


def load_model_release(root: str | Path) -> dict:
    """Load and validate the product release catalog."""
    path = Path(root).resolve() / RELEASE_PATH
    catalog = json.loads(path.read_text(encoding="utf-8"))
    if catalog.get("schema_version") != "foundry-model-release.v2":
        raise ValueError("unsupported Foundry model release schema")
    if not str(catalog.get("release_version", "")).strip():
        raise ValueError("Foundry model release_version is required")

    offerings = _expand_v2(catalog)
    if not isinstance(offerings, list) or not offerings:
        raise ValueError("Foundry model release requires offerings")
    keys: set[str] = set()
    for offering in offerings:
        key = str(offering.get("key", "")).strip()
        provider = str(offering.get("provider", "")).strip()
        model = str(offering.get("model", "")).strip()
        pricing = offering.get("pricing") or {}
        if not key or key in keys or key != f"{provider}:{model}":
            raise ValueError("Foundry model release has an invalid or duplicate key")
        if provider not in ALLOWED_PROVIDERS:
            raise ValueError(f"unreleased provider: {provider}")
        if offering.get("modality_group") not in ALLOWED_MODALITIES:
            raise ValueError(f"invalid modality group for {key}")
        evidence_status = offering.get("evidence", {}).get("status")
        if evidence_status not in {"verified", "unavailable"}:
            raise ValueError(f"explicit pricing evidence status is required for {key}")
        if offering.get("selector_eligible"):
            if not offering.get("coordinator_capable"):
                raise ValueError(f"non-coordinator model cannot be selected: {key}")
            if evidence_status != "verified":
                raise ValueError(f"verified pricing evidence is required for {key}")
            if not isinstance(pricing.get("input"), (int, float)) or not isinstance(
                pricing.get("output"), (int, float)
            ):
                raise ValueError(f"exact input/output pricing is required for {key}")
        for source_field in ("pricing_url", "model_catalog_url"):
            if not str(offering.get(source_field, "")).startswith("https://"):
                raise ValueError(f"{source_field} is required for {key}")
        keys.add(key)

    if catalog.get("default_key") not in keys:
        raise ValueError("Foundry model release default_key is not released")
    default = next(
        item for item in offerings if item["key"] == catalog["default_key"]
    )
    if not default.get("selector_eligible"):
        raise ValueError("Foundry model release default_key must be selectable")
    catalog["offerings"] = offerings
    return catalog


def find_released_offering(catalog: dict, provider: str, model: str) -> dict | None:
    """Return the exact released provider/model offering, if present."""
    key = f"{provider}:{model}"
    return next(
        (offering for offering in catalog["offerings"] if offering["key"] == key),
        None,
    )
