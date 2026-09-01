from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from costgov.experiment_contracts import (
    EVIDENCE_CATEGORIES,
    EXPERIMENT_SCHEMA_VERSION,
    OBSERVATION_UNIT,
    EvidenceApplicability,
    EvidenceReference,
    EvidenceStatus,
    ExperimentArm,
    ExperimentFactor,
    ExperimentManifest,
    ExperimentManifestStore,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "data" / "contracts" / "experiment-manifest.v1.schema.json"
REFERENCE_MANIFEST_PATH = (
    ROOT / "data" / "experiments" / "rag-policy-comparison.v1.json"
)


def _reference(
    category: str,
    *,
    applicability: EvidenceApplicability = EvidenceApplicability.APPLICABLE,
) -> EvidenceReference:
    return EvidenceReference(
        category=category,
        evidence_id=f"{category}-evidence",
        revision="v1",
        applicability=applicability,
        status=(
            EvidenceStatus.SOURCED
            if applicability is EvidenceApplicability.APPLICABLE
            else EvidenceStatus.BLOCKED
        ),
        authority="test-authority",
        content_hash="a" * 64 if applicability is EvidenceApplicability.APPLICABLE else None,
        location=f"repo:{category}.json",
        reason=(
            None
            if applicability is EvidenceApplicability.APPLICABLE
            else "Not required by this route."
        ),
    )


def _policy(name: str, content_hash: str) -> EvidenceReference:
    return EvidenceReference(
        category="policy_candidate",
        evidence_id=name,
        revision="v1",
        applicability=EvidenceApplicability.APPLICABLE,
        status=EvidenceStatus.PROPOSED,
        authority="tokengov",
        content_hash=content_hash,
        location=f"inline:{name}",
    )


def _manifest() -> ExperimentManifest:
    factors = (
        ExperimentFactor.from_value("execution.routing_mode", "quality"),
        ExperimentFactor.from_value("execution.semantic_cache.enabled", False),
    )
    return ExperimentManifest(
        schema_version=EXPERIMENT_SCHEMA_VERSION,
        experiment_id="rag-policy-comparison",
        revision="v1",
        created_at="2026-08-31T16:00:00+00:00",
        observation_unit=OBSERVATION_UNIT,
        evidence=tuple(_reference(category) for category in sorted(EVIDENCE_CATEGORIES)),
        arms=(
            ExperimentArm(
                arm_id="premium-baseline",
                role="baseline",
                policy_candidate=_policy("premium-baseline-policy", "b" * 64),
                factors=factors,
            ),
            ExperimentArm(
                arm_id="governed-candidate",
                role="candidate",
                policy_candidate=_policy("governed-policy", "c" * 64),
                factors=(
                    ExperimentFactor.from_value(
                        "execution.routing_mode", "balanced"
                    ),
                    ExperimentFactor.from_value(
                        "execution.semantic_cache.enabled", True
                    ),
                ),
            ),
        ),
    )


def test_machine_readable_schema_matches_runtime_contract():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["properties"]["schema_version"]["const"] == (
        EXPERIMENT_SCHEMA_VERSION
    )
    assert schema["properties"]["observation_unit"]["const"] == OBSERVATION_UNIT
    categories = set(
        schema["$defs"]["evidenceReference"]["properties"]["category"]["enum"]
    )
    assert categories == EVIDENCE_CATEGORIES
    assert (
        schema["$defs"]["policyCandidateReference"]["allOf"][1]["properties"][
            "category"
        ]["const"]
        == "policy_candidate"
    )


def test_manifest_round_trips_and_exposes_machine_readable_arm_differences():
    manifest = _manifest()

    reopened = ExperimentManifest.from_dict(
        json.loads(manifest.to_canonical_json())
    )

    assert reopened == manifest
    assert reopened.differences_from_baseline("governed-candidate") == (
        {
            "path": "policy_candidate",
            "baseline": manifest.arms[0].policy_candidate.to_dict(),
            "arm": manifest.arms[1].policy_candidate.to_dict(),
        },
        {
            "path": "execution.routing_mode",
            "baseline": "quality",
            "arm": "balanced",
        },
        {
            "path": "execution.semantic_cache.enabled",
            "baseline": False,
            "arm": True,
        },
    )
    assert reopened.differences_from_baseline("premium-baseline") == ()


def test_manifest_requires_complete_shared_evidence_and_comparable_arms():
    manifest = _manifest()
    with pytest.raises(ValueError, match="each required category"):
        replace(manifest, evidence=manifest.evidence[:-1])

    candidate = manifest.arms[1]
    with pytest.raises(ValueError, match="same factor paths"):
        replace(
            manifest,
            arms=(
                manifest.arms[0],
                replace(candidate, factors=candidate.factors[:-1]),
            ),
        )

    with pytest.raises(ValueError, match="must differ"):
        replace(
            manifest,
            arms=(
                manifest.arms[0],
                replace(
                    candidate,
                    policy_candidate=manifest.arms[0].policy_candidate,
                    factors=manifest.arms[0].factors,
                ),
            ),
        )

    policy_only_candidate = replace(
        candidate,
        factors=manifest.arms[0].factors,
    )
    policy_only_manifest = replace(
        manifest,
        arms=(manifest.arms[0], policy_only_candidate),
    )
    assert policy_only_manifest.differences_from_baseline(
        policy_only_candidate.arm_id
    ) == (
        {
            "path": "policy_candidate",
            "baseline": manifest.arms[0].policy_candidate.to_dict(),
            "arm": candidate.policy_candidate.to_dict(),
        },
    )


def test_non_applicable_or_unavailable_evidence_is_explicit():
    with pytest.raises(ValueError, match="requires a reason"):
        _reference(
            "commercial_rate_cards",
            applicability=EvidenceApplicability.NON_APPLICABLE,
        ).__class__(
            category="commercial_rate_cards",
            evidence_id="commercial-evidence",
            revision="v1",
            applicability=EvidenceApplicability.NON_APPLICABLE,
            status=EvidenceStatus.SOURCED,
            authority="test-authority",
        )


def test_manifest_store_is_append_only_and_detects_tampering(tmp_path):
    manifest = _manifest()
    store = ExperimentManifestStore(tmp_path)

    created = store.append(manifest)
    reopened = store.get(manifest.experiment_id, manifest.revision)

    assert reopened == created
    assert len(created.content_hash) == 64
    with pytest.raises(FileExistsError):
        store.append(manifest)

    path = next(tmp_path.glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["manifest"]["arms"][1]["factors"][0]["value"] = "cost"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        store.get(manifest.experiment_id, manifest.revision)


def test_reference_rag_manifest_pins_shared_revisions_and_real_source_hashes():
    manifest = ExperimentManifest.from_dict(
        json.loads(REFERENCE_MANIFEST_PATH.read_text(encoding="utf-8"))
    )

    assert manifest.experiment_id == "rag-policy-comparison"
    assert len(manifest.evidence) == len(EVIDENCE_CATEGORIES)
    assert manifest.differences_from_baseline("governed-candidate") == (
        {
            "path": "policy_candidate",
            "baseline": manifest.arms[0].policy_candidate.to_dict(),
            "arm": manifest.arms[1].policy_candidate.to_dict(),
        },
        {
            "path": "execution.routing_mode",
            "baseline": "quality",
            "arm": "balanced",
        },
        {
            "path": "execution.semantic_cache.enabled",
            "baseline": False,
            "arm": True,
        },
        {
            "path": "execution.context.prune",
            "baseline": False,
            "arm": True,
        },
    )

    for evidence in manifest.evidence:
        if evidence.location and evidence.location.startswith("repo:"):
            path = ROOT / evidence.location.removeprefix("repo:")
            assert path.is_file(), evidence.location
            assert hashlib.sha256(path.read_bytes()).hexdigest() == evidence.content_hash
