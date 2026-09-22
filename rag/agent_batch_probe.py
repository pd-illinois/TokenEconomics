"""Read-only, independently checked managed MCP/Search hybrid configuration."""

from __future__ import annotations

import re
import time

from rag import agent_batch as b

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_SEARCH = "https://search-xbk6ickycmp22.search.windows.net"
_API_VERSIONS = {"2026-05-01-preview", "2026-08-01-preview"}
_FIELDS = {
    "schema_version", "knowledge_base", "knowledge_source", "index", "vectorizer",
    "vector_field", "query_type", "semantic_configuration", "embedding_model",
    "embedding_deployment", "embedding_resource", "dimensions", "content_hash",
    "text_fields", "max_runtime_seconds", "max_output_documents", "max_output_tokens",
    "knowledge_base_etag", "knowledge_source_etag", "index_etag",
}


def _name(value):
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ValueError("Invalid configuration identity")
    return value


def validate_retrieval(value):
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise ValueError("Retrieval evidence allowlist violated")
    if (value["schema_version"] != "managed-hybrid-config.v1" or value["query_type"] != "semantic"
            or type(value["dimensions"]) is not int or not 1 <= value["dimensions"] <= 4096
            or value["content_hash"] != b._digest({k: v for k, v in value.items() if k != "content_hash"})):
        raise ValueError("Retrieval configuration binding invalid")
    scalar_names = {"knowledge_base", "knowledge_source", "index", "vectorizer", "vector_field",
                    "semantic_configuration", "embedding_model", "embedding_deployment"}
    for key in scalar_names:
        _name(value[key])
    if not isinstance(value["text_fields"], list) or not value["text_fields"]:
        raise ValueError("Text field identity required")
    for name in value["text_fields"]:
        _name(name)
    for key, maximum in (("max_runtime_seconds", 600), ("max_output_documents", 100),
                         ("max_output_tokens", 128000)):
        if type(value[key]) is not int or not 1 <= value[key] <= maximum:
            raise ValueError("Explicit retrieval bounds required")
    for key in ("knowledge_base_etag", "knowledge_source_etag", "index_etag"):
        if not isinstance(value[key], str) or not re.fullmatch(r"""[A-Za-z0-9"'W/=+._:-]{1,256}""", value[key]):
            raise ValueError("Configuration ETag required")
    if not isinstance(value["embedding_resource"], str) or not re.fullmatch(
            r"https://[a-zA-Z0-9-]+\.(?:openai\.azure\.com|cognitiveservices\.azure\.com)/?",
            value["embedding_resource"]):
        raise ValueError("Embedding resource must be an Azure endpoint")
    return value


def _get(credential, url, scope):
    import requests

    # These callers construct only known Azure inventory URLs, never tool URLs.
    with requests.Session() as session:
        session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
        started = time.monotonic()
        with session.get(
            url, headers={"Authorization": f"Bearer {credential.get_token(scope).token}"},
            timeout=(10, 15), allow_redirects=False, stream=True,
        ) as response:
            if response.status_code != 200:
                raise ValueError("Configuration read unavailable")
            data = bytearray()
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                if len(data) > 2_000_000 or time.monotonic() - started > 30:
                    raise ValueError("Configuration response exceeds bound")
            import json
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError("Invalid configuration response")
            return result


