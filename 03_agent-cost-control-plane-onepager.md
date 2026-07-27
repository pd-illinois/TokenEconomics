# Agent-Aware Cost Control Plane — Product One-Pager

*A control layer that makes agentic AI applications 40–90% cheaper without measurably degrading output quality.*

*Working name: **TokenGovernor** (placeholder). Draft 2026-07-13. Companion to [agentic-token-spend-research.md](02_agentic-token-spend-research.md) and [token-economics-research.md](01_token-economics-research.md).*

---

## The problem

Organizations are hitting a **"Tokenpocalypse."** Uber reportedly burned its **entire annual AI budget in four months**; Microsoft, Meta, and Amazon are pulling back internal AI usage; GitHub customers are depleting per-token allotments faster than expected. The root cause is structural, not wasteful behavior:

- **Agents spend tokens as their core mechanism.** Single agents use ~4× and multi-agent systems ~15× (trade press: up to 1000×) more tokens than chat. Token usage alone explains ~80% of agent performance variance.
- **Falling prices make it worse.** Per-token prices for fixed capability fall ~50×/year — so tokens feel free, and consumption explodes faster than prices drop (Jevons paradox). *Total bills rise while unit prices fall.*
- **Spend is diffuse and unattributable.** At Accenture the top consumers were *non-engineers* doing routine tasks. Nobody can see which agent, which loop, which tool-call is burning the money.

**The pain, in one line:** finance sees a scary bill; engineering has no lever to cut it that they trust *won't quietly break output quality.*

---

## Why now

1. The crisis is acute and named in 2026 trade press ("Tokenpocalypse"). *Note: the phrase "Token Economics: the new FinOps for agentic AI" is an **individual community author's post on the Microsoft Community Hub — user-generated content, NOT a Microsoft corporate position**; do not cite it as a Microsoft statement.* The FinOps Foundation does, however, officially recognize "FinOps for AI" as a defined discipline (guidance updated Feb 2026) and flags agentic cost allocation as an unsolved gap.
2. The optimization primitives are **proven but fragmented** — routing (RouteLLM: 95% of GPT-4 quality at >85% lower cost, *single-turn benchmarks only*), prompt caching (up to 90% cheaper on reads), semantic caching (up to 10×), compression (LLMLingua up to 20×). They work individually; quality-preservation on *multi-turn agentic loops* is unproven.
3. Agentic adoption is mainstream, so the token-explosion blast radius is now enterprise-wide.

> **⚠️ Critical-review correction (2026-07-13).** Adversarial verification **falsified the central "no incumbent occupies the integrated agent-aware slot" claim.** LiteLLM already ships per-session dollar budgets, iteration caps, per-agent trace IDs, and budget-triggered model fallback (graceful degradation) — explicitly for Claude Code. Langfuse ships agent-graph tracing, step-level LLM-judge evals, and golden-set datasets. Portkey can switch to a cheaper model on budget hit. **Dobby (dobby-ai.com) is a near-direct competitor** (per-agent budgets + auto-degradation + model tiering/semantic caching, claiming 40–60% savings). The **only unfilled slot** is *eval-gated cost governance* — linking a quality-regression harness to budget enforcement (LiteLLM has budgets but no evals; Langfuse has evals but no enforcement). Treat the sections below as the *original* thesis; the defensible version is the narrower "eval gate" module described in the correction note at the end.

---

## The insight / wedge

> **Existing tools optimize the API call. Agents fail at the trajectory.**

LiteLLM, OpenRouter, Portkey, and Helicone are excellent **gateways** — they see one request at a time. But agentic cost is made across a *multi-step, multi-agent trajectory*: a growing re-transmitted context, sub-agent fan-out, reflection loops, retries. No product governs the loop with loop-level awareness. And every vendor *claims* "cost savings without quality loss" — **none proves it with measurement.**

**Our wedge: be the agent-aware layer that (a) sees and governs the whole trajectory and (b) sells on measured, quality-preserving guarantees.**

---

## What it is

A policy-driven **control plane** that sits between the agent framework and the model providers (composes with, does not replace, existing gateways). Four integrated capabilities that today require stitching four vendors:

| Pillar | What it does | Replaces the manual use of |
|---|---|---|
| **1. Trajectory-aware routing & caching** | Route each step to the cheapest model that holds quality; prompt-cache stable prefixes; semantic-cache repeated sub-queries — all with knowledge of *where in the agent loop* the call sits | RouteLLM + GPTCache + native caching |
| **2. Context governance** | Auto-compaction, pruning, and compression when a trajectory's context bloats; detect "context rot" (paying more for worse output) | LLMLingua + hand-rolled compaction |
| **3. Per-task token budgets w/ graceful degradation** | Set a budget per task/trajectory; as it nears the cap, *automatically* downshift model or compact context instead of a hard cutoff; enforce max-iteration loop bounds | Nothing — this doesn't exist |
| **4. Step-level FinOps + quality guardrail** | Attribute cost to agent/step/tool ("this reflection loop cost $0.40, added nothing"); run every optimization against a **golden-set quality regression harness** before it ships | Helicone/Portkey (call-level only) + no quality harness |

**The differentiated core is #3 and the quality harness in #4** — the two things no incumbent ships.

---

## Differentiation

