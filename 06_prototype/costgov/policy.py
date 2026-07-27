"""Select quality-eligible policy candidates under forecast constraints."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import ForecastReceipt


@dataclass(frozen=True)
class PolicyCandidate:
    version: str
    routing_mode: str
    expected_quality: float
    expected_cost_multiplier: float


@dataclass(frozen=True)
class PolicySelection:
    candidate: PolicyCandidate
    expected_cost_usd: float
    reason: str


def select_policy(
    receipt: ForecastReceipt,
    candidates: tuple[PolicyCandidate, ...],
    *,
    quality_floor: float,
    budget_usd: float,
    segment_volumes: dict[str, int] | None = None,
) -> PolicySelection:
    volumes = segment_volumes or {}
    base_cost = sum(
        forecast.expected_cost_usd * volumes.get(forecast.segment, 1)
        for forecast in receipt.forecasts
    )
    eligible = []
    for candidate in candidates:
        cost = base_cost * candidate.expected_cost_multiplier
        if candidate.expected_quality >= quality_floor and cost <= budget_usd:
            eligible.append((cost, candidate))
    if not eligible:
        raise ValueError("no policy candidate satisfies quality and budget constraints")
    cost, candidate = min(eligible, key=lambda item: item[0])
    return PolicySelection(
        candidate=candidate,
        expected_cost_usd=cost,
        reason=(
            f"lowest forecast cost among candidates meeting quality >= {quality_floor} "
            f"and budget <= {budget_usd}"
        ),
    )