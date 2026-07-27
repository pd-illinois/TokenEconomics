# Enterprise workload inference remediation plan

**Date:** 2026-07-24  
**Status:** In progress — EWI-01/EWI-02/EWI-07/EWI-08 complete at their current seams; EWI-03/EWI-04/EWI-05 partially implemented; broader gates remain  
**Lifecycle step advanced:** `predict`  
**Trigger evidence:** 34-case seeded multi-model regression in `13_ENTERPRISE_AGENT_REGRESSION_RESULTS.md`  
**Current measured baseline:** 34/34 execution/model-preservation passes; 8/34 semantic-topology passes

**Current deterministic development evidence (2026-07-27):** The reviewed 34-case corpus is now a versioned JSON fixture with exact descriptions, topology, minimum modalities/tools, agent-count source/minimum, and written rationale. Its first deterministic run produced 26 topology/count failures. The new versioned workload-analysis seam now records schema/rule versions, description hash, selected topology, confidence, alternatives, evidence spans, agent-count provenance, and clarification prompts; the corpus passes 34/34 for topology/count. Directional audio and broader document/image inference plus enterprise capability detection satisfy the fixture's current minimum modality/tool rubric. Enterprise API/action capabilities are emitted as `externally_billed_unpriced`, making cost coverage explicitly incomplete rather than implying zero external cost. Studio now consumes this analysis over MCP, exposes an editable review step, requires confirmation, and stops low-confidence or clarification-bearing profiles without a receipt. Quantity parsing/default clarification, complete field-level provenance, LLM merge validation, dedicated receipt schema upgrade, and the full 34-case visible-browser rerun remain open.

## 1. Decision summary

Expand FutureTokenPredictor so it owns a versioned, inspectable workload-analysis contract. Remove Studio's responsibility for inferring agent topology, modalities, and tools from prose. Studio will select provider/model and scale, call FutureTokenPredictor to analyze the workload, show the inferred profile and uncertainty to the user, and submit a confirmed structured profile for prediction.

This is more than adding regex terms. The regression failures show that topology, retrieval, modalities, tools, and quantities are separate dimensions. A reliable predictor must preserve them separately and expose assumptions rather than silently collapsing an enterprise workflow into `SingleCall_TextOnly`.

## 2. Constitutional alignment

1. **Lifecycle:** Advances `predict` by making workload-intake evidence more faithful and reproducible.
2. **Economic unit:** Initially remains modeled workload invocation economics. It does not become complete task/trajectory economics merely because richer topology is modeled.
3. **Evidence consumed:** Versioned model catalog/pricing, archetype definitions, workload-analysis rules, user-confirmed profile, and the 34-case regression corpus.
4. **Evidence produced:** Versioned workload-analysis result, field-level provenance/confidence, alternatives/clarifications, confirmed structured profile, calculation trace, and immutable Plan receipt.
5. **Quality:** No runtime output-quality claim is introduced. Classification regression quality is measured separately from workload acceptance quality.
6. **Tail risk:** Monte Carlo percentiles remain modeled bounds, not calibrated chance constraints.
7. **Two planes/security:** Analysis and prediction remain control-plane operations. No browser credentials, policy publication, or data-plane reasoning is added.
8. **Reusability:** Enterprise examples become framework-neutral test fixtures. No RAG-, healthcare-, finance-, or security-specific logic enters TokenGov core.

## 3. Root causes to remove

### RC1 — split ownership and destructive override

Studio's `McpPredictorClient._build_arguments()` currently infers topology, tools, and modalities, then sends them as explicit structured arguments. The MCP server constructs a new profile from those arguments instead of classifying the description first. This bypasses FutureTokenPredictor's richer classifier and makes Studio's smaller regex set authoritative.

### RC2 — mutually conflated dimensions

`rag_pipeline` currently competes with `react_agent`, `workflow`, and `multi_agent` as one exclusive pattern. Retrieval is often a capability inside an autonomous, workflow, or multi-agent topology. The analysis contract must preserve retrieval separately and use `rag_pipeline` only when retrieval plus one synthesis call is the actual topology.

