# The Azure-First Token-Cost Optimization Framework
### An internal process & tool-awareness guide for architects: use Azure natively first, fall back to open-source where Azure is weak

*Not a product — a decision framework. For each agentic cost-optimization need, it names the Azure-native lever to reach for **first**, the specific open-source/third-party fallback, and the **trigger condition** that tells you when to switch.*

*Method: adversarial research pass — 19 sources, 25 claims verified (25/25 confirmed, 0 refuted), all Azure claims from official Microsoft Learn docs (updated 2026-05/06). Compiled 2026-07-13. Sequel to [critical-review-cost-control-plane.md](04_critical-review-cost-control-plane.md), [agentic-token-spend-research.md](02_agentic-token-spend-research.md), [token-economics-research.md](01_token-economics-research.md).*

---

## Why this shape is the right one

The critical review killed the "build a platform" idea: the agent-cost-control layer is crowded (LiteLLM, Langfuse, Portkey, Dobby) and the primitives are commoditized. **A process framework turns that verdict into an advantage** — you assemble proven, mostly-native tools instead of competing with them. The judging criterion is *usefulness to architects*, not defensibility.

**The honest headline from the Azure research:** Azure already ships **strong, documented, quality-preserving cost levers** for routing, caching, token-capping, and serving economics — **and a robust evaluation engine** (Foundry cloud evaluation: LLM-judge, golden sets, regression, continuous eval). It is weak in exactly one narrow place — not evaluation itself, but the **enforcement binding**: nothing native *acts* on an eval verdict in the runtime cost path (evaluation measures and reports, async and sampled; it doesn't route, cap, or degrade). That binding is the only thing you build — and, as need #7 shows, it can be built **Azure-native.**

---

## Azure-native inventory (what's real, what's GA vs preview)

| Azure capability | What it concretely does for cost | Status | Layer |
|---|---|---|---|
| **Foundry Model Router** | One deployment; a *trained* model routes each prompt to the cheapest LLM within a quality band. 3 deploy-time modes: **Balanced (~1–2%), Cost (~5–6%), Quality**. | GA for OpenAI models; **cross-vendor (DeepSeek/Llama/Grok/Claude) preview**; **only 2 regions** (East US 2, Sweden Central) | Orchestration |
| **APIM `llm-token-limit`** | Per-key TPM + token quota (Hourly→Yearly), any counter key, **pre-calculates prompt tokens to reject over-limit before hitting backend**, 429/403. Multi-provider (OpenAI, Anthropic v2, Vertex). | **GA** | FinOps / gateway |
| **APIM semantic cache** (`llm-semantic-cache-lookup`/`-store`) | Vector-similarity cache on Azure Managed Redis; reuse completions for similar prompts. **Tunable `score-threshold`** (docs warn >0.2 risks mismatch). | **GA**, all tiers | Application |
| **APIM `llm-emit-token-metric`** | Per-consumer token metrics (client IP / API / user) to Azure Monitor + App Insights dashboard. | **GA** | Observability |
| **Azure OpenAI prompt caching** | **Automatic, on by default, can't disable.** Cached input discounted on Standard, **up to 100% off input on PTU**. Needs ≥1,024 identical prefix tokens; 5–10 min TTL (24h extended on newer models). | **GA** | Serving |
| **Batch API** | Async, 24h *target*, **50% cheaper** than global standard; separate quota. No fine-tuned/embeddings/Assistants; 100k req/file. | **GA** | Serving |
| **Provisioned Throughput (PTU)** | Dedicated capacity, **$/PTU/hr regardless of tokens**, latency SLA. **Can't pause** (billing stops only on delete). **Azure Reservations** discount for 1-mo/1-yr. Cache hits don't consume PTU. | **GA** | Serving |
| **Foundry catalog** | 1,900+ models; managed-compute (core-hour) vs serverless (per-token) billing. | **GA** | Model supply |
| **Foundry Agent Monitoring + Continuous Eval** | Dashboard: token usage, latency, success, eval scores from App Insights. Continuous eval on **sampled** responses (default 100 runs/hr). Alerts on token/eval/latency. | **Preview**, no SLA | Observability |
| **Agent Framework** (Semantic Kernel + AutoGen) | OpenTelemetry-based, **step-level causal tracing** across executors (fan-in linked). Payloads opt-in (`EnableSensitiveData=false` default). | GA'ing | Orchestration |
| **Microsoft Cost Management + FinOps for AI** | Subscription/tag budgets, chargeback. Governance-by-caps. | GA | FinOps |

**The critical limitation, stated plainly:** Every Azure enforcement mechanism is **governance-by-caps** — token limits return **429/403 (hard rejection)**; agent alerts are **anomaly notifications, not enforcement**. Azure does **not** natively offer *automated quality-preserving degradation* (drop to a cheaper model / compact context on budget pressure) **gated by a live quality check**. That is the fallback zone.

---

## The decision framework: Azure-first → fallback → trigger

For each need, reach for the Azure lever first. Switch to the fallback **only when the trigger fires.**

### 1. Model routing (send easy work to cheap models)
- **Azure-first:** **Foundry Model Router**, Balanced mode. One deployment, no routing logic to maintain.
- **Fallback:** **RouteLLM** (OSS, self-host) or **LiteLLM** router / **Not Diamond** / **Portkey**.
- **Triggers to leave Azure:** (a) you need routing **outside East US 2 / Sweden Central**; (b) you need **cross-vendor routing at GA** (Azure's is preview); (c) you need **per-request** mode control (Azure mode is deployment-scoped, ~5 min to change); (d) the **input-token markup** makes it uneconomic at your volume — measure `1 − (router_cost / baseline_cost)`, since **Microsoft publishes no fixed savings %.**

### 2. Prompt caching (stable prefixes: system prompt, tools, RAG docs)
- **Azure-first:** **Azure OpenAI prompt caching** — automatic, zero-config, up to 100% off input on PTU. Nothing to build. **Just structure prompts with the stable prefix first** (≥1,024 tokens).
- **Fallback:** none needed — this is Azure's strongest, lossless lever. (Native provider caching also removes most of the "build a cache" rationale.)
- **Trigger:** only if on a model without support, or prefixes shorter than the threshold.

### 3. Semantic caching (repeated/similar queries)
- **Azure-first:** **APIM `llm-semantic-cache`** on Managed Redis; tune `score-threshold` **≤0.2**.
- **Fallback:** **GPTCache** (OSS) if you need finer eviction/embedding control or you're not on APIM.
- **Trigger:** semantic cache **quality risk** — false-positive hits return stale/wrong answers. **Gate every semantic-cache rollout behind an eval set** (see need #6); if Azure's threshold control can't hold quality, move to GPTCache with custom similarity + a stricter eval gate. Start conservative (high similarity required) and loosen.

### 4. Context pruning / compaction (attack the "context snowball")
- **Azure-first:** *no native automated pruning lever.* Use **Agent Framework** middleware hooks + your own compaction (summarize-and-reinitialize).
- **Fallback / add-on:** **LLMLingua** (prompt compression, up to 20×) in an APIM or app-layer step.
- **Trigger:** this is a **near-mandatory hybrid** — and the biggest **win-win**: Chroma "context rot" evidence shows pruning improves quality *and* cost. Build it regardless; Azure won't do it for you.

### 5. Token budgeting & spend caps (prevent the "budget in 4 months" surprise)
- **Azure-first:** **APIM `llm-token-limit`** (per-key TPM/quota, pre-calculated rejection) + **Cost Management budgets** + tags for chargeback.
- **Fallback:** **LiteLLM** for **per-key/user/team/tag/end-customer** budgets with `max_budget`, `budget_duration`, soft budgets, and **per-tool attribution** (tracks User-Agent → Claude Code, Gemini CLI).
- **Trigger:** you need **graceful degradation instead of a hard 429** (LiteLLM reroutes to a cheaper model on budget hit; APIM only rejects), or **finer per-agent/per-end-customer** attribution than APIM's key-level model.

### 6. Step-level cost attribution + quality evaluation
- **Azure-first:** **Foundry Agent Monitoring + Continuous Evaluation** (token + eval per run) and **Agent Framework OpenTelemetry** step-level traces → App Insights.
- **Fallback:** **Langfuse** — observation-level cost **and** LLM-as-judge evals **and** golden-set datasets/experiments in one open-source tool, self-hostable.
- **Triggers to add Langfuse:** (a) Foundry monitoring is **preview/no-SLA** and you need production guarantees; (b) you want cost and eval in **one queryable trace** rather than split across App Insights + a separate toolkit; (c) **watch the gotcha** — Foundry **playground evaluations are on by default and billed**; disable if unused.

### 7. ⭐ Eval-gated cost governance (the real gap — but Azure-first, not Langfuse-first)
> **Correction (2026-07-14):** an earlier draft framed this as a *mandatory Langfuse hybrid*. That under-credited **[Foundry cloud evaluation](https://learn.microsoft.com/en-us/azure/ai-foundry/how-to/develop/cloud-evaluation)**, which is a robust eval engine (built-in + custom evaluators, LLM-as-judge, versioned golden-set datasets, regression testing, on-demand + scheduled + continuous evaluation, CI/CD integration). **The gap is narrower than "Azure can't evaluate."** It is: *evaluation ≠ enforcement.* Foundry **measures and reports** (async, **sampled**, post-hoc — "a pass/fail label, similar to a unit test"); nothing natively **acts** on that verdict in the cost path. The only thing you build is the thin **enforcement binding** — and it can be **fully Azure-native.**

- **Azure-first (the eval brain):** **Foundry cloud evaluation** *is* the eval engine — golden set, LLM-judge, regression, continuous eval. Do not add Langfuse for this half.
- **The enforcement binding (two coexisting patterns — the eval brain + the hands):**
  - **Pattern A — Offline / CI-CD gate (pre-deployment), 100% Azure-native.** Foundry evals in the pipeline; **your CI logic gates the config change** (which model / router mode / cache threshold / prompt ships) on pass/fail. Covers *"don't ship a cheaper config unless quality holds."* No third-party tool.
  - **Pattern B — Online / runtime gate (live traffic).** Foundry **continuous evaluation** detects regression on sampled traffic → **fires an alert** (it's async/sampled, so it's a *drift detector, not a per-request gatekeeper*). Enforce via whatever is already in the request path:
    - *Alert-driven, Azure-native:* continuous-eval alert → **Azure Monitor action group** → **Logic App _or_ Azure Function** → flip **Model Router to Quality mode**, tighten **APIM semantic-cache threshold**, or adjust budget. Closed loop, all Azure, minutes-latency (fine for drift). **Choosing the compute:** use a **Logic App** when the binding is truly "alert → call one management API → notify," it fires rarely, and you want ops to maintain it no-code (native connectors for Monitor/ARM/APIM/Teams handle auth via managed identity). Use an **Azure Function** when there's real decision logic — thresholds, hysteresis (don't flip-flop), reading multiple eval dimensions, or cost math like `1 − (router/baseline)` — or you want it unit-tested and version-controlled as reusable IP, it fires frequently, or it may later move toward the request path. *Rule of thumb: "flip a mode on an alert" → Logic App; anything with branching or arithmetic → Function.* Common hybrid: Logic App as the event shell calling a Function for the decision (usually over-engineering at this size).
    - *Inline per-request:* if you need *every* request gated with graceful downshift and you're **not** fronting traffic with APIM, put the gate in **APIM policy or LiteLLM/app code** — Foundry supplies the offline-validated thresholds, the proxy enforces live.
- **When to add Langfuse/LiteLLM (now much narrower):** only if you want cost + eval in **one queryable trace store** (convenience, not necessity), or need **inline per-request eval-gating with model-downshift** without APIM in the path. Neither is because Azure "lacks evaluation."
- **Trigger:** build the enforcement binding whenever you push routing/caching/compression aggressively **without silently degrading output.** Prefer Pattern A (native) first; add Pattern B (native alert-driven) next; reach outside Azure last.

### 8. Serving economics (self-host or high-volume)
- **Azure-first:** **PTU + Azure Reservations** for sustained high-utilization; **Batch API (−50%)** for async/non-urgent; maximize **prompt-cache rate** (cached tokens don't consume PTU → fewer PTUs needed).
- **Fallback:** **vLLM / TensorRT-LLM / SGLang** on managed compute (or AKS) if you self-host open models — 2–4× throughput, more on long sequences.
- **Triggers / break-evens:** PTU only beats PAYG at **high sustained utilization** (Microsoft's own doc warns "continuous hourly billing at high utilization typically exceeds reservation pricing" — i.e., commit via Reservations once steady). PTU **can't pause** — don't provision for spiky/dev workloads. Batch can't do fine-tuned/embeddings/Assistants.

---

## Governance-by-caps vs. quality-preserving degradation (the core mental model)

| | Azure-native | When you need more |
|---|---|---|
| **Hard cap** (reject at limit) | ✅ APIM 429/403, Cost Management budgets | Sufficient for *safety rails* — do this first, always |
| **Passive optimization** (lossless) | ✅ Prompt caching, Batch, PTU, Model Router | Azure is strong here — prefer native |
| **Graceful degradation** (downshift model/context on pressure) | ⚠️ Model Router *Cost mode* is the closest native lever, but it's deploy-scoped, not budget-triggered | LiteLLM budget-fallback |
| **Eval-gated action** (only degrade if quality holds) | ⚠️ Eval is native & robust (**Foundry cloud evaluation**); the *enforcement binding* is not native but can be built Azure-native (CI/CD gate; Monitor-alert → Model Router/APIM) | Langfuse/LiteLLM only for unified trace store or inline per-request gating |

**Sequence for any team:** hard caps (native, day 1) → passive optimization (native) → attribution + evals (native-preview, add Langfuse if needed) → eval-gated degradation (hybrid, last).

---

## Anti-patterns & documented gotchas (flag these in review)
- **Foundry playground evaluations are ON by default and billed** — disable if unused.
- **PTU cannot be paused** — billing stops only on deletion; never use for spiky/dev traffic.
- **Model Router markup is on input tokens** and there's **no published savings %** — measure per-workload or it may not pay off.
- **Model Router = 2 regions, cross-vendor in preview** — don't design a GA multi-vendor architecture on it yet.
- **Semantic cache score-threshold >0.2 risks wrong-answer cache hits** — never ship semantic caching without an eval gate.
- **Azure enforcement is rejection, not degradation** — a 429 mid-agent-run can break a trajectory; design ret/fallback behavior.
- **"Comparable quality" (Model Router) and quality bands are examples, not SLAs** — treat as starting points to validate, not guarantees.

---

## Adoption path (Crawl / Walk / Run — mirrors FinOps Foundation maturity)
1. **Crawl:** APIM token-limit + Cost Management budgets + tags (safety rails); turn on prompt caching by structuring prompts; move async work to Batch.
2. **Walk:** Foundry Model Router (Balanced); APIM semantic cache with a conservative threshold + a golden-set eval gate; PTU+Reservations if utilization is steady.
3. **Run:** Agent Framework context pruning; Foundry continuous evaluation + step-level attribution (add Langfuse only for a unified trace store); the **eval-gated enforcement binding** — Foundry evals (brain) + Azure-native gate (CI/CD gate, or Monitor-alert → Model Router/APIM action) as reusable internal IP; drop to LiteLLM/app-code only for inline per-request gating without APIM.

---

## Honest limits of this framework
- **Azure claims are verified against official docs** (feature existence, GA/preview, pricing mechanics) — but docs describe *capability, not quality/reliability in your workload.* Validate on your own golden set.
- **The competitive positioning** (where OSS beats Azure) rests on the single strong documented signal (Foundry Eval ⇏ Model Router) plus each OSS tool's own docs — **no independent head-to-head benchmark** was verified. Treat "fall back to X" as *directionally sound*, to be confirmed by a spike.
- **Specific numbers not yet pinned:** exact $/PTU/hr, reservation discount %, Standard cached-input discount %, and Model Router input markup — get these from the live Azure pricing calculator before any break-even decision.
- **Preview risk:** Model Router cross-vendor, Foundry Agent Monitoring, and continuous eval are preview — re-check GA status before committing production architecture.

---

## Sources
**Azure (primary — Microsoft Learn):** [Model Router concept](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/model-router) · [Model Router how-to](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/model-router) · [Foundry Models overview](https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/foundry-models-overview) · [APIM GenAI gateway](https://learn.microsoft.com/en-us/azure/api-management/genai-gateway-capabilities) · [llm-token-limit policy](https://learn.microsoft.com/en-us/azure/api-management/llm-token-limit-policy) · [APIM semantic caching](https://learn.microsoft.com/en-us/azure/api-management/azure-openai-enable-semantic-caching) · [Prompt caching](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/prompt-caching) · [Batch API](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/batch) · [Provisioned throughput](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/provisioned-throughput) · [Agent monitoring dashboard](https://learn.microsoft.com/en-us/azure/ai-foundry/observability/how-to/how-to-monitor-agents-dashboard) · [Foundry observability](https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/observability) · [Agent Framework observability](https://learn.microsoft.com/en-us/agent-framework/workflows/observability)

**Open-source / third-party (primary docs):** [LiteLLM cost tracking](https://docs.litellm.ai/docs/proxy/cost_tracking) · [Langfuse observability](https://langfuse.com/docs/observability/overview) · [RouteLLM](https://github.com/lm-sys/RouteLLM) · [GPTCache](https://github.com/zilliztech/GPTCache)

---

**Bottom line:** Azure covers **most of the token-cost stack natively and well** — routing, caching (lossless, up to 100% off input on PTU), token caps, serving economics, **and a robust evaluation engine (Foundry cloud evaluation)** are all first-party. The framework's job is mostly to **sequence the native levers correctly and avoid the gotchas.** There is exactly **one structural gap — the enforcement binding** that makes an eval verdict *act* on runtime cost (route down / cap / degrade). Even that is buildable **Azure-native** — Foundry evals as the brain, a CI/CD gate or Monitor-alert→Model-Router/APIM action as the hands; reach for LiteLLM/Langfuse only for inline per-request gating or a unified trace store. So the framework is *more* Azure-first than the first draft implied: **Azure-first everywhere, including the eval gate; third-party only at the narrow runtime-enforcement edge.**
