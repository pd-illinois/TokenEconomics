#!/usr/bin/env python3
"""Mark a block done -- but only on real evidence -- then tag the worktree.

This is the irreversible, gated step of the trust loop, and it is now the SINGLE
review+checkpoint step: the objective half of code review is folded in and
enforced here, so there is no separate, skippable review. It refuses to mark a
block done unless ALL of these hold:

  1. The block is currently 'in-review' or 'building' (review is folded in).
  2. GREEN GATE -- the block's acceptance_test is actually GREEN (run_tests.py).
  3. REGRESSION GATE -- no already-`done` block has gone red.
  4. REVIEW/SCOPE GATE -- the diff stays inside the block's declared seam
     (its module + tests + interface outputs); an out-of-scope edit is refused.
  5. QUALITY GATE -- the block's new code meets the repo's coding & architecture
     standards (lint, format, types, complexity, security on the block's files;
     coverage + architecture boundaries repo-wide), declared in
     .weave/quality.yaml and proven as real exit codes by quality.py.
  6. DECISION GATE -- the block carries a complete decision record (rationale,
     pattern, complexity, provenance), so its provenance & purpose are proven,
     not buried in comments. Record it with set_decision.py.
  7. A non-empty checkpoint tag is supplied.

Two gates make inter-block dependencies un-fakeable rather than a prompt hint:

  * ORDER GATE -- a consumer cannot be checkpointed before every block it
    depends_on is done/baseline, so build order (producers before consumers)
    is enforced, not merely suggested.
  * CONTRACT GATE -- the block's own contract_test AND each dependency's
    contract_test must still be green, so a producer whose seam broke fails the
    consumer's checkpoint instead of rotting silently.

Only then does it flip the status to done, record the tag, re-render, and
create one git tag covering code + docs + state. An agent cannot fake "done"
or quietly sprawl: the gates are mechanical.

    python scripts/checkpoint.py <block_id> <tag> [--no-scope] [--allow=PATH ...]
"""
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".weave" / "state.yaml"


def find_block(state: dict, block_id: str) -> dict:
    for b in state["blocks"]:
        if b["id"] == block_id:
            return b
    raise ValueError(f"unknown block: {block_id}")


def mark_done(state: dict, block_id: str, tag: str) -> dict:
    """Pure transform: flip a building or in-review block to done and record its tag.

    Raises if the tag is empty or the block is not in a checkpointable status.
    """
    if not tag or not tag.strip():
        raise ValueError("checkpoint tag must not be empty")
    b = find_block(state, block_id)
    if b["status"] not in ("in-review", "building"):
        raise ValueError(
            f"{block_id} must be building or in-review before done (is {b['status']})"
        )
    b["status"] = "done"
    b["checkpoint_tag"] = tag.strip()
    return state


def runner_for(block: dict) -> str:
    """The test runner recorded for a block; pytest is the v1 default.

    The gate MUST honor this. A vitest/jest block gated as pytest would always
    look 'not green' (pytest against a .ts test exits non-zero), so a brownfield
    TS/JS block could never legitimately reach done.
    """
    return (block.get("runner") or "").strip() or "pytest"


def lang_for(block: dict) -> str:
    """The quality-gate language for a block, derived from its runner. A
    pytest block is python; a vitest/jest block is js. The quality config
    (.weave/quality.yaml) tags each check with a `lang`, so this routes a
    block's new code to the right toolchain (ruff/mypy for python, etc.).
    """
    return {"pytest": "python", "vitest": "js", "jest": "js"}.get(
        runner_for(block), "python")


def block_files(block: dict) -> list:
    """The block's OWN files -- its module plus its tests. The quality gate
    scopes per-file checks (lint/format/type/complexity/security) to exactly
    these, so a new block must meet the standard while legacy code is cleaned
    when it is next touched (a ratchet, not a big-bang reformat). Repo-wide
    checks (coverage, architecture) ignore this list and run over everything.
    """
    files = []
    for key in ("module", "acceptance_test", "contract_test"):
        value = block.get(key)
        if value:
            files.append(value)
    return files


