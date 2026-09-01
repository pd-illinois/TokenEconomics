"""Provision immutable Foundry IQ retrieval arms for TE-009 and TE-013."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.core.credentials import TokenCredential
from azure.identity import AzureCliCredential


SEARCH_ENDPOINT = "https://search-xbk6ickycmp22.search.windows.net"
PROJECT_ENDPOINT = (
    "https://ai-account-xbk6ickycmp22.services.ai.azure.com/api/projects/"
    "ai-project-tokeneconomics-te003"
)
PROJECT_RESOURCE_ID = (
    "/subscriptions/a91cc1ba-bd19-43a7-90ea-120794c0fbc6/"
    "resourceGroups/rg-tokeneconomics/providers/Microsoft.CognitiveServices/"
    "accounts/ai-account-xbk6ickycmp22/projects/"
    "ai-project-tokeneconomics-te003"
)
SEARCH_API_VERSION = "2026-08-01-preview"
ARM_API_VERSION = "2025-06-01"
AGENT_NAME = "tokengov-books-rag-agent"
MODEL_DEPLOYMENT = "rag-agent-runtime-gpt-4-1-mini"
INSTRUCTIONS = (
    "You are a grounded assistant for the TE-003 reference corpus.\n"
    "Use the knowledge base tool for every user question and never answer "
    "from model knowledge alone.\n"
    "If the knowledge base does not contain the answer, respond exactly: "
    "I don't know.\n"
    "Cite the retrieved sources in every grounded answer."
)


@dataclass(frozen=True)
class RetrievalArm:
    arm_id: str
    maximum_documents: int

    @property
    def knowledge_base_name(self) -> str:
        return f"books-knowledge-base-{self.arm_id}"

    @property
    def connection_name(self) -> str:
        return f"books-knowledge-base-{self.arm_id}-mcp"

    @property
    def mcp_url(self) -> str:
        return (
            f"{SEARCH_ENDPOINT}/knowledgebases/{self.knowledge_base_name}/mcp"
            f"?api-version={SEARCH_API_VERSION}"
        )


ARMS = (
    RetrievalArm("topk4", 4),
    RetrievalArm("topk1", 1),
)


def _request(
    credential: TokenCredential,
    *,
    method: str,
    url: str,
    scope: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import requests

    token = credential.get_token(scope).token
    response = requests.request(
        method,
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=60,
    )
    response.raise_for_status()
    if not response.content:
        return {}
    return response.json()


def _get_optional(
    credential: TokenCredential, *, url: str, scope: str
) -> dict[str, Any] | None:
    import requests

    try:
        return _request(
            credential, method="GET", url=url, scope=scope
        )
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise


def _contains(actual: object, expected: object) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _contains(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(
                _contains(actual_item, expected_item)
                for actual_item, expected_item in zip(actual, expected)
            )
        )
    return actual == expected


def _knowledge_base_body(arm: RetrievalArm) -> dict[str, Any]:
    return {
        "name": arm.knowledge_base_name,
        "description": (
            "TE-009/TE-013 extractive five-book RAG arm with a versioned "
            f"{arm.maximum_documents}-document retrieval limit."
        ),
        "outputMode": "extractiveData",
        "knowledgeSources": [{"name": "books-knowledge-source"}],
        "retrievalReasoningEffort": {"kind": "minimal"},
        "retrieveDefaults": {
            "maxRuntimeInSeconds": 30,
            "maxOutputDocuments": arm.maximum_documents,
            "maxOutputSizeInTokens": 8000,
        },
    }


def _connection_body(arm: RetrievalArm) -> dict[str, Any]:
    return {
        "properties": {
            "audience": "https://search.azure.com/",
            "authType": "ProjectManagedIdentity",
            "category": "RemoteTool",
            "group": "GenericProtocol",
            "isDefault": False,
            "isSharedToAll": False,
            "metadata": {"ApiType": "Azure"},
            "target": arm.mcp_url,
            "useWorkspaceManagedIdentity": False,
        }
    }


def _agent_definition(arm: RetrievalArm) -> dict[str, Any]:
    return {
        "kind": "prompt",
        "model": MODEL_DEPLOYMENT,
        "instructions": INSTRUCTIONS,
        "tools": [
            {
                "type": "mcp",
                "server_label": f"knowledge-base-{arm.arm_id}",
                "server_url": arm.mcp_url,
                "allowed_tools": {"tool_names": ["knowledge_base_retrieve"]},
                "require_approval": "never",
                "project_connection_id": arm.connection_name,
            }
        ],
    }


def _matching_agent_version(
    project: AIProjectClient, arm: RetrievalArm
) -> Any | None:
    expected = _agent_definition(arm)
    for version in project.agents.list_versions(
        AGENT_NAME, order="desc", include_drafts=False
    ):
        metadata = version.metadata or {}
        definition = version.definition.as_dict()
        if (
            metadata.get("retrieval_arm") == arm.arm_id
            and metadata.get("max_output_documents")
            == str(arm.maximum_documents)
            and definition == expected
        ):
            return version
    return None


def provision(credential: TokenCredential) -> dict[str, Any]:
    project = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)
    results: dict[str, Any] = {"arms": []}
    for arm in ARMS:
        knowledge_base_url = (
            f"{SEARCH_ENDPOINT}/knowledgebases/{arm.knowledge_base_name}"
            f"?api-version={SEARCH_API_VERSION}"
        )
        knowledge_base_body = _knowledge_base_body(arm)
        knowledge_base = _get_optional(
            credential,
            url=knowledge_base_url,
            scope="https://search.azure.com/.default",
        )
        if knowledge_base is None or not _contains(
            knowledge_base, knowledge_base_body
        ):
            _request(
                credential,
                method="PUT",
                url=knowledge_base_url,
                scope="https://search.azure.com/.default",
                body=knowledge_base_body,
            )
            knowledge_base = _request(
                credential,
                method="GET",
                url=knowledge_base_url,
                scope="https://search.azure.com/.default",
            )
        connection_url = (
            "https://management.azure.com"
            f"{PROJECT_RESOURCE_ID}/connections/{arm.connection_name}"
            f"?api-version={ARM_API_VERSION}"
        )
        connection_body = _connection_body(arm)
        connection = _get_optional(
            credential,
            url=connection_url,
            scope="https://management.azure.com/.default",
        )
        if connection is None or not _contains(connection, connection_body):
            _request(
                credential,
                method="PUT",
                url=connection_url,
                scope="https://management.azure.com/.default",
                body=connection_body,
            )
            connection = _request(
                credential,
                method="GET",
                url=connection_url,
                scope="https://management.azure.com/.default",
            )
        agent = _matching_agent_version(project, arm)
        if agent is None:
            agent = project.agents.create_version(
                agent_name=AGENT_NAME,
                definition=_agent_definition(arm),
                description=(
                    f"Immutable {arm.arm_id} retrieval arm for TE-009/TE-013 "
                    "using priced GPT-4.1 Mini."
                ),
                metadata={
                    "lifecycle_step": "execute",
                    "evidence_status": "proposed",
                    "retrieval_arm": arm.arm_id,
                    "max_output_documents": str(arm.maximum_documents),
                    "model_release": "foundry-model-release.v2",
                },
            )
        results["arms"].append(
            {
                "arm_id": arm.arm_id,
                "maximum_documents": arm.maximum_documents,
                "knowledge_base": knowledge_base["name"],
                "knowledge_base_etag": knowledge_base["@odata.etag"],
                "connection_id": connection["id"],
                "agent_name": agent.name,
                "agent_version": agent.version,
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    args = parser.parse_args()
    result = provision(AzureCliCredential())
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        from pathlib import Path

        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
