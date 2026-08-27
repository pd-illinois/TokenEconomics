#!/usr/bin/env python3
"""Fingerprint a repo so weaveadopt knows what it is looking at.

Deterministic, read-only. The agent does NOT guess the language or test runner
-- this script reads the real project files and reports them. Pure functions are
separated from filesystem access so they can be tested without a real repo.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def runner_from_package_json(pkg: dict):
    """Pick a JS/TS test runner from a parsed package.json, or None."""
    dev = {**pkg.get("devDependencies", {}), **pkg.get("dependencies", {})}
    test_script = (pkg.get("scripts", {}) or {}).get("test", "") or ""
    for runner in ("vitest", "jest"):
        if runner in dev or runner in test_script:
            return runner
    return None


def detect(listing, package_json_text=None, has_git=False):
    """Pure fingerprint.

    listing: iterable of top-level names in the repo root.
    package_json_text: contents of package.json if present, else None.
    Returns a dict: language, runner, is_brownfield, has_tests.
    """
    names = set(listing)
    fp = {
        "language": None,
        "runner": None,
        "has_git": bool(has_git),
        "has_tests": False,
        "is_brownfield": False,
    }
    if package_json_text is not None:
        fp["language"] = "typescript" if "tsconfig.json" in names else "javascript"
        try:
            fp["runner"] = runner_from_package_json(json.loads(package_json_text))
        except (ValueError, TypeError):
            fp["runner"] = None
    elif any(n in names for n in ("pyproject.toml", "setup.py", "requirements.txt")):
        fp["language"] = "python"
        fp["runner"] = "pytest"

    fp["has_tests"] = any(n in names for n in ("tests", "test", "__tests__"))
    has_source = any(n in names for n in ("src", "lib", "app")) or fp["language"] is not None
    fp["is_brownfield"] = bool(has_source and (fp["has_tests"] or has_git))
    return fp


def main(argv) -> int:
    names = [p.name for p in ROOT.iterdir()]
    pkg = ROOT / "package.json"
    pkg_text = pkg.read_text() if pkg.exists() else None
    has_git = (ROOT / ".git").exists()
    fp = detect(names, pkg_text, has_git)
    print(json.dumps(fp, indent=2))
    if fp["is_brownfield"]:
        print("\nbrownfield detected -> run /weaveadopt (or copilot @weaveadopt)")
    else:
        print("\nno existing project detected -> greenfield, run /weaveidea")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
