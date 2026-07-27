"""Tier 3 estimator — LLM-assisted token prediction.

Uses a cheap/fast model to estimate the output token count for a given
prompt+model combination, based on insights from:
  [2] Zheng et al. (2023) — LLMs can predict their own response length
  [3] Dong et al. (ICML 2025) — Hidden states encode response planning
  [6] Samragh et al. (2025) — Vanilla LLMs encode future token knowledge

The approach: meta-prompt a small model with the task description, target
model, and modality info, then ask it to estimate the output length.
Cross-validates against Tier 1 heuristics to catch hallucinated estimates.

This tier is OPTIONAL — it requires an API key and network access.
If unavailable, the predictor falls back to Tier 1/2.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Optional, Protocol

import httpx

from future_token_predictor.models.schemas import ModalityBreakdown, UseCaseProfile


# ── Configuration ──

# The meta-estimation prompt. Asks a cheap model to predict token usage
# for the ENTIRE workflow/task, not just a single call.
_META_PROMPT = """You are an expert at estimating LLM token usage for real-world tasks.

Given a workload description, target model, and workflow pattern, estimate the TOTAL
tokens consumed by ONE production invocation of that workload (all model calls inside
that invocation combined). Do not estimate the effort to design, explain, or implement
the described system. User counts and calls-per-day are scale inputs and must not change
the per-invocation estimate. For a RAG pipeline, estimate one end-user query: retrieval
context plus the generation call, unless the description explicitly requires a multi-step
agent loop.

Consider:
- How many LLM calls/steps this task realistically requires
- The typical input size per call (system prompt + user message + context)
- The typical output size per call (code is verbose, answers are short)
- Context growth across multi-step workflows (conversation history accumulates)
- The model's verbosity (e.g., Claude is more verbose than GPT-4.1-nano)
- For code generation: each file/component is a separate call, code output
  is typically 200-2000 tokens per function/component
- For RAG: retrieval chunks add to input, but output is typically concise
- For agents: each tool call is a round-trip with growing context

Task description: {description}
Target model: {model}
Provider: {provider}
Agent pattern: {agent_pattern}
Complexity: {complexity}
Modalities: {modalities}
Tools: {tools}

