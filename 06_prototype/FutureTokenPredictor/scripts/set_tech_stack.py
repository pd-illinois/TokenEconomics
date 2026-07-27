#!/usr/bin/env python3
"""Record product.tech_stack into state.yaml, then validate and re-render.

Pass the tech stack as a JSON array of {name, role} objects:

    python scripts/set_tech_stack.py '[{"name":"Python","role":"backend language"}]'
"""
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".weave" / "state.yaml"


def set_tech_stack(state: dict, stack: list) -> dict:
    """Pure transform: set product.tech_stack. Raises on bad input."""
    if not isinstance(stack, list):
        raise ValueError("tech_stack must be a JSON array")
    for entry in stack:
        if not isinstance(entry, dict):
            raise ValueError("each tech_stack entry must be an object")
        for k in ("name", "role"):
            if not str(entry.get(k, "")).strip():
                raise ValueError(f"tech_stack entry missing required field: {k}")
    state.setdefault("product", {})
    state["product"]["tech_stack"] = stack
    return state


def main(argv) -> int:
    if len(argv) != 2:
        print('usage: set_tech_stack.py \'[{"name":"...","role":"..."}]\'',
              file=sys.stderr)
        return 2
    try:
        stack = json.loads(argv[1])
    except json.JSONDecodeError as e:
        print(f"refused: not valid JSON: {e}", file=sys.stderr)
        return 2
    try:
        state = set_tech_stack(yaml.safe_load(STATE.read_text()), stack)
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
    print("tech_stack recorded")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
