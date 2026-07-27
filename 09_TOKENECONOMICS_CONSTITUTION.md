# TokenEconomics Constitution and Core Plan

*Canonical project intent and development guardrails. Adopted 2026-07-21.*

This document is the durable standard for deciding whether future TokenEconomics work is aligned. Research documents explain the evidence, architecture documents explain the design, and `decision.md` records implementation choices. If a proposed feature conflicts with this constitution, the feature must change or this document must be amended explicitly with evidence and rationale.

## 1. North star

TokenEconomics helps teams reduce the cost of agentic AI without silently degrading useful output. Its distinctive contribution is not another model gateway, price catalog, or observability dashboard. It is the integration of forecasting, authoritative policy, runtime enforcement, quality evaluation, bounded policy response, and learning:

> **Forecast agent-workload economics, permit cost optimization only when measured quality holds, revert bounded changes when quality regresses, and reconcile actual outcomes to improve the next forecast.**

The governing lifecycle is:

```text
predict -> compare policy -> admit -> execute -> evaluate -> respond -> reconcile -> learn
```

FutureTokenPredictor supplies feed-forward planning. TokenGov supplies feedback governance. A real workload proves whether the combined system creates useful economics in practice.

## 2. Validated intent

The project follows the thesis that survived the research and critical review:

- Agent cost is created across a stochastic task or trajectory, not only one API call.
- Cost reduction is credible only when quality is measured on the workload being optimized.
- The differentiated capability is **evaluation linked to cost enforcement**: cheaper behavior is gated by evidence and bounded reversion protects quality.
- Existing gateways, Azure services, and evaluation systems should be composed rather than unnecessarily rebuilt.
- TokenEconomics is a reusable reference architecture and research prototype, not a claim to a new product category or guaranteed savings.

Early claims that incumbents did not provide agent budgets, routing, tracing, or graceful degradation were falsified. Future work must not restore that framing. The project may demonstrate stronger integration, evidence, and operating discipline, but it must not claim invention of the individual primitives.

## 3. Constitutional principles

### 3.1 Optimize useful work, not token volume

The target unit is a successful, quality-acceptable task or trajectory. Token count, model cost per call, and completed-task cost are necessary intermediate measures, but none is the final value metric.

The intended unit economics are:

$$
U(\pi)=\frac{\mathbb{E}[C_{\text{task}}\mid\pi]}{P(A=1\mid\pi)}
$$

where $A$ is an explicitly defined acceptance outcome. A quality score must not be silently substituted for an acceptance probability.

### 3.2 Quality is a constraint, not a dashboard decoration

Policy optimization must preserve a quality floor for every material workload segment:

$$
Q_s(\pi)\ge Q_{\min}\quad\forall s
$$

Aggregate quality must not hide a failing segment. Automated responses require sufficient samples, consecutive-breach hysteresis, a bounded action ladder, and auditable recovery evidence.

### 3.3 Tail risk is distinct from a modeled percentile

The intended budget constraint is:

$$
P(C_{\text{task}}>B)\le\epsilon
$$

Modeled P50/P95/P99 values are useful forecast evidence, but P95 is not a guaranteed 5% breach bound. Chance-constraint claims require an explicit budget $B$, tolerance $\epsilon$, a probability calculation, and empirical coverage reporting against representative actuals.

### 3.4 Preserve the two-plane architecture

- The **data plane** performs latency-sensitive enforcement: routing, context controls, caching, budget actions, and telemetry emission.
- The **control plane** performs forecasting, evaluation, policy decisions, reconciliation, and FinOps analysis out of band.
- A versioned policy contract joins the planes. Control-plane logic must not add unnecessary reasoning or evaluation latency to every request.

### 3.5 Azure policy is authoritative and fail-closed

The effective TokenGov policy comes from the configured Azure authority and is bound to exact provenance. Missing or invalid policy must not silently fall back to permissive local behavior. Runtime identities read and enforce policy; separately authorized deployment identities publish it.

The Studio may create durable, validated change requests. It must not place production policy-publisher credentials in the browser or silently publish policy changes.

### 3.6 Evidence must remain immutable and traceable

Every material decision must be reproducible from versioned evidence, including:

