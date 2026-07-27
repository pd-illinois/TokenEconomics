#!/usr/bin/env python3
"""Record product.idea into state.yaml, then validate and re-render.

This is the ONLY sanctioned write path for the weaveidea stage. The prompt
agent never edits state.yaml by hand -- it calls this script so the change
goes through schema validation and a deterministic re-render of the views.
"""
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".weave" / "state.yaml"


def set_idea(state: dict, idea: str) -> dict:
    """Pure transform: set product.idea (trimmed). Raises on empty input."""
    if not idea or not idea.strip():
        raise ValueError("idea must not be empty")
    state.setdefault("product", {})
    state["product"]["idea"] = idea.strip()
    return state


def main(argv) -> int:
    if len(argv) != 2:
        print('usage: set_idea.py "<idea text>"', file=sys.stderr)
        return 2
    try:
        state = set_idea(yaml.safe_load(STATE.read_text()), argv[1])
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
    print("idea recorded")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
