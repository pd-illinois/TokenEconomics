# Studio Plan visible-input test results

**Execution date:** 2026-07-24  
**Lifecycle step:** `predict`  
**Status:** Measured local development evidence; not production validation or release approval  
**Studio URL:** `http://127.0.0.1:8765/?scoutTheme=light`  
**Evidence report:** `RPT-20260724-CB94001B` — Agentic RAG Plan UI matrix — current runtime

## Purpose and scope

These tests verify that materially different workload statements entered into Studio's visible **Describe the solution you want to estimate** box reach the Plan API, the local FutureTokenPredictor stdio MCP tool, the appropriate versioned archetype, and an immutable Plan receipt.

The tests cover modeled workload-invocation economics only. They do not measure a complete Foundry task trajectory, accepted-task quality, Azure infrastructure cost, production capacity, calibrated tail-risk probability, or savings.

## Fixed test controls

| Control | Value |
|---|---|
| Provider/model selection | `azure_openai:gpt-4.1` |
| Users | 1,000 |
| Calls per user per day | 10 |
| Daily modeled invocations | 10,000 |
| Pricing state | Verified by predictor output |
| Browser theme | Fluent light |
| Submission path | Visible textarea → Studio click handler → `POST /api/plan` → local stdio MCP `predict_token_usage` → immutable receipt |

## Input statements

### UI-RAG-01 — single retrieval

> Predict the tokens and AI cost for running a RAG solution for 1,000 users using Microsoft Foundry and Azure AI Search. Each request performs one retrieval and returns one concise grounded answer with citations.

**Expected classification:** `rag_pipeline` / `RAG_Pipeline`.

### UI-RAG-02 — ReAct tool loop

> Predict the tokens and AI cost for running a RAG solution for 1,000 users using Microsoft Foundry and Azure AI Search. An autonomous ReAct agent iterates through multiple retrieval and tool calls, evaluates evidence, and continues until it can return a cited answer.

**Expected classification:** `react_agent` / `ReAct_Agent`.

### UI-RAG-03 — deterministic workflow

> Predict the tokens and AI cost for running a RAG solution for 1,000 users using Microsoft Foundry and Azure AI Search. A deterministic multi-step workflow retrieves evidence, validates relevance, synthesizes the answer, and performs a final review before responding.

**Expected classification:** `workflow` / `Workflow`.

### UI-RAG-04 — four-agent system

> Predict the tokens and AI cost for running a RAG solution for 1,000 users using Microsoft Foundry and Azure AI Search. A 4 agent multi-agent team uses a researcher to retrieve, an analyst to synthesize, a writer to draft, and a reviewer to validate the final cited answer.

**Expected classification:** `multi_agent` / `MultiAgent`, with agent count 4.

## Corrected current-runtime results

All four visible-form submissions returned HTTP 201 and Plan status `complete`.

| Case | Pattern / archetype | Bounds | P5 / mean / P95 tokens per invocation | Model cost / invocation | Tool cost / invocation | Monthly model cost | Result |
|---|---|---|---:|---:|---:|---:|---|
| UI-RAG-01 | `rag_pipeline` / `RAG_Pipeline` | Heuristic, 1 sample | 3,472 / 4,960 / 7,440 | $0.017120 | $0.005000 | $4,917.30 | Pass |
| UI-RAG-02 | `react_agent` / `ReAct_Agent` | Monte Carlo, 1,000 samples | 8,074 / 23,176 / 42,395 | $0.085936 | $0.015000 | $23,225.93 | Pass |
| UI-RAG-03 | `workflow` / `Workflow` | Monte Carlo, 1,000 samples | 4,794 / 12,443 / 21,162 | $0.048607 | $0.012500 | $13,501.42 | Pass |
| UI-RAG-04 | `multi_agent` / `MultiAgent` | Monte Carlo, 1,000 samples | 143,664 / 210,741 / 282,329 | $0.921117 | $0.007500 | $253,570.58 | Pass |

