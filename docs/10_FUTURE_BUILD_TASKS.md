# TokenEconomics Future Build Tasks

*Executable backlog derived from the [TokenEconomics constitution](09_TOKENECONOMICS_CONSTITUTION.md). Created 2026-07-21.*

This backlog translates the constitutional implementation plan into dependency-ordered work. It is the execution queue, not a replacement for `decision.md`. Update task status and evidence links as work completes; do not mark a task complete from UI presence alone.

## Task status summary

This table indexes every task with an explicit status in this backlog. The detailed task sections and their evidence remain authoritative.

| Task | Work item | Parent | Status |
|---|---|---|---|
| TE-001 | Inventory the deployed agentic RAG boundary | - | **Complete** |
| TE-001.5 | Release-harden Studio Plan and FutureTokenPredictor | - | **Complete** |
| MODELREL-01 | Curate and version Foundry model/pricing releases | TE-001.5 | **Complete** |
| TE-001.6 | Add Copilot commercial-meter forecasting to Plan | - | **Complete** |
| CPLAN-01 | Freeze commercial contracts and rate fixtures | TE-001.6 | **Complete** |
| CPLAN-02 | Implement fail-closed rate-card loading | TE-001.6 | **Complete** |
| CPLAN-03 | Implement entitlement decisions | TE-001.6 | **Complete** |
| CPLAN-04 | Add deterministic Copilot Studio forecasting | TE-001.6 | **Complete** |
| CPLAN-05 | Compose Foundry/BYOM and Work IQ hybrids | TE-001.6 | **Complete** |
| CPLAN-06 | Add fixed-seat and purchase-portfolio economics | TE-001.6 | **Complete** |
| CPLAN-07 | Add Cowork and variable Work IQ scenario forecasts | TE-001.6 | **Complete** |
| CPLAN-08 | Make Studio Plan route-first | TE-001.6 | **Complete** |
| CPLAN-09 | Introduce immutable receipt schema 3.0 | TE-001.6 | **Complete** |
| CPLAN-10 | Complete TE-001.6 release evidence | TE-001.6 | **Complete** |
| TE-001.7 | Add experience-led meter-stack and GitHub Copilot forecasting | - | **Complete** |
| MSTACK-01 | Define and validate product meter stacks | TE-001.7 | **Complete** |
| MSTACK-02 | Add GitHub Copilot commercial forecasting | TE-001.7 | **Complete** |
| MSTACK-03 | Make Studio intake experience-led | TE-001.7 | **Complete** |
| MSTACK-04 | Persist immutable meter-stack receipts | TE-001.7 | **Complete** |
| MSTACK-05 | Complete TE-001.7 release evidence | TE-001.7 | **Complete** |
| TE-002 | Define the task/trajectory envelope | - | **Complete** |
| TE-003 | Build the RAG workload adapter | - | **Complete** |
| TE-004 | Create the experiment manifest | - | **Ready** |
| A365-01 | Define external agent-governance evidence | Agent 365 track | **Ready** |
| TE-005 | Define accepted-task outcomes | - | **Not started** |
| TE-006 | Implement the multi-meter trajectory ledger | - | **Not started** |
| TE-007 | Add accepted-task economics to Observe | - | **Not started** |
| TE-008 | Define immutable policy candidates | - | **Not started** |
| TE-009 | Add decision-grade quality and tail constraints | - | **Not started** |
| TE-010 | Integrate candidate selection into Govern | - | **Not started** |
| TE-011 | Persist evaluation decision state | - | **Not started** |
| TE-012 | Implement the reviewed publisher handoff | - | **Not started** |
| TE-013 | Prove bounded regression response on RAG | - | **Not started** |
| TE-014 | Reconcile acceptance, multi-meter actuals, and tail coverage | - | **Not started** |
| TE-015 | Demonstrate forecast learning | - | **Not started** |
| TE-016 | Run the portability test | - | **Not started** |

## Status vocabulary

- **Complete:** implementation and acceptance checks pass with evidence.
- **Ready:** dependencies are satisfied and work can start.
- **Blocked:** an external dependency or decision is missing.
- **Not started:** predecessor work is incomplete.

## Current foundation

The following foundations already exist and should be extended rather than rebuilt:

- Delivery-experience-first Plan forecasting with immutable schema-5 receipts across subscriptions, included usage, Microsoft Copilot Credits, GitHub AI Credits, Foundry model tokens, resource meters, and hybrid meter stacks. FutureTokenPredictor remains the model/workload and token-price authority; commercial meters remain parallel evidence.
- A versioned Studio Foundry release catalog limits coordinator and per-agent selectors to explicitly released OpenAI-on-Azure and Anthropic-on-Foundry offerings. Each entry pins deployment context, exact public rate dimensions, effective/retrieval dates, and first-party pricing/model citations; broad FutureTokenPredictor provider capability remains an internal catalog.
- Fail-closed Azure App Configuration policy authority with exact version, hash, label, and ETag provenance.
- Deterministic Govern admission and separately authorized draft policy changes.
- Policy-bound offline runs, segment quality, telemetry, and completed-task reconciliation.
- Experimental policy selection and evaluation reaction logic that are not connected to the Studio/Azure authority path.
- A live grounded RAG benchmark over a five-book Azure AI Search index plus a deployed Microsoft Foundry prompt agent connected to a Foundry IQ knowledge base through managed-identity MCP. TE-003 now preserves one measured policy-bound trajectory with exact prediction, policy, conversation, response, retrieval, model-usage, citation, and integrity evidence.
- Agent 365 research now defines an optional external identity, governance, security, and observability integration track. No Agent 365 runtime, API, telemetry, billing, or enforcement integration has yet been implemented or production-validated.

**Post-TE-001 roadmap scope rule:** Later tasks must operate on the resolved versioned meter stack rather than assume every workload is a Foundry token workload. Native quantities, commercial currencies, entitlements, purchase sources, model tokens, resource usage, and USD allocations remain separate evidence. No task may convert Microsoft Copilot Credits, GitHub AI Credits, tokens, seats, or resource units into one another without a versioned sourced rule. The deployed RAG workload remains the first end-to-end control-loop proof; it does not by itself validate all nine delivery experiences.

**Agent 365 integration rule:** Treat Microsoft Agent 365, Entra Agent ID, Defender, and Purview as external authorities for their documented identity, inventory, access, security, compliance, lifecycle, and operational-activity facts. TokenEconomics remains authoritative for forecasts, economic comparison/admission, explicit accepted-task outcomes, native-meter reconciliation, and learning. Agent 365 evidence must carry source, maturity, coverage, freshness, retention, and immutable correlation; registry presence or an HTTP-successful telemetry export is not proof of runtime enforcement, complete ingestion, accepted-task quality, or billable usage.

## Milestone 1: Reference workload contract

### TE-001 - Inventory the deployed agentic RAG boundary

**Status:** Complete
**Lifecycle:** all steps; establishes the integration boundary
**Deliverable:** A deployment-neutral integration note covering the agent entry point, task identity, retrieval/tool calls, model calls, retries/iterations, traces, evaluation path, authentication, and available cost signals. Record resource identifiers separately from reusable contracts.

**Evidence:** [Agentic RAG integration boundary](11_AGENTIC_RAG_INTEGRATION_BOUNDARY.md). Search configuration, corpus ingestion, grounded generation, post-hoc evaluation, local request correlation, and Application Insights export are verified. Foundry agent version 2 completed one inspectable Responses API trajectory through MCP tool discovery, `knowledge_base_retrieve`, cited final synthesis, and response completion. Missing policy, evaluation, cost, retry, and acceptance joins remain explicit inputs to later tasks.

**Consolidated milestone evidence:** [`16_TE_001_VALIDATION_RESULTS.md`](16_TE_001_VALIDATION_RESULTS.md) records the measured test, browser, package, integration-boundary, modeled-calculation, defect, and remaining-gap evidence for TE-001, TE-001.5, TE-001.6, and TE-001.7.

**Business outcome:** TokenEconomics now has a verified reference-workload entry point and observed step boundary rather than an assumed agent architecture. This proves where business requests, grounded retrieval, model synthesis, citations, identity, usage, and completion evidence originate. It does not yet provide a Studio-visible governed trajectory or demonstrate savings, acceptance, policy enforcement, or reconciliation for the deployed agent.

**Acceptance:**

- One real task can be traced from request through retrieval and every model/tool step to its final outcome.
- Missing telemetry and cost sources are listed explicitly.
- No secrets or workload-specific resource names enter TokenGov core schemas.