| vs. | They do | We add |
|---|---|---|
| **LiteLLM / OpenRouter** | Provider aggregation, spend tracking, virtual keys | Trajectory-level governance, budgets with graceful degradation, quality proof |
| **Portkey** | Gateway + observability + budget limits + guardrails | Agent-loop awareness (not per-call), measured quality-preservation, context governance |
| **Helicone** | Call/session observability | Step-level *control*, not just visibility; automated optimization |
| **RouteLLM / Martian** | Model routing | Routing as one pillar inside an integrated, budget-and-quality-governed loop |

**Moat:** the golden-set quality-regression engine + accumulated per-workload data on *which optimization stacks preserve quality*. Nobody has published the compounded quality frontier (route + compress + cache together). Whoever measures it across many customers owns the trust layer. **We sell quality guarantees, not just savings.**

---

## MVP scope (first 90 days)

**Ship the smallest thing that proves the wedge:**

1. **Drop-in proxy / SDK middleware** for one agent framework (LangGraph or the Claude/OpenAI Agents SDK) — sits under the framework, over the providers (or over LiteLLM).
2. **Two levers only:** (a) per-trajectory token budget with graceful model-downshift + hard max-iteration bound; (b) prompt-caching + one-hop routing (strong/weak pair).
3. **The killer feature: the quality harness.** Customer supplies a golden set; we A/B every optimization against it and show a **cost-vs-quality dashboard** — "you saved 58%, quality held at 97%."
4. **Step-level cost attribution** dashboard (agent → step → tool).

**Explicitly out of scope for MVP:** self-hosted serving/quantization (Layer 3 — refer to vLLM), multi-framework support, semantic caching (fast-follow).

**Success metric:** on a real customer agentic workload, demonstrate **≥40% cost reduction at ≥95% retained golden-set quality**, with per-step attribution finance can read.

---

## Business model

- **Open-core:** OSS proxy/SDK (distribution, à la LiteLLM) → paid cloud for the quality harness, budget governance, multi-tenant FinOps, and dashboards.
- **Pricing:** % of *verified* savings, or seat/volume tier — align our revenue with the customer's saved dollars.
- **Land:** one high-spend agentic team → **expand:** org-wide FinOps governance + procurement.

---

## Key risks & honest unknowns

1. **Whitespace is inferential.** Validate no incumbent already ships trajectory-level budgets + quality harness *before* building (Portkey is closest — watch them).
2. **Incumbents extend upward.** LiteLLM/Portkey could add loop-awareness. Mitigate by moving fast on the quality-harness moat and agent-framework-native integrations.
3. **The compounded quality frontier may be workload-specific** — meaning the harness is *more* valuable (it's the only way to know), but harder to productize as a one-size default. Lean in: measurement *is* the product.
4. **"Just use a gateway"** objection — answer with a measured side-by-side: gateway saves X at the call level; we save 2–3X at the trajectory level with proof it didn't break.
5. **Provider-native features** (Anthropic/OpenAI caching, prompt-cache) erode individual pillars over time — so the durable value is *orchestration + measurement*, not any single primitive.

---

## The pitch in three sentences

Agentic AI bills are exploding because agents spend tokens as their mechanism of working, and falling token prices only accelerate consumption. Every cost lever exists — routing, caching, compression, budgets — but they're fragmented across gateways that see one call at a time, and none can prove they preserve quality. **We're the agent-aware control plane that unifies them across the whole trajectory and guarantees, with measurement, that you cut cost without cutting quality.**

---

## Post-review correction: the version that survives adversarial scrutiny

*Added 2026-07-13 after a dedicated critical-review research pass (21 sources, 21/25 claims verified). See [agentic-token-spend-research.md](02_agentic-token-spend-research.md) and the debate in the chat log.*

**What broke:** the "no incumbent" whitespace claim (falsified — LiteLLM, Langfuse, Portkey, Cloudflare, Helicone, and Dobby already ship most pillars); the "Microsoft" citation (it's a community blog, not Microsoft); the "1000× tokens" figure (refuted; coding-specific at best); and "caching provably preserves quality" (refuted — must be measured, not assumed).

**What survives:** the cost driver (Stanford's input-dominated "context snowball," 30× per-task variance), the win-win of context pruning (Chroma "context rot": focused prompts beat bloated ones on quality *and* cost), and one genuinely empty competitive cell — **eval-gated cost governance**.

**The defensible product (narrowed):**
> An **open-source module on top of LiteLLM + Langfuse** (not a competitor to them) that ties an automated quality-regression harness *to* budget enforcement: cost-cutting actions (route down, compress, prune) fire **only when a golden-set eval confirms quality holds**, and auto-revert when it doesn't — proven on **multi-turn agentic** workloads, the benchmark gap nobody has filled.

**Reframed one-liner:** *"Everyone can cap spend; nobody can cap it without silently degrading quality. We're the eval gate that makes automated cost-cutting safe — on multi-turn agents, measured, on your golden set."*

**Before writing code:** install Dobby and LiteLLM session budgets to see how much already exists; get 3 agentic-app teams to confirm eval-gated governance is a painkiller, not a vitamin. The pain is real but buyer demand is *unvalidated* (FinOps-for-AI is early-maturity) — retire that risk first.
