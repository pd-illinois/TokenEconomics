#!/usr/bin/env python3
"""Record a block's decision record into state.yaml, then validate and re-render.

This is the sanctioned write path for a block's *provenance and purpose* -- why
the module/file structure was chosen (rationale), the architectural pattern it
follows, its Big-O / performance characterization (complexity), and where the
code came from (provenance). checkpoint.py gates `done` on a complete decision,
so this is what lets an agent PROVE the why instead of burying it in comments.

    python scripts/set_decision.py B0 \
        --rationale "Pure transform + thin CLI keeps the logic unit-testable" \
        --pattern   "pure function + argv shim" \
        --complexity "O(n) over blocks; single pass, no nested scans" \
        --provenance authored

provenance must be one of: authored | generated | adapted-from:<source>
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".weave" / "state.yaml"

FIELDS = ("rationale", "pattern", "complexity", "provenance")
PROVENANCE_RE = re.compile(r"^(authored|generated|adapted-from:.+)$")


def set_decision(state: dict, block_id: str, decision: dict) -> dict:
    """Pure transform: attach a complete decision record to one block.

    Raises ValueError on an unknown block, an empty field, or a provenance that
    is not authored / generated / adapted-from:<source>.
    """
    for key in FIELDS:
        if not str(decision.get(key, "")).strip():
            raise ValueError(f"decision field must not be empty: {key}")
    if not PROVENANCE_RE.match(decision["provenance"].strip()):
        raise ValueError(
            "provenance must be 'authored', 'generated', or 'adapted-from:<source>'"
        )
    for b in state["blocks"]:
        if b["id"] == block_id:
            b["decision"] = {k: decision[k].strip() for k in FIELDS}
            return state
    raise ValueError(f"unknown block: {block_id}")


def main(argv) -> int:
    parser = argparse.ArgumentParser(prog="set_decision.py", add_help=True)
    parser.add_argument("block_id")
    parser.add_argument("--rationale", required=True)
    parser.add_argument("--pattern", required=True)
    parser.add_argument("--complexity", required=True)
    parser.add_argument("--provenance", required=True)
    ns = parser.parse_args(argv[1:])

    decision = {
        "rationale": ns.rationale,
        "pattern": ns.pattern,
        "complexity": ns.complexity,
        "provenance": ns.provenance,
    }
    try:
        state = set_decision(yaml.safe_load(STATE.read_text()), ns.block_id, decision)
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
    print(f"decision recorded for {ns.block_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
