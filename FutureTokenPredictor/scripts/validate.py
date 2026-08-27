#!/usr/bin/env python3
"""Validate .weave/state.yaml against the schema. Exit 0 = valid, 1 = invalid."""
import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".weave" / "state.yaml"
SCHEMA = ROOT / ".weave" / "schema" / "state.schema.json"

# A block is "satisfied as a dependency" only once it carries real evidence:
# done (built + green + tagged) or baseline (adopted on a real green run).
DONE_STATES = {"done", "baseline"}


def find_cycle(blocks):
    """Return a cycle in the `depends_on` graph as a list of block ids, or None
    if the graph is acyclic.

    This is the deadlock-prevention gate: a directed ACYCLIC graph always has a
    source (a block with no unbuilt deps), so a complete build order is
    guaranteed to exist. A cycle is the ONLY way a block set can deadlock --
    nobody can start -- so we reject it before any building begins. DFS with a
    GRAY/BLACK colouring; a back-edge to a GRAY node is a cycle.
    """
    graph = {b["id"]: list(b.get("depends_on", [])) for b in blocks}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in graph}
    stack = []

    def visit(nid):
        color[nid] = GRAY
        stack.append(nid)
        for dep in graph.get(nid, []):
            if dep not in color:
                continue  # unknown deps are caught by the cross-field check
            if color[dep] == GRAY:
                return stack[stack.index(dep):] + [dep]
            if color[dep] == WHITE:
                found = visit(dep)
                if found:
                    return found
        stack.pop()
        color[nid] = BLACK
        return None

    for nid in graph:
        if color[nid] == WHITE:
            found = visit(nid)
            if found:
                return found
    return None


def ready_blocks(state):
    """Every block weaveaction may legitimately pick up next: status `todo` with
    every `depends_on` already done/baseline. Build order falls out of this --
    you never start a consumer before its producers carry real evidence."""
    by_id = {b["id"]: b for b in state["blocks"]}
    out = []
    for b in state["blocks"]:
        if b.get("status") != "todo":
            continue
        if all(by_id.get(d, {}).get("status") in DONE_STATES
               for d in b.get("depends_on", [])):
            out.append(b)
    return out


def blocked_report(state):
    """Map each `todo` block that is NOT ready to the list of dependency ids
    still blocking it. The raw material for an honest "why is nothing moving?"
    message instead of a silent spin."""
    by_id = {b["id"]: b for b in state["blocks"]}
    report = {}
    for b in state["blocks"]:
        if b.get("status") != "todo":
            continue
        unmet = [d for d in b.get("depends_on", [])
                 if by_id.get(d, {}).get("status") not in DONE_STATES]
        if unmet:
            report[b["id"]] = unmet
    return report


def is_stuck(state):
    """True iff there is `todo` work left, NOTHING is ready to pick up, and
    NOTHING is in flight (building/in-review). That is a genuine dead end -- a
    dependency was reverted or never built -- and weaveaction must STOP and
    report it (see blocked_report) rather than loop forever looking for a block
    it can start. On an acyclic graph this can only happen via a reverted dep."""
    todos = [b for b in state["blocks"] if b.get("status") == "todo"]
    if not todos:
        return False
    if ready_blocks(state):
        return False
    in_flight = [b for b in state["blocks"]
                 if b.get("status") in ("building", "in-review")]
    return not in_flight


def main() -> int:
    state = yaml.safe_load(STATE.read_text())
    schema = json.loads(SCHEMA.read_text())

    errors = sorted(Draft202012Validator(schema).iter_errors(state),
                    key=lambda e: list(e.path))
    if errors:
        for e in errors:
            loc = "/".join(str(p) for p in e.path) or "(root)"
            print(f"INVALID {loc}: {e.message}", file=sys.stderr)
        return 1

    # Cross-field check the schema cannot express: deps must be real block ids.
    ids = {b["id"] for b in state["blocks"]}
    for b in state["blocks"]:
        for dep in b.get("depends_on", []):
            if dep not in ids:
                print(f"INVALID {b['id']}: depends_on '{dep}' is not a known block",
                      file=sys.stderr)
                return 1

    # CYCLE GATE: the graph must be a DAG, or no deadlock-free build order
    # exists. Reject a cycle before any block is built.
    cycle = find_cycle(state["blocks"])
    if cycle:
        print(f"INVALID: depends_on has a cycle: {' -> '.join(cycle)}",
              file=sys.stderr)
        return 1

    print("state.yaml valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
