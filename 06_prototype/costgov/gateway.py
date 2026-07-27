"""
Gateway (data plane choke point).

The single place policy is enforced (mirrors APIM GenAI gateway / LiteLLM in file 05):
  1. token cap / budget      -> degrade or reject
  2. semantic cache lookup   -> return cached on hit (skip the model entirely)
  3. routing                 -> pick cheap vs premium per config mode + difficulty
  4. native prompt caching   -> discount on the stable-prefix input tokens
  5. emit token metric       -> to telemetry sink

It reads ALL behavior from the config store (the knobs); the control plane changes
those knobs at runtime with no code deploy.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import secrets
import uuid

from .contracts import ExecutionContext
from .models import MODELS
from .cache import SemanticCache
from .context import build_context_tokens

# Native prompt caching: cached stable-prefix input tokens billed at ~10% (file 01/05).
_PROMPT_CACHE_DISCOUNT = 0.90
_SEMANTIC_EMBED_LATENCY_MS = 60.0  # the in-path "latency tax" of a cache lookup (file: overhead economics)


@dataclass
class Result:
    tenant: str
    question: str
    difficulty: str
    answer_text: str
    model: str
    cost_usd: float
    latency_ms: float
    cache_hit: bool
    degraded: bool
    request_id: str
    trace_id: str
    timestamp: str
    report_id: str | None = None
    run_id: str = ""
    prediction_id: int | None = None
    segment: str = ""
    policy_version: str = ""
    input_tokens: float = 0.0
    output_tokens: float = 0.0
    cached_tokens: float = 0.0
    reasoning_tokens: float = 0.0
    document_tokens: float = 0.0


class Gateway:
    def __init__(self, config: dict, telemetry, models=None, embed_fn=None):
        self.config = config
        self.telemetry = telemetry
        self.models = models if models is not None else MODELS   # sim by default; real on --live
        self.cache = SemanticCache(
            threshold=config["semantic_cache"]["score_threshold"], embed_fn=embed_fn)
        self._history = {}  # tenant -> running turn count (context snowball)

    # --- routing decision (mirrors Foundry Model Router modes) ---
    def _route(self, difficulty: str) -> str:
        mode = self.config["routing"]["mode"]
        if mode == "quality":
            return "premium"
        if mode == "cost":
            return "cheap"  # aggressive: cheap even for hard -> risks quality (demo trigger)
        # balanced: cheap for easy, premium for hard
        return "cheap" if difficulty == "easy" else "premium"

    def handle(
        self,
        tenant: str,
        question: str,
        difficulty: str,
        execution: ExecutionContext | None = None,
    ) -> Result:
        cfg = self.config
        latency = 0.0
        request_id = execution.request_id if execution and execution.request_id else str(uuid.uuid4())
        trace_id = execution.trace_id if execution and execution.trace_id else secrets.token_hex(16)
        common = {
            "request_id": request_id,
            "trace_id": trace_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "report_id": execution.report_id if execution else None,
            "run_id": execution.run_id if execution else "",
            "prediction_id": execution.prediction_id if execution else None,
            "segment": execution.segment if execution else difficulty,
            "policy_version": execution.policy_version if execution else "",
        }

        # 2. semantic cache lookup (in-path; adds a small embedding latency tax)
        if cfg["semantic_cache"]["enabled"]:
            latency += _SEMANTIC_EMBED_LATENCY_MS
            cached, sim = self.cache.lookup(question)
            if cached is not None:
                res = Result(tenant, question, difficulty, cached.text, "cache",
                             0.0, latency, cache_hit=True, degraded=False, **common)
                self.telemetry.record(res, sampled_meta={"difficulty": difficulty})
                return res

        # 3. routing
        chosen = self._route(difficulty)

        # 1. budget / token cap -> graceful degradation or hard reject
        degraded = False
        spent = self.telemetry.tenant_cost(tenant)
        if spent >= cfg["budgets"]["per_tenant_usd_per_run"]:
            if cfg["budgets"]["hard_cap_action"] == "degrade":
                chosen, degraded = "cheap", True
            else:  # reject (429-style)
                res = Result(tenant, question, difficulty,
                             "[rejected: budget exceeded]", "none", 0.0, latency,
                             cache_hit=False, degraded=True, **common)
                self.telemetry.record(res, sampled_meta={"difficulty": difficulty})
                return res

        # context management (prune the snowball)
        self._history[tenant] = self._history.get(tenant, 0) + 1
        ctx_tokens = build_context_tokens(
            self._history[tenant], cfg["context"]["prune"], cfg["context"]["max_context_items"])

        # 4. call model + native prompt caching discount on stable prefix (200 tok)
        model = self.models[chosen]
        ans = model.generate(question, ctx_tokens, difficulty)
        cached_prefix_tokens = 200
        discount = (cached_prefix_tokens / 1000.0) * model.price_per_1k_input * _PROMPT_CACHE_DISCOUNT
        ans.cost_usd = round(max(0.0, ans.cost_usd - discount), 6)
        latency += ans.latency_ms

        # store for future cache hits
        if cfg["semantic_cache"]["enabled"]:
            self.cache.store(question, ans)

        res = Result(tenant, question, difficulty, ans.text, chosen,
                     ans.cost_usd, latency, cache_hit=False, degraded=degraded,
                     input_tokens=ans.input_tokens,
                     output_tokens=ans.output_tokens,
                     **common)
        # 5. emit metric
        self.telemetry.record(res, sampled_meta={"difficulty": difficulty})
        return res