- report, workload, golden-set, prediction, receipt, run, and trace identities;
- provider/model offering plus model-catalog and pricing revisions;
- exact policy version, content hash, source, label, and ETag;
- forecast assumptions and distribution method;
- observed cost, quality, acceptance, and reconciliation outcomes.

Historical evidence is append-only. Re-evaluation creates a new decision; it does not rewrite the old one.

### 3.7 Claims follow evidence

Measured, simulated, modeled, proposed, and blocked behavior must be labeled separately. Do not promise savings, quality preservation, calibrated confidence, or production readiness from simulated or insufficient evidence. Tool charges, model charges, infrastructure charges, and governance overhead must remain distinguishable.

### 3.8 The architecture remains reusable

TokenGov must not depend on one application's prompts, corpus, domain, evaluator, or agent framework. Workloads integrate through versioned contracts for task/trajectory identity, segments, forecasts, policy, telemetry, evaluation, acceptance, and actual usage.

Low-volume or low-risk workloads may correctly stop at basic hygiene. The full control loop is justified only when its governance and latency costs are proportionate to workload value and risk.

## 4. First reference workload: deployed agentic RAG

The deployed sample agentic RAG solution is the first reference use case for demonstrating TokenEconomics in practice. It is a connected workload and evaluation environment, **not part of TokenGov's core architecture**.

The current repository includes an initial grounded benchmark in `06_prototype/rag/` with:

- Azure AI Search retrieval over a five-book corpus;
- easy factual and hard synthesis/cross-book segments;
- repeated and paraphrased requests that exercise cache economics;
- a versioned golden set and workload;
- premium baseline, governed routing/cache, and Foundry Model Router comparison arms.

Before it can serve as the constitutional proof workload, its actual deployed agentic flow must be integrated through the same contracts used by any future workload. Integration must capture the complete task or trajectory, including retrieval, model calls, tool calls, retries, iterations, quality outcome, and cost. A single grounded model call may be used as an incremental benchmark, but it must not be represented as complete agent-trajectory governance.

### Reference-workload experiment contract

Each reproducible experiment must pin:

1. Workload, corpus/index, retrieval configuration, prompt/agent, golden-set, evaluator, model-catalog, pricing, and policy versions.
2. A baseline policy and one or more candidate policies evaluated on the same representative task set.
3. Task/trajectory and segment identifiers carried from prediction through telemetry and reconciliation.
4. End-to-end model, retrieval/tool, infrastructure allocation, evaluation, and observability costs where measurable; exclusions must be explicit.
5. Segment-level quality, sample counts, acceptance criteria, and accepted-task outcomes.
6. Expected and actual task cost, cost per accepted task, forecast error, budget breaches, latency, routing, cache, context, retry, and iteration behavior.
7. A decision stating whether the candidate is eligible, blocked, reverted, or inconclusive, with evidence and policy provenance.

The first proof should establish that at least one cheaper candidate can be evaluated fairly against the baseline, admitted only when all quality and tail-risk constraints hold, executed under the admitted revision, and reconciled back to its prediction. A quality regression must demonstrate a bounded block or reversion path. No predetermined savings percentage is required.

## 5. Product surfaces and responsibilities

### Plan

Plan describes a versioned workload, forecasts task/trajectory economics, exposes assumptions and calculation provenance, and creates an immutable receipt. It should evolve from model-invocation economics to policy-conditioned end-to-end task distributions.

### Govern

Govern explains the effective policy, evaluates immutable receipts, compares eligible policy candidates, records decision evidence, and stages authorized changes. It is a policy cockpit, not a generic settings page.

### Runs

Runs execute only an admitted policy binding and preserve workload, prediction, policy, and trace correlation across the complete trajectory.

### Observe

Observe reports cost, latency, quality, acceptance, routing, cache/context behavior, and governance overhead by workload segment and policy version. It distinguishes operational signals from decision-grade evidence.

### Reconcile

Reconcile joins forecast, policy, execution, evaluation, acceptance, and actual usage. It reports forecast error and distribution coverage, then records representative actuals for predictor learning without rewriting historical forecasts.

## 6. Definition of aligned completion

The project fulfills its intended design when the reference workload can prove this sequence end to end:

