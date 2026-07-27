"""LLM-based use-case classification.

Replaces the regex-based model/provider detection with a call to a cheap
Azure OpenAI / OpenAI model.  Falls back to regex when no API key is set
or when the LLM call fails.

Environment variables (checked in order):
  CLASSIFIER_API_KEY   — explicit key for the classifier model
  AZURE_OPENAI_API_KEY — Azure OpenAI key (uses CLASSIFIER_ENDPOINT too)
  OPENAI_API_KEY       — OpenAI key
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_AZURE_SCOPE = "https://cognitiveservices.azure.com/.default"


def _get_azure_bearer_token() -> str:
    """Get a bearer token via AzureCliCredential."""
    from azure.identity import AzureCliCredential

    credential = AzureCliCredential()
    return credential.get_token(_AZURE_SCOPE).token


# ── Configuration ────────────────────────────────────────────────────

_CLASSIFIER_TIMEOUT = 8.0  # seconds — keep tight for interactive UX

# The classifier prompt.  Asks a cheap model to extract structured info.
_CLASSIFIER_SYSTEM = """\
You are a model-name and provider classifier for LLM use-case descriptions.

Given the user's description, extract the following fields.
Rules:
- model: the exact canonical model ID (e.g. "gpt-5.4-mini", "claude-sonnet-4",
  "gemini-2.5-flash"). Use lowercase with hyphens. If no model is mentioned,
  pick a sensible default (e.g. "gpt-4.1" for OpenAI, "claude-sonnet-4" for
  Anthropic).
- provider: one of "openai", "azure_openai", "anthropic", "google", "mistral",
  "cohere", "bedrock", "local".  If "azure" is mentioned, use "azure_openai".
- agent_type: "prompt" (single call), "workflow" (multi-step chain/DAG),
  or "hosted" (autonomous agent loop / react / multi-agent).
- modalities: list from ["text","image_input","image_output","document",
  "audio_input","audio_output"]. Always include "text".
- complexity: "low", "medium", or "high".
- reasoning: true if the description explicitly asks for a reasoning / thinking
  model (o3, o4-mini, o1, extended thinking, etc.), else false.
- uses_prompt_caching: true if the description mentions prompt/context caching
  or reusing a large fixed prefix, else false.
- uses_batch_api: true if the description mentions batch / offline / async bulk
  processing, else false.
- uses_streaming: true if the description mentions streaming / token-by-token
  responses, else false.
- uses_retrieval: true if the description mentions RAG / retrieval / vector
  search / knowledge base lookups, else false.

Respond with ONLY a JSON object — no markdown fences, no explanation:
{"model":"...","provider":"...","agent_type":"...","modalities":[...],"complexity":"...","reasoning":false,"uses_prompt_caching":false,"uses_batch_api":false,"uses_streaming":false,"uses_retrieval":false}"""


@dataclass
class LLMClassification:
    """Structured result from the LLM classifier."""

    model: str
    provider: str
    agent_type: str = "prompt"
    modalities: list[str] | None = None
    complexity: str = "medium"
    reasoning: bool = False
    uses_prompt_caching: bool = False
    uses_batch_api: bool = False
    uses_streaming: bool = False
    uses_retrieval: bool = False


def classify_with_llm(
    description: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    timeout: float = _CLASSIFIER_TIMEOUT,
) -> Optional[LLMClassification]:
    """Call a cheap LLM to classify the use-case description.

    Returns None on any failure (network, auth, parse).
    """
    key, url, mdl = _resolve_credentials(api_key, base_url, model)
    if not key:
        logger.debug("No API key available for LLM classifier")
        return None

    headers: dict[str, str] = {"Content-Type": "application/json"}
    # Azure OpenAI uses api-key header; OpenAI-compatible uses Bearer token
    is_azure = "openai.azure.com" in url or "cognitiveservices.azure.com" in url
    if is_azure:
        if key == "entra":
            # Entra ID token auth (key-based auth disabled on resource)
            token = _get_azure_bearer_token()
            headers["Authorization"] = f"Bearer {token}"
        else:
            headers["api-key"] = key
    else:
        headers["Authorization"] = f"Bearer {key}"

    # Azure URL already includes /chat/completions; OpenAI needs it appended
    call_url = url if is_azure else f"{url}/chat/completions"

    try:
        resp = httpx.post(
            call_url,
            headers=headers,
            json={
                "model": mdl,
                "messages": [
                    {"role": "system", "content": _CLASSIFIER_SYSTEM},
                    {"role": "user", "content": description},
                ],
                "temperature": 0.0,
                "max_completion_tokens": 200,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        return _parse_classification(content)
    except Exception as exc:
        logger.warning("LLM classifier call failed: %s", exc)
        return None


# ── Internals ────────────────────────────────────────────────────────


def _resolve_credentials(
    api_key: str | None,
    base_url: str | None,
    model: str | None,
) -> tuple[str, str, str]:
    """Resolve API key, endpoint, and model from explicit args or env vars.

    Returns (key, url, model) — key is "" if nothing is available.
    """
    # Explicit overrides
    if api_key and base_url and model:
        return api_key, base_url.rstrip("/"), model

    # 1. Dedicated classifier env vars
    key = api_key or os.environ.get("CLASSIFIER_API_KEY", "")
    url = base_url or os.environ.get("CLASSIFIER_ENDPOINT", "")
    mdl = model or os.environ.get("CLASSIFIER_MODEL", "")

    if key and url and mdl:
        return key, url.rstrip("/"), mdl

    # 2. Azure OpenAI
    azure_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
    azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    if azure_endpoint:
        # Azure OpenAI uses deployment name as model
        azure_model = mdl or os.environ.get(
            "AZURE_OPENAI_CLASSIFIER_DEPLOYMENT", "gpt-4.1-nano"
        )
        api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
        azure_url = (
            f"{azure_endpoint.rstrip('/')}/openai/deployments/{azure_model}"
            f"/chat/completions?api-version={api_version}"
        )
        # If no API key, use "entra" sentinel — caller will acquire token
        return azure_key or "entra", azure_url, azure_model

    # 3. OpenAI
    openai_key = key or os.environ.get("OPENAI_API_KEY", "")
    if openai_key:
        return (
            openai_key,
            (url or "https://api.openai.com/v1").rstrip("/"),
            mdl or "gpt-4.1-nano",
        )

    return "", "", ""


def _parse_classification(content: str) -> Optional[LLMClassification]:
    """Parse the JSON response from the classifier model."""
    # Strip markdown code fences if present
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)
    content = content.strip()

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = _extract_json_object(content)
        if data is None:
            logger.warning("LLM classifier returned invalid JSON: %s", content[:200])
            return None

    if not isinstance(data, dict):
        return None

    model = data.get("model", "")
    provider = data.get("provider", "")
    if not model or not provider:
        return None

    return LLMClassification(
        model=str(model).lower().strip(),
        provider=str(provider).lower().strip(),
        agent_type=str(data.get("agent_type", "prompt")).lower().strip(),
        modalities=data.get("modalities"),
        complexity=str(data.get("complexity", "medium")).lower().strip(),
        reasoning=bool(data.get("reasoning", False)),
        uses_prompt_caching=bool(data.get("uses_prompt_caching", False)),
        uses_batch_api=bool(data.get("uses_batch_api", False)),
        uses_streaming=bool(data.get("uses_streaming", False)),
        uses_retrieval=bool(data.get("uses_retrieval", False)),
    )


def _extract_json_object(content: str) -> Optional[dict]:
    """Extract the first balanced JSON object from prose-wrapped text."""
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
