# Agentic RAG Integration Boundary

*TE-001 evidence note. Inventory performed 2026-07-21; grounded benchmark and deployed-agent evidence updated 2026-07-22.*

## 1. Outcome

TE-001 is **complete as an integration-boundary inventory**. A Microsoft Foundry prompt agent is deployed and one real task completed through the Foundry Responses API, MCP tool discovery, Foundry IQ knowledge-base retrieval, cited model synthesis, and final response. The response preserved correlated conversation, response, agent-version, model, tool-call, usage, and citation evidence.

The 2026-07-22 agent work advances `execute` and establishes the boundary required for later `evaluate -> reconcile` integration. It does not complete the constitutional lifecycle: policy admission, policy-bound trajectory persistence, accepted-task outcomes, segment evaluation, end-to-end cost attribution, response/reversion, and predictor learning remain unimplemented for the deployed agent.

### Business functionality established

TE-001 turns the five-book RAG use case from an assumed architecture into an observed business workload boundary. A user can ask the deployed agent a question, the agent is required to consult the approved knowledge base, Azure AI Search retrieves evidence from the indexed corpus, and the agent returns a grounded answer with source citations. The runtime uses Microsoft Entra identities and managed-identity connections rather than embedded Search or model keys.

For TokenEconomics, the business value of TE-001 is **observability readiness**, not cost optimization by itself. It establishes what a governed unit of work must contain and where evidence can be collected:

1. a business request enters a named, versioned agent;
2. the agent discovers and invokes the permitted knowledge tool;
3. the knowledge base retrieves source evidence;
4. the pinned model produces the final cited answer;
5. Foundry returns correlated conversation, response, tool, model, token-usage, citation, and completion evidence.

This lets the next contracts answer business questions such as: which workload and policy produced an answer, what steps and resources it consumed, whether the answer was acceptable for its segment, what the complete task cost, and whether actual behavior matched the forecast. TE-001 does **not** yet answer those questions in a decision-grade way because its Foundry identifiers are not persisted as a TokenEconomics trajectory and are not joined to policy, evaluation, acceptance, cost, or reconciliation evidence.

### Studio visibility

TE-001 does not currently appear as a dedicated record in TokenEconomics Studio. The current Studio API serves reports, Plan receipts, Azure policy and admission handoffs, and offline run results. It has no API or durable store for Foundry conversations, responses, MCP calls, citations, agent versions, or complete trajectories. Consequently, the measured TE-001 agent invocation is documented in this evidence note but is not displayed in the UI.

That separation is intentional: showing the one observed invocation in Runs or Observe now would make operational evidence look like a policy-bound, reusable TokenEconomics record. The constitution's UI rule requires the backing contracts before new cards or controls are added.

| Studio view | What is visible today | How TE-001 evidence should appear after the required backing task |
|---|---|---|
| **Plan** | FutureTokenPredictor model/workload invocation forecast and immutable receipt. | After TE-002 and TE-004, identify the versioned agentic workload, corpus/index, agent, retrieval configuration, and experiment revision being forecast. |
| **Govern** | Authoritative Azure policy, receipt admission, provenance, and staged change requests. | After TE-008 through TE-010, compare policy candidates for the same agent trajectory and show eligibility evidence; TE-001 alone supplies no admission decision. |
| **Runs** | Policy-bound offline reference runs. | First direct destination after TE-002 and TE-003: show each deployed RAG trajectory, agent version, status, ordered retrieval/tool/model steps, citations, and exact policy/prediction bindings. |
| **Observe** | Completed-request model cost and post-hoc segment quality from the existing prototype. | After TE-005 through TE-007, aggregate deployed trajectories by segment and policy with completion, acceptance, end-to-end cost, and cost per accepted task. Unknown Search, evaluation, and infrastructure costs must remain explicit. |
| **Reconcile** | Completed-task token calibration for offline runs. | After TE-009 and TE-014, join the deployed trajectory to its forecast, acceptance outcome, actual cost, budget breaches, and distribution coverage before writing calibration evidence. |

The nearest Studio change is therefore not a TE-001 status card. It is the TE-002 trajectory contract followed by the TE-003 RAG adapter and a Runs API/view that renders persisted, policy-bound deployed trajectories. TE-001 remains the evidence source that defines that integration boundary.