def verify_hybrid(kb, source, index):
    """Only metadata is examined; neither a search query nor an embedding runs."""
    sources = kb.get("knowledgeSources")
    if (not isinstance(sources, list) or len(sources) != 1
            or sources[0].get("name") != source.get("name")
            or kb.get("outputMode") != "extractiveData"
            or b._mapping(kb.get("retrievalReasoningEffort")).get("kind") != "minimal"):
        raise ValueError("Knowledge base configuration unsupported")
    parameters = b._mapping(source.get("searchIndexParameters"))
    if (source.get("kind") != "searchIndex"
            or parameters.get("searchIndexName") != index.get("name")
            or parameters.get("queryType") not in {None, "semantic"}):
        raise ValueError("Source semantic query configuration unverified")
    semantic = b._mapping(index.get("semantic"))
    semantic_name = parameters.get("semanticConfigurationName") or semantic.get("defaultConfiguration")
    configurations = semantic.get("configurations", [])
    matches = [row for row in configurations if row.get("name") == semantic_name]
    if len(matches) != 1 or not b._mapping(matches[0].get("prioritizedFields")).get("prioritizedContentFields"):
        raise ValueError("Semantic text configuration unavailable")
    # Current Search preview selects query-time hybrid through searchFields and
    # semanticConfigurationName, rather than a source-level queryType property.
    search_fields = parameters.get("searchFields")
    if not isinstance(search_fields, list):
        raise ValueError("Explicit text and vector search fields required")
    selected_fields = {row.get("name") for row in search_fields if isinstance(row, dict)}
    content_fields = {
        row.get("fieldName") for row in matches[0]["prioritizedFields"]["prioritizedContentFields"]
    }
    lexical = [row for row in index.get("fields", []) if row.get("name") in selected_fields & content_fields
               and row.get("type") == "Edm.String" and row.get("searchable") is True]
    if not lexical:
        raise ValueError("Search source must select a searchable semantic text field")
    vectors = b._mapping(index.get("vectorSearch"))
    fields = [row for row in index.get("fields", [])
              if row.get("type") == "Collection(Edm.Single)" and row.get("searchable") is True
              and row.get("name") in selected_fields
              and row.get("vectorSearchProfile") and row.get("dimensions")]
    candidates = []
    for field in fields:
        profiles = [row for row in vectors.get("profiles", []) if row.get("name") == field["vectorSearchProfile"]]
        if len(profiles) != 1:
            continue
        vectorizers = [row for row in vectors.get("vectorizers", []) if row.get("name") == profiles[0].get("vectorizer")]
        if len(vectorizers) != 1 or vectorizers[0].get("kind") != "azureOpenAI":
            continue
        algorithm = [row for row in vectors.get("algorithms", []) if row.get("name") == profiles[0].get("algorithm")]
        if len(algorithm) != 1 or algorithm[0].get("kind") not in {"hnsw", "exhaustiveKnn"}:
            continue
        candidates.append((field, vectorizers[0]))
    if len(candidates) != 1:
        raise ValueError("Unique query-time vectorizer required")
    field, vectorizer = candidates[0]
    embedding = b._mapping(vectorizer.get("azureOpenAIParameters"))
    if embedding.get("apiKey"):
        raise ValueError("Managed identity vectorizer required")
    evidence = {
        "schema_version": "managed-hybrid-config.v1",
        "knowledge_base": kb.get("name"), "knowledge_source": source.get("name"),
        "index": index.get("name"), "vectorizer": vectorizer.get("name"),
        "vector_field": field.get("name"), "query_type": "semantic",
        "semantic_configuration": semantic_name, "embedding_model": embedding.get("modelName"),
        "embedding_deployment": embedding.get("deploymentId"),
        "embedding_resource": embedding.get("resourceUri"), "dimensions": field.get("dimensions"),
        "text_fields": sorted(row["name"] for row in lexical),
        "knowledge_base_etag": kb.get("@odata.etag"), "knowledge_source_etag": source.get("@odata.etag"),
        "index_etag": index.get("@odata.etag"),
        "max_runtime_seconds": b._mapping(kb.get("retrieveDefaults")).get("maxRuntimeInSeconds"),
        "max_output_documents": b._mapping(kb.get("retrieveDefaults")).get("maxOutputDocuments"),
        "max_output_tokens": b._mapping(kb.get("retrieveDefaults")).get("maxOutputSizeInTokens"),
    }
    evidence["content_hash"] = b._digest(evidence)
    return validate_retrieval(evidence)