### TE-001.5 - Release-harden Studio Plan and FutureTokenPredictor

**Status:** Complete
**Priority:** Must complete before TE-002 or any later backlog task
**Depends on:** TE-001
**Lifecycle:** predict
**Scope:** Modeled workload-invocation forecasting and Plan reliability; this task does not claim complete task/trajectory economics, accepted-task quality, calibrated tail risk, or production-validated savings.

**Verified starting point (2026-07-24):** The assumption is correct. Studio Plan loads its provider/model offerings from FutureTokenPredictor's `build_model_catalog()` and submits forecasts to FutureTokenPredictor's `predict_token_usage` tool over a local stdio MCP process. Studio then creates an immutable, content-hashed Plan receipt. The pre-hardening baseline is 30 passing TokenEconomics prototype tests and 369 passing FutureTokenPredictor tests through its required evidence runner. These counts are measured baseline evidence, not release approval.

**Measured progress (2026-07-24):** Studio now uses Microsoft Fluent communication blue and neutral semantic tokens in light and dark modes; live 390 × 844 browser checks observed the expected `#0f6cbd` light and `#479ef5` dark accents, no horizontal overflow, and no recorded browser errors. A red-first regression proved that Studio previously flattened ReAct, workflow, and multi-agent RAG descriptions into `rag_pipeline`, and that structured MCP patterns did not select workflow-aware execution. The repaired boundary preserves `rag_pipeline`, `react_agent`, `workflow`, and `multi_agent`, including explicit multi-agent count. A real four-scenario, 1,000-user, 10-calls-per-user/day matrix traversed Studio's stdio MCP boundary and produced distinct archetypes and calculation traces. Automated assertions independently verify token-component sums, pricing-component sums, daily-call scale, 30/365-day projections, tool/model cost separation, bound ordering, and mean-within-bound coherence. Post-change regression evidence is 34 passing Studio tests and 373 passing FutureTokenPredictor tests through `scripts/run_tests.py`. These are measured local development results; release remains blocked by the other proof gates and clean-revision requirements below.

The same four statements were subsequently entered through the visible Studio Plan textarea and persisted under report `RPT-20260724-CB94001B`; exact inputs, outputs, Plan/receipt identities, a detected stale-runtime failure, corrected rerun evidence, and reopen validation are recorded in [`12_STUDIO_PLAN_UI_TEST_RESULTS.md`](12_STUDIO_PLAN_UI_TEST_RESULTS.md). Coordinate-based browser pointer activation remains unproven because automation reported pointer interception; DOM button activation exercised the normal Studio click handler. Pointer and keyboard coverage therefore remain open release-gate work rather than being mislabeled as passed.

A broader 34-case enterprise-agent regression then used reproducibly randomized provider/model selections across nine catalog offerings and persisted 34 immutable receipts in report `RPT-20260724-0CB42539`. Transport, Plan completion, selected-model preservation, pricing verification, and bound ordering passed 34/34, but semantic topology classification passed only 8/34: 0/13 expected multi-agent cases and 0/4 expected workflows were recognized correctly. The adapter also lost implied non-RAG tools and document/audio/image modalities. Exact inputs, model assignments, calculations, receipt IDs, and failure analysis are recorded in [`13_ENTERPRISE_AGENT_REGRESSION_RESULTS.md`](13_ENTERPRISE_AGENT_REGRESSION_RESULTS.md). These are release-blocking forecast-correctness failures, not accepted limitations.

**Approved remediation direction (2026-07-24):** FutureTokenPredictor becomes the single owner of a versioned workload-analysis contract; Studio becomes a thin analysis review/confirmation client and no longer classifies topology, modalities, or tools independently. The staged red-first work is EWI-01 through EWI-09 in [`14_ENTERPRISE_WORKLOAD_INFERENCE_REMEDIATION_PLAN.md`](14_ENTERPRISE_WORKLOAD_INFERENCE_REMEDIATION_PLAN.md): freeze the 34-case semantic corpus, analyze descriptions before merging explicit overrides, add evidence/confidence/clarification, expand modality and enterprise-tool representation, compose capabilities with topology-aware archetypes, upgrade immutable evidence, and rerun the complete release matrix. Unknown external tool and local-hosting charges must remain explicit unpriced exclusions rather than fabricated zero cost.

**Remediation progress (2026-07-24):** EWI-02 has started red-first. A focused MCP regression proved that supplying model/provider/scale replaced an autonomous description with `SingleCall_TextOnly`; the nested FutureTokenPredictor boundary now classifies the description first and applies only explicit field overrides. The focused test and the complete MCP module pass against the nested source (1/1 and 27/27 respectively) through `scripts/run_tests.py`. This work also exposed a release-evidence provenance defect: without an explicit nested `src` path, the current Python environment imports a separate checkout at `09_ideas/FutureTokenPredictor`.

**Source-provenance and suite-isolation evidence (2026-07-27):** The predictor's mandated `scripts/run_tests.py` now prepends its own resolved nested `src` directory to the child `PYTHONPATH`; a red-first unit test failed on the previous missing environment and now passes. With the caller's `PYTHONPATH` explicitly removed, the complete mandated predictor suite passes 421/421 after the enterprise-inference additions. Studio now has scoped pytest discovery that excludes the nested dependency suite from its own run; after EWI-07/EWI-08 the complete Studio-owned suite passes 46/46. Python 3.14 `pytest_asyncio` deprecation warnings remain non-failing environment debt. These are measured local results, not release approval.

**Per-agent model progress (2026-07-27):** Verification corrected an assumption: the existing MCP server could compare providers/models for the same token workload, but one `predict_token_usage` forecast could not assign separate models to separate agents. A red-first MCP test now covers an OpenAI research agent and Anthropic review agent in one multi-agent forecast. FutureTokenPredictor accepts versioned `agent_models` entries (`agent_id`, role, provider, model, finite positive `turn_weight`), validates/prices each offering, allocates the existing modeled multi-agent token distribution by normalized turn weights, aggregates costs, and emits a per-agent pricing ledger. Studio's original selector is now labeled default/coordinator model and an optional editor adds/removes per-agent assignments. A real Studio → stdio MCP round trip produced a `MultiAgent` forecast with 66.7%/33.3% allocations and two separately verified price entries; visible browser submission persisted under report `RPT-20260727-89B286D7`, receipt `plan_c982b2c17c6a65e544f3`, and rendered the immutable assignment evidence. Additional red-first boundary tests now reject a one-entry multi-agent assignment and zero, negative, infinite, or NaN weights in both the Studio adapter and MCP authority. Receipt regression coverage proves the intake assignments and output pricing ledger reopen unchanged; an unknown assigned model fails the Plan and creates no completed receipt. These weights are modeled planning assumptions, not measured per-agent runtime use. Full-suite and remaining TE-001.5 gates still determine release status.

**Enterprise inference progress (2026-07-27):** EWI-01 converted the exact 34 enterprise statements into a versioned machine-readable fixture with reviewed topology, minimum modalities/tools, agent-count expectation/source, and rationale. The required runner recorded a red baseline of 26 topology/count failures. FutureTokenPredictor now exposes a deterministic `analyze_workload` contract and MCP tool carrying schema/rule versions, description hash, topology confidence/evidence/alternatives, inferred role count provenance, and clarifications; the fixture passes 34/34 topology/count. Directional audio, document/image inference, enterprise action detection, and explicit `externally_billed_unpriced` tool evidence now satisfy the fixture's minimum modality/tool rubric without converting unknown charges to `$0`. This advances modeled `predict` only. Quantity/default clarification, complete field provenance and LLM merge controls, thin Studio analysis/review flow, receipt schema upgrade, visible-browser rerun, and the remaining release gates are still blocked/open.

**Studio analysis-first progress (2026-07-27):** EWI-07 is implemented for the current Plan seam. A red-first focused run recorded 11 failures across the absent adapter method/API, Studio-owned semantic overrides, confirmed-profile submission, and clarification safety. `McpPredictorClient.analyze()` now calls FutureTokenPredictor's `analyze_workload` tool; `_build_arguments()` no longer classifies prose and forwards only explicit selections and the reviewed profile. Studio exposes `/api/analyze`, renders editable topology, agent count, modalities, tools, confidence, alternatives, evidence, and clarifications, then requires confirmation before prediction. Low-confidence or clarification-bearing evidence creates a durable `needs_clarification` session with no receipt. The existing immutable intake snapshot retains analysis/rule evidence and the confirmed profile pending EWI-08's dedicated receipt-version upgrade. Focused tests pass 27/27, the complete Studio suite passes 45/45, and the mandated predictor suite remains 421/421. Visible browser evidence confirmed pointer analysis and completion to a rendered immutable receipt, plus keyboard activation yielding `multi_agent` with two inferred roles. A separate local-environment gap remains: running Studio outside the project environment lacks `azure.appconfiguration`, so the full no-console-error release gate is not yet satisfied. These are measured local development results, not release approval.

