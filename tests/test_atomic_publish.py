import ctypes
import errno
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from costgov import atomic_publish
from costgov.billing_snapshots import BillingSnapshotStore
from rag.batch_feedback import save_batch_feedback
from test_batch_feedback import feedback_context
from test_billing_snapshots import snapshot
from test_performance_review import evidence


@pytest.fixture
def no_hardlinks(monkeypatch):
    def unsupported(*args):
        raise OSError(errno.EOPNOTSUPP, "Hardlinks unavailable")
    monkeypatch.setattr(atomic_publish.os, "link", unsupported)


@pytest.mark.parametrize("without_hardlinks", [False, True])
def test_complete_publication_never_replaces_existing_evidence(tmp_path, request, without_hardlinks):
    if without_hardlinks:
        request.getfixturevalue("no_hardlinks")
    staged, target = tmp_path / "staged", tmp_path / "record"
    staged.write_text("original")
    atomic_publish.publish_immutable(staged, target)
    staged.unlink(missing_ok=True)
    staged.write_text("replacement")
    with pytest.raises(FileExistsError):
        atomic_publish.publish_immutable(staged, target)
    assert target.read_text() == "original"
    assert staged.read_text() == "replacement"


def test_no_hardlink_concurrent_publication_has_one_winner(tmp_path, no_hardlinks):
    target = tmp_path / "record"

    def publish(index):
        staged = tmp_path / str(index)
        staged.write_text(str(index))
        try:
            atomic_publish.publish_immutable(staged, target)
            return index
        except FileExistsError:
            return None
        finally:
            staged.unlink(missing_ok=True)

    with ThreadPoolExecutor(max_workers=4) as pool:
        winners = [index for index in pool.map(publish, range(8)) if index is not None]
    assert len(winners) == 1
    assert target.read_text() == str(winners[0])


@pytest.mark.parametrize("code", [errno.EACCES, errno.EXDEV, errno.EIO, errno.EEXIST])
def test_other_failures_are_not_retried_or_bypassed(monkeypatch, code):
    def fail(*args):
        raise OSError(code, "original failure")
    monkeypatch.setattr(atomic_publish.os, "link", fail)
    monkeypatch.setattr(atomic_publish, "_rename_no_replace_linux",
                        lambda *args: pytest.fail("Unexpected fallback"))
    with pytest.raises(OSError) as error:
        atomic_publish.publish_immutable("source", "target")
    assert error.value.errno == code


def test_linux_fallback_uses_atomic_no_replace_flag(monkeypatch, no_hardlinks):
    calls = []

    class Rename:
        def __call__(self, *args):
            calls.append(args)
            ctypes.set_errno(errno.EEXIST)
            return -1

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(atomic_publish.ctypes, "CDLL", lambda *a, **kw: SimpleNamespace(renameat2=Rename()))
    with pytest.raises(FileExistsError):
        atomic_publish.publish_immutable("source", "target")
    assert calls == [(-100, os.fsencode("source"), -100, os.fsencode("target"), 1)]


def test_linux_without_atomic_rename_fails_closed(monkeypatch, no_hardlinks):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(atomic_publish.ctypes, "CDLL", lambda *a, **kw: SimpleNamespace())
    with pytest.raises(OSError) as error:
        atomic_publish.publish_immutable("source", "target")
    assert error.value.errno == errno.ENOTSUP


def test_billing_store_replays_on_a_filesystem_without_hardlinks(tmp_path, no_hardlinks):
    store = BillingSnapshotStore(tmp_path)
    first, created = store.append(snapshot())
    assert created
    repeated, created = store.append(snapshot(retrieved_at="2026-09-12T00:00:00Z"))
    assert not created and repeated == first
    assert len(store.list()) == 1


def test_real_feedback_stores_replay_without_hardlinks(feedback_context, no_hardlinks):
    service, receipt, batch, config, billing, learning = feedback_context
    args = service, config, billing, learning, receipt["plan_id"], batch["run_id"]
    first = save_batch_feedback(*args, actor="operator", refresh_billing=False)
    repeated = save_batch_feedback(*args, actor="operator", refresh_billing=False)
    assert first["created"] and not repeated["created"]
    assert first["record"] == repeated["record"]
    assert len(learning.list()) == 1