def probe(config):
    from azure.ai.projects import AIProjectClient
    from rag.agent_batch_measurement import private_call

    with private_call(), b._credential() as credential:
        with AIProjectClient(
            endpoint=config["project_endpoint"], credential=credential,
            retry_total=0, connection_timeout=10, read_timeout=15,
            logging_enable=False, tracing_enable=False,
        ) as project:
            agent = b._mapping(project.agents.get(config["agent_name"]))
            version = config["agent_version"] or b._mapping(b._mapping(agent.get("versions")).get("latest")).get("version")
            if not isinstance(version, str) or not re.fullmatch(r"[1-9][0-9]*", version):
                raise ValueError("Pinned version required")
            pinned = b._mapping(project.agents.get_version(config["agent_name"], version))
            definition = b._mapping(pinned.get("definition"))
            deployment = b._identifier(definition.get("model"))
            model = b._mapping(project.deployments.get(deployment))
            result = {
                "agent_name": config["agent_name"], "agent_version": version,
                "kind": "prompt" if definition.get("kind") == "prompt" else "unsupported",
                "active": agent.get("state") == "enabled" and pinned.get("status") == "active",
                "deployment": deployment, "model": b._identifier(model.get("modelName")),
                "model_version": b._identifier(model.get("modelVersion")),
                "managed_books_mcp": False, "retrieval_mode": "managed_mcp_hybrid_unverified",
            }
            tools = definition.get("tools", [])
            if not isinstance(tools, list) or len(tools) != 1 or tools[0].get("type") != "mcp":
                return result
            tool = tools[0]
            url = tool.get("server_url", "")
            match = re.fullmatch(
                re.escape(_SEARCH) + r"/knowledgebases/(books-knowledge-base(?:-[A-Za-z0-9-]+)?)/mcp\?api-version=([0-9-]+preview)", url)
            if (not match or tool.get("require_approval") != "never"
                    or tool.get("allowed_tools") != {"tool_names": ["knowledge_base_retrieve"]}):
                return result
            api = match[2]
            if api not in _API_VERSIONS:
                return result
            connection_name = _name(tool.get("project_connection_id"))
            connection = b._mapping(project.connections.get(connection_name))
            if (connection.get("target") != url
                    or b._mapping(connection.get("credentials")).get("type") not in {"AAD", "ProjectManagedIdentity"}):
                return result
            resource_id = connection.get("id", "")
            if not re.fullmatch(
                    r"/subscriptions/[a-fA-F0-9-]{36}/resourceGroups/[A-Za-z0-9._()-]+"
                    r"/providers/Microsoft\.CognitiveServices/accounts/[A-Za-z0-9-]+"
                    r"/projects/[A-Za-z0-9_-]+/connections/" + re.escape(connection_name), resource_id, re.I):
                return result
            authority = _get(credential, f"https://management.azure.com{resource_id}?api-version=2025-06-01",
                             "https://management.azure.com/.default")
            properties = b._mapping(authority.get("properties"))
            if (properties.get("authType") != "ProjectManagedIdentity"
                    or properties.get("target") != url or properties.get("category") != "RemoteTool"
                    or properties.get("audience", "").rstrip("/") != "https://search.azure.com"):
                return result
            result["managed_books_mcp"] = True
            try:
                scope = "https://search.azure.com/.default"
                kb = _get(credential, f"{_SEARCH}/knowledgebases/{match[1]}?api-version={api}", scope)
                sources = kb.get("knowledgeSources", [])
                if len(sources) != 1:
                    return result
                source_name = _name(sources[0].get("name"))
                source = _get(credential, f"{_SEARCH}/knowledgesources/{source_name}?api-version={api}", scope)
                index_name = _name(b._mapping(source.get("searchIndexParameters")).get("searchIndexName"))
                index = _get(credential, f"{_SEARCH}/indexes/{index_name}?api-version={api}", scope)
                result["retrieval_evidence"] = verify_hybrid(kb, source, index)
                result["retrieval_mode"] = "managed_mcp_hybrid_verified"
            except Exception:
                pass  # Unavailable configuration is an explicit unverified blocker.
            return result
