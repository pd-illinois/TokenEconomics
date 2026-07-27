# HANDOFF — Token Economics: Cost-Governance for Agentic AI

**Audience:** whoever picks this up next (you in a fresh VS Code session, a teammate, or an
Azure MCP agent). **Goal:** understand the work, deploy the Azure resources, and run the
prototype — simulated in seconds, or live against real Foundry models in minutes.

> **Canonical direction:** Read [`09_TOKENECONOMICS_CONSTITUTION.md`](09_TOKENECONOMICS_CONSTITUTION.md) before planning material work. It defines the validated north star, the deployed agentic RAG solution's role as the first reference workload, the completion test, and the development alignment gate. Where this older handoff differs, the constitution and newer dated decisions take precedence.

**Status at handoff (2026-07-14):**
- ✅ Research complete and fact-checked (files 01–02, 04), product idea critiqued and corrected (03–04), Azure-first framework (05), reference architecture (06).
- ✅ **Working simulated prototype** — runs offline, zero dependencies, verified (`python demo.py`).
- ✅ **Live path built** — real Foundry/Azure OpenAI adapters, Entra ID auth, our-routing-vs-Model-Router benchmark, App Config + App Insights wiring. Code compiles; **not yet run against a real subscription** (needs your Azure resources).
- ✅ **Infra as code** — `provision.sh` (bash) and `infra/main.bicep` (validated, compiles clean).

---

## 1. What this project is (90 seconds)

AI/agentic bills explode because agents spend tokens as their working mechanism (4–15× chat;
see [02](02_agentic-token-spend-research.md)), even as per-token prices fall (~50×/yr median,
[01](01_token-economics-research.md)). We explored a **product** to fix it, **critically
falsified** the "greenfield" thesis ([04](04_critical-review-cost-control-plane.md) — incumbents
already ship most pieces; only *eval-gated cost governance* is a real gap), and pivoted to an
**Azure-first internal framework** ([05](05_azure-first-cost-optimization-framework.md)) and a
**reusable reference architecture** ([06](06_reusable-cost-governance-architecture.md)).

The **prototype** in [`06_prototype/`](06_prototype/) makes file 06 real and runnable.

**Business framing (no jargon):** [see the elevator pitch in the chat / file 03]. In one line:
*cut AI running costs without gambling on quality — a reusable approach with a built-in quality
safeguard that proves the savings are safe and reverts itself if they're not.*

---

## 2. Repo map

```
TokenEconomics/
  01_token-economics-research.md              # token pricing/cost structure/scaling laws
  02_agentic-token-spend-research.md          # why agents are token-hungry + the playbook
  03_agent-cost-control-plane-onepager.md     # the product one-pager (with correction notes)
  04_critical-review-cost-control-plane.md    # adversarial review that reshaped the idea
  05_azure-first-cost-optimization-framework.md  # Azure-first decision framework
  06_reusable-cost-governance-architecture.md    # the reference architecture the prototype implements
  HANDOFF.md                                  # <- you are here
  06_prototype/                               # the runnable prototype
    demo.py               # 5-act sim runner + live/benchmark runner (--live)
    dashboard.py          # renders dashboard.html from dashboard_data.json
    dashboard/
      workbook.json       # Azure Monitor Workbook (production dashboard over App Insights)
    config.json           # the config-knob contract (the "config store")
    golden_set.json       # eval reference facts
    workload.json         # simulated support traffic
    requirements.txt      # live-path deps only (sim needs none)
    .env.example          # live config template (copy to .env)
    README.md             # prototype-focused readme
    LIVE.md               # manual live runbook (az commands, RBAC)
    infra/
      provision.sh        # one-shot idempotent provisioner, bash (writes ../.env)
      provision.ps1       # one-shot idempotent provisioner, PowerShell (writes ../.env)
      main.bicep          # declarative infra (Azure MCP / az deployment)
      main.parameters.json
    costgov/              # the package
      models.py           # DATA PLANE simulated models
      providers.py        # DATA PLANE real Azure models + Model Router + judge (live)
      cache.py            # DATA PLANE semantic cache (local cosine or real embeddings)
      context.py          # DATA PLANE context pruning (the "snowball")
      gateway.py          # DATA PLANE choke point: route + cache + cap + metrics
      telemetry.py        # CONTROL PLANE telemetry sink + sampling
      evaluator.py        # CONTROL PLANE golden-set eval (offline + continuous)
      decision.py         # CONTROL PLANE eval->enforcement binding (auto-revert)
      config_store.py     # CONTROL PLANE knob store + audit trail
      finops.py           # CONTROL PLANE cost attribution + savings
      azure_integrations.py  # CONTROL PLANE optional App Config + App Insights (live)
```

