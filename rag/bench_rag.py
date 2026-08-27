"""
rag/bench_rag.py — the grounded live benchmark.

Same 3-arm comparison as `demo.py --live`, but every model call is grounded on real
passages retrieved from Azure AI Search over the 5-book corpus. Now the quality axis is
meaningful: with identical retrieved context, the cheap model (gpt-5-nano) drops detail on
HARD synthesis questions while premium (gpt-5) holds — so the eval gate has something real
to gate on.

Reuses the core gateway/telemetry/finops unchanged; only injects a retrieval
`context_provider` into the models (the generic hook added to providers.RealModel).

Run from the repository root: `.venv/Scripts/python.exe rag/bench_rag.py`
"""

from __future__ import annotations
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

import demo  # reuse run_workload, _live_quality, _write_dashboard_data, hr, _make_embed_fn
from costgov.evaluator import Evaluator
from costgov import finops
from costgov import azure_integrations as ai
from costgov.providers import (build_client, build_real_models, build_router_model, RealJudge)

from retrieval import Retriever


def rag_stream(cap, repeats):
    wl = json.load(open(os.path.join(HERE, "workload.rag.json"), encoding="utf-8"))
    stream = []
    for r in wl["requests"]:
        variants = [r["question"]] + r.get("paraphrases", [])
        for i in range(min(repeats, r["repeats"])):
            stream.append((r["tenant"], variants[i % len(variants)], r["difficulty"]))
    return stream[:cap]


