# Copilot instructions — this repo uses MVPWeaver

This project is built with **MVPWeaver**: agents produce *claims*, the framework
produces *evidence*, and the human sees the evidence. The test runner — never an
agent — is the source of truth for whether a test passed. Follow these rules in
every response, whether or not a `/weave*` agent was explicitly invoked.

## Never hand-edit generated state
- `.weave/state.yaml` is the single source of truth. Change it only through the
  scripts (`set_name.py`, `set_idea.py`, `set_tech_stack.py`, `add_block.py`, `set_status.py`,
  `checkpoint.py`, `revert.py`, `adopt.py`) — never by typing into it directly.
- `kanban.md` and `mindmap.md` are **generated** by `scripts/render.py`. Never
  edit them by hand; re-render instead. `mindmap.md` includes an ASCII
  dependency tree and tech stack box — both are rendered from state.

## Never claim a test result — prove it
- The only source of truth for a passing test is `scripts/run_tests.py` and its
  real exit code. Do not say a test "passes" from reading the code or from your
  own reasoning — run it.
- Build **red-first**: prove the test fails (`run_tests.py <test> --expect fail`)
  before writing the code that makes it pass. A test that never failed proves
  nothing.

## Status is earned, not asserted
- `done` is written only by `checkpoint.py`, after a real green run plus a git
  tag. It is not a status an agent may set by hand.
- `baseline` is written only by `adopt.py`, from a real green adoption run.
- `scripts/verify.py` is the tamper-evidence gate: it catches hand-edited boards
  and forged `done` / `baseline` statuses. Keep it passing.

## Work in small blocks
- One block = one job, one interface (seam), one acceptance test. Do not sprawl
  across files or batch several blocks into one change.
- Use the agents for the real loop: `/weaveidea` → `/weavemvp` →
  `/weaveaction <id>` → checkpoint; `/weaverevert <id>` when a block goes wrong;
  `/weaveadopt` to onboard existing code. `/weavereview <id>` is available for
  an independent second opinion but is no longer a mandatory step — the review
  checklist is built into `weaveaction`'s checkpoint flow.

## Read the board, not the chat
- `kanban.md` is the truth for what is `done` / `baseline` / `unverified` — not
  anything an agent narrated in chat. When in doubt, check the board.
