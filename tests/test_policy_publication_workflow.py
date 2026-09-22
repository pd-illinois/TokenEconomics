from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_publication_workflow_triggers_on_reviewed_policy_pushes():
    workflow = (
        ROOT / ".github" / "workflows" / "publish-tokengov-policy.yml"
    ).read_text(encoding="utf-8")

    assert "push:" in workflow
    assert "branches:\n      - main" in workflow
    assert "- data/policies/*.json" in workflow
    assert "- data/policy_reviews/*.json" in workflow
    assert "workflow_dispatch:" in workflow
    assert "python scripts/resolve_policy_review.py" in workflow
    assert '--before "${{ github.event.before }}"' in workflow
    assert '--after "${{ github.sha }}"' in workflow
    assert '"${{ needs.resolve.outputs.policy_path }}"' in workflow
    assert '--expected-etag "${{ needs.resolve.outputs.expected_etag }}"' in workflow