**Immutable analysis evidence progress (2026-07-27):** EWI-08 is implemented for new Plan receipts. A red-first receipt test proved new receipts were still schema `1.0`. New receipts are now schema `2.0` and hash first-class analysis, confirmed profile, assumptions, clarifications, and exclusions alongside intake, prediction, and infrastructure evidence. Admission integrity verification reconstructs the version-specific snapshot. A separately written historical schema-`1.0` fixture reopens byte-for-byte semantically unchanged, without invented analysis or confirmed-profile fields. Focused compatibility tests pass 2/2 and the complete Studio suite passes 46/46. Reanalysis/new-receipt browser matrix coverage remains part of EWI-09.

**Release-surface pivot progress (2026-07-28):** The TE-001.5 first release boundary now serves the existing Studio shell and keeps only Plan interactive in v1. Govern, Runs, Observe, and Reconcile remain visible but explicit read-only surfaces with v2 messaging, and run/govern mutation entry points are disabled from the v1 flow. The release manifest was updated to `surface: studio-plan-readonly` with `static_entry: studio.html`, and boundary enforcement was rerun at 8/8 passing. The full `06_prototype` suite was rerun after the pivot at 55/55 passing (plus one environment deprecation warning). These are measured local results and do not by themselves close the remaining TE-001.5 proof gates.

**Nine-experience validation evidence (2026-08-20):** A versioned fixture and executable HTTP-boundary matrix now cover every Plan delivery option: Microsoft Copilot for employees, Copilot Cowork, Agent Builder, Copilot Studio, Work IQ APIs, Microsoft Foundry, GitHub Copilot, Copilot Studio + Foundry, and Foundry + Work IQ. Each case independently recomputes its applicable seat, native-credit, scenario, purchase, token-derived-credit, predictor-pass-through, or period-normalized hybrid arithmetic and reopens an unchanged schema-4.0 receipt. Red-first validation exposed and corrected a missing scenario completion status, mixed monthly/annual hybrid totals, capacity-risk receipt completion, purchase-scope hard failure, missing GitHub long-context selection, loss of known fixed-seat cost with disabled overages, unknown-schema fallback hashing, and a `react_agent` compatibility regression. The corrected Foundry + Work IQ annual hybrid total is `$391.40`. The focused release tests pass 52/52, the nine-case matrix passes 10/10, the complete Studio suite passes 105/105, FutureTokenPredictor's mandated runner passes 485/485 with evidence exit 0, all 9 visible cards activate the expected guided inputs and action with no browser errors, and a 53-file allowlisted package builds, verifies hashes, and imports in isolation. Full details are in [`15_PLAN_NINE_EXPERIENCE_VALIDATION.md`](15_PLAN_NINE_EXPERIENCE_VALIDATION.md).

**Business goal:** Release a trustworthy first public slice consisting only of the TokenEconomics Studio shell, Plan experience, immutable Plan evidence, and FutureTokenPredictor dependency closure. Users must be able to describe a workload, select a priced provider/model offering, estimate invocation tokens and model/tool charges at scale, inspect assumptions and calculation provenance, and reopen an unchanged receipt. Unreleased Govern, Runs, Observe, Reconcile, RAG, Azure policy, and deployment implementation must not leak into the published artifact.

**Deliverables:**

1. A Plan-only runtime boundary that does not import or package Govern, Runs, Observe, Reconcile, RAG, Azure policy publication, or unrelated prototype modules.
2. A machine-readable release allowlist and dependency-closure check covering the Studio shell/Plan UI, Plan API/runtime, receipt/report persistence needed by Plan, packaging/setup material, and the nested FutureTokenPredictor package.
3. A Fluent 2-inspired Studio visual treatment based on the supplied `CanvasslideTemplate-Fluent.html`, preserving accessible semantics, responsive behavior, and the evidence labels required by the constitution.
4. An automated release-evidence report with exact commands, environment, revision identities, test counts, coverage by scoped module, stress results, known exclusions, and pass/fail status. Generated runtime data, credentials, caches, local environments, and unrelated prototype files are excluded.
5. The completed EWI-01 through EWI-09 workload-inference remediation, including a reviewed machine-readable 34-case corpus, description-first/explicit-override merge behavior, field-level analysis provenance, material-ambiguity clarification, enterprise-tool exclusions, and visible Studio confirmation before immutable completion.

**Required proof matrix:**

| Gate | Required evidence |
|---|---|
| Integration | Real local stdio MCP catalog and prediction round trips; exact provider/model selection; immutable receipt create/reopen; restart persistence; predictor failure, timeout, malformed response, and unavailable-runtime behavior. |
| Forecast correctness | Deterministic golden cases for text, RAG/document, reasoning, multimodal, cache scaling, and tool-cost separation; arithmetic invariants independently recomputed from output; provider/model pricing provenance retained. |
| Extreme constraints | Empty and oversized descriptions; minimum and maximum supported users/calls; zero, negative, non-integral, non-finite, and overflow-oriented inputs; unavailable/unpriced/unknown offerings; Unicode and hostile display strings; disk/write errors; interrupted and duplicate requests. |
| Stress and concurrency | Defined latency/error targets for catalog reads, concurrent forecasts, receipt writes, and reopen operations; bounded subprocess/resource behavior; no duplicate/corrupt receipts or cross-report data leakage. Load-test values and machine specifications must be recorded before interpreting results. |
| Regression | Existing Studio and predictor suites remain green; fixed regression corpus pins response schema, calculation trace, receipt hash behavior, and UI rendering contracts. Intentional forecast changes require reviewed fixture/version updates rather than silent snapshot replacement. |
| Quality | Branch and boundary coverage for Plan-owned code, mutation or equivalent fault-injection evidence for critical arithmetic/validation paths, static diagnostics, dependency audit, secret scan, and license/source review. A raw aggregate coverage percentage alone is insufficient. |
| UI | Browser tests for create/open report, catalog load/failure, submit/success/failure, calculation disclosure, receipt reopen, keyboard navigation, focus visibility, accessible names, contrast, responsive layouts, and no browser-console errors. Visual snapshots cover agreed desktop/mobile and light/dark variants. |
| Release isolation | A clean-source build contains exactly the allowlisted files and transitive runtime dependencies. The check fails on an extra prototype file, a missing dependency, generated evidence, secret, cache, virtual environment, or mutable local data. The root repository destination and nested predictor revision are explicit. |

**Acceptance:**

- The Plan-only dependency graph and publish allowlist are reviewed and enforced automatically; no unrelated TokenGov or reference-workload implementation is in the release payload.
- All scoped tests pass from a clean environment using reproducible commands. FutureTokenPredictor results are accepted only from `scripts/run_tests.py`; the release evidence records real exit codes.
- Coverage thresholds are set only after the initial scoped report, then enforced per critical module and branch. Every error/validation branch in the Plan API, MCP adapter, persistence boundary, and forecast arithmetic has direct evidence.
- Stress targets, concurrency, duration, hardware, process/memory observations, and failure criteria are declared before the release run; the final report labels results measured on that environment rather than generalizing them as production capacity.
- Extreme inputs fail safely with bounded, user-readable errors and do not create completed receipts. Completed receipts remain immutable and reproducible.
- UI behavior and accessibility checks pass in supported browsers/viewports, and the Fluent theme does not alter Plan calculations or evidence semantics.
- Pricing/model metadata age and static/fallback status are visible. A priced catalog entry is not represented as current production availability, and a modeled percentile is not represented as a calibrated probability guarantee.
- The reviewed release candidate is captured in an identified Git revision, and the unrelated local Studio screenshot is excluded from release scope.

**Constitution alignment:** This strengthens `predict` and produces test, build, receipt, and release-manifest evidence. It preserves the control-plane role of Plan, immutable history, explicit model-call scope, and honest modeled/measured labels. Segment-level acceptance, policy authority, complete trajectory quality, response, reconciliation, and learning remain outside this release slice and are not weakened or claimed complete.

#### MODELREL-01 - Curate and version Foundry model/pricing releases

**Status:** Complete
**Parent:** TE-001.5
**Lifecycle:** predict

