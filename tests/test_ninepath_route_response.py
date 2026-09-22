from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from costgov.route_response import (
    ConstraintWindow,
    RouteResponseStore,
    WindowOutcome,
    response_ladder_for,
)

ROOT = Path(__file__).resolve().parents[1]
LEGS = {
    "included": "subscription",
    "cowork": "commercial",
    "agent_builder": "subscription",
    "copilot_studio": "commercial",
    "work_iq": "work_iq",
    "foundry": "foundry",
    "github_copilot": "github_copilot",
    "copilot_studio_byom": "foundry",
    "foundry_work_iq": "work_iq",
}


def _window(number: int, outcome: WindowOutcome, leg: str) -> ConstraintWindow:
    return ConstraintWindow(
        window_id=f"window-{number}",
        content_hash=f"{number:x}" * 64,
        outcome=outcome,
        segment_id="hard",
        segment_version="v1",
        target_leg=leg,
        evaluated_at=f"2026-09-02T1{number}:00:00+00:00",
    )


def _record(
    store: RouteResponseStore,
    route: str,
    number: int,
    outcome: WindowOutcome,
    *,
    active: bool,
    leg: str | None = None,
):
    selected_leg = leg or LEGS[route]
    return store.record(
        route_id=route,
        candidate_id="candidate",
        candidate_version="v1",
        candidate_content_hash="a" * 64,
        candidate_is_active=active,
        current_publication_reference=(
            {
                "policy_version": "policy.v1",
                "etag": "etag",
                "content_hash": "b" * 64,
            }
            if active and selected_leg == "foundry"
            else None
        ),
        window=_window(number, outcome, selected_leg),
        recorded_at=f"2026-09-0{number}T20:00:00+00:00",
    )


def test_every_route_has_a_bounded_capability_specific_ladder() -> None:
    assert set(LEGS) == {
        "included",
        "cowork",
        "agent_builder",
        "copilot_studio",
        "work_iq",
        "foundry",
        "github_copilot",
        "copilot_studio_byom",
        "foundry_work_iq",
    }
    assert all(response_ladder_for(route, leg) for route, leg in LEGS.items())


def test_two_window_reversion_is_replay_safe_and_requires_review(tmp_path: Path) -> None:
    store = RouteResponseStore(tmp_path)
    first, created = _record(store, "foundry", 1, WindowOutcome.FAIL, active=True)
    replay, replay_created = _record(
        store, "foundry", 1, WindowOutcome.FAIL, active=True
    )
    second, _ = _record(store, "foundry", 2, WindowOutcome.FAIL, active=True)

    assert created is True
    assert replay_created is False
    assert replay == first
    assert first["status"] == "breach_observed"
    assert second["status"] == "reviewed_reversion_required"
    assert second["publisher_handoff"]["requires_review"] is True
    assert second["publisher_handoff"]["mutation_performed"] is False
    assert second["azure_mutation_performed"] is False


def test_inactive_and_external_candidates_never_fabricate_reversion(
    tmp_path: Path,
) -> None:
    inactive = RouteResponseStore(tmp_path / "inactive")
    _record(inactive, "foundry", 1, WindowOutcome.FAIL, active=False)
    blocked, _ = _record(inactive, "foundry", 2, WindowOutcome.FAIL, active=False)
    external = RouteResponseStore(tmp_path / "external")
    _record(external, "cowork", 1, WindowOutcome.FAIL, active=True)
    admin, _ = _record(external, "cowork", 2, WindowOutcome.FAIL, active=True)

    assert blocked["status"] == "candidate_blocked"
    assert "reverted" not in blocked["status"]
    assert blocked["publisher_handoff"] is None
    assert admin["status"] == "product_admin_action_required"
    assert admin["publisher_handoff"] is None
    assert admin["azure_mutation_performed"] is False


def test_advisory_route_records_recommendation_without_publication(
    tmp_path: Path,
) -> None:
    store = RouteResponseStore(tmp_path)
    _record(store, "included", 1, WindowOutcome.FAIL, active=True)
    advisory, _ = _record(store, "included", 2, WindowOutcome.FAIL, active=True)

    assert advisory["status"] == "advisory_recommendation"
    assert advisory["authority_mode"] == "advisory"
    assert advisory["publisher_handoff"] is None


def test_hybrid_leg_state_is_independent_and_recovery_uses_two_windows(
    tmp_path: Path,
) -> None:
    store = RouteResponseStore(tmp_path)
    _record(
        store,
        "copilot_studio_byom",
        1,
        WindowOutcome.FAIL,
        active=True,
        leg="foundry",
    )
    reverted, _ = _record(
        store,
        "copilot_studio_byom",
        2,
        WindowOutcome.FAIL,
        active=True,
        leg="foundry",
    )
    commercial, _ = _record(
        store,
        "copilot_studio_byom",
        3,
        WindowOutcome.PASS,
        active=True,
        leg="commercial",
    )
    recovery_one, _ = _record(
        store,
        "copilot_studio_byom",
        4,
        WindowOutcome.PASS,
        active=True,
        leg="foundry",
    )
    recovery_two, _ = _record(
        store,
        "copilot_studio_byom",
        5,
        WindowOutcome.PASS,
        active=True,
        leg="foundry",
    )

    assert reverted["status"] == "reviewed_reversion_required"
    assert commercial["status"] == "eligible"
    assert recovery_one["status"] == "recovery_observed"
    assert recovery_two["status"] == "recovery_review_required"
    assert recovery_two["publisher_handoff"]["requires_review"] is True


def test_insufficient_window_resets_counts_and_schema_aligns(tmp_path: Path) -> None:
    store = RouteResponseStore(tmp_path)
    _record(store, "foundry", 1, WindowOutcome.FAIL, active=True)
    insufficient, _ = _record(
        store, "foundry", 2, WindowOutcome.INSUFFICIENT, active=True
    )
    after, _ = _record(store, "foundry", 3, WindowOutcome.FAIL, active=True)

    assert insufficient["status"] == "inconclusive"
    assert insufficient["consecutive_failures"] == 0
    assert after["status"] == "breach_observed"
    schema = json.loads(
        (ROOT / "data/contracts/route-response-transition.v1.schema.json").read_text()
    )
    Draft202012Validator(schema).validate(after)


def test_insufficient_window_cannot_bypass_two_window_recovery(tmp_path: Path) -> None:
    store = RouteResponseStore(tmp_path)
    _record(store, "foundry", 1, WindowOutcome.FAIL, active=True)
    reverted, _ = _record(store, "foundry", 2, WindowOutcome.FAIL, active=True)
    insufficient, _ = _record(
        store, "foundry", 3, WindowOutcome.INSUFFICIENT, active=True
    )
    recovery_one, _ = _record(store, "foundry", 4, WindowOutcome.PASS, active=True)
    recovery_two, _ = _record(store, "foundry", 5, WindowOutcome.PASS, active=True)

    assert reverted["status"] == "reviewed_reversion_required"
    assert insufficient["status"] == "inconclusive"
    assert recovery_one["status"] == "recovery_observed"
    assert recovery_two["status"] == "recovery_review_required"
    assert recovery_two["publisher_handoff"]["requires_review"] is True
