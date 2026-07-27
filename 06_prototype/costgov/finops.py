"""
FinOps (control plane).

Cost attribution + budget status, the stand-in for Azure Cost Management. Turns raw
telemetry into the per-tenant chargeback view and the savings-vs-baseline number that
the elevator pitch and file 06 promise.
"""

from __future__ import annotations
from collections import defaultdict


def attribution(telemetry) -> dict:
    per_tenant = defaultdict(lambda: {"cost": 0.0, "requests": 0, "cache_hits": 0, "degraded": 0})
    for r in telemetry.records:
        t = per_tenant[r.tenant]
        t["cost"] += r.cost_usd
        t["requests"] += 1
        t["cache_hits"] += int(r.cache_hit)
        t["degraded"] += int(r.degraded)
    return {k: {**v, "cost": round(v["cost"], 4)} for k, v in per_tenant.items()}


def savings_vs_baseline(current_total: float, baseline_total: float) -> dict:
    saved = baseline_total - current_total
    pct = (saved / baseline_total * 100.0) if baseline_total else 0.0
    return {
        "baseline_usd": round(baseline_total, 4),
        "current_usd": round(current_total, 4),
        "saved_usd": round(saved, 4),
        "saved_pct": round(pct, 1),
    }


def model_breakdown(telemetry) -> list:
    """Cost + request count grouped by the model that served each request
    (cheap / premium / cache / none)."""
    agg = defaultdict(lambda: {"cost": 0.0, "requests": 0})
    for r in telemetry.records:
        a = agg[r.model]
        a["cost"] += r.cost_usd
        a["requests"] += 1
    return [{"model": k, "cost": round(v["cost"], 6), "requests": v["requests"]}
            for k, v in sorted(agg.items())]