**Evidence:** [`foundry-model-release.v2.json`](../06_prototype/data/model_catalogs/foundry-model-release.v2.json) is the current Studio release; [`foundry-model-release.v1.json`](../06_prototype/data/model_catalogs/foundry-model-release.v1.json) remains immutable historical provenance. Release `2026-08-25.2` expands the catalog to 98 sourced offerings: 86 OpenAI-on-Azure and 12 Anthropic-on-Foundry, grouped into text, embeddings, image, video, audio, and specialized modalities. The full catalog is visible, while only 50 coordinator-capable entries with verified model-specific input/output prices and compatible deployment paths are enabled in default/coordinator and per-agent selectors. Catalog presence, coordinator capability, and selector eligibility are separate fields; unavailable prices carry explicit reasons and are never inherited from adjacent models.

OpenAI rates pin Azure Retail Prices API Global Standard meters, dimensions, and effective dates; Anthropic rates pin its Foundry-specific CCU contract, cache-read/write dimensions, and Microsoft availability/billing references. FutureTokenPredictor now uses the same exact prices for every selectable model, including corrected `o3-pro` ($20/$80 per million input/output tokens), `gpt-4.5-preview` ($70/$150), canonical hyphenated Claude Foundry IDs, and non-rounded cache rates. A cross-repository regression fails if any selectable release entry lacks predictor metadata or differs on any price dimension. New receipts retain the exact release, offering, pricing snapshot, deployment context, and source evidence; historical receipts are not repriced or rewritten.

**Report correction:** Studio now loads delivery and model catalogs before reopening a receipt, selects the route recorded by the immutable receipt, and therefore reopens Foundry report `RPT-20260825-3C4ABA0C` as `foundry` rather than defaulting to the first Microsoft Copilot experience. Historical unverified pricing is not rewritten; the report renders a prominent unverified-reference warning and labels its source as reference-only.

**Catalog presentation:** Studio groups all released offerings by modality, labels coordinator-capable but non-selectable entries separately from catalog-only models, displays modality-specific pricing units, and shows the explicit evidence reason when pricing is unavailable.

**Alignment:** This change consumes measured public pricing/model-availability evidence and produces a versioned modeled model-call release snapshot. It does not price complete tasks or trajectories, alter segment-level quality controls, create calibrated tail-risk evidence, change Azure policy authority, or claim reconciliation, production validation, quality, or savings. Historical receipts remain immutable.

### TE-001.6 - Add Copilot commercial-meter forecasting to Plan

**Status:** Complete - CPLAN-01 through CPLAN-10 implemented and verified
**Priority:** Complete after TE-001.5 release hardening and before TE-002 broadens the task/trajectory contract
**Depends on:** TE-001, TE-001.5
**Lifecycle:** predict
**Scope:** Add route-first, entitlement-aware Copilot Credit and subscription economics beside FutureTokenPredictor's model/token forecasting. This task produces modeled control-plane forecasts only; it does not enable Govern, execute Copilot workloads, publish policy, claim accepted-task quality, or treat modeled percentiles as calibrated tail-risk guarantees.

**Design decision:** FutureTokenPredictor remains the authority for versioned workload analysis, provider/model validation, token distributions, and model-price subforecasts. A parallel Plan-side commercial-meter component owns Copilot rate cards, entitlement dispositions, native usage events, credit forecasts, and purchase-source allocation. Studio composes both components for BYOM and Foundry + Work IQ routes and persists their evidence in a new immutable receipt version.

**Evidence consumed:**

- versioned workload analysis and user-confirmed profile;
- product route, experience, and harness;
- sourced/effective Copilot commercial rate card;
- license, identity, audience, channel, trigger, test-mode, and product-boundary assumptions;
- Cowork or variable Work IQ scenario priors where deterministic rates are unavailable;
- FutureTokenPredictor prediction and pricing identities for hybrid routes;
- versioned seat, capacity-pack, P3, PAYG, allocation, and overage assumptions.

**Evidence produced:**

- native event quantities and calculation trace;
- entitlement decision per material meter;
- expected/modelled Copilot Credit demand;
- independently identified token/model subforecasts;
- retail, invoice, incremental, commitment, fixed-allocation, and amortized economic views;
- explicit unknowns and exclusions;
- immutable composite Plan receipt.

**Quality and claims:** Every forecast carries a workload segment and explicit observation unit. This task does not introduce a runtime quality claim. Any acceptance probability is a labeled planning assumption rather than an evaluator score. Copilot Studio deterministic rates are documented; purchase allocation is simulated; Cowork/variable Work IQ distributions are modeled until representative actuals establish calibration. No output is a guaranteed quote, saving, quality result, or chance constraint.

**Architecture and authority:** The work remains in the control plane. Microsoft-specific rate cards and entitlement rules stay outside reusable TokenGov policy/runtime core. Missing or stale material commercial evidence fails closed. Studio does not receive Azure policy-publisher credentials and does not override Microsoft 365 or Power Platform enforcement. Historical receipts remain immutable.

#### CPLAN-01 - Freeze commercial contracts and rate fixtures

**Status:** Complete - immutable contracts and sourced standard-harness fixture added
**Owner:** TokenEconomics Plan
**Work:** Add reusable contracts for economic routes, commercial meters, entitlement context/decisions, native usage events, and evidence status. Add a versioned, source-pinned Copilot Studio standard-harness rate fixture.

**Acceptance:**

- credits remain a native commercial unit and are never converted into assumed tokens;
- every meter records product, experience, harness, feature, native unit, unit size, rate-card version, effective dates, source URL/retrieval time, and evidence status;
- duplicate, negative, non-finite, unreviewed, stale, or source-free rates fail validation;
- the fixture pins classic/generative answers, agent actions, tenant Graph grounding, flow actions, AI tools, content processing, and voice rates;
- BYOM separation is explicit.

#### CPLAN-02 - Implement fail-closed rate-card loading

**Status:** Complete - fail-closed loader and focused regression coverage added
**Owner:** TokenEconomics Plan
**Depends on:** CPLAN-01
**Work:** Load and validate commercial rate cards independently from FutureTokenPredictor. Select only an active meter for the forecast date and retain exact source provenance.

**Acceptance:**

- missing or malformed fixtures produce bounded errors;
- no silent fallback rate exists;
- effective-date selection is deterministic;
- reopening persisted evidence does not consult or reinterpret the current rate card.

**Measured progress evidence (2026-08-20):** `python -m pytest tests\test_commercial_rate_cards.py tests\test_plan_release_boundary.py -q` completed with 16 passing tests. This evidence covers the initial commercial contract/rate-card boundary and Plan release boundary only; it does not validate CPLAN-03 onward.

#### CPLAN-03 - Implement entitlement decisions

**Status:** Complete - versioned per-meter entitlement decisions fail closed
**Depends on:** CPLAN-02
**Work:** Evaluate billing disposition per meter and workload segment using license, authenticated identity, audience, channel, trigger, test mode, computer use, and product boundary.

**Acceptance:**

- `audience=B2E` alone never implies zero-rated usage;
- qualifying authenticated Microsoft 365 Copilot employee usage is distinguishable from included fixed subscription allocation;
- computer use and nonqualifying agent-flow triggers remain billable;
- test exemptions apply to individual meters rather than the whole task;
- material unknowns produce `unknown_requires_policy` and block decision-grade completion.

#### CPLAN-04 - Add deterministic Copilot Studio forecasting

**Status:** Complete - additive native-unit forecasting implemented
**Depends on:** CPLAN-03
**Work:** Forecast additive standard-harness feature events and scale them by segment/task volume.

**Acceptance:**

- expected credits equal the sum of native event quantities times applicable rates and billable shares;
- reasoning feature charges and premium token charges compose without converting credits to model tokens;
- raw actions, pages, minutes, and billed-unit assumptions remain inspectable;
- Copilot Credits and USD are separate outputs.

#### CPLAN-05 - Compose Foundry/BYOM and Work IQ hybrids

**Status:** Complete - independent commercial and model subforecasts compose without unit conversion
**Depends on:** CPLAN-04
**Work:** Compose commercial-meter forecasts with unchanged FutureTokenPredictor subforecasts for Copilot Studio BYOM and Foundry + Work IQ.

**Acceptance:**

- each subforecast retains its identity, scope, model/rate version, and exclusions;
- hybrid totals equal independently evidenced components;
- unknown external charges remain unpriced rather than `$0`;
- FutureTokenPredictor does not acquire licensing or purchase-allocation logic.

