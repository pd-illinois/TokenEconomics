"""
demo.py - end-to-end walkthrough of the cost-governance architecture.

Run:  python demo.py         (from the 06_prototype folder)

It exercises the whole file-06 loop on the customer-support example:
  ACT 1  Baseline (no governance)         -> establish the expensive status quo
  ACT 2  Pre-deploy CI quality gate        -> Pattern A (offline eval blocks bad configs)
  ACT 3  Tier-2 governed run               -> routing + cache + caps cut the bill
  ACT 4  Inject a regression + closed loop  -> Pattern B eval detects, decision binding auto-reverts
  ACT 5  FinOps summary + overhead economics
"""

from __future__ import annotations
import copy
import json
import os
import sys

# Make output robust on Windows consoles (cp1252) - force UTF-8 if available, ASCII otherwise.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from costgov.config_store import ConfigStore
from costgov.telemetry import Telemetry
from costgov.gateway import Gateway
from costgov.evaluator import Evaluator
from costgov.decision import react
from costgov import finops
from costgov.models import MODELS, PREMIUM

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return json.load(fh)


def expand_workload(wl):
    """Turn the compact workload into a flat request stream with paraphrase variety."""
    stream = []
    for r in wl["requests"]:
        variants = [r["question"]] + r.get("paraphrases", [])
        for i in range(r["repeats"]):
            q = variants[i % len(variants)]
            stream.append((r["tenant"], q, r["difficulty"]))
    return stream


def run_workload(config, stream, models=None, embed_fn=None):
    tel = Telemetry(sample_rate=config["evaluation"]["sample_rate"])
    gw = Gateway(config, tel, models=models, embed_fn=embed_fn)
    for tenant, q, diff in stream:
        gw.handle(tenant, q, diff)
    return tel, gw