def gate_command(root: Path, test: str, runner: str) -> list:
    """argv that runs ONE acceptance test through the evidence harness,
    expecting green. The gate never shells out to a runner directly -- it goes
    through run_tests.py so the verdict is the framework's captured exit code.
    """
    return [
        sys.executable, str(root / "scripts" / "run_tests.py"),
        test, "--runner", runner, "--expect", "pass",
    ]


def regression_targets(state: dict, block_id: str) -> list:
    """Every (id, test, runner) that must STILL be green for this checkpoint to
    be honest: the block being checkpointed FIRST, then every already-`done`
    block that has an acceptance_test recorded.

    A checkpoint that turns a previously-green block red is not a real 'done' --
    re-running the done set here is the regression gate. Done blocks with no
    recorded test are skipped (nothing to re-run); only `done` counts as a
    regression risk -- todo/building/in-review siblings aren't verified yet.
    """
    targets = []
    target = find_block(state, block_id)
    if target.get("acceptance_test"):
        targets.append((target["id"], target["acceptance_test"], runner_for(target)))
    for b in state["blocks"]:
        if b["id"] == block_id:
            continue
        if b.get("status") == "done" and b.get("acceptance_test"):
            targets.append((b["id"], b["acceptance_test"], runner_for(b)))
    return targets


def order_gate(state: dict, block_id: str) -> list:
    """Build order, made un-fakeable: a consumer may not be checkpointed until
    every block it depends_on is already done/baseline. Returns the list of
    unmet dependency ids (empty == the gate passes).

    Dependency direction IS build order -- a block at the top of the graph
    (the RAG agent that consumes search, which consumes the index) is finished
    LAST, against producers that already carry real evidence. This gate refuses
    to let an agent finish a block out of order, so you never end up with a
    'done' consumer standing on a producer that doesn't exist yet.
    """
    by_id = {b["id"]: b for b in state["blocks"]}
    block = by_id[block_id]
    return [dep for dep in block.get("depends_on", [])
            if by_id.get(dep, {}).get("status") not in ("done", "baseline")]


def contract_targets(state: dict, block_id: str) -> list:
    """Every (id, test, runner) contract test that must STILL be green for this
    checkpoint to honour its seams: the block's OWN contract_test first, then
    the contract_test of each block it depends_on.

    This turns `contract_test` from a one-time red proof written at decomposition
    time into an enforced dependency check. A producer whose published seam broke
    fails the consumer's checkpoint here -- which is what stops the revert-then-
    rebuild thrash loop: the break surfaces loudly at the dependent instead of
    rotting silently. Blocks without a recorded contract_test are skipped.
    """
    by_id = {b["id"]: b for b in state["blocks"]}
    target = by_id[block_id]
    targets = []
    if target.get("contract_test"):
        targets.append((target["id"], target["contract_test"], runner_for(target)))
    for dep in target.get("depends_on", []):
        d = by_id.get(dep)
        if d and d.get("contract_test"):
            targets.append((d["id"], d["contract_test"], runner_for(d)))
    return targets


# Files the checkpoint itself owns/regenerates -- always in-scope for any block.
ALWAYS_ALLOWED = {
    "mindmap.md", "kanban.md",
    ".weave/state.yaml", ".weave/lessons.yaml",
}


def _norm(path: str) -> str:
    p = path.strip().replace("\\", "/")
    return p[2:] if p.startswith("./") else p


def _looks_like_path(text: str) -> bool:
    """A heuristic: an interface output that names a real file (not prose)."""
    t = text.strip()
    if not t or " " in t:
        return False
    return "/" in t or "." in t.split("/")[-1]


def allowed_paths(block: dict) -> set:
    """The set of files this block is allowed to touch: its module, its tests,
    any interface outputs that name real paths, plus the always-regenerated
    boards/state. Used to enforce 'minimal diff, scoped to the seam' as a
    MECHANICAL review item -- not just a thing the agent promises.
    """
    allowed = set(ALWAYS_ALLOWED)
    for key in ("module", "acceptance_test", "contract_test"):
        if block.get(key):
            allowed.add(_norm(block[key]))
    for out in block.get("interface", {}).get("outputs", []):
        if _looks_like_path(out):
            allowed.add(_norm(out))
    return allowed


