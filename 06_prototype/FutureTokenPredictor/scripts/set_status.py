#!/usr/bin/env python3
"""Flip a block's status through a LEGAL transition, then validate and re-render.

Used by weaveaction for the reversible steps of the trust loop
(todo -> building -> in-review). Marking a block 'done' is deliberately NOT
allowed here -- that is irreversible and must go through scripts/checkpoint.py,
which gates on a green acceptance test and writes a git tag.
"""
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".weave" / "state.yaml"

# Allowed status transitions. 'done' is reachable only via checkpoint.py;
# 'baseline' is reachable only via adopt.py (real green evidence at adoption).
# Neither is a legal set_status target -- the spine forbids labelling code
# verified without evidence.
LEGAL = {
    "todo": {"building"},
    "building": {"in-review", "todo", "unverified", "done"},
    "in-review": {"building", "done"},
    "done": {"reverted"},
    "reverted": {"todo", "building"},
    "unverified": {"building"},
    "baseline": {"building", "reverted"},
}


def find_block(state: dict, block_id: str) -> dict:
    for b in state["blocks"]:
        if b["id"] == block_id:
            return b
    raise ValueError(f"unknown block: {block_id}")


def set_status(state: dict, block_id: str, new_status: str) -> dict:
    """Pure transform with transition legality. Raises on an illegal jump."""
    b = find_block(state, block_id)
    cur = b["status"]
    if new_status == cur:
        return state
    if new_status not in LEGAL.get(cur, set()):
        raise ValueError(f"illegal transition {cur} -> {new_status} for {block_id}")
    b["status"] = new_status
    return state


def main(argv) -> int:
    if len(argv) != 3:
        print("usage: set_status.py <block_id> <status>", file=sys.stderr)
        return 2
    _, block_id, new_status = argv
    if new_status == "done":
        print("refused: use checkpoint.py to mark a block done", file=sys.stderr)
        return 2
    if new_status == "baseline":
        print("refused: 'baseline' is set by adopt.py from real test evidence",
              file=sys.stderr)
        return 2
    try:
        state = set_status(yaml.safe_load(STATE.read_text()), block_id, new_status)
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
    print(f"{block_id} -> {new_status}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
