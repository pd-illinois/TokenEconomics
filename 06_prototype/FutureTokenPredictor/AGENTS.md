# AGENTS.md

Behavioral rules for any AI coding agent working in this repository.
These rules are portable across tools (GitHub Copilot, Claude Code, Codex, Cursor).
Stack-specific conventions live in `.github/copilot-instructions.md`.

---

## 1. Think Before Coding

State assumptions before you edit. Do not guess.

- If the request is ambiguous, list 2–3 plausible interpretations and ask which one applies. Do not pick silently.
- If the request depends on context you don't have (a file's contents, an API shape, an env var, how a function is called elsewhere), read it first.
- If a claim in the prompt seems wrong, surface the contradiction. Do not paper over it.
- If you are uncertain, say "I am uncertain about X" — do not assert confidently and proceed.

**Anti-pattern:** "I'll assume you meant…" followed by a 50-line edit. Stop. Ask.

## 2. Simplicity First

Write the minimum code that solves the stated problem.

- No speculative abstractions. No "this might be useful later." No interfaces for one implementation.
- No new dependencies unless explicitly asked or strictly required.
- No new files when an existing file is the right home.
- No "flexibility" that wasn't requested. A hardcoded value is fine if the user didn't ask for configuration.
- Prefer a 3-line change over a 30-line refactor, even if the refactor is "cleaner." Cleanliness is not the task; the task is the task.

**Anti-pattern:** Asked for a one-line fix, produced a new module, a config file, and a test harness.

## 3. Surgical Changes

Touch only what was asked. Every changed line must trace to the request.

- Do not reformat code you weren't asked to reformat.
- Do not rename variables, reorder imports, or "tidy up" adjacent code.
- Do not change comments unless the comment is wrong because of your change.
- Do not delete code that looks unused — it may be used somewhere you haven't seen.
- If a fix genuinely requires changing nearby code, name those changes explicitly in your response before making them.

**Anti-pattern:** Asked to fix a typo, diff includes 12 unrelated formatting changes.

## 4. Goal-Driven Execution

Convert vague requests into verifiable success criteria before writing code.

- "Fix the bug" → write a test that reproduces it; the bug is fixed when the test passes.
- "Make it faster" → state the current baseline and the target; measure both.
- "Refactor this" → state what property improves (readability, testability, performance) and what stays the same (behavior, public API).
- If you can't state success in checkable terms, ask the user to clarify before coding.

**Anti-pattern:** "Done!" with no evidence the original problem is resolved.

---

## Circuit breakers

These override anything else when they trigger:

- **2x rule.** If your diff is more than 2x the size of what was asked, stop and ask before continuing.
- **Adjacent-edit rule.** If you are about to modify code outside the scope of the request, stop and ask first.
- **New-file rule.** If you are about to create a new file the user did not mention, stop and ask first.
- **New-dependency rule.** If you are about to add an import from a package not already used in this repo, stop and ask first.

## Reporting back

When you finish a change:

1. State what you changed in one sentence.
2. List the files touched.
3. State how the user can verify it works (command to run, test to check, page to load).
4. Flag anything you decided on the user's behalf, anything you skipped, and anything you're unsure about.

Do not summarize the diff line-by-line — the user can read it.
