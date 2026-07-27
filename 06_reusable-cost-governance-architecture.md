# Reusable Cost-Governance Architecture for Agentic Applications
### A reference architecture + where it plugs into the build process — for high-volume LLM / agentic / RAG workloads

*A design synthesis of the verified corpus (files 01–05). Not a product — a reusable reference architecture and a process-fit map. Compiled 2026-07-14. Builds on [01_token-economics-research.md](01_token-economics-research.md), [02_agentic-token-spend-research.md](02_agentic-token-spend-research.md), [04_critical-review-cost-control-plane.md](04_critical-review-cost-control-plane.md), [05_azure-first-cost-optimization-framework.md](05_azure-first-cost-optimization-framework.md).*

> **Scope decision, stated up front (and it's fine):** This architecture is for **high-volume / high-token-intensity / quality-sensitive** agentic workloads — RAG at scale, customer-support agents, coding/research agents, multi-tenant AI SaaS. It is **deliberately not** for low-volume, low-cost, or ultra-low-latency use cases; there, the overhead loses money and you should take only the free levers (native prompt caching + a spend cap). The architecture therefore ships with an **adoption gate** that classifies a use case before any of it is applied.

---

## 1. The core idea: two planes, one feedback loop

The single most important design decision: **separate the request path from the governance brain.** Everything latency-sensitive stays in the **data plane**; everything that thinks, measures, and decides stays in the **control plane**, out of band. They're joined by a **config store** (the externalized cost "knobs") so the control plane can change runtime behavior *without a code deploy.*

```
                    ┌──────────── CONTROL PLANE (out of band, latency-insensitive) ────────────┐
                    │                                                                           │
                    │  Foundry Cloud Eval ──(regression alert)──► Decision Binding ──►  CONFIG  │
                    │  (offline CI gate +                         (Logic App / Function)  STORE │
                    │   sampled continuous eval)                   • hysteresis, cost math   │  │
                    │        ▲                                                               │  │
                    │        │ sampled traces / scores                                       │  │
                    │  Telemetry Sink (App Insights / Langfuse)                              │  │
                    │  FinOps (Cost Management, budgets, tags, chargeback)                   │  │
                    └────────┼──────────────────────────────────────────────────────────────┼──┘
                       traces│/metrics                                    reads knobs (route │
                             │                                            mode, cache thresh,│
   ┌─────────────────────────┼──────── DATA PLANE (request path, latency-sensitive) ─────────┼──┐
   │                         │                                            budget)            ▼  │
   │  Agent Orchestrator ─► Context Mgmt ─► GATEWAY (APIM / LiteLLM) ─────────► Model Layer     │
   │  (Agent Framework /     • prune         • token-cap → 429/403            (Foundry / AOAI)   │
   │   Foundry Agent Svc)    • compact       • semantic-cache lookup/store    • native prompt    │
   │  emits OTel traces      • compress       • routing (Model Router)          caching (free)   │
   │                         (LLMLingua)      • emit token-metric                                │
   └────────────────────────────────────────────────────────────────────────────────────────────┘

   The loop: eval detects drift → decision binding tightens a knob in config → data plane reads
   new config → behavior changes → eval re-measures. Aggressive cost-cutting, guardrailed.
```

**Why this shape:** it lets you push the cost levers *aggressively* (route down, loosen cache, compress harder) while the eval gate silently guards quality and auto-reverts on regression — the one defensible capability the market doesn't wire together natively ([file 04](04_critical-review-cost-control-plane.md)), built here Azure-first ([file 05](05_azure-first-cost-optimization-framework.md)).

---

## 2. Components (what each is, which plane, latency/cost note)

### Data plane (in the request path — every ms and token counts)
| Component | Role | Azure-first / fallback | Latency impact |
|---|---|---|---|
| **Agent orchestrator** | Runs the loop; emits OpenTelemetry traces | Agent Framework / Foundry Agent Service · *LangGraph* | — |
| **Context management** | Prune / compact / compress before the call — attacks the "context snowball" (the #1 agentic cost driver) | app-layer + Agent Framework hooks · *LLMLingua* | +50–150 ms if compressing |
| **Gateway (the choke point)** | Single place policy is enforced: token caps, semantic cache, routing, metrics | APIM GenAI gateway · *LiteLLM* | +5–20 ms hop; +30–80 ms on semantic-cache embedding |
| **Model layer** | Inference; **native prompt caching is automatic here** (up to 100% off input on PTU) | Foundry / Azure OpenAI · *vLLM if self-host* | reduces latency & cost |

### Control plane (out of band — latency-insensitive, runs occasionally)
| Component | Role | Azure-first / fallback | Cost note |
|---|---|---|---|
| **Telemetry sink** | Traces, token & cost metrics | App Insights / Log Analytics · *Langfuse for unified trace+eval store* | ⚠️ full-payload logging can exceed savings at scale — **sample it** |
| **Evaluation engine** | Golden-set + LLM-judge; offline (CI) + continuous (sampled prod) | **Foundry cloud evaluation** | the real **"governance tax"** — judge tokens × sample rate |
| **Decision binding** | Consumes eval alerts + cost metrics → adjusts data-plane knobs | Logic App (simple) / **Azure Function** (logic, hysteresis, cost math) | pennies/mo — *not* where cost lives |
| **Config store** | Single source of truth for the knobs (route mode, cache threshold, budgets) | App Configuration / Key Vault | trivial |
| **FinOps** | Attribution, budgets, chargeback, spend alerts | Cost Management + tags | trivial |

---

## 3. Where it plugs into the process of building an agentic app

This is the point of the whole thing: **the architecture is not bolted on at the end — it maps onto each phase of the agentic build lifecycle, and two phases are decision *gates*.**

| Lifecycle phase | What you do here for cost governance | Component introduced | Gate? |
|---|---|---|---|
| **0. Scope / Ideate** | Decide agent vs. simple workflow (avoid the 4–15× multiplier if you don't need it). **Classify the cost tier** by expected volume × token-intensity × task-value × latency tolerance. | — | ⛔ **Adoption gate** — decides *whether and how much* of this architecture applies (see §4) |
| **1. Design** | Design the **context strategy** (what enters context, pruning plan); the **model-tier / routing strategy**; and — critically — **define the golden set + eval metrics now, not later.** Choose gateway (APIM vs LiteLLM). **Externalize the cost knobs** into config. | Config store schema; eval metric definitions | |
| **2. Build** | Implement **behind the gateway from day one** (even as pass-through). **Structure prompts stable-prefix-first** for cache hits. Wire **OTel tracing**. Add the golden-set eval to **CI**. | Gateway, tracing, context mgmt | |
| **3. Evaluate (pre-prod)** | Run Foundry eval vs. golden set; **CI gate blocks any cheaper config unless quality holds** (Pattern A). Establish the **cost/quality baseline**. **Load-test to measure the in-path latency tax.** | Foundry cloud eval (offline) | ⛔ **CI quality gate** — no cost-cutting config ships that regresses quality |
| **4. Deploy** | Turn on **native prompt caching** (free). Start at a **conservative config** (Balanced routing, strict cache threshold). Set **spend caps + budgets**. | Prompt caching, token-cap, budgets | |
| **5. Observe** | **Continuous eval (sampled)** + token metrics + **cost-attribution dashboards** (per agent/step/tenant). | Continuous eval, FinOps dashboards | |
| **6. Optimize (steady state)** | **Close the loop:** aggressively push routing down / loosen cache / add compression — the decision binding **auto-reverts on eval regression.** This is where the ROI is realized. | Decision binding (Pattern B) | |

**Two gates, two timescales:** the **adoption gate** (phase 0) is a one-time architectural decision; the **CI quality gate** (phase 3) runs on every change; the **runtime loop** (phase 6) runs continuously. Design-time decisions (golden set, knob externalization) in phases 1–2 are what make phases 3 and 6 possible — skip them and you can't gate anything later.

---

## 4. The adoption gate: tiers matched to the ROI crossover

Apply only the tier the use case earns. (Dollar thresholds are illustrative engineering estimates, not verified figures — recompute with live Azure pricing.)

| Tier | Adopt when | What you turn on | Overhead |
|---|---|---|---|
| **Tier 0 — Hygiene** *(all apps, always)* | Any LLM use | Native prompt caching (stable-prefix prompts) + spend cap + basic token metrics | ~zero |
| **Tier 1 — Optimized** | Medium volume (~>$5–10k/mo tokens) **or** repetitive queries | + Gateway (semantic cache + routing) + attribution dashboards + **CI eval gate (Pattern A)** | low; +latency tax in-path |
| **Tier 2 — Governed** | High volume **and** high-stakes (wrong answer is expensive) | + Continuous eval + **closed-loop decision binding (Pattern B)** + context pruning + PTU/Reservations economics | the full stack; justified by scale |

**Rule:** *low-volume/low-cost stops at Tier 0 — and that's correct, not a failure.* The eval-gated machinery (Tier 2) is justified **not by token savings** (it costs tokens) but as **insurance** — the guardrail that lets Tier 1's savings run aggressively where a bad answer costs real money (support misinformation, a coding agent shipping a bug, anything regulated).

---

## 5. What makes it *reusable* (a landing zone, not a one-off)

Package once, drop into any agentic project:
1. **IaC module** (Bicep/Terraform): APIM instance + GenAI policies (token-cap, semantic-cache), Foundry project, App Insights, Config store, budgets.
2. **Gateway policy templates** (APIM policy XML *or* LiteLLM config) with the knobs parameterized.
3. **Eval harness scaffold**: golden-set dataset template + evaluator set + the CI gate step.
4. **Decision-binding template**: Logic App *or* Function (per §5 of file 05) reading eval alerts → writing config.
5. **Dashboards**: token/cost per agent-step and per tenant.
6. **The config-knob contract**: route mode, cache threshold, budgets, sample rate — the interface between planes.
7. **The adoption-gate checklist** (§4) so teams self-classify before adopting.

A team consuming this gets Tier 0 for free, opts into Tier 1/2 by flipping config — no re-architecture.

---

## 6. Honest limits & risks
- **Dollar/latency figures are illustrative** — the verified research deliberately did not pin exact prices; run the numbers on live Azure pricing before committing.
- **The in-path latency tax stacks** (gateway + router + semantic-cache embedding + compression can add 100–300 ms) — measure in phase 3; it can disqualify real-time UX regardless of cost math.
- **The eval "governance tax" is real** — a judge model can cost as much as the model you're saving on; size the sample rate, don't judge every response with a frontier judge.
- **Observability is the sleeper cost** — sample telemetry; don't log every full payload at high volume.
- **Preview surface** — Foundry continuous eval and Model Router cross-vendor are preview; re-check GA before production ([file 05](05_azure-first-cost-optimization-framework.md)).
- **Not a moat, and that's fine** — this is internal reusable IP / architect guidance, not a product ([file 04](04_critical-review-cost-control-plane.md) killed the product framing).

---

**Bottom line:** The reusable architecture is **two planes joined by a config store and an eval-gated feedback loop** — data plane (gateway + caching + routing + pruning) captures the savings; control plane (Foundry eval + decision binding + FinOps) guards quality and attributes spend. It plugs into the agentic build lifecycle at **two gates** (adoption in phase 0, CI quality in phase 3) and **one runtime loop** (phase 6), and it **scales its own footprint to the use case** via three adoption tiers — full stack for high-volume/high-stakes agentic and RAG workloads, and honestly *just prompt caching + a cap* for everything else.
