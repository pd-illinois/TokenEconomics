# LIVE.md — running the prototype against real Azure / Foundry

The simulated demo (`python demo.py`) needs nothing. This file is the runbook to make
`python demo.py --live` call **real** Foundry / Azure OpenAI, with Entra ID auth, and
**benchmark our governance layer vs. the Foundry Model Router**.

---

## 0. What I still need from you (the short list)

Provision the resources below (or point me at existing ones) and give me — or drop into
`.env` — these values:

| Value | Where it comes from | Env var |
|---|---|---|
| Endpoint URL | Foundry project / Azure OpenAI resource | `AZURE_OPENAI_ENDPOINT` |
| Cheap chat deployment name | a mini/small model deployment | `AZURE_DEPLOYMENT_CHEAP` |
| Premium chat deployment name | a frontier model deployment | `AZURE_DEPLOYMENT_PREMIUM` |
| **Model Router** deployment name | Foundry `model-router` deployment | `AZURE_DEPLOYMENT_ROUTER` |
| Judge deployment name | can equal the premium one | `AZURE_DEPLOYMENT_JUDGE` |
| Embeddings deployment (optional) | `text-embedding-3-small` | `AZURE_DEPLOYMENT_EMBEDDING` |
| Real per-1K prices | Azure pricing page for those models | `PRICE_*` |
| App Config endpoint (optional) | App Configuration store | `AZURE_APPCONFIG_ENDPOINT` |
| App Insights connection string (optional) | App Insights resource | `APPLICATIONINSIGHTS_CONNECTION_STRING` |

Then: `cp .env.example .env`, fill it in, `az login`, `pip install -r requirements.txt`,
`python demo.py --live`.

---

## 1. Install + sign in

```bash
pip install -r requirements.txt
az login                          # Entra ID; no API key needed or stored
az account set --subscription "<your-subscription-id>"
```

## 2. Provision (skip any you already have)

```bash
RG=rg-tokengov
LOC=eastus2                       # Model Router GA regions: East US 2, Sweden Central
az group create -n $RG -l $LOC

# Azure OpenAI / Foundry account
az cognitiveservices account create -n tokengov-aoai -g $RG -l $LOC \
  --kind OpenAI --sku S0

# Deployments: cheap + premium + model-router + embeddings
az cognitiveservices account deployment create -g $RG -n tokengov-aoai \
  --deployment-name gpt-cheap    --model-name gpt-4o-mini --model-version "2024-07-18" \
  --model-format OpenAI --sku-name Standard --sku-capacity 50
az cognitiveservices account deployment create -g $RG -n tokengov-aoai \
  --deployment-name gpt-premium  --model-name gpt-4o      --model-version "2024-11-20" \
  --model-format OpenAI --sku-name Standard --sku-capacity 50
az cognitiveservices account deployment create -g $RG -n tokengov-aoai \
  --deployment-name model-router --model-name model-router --model-version "2025-11-18" \
  --model-format OpenAI --sku-name GlobalStandard --sku-capacity 50
az cognitiveservices account deployment create -g $RG -n tokengov-aoai \
  --deployment-name text-embed   --model-name text-embedding-3-small --model-version "1" \
  --model-format OpenAI --sku-name Standard --sku-capacity 50
```
> Model names/versions change — check `az cognitiveservices account list-models -n tokengov-aoai -g $RG`
> and the Foundry portal for the exact current `model-router` version.

## 3. Grant yourself data-plane access (Entra ID, not keys)

```bash
ME=$(az ad signed-in-user show --query id -o tsv)
SCOPE=$(az cognitiveservices account show -n tokengov-aoai -g $RG --query id -o tsv)
az role assignment create --assignee $ME \
  --role "Cognitive Services OpenAI User" --scope $SCOPE
```

## 4. (Optional) full-service wiring

```bash
# App Configuration (the real config store)
az appconfig create -n tokengov-appcfg -g $RG -l $LOC --sku standard
az role assignment create --assignee $ME --role "App Configuration Data Owner" \
  --scope $(az appconfig show -n tokengov-appcfg -g $RG --query id -o tsv)
# seed a knob:  az appconfig kv set --name tokengov-appcfg --key "costgov:routing.mode" --value balanced --yes

# Application Insights (telemetry export)
az monitor app-insights component create --app tokengov-ai -g $RG -l $LOC
# put its connectionString into APPLICATIONINSIGHTS_CONNECTION_STRING
```

Fill `.env` with the endpoint (`az cognitiveservices account show -n tokengov-aoai -g $RG
--query properties.endpoint -o tsv`), the four deployment names, real prices, and any
optional endpoints. Then:

```bash
python demo.py --live
```

## 5. What the live run does

1. **Baseline** — premium-all, real calls → real $ and latency.
2. **Arm 1 (our governance)** — balanced routing + semantic cache + prune → real savings, judged quality.
3. **Arm 2 (Foundry Model Router)** — Azure routes everything → real savings, judged quality.
4. **Benchmark table** — cost / savings / quality side by side, so you see whether our
   routing or Azure's wins on *your* traffic. Our layer's per-task budgets + eval-gated
   auto-revert sit on top of whichever wins.

Cost is bounded by `LIVE_MAX_REQUESTS` (default 20) and `LIVE_WORKLOAD_REPEATS` (default 3),
so a full live run costs cents.

## 6. Swapping the inline judge for the managed Foundry cloud-evaluation service

`RealJudge` (inline LLM-as-judge) runs immediately. To use the **managed** Foundry
Evaluations service (datasets, scheduled/continuous eval, the portal), install
`azure-ai-evaluation`, upload `golden_set.json` as a dataset, and replace `RealJudge.score`
with an `azure.ai.evaluation` evaluator call — the `evaluator`/`decision` interfaces are
unchanged, so nothing else moves. (See file 05 §7: Foundry eval is the brain; our decision
binding is the hands.)

---

## Notes / gotchas (from files 05–06)
- **Model Router is GA for OpenAI models only**, in **East US 2 / Sweden Central**; cross-vendor is preview.
- **Prompt caching is automatic** — structure the stable prefix first (providers.py already does).
- **PTU / Batch** are separate cost levers not exercised here (serving-layer, not agent-layer).
- **Auth is Entra ID throughout** — no keys in `.env`, nothing secret to leak.
- **Cost guardrail** — real calls bill real money; keep the caps until you trust the numbers.
