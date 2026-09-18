"""Bounded, Entra-only repair of the existing books retrieval configuration."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
from uuid import uuid4

import requests
from azure.identity import AzureCliCredential


SUBSCRIPTION = "a91cc1ba-bd19-43a7-90ea-120794c0fbc6"
ROOT_ID = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-tokeneconomics/providers"
SEARCH = "https://search-xbk6ickycmp22.search.windows.net"
ACCOUNT_ID = ROOT_ID + "/Microsoft.CognitiveServices/accounts/ai-account-xbk6ickycmp22"
EMBED_ENDPOINT = "https://ai-account-xbk6ickycmp22.cognitiveservices.azure.com"
STORAGE_ID = ROOT_ID + "/Microsoft.Storage/storageAccounts/stxbk6ickycmp22"
STORAGE = "https://stxbk6ickycmp22.blob.core.windows.net"
CONTAINER = "rag-usage-evidence"
SEARCH_VERSION = "2026-08-01-preview"
PATHS = (
    "indexes/books",
    "knowledgesources/books-knowledge-source",
    "knowledgebases/books-knowledge-base-topk1",
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def clean(value):
    if isinstance(value, dict):
        return {
            key: ("REDACTED" if key.lower() in {
                "apikey", "accesstoken", "secret", "clientsecret", "connectionstring"
            } and item else clean(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [clean(item) for item in value]
    return value


def config(value):
    return {key: copy.deepcopy(item) for key, item in value.items()
            if not key.startswith("@odata.")}


def cli(*args):
    result = subprocess.run(
        ["az.cmd", *args, "--only-show-errors", "-o", "json"],
        capture_output=True, text=True, timeout=90,
    )
    if result.returncode:
        raise RuntimeError("azure_cli_failed")
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--evidence-dir", required=True)
    args = parser.parse_args()
    evidence = Path(args.evidence_dir)
    evidence.mkdir(parents=True, exist_ok=False)
    credential = AzureCliCredential(process_timeout=40)
    session = requests.Session()
    manifest = {
        "schema_version": "rag-cloud-repair.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "apply" if args.apply else "inspect",
        "api_version": SEARCH_VERSION,
        "changes": [],
        "lifecycle": ["execute", "evaluate", "reconcile"],
        "runtime_hybrid_proof": False,
        "acceptance": "not_evaluated",
        "inference_invoked": False,
    }

    def save(name, value):
        with (evidence / name).open("x", encoding="utf-8") as stream:
            json.dump(clean(value), stream, indent=2)

    def request(method, url, audience, *, expected=(200,), headers=None, **kwargs):
        auth = {"Authorization": "Bearer " + credential.get_token(audience + "/.default").token}
        auth.update(headers or {})
        response = session.request(
            method, url, headers=auth, timeout=(20, 60), **kwargs,
        )
        if response.status_code not in expected:
            raise RuntimeError(f"azure_http_{response.status_code}")
        return response

    def arm(path):
        return request("GET", "https://management.azure.com" + path,
                       "https://management.azure.com",
                       params={"api-version": "2025-06-01"}).json()

    def search_get(path):
        response = request("GET", f"{SEARCH}/{path}", "https://search.azure.com",
                           params={"api-version": SEARCH_VERSION})
        body = response.json()
        etag = response.headers.get("ETag") or body.get("@odata.etag")
        if not etag:
            raise RuntimeError("missing_etag")
        return {"etag": etag, "sha256": digest(config(clean(body))), "config": body}

    try:
        account = cli("account", "show", "--query", "{id:id}")
        if account["id"] != SUBSCRIPTION:
            raise RuntimeError("wrong_subscription")
        deployment = arm(ACCOUNT_ID + "/deployments/text-embed")
        model = deployment["properties"]["model"]
        if (model["name"], model["version"]) != ("text-embedding-3-small", "1"):
            raise RuntimeError("embedding_model_mismatch")
        if deployment["properties"]["provisioningState"] != "Succeeded":
            raise RuntimeError("embedding_deployment_unavailable")
        ingest = Path("rag/ingest.py").read_text(encoding="utf-8")
        if 'EMBED_DIM = 1536' not in ingest or '"text-embed"' not in ingest:
            raise RuntimeError("ingestion_contract_changed")
        identity = cli(
            "search", "service", "show", "-g", "rg-tokeneconomics",
            "-n", "search-xbk6ickycmp22", "--query", "identity",
        )
        roles = cli(
            "role", "assignment", "list", "--assignee", identity["principalId"],
            "--scope", ACCOUNT_ID, "--include-inherited",
            "--query", "[].{id:id,role:roleDefinitionName,scope:scope,principalId:principalId}",
        )
        if not any(item["role"] == "Cognitive Services OpenAI User" for item in roles):
            raise RuntimeError("embedding_rbac_missing")
        manifest["embedding"] = {
            "resource_id": ACCOUNT_ID, "endpoint": EMBED_ENDPOINT,
            "deployment": "text-embed", "model": model, "dimensions": 1536,
            "ingestion_source_sha256": hashlib.sha256(ingest.encode()).hexdigest(),
            "match_basis": "ingestion source default and historical documented model/version; live deployment",
            "historical_vector_reembedding_comparison": "not_performed",
            "search_identity": identity, "existing_roles": roles,
            "new_role_assignments": [],
        }
        before = {path: search_get(path) for path in PATHS}
        save("before.search.v1.json", before)
        desired_index = config(before[PATHS[0]]["config"])
        fields = {item["name"]: item for item in desired_index["fields"]}
        if fields["content_vector"]["dimensions"] != 1536:
            raise RuntimeError("vector_dimensions_mismatch")
        if fields["content_vector"]["vectorSearchProfile"] != "vprofile":
            raise RuntimeError("vector_profile_mismatch")
        if not fields["content"]["searchable"]:
            raise RuntimeError("lexical_field_not_searchable")
        vectorizer = {
            "name": "books-text-embed-v1",
            "kind": "azureOpenAI",
            "azureOpenAIParameters": {
                "resourceUri": EMBED_ENDPOINT,
                "deploymentId": "text-embed",
                "modelName": "text-embedding-3-small",
            },
        }
        vector_search = desired_index["vectorSearch"]
        existing_vectorizers = vector_search.get("vectorizers", [])
        if existing_vectorizers:
            existing_parameters = existing_vectorizers[0].get("azureOpenAIParameters", {})
            if (len(existing_vectorizers) != 1
                    or existing_vectorizers[0].get("name") != vectorizer["name"]
                    or existing_vectorizers[0].get("kind") != vectorizer["kind"]
                    or any(existing_parameters.get(key) != value
                           for key, value in vectorizer["azureOpenAIParameters"].items())
                    or existing_parameters.get("apiKey")
                    or existing_parameters.get("authIdentity")):
                raise RuntimeError("different_vectorizer_configuration_present")
        else:
            vector_search["vectorizers"] = [vectorizer]
        profile = next(item for item in vector_search["profiles"] if item["name"] == "vprofile")
        if profile.get("vectorizer") not in (None, vectorizer["name"]) or profile["algorithm"] != "hnsw":
            raise RuntimeError("profile_configuration_changed")
        profile["vectorizer"] = vectorizer["name"]
        desired_source = config(before[PATHS[1]]["config"])
        parameters = desired_source["searchIndexParameters"]
        if (parameters["searchIndexName"], parameters["semanticConfigurationName"]) != ("books", "sem"):
            raise RuntimeError("knowledge_source_binding_changed")
        if parameters["searchFields"] not in (
                [{"name": "content"}], [{"name": "content"}, {"name": "content_vector"}]):
            raise RuntimeError("knowledge_source_fields_changed")
        parameters["searchFields"] = [{"name": "content"}, {"name": "content_vector"}]
        manifest["before"] = {path: {key: value[key] for key in ("etag", "sha256")}
                              for path, value in before.items()}
        manifest["proposed"] = {PATHS[0]: digest(desired_index), PATHS[1]: digest(desired_source)}
        if not args.apply:
            save("manifest.v1.json", manifest)
            print(canonical(manifest))
            return
        for path, body in ((PATHS[0], desired_index), (PATHS[1], desired_source)):
            if body == config(before[path]["config"]):
                continue
            request("PUT", f"{SEARCH}/{path}", "https://search.azure.com",
                    params={"api-version": SEARCH_VERSION},
                    headers={"If-Match": before[path]["etag"]}, json=body,
                    expected=(200, 201, 204))
            manifest["changes"].append(path)
            save(f"mutation-{len(manifest['changes'])}.v1.json",
                 {"path": path, "prior_etag": before[path]["etag"], "desired_sha256": digest(body)})
        after = {path: search_get(path) for path in PATHS}
        save("after.search.v1.json", after)
        # Azure returns default/null fields absent from the submitted vectorizer.
        observed_index = config(after[PATHS[0]]["config"])
        observed_vectorizers = observed_index["vectorSearch"].pop("vectorizers")
        expected_without_vectorizers = copy.deepcopy(desired_index)
        expected_without_vectorizers["vectorSearch"].pop("vectorizers")
        if observed_index != expected_without_vectorizers:
            raise RuntimeError("unintended_index_change")
        if len(observed_vectorizers) != 1:
            raise RuntimeError("vectorizer_readback_mismatch")
        observed_parameters = observed_vectorizers[0]["azureOpenAIParameters"]
        if any(observed_parameters.get(key) != value
               for key, value in vectorizer["azureOpenAIParameters"].items()):
            raise RuntimeError("embedding_binding_readback_mismatch")
        if observed_parameters.get("apiKey") or observed_parameters.get("authIdentity"):
            raise RuntimeError("unexpected_vectorizer_auth")
        if config(after[PATHS[1]]["config"]) != desired_source:
            raise RuntimeError("unintended_source_change")
        if after[PATHS[2]] != before[PATHS[2]]:
            raise RuntimeError("knowledge_base_changed_concurrently")
        manifest["after"] = {path: {key: value[key] for key in ("etag", "sha256")}
                             for path, value in after.items()}
        manifest["config_readback_verified"] = True
        storage = cli(
            "storage", "account", "show", "-g", "rg-tokeneconomics", "-n", "stxbk6ickycmp22",
            "--query", "{id:id,allowBlobPublicAccess:allowBlobPublicAccess,allowSharedKeyAccess:allowSharedKeyAccess}",
        )
        if storage["allowBlobPublicAccess"] is not False or storage["allowSharedKeyAccess"] is not False:
            raise RuntimeError("storage_security_precondition_failed")
        principal = cli("ad", "signed-in-user", "show", "--query", "{id:id}")
        storage_roles = cli(
            "role", "assignment", "list", "--assignee", principal["id"],
            "--scope", STORAGE_ID + "/blobServices/default/containers/" + CONTAINER,
            "--include-inherited", "--include-groups",
            "--query", "[].{id:id,role:roleDefinitionName,scope:scope,principalId:principalId}",
        )
        blob_headers = {"x-ms-version": "2023-11-03"}
        container_url = f"{STORAGE}/{CONTAINER}"
        existing = request("HEAD", container_url, "https://storage.azure.com",
                           params={"restype": "container"}, headers=blob_headers,
                           expected=(200, 404))
        save("before.storage.v1.json", {
            "account": storage, "container_exists": existing.status_code == 200,
            "etag": existing.headers.get("ETag"),
        })
        if existing.status_code == 404:
            request("PUT", container_url, "https://storage.azure.com",
                    params={"restype": "container"},
                    headers={**blob_headers, "If-None-Match": "*"},
                    expected=(201,))
            manifest["changes"].append("containers/" + CONTAINER)
        properties = request("HEAD", container_url, "https://storage.azure.com",
                             params={"restype": "container"}, headers=blob_headers)
        if properties.headers.get("x-ms-blob-public-access"):
            raise RuntimeError("container_is_public")
        probe = {
            "schema_version": "rag-evidence-storage-probe.v1",
            "probe_id": str(uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "question_count": 0, "model_invocations": 0,
            "purpose": "create-only-storage-validation",
        }
        payload = canonical(probe).encode()
        probe_name = f"probes/{probe['probe_id']}.json"
        probe_url = container_url + "/" + probe_name
        put_headers = {**blob_headers, "x-ms-blob-type": "BlockBlob",
                       "Content-Type": "application/json", "If-None-Match": "*"}
        created = request("PUT", probe_url, "https://storage.azure.com",
                          headers=put_headers, data=payload, expected=(201,))
        save("probe.created.v1.json", {
            "probe_name": probe_name, "etag": created.headers.get("ETag"),
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
        blocked = request("PUT", probe_url, "https://storage.azure.com",
                          headers=put_headers, data=payload, expected=(409, 412))
        if blocked.headers.get("x-ms-error-code") not in ("BlobAlreadyExists", "ConditionNotMet"):
            raise RuntimeError("unexpected_duplicate_probe_error")
        readback = request("GET", probe_url, "https://storage.azure.com", headers=blob_headers)
        if readback.content != payload:
            raise RuntimeError("probe_readback_mismatch")
        manifest["storage"] = {
            "account": storage, "account_url": STORAGE, "container": CONTAINER,
            "container_etag": properties.headers.get("ETag"),
            "public_access": "disabled", "auth": "Entra ID",
            "probe_name": probe_name, "probe_etag": created.headers.get("ETag"),
            "probe_sha256": hashlib.sha256(payload).hexdigest(),
            "overwrite_rejected_status": blocked.status_code,
            "readback_verified": True, "retention_lock": "not_configured",
            "local_principal_id": principal["id"], "existing_roles": storage_roles,
            "new_role_assignments": [],
        }
        save("after.storage.v1.json", manifest["storage"])
        save("manifest.v1.json", manifest)
        print(canonical(manifest))
    except Exception as exc:
        safe_error = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        manifest["status"] = "incomplete"
        manifest["error_code"] = safe_error
        save("failure.v1.json", manifest)
        print(canonical({"status": "incomplete", "error_code": safe_error,
                         "changes": manifest["changes"], "evidence": str(evidence)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