#### CPLAN-06 - Add fixed-seat and purchase-portfolio economics

**Status:** Complete - commitment, PAYG, capacity-risk, fixed-allocation, and amortized views implemented
**Depends on:** CPLAN-04
**Work:** Model fixed Microsoft 365 Copilot seat allocation, capacity packs, environment allocation, P3, PAYG, overage, and unused commitment.

**Acceptance:**

- retail cost, invoice cost, incremental cash cost, commitment drawdown, fixed allocation, and amortized cost remain separate;
- unused capacity changes realized unit economics;
- allocation respects product/environment/scope evidence;
- no configured overage source is surfaced as capacity risk rather than silently billed.

#### CPLAN-07 - Add Cowork and variable Work IQ scenario forecasts

**Status:** Complete - seeded modeled scenario priors implemented with explicit calibration status
**Depends on:** CPLAN-02
**Work:** Add persona/task-class priors, task-volume distributions, and calibration metadata for commercial meters without a complete public deterministic formula.

**Acceptance:**

- Cowork credits are never inferred from token count alone;
- every prior has source, version, evidence status, and applicable segment;
- fixed seeds reproduce modeled distributions;
- P50/P95 are labeled modeled until empirical coverage is established.

#### CPLAN-08 - Make Studio Plan route-first

**Status:** Complete - route selection precedes optional model selection in Studio Plan
**Depends on:** CPLAN-04, CPLAN-05
**Work:** Change Plan to analyze task -> select route -> resolve entitlement -> configure native usage -> request a model only when needed -> configure purchase portfolio -> review forecast.

**Acceptance:**

- included, Copilot Studio, Cowork, Work IQ, Foundry, and hybrid routes are distinguishable;
- the model catalog is not required for non-model routes;
- material missing evidence stops at `needs_clarification`;
- candidate comparison does not select/admit a route or enable read-only v1 surfaces.

#### CPLAN-09 - Introduce immutable receipt schema 3.0

**Status:** Complete - schema-3.0 hashes commercial and hybrid evidence while preserving legacy reads
**Depends on:** CPLAN-04, CPLAN-05, CPLAN-06
**Work:** Hash route, rate-card, entitlement, event, token-subforecast, purchase, economics, acceptance-assumption, and exclusion evidence.

**Acceptance:**

- schema-1.0 and schema-2.0 receipts reopen without reinterpretation;
- schema-3.0 receipts reopen without consulting current rates;
- changes to any decision-relevant commercial evidence change the hash;
- reforecasting creates a new Plan/receipt and never rewrites history.

#### CPLAN-10 - Complete TE-001.6 release evidence

**Status:** Complete - focused, full-suite, predictor, package, and browser evidence captured
**Depends on:** CPLAN-01 through CPLAN-09
**Work:** Extend the Plan release allowlist only with verified commercial dependencies and run focused, full-suite, browser, failure, stress, compatibility, and clean-package evidence.

**Acceptance:**

- deterministic golden cases and arithmetic invariants pass;
- entitlement and purchase edge cases fail safely;
- Studio and the mandated FutureTokenPredictor suite remain green;
- Govern/Runs/Observe/Reconcile remain read-only;
- the release report labels documented, modeled, simulated, measured, blocked, and production-validated evidence correctly.

**Measured completion evidence (2026-08-20):**

- TE-001.6 focused suite: 40 passed;
- complete Studio suite: 76 passed;
- mandated FutureTokenPredictor suite: 484 passed through `scripts\run_tests.py --all --expect pass`;
- allowlisted release: 50 files built and hash verification passed;
- route-first Studio JavaScript parsed successfully and the served UI rendered successfully in headless Microsoft Edge;
- Python compilation completed for `costgov`, `plan_studio.py`, and `studio.py`.

The evidence is measured for deterministic software behavior and release packaging. Copilot rates and entitlement rules are documented evidence; Cowork/variable Work IQ distributions and allocation economics remain modeled; no tenant billing, accepted-task quality, calibrated tail risk, production execution, or savings outcome was validated.

**Remaining constitutional gaps after TE-001.6:** No Copilot route has segment-level runtime quality evidence, observed accepted-task outcomes, calibrated budget-breach probability, admitted cross-meter policy, policy-bound execution, admin-center/invoice reconciliation, or learned subsequent forecast. TE-001.6 improves `predict`; it does not complete the end-to-end lifecycle.

### TE-001.7 - Add experience-led meter-stack and GitHub Copilot forecasting

**Status:** Complete - meter contracts, GitHub forecasting, guided intake, schema-4.0 receipts, and release evidence verified
**Priority:** Complete after TE-001.6 and before TE-002
**Depends on:** TE-001, TE-001.5, TE-001.6
**Lifecycle:** predict
**Scope:** Replace the single-route mental model with versioned product meter stacks and guide users from work description to delivery experience to route-specific evidence. Add GitHub Copilot as an independent token-derived GitHub AI Credit forecast while leaving Microsoft Foundry model-token calculations under FutureTokenPredictor.

**Design decision:** A product can combine subscription, included entitlement, native credit, token-derived credit, direct token, and resource layers. Studio therefore resolves a versioned meter stack rather than selecting one universal unit. Microsoft Copilot Credits and GitHub AI Credits remain independent currencies with independent authorities and no implicit conversion.

**Evidence consumed:**

- selected delivery experience and `consumption-models.v1` meter-stack contract;
- versioned Microsoft subscription, entitlement, Copilot Credit, and Copilot Studio evidence;
- user-confirmed GitHub plan, seat count, token quantities, fixed seat cost, and overage setting;
- source-pinned GitHub model-token prices and included AI Credit allowances;
- unchanged FutureTokenPredictor workload, model, pricing, and calculation evidence for Foundry-backed routes.

**Evidence produced:**

- resolved product meter stack with layer family, unit, currency, authority, applicability, and source;
- GitHub model-token line items, USD model cost, gross/included/additional GitHub AI Credits, and capacity-risk state;
- generated route-specific evidence preview instead of a normal-path raw JSON editor;
- immutable schema-4.0 receipt hashing meter-stack evidence alongside prior Plan evidence;
- historical schema-1.0 through schema-3.0 compatibility without reinterpretation.

#### MSTACK-01 - Define and validate product meter stacks

**Status:** Complete
**Work:** Add versioned stacks for Microsoft Copilot, Cowork, Agent Builder, Copilot Studio, Work IQ APIs, Microsoft Foundry, GitHub Copilot, and both supported hybrid routes.

**Acceptance:** Duplicate or incomplete layers fail validation; each layer identifies its unit, currency, authority, source, evidence status, and conditional applicability; Microsoft and GitHub credit currencies remain distinct.

#### MSTACK-02 - Add GitHub Copilot commercial forecasting

**Status:** Complete
**Depends on:** MSTACK-01
**Work:** Load fail-closed GitHub rate evidence and convert explicit input, cached-input, cache-write, and output token quantities to dollar-denominated GitHub AI Credits.

**Acceptance:** Rate evidence is source-pinned and effective-date checked; token-price arithmetic, pooled plan allowance, additional usage, fixed seat allocation, and capacity risk remain inspectable; reasoning-token pricing is not invented; GitHub Actions minutes remain an explicit unpriced resource exclusion.

#### MSTACK-03 - Make Studio intake experience-led

**Status:** Complete
**Depends on:** MSTACK-01
**Work:** Ask users to describe the work, choose how it will be delivered, inspect the resolved meter stack, and provide only route-relevant inputs.

**Acceptance:** All nine experiences render their correct guided fields; non-model routes bypass FutureTokenPredictor; Foundry-backed routes retain analyze/review/confirm behavior; generated evidence remains available read-only for review.

#### MSTACK-04 - Persist immutable meter-stack receipts

**Status:** Complete
**Depends on:** MSTACK-01, MSTACK-02, MSTACK-03
**Work:** Introduce schema 4.0 and include the resolved stack in receipt hashing and admission-integrity reconstruction.

**Acceptance:** Decision-relevant stack changes alter the hash; schema-1.0 through schema-3.0 receipts remain readable; new Foundry, Copilot, hybrid, and GitHub forecasts use schema 4.0.

#### MSTACK-05 - Complete TE-001.7 release evidence

**Status:** Complete
**Depends on:** MSTACK-01 through MSTACK-04
**Measured completion evidence (2026-08-20):**