### RC3 — coarse classification contract

The current classifier exposes a final profile but no rule version, evidence spans, confidence, alternatives, or clarification needs. Users cannot tell why a field was inferred or correct it before an immutable forecast is produced.

### RC4 — inadequate enterprise-action and modality representation

Current priced tools cover file search, code interpreter, and web search. ERP, CRM, ticketing, CI/CD, telemetry, refunds, remediation, and other actions are usually custom functions or MCP/API calls with unknown external charges. Treating those as no tools and `$0` is misleading. Audio input/output are also conflated, and document/image/audio quantities are frequently defaulted without confirmation.

### RC5 — no machine-readable semantic regression corpus

The 34 exact descriptions and expected topology rubric exist in a report, but not yet as a versioned test fixture enforced by the required predictor test runner and Studio tests.

## 4. Target contract

### 4.1 New reusable analysis result

FutureTokenPredictor will add a structured result conceptually equivalent to:

```text
WorkloadAnalysis
  schema_version
  rule_set_version
  description_hash
  topology
    selected: single_call | rag_pipeline | react_agent | workflow | multi_agent | code_exec
    confidence: high | medium | low
    alternatives[]
    evidence[]
  agent_count
    value
    source: explicit | inferred_roles | defaulted | user_confirmed
    evidence[]
  modalities[]
    modality
    direction
    source
    evidence[]
    quantity_status
  tools[]
    tool
    source
    evidence[]
    pricing_status: priced | approximate | externally_billed_unpriced
  complexity
  uses_retrieval
  assumptions[]
  exclusions[]
  clarifications[]
```

The exact implementation may use dataclasses and existing enums. No new dependency is required.

### 4.2 Precedence and scoring rules

Rules will produce evidence-backed scores rather than first-match keyword precedence:

1. **Multi-agent:** explicit count, “multi-agent”/“agent-to-agent,” or at least two distinct named agent-role groups that collaborate, coordinate, review, or hand off work.
2. **Autonomous/ReAct:** one autonomous decision maker or control loop that observes, decides, acts, validates, retries, adapts, or continues without human step selection.
3. **Workflow:** bounded ordered stages, approvals, routing, validation, or deterministic processing without autonomous iteration.
4. **RAG pipeline:** retrieval/file/knowledge search followed by one synthesis call when no stronger agentic topology is evidenced.
5. **Code execution:** code/test/computation behavior when code execution is the dominant pattern; otherwise code is a tool inside ReAct/workflow/multi-agent.
6. **Single call:** only when no multi-step, autonomous, collaborative, or tool-execution evidence exists.

High-impact ambiguity—such as autonomous wording combined with multiple role agents, or weak workflow evidence—must produce alternatives or a clarification instead of silent confidence.

### 4.3 Structured override semantics

FutureTokenPredictor will always:

1. analyze the description;
2. apply explicit caller overrides field by field;
3. record each field's source as inferred, explicit, defaulted, or user-confirmed;
4. validate cross-field consistency;
5. predict from the merged profile.

Selecting a model/provider or supplying scale must no longer erase inferred topology, modalities, tools, or quantities.

### 4.4 Per-agent model assignments

Multi-agent profiles may include explicit `agent_models` entries containing a stable logical agent ID, optional role, provider, model, and positive `turn_weight`. The top-level provider/model remains the default or coordinator assignment for callers that do not provide a complete per-agent map.

For the current invocation-level predictor, the existing multi-agent Monte Carlo token distribution is allocated across explicit assignments using normalized turn weights. Each allocation is priced through its own validated provider/model catalog entry and returned in an additive per-agent ledger. The receipt records the raw weights, normalized shares, allocated tokens, pricing verification, price/model sources, and per-agent modeled cost.

This does not assert that agents really consumed those shares. Measured per-agent turns and usage belong to later complete-trajectory telemetry and reconciliation. Model capability compatibility, quality, external tools, and infrastructure costs also remain separate evidence.

### 4.5 Tools and excluded external charges

