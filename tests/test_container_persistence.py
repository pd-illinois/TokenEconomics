import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STORES = {
    "studio_billing_evidence", "studio_decision_state", "studio_governance_evidence",
    "studio_learning_evidence", "studio_lifecycle", "studio_plans", "studio_policy_changes",
    "studio_portability_evidence", "studio_reconciliation_evidence", "studio_reports",
    "studio_response_learning", "studio_runs",
}


def test_all_studio_stores_use_the_same_container_persistence_paths():
    from costgov.studio_health import STUDIO_STATE_STORES
    assert set(STUDIO_STATE_STORES) == STORES
    dockerfile = (ROOT / "Dockerfile").read_text()
    entrypoint = (ROOT / "scripts" / "container-entrypoint.sh").read_text()
    for source in (dockerfile, entrypoint):
        assert set(re.findall(r"^\s+(studio_[a-z_]+)", source, re.MULTILINE)) == STORES
    assert 'state_root="${TOKENECONOMICS_STATE_ROOT:-/data}"' in entrypoint
    assert 'ln -s "$state_root/$path" "/app/$path"' in entrypoint
    assert '[ -z "$(ls -A "$state_root/$path")" ]' in entrypoint


def test_image_updates_do_not_bundle_local_runtime_evidence_or_credentials():
    ignored = set((ROOT / ".dockerignore").read_text().splitlines())
    assert {f"{name}/" for name in STORES} <= ignored
    assert {".env", "**/.env", ".git", "**/.git", ".azure", ".copilot"} <= ignored


def test_new_core_store_paths_are_mounted_in_the_image():
    source = (ROOT / "studio.py").read_text()
    roots = set(re.findall(r'ROOT / "(studio_[a-z_]+)"', source))
    assert roots <= STORES


def test_image_includes_the_brand_icon_but_excludes_other_png_artifacts():
    rules = (ROOT / ".dockerignore").read_text().splitlines()
    assert rules.index("!TokEcoStudio.png") > rules.index("*.png")
    assert (ROOT / "TokEcoStudio.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert "TOKENECONOMICS_FOUNDRY_ONLY=true" in (ROOT / "Dockerfile").read_text()