- focused meter-stack, GitHub, commercial, receipt, boundary, and API suite: 44 passed;
- complete Studio suite: 88 passed;
- mandated FutureTokenPredictor suite: 484 passed through `scripts\run_tests.py --all --expect pass`;
- allowlisted release: 53 files built and hash verification passed;
- Python modules compiled and Studio JavaScript parsed successfully;
- restarted Studio returned HTTP 200, exposed `consumption-models.v1` with nine experiences, and rendered without browser console errors;
- browser checks confirmed the correct guided-field combination and action for every experience, and a GitHub Copilot forecast rendered its resolved stack and 4,500 gross GitHub AI Credits.

The software and browser results are measured. Product terms and published rates are documented evidence. User quantities, seat allocations, and forecast outputs are modeled. No tenant invoice, production execution, accepted-task quality, calibrated tail-risk, or savings outcome was validated.

**Development alignment gate:** TE-001.7 advances control-plane `predict` only. It preserves model-call versus complete task/trajectory scope, explicit currency boundaries, immutable evidence, Microsoft-specific workload logic outside reusable TokenGov core, two-plane separation, and fail-closed evidence loading. No segment-level quality control is weakened because this task introduces no runtime acceptance claim.

**Remaining constitutional gaps after TE-001.7:** No route has end-to-end task/trajectory capture, segment-level accepted-task outcomes, calibrated budget-breach probability, policy comparison/admission across meter stacks, policy-bound execution, runtime response, tenant billing reconciliation, or learned subsequent forecast. TE-001.7 does not complete `compare policy -> admit -> execute -> evaluate -> respond -> reconcile -> learn`.

### TE-002 - Define the task/trajectory envelope

**Status:** Complete
**Depends on:** TE-001, TE-001.5, TE-001.6, TE-001.7
**Lifecycle:** predict, execute, evaluate, reconcile
**Deliverable:** Versioned framework-neutral contracts for workload, task, trajectory, segment, step, policy binding, prediction binding, and timestamps.

**Start evidence (2026-08-20):** Red-first validation recorded the missing contract module. The first implementation slice now defines stable workload, task, trajectory, run, trace, segment, prediction, policy, and step identities; exact prediction receipt and Azure policy provenance bindings; UTC timestamp ordering; generic retrieval, model, tool, cache, retry, and iteration steps; canonical serialization; and content-hashed append-only persistence. Ten focused tests pass, including exact round trip, all six step kinds, duplicate/orphan rejection, fail-closed policy provenance, hashed path-safe storage, temporal validation, concurrent duplicate-write rejection, atomic temp cleanup, and tamper detection. The complete Studio suite passes 115/115.

**Completion evidence (2026-08-20):** New Plan receipts use schema `5.0` and bind `trajectory-envelope.v1`, stable workload identity, and segment-schema version. Govern adds the exact prediction-receipt hash and fail-closed Azure policy revision, label, and ETag. Execution context, gateway telemetry, sampled evaluation outcomes, reconciliation records, persisted trajectory envelopes, and Studio evidence views retain the same workload, task, trajectory, segment, receipt, and policy identities. `data/contracts/trajectory-envelope.v1.schema.json` publishes the framework-neutral JSON Schema compatibility boundary, while Python validation additionally enforces temporal ordering, parent ordering, unique evidence keys, and content-hash integrity. The focused TE-002 and release-boundary set passes 21/21; the complete Studio suite passes 118/118; the Plan-only release contains 55 allowlisted files, verifies every hash, and imports in isolation.

**Development alignment gate:** TE-002 advances the contract and evidence boundary across `predict -> execute -> evaluate -> reconcile`. It consumes immutable schema-5 prediction receipts and exact fail-closed policy provenance, and produces content-hashed trajectory envelopes, identity-bound telemetry, per-task raw evaluation outcomes, and reconciliation joins. Model-call evidence remains distinct from the complete task/trajectory envelope. Segment identity is preserved without claiming an accepted-task outcome, modeled percentiles are not described as calibrated tail risk, workload-specific RAG translation remains outside reusable TokenGov core, and historical receipt schemas 1.0-4.0 remain readable without reinterpretation.

**Remaining constitutional gaps after TE-002:** TE-002 does not complete policy comparison across candidates, production admission, real deployed workload execution, accepted-task quality, user response evidence, tenant billing reconciliation, forecast learning, or calibrated budget-breach probability. The current orchestrator proof is simulated; TE-003 must supply the first real deployed RAG adapter and trajectory outside `costgov/`.

**Acceptance:**

- The contract represents retrieval, model, tool, cache, retry, and iteration steps without RAG-specific fields in the core type.
- IDs remain stable across Plan, Govern, telemetry, evaluation, and reconciliation.
- Schema validation and round-trip persistence tests pass.

### TE-003 - Build the RAG workload adapter

**Status:** Complete
**Depends on:** TE-002
**Lifecycle:** execute
**Deliverable:** An adapter outside `costgov/` that translates the deployed Foundry agent's traces/events into the trajectory envelope, carries an admitted policy binding into execution, and emits the direct-token and resource-meter evidence available from that workload. If Agent 365 is licensed and available for the reference agent, also preserve its agent/blueprint identity and OpenTelemetry correlation as optional external evidence; Agent 365 availability is not a prerequisite for the core RAG trajectory proof.

**Start evidence (2026-08-21):** `rag/foundry_trajectory_adapter.py` now validates the complete admitted `trajectory-envelope.v1` binding before creating a Foundry conversation, invokes a pinned prompt-agent version through the official project Responses API, translates MCP discovery, `knowledge_base_retrieve`, final model response, provider token usage, and citations into generic ordered steps, hashes rather than copies retrieved corpus output, and appends the resulting envelope through `TrajectoryStore`. `rag/capture_foundry_trajectory.py` provides the Entra-authenticated live command. Four focused adapter tests plus the twelve existing trajectory contract/integration tests pass (16/16). The tests use simulated provider objects and therefore prove translation and fail-closed behavior, not live execution.

**Completion evidence (2026-08-25):** A new proof environment in subscription `a91cc1ba-bd19-43a7-90ea-120794c0fbc6` uses resource group `rg-tokeneconomics`, Foundry project `ai-project-tokeneconomics-te003`, agent `tokengov-books-rag-agent:1`, Foundry IQ knowledge base `books-knowledge-base`, and Azure App Configuration authority `appcs-xbk6ickycmp22`. Script `scripts/run_te003_live_test.py` created schema-5 report `RPT-20260825-3C4ABA0C`, admitted receipt `plan_f5504c8d0c6ee9bea293` under policy `tokengov-te003-live-proof:2026-08-25.2` at label `te003-live-v2` and ETag `4QngvGCIxXB3E8h240XjR3FuxMpcZXe9Ac2Jt977IhU`, invoked the pinned hosted agent, and reopened trajectory `trajectory-605b9b868a5d45068d20c45e7973590b` with content hash `06095b9d2ea884c454755c1c64f941bd75301df19f2ac90c66c7894ead12fd2b`.

The measured trajectory contains ordered agent-response, MCP discovery, reasoning, `knowledge_base_retrieve`, and cited model-synthesis steps. Foundry reported 11,530 input tokens, 138 output tokens, and 11,668 total tokens. Search/resource-meter cost and model cost remain explicitly unavailable/unpriced in the trajectory. The associated Plan forecast records `pricing_verified: false` and warns that the deployment is absent from local pricing catalogs. Azure policy revision `2026-08-25.2` therefore permits unverified pricing for this execution-binding proof; its passed model-cost ceiling is not decision-grade Luna price validation. An earlier report that exposed the false `pricing_verified` claim remains immutable historical evidence and is excluded from TE-003 acceptance.

Red-first predictor work corrected catalog presence being mislabeled as verified price evidence. The complete predictor test module passes 21/21 through its required evidence runner, and the focused TE-003/trajectory/release-boundary set passes 28/28. These tests and the live report prove the adapter acceptance criteria, not accepted-task quality or complete economics.

**Development alignment gate:** This slice advances deployed-workload `execute` only. It consumes an immutable schema-5 prediction binding, exact fail-closed Azure policy provenance, stable workload/task/trajectory/segment identities, and a pinned Foundry agent reference. It produces an append-only Foundry/RAG reference envelope with provider-reported model tokens and explicit unavailable/unpriced retrieval, resource, and model-cost fields. Model and retrieval steps remain distinct from the complete trajectory; segment identity is preserved without inventing accepted-task quality; no modeled percentile is treated as calibrated tail risk; and all Foundry, MCP, Search, and corpus translation remains under `rag/`, outside reusable `costgov/`.

