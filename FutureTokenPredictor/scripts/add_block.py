#!/usr/bin/env python3
"""Append a block to state.yaml, then validate and re-render.

The sanctioned write path for the weavemvp stage. weavemvp decides the
decomposition; this script performs the deterministic, schema-checked write.
Pass the block as one JSON object:

    python scripts/add_block.py '{"id":"B5","title":"...","goal":"...",
      "depends_on":["B4"],"contract_test":"tests/test_b5_contract.py"}'
"""
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".weave" / "state.yaml"


def add_block(state: dict, block: dict) -> dict:
    """Pure transform: append a block. Defaults status to 'todo'.

    Raises ValueError on missing id/title/goal or a duplicate id.
    """
    for k in ("id", "title", "goal"):
        if not str(block.get(k, "")).strip():
            raise ValueError(f"block missing required field: {k}")
    block.setdefault("status", "todo")
    existing = {b["id"] for b in state.get("blocks", [])}
    if block["id"] in existing:
        raise ValueError(f"block id already exists: {block['id']}")
    state.setdefault("blocks", []).append(block)
    return state


def main(argv) -> int:
    if len(argv) != 2:
        print('usage: add_block.py \'{"id":...,"title":...,"goal":...}\'',
              file=sys.stderr)
        return 2
    try:
        block = json.loads(argv[1])
    except json.JSONDecodeError as e:
        print(f"refused: not valid JSON: {e}", file=sys.stderr)
        return 2
    try:
        state = add_block(yaml.safe_load(STATE.read_text()), block)
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
    print(f"block {block['id']} added")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
