#!/usr/bin/env python3
"""Adopt an existing project as labelled blocks -- honestly.

weaveadopt's write path. The AGENT proposes the seams to adopt (a JSON list of
modules); this SCRIPT runs the project's full test suite for real and decides
each seam's status from that evidence. A seam can only be 'baseline' if the
suite was genuinely green AND the seam has a test file on disk. Everything else
is 'unverified'. No flag an agent can pass changes that.

  adopt.py --runner vitest '[{"title":"auth","module":"src/auth","test":"src/auth/auth.test.ts"}]'

It runs the whole suite once via run_tests.py --all --expect pass (so a RED
suite is detected honestly and can never produce a baseline), appends the
blocks, validates, re-renders, and tags weave/baseline iff the suite was green.

Re-running is safe: a seam whose module (or title, when there is no module) is
already an adopted block is skipped, so an aborted run never duplicates blocks.
Pass --replace to clear existing origin:adopted blocks and re-adopt from
scratch. Run this with the PROJECT's own interpreter (its venv) so the suite
sees the project's dependencies -- it shells out with sys.executable.
"""
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".weave" / "state.yaml"
RUN_TESTS = ROOT / "scripts" / "run_tests.py"


def next_block_id(existing_ids):
    nums = [int(i[1:]) for i in existing_ids if i[1:].isdigit()]
    return f"B{(max(nums) + 1) if nums else 0}"


def classify(suite_green: bool, has_test: bool) -> str:
    """The honest tier. baseline requires real green evidence AND a test."""
    return "baseline" if (suite_green and has_test) else "unverified"


def make_block(block_id, seam, runner, suite_green, test_exists):
    """Pure: build one adopted block dict from a proposed seam."""
    status = classify(suite_green, test_exists)
    block = {
        "id": block_id,
        "title": seam["title"],
        "goal": f"Adopted seam: {seam.get('module', seam['title'])}.",
        "status": status,
        "origin": "adopted",
        "runner": runner,
        "depends_on": [],
    }
    if seam.get("module"):
        block["module"] = seam["module"]
    if seam.get("test"):
        block["acceptance_test"] = seam["test"]
    return block


def adopt(state, seams, runner, suite_green, test_exists_fn, replace=False):
    """Pure transform: append a block per proposed seam. Returns new state.

    Idempotent by seam key (module, or title when there is no module): a seam
    already present as an adopted block is skipped, so re-running after an
    aborted run never duplicates blocks. With replace=True, existing
    origin:adopted blocks are cleared first so the seams are re-adopted cleanly.
    """
    blocks = state["blocks"]
    if replace:
        state["blocks"] = blocks = [b for b in blocks if b.get("origin") != "adopted"]
    seen = {
        (b.get("module") or b.get("title"))
        for b in blocks
        if b.get("origin") == "adopted"
    }
    ids = [b["id"] for b in blocks]
    for seam in seams:
        key = seam.get("module") or seam.get("title")
        if key in seen:
            continue
        bid = next_block_id(ids)
        ids.append(bid)
        seen.add(key)
        test_exists = bool(seam.get("test")) and test_exists_fn(seam["test"])
        blocks.append(make_block(bid, seam, runner, suite_green, test_exists))
    return state


def _suite_cmd(runner):
    # --expect pass: exit 0 ONLY on a genuinely green suite. Without it,
    # run_tests.py treats "no expectation" as "any verdict is fine" and exits 0
    # even when red -- which would forge a baseline.
    return [sys.executable, str(RUN_TESTS), "--runner", runner, "--all", "--expect", "pass"]


def _run_suite(runner) -> bool:
    r = subprocess.run(_suite_cmd(runner), cwd=ROOT)
    return r.returncode == 0


def main(argv) -> int:
    args = argv[1:]
    runner = "pytest"
    replace = False
    if "--replace" in args:
        args.remove("--replace")
        replace = True
    if "--runner" in args:
        i = args.index("--runner")
        runner = args[i + 1]
        del args[i:i + 2]
    if len(args) != 1:
        print('usage: adopt.py [--runner pytest|vitest|jest] [--replace] \'[{"title":...,"module":...,"test":...}]\'',
              file=sys.stderr)
        return 2
    try:
        seams = json.loads(args[0])
        assert isinstance(seams, list) and seams
    except (ValueError, AssertionError):
        print("refused: argument must be a non-empty JSON list of seams", file=sys.stderr)
        return 2

    print(f"running the full {runner} suite once for a real baseline...")
    suite_green = _run_suite(runner)
    print(f"suite verdict: {'green' if suite_green else 'RED'}")

    original_text = STATE.read_text()
    state = yaml.safe_load(original_text)
    pre_ids = {b["id"] for b in state["blocks"]}
    state = adopt(state, seams, runner, suite_green,
                  lambda t: (ROOT / t).exists(), replace=replace)
    n_added = len({b["id"] for b in state["blocks"]} - pre_ids)
    n_skipped = len(seams) - n_added
    STATE.write_text(yaml.safe_dump(state, sort_keys=False, allow_unicode=True, width=80))

    # Validate/render against the freshly-written state. If either fails (e.g.
    # a missing dependency like jsonschema), roll the board back so we never
    # leave a half-written state.yaml on disk.
    for script in ("validate.py", "render.py"):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / script)], cwd=ROOT)
        if r.returncode != 0:
            STATE.write_text(original_text)
            print("adopt rolled back: validate/render failed; state.yaml restored.",
                  file=sys.stderr)
            return r.returncode

    if n_skipped and not replace:
        print(f"adopted {n_added} new block(s); skipped {n_skipped} already-adopted "
              f"seam(s). Pass --replace to re-adopt from scratch.")
    else:
        print(f"adopted {n_added} new block(s).")

    if suite_green:
        subprocess.run(["git", "add", "-A"], cwd=ROOT)
        subprocess.run(["git", "commit", "-m", "adopt: baseline"], cwd=ROOT)
        subprocess.run(["git", "tag", "weave/baseline"], cwd=ROOT)
        print("tagged weave/baseline (suite was green)")
    else:
        print("NOT tagging baseline -- suite is red; all seams adopted as unverified.")
        print("record the failures in .weave/lessons.yaml and get to green first.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