| Evidence status | Meaning in this note |
|---|---|
| **Verified** | Observed from current repository code or read-only Azure inspection. |
| **Repository-only** | Implemented locally but not proven in a deployed agent trajectory. |
| **Planned** | Described by project decisions but not observed as deployed behavior. |
| **Blocked** | Requires an external identity, permission, or trace that is not currently available. |

## 2. Deployment-neutral boundary

| Boundary concern | Current evidence | Status | Required adapter evidence |
|---|---|---|---|
| Agent entry point | Foundry Responses API agent reference `tokengov-books-rag-agent`, version 2, is a measured hosted entry point. `rag/bench_rag.py` remains the local batch benchmark entry point. | Measured live | Workload revision, request timestamp, and stable TokenEconomics task ID at ingress. |
| Task identity | The completed invocation returned a conversation ID and response ID bound to agent version 2. Benchmark `request_id`, `trace_id`, `run_id`, `prediction_id`, `segment`, and `policy_version` are not yet joined to those Foundry IDs. | Measured live / incomplete contract | One stable task and trajectory ID propagated through ingress, retrieval, every step, evaluation, and reconciliation. |
| Retrieval/tool calls | The agent listed the `books-knowledge-base` MCP server and completed `knowledge_base_retrieve` with query arguments. The Foundry IQ knowledge base returned Search-backed evidence from the 2,703-chunk `books` index; the final answer cited four `mcp://searchindex/...` document keys. | Measured live | Persist retrieval timing, index/knowledge-base revision, result count, token contribution, status, parent trace, and measurable charge or explicit exclusion. |
| Model calls | Agent version 2 used pinned deployment `rag-agent-runtime-gpt-4-1-mini` (`gpt-4.1-mini`, `2025-04-14`) for final synthesis. The completed response reported 11,759 input, 337 output, and 12,096 total tokens. Benchmark judge and embedding calls remain separate. | Measured live | Separate generation, embedding, routing, and evaluation step records with deployment revision, token categories, latency, status, and cost provenance. |
| Tools beyond retrieval | Agent version 2 allows only `knowledge_base_retrieve`; no other deployed-agent tool call occurred in the completed trajectory. | Measured live | Generic tool step records for future tools without RAG-specific fields in TokenGov core. |
| Retries and iterations | No first-class retry, iteration, or agent-loop records exist in the benchmark telemetry. Provider/SDK retries are not attributed to a task trajectory. | Verified gap | Attempt and iteration numbers, parent step, retry reason, backoff duration, and token/cost effects. |
| Trace topology | One Responses API result reconstructs conversation -> response -> MCP list -> completed MCP call -> completed message, with agent/model/version, usage, and four citations. It is not yet joined to TokenGov policy, evaluation, or reconciliation records. | Measured live / incomplete contract | A durable trace graph joining task, trajectory, step, request, run, prediction, segment, policy binding, evaluation, and reconciliation. |
| Evaluation | `Evaluator` and `RealJudge` evaluate benchmark answers after execution and report mean and per-difficulty quality. The bounded benchmark defaults to 100% evaluation and now rejects empty samples as inconclusive. Quality is not an explicit accepted-task outcome. | Measured live / incomplete contract | Evaluator/golden-set revisions, segment, raw scores, sample sufficiency, and a separate accepted/rejected/inconclusive outcome. |
| Authentication | The Foundry project uses a system identity and a keyless `RemoteTool` project connection with `ProjectManagedIdentity` and Search audience. The project identity has Search read access; the Search identity has Cognitive Services User on Foundry. The agent also exposes managed instance/blueprint identities. | Verified live | Reduce any temporary deployment-time role assignments after provisioning and prove runtime policy access cannot publish policy. |
| Policy binding | The generic TokenGov path can carry immutable policy and prediction bindings. The local RAG benchmark does not prove that a deployed agent enforces an admitted binding over a complete trajectory. | Repository-only | Exact policy version, hash, label, ETag, admission receipt, and prediction binding at trajectory start and completion. |

## 3. Current repository flow

The only traceable RAG flow currently available is an incremental model-invocation benchmark:

```text
batch request
  -> Retriever.context(question)
  -> Azure AI Search hybrid/semantic retrieval
  -> one grounded generation call
  -> request-level telemetry record
  -> post-hoc sampled judge call
  -> aggregate and segment quality report
```

