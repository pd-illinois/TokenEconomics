"""costgov — a runnable reference prototype of the reusable cost-governance
architecture (see ../06_reusable-cost-governance-architecture.md).

Two planes:
  data plane   : models, cache, context, gateway
  control plane: telemetry, config_store, evaluator, decision, finops
joined by the config store and an eval-gated feedback loop.
"""