- Map known priced capabilities to `file_search`, `web_search`, and `code_interpreter`.
- Map enterprise APIs/actions to `custom_function` or `mcp_server` for token/iteration modeling.
- Mark external application/service charges as `externally_billed_unpriced` unless a versioned price source is supplied.
- Keep model cost, predictor-priced tool cost, external unpriced tools, and infrastructure exclusions separate.
- Never convert “not priced” into `$0 total cost`.

### 4.6 Modalities and quantities

- Distinguish audio input from audio output.
- Detect invoice/PDF/contract/record/document inputs separately from retrieval.
- Detect screenshots/scans/images when visually processed.
- Preserve multimodal combinations.
- Parse explicit counts, pages, durations, images, searches, steps, and agents.
- When material quantities are absent, show defaults and either request confirmation or widen modeled assumptions; do not present defaults as observed facts.

## 5. Studio target flow

```text
Describe workload
  -> Analyze with FutureTokenPredictor
  -> Review inferred workload profile
  -> Confirm/edit topology, agent count, modalities, tools, quantities, scale
  -> Estimate
  -> Inspect calculation and exclusions
  -> Create immutable receipt
```

The existing one-click path may remain for high-confidence, low-impact cases, but inferred fields and assumptions must still be visible. Low-confidence or materially incomplete cases must stop at `needs_clarification` rather than generate a misleading completed receipt.

## 6. Incremental implementation blocks

Each block is one seam with red-first evidence. FutureTokenPredictor claims are valid only when run through `scripts/run_tests.py`.

### Block EWI-01 — freeze the semantic corpus

**Status:** Complete in local development evidence; release provenance remains open.

**Owner:** FutureTokenPredictor tests  
**Files:** existing classifier tests plus one machine-readable fixture under the existing test-data convention  
**Work:** Convert the 34 exact descriptions, expected topology, expected minimum modalities/tools, expected agent-count source, and rationale into a versioned fixture. Add deterministic tests with all classifier API keys cleared.

**Acceptance:**

- The current implementation is proven red against the fixture.
- Every expected label has a written rationale.
- Fixture changes require explicit review; outputs are not silently rewritten.

### Block EWI-02 — analyze first, then merge explicit overrides

**Status:** Complete for the existing MCP prediction boundary.

**Owner:** FutureTokenPredictor MCP boundary  
**Files:** `mcp_server.py`, focused MCP tests  
**Work:** Start from `classify(description)` whenever a description exists; overlay model, provider, scale, and other explicit fields without replacing unrelated inferred fields.

**Acceptance:**

- Supplying only model/provider/users/calls preserves inferred topology, modalities, tools, retrieval, and complexity.
- Explicit topology/modality/tool overrides still win and record provenance.
- Existing description-only and structured-only contracts remain compatible.

### Block EWI-03 — versioned deterministic workload analysis

**Status:** Partial — deterministic topology/count evidence and dedicated MCP analysis tool implemented; complete per-field provenance and LLM merge validation remain.

**Owner:** FutureTokenPredictor classifier and schemas  
**Files:** `models/schemas.py`, `classifier.py`, `llm_classifier.py`, classifier tests  
**Work:** Add the analysis result, evidence/provenance, scoring, alternatives, rule-set version, description hash, and distinct role-agent detection. Extend optional LLM classification to the same schema, but retain deterministic fallback and validation.

**Acceptance:**

- 34/34 expected topology cases pass deterministic fallback.
- Multi-agent named-role lists infer count from distinct roles and label it inferred, not explicit.
- Conflicting signals produce alternatives/clarification evidence.
- LLM output cannot bypass schema validation or erase deterministic evidence silently.

### Block EWI-04 — modality and quantity inference

**Status:** Partial — minimum corpus modalities and audio direction pass; explicit quantity parsing/default clarification remain.

**Owner:** FutureTokenPredictor classifier/profile  
**Files:** classifier, schemas, token calculators, tests  
**Work:** Separate audio directions; detect document, scan/image, and audio inputs; parse explicit quantities; attach default/clarification provenance.