This is useful measured workload evidence when run live, but it is distinct from the hosted-agent trajectory. Retrieval is folded into the generation context hook, judge calls are not child steps of the original task trace, and retries or agent iterations cannot be reconstructed.

On 2026-07-22, the bounded one-request comparison was run with full evaluation coverage. Premium and governed arms scored `1.0` on the one easy case; Model Router scored `0.0`. The governed request used the cheap route, 3,599 input tokens, 25 output tokens, 1,380.3 ms model-path latency, and calculated model cost of $0.000020. These are measured single-task values, not representative savings or quality claims.

Retrieval calibration over the 12-case golden set found required-fact recall of 7/7 easy and 2/5 hard cases at `top_k=12`. The hard segment is not decision-grade and requires improved multi-source retrieval before policy comparison.

The measured deployed-agent flow is:

```text
Foundry conversation + Responses API request
  -> agent reference tokengov-books-rag-agent:2
  -> MCP list tools on books-knowledge-base
  -> knowledge_base_retrieve(query)
  -> Foundry IQ minimal hybrid/semantic Search retrieval
  -> gpt-4.1-mini final synthesis
  -> cited completed response
```

The completed response ID was `resp_1830afa761d85c53006a61626f08a48190a52c456d62bac94a`. This identifier is operational evidence, not a durable TokenEconomics trajectory record; TE-002 and TE-003 must define and persist that contract.

## 4. Available economics and missing sources

### Available signals

- Generation input, output, cached, reasoning, and document tokens where returned by the provider.
- Per-request model cost calculated by the prototype pricing configuration.
- Model-call latency, selected model, cache hit, degraded state, tenant, segment, and policy/prediction/run correlation fields.
- Aggregate benchmark cost and post-hoc segment quality for premium, governed, and Model Router arms.
- Local request and trace correlation plus exported Application Insights `costgov.request` records.
- Foundry conversation/response IDs, agent name/version, model deployment, MCP list/call items, tool arguments, final message status, citations, and Responses API token usage for the measured agent task.

### Missing or incomplete signals

- Retrieval request count, latency, query units, and attributable Azure AI Search cost per task.
- Durable hosted-agent spans exported to the TokenEconomics evidence store; the Responses API returned an inspectable item sequence but no TokenGov trajectory record.
- Embedding and evaluation calls joined to the originating task and included in end-to-end cost.
- APIM token metrics or gateway request evidence for the RAG workload.
- Allocated infrastructure, observability, and governance overhead per task.
- Explicit acceptance outcomes and cost per accepted task.
- Empirical budget-breach probability and forecast distribution coverage.
- One deployed trace proving the same policy binding from admission through final outcome.

Unknown or excluded costs must remain unknown or excluded in later contracts; they must not be represented as zero.

## 5. Known Azure deployment appendix

Concrete resource identifiers are recorded here and must not enter reusable TokenGov core schemas.

### Verified

- Subscription: `MCAPS-iCSU-pd-America-sub` (`0c2e0734-5fab-424f-88a0-1693380415fd`).
- TokenGov resource group: `rg-tokengov`.
- RAG resource group: `rg-tokengov-rag`.
- Azure AI Services account: `tokengov-aoai`.
- Succeeded deployments: `gpt-cheap` (`gpt-5-nano`, `2025-08-07`), `gpt-premium` (`gpt-5`, `2025-08-07`), `model-router` (`2025-11-18`), and `text-embed` (`text-embedding-3-small`, version `1`).
- Azure AI Search service: `tokengov-rag-6ayi7`, Basic tier, West Europe.
- Application Insights: `tokengov-aoai-ai`, workspace-backed by `tokengov-aoai-law`.
- App Configuration policy authority: `tokengov-aoai-appcfg`.
- APIM: `apim-tokengov-x2ddgwhexwaby`; only the default `echo-api` is present and no APIM backends are configured.
- The two known resource groups contain no App Service, Function App, or Container App for this workload. The Foundry project and hosted prompt agent listed below are deployed in `rg-tokengov-rag`.
- Search authentication allows Entra ID or API keys; the workload uses Entra ID. The developer roles are scoped to the Search service.
- Search index `books` has fields `id`, `book`, `content`, and `content_vector`, vector profile `vprofile`, semantic configuration `sem`, and 2,703 documents from all five configured books.
- Foundry account `tokengov-rag-foundry` and project `tokengov-rag-project` are deployed in `rg-tokengov-rag`.
- Foundry IQ knowledge source `books-knowledge-source` and knowledge base `books-knowledge-base` target the `books` index. The knowledge base uses preview API `2026-05-01-preview`, `extractiveData`, and minimal reasoning for the agent boundary.
- Project connection `books-knowledge-base-mcp` is a keyless `RemoteTool` connection to the knowledge-base MCP endpoint using `ProjectManagedIdentity`.
- Agent `tokengov-books-rag-agent`, version 2, is active with only `knowledge_base_retrieve` allowed and approval disabled for that vetted tool.
- Agent runtime deployment `rag-agent-runtime-gpt-4-1-mini` pins `gpt-4.1-mini` version `2025-04-14`, GlobalStandard capacity 100. Capacity is throughput configuration, not measured consumption or cost.
- A live hybrid/semantic query returned passages from the expected book. Golden-set calibration established 7/7 easy and 2/5 hard required-fact retrieval at `top_k=12`.
- After correcting the logger level, `AppTraces` contained four recent `costgov.request` records with four distinct request/trace IDs and `probe`/`easy`, `premium`/`cheap` dimensions.