Respond with ONLY a JSON object (no markdown, no explanation):
{{
  "estimated_output_tokens": <integer total across all calls>,
  "estimated_input_tokens": <integer total across all calls>,
  "estimated_reasoning_tokens": <integer, 0 if not a reasoning model>,
  "estimated_steps": <integer number of LLM calls/steps>,
  "confidence": <float 0-1>,
  "reasoning": "<brief explanation of how you estimated>"
}}"""


@dataclass
class Tier3Estimate:
    """Result from LLM-assisted estimation."""

    estimated_output_tokens: int = 0
    estimated_input_tokens: int = 0
    estimated_reasoning_tokens: int = 0
    estimated_steps: int = 1
    confidence: float = 0.0
    reasoning: str = ""
    model_used: str = ""  # The cheap model that made the estimate


class LLMClient(Protocol):
    """Protocol for LLM API clients used in Tier 3 estimation."""

    def estimate_tokens(
        self,
        description: str,
        profile: UseCaseProfile,
    ) -> Optional[Tier3Estimate]: ...


class OpenAICompatibleClient:
    """Client for OpenAI-compatible APIs (OpenAI, Azure OpenAI, Ollama, vLLM, etc.).

    Credential resolution order:
    1. Explicit constructor args
    2. TIER3_API_KEY + TIER3_BASE_URL + TIER3_MODEL
    3. AZURE_OPENAI_ENDPOINT (Entra ID via AzureCliCredential, or API key)
    4. OPENAI_API_KEY
    """

    _AZURE_SCOPE = "https://cognitiveservices.azure.com/.default"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._timeout = timeout
        resolved = self._resolve(api_key, base_url, model)
        self._api_key = resolved[0]
        self._base_url = resolved[1]
        self._model = resolved[2]
        self._is_azure = resolved[3]
        self._api_version = resolved[4]
        self._use_entra = resolved[5]

    @staticmethod
    def _is_azure_endpoint(url: str) -> bool:
        return "openai.azure.com" in url or "cognitiveservices.azure.com" in url

    @classmethod
    def _resolve(
        cls,
        api_key: str | None,
        base_url: str | None,
        model: str | None,
    ) -> tuple[str, str, str, bool, str, bool]:
        """Resolve credentials from args or env vars.

        Returns (api_key, base_url, model, is_azure, api_version, use_entra).
        """
        # Explicit
        if api_key and base_url and model:
            is_azure = cls._is_azure_endpoint(base_url)
            return api_key, base_url.rstrip("/"), model, is_azure, "", False

        # TIER3_* env vars
        t3_key = api_key or os.environ.get("TIER3_API_KEY", "")
        t3_url = base_url or os.environ.get("TIER3_BASE_URL", "")
        t3_model = model or os.environ.get("TIER3_MODEL", "")
        if t3_key and t3_url and t3_model:
            is_azure = cls._is_azure_endpoint(t3_url)
            return t3_key, t3_url.rstrip("/"), t3_model, is_azure, "", False

        # Azure OpenAI (key or Entra ID)
        az_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
        az_ep = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        if az_ep:
            az_model = t3_model or os.environ.get(
                "AZURE_OPENAI_TIER3_DEPLOYMENT", "gpt-4.1-nano"
            )
            api_ver = os.environ.get("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
            url = f"{az_ep.rstrip('/')}/openai/deployments/{az_model}"
            use_entra = not az_key
            return az_key or "entra", url, az_model, True, api_ver, use_entra

        # OpenAI
        oai_key = t3_key or os.environ.get("OPENAI_API_KEY", "")
        return (
            oai_key,
            (t3_url or "https://api.openai.com/v1").rstrip("/"),
            t3_model or "gpt-4.1-nano",
            False,
            "",
            False,
        )

    def _get_azure_token(self) -> str:
        """Get a bearer token via AzureCliCredential."""
        from azure.identity import AzureCliCredential
        credential = AzureCliCredential()
        token = credential.get_token(self._AZURE_SCOPE)
        return token.token

    def estimate_tokens(
        self,
        description: str,
        profile: UseCaseProfile,
    ) -> Optional[Tier3Estimate]:
        """Call a cheap model to estimate output tokens."""
        if not self._api_key:
            return None

        prompt = _META_PROMPT.format(
            description=description or "(structured profile, no description)",
            model=profile.model,
            provider=profile.provider.value if profile.provider else "unknown",
            agent_pattern=profile.agent_pattern.value if profile.agent_pattern else "single_call",
            complexity=profile.complexity.value if profile.complexity else "medium",
            modalities=", ".join(m.value for m in profile.modalities),
            tools=", ".join(t.value for t in profile.tools) if profile.tools else "none",
        )

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._is_azure:
            if self._use_entra:
                token = self._get_azure_token()
                headers["Authorization"] = f"Bearer {token}"
            else:
                headers["api-key"] = self._api_key
        else:
            headers["Authorization"] = f"Bearer {self._api_key}"

        url = f"{self._base_url}/chat/completions"
        if self._api_version:
            url += f"?api-version={self._api_version}"

        try:
            response = httpx.post(
                url,
                headers=headers,
                json={
                    "model": self._model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                    "max_completion_tokens": 300,
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"].strip()
            return _parse_estimate(content, self._model)
        except Exception:
            return None


class MockLLMClient:
    """Mock client for testing — returns deterministic estimates."""

    def __init__(self, estimates: Optional[dict[str, Tier3Estimate]] = None) -> None:
        self._estimates = estimates or {}
        self._default = Tier3Estimate(
            estimated_output_tokens=500,
            estimated_input_tokens=800,
            estimated_reasoning_tokens=0,
            confidence=0.7,
            reasoning="Mock estimate",
            model_used="mock",
        )

    def estimate_tokens(
        self,
        description: str,
        profile: UseCaseProfile,
    ) -> Optional[Tier3Estimate]:
        key = profile.model
        return self._estimates.get(key, self._default)


def _parse_estimate(content: str, model_used: str) -> Optional[Tier3Estimate]:
    """Parse the JSON response from the meta-estimation model."""
    # Strip markdown code fences if present
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)
    content = content.strip()

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    output_tokens = data.get("estimated_output_tokens")
    if not isinstance(output_tokens, (int, float)) or output_tokens < 0:
        return None

    return Tier3Estimate(
        estimated_output_tokens=int(data.get("estimated_output_tokens", 0)),
        estimated_input_tokens=int(data.get("estimated_input_tokens", 0)),
        estimated_reasoning_tokens=int(data.get("estimated_reasoning_tokens", 0)),
        estimated_steps=max(1, int(data.get("estimated_steps", 1))),
        confidence=float(min(1.0, max(0.0, data.get("confidence", 0.5)))),
        reasoning=str(data.get("reasoning", "")),
        model_used=model_used,
    )


# ── Tier 3 Application Logic ──

# Bounds for sanity-checking LLM estimates against Tier 1.
# Note: Tier 3 now estimates TOTAL tokens across all workflow steps,
# while Tier 1 estimates single-call tokens. For multi-step workflows
# (code gen, agents), the ratio can legitimately be 10-20x.
_MIN_RATIO = 0.1   # LLM estimate must be ≥10% of Tier 1
_MAX_RATIO = 25.0  # LLM estimate must be ≤2500% of Tier 1
_MIN_CONFIDENCE = 0.3  # Minimum LLM self-reported confidence


def apply_tier3(
    tier1_tokens: ModalityBreakdown,
    estimate: Tier3Estimate,
) -> Optional[ModalityBreakdown]:
    """Apply Tier 3 LLM estimate to adjust Tier 1 predictions.

    Cross-validates: if the LLM estimate is wildly different from Tier 1,
    we reject it (the LLM may be hallucinating).

    When accepted, blends the LLM estimate with Tier 1 using the LLM's
    self-reported confidence as the blend weight.

    Returns None if the estimate fails validation (caller uses Tier 1/2).
    """
    if estimate.confidence < _MIN_CONFIDENCE:
        return None

    # Validate output tokens against Tier 1
    if tier1_tokens.text_output > 0 and estimate.estimated_output_tokens > 0:
        ratio = estimate.estimated_output_tokens / tier1_tokens.text_output
        if ratio < _MIN_RATIO or ratio > _MAX_RATIO:
            return None

    # Validate input tokens if provided
    if (
        tier1_tokens.text_input > 0
        and estimate.estimated_input_tokens > 0
    ):
        ratio = estimate.estimated_input_tokens / tier1_tokens.text_input
        if ratio < _MIN_RATIO or ratio > _MAX_RATIO:
            return None

    # Blend: result = (1 - confidence) * tier1 + confidence * llm_estimate
    w = estimate.confidence

    new_output = (
        (1 - w) * tier1_tokens.text_output + w * estimate.estimated_output_tokens
        if estimate.estimated_output_tokens > 0
        else tier1_tokens.text_output
    )

    new_input = (
        (1 - w) * tier1_tokens.text_input + w * estimate.estimated_input_tokens
        if estimate.estimated_input_tokens > 0
        else tier1_tokens.text_input
    )

    new_reasoning = (
        (1 - w) * tier1_tokens.reasoning + w * estimate.estimated_reasoning_tokens
        if estimate.estimated_reasoning_tokens > 0
        else tier1_tokens.reasoning
    )

    return ModalityBreakdown(
        text_input=new_input,
        text_output=new_output,
        cached_input=tier1_tokens.cached_input,
        image_input=tier1_tokens.image_input,  # Formula-based, don't adjust
        image_output=tier1_tokens.image_output,
        document_input=tier1_tokens.document_input,
        audio_input=tier1_tokens.audio_input,
        audio_output=tier1_tokens.audio_output,
        reasoning=new_reasoning,
    )
