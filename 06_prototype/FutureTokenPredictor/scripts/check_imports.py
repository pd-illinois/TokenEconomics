#!/usr/bin/env python3
"""check_imports.py -- the architecture-boundary check, as EVIDENCE.

A clean MVPWeaver repo has two independent layers:

  * src/mvpweaver/  -- the installer/CLI that stamps the scaffold into a repo.
  * scripts/        -- the deterministic plumbing that runs INSIDE a stamped
                       repo (state writes, validation, rendering, the gates).

These must not import each other: the installer must not depend on a project's
runtime plumbing, and the plumbing must not depend on the installer. That is an
architecture rule, and like every rule in MVPWeaver it is worthless as a claim
-- so this script proves it with a real exit code. It is a deterministic text
scan (no import side effects): cheap, hermetic, and runnable as a quality check.

    python scripts/check_imports.py        # exit 0 == boundaries intact
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Each rule: files under `layer` must not import any module in `forbidden`.
RULES = [
    {"layer": "src/mvpweaver", "forbidden": ("scripts",)},
    {"layer": "scripts", "forbidden": ("mvpweaver", "src.mvpweaver")},
]


def _imports(text: str, module: str) -> bool:
    """True if `text` imports top-level `module` (import X / from X import ...)."""
    pat = rf"^\s*(?:import\s+{re.escape(module)}\b|from\s+{re.escape(module)}\b)"
    return re.search(pat, text, re.MULTILINE) is not None


def find_cross_imports(root: Path) -> list:
    """Return a list of human-readable boundary violations (empty == clean)."""
    root = Path(root)
    violations = []
    for rule in RULES:
        layer_dir = root / str(rule["layer"])
        if not layer_dir.exists():
            continue
        for py in sorted(layer_dir.rglob("*.py")):
            text = py.read_text(encoding="utf-8", errors="ignore")
            for forbidden in rule["forbidden"]:
                if _imports(text, forbidden):
                    rel = py.relative_to(root).as_posix()
                    violations.append(f"{rel} imports '{forbidden}' (forbidden)")
    return violations


def main() -> int:
    violations = find_cross_imports(ROOT)
    if violations:
        print("INVALID: architecture boundary crossed:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1
    print("architecture: OK -- layers independent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
