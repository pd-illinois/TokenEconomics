"""Read complete Azure Cost Management export revisions using Entra identity."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
from datetime import date, datetime, timezone
from urllib.parse import urlparse

from costgov.studio_lifecycle import digest


def validate_source(config: dict) -> dict:
    if config.get("schema_version") != "studio-billing-source.v1":
        raise ValueError("Unsupported billing source configuration")
    parsed = urlparse(config.get("account_url", ""))
    if (parsed.scheme != "https" or not (parsed.hostname or "").endswith(".blob.core.windows.net")
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.port not in (None, 443) or parsed.path not in ("", "/")):
        raise ValueError("Billing source must be a server-configured Azure Blob account")
    if not config.get("container") or not config.get("prefix") or not config.get("export_id"):
        raise ValueError("Billing container, export prefix and export identity are required")
    if config.get("binding", {}).get("schema_version") != "workload-resource-binding.v1":
        raise ValueError("A versioned workload resource binding is required")
    return config


def _credential():
    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

    if os.environ.get("CONTAINER_APP_NAME") or os.environ.get("IDENTITY_ENDPOINT"):
        return ManagedIdentityCredential(client_id=os.environ.get("AZURE_CLIENT_ID") or None)
    return DefaultAzureCredential(exclude_interactive_browser_credential=True)


def read_export(manifest: dict, read_blob, *, config: dict, manifest_name: str) -> list[dict]:
    """Verify every partition before normalizing; incomplete revisions fail closed."""
    validate_source(config)
    export = manifest.get("exportConfig", {})
    delivery = manifest.get("deliveryConfig", {})
    if (export.get("resourceId", "").lower() != config["export_id"].lower()
            or export.get("type") != "ActualCost"
            or export.get("granularity") != "Daily"
            or delivery.get("fileFormat") != "Csv"
            or delivery.get("compressionMode") not in ("Gzip", "None", None)):
        raise ValueError("Manifest does not match the configured daily ActualCost export")
    blobs = manifest.get("blobs")
    if not isinstance(blobs, list) or not blobs or manifest.get("blobCount") != len(blobs):
        raise ValueError("Export partition manifest is incomplete")
    folder = manifest_name.rsplit("/", 1)[0] + "/"
    if not folder.startswith(config["prefix"]):
        raise ValueError("Export manifest is outside the configured prefix")
    rows, names, byte_count = [], set(), 0
    for blob in blobs:
        name = blob.get("blobName", "")
        if not name.startswith(folder) or ".." in name.split("/") or name in names:
            raise ValueError("Invalid or duplicated export partition")
        names.add(name)
        data = read_blob(name)
        if len(data) != blob.get("byteCount"):
            raise ValueError("Export partition size does not match manifest")
        byte_count += len(data)
        text = gzip.decompress(data) if delivery.get("compressionMode") == "Gzip" else data
        partition = list(csv.DictReader(io.StringIO(text.decode("utf-8-sig"))))
        if len(partition) != blob.get("dataRowCount") or any(None in row for row in partition):
            raise ValueError("Export partition rows do not match manifest")
        rows.extend(partition)
    if len(rows) != manifest.get("dataRowCount") or byte_count != manifest.get("byteCount"):
        raise ValueError("Export totals do not match complete manifest")
    return rows


def sync_export(config: dict, store, *, run_date: str, container=None, now=None):
    """Import one complete revision for the selected run's month; never sum revisions."""
    from costgov.billing_snapshots import normalize_rows
    from azure.core.exceptions import AzureError

    validate_source(config)
    selected_day = date.fromisoformat(run_date)
    instant = now or datetime.now(timezone.utc)
    owned_client, credential = None, None
    try:
        if container is None:
            from azure.storage.blob import BlobServiceClient

            credential = _credential()
            owned_client = BlobServiceClient(
                config["account_url"], credential=credential,
                connection_timeout=10, read_timeout=60, retry_total=2,
            )
            container = owned_client.get_container_client(config["container"])
        prefix = f"{config['prefix']}{selected_day:%Y%m}01-"
        manifests = [blob for blob in container.list_blobs(name_starts_with=prefix)
                     if blob.name.endswith("/manifest.json")]
        if not manifests:
            raise ValueError("No billing export has been delivered for this batch's month")
        latest = max(manifests, key=lambda blob: (blob.last_modified, blob.name))
        manifest_bytes = container.download_blob(latest.name).readall()
        manifest = json.loads(manifest_bytes)
        raw_hashes = {}

        def read_blob(name):
            data = container.download_blob(name).readall()
            raw_hashes[name] = hashlib.sha256(data).hexdigest()
            return data

        rows = read_export(manifest, read_blob, config=config, manifest_name=latest.name)
        run_info = manifest["runInfo"]
        start = date.fromisoformat(run_info["startDate"][:10])
        end = date.fromisoformat(run_info["endDate"][:10])
        if start.strftime("%Y-%m") != selected_day.strftime("%Y-%m") or end < start:
            raise ValueError("Manifest period does not match the selected billing month")
        snapshot = normalize_rows(
            rows, source={
                "kind": "azure_cost_management_export", "source_id": config["export_id"],
                "revision": run_info["runId"],
                "content_hash": digest({"manifest": manifest, "partition_hashes": raw_hashes}),
                "manifest_blob": latest.name,
                "delivered_at": latest.last_modified.isoformat(),
                "submitted_at": run_info.get("submittedTime"),
            },
            period_start=start.isoformat(), period_end=end.isoformat(),
            allowed_resource_ids=[item["resource_id"] for item in config["binding"]["resources"]],
            retrieved_at=instant.isoformat(),
        )
        return store.append(snapshot)
    except AzureError as exc:
        raise ValueError("Azure billing export could not be read. Check identity, container read access and source delivery.") from exc
    finally:
        if owned_client is not None:
            owned_client.close()
        if credential is not None:
            credential.close()
