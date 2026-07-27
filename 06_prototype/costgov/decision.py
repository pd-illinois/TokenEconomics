"""
Decision binding (control plane — the "hands").

Consumes an eval report + spend, and adjusts the config knobs to keep quality above
the floor while cutting cost. This is the eval->enforcement wire that file 04/05 found
is the ONE genuine gap no incumbent ships natively. Includes hysteresis so it doesn't
flip-flop (the reason to use an Azure Function over a Logic App per file 05 §7).

In Azure this is the Logic App / Function fired by a Monitor alert; here it's a function.
"""

from __future__ import annotations


# Ordered from cheapest/riskiest to safest.
_ROUTE_LADDER = ["cost", "balanced", "quality"]


def react(config_store, eval_report, tenant_spend_ok: bool) -> list:
    """Return a list of human-readable actions taken (also written to the config store).

    Gates on the WORST segment, not the mean: an aggregate score can stay above the
    floor while a high-stakes segment (e.g. 'hard' questions) has already collapsed.
    Segment-aware gating is why this belongs in a Function, not a mean-threshold alert.
    """
    cfg = config_store.data
    floor = cfg["evaluation"]["min_quality"]
    min_samples = cfg["evaluation"].get("min_segment_samples", 1)
    required_breaches = cfg["evaluation"].get("consecutive_breaches", 1)
    actions = []

    # worst-segment score drives the decision (falls back to mean if no segments)
    segments = eval_report.by_difficulty or {}
    worst = min(segments.values()) if segments else eval_report.mean_score
    worst_seg = min(segments, key=segments.get) if segments else "overall"
    segment_counts = eval_report.by_difficulty_n or {}
    worst_count = segment_counts.get(worst_seg, eval_report.n)

    mode = cfg["routing"]["mode"]
    idx = _ROUTE_LADDER.index(mode)

    if worst < floor:
        if worst_count < min_samples:
            return [
                f"HOLD: '{worst_seg}' has {worst_count} evaluated samples; "
                f"minimum is {min_samples}"
            ]
        breach_state = cfg.setdefault("_decision_state", {}).setdefault("breaches", {})
        consecutive = breach_state.get(worst_seg, 0) + 1
        breach_state[worst_seg] = consecutive
        if consecutive < required_breaches:
            return [
                f"HOLD: '{worst_seg}' breach {consecutive}/{required_breaches}; "
                "waiting for consecutive evidence"
            ]
        # Quality regression on some segment -> escalate one rung toward safety.
        if idx < len(_ROUTE_LADDER) - 1:
            new_mode = _ROUTE_LADDER[idx + 1]
            config_store.update(["routing", "mode"], new_mode,
                                 f"eval regression: '{worst_seg}' quality {worst} < floor {floor}")
            actions.append(f"REVERT: routing {mode} -> {new_mode} "
                           f"('{worst_seg}' segment {worst} < floor {floor})")
        # also tighten the cache to stop serving weak cached answers
        thr = cfg["semantic_cache"]["score_threshold"]
        if thr < 0.9:
            config_store.update(["semantic_cache", "score_threshold"], round(thr + 0.05, 2),
                                "eval regression: tighten cache to avoid stale/weak hits")
            actions.append(f"TIGHTEN: cache threshold {thr} -> {round(thr + 0.05, 2)}")
    else:
        cfg.setdefault("_decision_state", {})["breaches"] = {}
        # Every segment has headroom -> allowed to push cost down one rung (hysteresis margin).
        headroom = worst - floor
        if headroom > 0.1 and idx > 0 and tenant_spend_ok:
            new_mode = _ROUTE_LADDER[idx - 1]
            config_store.update(["routing", "mode"], new_mode,
                                 f"all segments have headroom {round(headroom,3)} -> push cost down")
            actions.append(f"OPTIMIZE: routing {mode} -> {new_mode} "
                           f"(worst-segment headroom {round(headroom,3)})")

    if not actions:
        actions.append("HOLD: all segments within band, no change")
    return actions