**Minimum corpus acceptance:**

- Invoice processing includes document and image input when “any format” implies scanned/visual documents, marked inferred.
- Clinical notes include audio input but not audio output unless spoken output is requested.
- Contract/document pipelines include document input.
- Missing duration/page/image counts are exposed as assumptions or clarifications.

### Block EWI-05 — enterprise tools and unpriced exclusions

**Status:** Implemented for the reviewed corpus; broader near-neighbor and reporting regressions remain part of EWI-09.

**Owner:** FutureTokenPredictor tools/reporting  
**Files:** schemas, classifier, `tool_cost_estimator.py`, `report.py`, tests  
**Work:** Detect web search, code/test execution, and enterprise API/action calls. Add pricing status and unpriced external-tool evidence.

**Acceptance:**

- Competitor analysis includes web search and retrieval.
- Bug resolution includes code execution plus external CI/repository actions.
- ERP/CRM/refund/remediation actions appear as unpriced external tools, not absent tools.
- Model, priced-tool, approximate-tool, external-unpriced, and infrastructure figures remain separate.

### Block EWI-06 — topology-aware composite archetypes

**Owner:** FutureTokenPredictor prediction engine  
**Files:** archetype matching/profiles, `single_call_predictor.py`, `workflow_predictor.py`, calculation trace tests  
**Work:** Preserve topology as the primary iteration model while modalities and tools add token/cost components. Retrieval inside ReAct/workflow/multi-agent must not collapse to the one-call RAG archetype.

**Acceptance:**

- ReAct + retrieval uses ReAct iteration plus document/search components.
- Multi-agent + retrieval uses multi-agent turns/context sharing plus retrieval components.
- Workflow + documents uses workflow steps plus document input.
- Mean/components/bounds remain arithmetically coherent and deterministic under the fixed seed.

### Block EWI-07 — Studio becomes a thin analysis client

**Status:** Complete for the current Studio Plan seam; full EWI-09 browser matrix and release isolation remain open.

**Owner:** TokenEconomics Studio Plan  
**Files:** `costgov/mcp_prediction.py`, `studio.py`, `studio.html`, focused Studio tests  
**Work:** Remove prose topology/modality/tool classification from Studio. Add analysis call, review/confirmation UI, clarification handling, and confirmed-profile submission.

**Acceptance:**

- Studio-selected model/provider/scale do not overwrite FutureTokenPredictor analysis.
- User can inspect and correct topology, agent count, modalities, tools, and material quantities.
- Low-confidence/materially incomplete profiles do not create completed receipts.
- Pointer and keyboard activation are covered by browser tests.

### Block EWI-08 — immutable evidence schema upgrade

**Status:** Complete for new schema-2.0 receipts and historical schema-1.0 reads; full EWI-09 reopen/reanalysis matrix remains open.

**Owner:** Plan persistence  
**Files:** Plan contracts/store and tests  
**Work:** Version the receipt schema and persist analysis, analysis/rule version, confirmed profile, field provenance, assumptions, clarifications, and exclusions. Preserve old receipt readability.

**Acceptance:**

- Reopening a receipt reproduces exactly what was inferred and confirmed.
- Reanalysis creates a new Plan/receipt and never rewrites old evidence.
- Old schema `1.0` receipts remain readable and are not reinterpreted as new analysis.

### Block EWI-09 — full regression and release gate

**Owner:** both repositories  
**Work:** Rerun the 34-case corpus across deterministic classifier tests, MCP integration, Studio API, visible browser flow, model variation, arithmetic invariants, restart/reopen, and failure paths.

**Required exit criteria:**

- 34/34 topology contract passes after rubric review.
- Required modalities/tools and agent-count provenance pass per fixture.
- 34/34 selected provider/model preservation passes across the seeded model matrix.
- No `$0 total cost` implication for local models or unpriced external tools.
- No completed receipt when required clarification is unresolved.
- Full Studio regression green.
- Full FutureTokenPredictor suite green through `scripts/run_tests.py --all --expect pass`.
- Exact before/after confusion matrix and forecast deltas are documented; changed estimates are not called improvements without trajectory actuals.

