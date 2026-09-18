import errno
import json
import threading
import time
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import studio
from costgov.studio_health import StorageHealthCheck, StorageUnavailable, probe_storage


def test_local_probe_accepts_lazily_created_stores_and_preserves_evidence(tmp_path):
    reports = tmp_path / "studio_reports"
    reports.mkdir()
    evidence = reports / "historical.json"
    evidence.write_bytes(b'{"unchanged":true}')
    probe_storage(tmp_path, None)
    assert evidence.read_bytes() == b'{"unchanged":true}'
    assert list((tmp_path / ".studio-health").iterdir()) == []
    assert not (tmp_path / "studio_plans").exists()


def test_local_store_permission_failure_is_not_treated_as_missing(tmp_path, monkeypatch):
    original = Path.stat
    def stat(path, *args, **kwargs):
        if path.name == "studio_reports":
            raise PermissionError(errno.EACCES, "Private path")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", stat)
    with pytest.raises(PermissionError):
        probe_storage(tmp_path, None)


def test_missing_hosted_mount_fails_without_writing_to_ephemeral_disk(tmp_path, monkeypatch):
    monkeypatch.setattr("costgov.studio_health.os.path.ismount", lambda path: False)
    with pytest.raises(StorageUnavailable, match="persistent_mount_missing"):
        probe_storage(tmp_path, str(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_missing_or_shadowed_hosted_store_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr("costgov.studio_health.os.path.ismount", lambda path: True)
    with pytest.raises(StorageUnavailable, match="persistent_store_binding_invalid"):
        probe_storage(tmp_path, str(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_readback_mismatch_is_detected_and_probe_files_are_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "read_bytes", lambda path: b"wrong bytes")
    with pytest.raises(StorageUnavailable, match="readback_failed"):
        probe_storage(tmp_path, None)
    assert list((tmp_path / ".studio-health").iterdir()) == []


def test_success_is_cached_and_failure_replaces_it_after_expiry():
    now = [0.0]
    calls = []
    def probe():
        calls.append(1)
        if len(calls) > 1:
            raise OSError(errno.EHOSTDOWN, "Sensitive mount path")
    checker = StorageHealthCheck(probe, clock=lambda: now[0])
    assert checker.check()["status"] == "healthy"
    assert checker.check()["status"] == "healthy" and len(calls) == 1
    now[0] = 6
    assert checker.check() == {"status": "unhealthy", "reason": "storage_io_failed"}
    assert len(calls) == 2


def test_failures_recover_after_cache_expiry():
    now = [0.0]
    def probe():
        if now[0] == 0:
            raise OSError(errno.EHOSTDOWN, "stale mount")
    checker = StorageHealthCheck(probe, clock=lambda: now[0])
    assert checker.check()["status"] == "unhealthy"
    now[0] = 6
    assert checker.check()["status"] == "healthy"


def test_blocked_storage_returns_promptly_and_never_spawns_more_workers():
    entered, release = threading.Event(), threading.Event()
    calls = []
    def blocked():
        calls.append(1)
        entered.set()
        release.wait(5)
    checker = StorageHealthCheck(blocked, timeout_seconds=0.02, cache_seconds=30)
    try:
        start = time.monotonic()
        assert checker.check()["reason"] == "storage_check_timeout"
        assert entered.is_set()
        for _ in range(5):
            assert checker.check()["status"] == "unhealthy"
        assert time.monotonic() - start < 1
        assert len(calls) == 1
    finally:
        release.set()
    assert checker._job.done.wait(1)
    assert checker.check()["status"] == "healthy"


def test_concurrent_requests_share_the_same_worker():
    entered, release = threading.Event(), threading.Event()
    calls, results = [], []
    def probe():
        calls.append(1)
        entered.set()
        release.wait(2)
    checker = StorageHealthCheck(probe, timeout_seconds=1)
    threads = [threading.Thread(target=lambda: results.append(checker.check())) for _ in range(8)]
    for thread in threads:
        thread.start()
    assert entered.wait(1)
    release.set()
    for thread in threads:
        thread.join()
    assert len(calls) == 1
    assert all(result["status"] == "healthy" for result in results)


@pytest.mark.parametrize("timeout,cache", [(0, 5), (-1, 5), (1, -1)])
def test_invalid_monitor_bounds_are_rejected(timeout, cache):
    with pytest.raises(ValueError):
        StorageHealthCheck(lambda: None, timeout_seconds=timeout, cache_seconds=cache)


@pytest.mark.parametrize("failure", [False, True])
def test_http_health_readiness_and_liveness_use_separate_scopes(monkeypatch, failure):
    def probe():
        if failure:
            raise OSError(errno.EHOSTDOWN, "PRIVATE_STORAGE_PATH")
    monkeypatch.setattr(studio, "_storage_health", StorageHealthCheck(probe))
    monkeypatch.setattr(studio, "load_policy_from_environment",
                        lambda: pytest.fail("Health must not call policy"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), studio.StudioHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port)
    try:
        for path in ("/health", "/readyz", "/livez"):
            connection.request("GET", path)
            response = connection.getresponse()
            raw = response.read().decode()
            payload = json.loads(raw)
            assert response.getheader("Cache-Control") == "no-store"
            assert "PRIVATE_STORAGE_PATH" not in raw
            assert response.status == (503 if failure and path != "/livez" else 200)
            if path != "/livez":
                assert payload["schema_version"] == "studio-health.v1"
                assert payload["health_scope"] == "process_and_persistent_storage"
                assert payload["checks"]["persistent_storage"]["status"] == ("unhealthy" if failure else "healthy")
    finally:
        connection.close()
        server.shutdown()
        thread.join()
        server.server_close()


def test_iac_probes_do_not_restart_the_process_for_storage_failures():
    source = (Path(__file__).resolve().parents[1] / "infra" / "studio-container-app.bicep").read_text()
    assert "type: 'Startup'\n              httpGet: {\n                path: '/livez'" in source
    assert "type: 'Liveness'\n              httpGet: {\n                path: '/livez'" in source
    assert "type: 'Readiness'\n              httpGet: {\n                path: '/readyz'" in source
    assert "timeoutSeconds: 3" in source
    assert "'/health'\n        '/livez'\n        '/readyz'" in source
