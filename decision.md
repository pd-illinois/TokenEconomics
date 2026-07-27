# TokenEconomics — Decisions & Pending Items

*Working log for the cost-governance prototype going live on Azure. Paused 2026-07-14. Resume from "Pending / Next" at the bottom.*

**Subscription:** MCAPS-iCSU-pd-America-sub (`0c2e0734-5fab-424f-88a0-1693380415fd`) · tenant `16b3c013-...` · user `pdhiman@microsoft.com` (objectId `6b3b7ee7-8197-4908-984c-0727feafb545`)

---

## 1. What's the goal
Take the in-process prototype in `06_prototype/` (two planes + eval-gated loop) and:
1. Run it live on real Azure models (done).
2. Add a real agentic-RAG workload so the benchmark's quality axis is meaningful (in progress).
3. **Option (b): promote the in-process gateway / cache / decision-binding to real Azure services** (in progress — blocked, see §5).

---

## 2. Resources deployed (live)

### RG `rg-tokengov` (swedencentral)
| Resource | Detail |
|---|---|
| AI Services `tokengov-aoai` | endpoint `https://tokengov-aoai.cognitiveservices.azure.com/` |
| deploy `gpt-cheap` | **gpt-5-nano** (2025-08-07), GlobalStandard |
| deploy `gpt-premium` | **gpt-5** (2025-08-07), GlobalStandard |
| deploy `model-router` | model-router 2025-11-18, GlobalStandard |
| deploy `text-embed` | text-embedding-3-small v1, GlobalStandard |
| App Config `tokengov-aoai-appcfg` | `https://tokengov-aoai-appcfg.azconfig.io` |
| Log Analytics `tokengov-aoai-law` + App Insights `tokengov-aoai-ai` | telemetry |
| RBAC | Cognitive Services OpenAI User → user |
| **APIM** `apim-tokengov-<uniq>` | **Developer tier — PROVISIONING (async, started ~40min clock). Bare service only; backend/API/policies NOT yet applied.** |
| Storage `sttokengovx2ddgwhe` | orphaned from failed Function deploy; policy forced private (see §5) |

### RG `rg-tokengov-rag` (swedencentral)
| Resource | Detail |
|---|---|
| AI Search `tokengov-rag-6ayi7` | **westeurope** (swedencentral had no Basic capacity), Basic, semantic ranker = standard. Was provisioning at pause; verify state. |

---

## 3. Key DECISIONS made

| # | Decision | Why |
|---|---|---|
| D1 | **Models: gpt-5-nano (cheap) + gpt-5 (premium)** | gpt-4o-mini 2024-07-18 deprecated for NEW deploys; gpt-4.1 family has NO deployable quota (Batch only); only gpt-5 family has quota in swedencentral |
| D2 | **Code made reasoning-model compatible** | gpt-5 = reasoning models → `max_completion_tokens` (not `max_tokens`), no `temperature` override, optional `AZURE_REASONING_EFFORT=minimal`. Done in `providers.py` |
| D3 | **Auth = `AZURE_TOKEN_CREDENTIALS=dev`** (az login only) | a Conditional-Access-blocked service principal in the machine env was picked first by DefaultAzureCredential; `dev` skips it |
| D4 | **Embedding + chat SKUs = GlobalStandard** | Standard SKU not available for these models in swedencentral |
| D5 | **RAG: 5 Gutenberg books, Azure AI Search Basic + semantic ranker, keep (no delete)** | make the live benchmark's quality signal real |
| D6 | **AI Search in westeurope** | swedencentral had no Basic Search capacity |
| D7 | **All RAG code in `06_prototype/rag/`** | keep separate from core `costgov/` package; wired via generic `context_provider` hook |
| D8 | **Option (b) scope: APIM (gateway) + Function (decision binding) + budget + action group; AI Search codified** | promote in-process pieces to real Azure services |
| D9 | **APIM tier = Developer (~$50/mo)** | cheapest/no-SLA, right for dev/test |
| D10 | **Semantic cache: DEFERRED — no Redis** | APIM `azure-openai-semantic-cache` needs vector Redis (Enterprise/Managed Redis); Basic C0 can't do it; keep cache in-process (`cache.py`) to save cost |
| D11 | **Decision binding = Azure Function (event-driven), NOT App Service** | file 05 §7 chose Function for Monitor-alert-driven execution + hysteresis/arithmetic. App Service isn't event-triggered. (User corrected an App Service detour.) |

