# TokenEconomics Studio

**AI unit economics: predict, govern, learn.**

Development is governed by the project [`constitution`](../docs/09_TOKENECONOMICS_CONSTITUTION.md). The deployed sample agentic RAG solution is the first reference workload used to prove the full lifecycle in practice; it remains outside TokenGov core and integrates through reusable task/trajectory contracts.

A **runnable reference implementation** of the two-plane cost-governance
architecture in [`../docs/06_reusable-cost-governance-architecture.md`](../docs/06_reusable-cost-governance-architecture.md).
The original simulated command-line demo demonstrates the local closed-loop mechanism on
a customer-support example. Studio currently implements the durable Plan, authoritative
Azure admission, policy-bound offline run, observation, and completed-task calibration
path. Candidate-policy optimization, accepted-task economics, deployed RAG trajectories,
and authorized Azure reversion remain future work tracked in
[`../docs/10_FUTURE_BUILD_TASKS.md`](../docs/10_FUTURE_BUILD_TASKS.md).

## Set up the shared environment

FutureTokenPredictor lives at `06_prototype/FutureTokenPredictor` and is installed into
the prototype's shared virtual environment. Python 3.11 or newer is required.

```powershell
cd 06_prototype
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\FutureTokenPredictor[dev]"
```

## Run it

Launch the operator application and open `http://127.0.0.1:8765`:

```powershell
.\.venv\Scripts\python.exe .\studio.py
```

The five views follow one workflow: **Plan -> Govern -> Runs -> Observe -> Reconcile**.
Each run writes its result and request telemetry under `studio_runs/<run-id>/`; predictor
history is durable in `studio_runs/predictor_history.db`.

The five-view structure already fits the constitution and should be extended rather than
replaced. Plan owns forecast evidence; Govern owns policy decisions and provenance; Runs
owns policy-bound execution; Observe owns measured outcomes; Reconcile owns forecast
feedback. The current limits of each view are labeled in Studio so prototype behavior is
not confused with the completed controller.

Studio starts with a report workspace picker. Creating a report issues a durable ID such
as `RPT-20260720-A1B2C3D4`; every Plan, prediction, immutable receipt, Govern handoff,
run, and reconciliation result created from that workspace remains associated with that
ID. Report manifests are stored under `studio_reports/<report-id>/report.json` and can be
renamed, saved, and reopened after a server restart without duplicating child artifacts.

Plan sessions survive server restarts under `studio_plans/<plan-id>/session.json`. A
completed plan writes one immutable receipt to `studio_plans/<plan-id>/receipts/`; the
receipt snapshots normalized intake, predictor output, separate infrastructure status,
pricing provenance, and a SHA-256 identity. Govern handoff references that receipt and
evaluates it against the exact TokenGov policy revision loaded from Azure App
Configuration. Studio fails closed if that policy or its required label cannot be read.

## Deploy the TokenGov policy authority

The smallest meaningful Azure deployment is the dedicated policy stack. It creates one
standard App Configuration store, disables access-key authentication, publishes the
version-controlled `tokengov:policy` JSON document with a `production` label, grants the
Studio identity **App Configuration Data Reader**, and grants a separate policy
administrator **App Configuration Data Owner**.

```powershell
az login
pwsh infra/provision-policy.ps1 -SubscriptionId <subscription-id>
```

For local development, the signed-in user is used for both roles unless explicit object
IDs are supplied. A hosted Studio should use its managed identity for
`-PolicyReaderPrincipalId`; the deployment or release identity should be the policy
administrator. The script preserves unrelated `.env` settings and writes:

```text
TOKENGOV_POLICY_SOURCE=azure
AZURE_APPCONFIG_ENDPOINT=https://<store>.azconfig.io
TOKENGOV_POLICY_KEY=tokengov:policy
TOKENGOV_POLICY_LABEL=production
```

The initial thresholds in `infra/policies/production.json` are governance decisions for
owner review, not measured claims. Mutation is deliberately `manual` and has no allowed
knobs until real evaluation evidence supports a bounded automated policy. Changing the
policy requires a reviewed source change and redeployment; Studio never silently falls
back to a local policy.

The original five-act command-line demonstration remains available:

```bash
cd 06_prototype
python demo.py          # runs the 5-act closed-loop story (simulated)
python dashboard.py     # renders dashboard.html from the run - open it in a browser
```

No API keys are needed. Models are **simulated** so the whole loop runs offline and
deterministically. Costs/latencies are illustrative, not real prices.

## Dashboards (where you *see* the numbers)

- **Local:** `python demo.py` writes `dashboard_data.json`; `python dashboard.py` renders a
  self-contained **`dashboard.html`** (stat tiles, cost-by-scenario / tenant / model bars,
  quality-by-difficulty, the regression->auto-revert table, and the config changelog).
  Zero-dependency, offline, light/dark, colors from the validated data-viz palette.
- **Production (Azure):** the live path emits each request to Application Insights as a
  `costgov.request` event; **`dashboard/workbook.json`** is an Azure Monitor Workbook that
  visualizes cost by model/tenant/difficulty and spend over time. Import it in the portal
  (Monitor -> Workbooks -> New -> Advanced Editor -> paste). The built-in **Foundry Agent
  Monitoring dashboard** covers token/latency/eval out of the box once telemetry flows.

## What the demo shows (5 acts)

