from __future__ import annotations

import json
from pathlib import Path

from costgov.github_policy_review import (
    GitHubAppReviewConfig,
    GitHubCliReviewClient,
    GitHubCliReviewConfig,
    GitHubPolicyReviewClient,
    review_configuration,
)

ROOT = Path(__file__).resolve().parents[1]


def _proposal() -> dict:
    return {
        "change_id": "PCR-ABC123",
        "authoring_mode": "edit",
        "created_at": "2026-09-03T12:00:00+00:00",
        "reason": "Admit a reviewed model.",
        "base_policy": {
            "policy_id": "tokengov-production",
            "version": "2026-08-31.1",
            "etag": "etag-1",
            "content_hash": "a" * 64,
        },
        "proposed_version": "2026-09-03.1",
        "diff": [
            {
                "path": "admission.allowed_models",
                "current": ["gpt-4.1-mini"],
                "proposed": ["gpt-5.6-luna"],
            }
        ],
        "proposed_policy": {
            "schema_version": "1.0",
            "policy_id": "tokengov-production",
            "version": "2026-09-03.1",
        },
    }


def test_review_configuration_requires_app_secret_and_authenticated_ingress(
    monkeypatch,
):
    monkeypatch.delenv("TOKENGOV_REVIEW_ALLOW_LOCAL", raising=False)
    monkeypatch.delenv("TOKENGOV_REVIEW_PROVIDER", raising=False)
    monkeypatch.setenv("TOKENGOV_GITHUB_APP_ID", "1")
    monkeypatch.setenv("TOKENGOV_GITHUB_APP_INSTALLATION_ID", "2")
    monkeypatch.setenv("TOKENGOV_GITHUB_REPOSITORY", "example/repo")
    monkeypatch.delenv("TOKENGOV_GITHUB_APP_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("TOKENGOV_REVIEW_AUTHENTICATED_INGRESS", raising=False)
    monkeypatch.delenv("TOKENGOV_REVIEW_ALLOWED_PRINCIPAL", raising=False)

    result = review_configuration()

    assert result["configured"] is False
    assert set(result["missing"]) == {
        "private_key",
        "authenticated_ingress",
        "authorized_principal",
    }


def test_github_app_review_creates_policy_manifest_and_pull_request():
    class FakeClient(GitHubPolicyReviewClient):
        def __init__(self):
            super().__init__(
                GitHubAppReviewConfig("1", "2", "example/repo", "unused")
            )
            self.calls = []

        def _installation_token(self):
            return "installation-token"

        def _request(self, method, path, *, token, payload=None):
            self.calls.append((method, path, payload))
            if path.endswith("/pulls?state=open&head=example%3Atokengov%2Fpcr-abc123&per_page=1"):
                return []
            if path.endswith("/git/ref/heads/main"):
                return {"object": {"sha": "base-sha"}}
            if "/contents/data/policies/" in path:
                from costgov.github_policy_review import GitHubPolicyReviewError

                raise GitHubPolicyReviewError("GitHub API GET failed (404): not found")
            if method == "POST" and path.endswith("/git/refs"):
                return {"ref": "refs/heads/tokengov/pcr-abc123"}
            if path.endswith("/git/ref/heads/tokengov%2Fpcr-abc123"):
                return {"object": {"sha": "base-sha"}}
            if path.endswith("/git/commits/base-sha"):
                return {"tree": {"sha": "base-tree"}}
            if path.endswith("/git/blobs"):
                return {"sha": f"blob-{len(self.calls)}"}
            if path.endswith("/git/trees"):
                return {"sha": "new-tree"}
            if method == "POST" and path.endswith("/git/commits"):
                return {"sha": "new-commit"}
            if method == "PATCH" and "/git/refs/heads/" in path:
                return {"object": {"sha": "new-commit"}}
            if method == "POST" and path.endswith("/pulls"):
                return {
                    "number": 42,
                    "html_url": "https://github.com/example/repo/pull/42",
                    "state": "open",
                }
            raise AssertionError((method, path, payload))

    client = FakeClient()
    result = client.create_review(_proposal())

    assert result["pull_request_number"] == 42
    tree_payload = next(
        payload
        for method, path, payload in client.calls
        if method == "POST" and path.endswith("/git/trees")
    )
    assert {item["path"] for item in tree_payload["tree"]} == {
        "data/policies/tokengov-production.2026-09-03.1.json",
        "data/policy_reviews/pcr-abc123.json",
    }
    blob_contents = [
        json.loads(payload["content"])
        for method, path, payload in client.calls
        if method == "POST"
        and path.endswith("/git/blobs")
        and '"schema_version": "policy-review.v1"' in payload["content"]
    ]
    assert blob_contents[0]["expected_etag"] == "etag-1"


def test_local_review_uses_keyring_backed_github_cli(monkeypatch):
    monkeypatch.setenv("TOKENGOV_REVIEW_ALLOW_LOCAL", "true")
    monkeypatch.setenv("TOKENGOV_GITHUB_REPOSITORY", "example/repo")
    monkeypatch.setenv("TOKENGOV_GITHUB_CLI_ALLOWED_LOGIN", "example")
    monkeypatch.delenv("TOKENGOV_REVIEW_PROVIDER", raising=False)
    monkeypatch.setattr(
        GitHubCliReviewClient,
        "probe",
        lambda self: {"actor": "example", "permissions": {"push": True}},
    )

    result = review_configuration()

    assert result == {
        "provider": "github_cli",
        "configured": True,
        "repository": "example/repo",
        "actor": "example",
        "missing": [],
    }


def test_github_cli_transport_ignores_ambient_token(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        return type("Result", (), {"returncode": 0, "stdout": '{"login":"example"}', "stderr": ""})()

    monkeypatch.setenv("GH_TOKEN", "must-not-be-used")
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-be-used")
    monkeypatch.setattr("costgov.github_policy_review.subprocess.run", fake_run)
    client = GitHubCliReviewClient(GitHubCliReviewConfig("example/repo"))

    assert client._request("GET", "/user", token="")["login"] == "example"
    assert "GH_TOKEN" not in captured["environment"]
    assert "GITHUB_TOKEN" not in captured["environment"]
    assert captured["command"][:4] == ["gh", "api", "--hostname", "github.com"]


def test_github_cli_transport_normalizes_http_status(monkeypatch):
    def fake_run(command, **kwargs):
        return type(
            "Result",
            (),
            {
                "returncode": 1,
                "stdout": "",
                "stderr": "gh: Not Found (HTTP 404)",
            },
        )()

    monkeypatch.setattr("costgov.github_policy_review.subprocess.run", fake_run)
    client = GitHubCliReviewClient(GitHubCliReviewConfig("example/repo"))

    try:
        client._request("GET", "/missing", token="")
    except Exception as exc:
        assert "failed (404)" in str(exc)
    else:
        raise AssertionError("expected missing GitHub resource to fail")


def test_policy_publication_workflow_resolves_merged_review_manifest():
    workflow = (
        ROOT / ".github" / "workflows" / "publish-tokengov-policy.yml"
    ).read_text(encoding="utf-8")

    assert "push:" in workflow
    assert "data/policy_reviews/*.json" in workflow
    assert "scripts/resolve_policy_review.py" in workflow
    assert '${{ needs.resolve.outputs.expected_etag }}' in workflow
    assert "environment: tokengov-production" in workflow