---

## 3. Architecture recap (what the prototype implements)

Two planes joined by a **config store** and an **eval-gated feedback loop** (full detail in
[06](06_reusable-cost-governance-architecture.md)):

- **Data plane** (request path): orchestrator → context pruning → **gateway** (routing +
  semantic cache + token caps + metrics) → model layer (native prompt caching). *Captures the savings.*
- **Control plane** (out of band): telemetry → **eval** (Foundry cloud eval) → **decision
  binding** (Logic App/Function) → **config store** → FinOps. *Guards quality, attributes spend.*
- **The loop:** eval detects drift → decision binding tightens a knob in config → data plane
  reads it → behavior changes. No code deploy.

---

## 4. Quickstart A — simulated (no Azure, ~2 seconds)

```bash
cd 06_prototype
python demo.py          # baseline -> CI gate -> governed run -> regression + auto-revert -> FinOps
python dashboard.py     # renders dashboard.html - open it in a browser
```
Runs the 5-act story and writes `dashboard_data.json` + a visual **`dashboard.html`** (stat
tiles, cost bars, quality, the closed-loop table, the changelog). Costs are simulated. This
is the fastest way to see the whole architecture behave — and to *see* the numbers.

### Where is the dashboard?
- **Local / demo:** `dashboard.html` (generated by `dashboard.py`) — self-contained, offline, light/dark.
- **Production:** `dashboard/workbook.json` — an **Azure Monitor Workbook** over the
  `costgov.request` events the live path sends to Application Insights (import via Monitor ->
  Workbooks -> Advanced Editor). Plus the built-in **Foundry Agent Monitoring dashboard**
  (token/latency/eval) once telemetry flows. The dashboard is a *read-only control-plane view*
  — it observes; enforcement stays in the decision binding.

---

## 5. Quickstart B — live on real Azure

### Step 1 — Deploy resources (pick ONE)

**Option 1 — Azure MCP in VS Code (recommended for you).**
Tell the Azure MCP agent something like:
> "Get my signed-in user object id. Create resource group `rg-tokengov` in `eastus2`. Deploy
> `06_prototype/infra/main.bicep` to it, passing `principalId=<that object id>`. Then show me
> the deployment outputs (`endpoint`, `appConfigEndpoint`, `appInsightsConnectionString`)."

The template creates: an AI Services (OpenAI) account, four deployments (`gpt-cheap`,
`gpt-premium`, `model-router`, `text-embed`), the **Cognitive Services OpenAI User** role
assignment for you, and optional App Configuration + Application Insights. It compiles clean
(validated). **Verify model names/versions** for your region first — the MCP agent can run
`az cognitiveservices account list-models` if a deployment fails.

**Option 2 — one-shot script (auto-writes `.env`).** Pick your shell:
```bash
# bash / macOS / Linux / Git Bash
az login && az account set --subscription <id>
bash infra/provision.sh                 # from the 06_prototype folder
```
```powershell
# PowerShell / Windows (VS Code default terminal)
az login ; az account set --subscription <id>
pwsh infra/provision.ps1                # from the 06_prototype folder
#   override defaults e.g.:  pwsh infra/provision.ps1 -Loc swedencentral -DeployOptional:$false
```
Both are idempotent, create the same resources + Entra RBAC, and write `../.env` for you
(backing up any existing `.env`). `provision.ps1` parses clean; `provision.sh` passes `bash -n`.