def review_gate(block: dict, changed: list, extra_allowed: set = None) -> list:
    """The objective half of code review, made mechanical: every changed file
    must fall inside the block's declared boundary. Returns a list of
    out-of-scope files (empty == the scope item passes).

    Only enforceable when the block declares a `module`; without one the seam's
    boundary is undefined, so the caller skips the scope check.
    """
    allowed = allowed_paths(block) | (extra_allowed or set())
    return [f for f in changed if _norm(f) not in allowed]


# The fields a complete decision record must carry. Kept in lockstep with the
# `decision` object in state.schema.json and set_decision.py.
DECISION_FIELDS = ("rationale", "pattern", "complexity", "provenance")


def decision_gate(block: dict) -> list:
    """Provenance & purpose, made mechanical: a block cannot reach `done`
    without a COMPLETE decision record. Returns a list of problems (empty ==
    the gate passes). This is the hook that forces an agent to prove WHY the
    code is built the way it is, rather than asserting it in prose.
    """
    d = block.get("decision")
    if not d:
        return [
            f"{block['id']} has no decision record -- record one with "
            f"set_decision.py (rationale, pattern, complexity, provenance) "
            f"before done"
        ]
    missing = [k for k in DECISION_FIELDS if not str(d.get(k, "")).strip()]
    if missing:
        return [
            f"{block['id']} decision record is incomplete: missing "
            f"{', '.join(missing)}"
        ]
    return []


def changed_files(root: Path) -> list:
    """Files changed vs HEAD (tracked, staged or not) plus untracked files.
    The raw material for the scope check. Empty list if git is unavailable."""
    files = set()
    try:
        tracked = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"], cwd=root,
            capture_output=True, text=True,
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"], cwd=root,
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        return []
    for r in (tracked, untracked):
        if r.returncode == 0:
            files.update(line for line in r.stdout.splitlines() if line.strip())
    return sorted(files)


