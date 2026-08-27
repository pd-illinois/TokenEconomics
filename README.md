# TokenEconomics

**AI unit economics: predict, govern, learn.**

Per-token model prices keep falling, yet agent bills can still rise because agents execute
long, branching, stochastic trajectories. TokenEconomics plans and governs the unit that
maps to business value: **cost per accepted task**, not cost per token.

```text
predict -> compare policy -> admit -> execute -> evaluate -> respond -> reconcile -> learn
```

The architecture separates two concerns:

- **FutureTokenPredictor** provides feed-forward workload and model-cost forecasts before
  execution.
- **TokenGov** connects immutable forecasts to policy, runtime evidence, quality
  evaluation, bounded response, and reconciliation.

TokenEconomics composes existing Azure and Microsoft primitives rather than replacing
them. Current outputs are research-prototype evidence, not guaranteed savings, quality,
production readiness, or calibrated tail-risk claims.

## Current scope

Studio provides a five-view workflow: **Plan -> Govern -> Runs -> Observe -> Reconcile**.
Plan forecasts Foundry model usage and commercial meter stacks for Microsoft Copilot,
Copilot Studio, Cowork, Work IQ, and GitHub Copilot. New plans preserve versioned workload
analysis, pricing evidence, meter stacks, and immutable receipt identities.

The repository also contains a policy-bound Microsoft Foundry RAG adapter that captured
one measured reference trajectory through a Foundry prompt agent, Foundry IQ knowledge
base, MCP retrieval, Azure AI Search, cited synthesis, and provider-reported token usage.
Workload-specific Foundry, Search, MCP, and corpus translation remains under `rag/`;
reusable TokenGov contracts remain under `costgov/`.

### Latest progress

| Milestone | Status | Evidence boundary |
|---|---|---|
| Studio Plan release hardening | Complete | Experience-led intake, deterministic workload analysis, immutable schema-5 receipts, and saved-plan restoration |
| Copilot and GitHub economics | Complete | Subscriptions, entitlements, Microsoft Copilot Credits, GitHub AI Credits, model tokens, and resource meters remain separate |
| Foundry model release `2026-08-25.2` | Complete | 98 sourced OpenAI/Anthropic offerings; 50 verified coordinator models are selectable |
| Framework-neutral trajectory contract | Complete | Stable workload, task, trajectory, segment, prediction, policy, run, and trace identities |
| Foundry RAG adapter (TE-003) | Complete | Live report `RPT-20260825-3C4ABA0C` preserves one policy-bound deployed trajectory |
| Experiment manifest (TE-004) | Ready | Next step is pinning the complete comparable experiment definition |

The current measured local regression boundary is **133 TokenEconomics tests** and
**500 FutureTokenPredictor tests**. This proves the local contracts and modeled
calculations at the tested revision; it is not production-capacity evidence.

The remaining end-to-end work is material: representative experiment manifests,
explicit accepted-task outcomes, segment-level sample sufficiency, complete multi-meter
trajectory economics, policy-candidate comparison, calibrated budget-risk evidence,
bounded response, billing reconciliation, and predictor learning.

## Run Studio locally

Prerequisites: **Python 3.11+**, **git**, and PowerShell.

```powershell
git clone https://github.com/pd-illinois/TokenEconomics.git
cd TokenEconomics
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".\FutureTokenPredictor[dev]"
.\.venv\Scripts\python.exe .\studio.py
```

Open <http://127.0.0.1:8765>.

Studio stores local reports, plans, runs, and reconciliation evidence in ignored
root-level runtime directories. These files survive server restarts but are not committed.

## Foundry model release

Studio loads the versioned catalog from
`data/model_catalogs/foundry-model-release.v2.json`. The complete OpenAI and Anthropic
inventory contains 98 offerings across text, embeddings, image, video, audio, and
specialized modalities. The full inventory is visible, while selectors fail closed to 50
coordinator-capable offerings with verified, model-specific input and output pricing.
Catalog presence, coordinator capability, and selector eligibility are distinct.
Historical catalog releases and receipts remain immutable.

## Repository layout

```text
TokenEconomics/
  studio.py                  # local HTTP API and asynchronous run service
  studio.html                # five-view operator interface
  plan_studio.py             # Plan-only release boundary
  costgov/                   # reusable planning, policy, telemetry, and contracts
  data/                      # versioned commercial, model, and schema evidence
  FutureTokenPredictor/      # model/workload forecasting component
  rag/                       # Foundry RAG reference-workload adapter
  infra/                     # Azure policy and reference infrastructure
  scripts/                   # release, regression, and live-proof utilities
  tests/                     # TokenEconomics integration and contract tests
```

The original deterministic five-act simulation remains available:

```powershell
.\.venv\Scripts\python.exe .\demo.py
.\.venv\Scripts\python.exe .\dashboard.py
```

It uses simulated models and illustrative costs; it is mechanism evidence, not a current
provider-price or savings claim.

## Azure policy authority

The policy deployment creates an Azure App Configuration store, disables access-key
authentication, gives Studio a read-only runtime identity, and reserves publication for a
separately authorized identity:

```powershell
az login
pwsh .\infra\provision-policy.ps1 -SubscriptionId <subscription-id>
```

Studio fails closed when the configured Azure policy or required provenance cannot be
read. Browser code never receives policy-publisher credentials.

## Validate

Run the Studio-owned suite from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Run FutureTokenPredictor through its evidence runner:

```powershell
.\.venv\Scripts\python.exe .\FutureTokenPredictor\scripts\run_tests.py tests --expect pass
```

These suites validate local contracts and modeled calculations. They do not independently
validate live Azure credentials, deployed model behavior, private pricing, accepted-task
quality, or production capacity.
