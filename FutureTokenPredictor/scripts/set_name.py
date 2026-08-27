#!/usr/bin/env python3
"""Record product.name into state.yaml, then validate and re-render.

This is the ONLY sanctioned write path for setting the product name. The
prompt agent never edits state.yaml by hand -- it calls this script so the
change goes through schema validation and a deterministic re-render of the
views. Mirrors set_idea.py.
"""
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".weave" / "state.yaml"

PLACEHOLDER = "<name your product>"


def set_name(state: dict, name: str) -> dict:
    """Pure transform: set product.name (trimmed). Raises on empty/placeholder."""
    if not name or not name.strip():
        raise ValueError("name must not be empty")
    if name.strip() == PLACEHOLDER:
        raise ValueError("name must not be the placeholder")
    state.setdefault("product", {})
    state["product"]["name"] = name.strip()
    return state


def main(argv) -> int:
    if len(argv) != 2:
        print('usage: set_name.py "<product name>"', file=sys.stderr)
        return 2
    try:
        state = set_name(yaml.safe_load(STATE.read_text()), argv[1])
    except ValueError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    STATE.write_text(
        yaml.safe_dump(state, sort_keys=False, allow_unicode=True, width=80)
    )
    for script in ("validate.py", "render.py"):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / script)], cwd=ROOT)
        if r.returncode != 0:
            return r.returncode
    print("name recorded")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