**Remaining constitutional gaps after TE-003:** This is one measured factual-lookup execution, not a representative experiment or production validation. TE-004 must pin the complete experiment manifest; TE-005 and later work must add explicit acceptance outcomes, segment-level sample sufficiency, end-to-end multi-meter cost, candidate comparison, calibrated budget-risk evidence, bounded response, billing reconciliation, and predictor learning. Agent 365 remains optional and was not required or integrated for this proof.

**Acceptance:**

- At least one real deployed RAG trajectory is captured end to end.
- The adapter does not copy corpus, prompt, or retrieval implementation into TokenGov core.
- An invalid or absent policy binding fails closed.
- The result is labeled as the Foundry/RAG reference proof, not evidence that Microsoft Copilot, Cowork, Copilot Studio, Work IQ, or GitHub Copilot telemetry has been integrated.
- Any Agent 365 export distinguishes attempted transport, HTTP acceptance, verified ingestion, downstream visibility, and unavailable coverage; none is treated as authoritative model or commercial billing evidence.

### TE-004 - Create the experiment manifest

**Status:** Ready
**Depends on:** TE-001, TE-002
**Lifecycle:** predict, compare policy, reconcile
**Deliverable:** An immutable manifest that pins workload, corpus/index, retrieval, prompt/agent, golden set, evaluator, model catalog, pricing, resolved meter stack, commercial rate cards, entitlement and purchase-allocation contracts, billing period, policy candidate, infrastructure revisions, and any applicable external agent-governance evidence contract and capability-maturity revision.

**Acceptance:**

- Baseline and candidate runs can prove they used the same representative task set and all intentionally shared revisions.
- Any intentional arm difference is machine-readable.
- Historical manifests remain readable after later revisions.
- Non-applicable evidence is explicit; a commercial-only or hybrid workload is not forced to invent a model, corpus, or evaluator revision.
- Agent 365, Entra, Defender, or Purview evidence is optional unless the candidate explicitly requires it; required evidence pins freshness, source authority, maturity, coverage, and fail behavior.

## Cross-cutting track: Microsoft Agent 365 integration

