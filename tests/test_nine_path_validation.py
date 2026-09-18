from __future__ import annotations

import hashlib
import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import studio

from costgov.route_validation import (
    NINE_PATH_VALIDATION_SCHEMA_VERSION,
    nine_path_validation_matrix,
)

ROOT = Path(__file__).resolve().parents[1]


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def test_validation_matrix_covers_all_routes_without_production_claims():
    matrix = nine_path_validation_matrix()
    routes = {item["route_id"]: item for item in matrix["routes"]}

    assert set(routes) == {
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
    assert all(item["software_contract_validation"] == "passed" for item in routes.values())
    assert all(item["production_validated"] is False for item in routes.values())
    assert routes["foundry"]["source_verifiable_execution"]["status"] == (
        "measured_prototype_execution"
    )
    assert routes["work_iq"]["source_verifiable_execution"]["status"] == (
        "measured_single_task_portability"
    )
    assert routes["cowork"]["source_verifiable_execution"]["status"] == (
        "blocked_external_validation"
    )
    assert routes["copilot_studio_byom"]["hybrid_dual_ledger_required"] is True


def test_validation_matrix_is_hash_bound_and_schema_versioned():
    matrix = nine_path_validation_matrix()
    content_hash = matrix.pop("content_hash")
    schema = json.loads(
        (
            ROOT / "data" / "contracts" / "nine-path-validation.v1.schema.json"
        ).read_text(encoding="utf-8")
    )

    assert content_hash == _digest(matrix)
    assert schema["properties"]["schema_version"]["const"] == (
        NINE_PATH_VALIDATION_SCHEMA_VERSION
    )


def test_studio_exposes_route_capabilities_and_validation_matrix():
    server = ThreadingHTTPServer(("127.0.0.1", 0), studio.StudioHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        for path, expected_count in (
            ("/api/route-capabilities", 9),
            ("/api/nine-path-validation", 9),
        ):
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request("GET", path)
            response = connection.getresponse()
            payload = json.loads(response.read())
            connection.close()
            assert response.status == 200
            key = "profiles" if path.endswith("capabilities") else "routes"
            assert len(payload[key]) == expected_count
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
