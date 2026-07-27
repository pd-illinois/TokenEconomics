# TokenEconomics development instructions

Before planning or making a material architecture, schema, integration, policy, evaluation, infrastructure, or product-surface change, read [`09_TOKENECONOMICS_CONSTITUTION.md`](../09_TOKENECONOMICS_CONSTITUTION.md) and the latest relevant entries in [`decision.md`](../decision.md).

Use the constitution as the canonical project intent. Prototype behavior and older handoff notes do not override it.

For material changes:

- State which lifecycle step the change advances: `predict -> compare policy -> admit -> execute -> evaluate -> respond -> reconcile -> learn`.
- Identify the versioned evidence the change consumes and produces.
- Distinguish model-call behavior from complete task/trajectory behavior.
- Preserve segment-level quality controls, explicit acceptance outcomes, and the distinction between modeled percentiles and calibrated tail-risk claims.
- Preserve two-plane separation, fail-closed Azure policy authority, least-privilege policy publication, and immutable historical evidence.
- Keep sample agentic RAG and other workload-specific logic outside reusable TokenGov core; integrate workloads through versioned contracts.
- Label measured, modeled, simulated, proposed, blocked, and production-validated behavior accurately.
- Do not claim guaranteed savings or quality, and do not frame existing gateway, budget, routing, tracing, caching, or evaluation primitives as novel.

Before considering a material change complete, apply the constitution's development alignment gate and report any remaining gap against the end-to-end completion definition.

The nested `06_prototype/FutureTokenPredictor` repository has its own contributor instructions. Follow those instructions for changes inside that repository in addition to this project's integration contract.