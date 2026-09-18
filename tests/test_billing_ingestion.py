import gzip

import pytest

from costgov.billing_ingestion import read_export


CONFIG = {"schema_version": "studio-billing-source.v1", "account_url": "https://test.blob.core.windows.net",
          "container": "costs", "prefix": "costs/", "export_id": "/export/one",
          "binding": {"schema_version": "workload-resource-binding.v1"}}


def sample():
    content = gzip.compress(b"Date,ResourceId,Cost,Currency\n2026-09-09,/resource/one,1,USD\n")
    manifest = {
        "exportConfig": {"resourceId": "/export/one", "type": "ActualCost", "granularity": "Daily"},
        "deliveryConfig": {"fileFormat": "Csv", "compressionMode": "Gzip"},
        "blobCount": 1, "byteCount": len(content), "dataRowCount": 1,
        "blobs": [{"blobName": "costs/revision/part.csv.gz", "byteCount": len(content), "dataRowCount": 1}],
    }
    return manifest, content


def test_complete_manifest_preserves_raw_source_rows():
    manifest, content = sample()
    rows = read_export(manifest, lambda _: content, config=CONFIG, manifest_name="costs/revision/manifest.json")
    assert rows == [{"Date": "2026-09-09", "ResourceId": "/resource/one", "Cost": "1", "Currency": "USD"}]


@pytest.mark.parametrize("field,value", [("blobCount", 2), ("byteCount", 0), ("dataRowCount", 2)])
def test_incomplete_source_revision_is_not_imported(field, value):
    manifest, content = sample()
    manifest[field] = value
    with pytest.raises(ValueError):
        read_export(manifest, lambda _: content, config=CONFIG, manifest_name="costs/revision/manifest.json")


def test_foreign_export_or_partition_is_rejected():
    manifest, content = sample()
    manifest["exportConfig"]["resourceId"] = "/export/other"
    with pytest.raises(ValueError):
        read_export(manifest, lambda _: content, config=CONFIG, manifest_name="costs/revision/manifest.json")
    manifest, content = sample()
    manifest["blobs"][0]["blobName"] = "costs/revision/../other.csv.gz"
    with pytest.raises(ValueError):
        read_export(manifest, lambda _: content, config=CONFIG, manifest_name="costs/revision/manifest.json")


def test_partition_byte_mismatch_is_rejected():
    manifest, content = sample()
    with pytest.raises(ValueError):
        read_export(manifest, lambda _: content + b"x", config=CONFIG, manifest_name="costs/revision/manifest.json")