### Unverified or absent

- Any APIM request path from the RAG workload, because the service has no configured AI backend/API.
- End-to-end retrieval, generation, post-hoc evaluation, policy, and reconciliation spans under one TokenEconomics trajectory ID.

### Remaining dependency for TE-003 and later

The deployed agent boundary is now known. The next dependency is a framework-neutral task/trajectory contract and adapter that carry stable TokenEconomics identity and admitted policy evidence through the measured Foundry response items, then join evaluation and reconciliation without coupling core schemas to Foundry, MCP, Search, or the corpus.

## 6. TE-001 acceptance assessment

| Acceptance criterion | Result |
|---|---|
| One real task traced through retrieval and every model/tool step to final outcome | **Met for boundary inventory.** A real Foundry response records MCP discovery, completed `knowledge_base_retrieve`, one final model response, usage, citations, and completion. Policy, post-hoc evaluation, and reconciliation are correctly listed as later contract gaps. |
| Missing telemetry and cost sources listed explicitly | **Met.** See section 4. |
| No secrets or workload-specific resource names enter TokenGov core schemas | **Met for this inventory.** Resource identifiers are confined to this appendix; no core schema changed. |

## 7. Constitutional alignment

- **Lifecycle:** establishes the deployed `execute` boundary and evidence needed across the full lifecycle. Benchmark evaluation is measured separately; policy admission, bounded response, trajectory reconciliation, and learning remain unproven for this workload.
- **Scope:** distinguishes the local model-invocation benchmark, the measured hosted-agent task, and the still-missing policy-bound TokenEconomics trajectory.
- **Versioned evidence consumed:** current repository code, constitution, decisions, Azure resource metadata, deployment revisions, and a bounded telemetry query interval.
- **Versioned evidence produced:** this dated integration-boundary note, the linked TE-001 backlog status, agent version 2, pinned model deployment, knowledge-base and connection definitions, one correlated Foundry response, the `books` index contract, local request telemetry, and Azure request telemetry.
- **Segment quality:** preserves the easy/hard segment distinction and records that current post-hoc scores are not accepted-task outcomes.
- **Economics and tail risk:** records measured agent token usage but does not infer cost from it; end-to-end, accepted-task, and calibrated tail-risk evidence remain missing.
- **Architecture and authority:** project-to-Search access is keyless and least-privilege at runtime; production policy authority was not changed; RAG-specific behavior remains outside `costgov/`. A deployment-time `Foundry Project Manager` assignment was required to create the connection and should be reviewed after provisioning.
- **Evidence label:** agent deployment, MCP retrieval, cited response, index, benchmark retrieval/model calls, bounded evaluation, and request telemetry are measured live. Foundry IQ full features use preview API `2026-05-01-preview`; production readiness, accepted-task economics, and the policy loop remain proposed or incomplete.

TE-002 may now begin from this measured boundary. It must define stable framework-neutral identity and evidence contracts before TE-003 maps Foundry response items into a policy-bound trajectory.