This track runs beside the Foundry/RAG proof and does not replace TE-003 through TE-016. Its first-party research basis is the Microsoft [Agent 365 overview](https://learn.microsoft.com/en-us/microsoft-agent-365/overview), [service description](https://learn.microsoft.com/en-us/office365/servicedescriptions/microsoft-agent-365/microsoft-agent-365), and [observability concepts](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/observability-concepts); decision D41 records the durable project boundary.

### A365-01 - Define external agent-governance evidence

**Status:** Ready
**Depends on:** TE-002
**Lifecycle:** predict, compare policy, admit, execute, observe, respond, reconcile
**Deliverable:** Versioned `agent365-evidence.v1`, `external-governance-posture.v1`, and correlation-profile contracts that reference Agent 365 evidence from the framework-neutral trajectory without adding Microsoft-specific required fields to TokenGov core.

**Acceptance:**

- The contracts preserve Agent 365 trace, conversation, session, agent identity, blueprint, platform, channel, and span references alongside TokenEconomics workload, task, trajectory, run, step, segment, prediction, and policy identities.
- Every record carries source, event and collection timestamps, maturity, coverage, freshness, retention deadline, ingestion-verification state, immutable hash/reference, and explicit unavailable fields.
- One Agent 365 message/reply run can map into a longer TokenEconomics task/trajectory without forcing one-to-one identity.
- Agent 365-specific collection and translation remain outside `costgov/`.

### Conditional Agent 365 follow-on gate

Do not schedule separate Agent 365 runtime, Govern, Observe, or Reconcile milestones yet. After A365-01, reconsider a real connector only as part of TE-016's portability proof or a later evidence-backed amendment. Proceed only when all of the following are true:

- a licensed tenant and supported reference-agent identity/runtime are available;
- the required registry, observability, posture, or lifecycle APIs are documented and accessible;
- downstream telemetry ingestion can be verified rather than inferred from HTTP success;
- evidence can be retained in TokenEconomics before Agent 365's documented 30-day expiry;
- the integration answers a TokenEconomics economic, quality, or reconciliation question that existing Azure/Application Insights evidence cannot answer more directly;
- Studio can summarize or link to Agent 365 posture without recreating an Agent 365, Defender, or Purview administration console.

If the gate passes, amend TE-016 or create a new dated task and decision. Until then, Agent 365 remains an optional architecture connector and commercial subscription layer, not an active implementation dependency.

## Milestone 2: Acceptance and end-to-end economics

### TE-005 - Define accepted-task outcomes

**Status:** Not started
**Depends on:** TE-002, TE-004
**Lifecycle:** evaluate
**Deliverable:** Versioned segment-specific acceptance rules and an immutable outcome record distinct from raw evaluator scores.

**Acceptance:**

- Every evaluated task is `accepted`, `rejected`, or `inconclusive` with reason and evaluator provenance.
- Acceptance cannot be inferred by silently treating mean quality as a probability.
- Human-review and automated-evaluator outcomes can be represented separately.

### TE-006 - Implement the multi-meter trajectory ledger

**Status:** Not started
**Depends on:** TE-002, TE-003
**Lifecycle:** execute, observe
**Deliverable:** An append-only usage-and-cost ledger for subscriptions, included usage, Microsoft Copilot Credits, GitHub AI Credits, model tokens, resource meters, retrieval/tool activity, evaluation, observability, Agent 365 per-user licensing where applicable, and allocatable infrastructure by task, trajectory, step, segment, tenant, product, environment, and policy revision. Agent 365 operational/security evidence is correlated but remains distinct from authoritative commercial meter and invoice evidence.

**Acceptance:**

- Each entry includes meter family, native unit and currency, quantity, entitlement disposition, purchase source, evidence source, pricing/rate-card revision, billing period, and calculation or allocation method.
- Microsoft Copilot Credits, GitHub AI Credits, tokens, seats, resource units, and USD allocations remain distinct ledger dimensions; aggregation occurs only where a versioned sourced conversion or allocation supports it.
- Included or zero-rated use remains recorded with its usage quantity and entitlement evidence rather than disappearing as a zero-cost event.
- Unknown or excluded costs remain explicit rather than becoming zero.
- Aggregates reconcile to source telemetry within documented tolerances.
- Agent 365 spans or activity can corroborate that work occurred but cannot supply a missing native quantity, rate, credit conversion, or invoice amount.

### TE-007 - Add accepted-task economics to Observe

**Status:** Not started
**Depends on:** TE-005, TE-006
**Lifecycle:** observe
**Deliverable:** APIs and UI for completion rate, acceptance rate, native meter consumption, entitlement and overage disposition, committed-capacity utilization, total allocatable cost, cost coverage, cost per completed task, and cost per accepted task by segment, product, environment, meter stack, and policy revision. For Agent 365-enabled workloads, add separately labeled inventory/identity, activity, telemetry coverage, security/compliance, and adoption evidence.

**Acceptance:**

- Denominators and inconclusive outcomes are visible.
- Segment views cannot be replaced by an aggregate-only result.
- The UI labels measured, modeled, and incomplete economics accurately.
- Native currencies are displayed independently; a combined USD total appears only for components with sourced pricing or allocation evidence, with uncovered components visible.
- Operational completion, adoption, Defender/Purview events, explicit task acceptance, and economic outcomes remain separate dimensions.

## Milestone 3: Policy-conditioned planning and decisions

### TE-008 - Define immutable policy candidates

**Status:** Not started
**Depends on:** TE-002, TE-004
**Lifecycle:** compare policy
**Deliverable:** Candidate revisions that pin only applicable and enforceable controls: model/routing, cache, context, retry/iteration, evaluation, monetary budget, native-meter caps, committed-capacity thresholds, bounded overage actions, and optional external agent-governance posture requirements, plus meter-stack and authority provenance.

**Acceptance:**

- Candidates are comparable without mutating the active Azure policy.
- Candidate identity is carried through forecasts, simulations, evaluations, and decisions.
- Unsupported controls fail validation.
- Each control declares its authority and capability. Subscription assignments, tenant entitlements, commercial commitments, or external product settings remain evidence or reviewed recommendations unless an authorized integration exists.
- Agent 365 posture requirements declare source, freshness, maturity, enforcement coverage, and `satisfied | failed | inconclusive | unavailable`; they are not represented as Azure-publishable TokenGov controls.

### TE-009 - Add decision-grade quality and tail constraints

**Status:** Not started
**Depends on:** TE-005, TE-008
**Lifecycle:** compare policy, evaluate
**Deliverable:** Segment sample/confidence evidence plus explicit monetary budget $B$, tolerance $\epsilon$, and forecast $P(C_{\text{task}}>B)$ for each candidate, with separate native-meter exhaustion, committed-capacity, or overage-risk constraints where those are decision relevant.

**Acceptance:**

- Insufficient segment evidence produces `inconclusive`, not automatic eligibility.
- P95 is never substituted for breach probability without a documented distribution calculation.
- Reconciliation can later report empirical breach and coverage rates against the same values.
- Native-credit or resource-capacity risk is not presented as monetary tail risk unless a versioned rate or allocation contract supports the conversion.

### TE-010 - Integrate candidate selection into Govern

**Status:** Not started
**Depends on:** TE-006, TE-008, TE-009
**Lifecycle:** compare policy, admit
**Deliverable:** Govern comparison and decision evidence that selects the least expected allocatable-cost eligible candidate, or states that none satisfies all economic, quality, tail, native-meter, and applicable external agent-governance constraints, or that evidence coverage is insufficient for a decision.

**Acceptance:**

- Expected cost is policy-conditioned and uses a consistent task/trajectory unit.
- Every segment quality constraint and the chance constraint are visible in the decision.
- Selection produces a new immutable decision; it does not overwrite prior admissions.
- Comparisons retain the resolved meter stack and show native quantities, entitlement disposition, capacity/overage risk, sourced USD components, and uncovered components without cross-currency conflation.
- External Agent 365/Entra/Defender/Purview posture is source-attributed and separately evaluated; it does not replace TokenGov's final economic/quality admission or exact Azure policy binding.

## Milestone 4: Authorized feedback loop

### TE-011 - Persist evaluation decision state

**Status:** Not started
**Depends on:** TE-005, TE-009
**Lifecycle:** evaluate, respond
**Deliverable:** Durable sample-sufficiency, breach, hysteresis, optimization-eligibility, reversion, and recovery state by workload segment and policy revision, with separately sourced Agent 365/Entra/Defender/Purview events and external response-state references where applicable.

**Acceptance:**

- Restarting a service does not reset consecutive-breach history.
- Every transition cites the evaluation evidence that caused it.
- Bounded route/action ladders and recovery conditions are explicit.
- Response actions are scoped by meter family and authority; TokenGov does not silently mutate tenant licenses, credit purchases, or external product controls.
- Blocking, identity restriction, DLP, tool blocking, unpublish, or other external security/lifecycle actions require their own authorized identity, capability proof, outcome evidence, and rollback/recovery semantics.

### TE-012 - Implement the reviewed publisher handoff

**Status:** Not started
**Depends on:** TE-008, TE-010
**Lifecycle:** admit, respond
**Deliverable:** A PR/pipeline or equivalent approval path that validates a proposed policy, runs tests and deployment preview, publishes with a separate identity, and verifies Azure ETag/content hash.

**Acceptance:**

- Studio and runtime identities cannot publish policy.
- Publication failure leaves the prior policy authoritative.
- Approval, rollout, verification, and rollback evidence are durable.
- The publisher changes only the authoritative TokenGov policy surface. Commercial licensing, capacity purchases, and external product administration remain separately authorized actions or recommendations.
- Agent 365, Entra, Defender, Purview, and Intune administration remains outside the TokenGov Azure publisher identity.

### TE-013 - Prove bounded regression response on RAG

**Status:** Not started
**Depends on:** TE-003, TE-011, TE-012
**Lifecycle:** execute, evaluate, respond
**Deliverable:** A reproducible reference experiment where a candidate degrades one material RAG segment and is blocked or reverted through the authorized path.

**Acceptance:**

- The aggregate cannot hide the segment failure.
- Sample sufficiency and hysteresis behavior match the policy.
- The recovered run is bound to the verified replacement/reverted policy revision.

## Milestone 5: Reconciliation and learning proof

### TE-014 - Reconcile acceptance, multi-meter actuals, and tail coverage

**Status:** Not started
**Depends on:** TE-005, TE-006, TE-009, TE-013
**Lifecycle:** reconcile
**Deliverable:** Idempotent reconciliation of end-to-end accepted-task economics, native meter actuals, entitlement disposition, committed/included/PAYG allocation, sourced invoice or billing exports, model/resource cost, forecast error, budget or capacity breaches, percentile/chance-constraint coverage, and optional Agent 365 identity/activity/security evidence.

**Acceptance:**

- Re-running reconciliation does not duplicate calibration writes.
- Incomplete trajectories remain incomplete and are not treated as zero-cost successes.
- Results retain experiment, prediction, candidate, active policy, meter-stack, rate-card, entitlement, purchase-source, billing-period, pricing, and evaluator provenance.
- Missing tenant, product, invoice, or usage sources leave reconciliation explicitly partial; native currencies are not collapsed merely because they share a nominal cent value.
- Agent 365 activity remains corroborating operational evidence and never replaces authoritative native commercial meter, price, entitlement, or invoice evidence.

### TE-015 - Demonstrate forecast learning

**Status:** Not started
**Depends on:** TE-014
**Lifecycle:** learn, predict
**Deliverable:** A before/after experiment showing correlated representative actuals routed to the correct learning boundary: model-token and workload actuals to FutureTokenPredictor; native-credit, entitlement, purchase-allocation, seat, and resource-meter actuals to versioned commercial forecast calibration outside FutureTokenPredictor; accepted outcomes to quality calibration; and Agent 365 operational/security/adoption evidence to separate workload, risk, and governance analysis.

**Acceptance:**

- Historical forecasts remain unchanged.
- The new forecast identifies the calibration evidence it used.
- Improvement or non-improvement is reported honestly using a predefined error metric.
- Commercial credits or product entitlements are never converted into token-training observations, and token actuals do not rewrite commercial rate cards.
- Agent 365 operational completion or adoption never becomes an implicit accepted-task label.

### TE-016 - Run the portability test

**Status:** Not started
**Depends on:** TE-015
**Lifecycle:** full lifecycle
**Deliverable:** Integrate a materially different second workload with an authoritative non-Foundry or hybrid meter source through the same contracts and document any core-schema changes required. Prefer an Agent 365-correlated Copilot Studio + Foundry or Foundry + Work IQ workload when tenant telemetry and documented APIs are available; otherwise select another route with source-verifiable commercial usage and label unavailable Agent 365 integrations as blocked.

**Acceptance:**

- No RAG-specific core field is required.
- Workload-specific evaluation and adapters remain outside `costgov/`.
- Any contract amendment follows the constitutional decision process.
- The second workload proves that native credits, entitlements, purchase sources, or hybrid meters can reconcile without being converted into Foundry tokens or Microsoft Copilot Credits.
- If Agent 365 is used, the portability proof also preserves identity/activity correlation without making Agent 365 the runtime, economic, quality, or billing authority.

## UI implementation rule

Do not build future-state cards or controls before the backing contracts and decision evidence exist. Extend the current five views in place:

| View | Current constitutional role | Add only after |
|---|---|---|
| Plan | Delivery-experience forecast, resolved meter stack, model/commercial subforecasts, and immutable receipt | TE-004, TE-008 for experiment and candidate forecasts |
| Govern | Authoritative Azure admission, policy explanation, and draft changes | TE-009, TE-010, TE-011 for candidate decisions and outcome state; optional external posture only after the Agent 365 follow-on gate |
| Runs | Policy-bound offline reference execution | TE-003 for deployed RAG trajectories; optional Agent 365 correlation only under TE-016 or a later approved task |
| Observe | Completed-request quality plus native-meter and allocatable-cost evidence | TE-005 through TE-007 for accepted-task economics and meter coverage; link or summarize external security/adoption evidence only after it proves decision value |
| Reconcile | Completed-task multi-meter reconciliation and forecast calibration | TE-009, TE-014, TE-015 for acceptance, distribution coverage, and split learning; Agent 365 remains corroborating evidence if later integrated |

The five-view structure is aligned and does not need replacement. The work is to strengthen the evidence flowing through it.

## Next executable action

Start TE-003 by capturing one real policy-bound Foundry/RAG trajectory through `trajectory-envelope.v1`. Emit every available model-token, retrieval/tool, and resource-meter signal without claiming that this proves the other eight delivery experiences. In parallel, design TE-004's experiment manifest and A365-01's external-evidence/correlation contracts. Do not block the RAG proof on Agent 365 licensing or preview capability: record Agent 365 integration as unavailable or blocked until a supported tenant/runtime path is verified, and never treat telemetry transport success as ingestion, accepted-task, or billing proof.
