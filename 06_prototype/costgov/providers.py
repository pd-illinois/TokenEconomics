"""
providers.py — REAL model adapters (live path only).

Same Answer contract as costgov.models.SimulatedModel, so gateway.py is unchanged.
Auth is Entra ID via DefaultAzureCredential (az login) — no API key stored.
Cost is computed from the API's real `usage` (including the cached-token discount).

All Azure imports are LAZY (inside functions) so the simulated demo keeps running
with zero dependencies. Nothing here executes unless demo.py is run with --live.
"""

from __future__ import annotations
import os
import time
from dataclasses import dataclass

from .models import Answer

# Stable system prefix -> eligible for native prompt caching (Azure OpenAI/Foundry).
_SYSTEM_PROMPT = (
    "You are a concise customer-support assistant. Answer in 1-2 sentences using only "
    "the facts you are confident about. If unsure, say you will hand off to an agent."
)


def build_client():
    """AzureOpenAI client authenticated with Entra ID (DefaultAzureCredential)."""
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    from openai import AzureOpenAI

    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
    # gpt-5 / o-series reasoning models require a recent API version.
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
    return AzureOpenAI(
        azure_endpoint=endpoint,
        azure_ad_token_provider=token_provider,
        api_version=api_version,
    )


def _pad_context(context_tokens: int) -> str:
    """Materialize ~context_tokens of prior-turn history so pruning has a REAL cost effect.
    (~0.75 words/token.) This makes the 'context snowball' economics measurable on live calls."""
    words = max(0, int(context_tokens * 0.75))
    if words == 0:
        return ""
    return "Prior conversation context: " + ("lorem ipsum " * (words // 2 + 1))


def _reasoning_params() -> dict:
    """Params that differ between classic (gpt-4o/4.1) and reasoning (gpt-5/o-series) models.

    Reasoning models reject temperature!=1 and require `max_completion_tokens` (not
    `max_tokens`); they also spend hidden reasoning tokens against that budget. Setting
    AZURE_REASONING_EFFORT='minimal' (recommended for gpt-5 on short support answers) cuts
    reasoning-token burn + latency and prevents content starvation. Leave it unset for
    classic models. This keeps the adapter model-agnostic (reusable-IP friendly).
    """
    params = {}
    effort = os.environ.get("AZURE_REASONING_EFFORT")
    if effort:
        params["reasoning_effort"] = effort
    return params


@dataclass
class RealModel:
    """One real Azure deployment behind the Answer contract."""
    name: str
    deployment: str
    price_per_1k_input: float
    price_per_1k_output: float
    client: object
    input_markup_per_1k: float = 0.0   # for Model Router input markup
    # Optional grounding hook: context_provider(question)->str. If set, its text is used as
    # the prompt context (e.g. RAG-retrieved passages) instead of the synthetic _pad_context.
    context_provider: object = None

    def generate(self, question: str, context_tokens: int, difficulty: str) -> Answer:
        context = (self.context_provider(question) if self.context_provider
                   else _pad_context(context_tokens))
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": (context +
                                         "\n\nQuestion: " + question).strip()},
        ]
        t0 = time.perf_counter()
        # max_completion_tokens (not max_tokens) + no temperature override => works for
        # gpt-5 reasoning models AND classic gpt-4o/4.1. Budget must leave room for content
        # after hidden reasoning tokens on reasoning models (see _reasoning_params).
        resp = self.client.chat.completions.create(
            model=self.deployment, messages=messages,
            max_completion_tokens=int(os.environ.get("LIVE_MAX_COMPLETION_TOKENS", "1024")),
            **_reasoning_params())
        latency_ms = (time.perf_counter() - t0) * 1000.0

        u = resp.usage
        details = getattr(u, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) or 0
        # cached input billed at ~10% (Azure/Anthropic prompt caching); rest at full price
        effective_input = (u.prompt_tokens - cached) + cached * 0.10
        cost = (effective_input / 1000.0) * (self.price_per_1k_input + self.input_markup_per_1k) \
             + (u.completion_tokens / 1000.0) * self.price_per_1k_output
        text = resp.choices[0].message.content or ""
        return Answer(text, u.prompt_tokens, u.completion_tokens,
                      round(cost, 6), round(latency_ms, 1), self.name)


def build_real_models(client) -> dict:
    """cheap + premium real deployments for OUR-gateway routing (benchmark arm 1)."""
    return {
        "cheap": RealModel(
            name="cheap", deployment=os.environ["AZURE_DEPLOYMENT_CHEAP"],
            # illustrative gpt-5-nano public prices; override with real PRICE_* in .env
            price_per_1k_input=float(os.environ.get("PRICE_CHEAP_INPUT", 0.00005)),
            price_per_1k_output=float(os.environ.get("PRICE_CHEAP_OUTPUT", 0.0004)),
            client=client),
        "premium": RealModel(
            name="premium", deployment=os.environ["AZURE_DEPLOYMENT_PREMIUM"],
            # illustrative gpt-5 public prices; override with real PRICE_* in .env
            price_per_1k_input=float(os.environ.get("PRICE_PREMIUM_INPUT", 0.00125)),
            price_per_1k_output=float(os.environ.get("PRICE_PREMIUM_OUTPUT", 0.01)),
            client=client),
    }


def build_router_model(client) -> RealModel:
    """Foundry Model Router as a single deployment (benchmark arm 2 — Azure routes)."""
    return RealModel(
        name="router", deployment=os.environ["AZURE_DEPLOYMENT_ROUTER"],
        # router bills at the chosen model's price; we approximate with premium input + markup
        price_per_1k_input=float(os.environ.get("PRICE_PREMIUM_INPUT", 0.0025)),
        price_per_1k_output=float(os.environ.get("PRICE_PREMIUM_OUTPUT", 0.01)),
        input_markup_per_1k=float(os.environ.get("PRICE_ROUTER_INPUT_MARKUP", 0.0)),
        client=client)


class RealJudge:
    """LLM-as-judge via a real Foundry/Azure judge deployment.

    Returns a 0..1 score = fraction of required facts the answer covers, decided by the
    judge model. This is the inline, immediately-runnable judge. For the MANAGED Foundry
    cloud-evaluation service (datasets, scheduled/continuous eval), see LIVE.md — it drops
    in here without changing the evaluator/decision interfaces.
    """
    def __init__(self, client, deployment: str):
        self.client = client
        self.deployment = deployment

    def score(self, question: str, answer_text: str, must_include) -> float:
        rubric = (
            "You are grading a support answer. Given the required facts, respond with ONLY "
            "a number between 0 and 1 = fraction of the required facts the answer correctly "
            "conveys. No words, just the number.\n"
            f"Required facts: {must_include}\n"
            f"Question: {question}\nAnswer: {answer_text}\n"
        )
        resp = self.client.chat.completions.create(
            model=self.deployment,
            messages=[{"role": "user", "content": rubric}],
            max_completion_tokens=int(os.environ.get("LIVE_JUDGE_COMPLETION_TOKENS", "512")),
            **_reasoning_params())
        raw = (resp.choices[0].message.content or "0").strip()
        try:
            return max(0.0, min(1.0, float(raw.split()[0])))
        except (ValueError, IndexError):
            return 0.0
