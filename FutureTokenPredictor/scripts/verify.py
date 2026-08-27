#!/usr/bin/env python3
"""verify.py -- make the repo tamper-EVIDENT, not just tamper-discouraged.

The scripts already make the *happy path* honest: set_status.py refuses to write
'done'/'baseline', and checkpoint.py only writes 'done' on a real green test. But
nothing physically stops an agent (or a person) from going AROUND the scripts and
hand-editing a file. This script closes that gap by checking the two ways a repo
can be quietly doctored:

  1. DOC DRIFT -- someone hand-edited a rendered board. kanban.md / mindmap.md
     are generated artifacts; they must match what render.py would produce from
     state.yaml right now. Any difference means a doc was edited by hand.

  2. FORGED PROVENANCE -- someone hand-wrote an un-earned status into state.yaml.
       * 'done' is only legitimate when checkpoint.py wrote it, which always
         records a checkpoint_tag AND creates that git tag. So: every 'done' block
         must carry a checkpoint_tag, and that tag must actually exist in the repo.
       * 'baseline' is only legitimate when adopt.py wrote it from a real green
         adoption run, which always stamps origin: adopted. So: every 'baseline'
         block must have origin 'adopted'.

Run it standalone, or as a pre-commit / CI gate -- it exits non-zero on any
problem and prints exactly what is wrong, so a doctored board or a forged status
fails the commit instead of sneaking in.

    python scripts/verify.py            # consult git for real tags
    python scripts/verify.py --no-git   # field/doc checks only (tags not checked)
"""
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".weave" / "state.yaml"
KANBAN = ROOT / "kanban.md"
MINDMAP = ROOT / "mindmap.md"

# render.py lives next to this file and its render_* functions are pure (no I/O
# at import time), so we reuse them for a byte-faithful drift check.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import render  # noqa: E402


def check_docs(state, kanban_text, mindmap_text):
    """Every rendered doc must match what render.py would produce now."""
    problems = []
    if kanban_text != render.render_kanban(state):
        problems.append(
            "kanban.md does not match state.yaml -- it was hand-edited "
            "(re-run scripts/render.py)"
        )
    if mindmap_text != render.render_mindmap(state):
        problems.append(
            "mindmap.md does not match state.yaml -- it was hand-edited "
            "(re-run scripts/render.py)"
        )
    return problems


def check_provenance(state, tags):
    """Earned-status check. tags=None means 'do not consult git'."""
    problems = []
    for b in state["blocks"]:
        bid, status = b["id"], b["status"]
        if status == "done":
            tag = b.get("checkpoint_tag")
            if not tag:
                problems.append(
                    f"{bid} is 'done' but has no checkpoint_tag -- a real "
                    f"checkpoint always records one (forged status)"
                )
            elif tags is not None and tag not in tags:
                problems.append(
                    f"{bid} is 'done' with checkpoint_tag '{tag}' but no such "
                    f"git tag exists -- the checkpoint was never made (forged)"
                )
        elif status == "baseline":
            if b.get("origin") != "adopted":
                problems.append(
                    f"{bid} is 'baseline' but was not produced by adoption "
                    f"(missing origin: adopted) -- forged status"
                )
    return problems


def verify(state, kanban_text, mindmap_text, tags=None):
    """Aggregate every honesty check into one list of problems."""
    return check_docs(state, kanban_text, mindmap_text) + check_provenance(state, tags)


def git_tags():
    """Real tag names in this repo, or None if git is unavailable."""
    try:
        r = subprocess.run(["git", "tag"], cwd=ROOT,
                           capture_output=True, text=True)
    except FileNotFoundError:
        return None
    if r.returncode != 0:
        return None
    return [t for t in r.stdout.splitlines() if t.strip()]


def main(argv) -> int:
    use_git = "--no-git" not in argv
    state = yaml.safe_load(STATE.read_text())
    kanban_text = KANBAN.read_text() if KANBAN.exists() else ""
    mindmap_text = MINDMAP.read_text() if MINDMAP.exists() else ""
    tags = git_tags() if use_git else None

    problems = verify(state, kanban_text, mindmap_text, tags)
    if not problems:
        print("verify: OK -- board and statuses match the evidence")
        return 0
    print("verify: FAILED -- the repo has been doctored around the scripts:",
          file=sys.stderr)
    for p in problems:
        print(f"  - {p}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
