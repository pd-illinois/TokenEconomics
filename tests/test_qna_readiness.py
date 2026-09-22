import pytest

from costgov.policy_store import PolicyLoadError
from rag.qna_readiness import PilotReadinessError, authorize_readiness


def denied(*codes):
    return {"ready": False, "blockers": [{"code": code} for code in codes]}


def test_transient_inventory_get_is_retried_without_releasing_captured_content():
    statuses = iter([
        denied("agent_read_access_unavailable", "retrieval_mode_unverified"),
        {"ready": True, "blockers": []},
    ])
    capture = ["private answer in caller memory"]
    attempts, delays = [], []

    def check():
        assert capture
        return next(statuses)

    assert authorize_readiness(check, record=attempts.append, sleep=delays.append) is None
    assert delays == [1]
    assert [row["ready"] for row in attempts] == [False, True]
    assert all(not row["billable_request_retried"] for row in attempts)
    assert "private answer" not in str(attempts)


def test_inventory_retries_stop_after_three_attempts():
    attempts, delays = [], []
    with pytest.raises(PilotReadinessError, match="agent_read_access_unavailable") as error:
        authorize_readiness(
            lambda: denied("agent_read_access_unavailable", "retrieval_mode_unverified"),
            record=attempts.append, sleep=delays.append,
        )
    assert len(attempts) == 3
    assert error.value.attempts == attempts
    assert delays == [1, 2]
    assert attempts[-1]["will_retry"] is False


@pytest.mark.parametrize("code", [
    "measurement_authorization_expired", "azure_authority_required", "receipt_policy_rejected",
    "judge_binding_changed", "response_configuration_changed", "agent_not_active",
])
def test_denials_never_retry_even_when_inventory_also_failed(code):
    attempts, delays = [], []
    with pytest.raises(PilotReadinessError, match=code):
        authorize_readiness(
            lambda: denied("agent_read_access_unavailable", code),
            record=attempts.append, sleep=delays.append,
        )
    assert len(attempts) == 1
    assert not delays


def test_policy_load_failure_is_recorded_without_exception_content():
    attempts = []

    def check():
        raise PolicyLoadError("sensitive upstream details")

    with pytest.raises(PilotReadinessError, match="policy_unavailable"):
        authorize_readiness(check, record=attempts.append)
    assert len(attempts) == 1
    assert "sensitive" not in str(attempts)


def test_diagnostic_persistence_failure_stops_authorization():
    def record(_):
        raise OSError("disk unavailable")

    with pytest.raises(OSError, match="disk unavailable"):
        authorize_readiness(lambda: {"ready": True, "blockers": []}, record=record)