**Option 3 — manual:** follow [`LIVE.md`](06_prototype/LIVE.md) step by step.

### Step 2 — Configure `.env`
- `provision.sh` writes `.env` for you.
- With Bicep/MCP, `cp .env.example .env` and paste the deployment **outputs** in:
  `AZURE_OPENAI_ENDPOINT` = `endpoint`, plus the four deployment names (as created), and
  `AZURE_APPCONFIG_ENDPOINT` / `APPLICATIONINSIGHTS_CONNECTION_STRING` from the outputs.
- **Set real `PRICE_*` values** from the Azure pricing page for the models you deployed —
  this is what makes the cost numbers accurate.

### Step 3 — Run live
```bash
pip install -r requirements.txt
az login                          # Entra ID; no keys anywhere
python demo.py --live
```

---

## 6. Reading the live benchmark

`--live` runs three real passes and prints a comparison table:

| arm | what it is | what to look at |
|---|---|---|
| **premium baseline** | every request → premium model, no cache | the expensive status quo ($ and latency) |
| **our governance (arm 1)** | our gateway routes easy→cheap, semantic-caches, prunes | $ saved **and** judged quality held? |
| **model router (arm 2)** | Foundry Model Router picks the model | Azure's routing $ and quality |

**Interpretation:** compare *dollars saved* against *judged quality* across arms. Whichever
routing wins on your traffic, our layer's value-add sits on top: **per-task budgets +
eval-gated auto-revert** (the one gap incumbents don't wire together — see [04](04_critical-review-cost-control-plane.md)/[05](05_azure-first-cost-optimization-framework.md)).

---

## 7. Code → Azure mapping (what each module becomes in production)

| Module | Role | Production Azure service |
|---|---|---|
| `models.py` / `providers.py` | model layer | Foundry Model Router / Azure OpenAI |
| `cache.py` | semantic cache | APIM `llm-semantic-cache` (Redis) / GPTCache |
| `context.py` | context pruning | Agent Framework middleware / LLMLingua |
| `gateway.py` | policy choke point | APIM GenAI gateway / LiteLLM |
| `telemetry.py` | telemetry sink | App Insights / Log Analytics (`azure_integrations.py` wires it) |
| `evaluator.py` / `providers.RealJudge` | eval engine | Foundry cloud evaluation |
| `decision.py` | eval→enforcement binding | Logic App / **Azure Function** (per [05](05_azure-first-cost-optimization-framework.md) §7) |
| `config_store.py` | knob store + audit | Azure App Configuration (`azure_integrations.py` wires it) |
| `finops.py` | attribution + savings | Microsoft Cost Management |

---

## 8. Config knobs (`config.json`) — the control↔data contract

| Knob | Effect |
|---|---|
| `routing.mode` | `balanced` (easy→cheap, hard→premium) / `cost` (cheap all) / `quality` (premium all) |
| `semantic_cache.enabled` / `score_threshold` | cache on/off; higher threshold = stricter match = fewer wrong hits |
| `context.prune` / `max_context_items` | cap re-sent history (attacks the snowball) |
| `budgets.per_tenant_usd_per_run` / `hard_cap_action` | spend cap; `degrade` (cheaper model) vs `reject` (429) |
| `evaluation.sample_rate` / `min_quality` | fraction of live traffic judged; the quality floor the decision binding enforces |

Turning knobs off models the **adoption tiers** ([06](06_reusable-cost-governance-architecture.md) §4): Tier 0 (prompt caching + cap only) → Tier 1 (+ routing/cache/CI gate) → Tier 2 (+ closed loop). Low-volume apps should stop at Tier 0 — that's correct, not a shortfall.

---

## 9. Security & cost guardrails
- **Auth is Entra ID everywhere** (`DefaultAzureCredential`). No API keys in `.env`, nothing secret to leak. `.env` is gitignored.
- **Live runs bill real money.** Bounded by `LIVE_MAX_REQUESTS` (20) and `LIVE_WORKLOAD_REPEATS` (3) → a full run costs cents. Keep the caps until you trust the numbers.
- **Governance tax is real** — the eval judge costs tokens; `evaluation.sample_rate` sizes it. Don't judge every response with a frontier judge.
- **Telemetry can be the sleeper cost** — don't log full payloads at high volume; sample.

---

## 10. Known limitations / not-yet-verified (read before trusting output)
1. **Live path is unproven against a real subscription** — code compiles and imports, but the actual Azure calls haven't run here. Expect 1–2 small fixes on first `--live` (exact Model Router version string, an SDK field, a price). Paste errors and they're quick to fix.
2. **Model names/versions in the infra files may drift** — verify with `az cognitiveservices account list-models` for your region; Model Router is GA only in **eastus2 / swedencentral** and for OpenAI models (cross-vendor preview).
3. **Simulated numbers are illustrative** — the sim's 98% savings reflects a highly repetitive workload dominated by cache hits; real savings depend on your traffic (routing-only is typically ~30–50%).
4. **`RealJudge` is an inline LLM-judge**, not the managed Foundry Evaluations service — swap per [`LIVE.md`](06_prototype/LIVE.md) §6 when you want datasets/scheduled eval; interfaces don't change.
5. **APIM / Logic App / Function are represented in-process**, not deployed — the prototype proves the *loop logic*; production moves the gateway to APIM and the decision binding to a Function.

---

## 11. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `LIVE MODE NEEDS CONFIG` | `.env` not filled | run `provision.sh` or copy `.env.example` → `.env` |
| `DefaultAzureCredential failed` | not logged in / no RBAC | `az login`; ensure **Cognitive Services OpenAI User** on the account |
| `DeploymentNotFound` / 404 | deployment name mismatch | match `.env` names to what was created (`gpt-cheap`, etc.) |
| model deploy fails in Bicep/script | model/version/region/quota | `az cognitiveservices account list-models -n <acct> -g <rg> -o table`; adjust params |
| 429 mid-run | throughput/quota | lower `LIVE_MAX_REQUESTS`, raise deployment capacity, or retry |
| `model-router` version error | version string outdated | check current version in Foundry portal; set `ROUTER_VER` / `routerVersion` |

---

## 12. Suggested next steps
1. **First live run** — deploy via MCP/Bicep, run `--live`, share the benchmark table; fix any real-call issues.
2. **Real prices** — fill `PRICE_*` from the pricing page so cost is accurate; recompute the ROI crossover from [06](06_reusable-cost-governance-architecture.md) §4/§6.
3. **Swap in managed Foundry cloud eval** (LIVE.md §6) for datasets + continuous eval.
4. **Productionize one lever** — move the gateway to APIM (token-limit + semantic-cache policies), the decision binding to an Azure Function.
5. **Pick a real pilot workload** — a high-volume, high-stakes agent/RAG app (the tier where this pays off), and measure the real compounded quality frontier on its golden set.

---

## 13. Provenance
Findings were produced via multi-source, adversarially fact-checked research passes; named-company/trade-press claims are flagged as unverified, and a citation misattribution (the "new FinOps for agentic AI" phrase is a **community blog**, not a Microsoft statement) was caught and corrected. Azure capability claims come from official Microsoft Learn docs. Dollar/latency figures throughout are **illustrative engineering estimates**, not verified prices. See each file's sources section and the correction addenda in [02](02_agentic-token-spend-research.md)/[03](03_agent-cost-control-plane-onepager.md)/[05](05_azure-first-cost-optimization-framework.md).
