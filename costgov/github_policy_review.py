"""GitHub App integration for review-only TokenGov policy pull requests."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .policy_publication import content_hash

GITHUB_API = "https://api.github.com"
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class GitHubPolicyReviewError(RuntimeError):
    """Raised when a policy review pull request cannot be created."""


@dataclass(frozen=True)
class GitHubAppReviewConfig:
    app_id: str
    installation_id: str
    repository: str
    private_key: str
    base_branch: str = "main"

    @classmethod
    def from_environment(cls) -> "GitHubAppReviewConfig":
        values = {
            "app_id": os.environ.get("TOKENGOV_GITHUB_APP_ID", "").strip(),
            "installation_id": os.environ.get(
                "TOKENGOV_GITHUB_APP_INSTALLATION_ID", ""
            ).strip(),
            "repository": os.environ.get("TOKENGOV_GITHUB_REPOSITORY", "").strip(),
            "private_key": os.environ.get(
                "TOKENGOV_GITHUB_APP_PRIVATE_KEY", ""
            ).strip(),
            "base_branch": os.environ.get(
                "TOKENGOV_GITHUB_BASE_BRANCH", "main"
            ).strip(),
        }
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise GitHubPolicyReviewError(
                "GitHub App review integration is not configured: "
                + ", ".join(sorted(missing))
            )
        if not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", values["repository"]
        ):
            raise GitHubPolicyReviewError(
                "TOKENGOV_GITHUB_REPOSITORY must use owner/repository format"
            )
        return cls(**values)


@dataclass(frozen=True)
class GitHubCliReviewConfig:
    repository: str
    base_branch: str = "main"
    allowed_login: str = ""

    @classmethod
    def from_environment(cls) -> "GitHubCliReviewConfig":
        if os.environ.get("TOKENGOV_REVIEW_ALLOW_LOCAL", "").lower() != "true":
            raise GitHubPolicyReviewError(
                "GitHub CLI review is allowed only for explicit loopback development"
            )
        repository = os.environ.get("TOKENGOV_GITHUB_REPOSITORY", "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise GitHubPolicyReviewError(
                "TOKENGOV_GITHUB_REPOSITORY must use owner/repository format"
            )
        if shutil.which("gh") is None:
            raise GitHubPolicyReviewError("GitHub CLI is not installed")
        return cls(
            repository=repository,
            base_branch=os.environ.get(
                "TOKENGOV_GITHUB_BASE_BRANCH", "main"
            ).strip(),
            allowed_login=os.environ.get(
                "TOKENGOV_GITHUB_CLI_ALLOWED_LOGIN", ""
            ).strip(),
        )


def _review_provider(local_authorized: bool) -> str:
    configured = os.environ.get("TOKENGOV_REVIEW_PROVIDER", "").strip().lower()
    provider = configured or ("github_cli" if local_authorized else "github_app")
    if provider not in {"github_app", "github_cli"}:
        raise GitHubPolicyReviewError(
            "TOKENGOV_REVIEW_PROVIDER must be github_app or github_cli"
        )
    if provider == "github_cli" and not local_authorized:
        raise GitHubPolicyReviewError(
            "GitHub CLI review cannot be used by deployed Studio"
        )
    return provider


def review_configuration() -> dict[str, Any]:
    local_authorized = os.environ.get(
        "TOKENGOV_REVIEW_ALLOW_LOCAL", ""
    ).lower() == "true"
    try:
        provider = _review_provider(local_authorized)
    except GitHubPolicyReviewError as exc:
        return {
            "provider": "unavailable",
            "configured": False,
            "repository": None,
            "missing": ["review_provider"],
            "error": str(exc),
        }
    if provider == "github_cli":
        try:
            client = GitHubCliReviewClient(GitHubCliReviewConfig.from_environment())
            identity = client.probe()
            return {
                "provider": provider,
                "configured": True,
                "repository": client.config.repository,
                "actor": identity["actor"],
                "missing": [],
            }
        except GitHubPolicyReviewError as exc:
            return {
                "provider": provider,
                "configured": False,
                "repository": os.environ.get(
                    "TOKENGOV_GITHUB_REPOSITORY", ""
                ).strip()
                or None,
                "missing": ["github_cli_auth"],
                "error": str(exc),
            }
    required = {
        "app_id": os.environ.get("TOKENGOV_GITHUB_APP_ID", "").strip(),
        "installation_id": os.environ.get(
            "TOKENGOV_GITHUB_APP_INSTALLATION_ID", ""
        ).strip(),
        "repository": os.environ.get("TOKENGOV_GITHUB_REPOSITORY", "").strip(),
        "private_key": os.environ.get("TOKENGOV_GITHUB_APP_PRIVATE_KEY", "").strip(),
        "authenticated_ingress": (
            "local"
            if local_authorized
            else os.environ.get(
                "TOKENGOV_REVIEW_AUTHENTICATED_INGRESS", ""
            ).strip()
        ),
        "authorized_principal": (
            "local"
            if local_authorized
            else os.environ.get(
                "TOKENGOV_REVIEW_ALLOWED_PRINCIPAL", ""
            ).strip()
        ),
    }
    missing = [key for key, value in required.items() if not value]
    return {
        "provider": provider,
        "configured": not missing,
        "repository": required["repository"] or None,
        "missing": missing,
    }


def configured_review_client() -> "GitHubPolicyReviewClient":
    local_authorized = os.environ.get(
        "TOKENGOV_REVIEW_ALLOW_LOCAL", ""
    ).lower() == "true"
    provider = _review_provider(local_authorized)
    if provider == "github_cli":
        return GitHubCliReviewClient(GitHubCliReviewConfig.from_environment())
    return GitHubPolicyReviewClient(GitHubAppReviewConfig.from_environment())


class GitHubPolicyReviewClient:
    def __init__(self, config: GitHubAppReviewConfig) -> None:
        self.config = config

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        payload: dict | None = None,
    ) -> Any:
        data = (
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
            if payload is not None
            else None
        )
        request = Request(
            f"{GITHUB_API}{path}",
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "TokenEconomics-Studio",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                body = response.read()
                return json.loads(body) if body else None
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise GitHubPolicyReviewError(
                f"GitHub API {method} {path} failed ({exc.code}): {detail}"
            ) from exc

    def _app_jwt(self) -> str:
        try:
            import jwt
        except ImportError as exc:
            raise GitHubPolicyReviewError(
                "PyJWT with cryptography support is required"
            ) from exc
        key = self.config.private_key.replace("\\n", "\n")
        if "BEGIN" not in key:
            try:
                key = base64.b64decode(key).decode("utf-8")
            except (ValueError, UnicodeDecodeError) as exc:
                raise GitHubPolicyReviewError(
                    "GitHub App private key must be PEM text or base64-encoded PEM"
                ) from exc
        now = int(time.time())
        return jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": self.config.app_id},
            key,
            algorithm="RS256",
        )

    def _installation_token(self) -> str:
        response = self._request(
            "POST",
            f"/app/installations/{quote(self.config.installation_id)}/access_tokens",
            token=self._app_jwt(),
            payload={"repositories": [self.config.repository.split("/", 1)[1]]},
        )
        token = response.get("token") if isinstance(response, dict) else None
        if not token:
            raise GitHubPolicyReviewError(
                "GitHub App installation did not return an access token"
            )
        return token

    @staticmethod
    def _component(value: object, label: str) -> str:
        text = str(value or "").strip()
        if not _SAFE_COMPONENT.fullmatch(text):
            raise GitHubPolicyReviewError(f"{label} contains unsupported characters")
        return text

    def create_review(self, proposal: dict) -> dict[str, Any]:
        token = self._installation_token()
        repository = self.config.repository
        branch = f"tokengov/{proposal['change_id'].lower()}"
        policy = proposal["proposed_policy"]
        policy_id = self._component(policy["policy_id"], "policy_id")
        version = self._component(policy["version"], "version")
        policy_path = str(PurePosixPath("data/policies") / f"{policy_id}.{version}.json")
        review_path = str(
            PurePosixPath("data/policy_reviews")
            / f"{proposal['change_id'].lower()}.json"
        )
        if not proposal["base_policy"].get("etag"):
            raise GitHubPolicyReviewError(
                "A reviewed Azure base ETag is required before approval"
            )

        existing = self._find_pull_request(token, branch)
        if existing:
            return self._review_result(existing, branch, policy_path, review_path)

        base_ref = self._request(
            "GET",
            f"/repos/{repository}/git/ref/heads/{quote(self.config.base_branch, safe='')}",
            token=token,
        )
        base_sha = base_ref["object"]["sha"]
        try:
            self._request(
                "GET",
                (
                    f"/repos/{repository}/contents/{quote(policy_path, safe='/')}"
                    f"?ref={quote(self.config.base_branch, safe='')}"
                ),
                token=token,
            )
        except GitHubPolicyReviewError as exc:
            if "(404)" not in str(exc):
                raise
        else:
            raise GitHubPolicyReviewError(
                f"review target already exists on {self.config.base_branch}: {policy_path}"
            )
        try:
            self._request(
                "POST",
                f"/repos/{repository}/git/refs",
                token=token,
                payload={"ref": f"refs/heads/{branch}", "sha": base_sha},
            )
        except GitHubPolicyReviewError as exc:
            if "Reference already exists" not in str(exc):
                raise

        review_manifest = {
            "schema_version": "policy-review.v1",
            "change_id": proposal["change_id"],
            "policy_path": policy_path,
            "expected_etag": proposal["base_policy"]["etag"],
            "base_policy": proposal["base_policy"],
            "proposed_version": proposal["proposed_version"],
            "proposed_content_hash": content_hash(policy),
            "authoring_mode": proposal["authoring_mode"],
            "created_at": proposal["created_at"],
        }
        self._commit_files(
            token,
            branch,
            {
                policy_path: json.dumps(policy, indent=2, ensure_ascii=True) + "\n",
                review_path: json.dumps(
                    review_manifest, indent=2, ensure_ascii=True
                )
                + "\n",
            },
            f"Add reviewed policy {policy_id} {version}",
        )
        pull = self._request(
            "POST",
            f"/repos/{repository}/pulls",
            token=token,
            payload={
                "title": f"Review TokenGov policy {version}",
                "head": branch,
                "base": self.config.base_branch,
                "body": self._pull_request_body(proposal, policy_path, review_path),
                "maintainer_can_modify": True,
            },
        )
        return self._review_result(pull, branch, policy_path, review_path)

    def _commit_files(
        self,
        token: str,
        branch: str,
        files: dict[str, str],
        message: str,
    ) -> None:
        repository = self.config.repository
        ref = self._request(
            "GET",
            f"/repos/{repository}/git/ref/heads/{quote(branch, safe='')}",
            token=token,
        )
        parent_sha = ref["object"]["sha"]
        commit = self._request(
            "GET", f"/repos/{repository}/git/commits/{parent_sha}", token=token
        )
        tree_items = []
        for path, content in files.items():
            blob = self._request(
                "POST",
                f"/repos/{repository}/git/blobs",
                token=token,
                payload={"content": content, "encoding": "utf-8"},
            )
            tree_items.append(
                {"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]}
            )
        tree = self._request(
            "POST",
            f"/repos/{repository}/git/trees",
            token=token,
            payload={"base_tree": commit["tree"]["sha"], "tree": tree_items},
        )
        new_commit = self._request(
            "POST",
            f"/repos/{repository}/git/commits",
            token=token,
            payload={"message": message, "tree": tree["sha"], "parents": [parent_sha]},
        )
        self._request(
            "PATCH",
            f"/repos/{repository}/git/refs/heads/{quote(branch, safe='')}",
            token=token,
            payload={"sha": new_commit["sha"], "force": False},
        )

    def _find_pull_request(self, token: str, branch: str) -> dict | None:
        owner = self.config.repository.split("/", 1)[0]
        query = urlencode({"state": "open", "head": f"{owner}:{branch}", "per_page": 1})
        pulls = self._request(
            "GET",
            f"/repos/{self.config.repository}/pulls?{query}",
            token=token,
        )
        return pulls[0] if pulls else None

    def _review_result(
        self,
        pull: dict,
        branch: str,
        policy_path: str,
        review_path: str,
    ) -> dict[str, Any]:
        return {
            "provider": "github",
            "repository": self.config.repository,
            "branch": branch,
            "policy_path": policy_path,
            "review_manifest_path": review_path,
            "pull_request_number": pull["number"],
            "pull_request_url": pull["html_url"],
            "state": pull.get("state", "open"),
        }

    @staticmethod
    def _pull_request_body(
        proposal: dict, policy_path: str, review_path: str
    ) -> str:
        rows = "\n".join(
            f"| `{item['path']}` | `{item['current']}` | `{item['proposed']}` |"
            for item in proposal["diff"]
        )
        return (
            "## TokenGov policy review\n\n"
            f"- Change request: `{proposal['change_id']}`\n"
            f"- Authoring mode: `{proposal['authoring_mode']}`\n"
            f"- Base version: `{proposal['base_policy']['version']}`\n"
            f"- Reviewed base ETag: `{proposal['base_policy']['etag']}`\n"
            f"- Proposed version: `{proposal['proposed_version']}`\n"
            f"- Policy artifact: `{policy_path}`\n"
            f"- Review manifest: `{review_path}`\n\n"
            f"**Business reason:** {proposal['reason']}\n\n"
            "| Control | Current | Proposed |\n"
            "|---|---|---|\n"
            f"{rows}\n\n"
            "Merging records content approval. The protected publication workflow "
            "still requires environment approval and verifies Azure ETag and content "
            "before Studio can display this policy as Active.\n"
        )


class GitHubCliReviewClient(GitHubPolicyReviewClient):
    """Loopback-only GitHub API transport using the active gh keyring identity."""

    def __init__(self, config: GitHubCliReviewConfig) -> None:
        self.config = config
        self._actor = ""

    @staticmethod
    def _gh_environment() -> dict[str, str]:
        environment = os.environ.copy()
        environment.pop("GH_TOKEN", None)
        environment.pop("GITHUB_TOKEN", None)
        return environment

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        payload: dict | None = None,
    ) -> Any:
        del token
        command = [
            "gh",
            "api",
            "--hostname",
            "github.com",
            "--method",
            method,
            path.lstrip("/"),
            "--header",
            "Accept: application/vnd.github+json",
            "--header",
            "X-GitHub-Api-Version: 2022-11-28",
        ]
        input_text = None
        if payload is not None:
            command.extend(["--input", "-"])
            input_text = json.dumps(payload, separators=(",", ":"))
        try:
            result = subprocess.run(
                command,
                input=input_text,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                env=self._gh_environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitHubPolicyReviewError(
                f"GitHub CLI {method} request could not run: {exc}"
            ) from exc
        if result.returncode != 0:
            detail = result.stderr.strip()[:1000] or "request failed"
            status_match = re.search(r"\bHTTP\s+(\d{3})\b", detail)
            status = f" ({status_match.group(1)})" if status_match else ""
            raise GitHubPolicyReviewError(
                f"GitHub CLI {method} {path} failed{status}: {detail}"
            )
        body = result.stdout.strip()
        if not body:
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise GitHubPolicyReviewError(
                f"GitHub CLI {method} {path} returned invalid JSON"
            ) from exc

    def _installation_token(self) -> str:
        return ""

    def probe(self) -> dict[str, Any]:
        user = self._request("GET", "/user", token="")
        actor = str(user.get("login", "")).strip() if isinstance(user, dict) else ""
        if not actor:
            raise GitHubPolicyReviewError(
                "GitHub CLI does not have an active authenticated account"
            )
        if self.config.allowed_login and actor != self.config.allowed_login:
            raise GitHubPolicyReviewError(
                f"GitHub CLI must use {self.config.allowed_login}, not {actor}"
            )
        repository = self._request(
            "GET", f"/repos/{self.config.repository}", token=""
        )
        permissions = (
            repository.get("permissions", {}) if isinstance(repository, dict) else {}
        )
        if not permissions.get("push"):
            raise GitHubPolicyReviewError(
                f"GitHub CLI account {actor} lacks push access to "
                f"{self.config.repository}"
            )
        self._actor = actor
        return {"actor": actor, "permissions": permissions}

    def create_review(self, proposal: dict) -> dict[str, Any]:
        self.probe()
        result = super().create_review(proposal)
        result["provider"] = "github_cli"
        result["actor"] = self._actor
        return result