---

## 4. Code / file changes done (all under `06_prototype/`)
- `costgov/providers.py` — reasoning-model compat; gpt-5 price defaults; api-version `2025-04-01-preview`; generic `context_provider` hook on `RealModel` (for RAG grounding).
- `infra/main.bicep` — models → gpt-5-nano/gpt-5; `chatSku`+`embedSku` = GlobalStandard params.
- `.env` (gitignored) — endpoints, deployments, `AZURE_TOKEN_CREDENTIALS=dev`, reasoning knobs. **Smoke test PASSED** (real gpt-5 call + judge).
- `.env.example` — mirrors the above.
- `.venv` — created; requirements + `azure-search-documents` installed (search SDK install was in-flight at one point; verify).
- `rag/` — `books.json`, `golden_set.rag.json`, `workload.rag.json`, `ingest.py`, `retrieval.py` (hybrid+semantic), `bench_rag.py` (3-arm grounded benchmark), `.gitignore`.
- `infra/apim.bicep` — APIM service (deployed async, provisioning).
- `infra/platform.bicep` — **contains the OLD Function+Storage version that FAILED** (storage 403). Needs rework (see §5). Budget + action group in it are fine.
- `infra/functionapp/` — `function_app.py` (decision binding, HTTP `POST /decide`), `requirements.txt`, `host.json`. Logic mirrors `costgov/decision.py`.

---

