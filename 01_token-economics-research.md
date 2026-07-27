# The Economics of Tokens in Large Language Models
### A balanced assessment of where LLM token costs are, where they're heading, and what to do about it

*Research method: 5 search angles → 20 sources fetched → 91 claims extracted → 25 verified under 3-vote adversarial fact-checking (24 confirmed, 1 refuted). Sources are primarily peer-reviewed arXiv papers plus Epoch AI, Stanford HAI, and vendor engineering references.*

*Compiled 2026-07-13.*

---

## The one fact that dominates everything

The per-token price to reach a **fixed capability level** is collapsing at a **median of ~50× per year** (range 9×–900× across benchmarks), and on data restricted to post-January-2024 models that median **accelerates to ~200×/year**, with the fastest trends near 900×/year. Reaching GPT-4-level performance on PhD-level science questions (GPQA Diamond) got ~40× cheaper per year. This is far steeper than Moore's Law. ([Epoch AI](https://epoch.ai/data-insights/llm-inference-price-trends))

Corroborating this from independent angles: a16z's "LLMflation" analysis found ~10×/year cost declines for equivalent performance and a **1,000× drop over three years** (Nov 2021–Nov 2024); Stanford's 2025 AI Index found the cost to hit GPT-3.5-level performance fell **~280-fold** between Nov 2022 and Oct 2024. ([a16z](https://a16z.com/llmflation-llm-inference-cost/), [Stanford HAI 2025 AI Index](https://hai.stanford.edu/ai-index/2025-ai-index-report))

**The critical distinction:** this is the price for a *frozen* capability. The price of the *frontier* is not falling — you pay roughly the same to access the best model available at any given moment. Both things are true at once, and confusing them is the most common error in this space.

**Important caveat, flagged by the source itself:** Epoch AI warns the recent ~200–900×/year acceleration is too new to know whether it persists, and part of it likely reflects aggressive competitive/loss-leader pricing rather than true marginal serving cost. Treat the acceleration as *provisional*.

---

## Part 1 — How per-token pricing actually works

Providers meter three distinct token classes, each priced differently (USD per million tokens):

- **Input (prompt) tokens** — cheapest
- **Output (generated) tokens** — most expensive, typically 3–5× input
- **Cache-hit tokens** — cheapest of all; Anthropic prices cache *reads* at **0.1× (one-tenth) of base input**, with a cache-*write* premium ([Claude prompt-caching docs](https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching))

Artificial Analysis reports a "blended" price using a default **7:2:1 cache-hit:input:output** weighting to make cross-model comparison tractable. Raw API pricing varies by up to **~100× (two orders of magnitude)** across models. ([Artificial Analysis](https://artificialanalysis.ai/), [FrugalGPT](https://arxiv.org/abs/2305.05176))

> **A myth this research killed:** the intuitive claim that "output tokens dominate latency while input length is negligible" was **refuted 0–3** under adversarial verification. The real asymmetry is not simply output-vs-input — it's the two-regime compute structure below.

---

## Part 2 — The cost structure underneath the price

LLM inference runs in **two distinct regimes**, and this split is the structural root of nearly everything else:

| Regime | What it does | Bottleneck | Utilization |
|---|---|---|---|
| **Prefill** (first token) | Processes the whole prompt in parallel | **Compute-bound** (FLOPs) | High — up to **76% MFU** on PaLM 540B |
| **Decode** (each subsequent token) | Generates one token at a time, autoregressively | **Memory-bandwidth-bound** | Low — latency-bound (~29ms/token, PaLM 540B, int8, low batch) |

The key economic consequence: for token *generation*, **HBM memory bandwidth — not peak FLOPs — predicts cost.** ([Databricks](https://www.databricks.com/blog/llm-inference-performance-engineering-best-practices), [Pope et al., *Efficiently Scaling Transformer Inference*](https://arxiv.org/abs/2211.05102))

Three structural root causes of inefficiency, per the efficient-inference survey literature: **(1)** large model size, **(2)** quadratic-complexity attention, **(3)** autoregressive decoding. Mitigations organize into **data-level, model-level, and system-level** tiers. ([Survey on Efficient Inference for LLMs](https://arxiv.org/abs/2404.14294), [Towards Efficient Generative LLM Serving](https://arxiv.org/abs/2312.15234))

**The serving-system levers** that convert this structure into lower prices:
- **PagedAttention / vLLM** — prior systems wasted **60–80% of KV-cache memory** to fragmentation/duplication (using only ~20–38%); OS-paging-inspired allocation achieves near-zero waste and **2–4× throughput**. ([Kwon et al., SOSP 2023](https://arxiv.org/abs/2309.06180))
- **Continuous batching** — **10–20× throughput** over naive batching, explicitly trading latency for throughput (e.g. ~4× latency for ~14× throughput on 1×A100). ([Databricks](https://www.databricks.com/blog/llm-inference-performance-engineering-best-practices))
- **Multiquery / grouped-query attention** — sharing key/value heads cuts KV-cache memory enough to scale context length up to **~32×**; GQA is now the field standard. ([Pope et al.](https://arxiv.org/abs/2211.05102))

---

## Part 3 — The training-side economics (scaling laws)

This is where the academic literature is strongest and most decision-relevant:

- **Chinchilla (Hoffmann et al., 2022)** — compute-optimal training scales parameters and tokens *equally* (double the model → double the data). It showed 2022 models were badly *undertrained*: a **70B Chinchilla beat Gopher 280B, GPT-3 175B, and MT-NLG 530B**. Data allocation, not parameter count, drives efficiency. ([arXiv:2203.15556](https://arxiv.org/abs/2203.15556))

- **Beyond Chinchilla (Sardana et al., ICML 2024)** — the pivotal *inference-aware* correction. When you expect **large inference demand (~1B+ requests)**, it's economically optimal to **deliberately over-train smaller models** on far more data — quality keeps improving up to ~**10,000 tokens/parameter** (vs Chinchilla's ~20). This is exactly the Llama-style strategy that lowers *deployed* token cost. ([arXiv:2401.00448](https://arxiv.org/abs/2401.00448))

- **Scaling Laws for Precision (Kumar et al., 2024, 465+ runs)** — low-precision training reduces "effective parameter count"; post-training quantization degradation *grows with pretraining data*, so extra tokens can become **actively harmful** for a model you later quantize. Training larger models in lower precision can be compute-optimal. ([arXiv:2411.04330](https://arxiv.org/abs/2411.04330))

- **Distillation Scaling Laws (2025)** — predict a student model's performance from a compute budget and its student/teacher split, making distillation a *quantifiable* cost lever rather than a black art. ([arXiv:2502.08606](https://arxiv.org/abs/2502.08606))

There's a real tension worth naming: over-training (Beyond-Chinchilla) and later-quantizing (Precision laws) **pull in opposite directions** — the extra data that makes an over-trained model cheap to serve at full precision is the same data that makes it degrade more when quantized.

---

## Part 4 — A fair, balanced assessment of the future

**What's genuinely likely:**
- **Unit prices for a fixed capability keep falling sharply.** Multiple independent measurements agree, driven by *both* hardware (~30%/year cheaper, ~40%/year more efficient per Stanford HAI) *and* algorithmic/architectural gains. This is the best-supported claim in the entire body of evidence.

**The honest counterweight — why falling prices don't mean falling bills:**
- **Total spend rises even as unit prices fall.** Reasoning models (long chains-of-thought) and agentic workflows (multi-step tool use, large contexts) explode per-task token consumption. This is the **Jevons paradox** of tokens: cheaper tokens induce far more token use.
- **The macro sustainability question is unresolved.** Sequoia's "$600B question" frames the gap between AI capex and the revenue needed to justify it — some current pricing is competitive subsidy, not marginal cost. ([Sequoia](https://www.sequoiacap.com/article/ais-600b-question/))

**Where the evidence is genuinely thin (state this plainly):** the verified claims are strong on *empirical pricing, serving systems, and scaling-law economics* but **weak on governance** — energy/sustainability, compute-market concentration, regulation, and pricing mechanism-design were not well-covered by surviving sources. Treat conclusions in that dimension as under-evidenced.

---

## Part 5 — What can actually be done (the management toolkit)

Because unit prices fall but total spend rises, **the lever is the cost stack, not betting on any single price cut.** Ordered roughly by ease-of-adoption:

**Practitioner / application layer** (evidence-backed):
1. **Prompt caching** — ~**10× cheaper** on reused context; the single highest-ROI move for RAG and agents with stable system prompts. ([Claude docs](https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching))
2. **Model routing / cascades** — send easy queries to cheap models, hard ones to strong models. **FrugalGPT** matched GPT-4 accuracy at up to **~98% lower cost**; learned routers cut cost **>2×** while preserving quality. ([FrugalGPT](https://arxiv.org/abs/2305.05176), [routing research](https://arxiv.org/abs/2406.18665))
3. **Output-token discipline** — since output is the most expensive class and decode is the bandwidth-bound regime, constraining generation length is a direct cost lever.

**Serving / infrastructure layer:**
4. **Continuous batching + PagedAttention** (vLLM) — 10–20× and 2–4× throughput respectively; table stakes for anyone self-hosting.
5. **Quantization** — reduces effective serving cost, *but* mind the precision-scaling caveat for over-trained models.

**Model-supply layer:**
6. **Over-train smaller models** (Beyond-Chinchilla) when inference demand is high — amortize training cost across cheap serving.
7. **Distillation** — now recipe-guided by distillation scaling laws.

**Policy / governance layer** (least-evidenced, flagged as open):
8. Pricing transparency, energy/sustainability accounting, and market-concentration oversight — identified as important but under-sourced in this research.

---

## Key open questions (unresolved by the evidence)

1. **Is the ~200×/year acceleration real or transient?** Epoch itself won't commit — it may be a few frontier models plus loss-leader pricing.
2. **Do exploding reasoning/agentic token counts outpace per-token price declines**, so total spend rises despite cheaper tokens?
3. **How much of provider pricing is marginal cost vs. margin/subsidy/strategy** — and what regulatory or mechanism-design frameworks (if any) would make it transparent?
4. **The energy, sustainability, and market-concentration dimensions** need dedicated sourcing beyond this technical corpus.

---

## Verified source list

**Primary / peer-reviewed (arXiv):** [Chinchilla 2203.15556](https://arxiv.org/abs/2203.15556) · [Beyond-Chinchilla 2401.00448](https://arxiv.org/abs/2401.00448) · [Scaling Laws for Precision 2411.04330](https://arxiv.org/abs/2411.04330) · [Distillation Scaling Laws 2502.08606](https://arxiv.org/abs/2502.08606) · [Kaplan Scaling Laws 2001.08361](https://arxiv.org/abs/2001.08361) · [Efficiently Scaling Transformer Inference 2211.05102](https://arxiv.org/abs/2211.05102) · [PagedAttention/vLLM 2309.06180](https://arxiv.org/abs/2309.06180) · [Survey on Efficient Inference 2404.14294](https://arxiv.org/abs/2404.14294) · [Efficient Generative LLM Serving 2312.15234](https://arxiv.org/abs/2312.15234) · [FrugalGPT 2305.05176](https://arxiv.org/abs/2305.05176) · [LLM Routing 2406.18665](https://arxiv.org/abs/2406.18665)

**Data / industry:** [Epoch AI — Inference Price Trends](https://epoch.ai/data-insights/llm-inference-price-trends) · [Stanford HAI 2025 AI Index](https://hai.stanford.edu/ai-index/2025-ai-index-report) · [Databricks — Inference Performance](https://www.databricks.com/blog/llm-inference-performance-engineering-best-practices) · [Claude Prompt Caching](https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching) · [a16z — LLMflation](https://a16z.com/llmflation-llm-inference-cost/) · [Artificial Analysis](https://artificialanalysis.ai/) · [Sequoia — AI's $600B Question](https://www.sequoiacap.com/article/ais-600b-question/)

---

**Bottom line:** Token economics is governed by one robust fact — *the price of a fixed capability falls ~50× a year* — and one honest complication — *total spend still rises because demand and reasoning/agentic token use grow faster.* The winning strategy is not to wait for prices to drop but to **engineer the cost stack**: cache aggressively, route by difficulty, constrain output, quantize and distill deliberately, and (on the supply side) over-train small models for cheap serving. The technical and scaling-law evidence for this is strong; the governance, energy, and market-structure questions remain genuinely open.