The scenario ordering is explainable rather than assumed: the single RAG call is smallest; the deterministic workflow repeats bounded ordered steps; the ReAct pattern models variable iterative tool loops; and the explicit four-agent topology models repeated turns plus context-sharing overhead. The output is an archetype-based estimate, not a measurement of a deployed workload.

## Immutable evidence identities

| Case | Prediction ID | Plan ID | Receipt ID | Receipt SHA-256 |
|---|---:|---|---|---|
| UI-RAG-01 | 1091 | `5b86945c-8d60-4f06-88b3-4e58bbf52b2b` | `plan_1220d5c974be4756c14c` | `1220d5c974be4756c14c9102b54025b1b0241c8efac335922429fe795ff09200` |
| UI-RAG-02 | 1092 | `e2ac54e3-f709-444d-9466-4e1bf23eef85` | `plan_a09bf8a5cf48d91b9286` | `a09bf8a5cf48d91b9286370fabe3487f3c69626403f590f2e841424f7da33b4b` |
| UI-RAG-03 | 1093 | `5f06face-c7ae-4bd7-9cc6-45c4b0f4e746` | `plan_255f9953b1af5d5c60b7` | `255f9953b1af5d5c60b70b920fd05df874a528836d74e70589b2766849ef1585` |
| UI-RAG-04 | 1094 | `b3cf1dd6-762d-4121-bde8-282e0f05c27a` | `plan_984122028807dc057b76` | `984122028807dc057b76687d2957829fc29c1b270c7363d276b20b855f887d33` |

## Calculation checks

The corresponding automated contract tests verify for every pattern:

- displayed token total equals the sum of modality components, within serialization rounding;
- model cost equals the sum of token-component costs using catalog rates;
- daily calls equal users × calls per user per day;
- monthly and annual model costs use 30 and 365 days respectively;
- tool charges are explicitly excluded from model-cost figures;
- P5 ≤ P50 ≤ P95;
- the modeled mean lies within the reported low/high range for Monte Carlo outputs;
- explicit multi-agent count reaches workflow simulation.

Post-change regression evidence is 34 passing Studio tests and 373 passing FutureTokenPredictor tests through its required `scripts/run_tests.py` runner.

After all submissions, Studio was reloaded, evidence report `RPT-20260724-CB94001B` was reopened through the report chooser, and all four Plan IDs were present. Opening UI-RAG-01 restored receipt `plan_1220d5c974be4756c14c`, its displayed calculation formulas, prediction ID 1091, and the matching receipt-hash prefix. This is measured restart/reopen persistence evidence for this run.

## Defect detected by the visible-input test

The first browser run used a Studio process that had been started before the agent-pattern repair. Although the source and direct MCP tests were current, that in-memory server classified UI-RAG-02, UI-RAG-03, and UI-RAG-04 as `rag_pipeline` and produced the same 4,960-token result for all three. Those receipts are retained in report `RPT-20260724-AE60AE39` as invalid pre-restart evidence and are not the accepted results above.

The stale server was stopped, Studio was restarted from current source, and all four cases were rerun in the clean evidence report. The corrected run produced the expected distinct patterns, archetypes, bounds, costs, and immutable receipts.

## Browser-interaction observation

The visible textarea was populated for every case and the normal Studio click handler generated each `POST /api/plan` request. The browser automation's coordinate-based pointer click reported interception by an ancestor/root element, so the same button's DOM `click()` activation was used. Submission logic, HTTP traffic, response rendering, and receipt persistence were real; however, ordinary pointer activation remains an explicit UI test item rather than a passed claim. Keyboard and pointer activation should be covered by the TE-001.5 browser suite before release.

## Release interpretation

This evidence advances TE-001.5 forecast correctness and visible-form integration. It does not close Plan-only dependency isolation, extreme-input handling, stress/concurrency, accessibility, pointer/keyboard interaction, release allowlisting, dependency/secret/license review, or clean Git provenance. Publication remains blocked until those gates pass.