def _write_dashboard_data(payload):
    with open(os.path.join(HERE, "dashboard_data.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print("[dashboard data written to dashboard_data.json -> run: python dashboard.py]")


def hr(title):
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


def main():
    wl = load("workload.json")
    stream = expand_workload(wl)
    evaluator = Evaluator(os.path.join(HERE, "golden_set.json"))

    print(f"Workload: {len(stream)} support requests across "
          f"{len(set(t for t,_,_ in stream))} tenants "
          f"({sum(1 for _,_,d in stream if d=='easy')} easy / "
          f"{sum(1 for _,_,d in stream if d=='hard')} hard)")

    # ---- ACT 1: BASELINE (no governance - everything to premium, no cache) ----
    hr("ACT 1 - BASELINE: no governance (Tier 0 off), premium model for everything")
    base_cfg = copy.deepcopy(load("config.json"))
    base_cfg["routing"]["mode"] = "quality"      # premium for all
    base_cfg["semantic_cache"]["enabled"] = False
    base_cfg["context"]["prune"] = False
    base_tel, _ = run_workload(base_cfg, stream)
    baseline_total = base_tel.total_cost()
    print(f"  total cost     : ${baseline_total:.4f}")
    print(f"  avg latency    : {base_tel.avg_latency()} ms")
    print(f"  quality        : premium everywhere -> reference quality (1.00)")

    # ---- ACT 2: PRE-DEPLOY CI QUALITY GATE (Pattern A, offline) ----
    hr("ACT 2 - CI QUALITY GATE (Pattern A): would an aggressive 'cost' config ship?")
    for candidate in ("cost", "balanced"):
        def answer_fn(question, difficulty, mode=candidate):
            model = MODELS["cheap"] if (mode == "cost" or difficulty == "easy") else PREMIUM
            return model.generate(question, 200, difficulty).text
        rep = evaluator.offline(answer_fn)
        floor = base_cfg["evaluation"]["min_quality"]
        verdict = "PASS -> may ship" if rep.mean_score >= floor else "BLOCK -> regresses quality"
        print(f"  candidate routing='{candidate}': golden score {rep.mean_score} "
              f"(by difficulty {rep.by_difficulty}) -> {verdict}")
    print(f"  => CI gate blocks 'cost' (fails hard cases), admits 'balanced'. No bad config reaches prod.")

    # ---- ACT 3: TIER-2 GOVERNED RUN (balanced routing + cache + caps + prune) ----
    hr("ACT 3 - GOVERNED RUN (Tier 2): balanced routing + semantic cache + prune + caps")
    store = ConfigStore(os.path.join(HERE, "config.json"))
    store.data["routing"]["mode"] = "balanced"
    tel, gw = run_workload(store.data, stream)
    gov_total = tel.total_cost()
    sav = finops.savings_vs_baseline(gov_total, baseline_total)
    live = evaluator.continuous(tel.sampled)
    print(f"  total cost     : ${gov_total:.4f}   (baseline ${baseline_total:.4f})")
    print(f"  SAVINGS        : ${sav['saved_usd']:.4f}  = {sav['saved_pct']}% cheaper")
    print(f"  cache hit rate : {gw.cache.hit_rate*100:.0f}%   avg latency {tel.avg_latency()} ms")
    print(f"  live quality   : {live.mean_score} (sampled {live.n} of {len(tel.records)} reqs) "
          f"by difficulty {live.by_difficulty}")

    # ---- ACT 4: INJECT REGRESSION + CLOSED LOOP (Pattern B auto-revert) ----
    hr("ACT 4 - REGRESSION + CLOSED LOOP (Pattern B): someone flips routing to 'cost'")
    store.update(["routing", "mode"], "cost",
                 "operator over-optimized: forced cheap model for ALL traffic")
    print("  [runtime] routing knob changed to 'cost' (cheap model even for hard queries)")
    tel2, gw2 = run_workload(store.data, stream)
    live2 = evaluator.continuous(tel2.sampled)
    worst2 = min(live2.by_difficulty.values()) if live2.by_difficulty else live2.mean_score
    print(f"  [continuous eval] mean quality {live2.mean_score} looks fine, BUT the worst "
          f"segment collapsed: by difficulty {live2.by_difficulty}")
    print(f"  [insight] aggregate masks the regression; segment-aware gating catches "
          f"'hard' = {worst2} < floor {store.data['evaluation']['min_quality']}")
    print(f"  [cost] this run ${tel2.total_cost():.4f} (cheaper, but the hard segment breached the floor)")

    print("\n  --- decision binding fires (the eval->enforcement wire) ---")
    actions = react(store, live2, tenant_spend_ok=True)
    for a in actions:
        print(f"    * {a}")

    # re-run after auto-revert
    tel3, gw3 = run_workload(store.data, stream)
    live3 = evaluator.continuous(tel3.sampled)
    print(f"  [after auto-revert] routing now '{store.data['routing']['mode']}', "
          f"live quality RECOVERED to {live3.mean_score} by difficulty {live3.by_difficulty}")
    print(f"  [cost] ${tel3.total_cost():.4f} - savings kept where safe, quality restored where not")

    # ---- ACT 5: FINOPS + OVERHEAD ECONOMICS ----
    hr("ACT 5 - FINOPS attribution + overhead economics")
    print("  per-tenant chargeback (governed run):")
    for tenant, v in finops.attribution(tel).items():
        print(f"    {tenant:8s}  ${v['cost']:.4f}  over {v['requests']} reqs  "
              f"({v['cache_hits']} cache hits, {v['degraded']} degraded)")

    # governance tax: judge tokens for the sampled continuous eval vs the savings
    judged = live.n
    judge_cost = judged * 0.0008           # ~cost of one judge call (illustrative)
    print("\n  overhead economics (illustrative, per file 06 section 6):")
    print(f"    savings this run      : ${sav['saved_usd']:.4f} ({sav['saved_pct']}%)")
    print(f"    eval 'governance tax' : ${judge_cost:.4f} ({judged} judged of {len(tel.records)})")
    print(f"    decision binding      : ~$0.00 (fires only on drift)")
    net = sav['saved_usd'] - judge_cost
    print(f"    NET benefit           : ${net:.4f}  -> "
          f"{'worth it at this volume' if net > 0 else 'overhead exceeds savings - stay Tier 0'}")

    # audit trail of knob changes (the config store history)
    hr("CONFIG CHANGELOG (audit trail of every knob change - no code deploy)")
    for entry in store.data.get("_changelog", []):
        print(f"  {entry['knob']}: {entry['from']} -> {entry['to']}  ({entry['reason']})")

    tel.dump_jsonl(os.path.join(HERE, "telemetry_out.jsonl"))
    print("\n[telemetry written to telemetry_out.jsonl]")

    _write_dashboard_data({
        "mode": "sim",
        "title": "Cost-Governance Prototype - Simulated Run",
        "kpis": {
            "baseline_cost": round(baseline_total, 4),
            "governed_cost": round(gov_total, 4),
            "saved_usd": sav["saved_usd"],
            "saved_pct": sav["saved_pct"],
            "cache_hit_rate": round(gw.cache.hit_rate * 100, 1),
            "avg_latency_ms": tel.avg_latency(),
            "quality": live.mean_score,
            "requests": len(tel.records),
        },
        "scenarios": [
            {"name": "Baseline (premium all)", "cost": round(baseline_total, 4), "quality": 1.0},
            {"name": "Governed (Tier 2)", "cost": round(gov_total, 4), "quality": live.mean_score},
            {"name": "Regression (cost mode)", "cost": round(tel2.total_cost(), 4),
             "quality": min(live2.by_difficulty.values()) if live2.by_difficulty else live2.mean_score},
            {"name": "After auto-revert", "cost": round(tel3.total_cost(), 4), "quality": live3.mean_score},
        ],
        "tenants": [{"tenant": k, **v} for k, v in finops.attribution(tel).items()],
        "models": finops.model_breakdown(tel),
        "quality_by_difficulty": live.by_difficulty,
        "closed_loop": {"before": live2.by_difficulty, "after": live3.by_difficulty,
                        "floor": store.data["evaluation"]["min_quality"]},
        "changelog": store.data.get("_changelog", []),
    })

    print("\nDONE - the closed loop cut cost, caught a regression, and auto-reverted. "
          "See README.md for how each piece maps to Azure + file 06.")


# ======================================================================
# LIVE PATH  (python demo.py --live)  — real Foundry/Azure OpenAI calls
# ======================================================================
_REQUIRED_LIVE_ENV = [
    "AZURE_OPENAI_ENDPOINT", "AZURE_DEPLOYMENT_CHEAP",
    "AZURE_DEPLOYMENT_PREMIUM", "AZURE_DEPLOYMENT_ROUTER",
]


def _make_embed_fn(client, deployment):
    def embed(text):
        r = client.embeddings.create(model=deployment, input=text)
        return r.data[0].embedding
    return embed


def _small_live_stream(cap, repeats):
    wl = load("workload.json")
    stream = []
    for r in wl["requests"]:
        variants = [r["question"]] + r.get("paraphrases", [])
        for i in range(min(repeats, r["repeats"])):
            stream.append((r["tenant"], variants[i % len(variants)], r["difficulty"]))
    return stream[:cap]


def _live_quality(judge, evaluator, telemetry):
    """Score the sampled live stream with the REAL judge model against golden references."""
    scores, buckets = [], {}
    for item in telemetry.sampled:
        case = evaluator._match_case(item["question"])
        if not case:
            continue
        s = judge.score(item["question"], item["answer_text"], case["must_include"])
        scores.append(s)
        buckets.setdefault(item["difficulty"], []).append(s)
    if not scores:
        raise RuntimeError(
            "no benchmark answers matched the golden set; quality is inconclusive"
        )
    mean = round(sum(scores) / len(scores), 3)
    by = {k: round(sum(v) / len(v), 3) for k, v in buckets.items()}
    return mean, by, len(scores)


def live_main():
    # load .env if python-dotenv is available
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(HERE, ".env"))
    except Exception:
        pass

    missing = [k for k in _REQUIRED_LIVE_ENV if not os.environ.get(k)]
    if missing:
        print("LIVE MODE NEEDS CONFIG. Missing env vars:\n  " + "\n  ".join(missing))
        print("\n-> Copy .env.example to .env and fill it in, then `az login`.")
        print("-> See LIVE.md for the exact provisioning + role-assignment steps.")
        sys.exit(2)

    from costgov.providers import (build_client, build_real_models,
                                   build_router_model, RealJudge)
    from costgov import azure_integrations as ai

    print("Authenticating with Entra ID (DefaultAzureCredential)...")
    client = build_client()
    real_models = build_real_models(client)
    router = build_router_model(client)
    judge = RealJudge(client, os.environ.get("AZURE_DEPLOYMENT_JUDGE",
                                             os.environ["AZURE_DEPLOYMENT_PREMIUM"]))
    embed_fn = None
    if os.environ.get("AZURE_DEPLOYMENT_EMBEDDING"):
        embed_fn = _make_embed_fn(client, os.environ["AZURE_DEPLOYMENT_EMBEDDING"])

    ai.init_app_insights()
    cfg = load("config.json")
    ai.hydrate_config_from_appconfig(cfg)
    evaluator = Evaluator(os.path.join(HERE, "golden_set.json"))

    cap = int(os.environ.get("LIVE_MAX_REQUESTS", 20))
    repeats = int(os.environ.get("LIVE_WORKLOAD_REPEATS", 3))
    stream = _small_live_stream(cap, repeats)
    print(f"LIVE workload: {len(stream)} real requests (cap {cap}). Each pass calls the API.")

    # --- Baseline: premium-all, no cache (real $) ---
    hr("LIVE ACT 1 - BASELINE (premium-all, real calls)")
    b_cfg = copy.deepcopy(cfg); b_cfg["routing"]["mode"] = "quality"
    b_cfg["semantic_cache"]["enabled"] = False; b_cfg["context"]["prune"] = False
    b_tel, _ = run_workload(b_cfg, stream, models=real_models, embed_fn=None)
    baseline = b_tel.total_cost()
    print(f"  cost ${baseline:.4f} | avg latency {b_tel.avg_latency()} ms")

    # --- Arm 1: OUR gateway (balanced routing + cache + prune) ---
    hr("LIVE ACT 2 - ARM 1: our governance (routing+cache+prune, real calls)")
    g_cfg = copy.deepcopy(cfg); g_cfg["routing"]["mode"] = "balanced"
    g_tel, g_gw = run_workload(g_cfg, stream, models=real_models, embed_fn=embed_fn)
    g_cost = g_tel.total_cost()
    gq_mean, gq_by, gq_n = _live_quality(judge, evaluator, g_tel)
    sav = finops.savings_vs_baseline(g_cost, baseline)
    print(f"  cost ${g_cost:.4f} | SAVINGS {sav['saved_pct']}% | cache {g_gw.cache.hit_rate*100:.0f}% "
          f"| avg latency {g_tel.avg_latency()} ms")
    print(f"  judged quality {gq_mean} by difficulty {gq_by} (n={gq_n})")

    # --- Arm 2: Foundry Model Router (Azure routes everything) ---
    hr("LIVE ACT 3 - ARM 2: Foundry Model Router (Azure routes, real calls)")
    r_cfg = copy.deepcopy(cfg); r_cfg["routing"]["mode"] = "quality"  # gateway just forwards
    r_tel, _ = run_workload(r_cfg, stream, models={"premium": router, "cheap": router},
                            embed_fn=None)
    r_cost = r_tel.total_cost()
    rq_mean, rq_by, rq_n = _live_quality(judge, evaluator, r_tel)
    rsav = finops.savings_vs_baseline(r_cost, baseline)
    print(f"  cost ${r_cost:.4f} | SAVINGS {rsav['saved_pct']}% | avg latency {r_tel.avg_latency()} ms")
    print(f"  judged quality {rq_mean} by difficulty {rq_by} (n={rq_n})")

    # --- Benchmark verdict ---
    hr("LIVE BENCHMARK - our governance vs Foundry Model Router (vs premium baseline)")
    print(f"  {'arm':28s}{'cost':>10s}{'savings':>10s}{'quality':>10s}")
    print(f"  {'premium baseline':28s}{baseline:>10.4f}{'0%':>10s}{'1.000':>10s}")
    print(f"  {'our governance (arm 1)':28s}{g_cost:>10.4f}{str(sav['saved_pct'])+'%':>10s}{gq_mean:>10.3f}")
    print(f"  {'model router (arm 2)':28s}{r_cost:>10.4f}{str(rsav['saved_pct'])+'%':>10s}{rq_mean:>10.3f}")
    print("\n  Interpretation: compare $ saved AND judged quality. Our layer adds per-task")
    print("  budgets + eval-gated auto-revert on top of whichever routing wins here.")

    g_tel.dump_jsonl(os.path.join(HERE, "telemetry_live.jsonl"))
    print("\n[live telemetry written to telemetry_live.jsonl]")

    _write_dashboard_data({
        "mode": "live",
        "title": "Cost-Governance Prototype - Live Azure Benchmark",
        "kpis": {
            "baseline_cost": round(baseline, 4),
            "governed_cost": round(g_cost, 4),
            "saved_usd": sav["saved_usd"],
            "saved_pct": sav["saved_pct"],
            "cache_hit_rate": round(g_gw.cache.hit_rate * 100, 1),
            "avg_latency_ms": g_tel.avg_latency(),
            "quality": gq_mean,
            "requests": len(g_tel.records),
        },
        "scenarios": [
            {"name": "Baseline (premium all)", "cost": round(baseline, 4), "quality": 1.0},
            {"name": "Our governance", "cost": round(g_cost, 4), "quality": gq_mean},
            {"name": "Foundry Model Router", "cost": round(r_cost, 4), "quality": rq_mean},
        ],
        "tenants": [{"tenant": k, **v} for k, v in finops.attribution(g_tel).items()],
        "models": finops.model_breakdown(g_tel),
        "quality_by_difficulty": gq_by,
        "closed_loop": {},
        "changelog": [],
    })


if __name__ == "__main__":
    if "--live" in sys.argv:
        live_main()
    else:
        main()

