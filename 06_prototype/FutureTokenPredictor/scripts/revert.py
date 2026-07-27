#!/usr/bin/env python3
"""Revert to a block's checkpoint tag, then record why into lessons.yaml.

This is the only sanctioned way to roll a block back. It refuses unless the
block has a real checkpoint tag, warns about dependents that were built on top
of it, restores the worktree from the tag, and appends the reason to the active
lessons-log that weavemvp reads before its next decomposition.
"""
import datetime as dt
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".weave" / "state.yaml"
LESSONS = ROOT / ".weave" / "lessons.yaml"

# A dependent is "orphaned" by a revert only if real work was built on it.
BUILT = {"building", "in-review", "done"}


def find_block(state, block_id):
    for b in state["blocks"]:
        if b["id"] == block_id:
            return b
    raise ValueError(f"unknown block: {block_id}")


def tag_for(state, block_id):
    tag = find_block(state, block_id).get("checkpoint_tag")
    if not tag:
        raise ValueError(f"{block_id} has no checkpoint tag -- nothing to revert to")
    return tag


def dependents(state, block_id):
    """Every block that transitively depends on block_id."""
    out = set()
    changed = True
    while changed:
        changed = False
        for b in state["blocks"]:
            if b["id"] in out or b["id"] == block_id:
                continue
            deps = set(b.get("depends_on", []))
            if block_id in deps or (deps & out):
                out.add(b["id"])
                changed = True
    return out


def orphaned_dependents(state, block_id):
    deps = dependents(state, block_id)
    return sorted(
        b["id"] for b in state["blocks"]
        if b["id"] in deps and b["status"] in BUILT
    )


def make_lesson(block_id, reason, tag, date=None):
    return {
        "block": block_id,
        "reason": reason.strip(),
        "reverted_to": tag,
        "date": date or dt.date.today().isoformat(),
    }


def record_lesson(lessons, entry):
    lessons.setdefault("lessons", []).append(entry)
    return lessons


def main(argv):
    if len(argv) != 3:
        print('usage: revert.py <block_id> "<reason>"', file=sys.stderr)
        return 2
    _, block_id, reason = argv
    if not reason.strip():
        print("refused: a reason is required to revert", file=sys.stderr)
        return 2

    state = yaml.safe_load(STATE.read_text())
    try:
        tag = tag_for(state, block_id)
    except ValueError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2

    orphans = orphaned_dependents(state, block_id)
    if orphans:
        print(
            f"WARNING: reverting {block_id} orphans dependents: "
            f"{', '.join(orphans)}",
            file=sys.stderr,
        )

    r = subprocess.run(["git", "checkout", tag, "--", "."], cwd=ROOT)
    if r.returncode != 0:
        print(f"refused: git checkout {tag} failed", file=sys.stderr)
        return r.returncode

    lessons = yaml.safe_load(LESSONS.read_text()) if LESSONS.exists() else {}
    lessons = record_lesson(lessons or {}, make_lesson(block_id, reason, tag))
    LESSONS.write_text(
        yaml.safe_dump(lessons, sort_keys=False, allow_unicode=True, width=80)
    )

    for script in ("validate.py", "render.py"):
        rr = subprocess.run([sys.executable, str(ROOT / "scripts" / script)], cwd=ROOT)
        if rr.returncode != 0:
            return rr.returncode

    print(f"reverted {block_id} to {tag}; lesson recorded in {LESSONS.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