| Act | Demonstrates | Architecture piece (file 06) |
|---|---|---|
| **1. Baseline** | Premium model for everything, no cache -> the expensive status quo | — |
| **2. CI quality gate** | Offline eval over the golden set **blocks** an over-aggressive `cost` config before it ships; admits `balanced` | Control plane — **Pattern A** (pre-deploy gate) |
| **3. Governed run** | `balanced` routing + semantic cache + prune + caps -> large savings, quality held, per-tenant attribution | Data plane + FinOps |
| **4. Regression + closed loop** | Someone flips routing to `cost`; **continuous eval catches the hard-segment collapse** (that the mean hides); the **decision binding auto-reverts** and tightens the cache; quality recovers | Control plane — **Pattern B** (the eval->enforcement wire) |
| **5. Overhead economics** | Savings vs. the eval "governance tax" vs. ~free decision binding -> net benefit; config changelog audit trail | file 06 section 6 |

## How the code maps to the architecture (and to Azure)

```
06_prototype/
  config.json          # CONFIG STORE — the knob contract        -> Azure App Configuration
  golden_set.json      # eval reference facts (defined at design time)
  workload.json        # simulated live support traffic
  costgov/
    contracts.py       # SHARED: forecast/execution/actual schemas
    prediction.py      # CONTROL PLANE: predictor adapter        -> Python package / MCP
    policy.py          # CONTROL PLANE: forecast-driven admission
    models.py          # DATA PLANE: model layer (cheap/premium) -> Foundry Model Router / AOAI / Anthropic
    cache.py           # DATA PLANE: semantic cache               -> APIM llm-semantic-cache / GPTCache
    context.py         # DATA PLANE: context pruning (snowball)   -> agent-framework middleware / LLMLingua
    gateway.py         # DATA PLANE: the choke point (route/cache/cap/metric) -> APIM GenAI gateway / LiteLLM
    telemetry.py       # CONTROL PLANE: telemetry + sampling      -> App Insights / Langfuse
    evaluator.py       # CONTROL PLANE: golden-set LLM-judge       -> Foundry cloud evaluation
    decision.py        # CONTROL PLANE: eval->enforcement binding  -> Logic App / Azure Function
    config_store.py    # CONTROL PLANE: knob store + audit trail   -> Azure App Configuration
    reconciliation.py # CONTROL PLANE: telemetry -> predictor actuals
    orchestrator.py    # APP SERVICE: plan/run/evaluate/reconcile
    finops.py          # CONTROL PLANE: attribution + savings      -> Microsoft Cost Management
  studio.py            # local HTTP API and asynchronous run service
  studio.html          # five-view operator interface
  FutureTokenPredictor/# nested predictor repository
  demo.py              # the 5-act end-to-end runner
```

In the original simulated CLI demo, the **feedback loop** (file 06 section 1) is literal:
`evaluator` scores the sampled stream -> `decision.react()` writes local demo knobs into
`config_store` -> the next `gateway` run reads them -> behavior changes. Studio does not
use that legacy mutation path. It executes only settings from an admitted, versioned
Azure policy; unrestricted flat-key App Configuration writeback is disabled.

## Design choices worth knowing

- **Segment-aware gating.** The decision binding gates on the **worst segment**, not the
  mean. In Act 4 the aggregate score (0.96) stays above the floor while the `hard`
  segment collapses to 0.33 — a mean-threshold alert would miss it. This is exactly why
  file 05 recommends an **Azure Function** (branching/logic) over a Logic App for the
  binding when the decision is non-trivial.
- **Evidence-gated reversion.** `evaluation.min_segment_samples` prevents a sparse segment
  from triggering an automatic change, while `evaluation.consecutive_breaches` controls
  how many credible breaches are required. A healthy report resets breach state.
- **Forecast-driven admission.** `policy.select_policy()` multiplies per-task forecast
  cost by workload segment volume, filters candidates below the quality floor or above
  budget, and admits the cheapest remaining policy. The selected policy version is
  carried on every gateway record.
- **Graceful degradation, not hard stops.** On a budget breach the gateway can degrade to
  a cheaper model (config `budgets.hard_cap_action: "degrade"`) instead of a 429 reject —
  the quality-preserving path from file 02/05.
- **Native prompt caching is free.** The gateway applies the ~90% cached-prefix discount
  automatically (stable prompt prefix), mirroring Azure OpenAI / Anthropic prompt caching.
- **The headline savings % is workload-dependent.** On this repetitive support workload,
  savings are dominated by the semantic-cache hit rate; a less repetitive workload saves
  less (routing-only is typically ~30-50%). The prototype shows the *mechanism*, not a
  promised number.

## Plugging in real models

Replace `SimulatedModel.generate()` in `costgov/models.py` with a real call — an Azure AI
Foundry **Model Router** deployment (one endpoint auto-selects the model), Azure OpenAI,
or Anthropic (`claude-haiku-4-5` / `claude-opus-4-8`). Keep the `Answer` return shape and
the rest of the architecture is unchanged.

## Adoption tiers (from file 06 section 4)

The prototype runs "Tier 2" (full closed loop). Turn pieces off via `config.json` to model
lower tiers: `semantic_cache.enabled=false`, `context.prune=false`, routing `quality`, and
skip the `decision.react()` call -> Tier 0/1. Low-volume apps should stop at Tier 0
(native prompt caching + a spend cap), which is the honest recommendation, not a failure.

## Validate

```powershell
.\.venv\Scripts\python.exe -m pytest .\tests -q
.\.venv\Scripts\python.exe -m pytest .\FutureTokenPredictor\tests -q
```

The Studio and predictor tests are deterministic and offline. They do not validate live
Azure credentials, deployed model behavior, current provider prices, or the planned
five-book grounded RAG benchmark.