## 7. Test strategy

### Red-first order

1. Add the fixture and prove current failures using `scripts/run_tests.py <target> --expect fail`.
2. Fix one block at a time.
3. Run the focused target with `--expect pass`.
4. Run existing classifier/MCP/predictor tests after each block.
5. Run the entire mandated suite only after focused blocks are green.
6. Run Studio tests and browser matrix after the reusable predictor contract is stable.

### Invariants retained

- token total equals component sum;
- model cost equals priced token-component sum;
- tool costs remain outside model cost;
- unpriced external tools are not represented as zero-priced tools;
- daily calls equal users × calls/user/day;
- monthly and annual projections use 30 and 365 days;
- P5 ≤ P50 ≤ P95 and modeled mean is coherent with bounds;
- same confirmed profile and fixed seed reproduce modeled output;
- model/provider selection is preserved exactly;
- receipt hashes change when confirmed workload evidence changes.

### Adversarial and ambiguity cases

Add near-neighbor tests so the fix does not overclassify:

- plural “agents” used generically but only one execution actor;
- a single agent that consults multiple systems versus multiple collaborating agents;
- a document workflow with no LLM call at every stage;
- “workflow” as a business noun inside an autonomous loop;
- “research agents” as a product name rather than role count;
- retrieval inside ReAct and multi-agent topologies;
- audio input without audio output;
- local model API cost versus excluded hosting cost;
- custom tool with unknown price;
- explicit user override contradicting inferred topology.

## 8. Files expected to change

### FutureTokenPredictor

- `src/future_token_predictor/models/schemas.py`
- `src/future_token_predictor/classifier.py`
- `src/future_token_predictor/llm_classifier.py`
- `src/future_token_predictor/mcp_server.py`
- `src/future_token_predictor/archetypes.py`
- `src/future_token_predictor/single_call_predictor.py`
- `src/future_token_predictor/workflow_predictor.py`
- `src/future_token_predictor/tool_cost_estimator.py`
- `src/future_token_predictor/predictor.py`
- `src/future_token_predictor/report.py`
- focused existing test files and one versioned regression fixture

### TokenEconomics Studio

- `06_prototype/costgov/mcp_prediction.py`
- `06_prototype/costgov/contracts.py`
- `06_prototype/costgov/planning.py`
- `06_prototype/studio.py`
- `06_prototype/studio.html`
- focused existing test files

This is the anticipated surface, not permission for a broad refactor. Each block should touch only its required seam.

## 9. Risks and controls

| Risk | Control |
|---|---|
| Overfitting to 34 descriptions | Add adversarial near-neighbors and evidence-based rules rather than title lookup. |
| False certainty from regex or LLM classifier | Field-level evidence, alternatives, confidence, and user confirmation. |
| LLM classifier nondeterminism | Deterministic fallback is release baseline; optional LLM output is schema-validated and provenance-labeled. |
| Breaking existing MCP callers | Preserve current arguments; add analysis fields/tools compatibly and test description-only/structured-only/merged paths. |
| Large estimate jumps | Publish before/after deltas with changed archetype assumptions; do not label larger numbers as more accurate without actuals. |
| Custom-tool cost fabrication | Record usage and `externally_billed_unpriced`; never invent prices. |
| Dirty nested repository | Do not reset or absorb unrelated changes; identify exact owned files and establish clean release provenance before publication. |

## 10. Recommended implementation sequence

Start with EWI-01 and EWI-02. They provide the highest leverage with the smallest seam: freeze the failures, then stop Studio's model/scale selection from erasing FutureTokenPredictor analysis. Continue through EWI-03 to EWI-06 inside FutureTokenPredictor before changing the Studio UI. Finish with immutable schema upgrade and end-to-end browser regression.

No implementation block should be declared complete from code inspection. FutureTokenPredictor evidence must come from its mandated runner, and TE-001.5 remains blocked until all broader release gates are satisfied.
