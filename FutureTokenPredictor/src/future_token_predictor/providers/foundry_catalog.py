"""Live Azure AI Foundry model catalog lookups.

Uses the public (unauthenticated) Azure AI model catalog API to validate
whether a model exists in the Foundry marketplace.  This is the same API
that the official Foundry MCP Server uses internally.

Endpoint:
    POST https://api.catalog.azureml.ms/asset-gallery/v1.0/models

No Azure credentials, Foundry project, or environment variables are required.
Results are cached to disk with a 24-hour TTL.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# ─── Configuration ─────────────────────────────────────────────────

_CATALOG_URL = "https://api.catalog.azureml.ms/asset-gallery/v1.0/models"
_CACHE_DIR = Path.home() / ".future_token_predictor" / "model_cache"
_CACHE_FILE = "foundry_catalog.json"
_CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 hours
_HTTP_TIMEOUT = 15.0  # seconds


# ─── Disk cache helpers ────────────────────────────────────────────

def _cache_path() -> Path:
    return _CACHE_DIR / _CACHE_FILE


def _is_cache_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age = time.time() - path.stat().st_mtime
    return age < _CACHE_TTL_SECONDS


def _read_cache() -> set[str] | None:
    path = _cache_path()
    if not _is_cache_fresh(path):
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data) if isinstance(data, list) else None
    except (json.JSONDecodeError, OSError):
        return None


def _write_cache(model_names: set[str]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sorted(model_names), indent=2),
        encoding="utf-8",
    )


# ─── API helpers ───────────────────────────────────────────────────

def _post_catalog(
    filters: list[dict],
    *,
    page_size: int = 200,
    continuation_token: str | None = None,
) -> dict:
    """Make a single POST to the catalog API and return the JSON response."""
    body: dict = {"filters": filters, "pageSize": page_size}
    if continuation_token:
        body["continuationToken"] = continuation_token

    resp = httpx.post(_CATALOG_URL, json=body, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# ─── Public interface ──────────────────────────────────────────────

def check_model_exists(model_name: str) -> bool:
    """Check if a *single* model exists in the Foundry catalog (fast path).

    Uses the ``name`` filter for a targeted server-side lookup instead of
    fetching the entire catalog.  Falls back gracefully on network errors.
    """
    try:
        data = _post_catalog(
            filters=[
                {"field": "name", "values": [model_name], "operator": "eq"},
            ],
            page_size=5,
        )
        summaries = data.get("summaries", [])
        return len(summaries) > 0
    except Exception as exc:
        logger.debug("Foundry catalog single-model check failed: %s", exc)
        return False


def fetch_catalog_models() -> set[str]:
    """Fetch the full set of model names from the Foundry catalog.

    Returns cached results when available.  On API failure returns an empty
    set (never raises).
    """
    cached = _read_cache()
    if cached is not None:
        return cached

    try:
        all_names: set[str] = set()
        continuation: str | None = None
        # The "latest" label filter returns current production models.
        filters = [{"field": "labels", "values": ["latest"], "operator": "eq"}]

        while True:
            data = _post_catalog(
                filters, page_size=200, continuation_token=continuation,
            )
            for summary in data.get("summaries", []):
                name = summary.get("name")
                if name:
                    all_names.add(name.lower())

            continuation = data.get("continuationToken")
            if not continuation:
                break

        if all_names:
            _write_cache(all_names)
            logger.info(
                "Foundry catalog: cached %d models to %s",
                len(all_names),
                _cache_path(),
            )
        return all_names

    except Exception as exc:
        logger.warning("Failed to fetch Foundry catalog: %s", exc)
        return set()