def main():
    for k in ("AZURE_OPENAI_ENDPOINT", "AZURE_SEARCH_ENDPOINT",
              "AZURE_DEPLOYMENT_CHEAP", "AZURE_DEPLOYMENT_PREMIUM", "AZURE_DEPLOYMENT_ROUTER"):
        if not os.environ.get(k):
            print(f"MISSING ENV: {k}. Fill .env (see README in rag/).")
            sys.exit(2)

    print("Authenticating (Entra ID) + wiring RAG retrieval...")
    ai.init_app_insights()
    client = build_client()
    real_models = build_real_models(client)
    router = build_router_model(client)
    judge = RealJudge(client, os.environ.get("AZURE_DEPLOYMENT_JUDGE",
                                             os.environ["AZURE_DEPLOYMENT_PREMIUM"]))
    retriever = Retriever(client, top_k=int(os.environ.get("RAG_TOP_K", "4")))

    # Ground every model on retrieved passages (the context-management stage).
    for m in real_models.values():
        m.context_provider = retriever.context
    router.context_provider = retriever.context

    embed_fn = demo._make_embed_fn(client, os.environ["AZURE_DEPLOYMENT_EMBEDDING"]) \
        if os.environ.get("AZURE_DEPLOYMENT_EMBEDDING") else None

    evaluator = Evaluator(os.path.join(HERE, "golden_set.rag.json"))
    cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    cfg["evaluation"]["sample_rate"] = float(
        os.environ.get("RAG_EVALUATION_SAMPLE_RATE", "1.0")
    )
    cap = int(os.environ.get("LIVE_MAX_REQUESTS", "20"))
    repeats = int(os.environ.get("LIVE_WORKLOAD_REPEATS", "3"))
    stream = rag_stream(cap, repeats)
    print(f"RAG workload: {len(stream)} grounded requests (cap {cap}).")

    # --- Baseline: premium-all, no cache ---
    demo.hr("RAG ACT 1 - BASELINE (premium-all, grounded)")
    b_cfg = copy.deepcopy(cfg); b_cfg["routing"]["mode"] = "quality"
    b_cfg["semantic_cache"]["enabled"] = False; b_cfg["context"]["prune"] = False
    b_tel, _ = demo.run_workload(b_cfg, stream, models=real_models, embed_fn=None)
    baseline = b_tel.total_cost()
    bq_mean, bq_by, _ = demo._live_quality(judge, evaluator, b_tel)
    print(f"  cost ${baseline:.4f} | avg latency {b_tel.avg_latency()} ms | quality {bq_mean} {bq_by}")

    # --- Arm 1: OUR gateway (balanced routing + cache) ---
    demo.hr("RAG ACT 2 - ARM 1: our governance (route cheap/premium + cache, grounded)")
    g_cfg = copy.deepcopy(cfg); g_cfg["routing"]["mode"] = "balanced"
    g_tel, g_gw = demo.run_workload(g_cfg, stream, models=real_models, embed_fn=embed_fn)
    g_cost = g_tel.total_cost()
    gq_mean, gq_by, gq_n = demo._live_quality(judge, evaluator, g_tel)
    sav = finops.savings_vs_baseline(g_cost, baseline)
    print(f"  cost ${g_cost:.4f} | SAVINGS {sav['saved_pct']}% | cache {g_gw.cache.hit_rate*100:.0f}% "
          f"| avg latency {g_tel.avg_latency()} ms")
    print(f"  judged quality {gq_mean} by difficulty {gq_by} (n={gq_n})")

    # --- Arm 2: Foundry Model Router ---
    demo.hr("RAG ACT 3 - ARM 2: Foundry Model Router (Azure routes, grounded)")
    r_cfg = copy.deepcopy(cfg); r_cfg["routing"]["mode"] = "quality"
    r_tel, _ = demo.run_workload(r_cfg, stream, models={"premium": router, "cheap": router},
                                 embed_fn=None)
    r_cost = r_tel.total_cost()
    rq_mean, rq_by, rq_n = demo._live_quality(judge, evaluator, r_tel)
    rsav = finops.savings_vs_baseline(r_cost, baseline)
    print(f"  cost ${r_cost:.4f} | SAVINGS {rsav['saved_pct']}% | avg latency {r_tel.avg_latency()} ms")
    print(f"  judged quality {rq_mean} by difficulty {rq_by} (n={rq_n})")

    # --- Verdict ---
    demo.hr("RAG BENCHMARK - our governance vs Foundry Model Router (vs premium baseline)")
    print(f"  {'arm':28s}{'cost':>10s}{'savings':>10s}{'quality':>10s}")
    print(f"  {'premium baseline':28s}{baseline:>10.4f}{'0%':>10s}{bq_mean:>10.3f}")
    print(f"  {'our governance (arm 1)':28s}{g_cost:>10.4f}{str(sav['saved_pct'])+'%':>10s}{gq_mean:>10.3f}")
    print(f"  {'model router (arm 2)':28s}{r_cost:>10.4f}{str(rsav['saved_pct'])+'%':>10s}{rq_mean:>10.3f}")
    print("\n  Grounded on real retrieved passages -> the quality column is now a real signal.")

    g_tel.dump_jsonl(os.path.join(HERE, "telemetry_rag.jsonl"))
    demo._write_dashboard_data({
        "mode": "live",
        "title": "Cost-Governance Prototype - Live RAG Benchmark (5-book corpus)",
        "kpis": {
            "baseline_cost": round(baseline, 4), "governed_cost": round(g_cost, 4),
            "saved_usd": sav["saved_usd"], "saved_pct": sav["saved_pct"],
            "cache_hit_rate": round(g_gw.cache.hit_rate * 100, 1),
            "avg_latency_ms": g_tel.avg_latency(), "quality": gq_mean,
            "requests": len(g_tel.records),
        },
        "scenarios": [
            {"name": "Baseline (premium all)", "cost": round(baseline, 4), "quality": bq_mean},
            {"name": "Our governance", "cost": round(g_cost, 4), "quality": gq_mean},
            {"name": "Foundry Model Router", "cost": round(r_cost, 4), "quality": rq_mean},
        ],
        "tenants": [{"tenant": k, **v} for k, v in finops.attribution(g_tel).items()],
        "models": finops.model_breakdown(g_tel),
        "quality_by_difficulty": gq_by, "closed_loop": {}, "changelog": [],
    })


if __name__ == "__main__":
    main()