1. Plan forecasts a distribution for a versioned agentic workload under versioned policy candidates.
2. Govern compares candidates using expected cost, segment-level quality evidence, and explicit budget-breach probability.
3. Govern selects the least expected-cost eligible candidate, or reports that no candidate satisfies the constraints.
4. A separately authorized workflow publishes the reviewed policy and verifies the resulting Azure provenance.
5. The runtime enforces that exact revision over the complete task or trajectory.
6. Evaluation produces segment-level quality and explicit accepted-task outcomes with sufficient evidence.
7. A credible regression produces a bounded block or reversion through the authorized policy path.
8. Reconciliation records actual end-to-end cost, cost per accepted task, forecast error, and percentile/chance-constraint coverage.
9. FutureTokenPredictor receives the correlated actuals and uses them to improve a subsequent forecast.

Completing Studio screens, deploying Azure resources, or producing a cheaper benchmark is not sufficient by itself. The evaluation-to-enforcement and reconciliation links are the core result.

## 7. Core implementation plan

Development should proceed in this order unless evidence supports an explicit amendment:

1. **Reference-workload adapter:** define a framework-neutral task/trajectory envelope and integrate the deployed agentic RAG solution without coupling TokenGov to its domain.
2. **Accepted-task contract:** define segment-specific acceptance criteria and persist acceptance independently from raw evaluator scores.
3. **End-to-end economics:** attribute model, retrieval/tool, evaluation, observability, and allocatable infrastructure cost to each task/trajectory.
4. **Policy-conditioned forecasting:** forecast comparable candidate policies, including routing, cache, context, retry, and iteration assumptions.
5. **Decision-grade constraints:** add segment sample/confidence evidence and explicit $B$, $\epsilon$, and budget-breach probability.
6. **Govern comparison and outcomes:** surface candidate comparison, eligibility, hysteresis state, policy-version outcomes, and historical simulation.
7. **Authorized closed loop:** connect evaluation decisions to reviewed policy publication and bounded runtime reversion while preserving identity separation.
8. **Calibration proof:** reconcile accepted-task economics and empirical distribution coverage into FutureTokenPredictor, then demonstrate an improved subsequent forecast.
9. **Second workload portability test:** integrate a materially different workload through the same contracts to verify that the architecture is reusable.

## 8. Development alignment gate

Every material feature, schema, integration, or infrastructure change must answer these questions in its design note, issue, or change request:

1. Which lifecycle step does this advance?
2. Does it operate on a model call or the complete task/trajectory, and is that scope explicit?
3. What versioned evidence does it produce or consume?
4. How does it protect segment-level quality?
5. How does it affect expected cost, tail risk, or cost per accepted task?
6. Does it preserve two-plane separation, fail-closed authority, least privilege, and immutable history?
7. Is the behavior measured, modeled, simulated, proposed, or production-validated?
8. Does it keep workload-specific logic outside the reusable TokenGov core?

A change that cannot answer at least one of questions 1, 3, 4, or 5 is likely outside the core plan. A change that weakens questions 6 through 8 requires an explicit constitutional amendment.

## 9. Non-goals

Unless separately justified, TokenEconomics will not:

- build another general-purpose model gateway or observability platform;
- claim that caching, routing, or cheaper models inherently preserve quality;
- optimize only provider price while ignoring task success and trajectory behavior;
- treat modeled percentiles as calibrated guarantees;
- let a browser or runtime identity publish production policy;
- hard-code the architecture to the sample RAG solution;
- require the full governance stack for workloads whose economics do not justify it;
- claim guaranteed savings, guaranteed quality, or unique ownership of existing primitives.

## 10. Amendment and decision policy

This constitution may evolve when workload evidence, research, Azure capabilities, or implementation experience falsifies an assumption. Amendments must:

1. state the existing principle being changed;
2. provide evidence and the reason for the change;
3. describe effects on contracts, security, historical interpretation, and claims;
4. update `decision.md` with the dated decision;
5. preserve old experiment and policy evidence without reinterpretation.

Implementation convenience alone is not sufficient reason to weaken quality gates, provenance, authority separation, or evidence integrity.

## 11. Decision hierarchy

When documents disagree, use this order:

1. This constitution and its explicit amendments.
2. Dated decisions in `decision.md`.
3. The reusable architecture and Azure-first framework.
4. The critical review and corrected one-pager.
5. Prototype behavior and older handoff notes.

Prototype behavior demonstrates progress; it does not redefine project intent.