## 5. BLOCKER — MCAPS storage policy (must solve to deploy the Function)
- **Azure Policy forces every storage account to `allowSharedKeyAccess=false` + `publicNetworkAccess=Disabled`** (verified: our Bicep didn't set these; policy mutated them, and an explicit `--public-network-access Enabled` was reverted).
- Consumption Functions failed: **file-share creation 403** (needs shared key + public reachable storage).
- **Implication:** an event-driven Function here requires **Flex Consumption + VNet integration + private endpoint(s) to storage + private DNS** (blob, likely queue/table too). This is the compliant pattern but is a meaningful networking build with more policy-surface risk.
- Was about to check for an **existing VNet / landing-zone** to integrate with (MCAPS subs often provide one) — **not yet checked.** ← good first step on resume.

---

## 6. Also noted / gotchas
- `$pid` is a read-only PowerShell automatic var — use `$objId`.
- Terminal `az ... create` client-side waits can be cancelled without cancelling the server-side op (APIM/Search kept provisioning).
- APIM semantic-cache, token-limit, emit-token-metric policy XML + the CLI backend/API-import approach are captured from the `azure-aigateway` skill (policies.md / patterns.md).

---

## 7. PENDING / NEXT (resume here)

### Option (b) — finish promoting to Azure services
1. **Check for an existing VNet** in the sub to integrate the Function with (`az network vnet list`). If present → reuse; else create VNet + subnets.
2. **Rework `infra/platform.bicep`**: split into
   - `platform.bicep` = budget + Monitor action group only (deploy — these work).
   - `function.bicep` = **Flex Consumption** Function + VNet integration + private endpoint storage + private DNS + identity-based `AzureWebJobsStorage` + RBAC (Storage Blob Data Owner, App Config Data Owner, Cognitive Services OpenAI User).
3. **Deploy** platform.bicep, then function.bicep; deploy function code (`func azure functionapp publish` or zip).
4. **APIM (after ~40min provision completes):** verify service up; then apply via CLI (per azure-aigateway skill):
   - enable system MI (already on) → grant **Cognitive Services OpenAI User** on `tokengov-aoai`.
   - create AOAI backend + import OpenAI API + apply policies: `azure-openai-token-limit`, `azure-openai-emit-token-metric`, `authentication-managed-identity`, `set-backend-service`. (No semantic-cache policy — deferred, D10.)
   - create a subscription/product for the gateway.
5. **Point the prototype at APIM**: set `AZURE_OPENAI_ENDPOINT` to the APIM gateway URL (+ subscription key header) and re-run to prove the governed gateway end-to-end.
6. **Codify AI Search** into `rag/infra` Bicep (currently CLI-provisioned, orphaned from IaC).
7. **Wire the closed loop**: Monitor alert (eval regression / token metric) → action group → Function `/decide` → writes knob to App Config → gateway reads it.

### Parked RAG tasks (do after or alongside)
8. Verify `azure-search-documents` installed in `.venv`.
9. Enable Entra RBAC on the Search service + assign roles to user (Search Service Contributor + Search Index Data Contributor/Reader).
10. Add `AZURE_SEARCH_ENDPOINT` / `AZURE_SEARCH_INDEX` to `.env`.
11. Run `rag/ingest.py` (index the 5 books), then `rag/bench_rag.py` (grounded benchmark).
12. **Foundry project + hosted agent with the AI Search tool** (full agentic-RAG showcase — the scope chosen earlier).

### Docs
13. Add the Azure solution architecture section to `06_prototype/README.md`.

---

## 8. Cost note
Running now: AI Services (pay-per-token), App Config, Log Analytics/App Insights, AI Search Basic (~$74/mo), **APIM Developer (~$50/mo, provisioning)**. No Redis (deferred). Everything reversible by deleting `rg-tokengov` + `rg-tokengov-rag`.

---

## 9. Govern tab direction and implementation (2026-07-20)

### Decisions

| # | Decision | Why |
|---|---|---|
| D12 | **Govern becomes the token-economics policy cockpit, not a generic settings page.** | Its job is to explain the effective policy, prove admissions, connect decisions to cost/quality outcomes, and support evidence-backed changes. |
| D13 | **The effective policy is read-only in the Govern UI.** | Studio's runtime identity remains App Configuration Data Reader; the browser must not hold policy-publisher privileges. |
| D14 | **Policy edits begin as durable change requests.** | A proposer supplies structured control changes, a new version, and a reason. Studio validates and stores a draft but does not write Azure. |
| D15 | **Publication remains an external review pipeline.** | Approved changes update `infra/policies/production.json`, run tests and Bicep what-if, publish with a deployment identity, and verify the resulting Azure ETag. |
| D16 | **Observed economics must close the loop.** | Govern should eventually show forecast-to-actual cost, quality-floor compliance, cost per successful task, routing/cache savings, avoided spend, and outcomes by policy version. |

### Gaps confirmed in the current tab

- Admission shows unit-cost evidence but not monthly/annual exposure, budget headroom, or cost per successful task.
- The active policy is visible only indirectly through admission checks; there is no complete human-readable effective-policy view.
- There is no historical backtest showing which Plans a proposed policy would admit or reject.
- Policy version history, business rationale, approvals, rollout state, and rollback evidence are not represented.
- Observe and Reconcile contain outcome evidence, but Govern does not summarize whether the policy achieved cost savings without violating quality.
- The eval-gated decision logic exists in-process, but Govern does not expose sample sufficiency, consecutive breaches, optimization eligibility, or reversion history.

### UI shape

Govern will be implemented as three initial work areas:

1. **Decision** — current admission, economic headroom, checks, immutable receipt, and Azure provenance.
2. **Effective policy** — read-only grouped controls for spend, supply/risk, optimization, quality, and automation.
3. **Change requests** — structured proposal form, current-versus-proposed diff, draft status, and explicit external-review publication state.

Later phases add an **Outcomes** area and historical impact simulation.

### Implementation status

- [x] Real Azure App Configuration policy authority and fail-closed loading.
- [x] Immutable admission and run binding to policy version/ETag.
- [x] Legacy `pending_admission` handoffs can be reevaluated against Azure.
- [x] Durable, validated draft policy-change store with an editable-control allowlist.
- [x] API contract for effective policy and draft change requests.
- [x] Govern sub-navigation and effective-policy UI.
- [x] Structured proposal form and change-request list.
- [x] Admission economics: monthly/annual exposure and threshold headroom.
- [ ] Historical policy simulation and cost/quality impact preview.
- [ ] Approval/PR/pipeline integration and policy-version outcome view.

---

## 10. Project constitution and reference workload (2026-07-21)

### Decisions

| # | Decision | Why |
|---|---|---|
| D17 | **Adopt [`09_TOKENECONOMICS_CONSTITUTION.md`](09_TOKENECONOMICS_CONSTITUTION.md) as the canonical project intent and development-alignment standard.** | The project needs a durable definition of success that survives UI, infrastructure, and implementation changes. |
| D18 | **Use the deployed sample agentic RAG solution as the first reference workload and evaluation environment.** | TokenEconomics must integrate with a real use case to prove forecast, policy, quality, enforcement, and reconciliation behavior in practice. |
| D19 | **Keep the RAG solution outside TokenGov core and integrate it through framework-neutral, versioned task/trajectory contracts.** | The proof must validate reusability rather than turn TokenGov into a workload-specific application. |
| D20 | **Define completion by the full lifecycle, not by completed screens or deployed resources.** | The validated contribution is the evaluation-to-enforcement and learning loop: `predict -> compare policy -> admit -> execute -> evaluate -> respond -> reconcile -> learn`. |
| D21 | **Make accepted-task economics and explicit tail risk first-class before claiming the mathematical controller is implemented.** | Completed-task cost is not cost per accepted task, quality is not acceptance probability, and modeled P95 is not a calibrated chance constraint. |
| D22 | **Record the reference-workload state observed before agent deployment: the live proof was a grounded Azure AI Search batch benchmark.** | Read-only inventory and user confirmation falsified the earlier assumption that a deployed sample agentic RAG solution already existed. On 2026-07-22 the five-book `books` index, live retrieval/generation/evaluation, and Application Insights request export were validated. This historical state is superseded by D23 for the deployed-agent boundary. |
| D23 | **Use a versioned Microsoft Foundry prompt agent with the Foundry IQ knowledge base exposed only through its managed-identity MCP connection as the first hosted RAG boundary.** | Agent version 2 completed a measured Responses API trajectory through MCP discovery, `knowledge_base_retrieve`, cited synthesis, and final completion without keys. This keeps Search/corpus logic outside TokenGov core and supplies the concrete boundary required for TE-002. |
| D24 | **Configure the agent-facing knowledge base for `extractiveData` with minimal retrieval reasoning and perform final synthesis in the agent model.** | The initial low-reasoning answer-synthesis configuration duplicated model work and consumed 42,208 retrieval reasoning tokens in one direct probe, causing deployment throttling. The bounded configuration preserves hybrid/semantic Foundry IQ retrieval and citations while removing duplicate query-planning/answer-synthesis model calls. It is measured prototype behavior on preview API `2026-05-01-preview`, not a production optimization claim. |
| D25 | **Separate the agent runtime model deployment from the earlier Foundry IQ probe deployment and pin both model name and version.** | A dedicated `rag-agent-runtime-gpt-4-1-mini` deployment isolates the agent boundary from retrieval experiments. Capacity 100 was required for the measured 12,096-token agent task; capacity is a throughput setting and must not be represented as task cost or guaranteed availability. |
| D26 | **Use a versioned Microsoft Foundry evaluation dataset as the first deployed-agent evaluation-suite source.** | The dataset will provide a managed evaluation asset, but it becomes TokenEconomics decision evidence only after TE-002 and TE-004 bind each case to stable task, trajectory, segment, corpus/index, agent, prompt, evaluator, and experiment revisions. The current 7/7 easy and 2/5 hard retrieval calibration remains separate measured benchmark evidence. |
| D27 | **Insert TE-001.5 as an urgent release-hardening gate before TE-002 and restrict the first repository release to the Studio Plan slice plus FutureTokenPredictor's verified dependency closure.** | Plan is confirmed to use FutureTokenPredictor through stdio MCP and already produces immutable receipts, but existing green tests are not sufficient release proof. The current Studio server imports unrelated governance/run modules, the workspace root is not a Git repository, and the nested predictor has pre-existing uncommitted changes. A tested Plan-only boundary, enforced file allowlist, Fluent 2-inspired UI, stress/extreme/regression/accessibility evidence, and clean revision provenance are therefore prerequisites to publication. This advances model-invocation `predict`; it does not redefine Plan output as complete trajectory economics or defer constitutional quality/evaluation work beyond the release gate. |
| D28 | **Preserve explicit agentic execution topology across the Studio-to-FutureTokenPredictor MCP contract instead of treating every retrieval workload as one RAG call.** | Red-first tests demonstrated that the adapter's RAG override erased ReAct, deterministic workflow, and multi-agent intent, while the MCP profile did not synchronize explicit patterns with workflow execution. Plan now carries the selected pattern and multi-agent count, and workflow simulation selects the matching versioned archetype. A measured four-scenario matrix and arithmetic invariants verify distinct modeled invocation economics and coherent bounds. This advances `predict` only: the outputs remain modeled archetype estimates, tool charges remain separate from model charges, and no result is a complete trajectory, accepted-task, calibrated tail-risk, quality, or savings claim. |
| D29 | **Make FutureTokenPredictor the single owner of versioned workload analysis; make Studio a thin review-and-confirm client.** | The seeded 34-case enterprise regression passed transport, model preservation, pricing, bounds, and persistence 34/34 but matched the reviewed topology rubric only 8/34. Studio's weaker prose classifier supplied defaults as explicit MCP fields and thereby suppressed FutureTokenPredictor classification. The repair will analyze the description first, merge only explicit overrides field by field, preserve retrieval/modalities/tools independently from topology, expose field-level provenance and uncertainty, request clarification for material ambiguity, and represent unknown external tool/infrastructure charges as unpriced exclusions rather than `$0`. The staged implementation and acceptance contract are recorded in [`14_ENTERPRISE_WORKLOAD_INFERENCE_REMEDIATION_PLAN.md`](14_ENTERPRISE_WORKLOAD_INFERENCE_REMEDIATION_PLAN.md). This expands `predict` while retaining modeled invocation scope and immutable evidence; it does not claim runtime quality, complete trajectory cost, calibrated tail risk, or savings. |
| D30 | **Support explicit provider/model assignments per logical agent while retaining one default/coordinator model.** | Verification found that FutureTokenPredictor's `compare_providers` tool compared alternative providers for the same workload, but `predict_token_usage` still used one provider/model for every agent. The new optional `agent_models` contract assigns a unique agent ID, role, provider, model, and positive turn weight to each logical agent. FutureTokenPredictor validates and prices every offering separately, allocates the existing modeled multi-agent token distribution by normalized turn weights, aggregates the assignment ledger, and persists it in calculation evidence and Plan receipts. Studio exposes an optional per-agent editor while the original selector is explicitly the default/coordinator model. This advances modeled `predict`; turn weights are assumptions rather than observed orchestration, and runtime quality, complete trajectories, model compatibility, external tools, and infrastructure remain separate evidence concerns. |
| D31 | **Make deterministic, versioned workload analysis a separate FutureTokenPredictor contract and label unknown enterprise action charges as externally billed and unpriced.** | The frozen 34-case corpus proved that a final profile without evidence or provenance was insufficient for Studio review and regression control. FutureTokenPredictor now owns an `analyze_workload` result and MCP surface containing rule/schema versions, description hash, topology evidence/confidence/alternatives, role-derived agent count, modalities, tools, and clarification prompts. Known predictor-priced tools remain separate from custom enterprise actions; custom function/MCP/function-calling charges are explicitly `externally_billed_unpriced`, making coverage incomplete instead of treating an unknown charge as zero. This advances control-plane `predict` and produces versioned modeled evidence. It does not establish complete trajectory cost, observed tool usage, segment quality, calibrated tail risk, or savings; Studio confirmation, immutable receipt schema integration, and runtime reconciliation remain required. |
| D32 | **Require Studio Plan to analyze, review, and confirm FutureTokenPredictor workload evidence before immutable completion.** | Studio's MCP adapter no longer infers topology, modalities, tools, or complexity from prose. Plan first calls the versioned `analyze_workload` MCP tool, renders editable topology/count/modality/tool fields with confidence and rule evidence, and submits only the confirmed profile as explicit prediction arguments. Low-confidence or clarification-bearing analysis stops in `needs_clarification` and creates no receipt unless a structured profile is confirmed. New schema-2.0 receipts hash the analysis, confirmed profile, assumptions, clarifications, and exclusions as first-class evidence; historical schema-1.0 receipts remain readable without reinterpretation. This advances modeled control-plane `predict`, preserves two-plane and authority separation, and does not prove execution behavior, quality, complete trajectory economics, calibrated tail risk, or savings. |

### Alignment requirement

Future material changes must pass the development alignment gate in the constitution. In particular, work must identify which lifecycle step it advances, what versioned evidence it produces, how it protects segment-level quality, and whether workload-specific behavior remains outside TokenGov core.

### Immediate core plan

1. Define the framework-neutral task/trajectory envelope from the measured Foundry Responses API and MCP boundary.
2. Define and persist accepted-task outcomes independently from evaluator scores.
3. Capture end-to-end task economics, including retrieval/tool and governance costs where measurable.
4. Compare policy candidates using segment-level quality evidence and explicit budget-breach probability.
5. Connect decision evidence to the separately authorized Azure publication/reversion path.
6. Reconcile accepted-task outcomes and distribution coverage into FutureTokenPredictor.
7. Validate portability with a second materially different workload after the RAG proof.