def main(argv) -> int:
    args = [a for a in argv[1:]]
    no_scope = "--no-scope" in args
    args = [a for a in args if a != "--no-scope"]
    extra_allowed = {_norm(a.split("=", 1)[1]) for a in args if a.startswith("--allow=")}
    args = [a for a in args if not a.startswith("--allow=")]
    if len(args) != 2:
        print("usage: checkpoint.py <block_id> <tag> [--no-scope] [--allow=PATH ...]",
              file=sys.stderr)
        return 2
    block_id, tag = args
    state = yaml.safe_load(STATE.read_text())
    try:
        b = find_block(state, block_id)
    except ValueError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2

    if not b.get("acceptance_test"):
        print(f"refused: {block_id} has no acceptance_test to verify",
              file=sys.stderr)
        return 2

    # ORDER GATE -- a consumer cannot be checkpointed before its producers carry
    # real evidence. Build order becomes un-fakeable, not just a prompt hint.
    unmet = order_gate(state, block_id)
    if unmet:
        print(f"refused: {block_id} depends on blocks that are not done yet: "
              f"{', '.join(unmet)} -- build them first", file=sys.stderr)
        return 1
    print("review: ORDER GATE passed -- every depends_on is done/baseline")

    # GREEN GATE + REGRESSION GATE -- the block's own test must really pass,
    # AND no already-`done` block may have gone red. Each test runs through
    # run_tests.py with its OWN recorded runner, so the verdict is real for
    # pytest and vitest/jest alike. This is what stops a lie.
    for tid, test, runner in regression_targets(state, block_id):
        gate = subprocess.run(gate_command(ROOT, test, runner), cwd=ROOT)
        if gate.returncode != 0:
            if tid == block_id:
                print(f"refused: {test} is not green -- cannot checkpoint {block_id}",
                      file=sys.stderr)
            else:
                print(f"refused: checkpoint of {block_id} would regress {tid} "
                      f"-- {test} is no longer green", file=sys.stderr)
            return 1

    print(f"review: GREEN GATE passed -- {b['acceptance_test']} is genuinely green")

    # CONTRACT GATE -- the seam this block publishes AND every seam it consumes
    # must still hold. Runs the block's own contract_test plus each dependency's
    # contract_test, green. A producer whose contract broke fails here, so the
    # dependency is verified at the consumer, never assumed.
    for cid, ctest, crunner in contract_targets(state, block_id):
        gate = subprocess.run(gate_command(ROOT, ctest, crunner), cwd=ROOT)
        if gate.returncode != 0:
            if cid == block_id:
                print(f"refused: contract test {ctest} is not green -- cannot "
                      f"checkpoint {block_id}", file=sys.stderr)
            else:
                print(f"refused: checkpoint of {block_id} relies on {cid}, whose "
                      f"contract test {ctest} is no longer green", file=sys.stderr)
            return 1
    if contract_targets(state, block_id):
        print("review: CONTRACT GATE passed -- this block's + its dependencies' "
              "seams still hold")

    # QUALITY GATE -- the block's new code must meet the repo's coding AND
    # architecture standards before it can be called done. quality.py runs the
    # checks declared in .weave/quality.yaml as REAL exit codes: lint, format,
    # types, complexity and security on the block's OWN files (a ratchet -- new
    # code is held to standard, legacy is cleaned when next touched), plus
    # coverage and architecture boundaries repo-wide. An empty/missing config
    # makes this a no-op, so quality is opt-in per repo.
    quality_cmd = [
        sys.executable, str(ROOT / "scripts" / "quality.py"),
        "--lang", lang_for(b),
    ]
    files = block_files(b)
    if files:
        quality_cmd += ["--files", *files]
    quality_gate = subprocess.run(quality_cmd, cwd=ROOT)
    if quality_gate.returncode != 0:
        print(f"refused: quality gate failed for {block_id} -- fix the issues "
              f"reported above before checkpointing", file=sys.stderr)
        return 1
    print("review: QUALITY GATE passed -- coding & architecture standards met")

    # REVIEW GATE (the objective half of code review, folded into checkpoint so
    # review + checkpoint are ONE enforced step). The scope check refuses a diff
    # that strays outside the block's declared seam. Skipped only when the block
    # has no `module` (no defined boundary) or the user passes --no-scope.
    if b.get("module") and not no_scope:
        out_of_scope = review_gate(b, changed_files(ROOT), extra_allowed)
        if out_of_scope:
            print(f"refused: checkpoint of {block_id} touches files outside its "
                  f"seam (module: {b['module']}):", file=sys.stderr)
            for f in out_of_scope:
                print(f"  - {f}", file=sys.stderr)
            print("  fix the diff, or pass --allow=PATH for each intended file, "
                  "or --no-scope to bypass.", file=sys.stderr)
            return 1
        print(f"review: SCOPE GATE passed -- diff stays within {b['module']} + tests")
    else:
        print("review: scope gate skipped (no module declared or --no-scope)")

    # DECISION GATE -- provenance & purpose must be recorded before done. This
    # is a hard hook: no decision record, no checkpoint.
    decision_problems = decision_gate(b)
    if decision_problems:
        for p in decision_problems:
            print(f"refused: {p}", file=sys.stderr)
        return 1
    print("review: DECISION GATE passed -- provenance & purpose recorded")

    try:
        state = mark_done(state, block_id, tag)
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

    # Irreversible step: one commit + one tag covering code + docs + state.
    for cmd in (["git", "add", "-A"],
                ["git", "commit", "-m", f"checkpoint {block_id}"],
                ["git", "tag", tag]):
        r = subprocess.run(cmd, cwd=ROOT)
        if r.returncode != 0:
            print(f"refused: git step failed: {' '.join(cmd)}", file=sys.stderr)
            return r.returncode

    print(f"checkpointed {block_id} at tag {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
