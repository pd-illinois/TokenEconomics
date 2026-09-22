"""Bounded, single-flight checks of Studio's persistent-state dependency."""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


LOGGER = logging.getLogger(__name__)
STUDIO_STATE_STORES = (
    "studio_billing_evidence", "studio_decision_state", "studio_governance_evidence",
    "studio_learning_evidence", "studio_lifecycle", "studio_plans", "studio_policy_changes",
    "studio_portability_evidence", "studio_reconciliation_evidence", "studio_reports",
    "studio_response_learning", "studio_runs",
)


class StorageUnavailable(Exception):
    """A safe, non-sensitive diagnostic code for the public health response."""


def probe_storage(app_root: Path, state_root: str | None) -> None:
    root = Path(state_root) if state_root else app_root
    if state_root and not os.path.ismount(root):
        raise StorageUnavailable("persistent_mount_missing")
    for name in STUDIO_STATE_STORES:
        path = app_root / name
        if state_root:
            if not path.is_symlink() or path.resolve(strict=True) != root.resolve(strict=True) / name:
                raise StorageUnavailable("persistent_store_binding_invalid")
        else:
            try:
                path.stat()
            except FileNotFoundError:
                # Native stores are created lazily, unlike container-mounted stores.
                continue
        with os.scandir(path) as entries:
            next(entries, None)
    probe_root = root / ".studio-health"
    probe_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="probe-", dir=probe_root) as directory:
        path = Path(directory) / "io-check"
        token = os.urandom(32)
        with path.open("xb") as stream:
            stream.write(token)
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != token:
            raise StorageUnavailable("persistent_storage_readback_failed")


@dataclass
class _Probe:
    done: threading.Event = field(default_factory=threading.Event)
    result: dict = field(default_factory=lambda: {
        "status": "unhealthy", "reason": "storage_check_failed",
    })
    completed_at: float | None = None
    timeout_logged: bool = False


class StorageHealthCheck:
    def __init__(
        self, probe: Callable[[], None], *, timeout_seconds: float = 1,
        cache_seconds: float = 5, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout_seconds <= 0 or cache_seconds < 0:
            raise ValueError("health timeout must be positive and cache duration non-negative")
        self._probe = probe
        self._timeout = timeout_seconds
        self._cache = cache_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._job: _Probe | None = None

    def _run(self, job: _Probe) -> None:
        try:
            self._probe()
            job.result = {"status": "healthy", "reason": "storage_io_verified"}
        except StorageUnavailable as exc:
            job.result = {"status": "unhealthy", "reason": str(exc)}
            LOGGER.warning("Studio storage readiness failed: %s", exc)
        except OSError as exc:
            job.result = {"status": "unhealthy", "reason": "storage_io_failed"}
            LOGGER.warning("Studio storage readiness I/O failed (errno=%s)", exc.errno)
        finally:
            job.completed_at = self._clock()
            job.done.set()

    def check(self) -> dict:
        with self._lock:
            job = self._job
            if job is None or (
                job.done.is_set() and job.completed_at is not None
                and self._clock() - job.completed_at >= self._cache
            ):
                job = self._job = _Probe()
                # A blocked SMB syscall cannot be cancelled safely. Keep one daemon
                # worker in flight rather than leaking a new thread on every probe.
                threading.Thread(target=self._run, args=(job,), daemon=True,
                                 name="studio-storage-health").start()
        if not job.done.wait(self._timeout):
            with self._lock:
                if not job.timeout_logged:
                    LOGGER.warning("Studio storage readiness timed out")
                    job.timeout_logged = True
            return {"status": "unhealthy", "reason": "storage_check_timeout"}
        return dict(job.result)
