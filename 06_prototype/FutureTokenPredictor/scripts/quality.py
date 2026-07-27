#!/usr/bin/env python3
"""quality.py -- run a repo's coding & architecture standards as EVIDENCE.

MVPWeaver's spine is "agents produce claims, the framework produces evidence."
"Follow best practices" is a claim -- worthless unless it is backed by a real,
captured exit code. This script turns the mechanizable half of code quality into
exactly that: it runs the checks declared in `.weave/quality.yaml` through real
subprocesses and reports a real verdict. checkpoint.py gates `done` on it, so a
block cannot be called finished while it is lint-dirty, mistyped, insecure,
over-complex, under-covered, or violating an architecture boundary.

Each check in `.weave/quality.yaml` is:

    - name: lint              # shown in output
      lang: python            # python | js | (omitted == any)
      cmd: "{py} -m ruff check {files}"

Placeholders:
  {py}     -> this interpreter (so the gate uses the project's installed tools).
  {files}  -> the block's own files (its module + tests). A check that names
              {files} but is handed none is SKIPPED -- it is a per-file check
              with nothing in scope. Checks WITHOUT {files} are repo-wide
              (coverage floor, architecture boundaries) and always run.

An empty or missing config makes the whole gate a no-op, so a repo opts in by
writing the file -- nothing is forced on a project that has not adopted it.

    python scripts/quality.py --lang python --files scripts/foo.py tests/test_foo.py
    python scripts/quality.py            # repo-wide checks only
"""

import argparse
import shlex
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".weave" / "quality.yaml"

FILES_TOKEN = "{files}"


def load_checks(path: Path) -> list:
    """The list of checks declared in a quality config, or [] if there is none.
    A missing or empty file means the gate does nothing -- quality is opt-in."""
    if not Path(path).exists():
        return []
    data = yaml.safe_load(Path(path).read_text()) or {}
    return list(data.get("checks", []))


def select_checks(checks: list, lang: str) -> list:
    """Keep the checks that apply to this block's language. A check with no
    `lang` is language-agnostic (e.g. an architecture boundary) and always
    applies; a `lang` must match the block's language to run."""
    return [c for c in checks if c.get("lang", "any") in ("any", lang)]


def resolve(cmd: str, *, py: str, files: list):
    """Turn a check's `cmd` template into an argv list, or None if it should be
    SKIPPED. A check that references {files} but is handed none is skipped --
    there is nothing in scope to check. {py} always becomes this interpreter.

    The template is tokenised FIRST and the placeholders substituted per-token,
    so an interpreter path with backslashes (Windows) is never re-parsed by the
    shell-lexer and mangled.
    """
    if FILES_TOKEN in cmd and not files:
        return None
    argv = []
    for tok in shlex.split(cmd):
        if tok == FILES_TOKEN:
            argv.extend(files)
        else:
            argv.append(tok.replace("{py}", py))
    return argv


def run_gate(checks: list, lang: str, *, py: str, files: list, cwd: Path) -> list:
    """Run every applicable check and return one result dict per check:
    {name, skipped, returncode}. The raw tool output streams to the terminal so
    the human sees the real evidence -- this never summarises or paraphrases it.
    """
    results = []
    for check in select_checks(checks, lang):
        name = check.get("name", "check")
        argv = resolve(check["cmd"], py=py, files=files)
        if argv is None:
            results.append({"name": name, "skipped": True, "returncode": 0})
            continue
        print(f"== quality: {name} ==")
        proc = subprocess.run(argv, cwd=cwd)
        results.append({"name": name, "skipped": False, "returncode": proc.returncode})
    return results


def gate_failed(results: list) -> bool:
    """True if any non-skipped check returned a non-zero exit code."""
    return any((not r["skipped"]) and r["returncode"] != 0 for r in results)


def main(argv) -> int:
    parser = argparse.ArgumentParser(prog="quality.py")
    parser.add_argument("--lang", default="python")
    parser.add_argument("--files", nargs="*", default=[])
    ns = parser.parse_args(argv[1:])

    checks = load_checks(CONFIG)
    if not checks:
        print("quality: no checks configured (.weave/quality.yaml) -- skipping")
        return 0

    results = run_gate(checks, ns.lang, py=sys.executable, files=ns.files, cwd=ROOT)
    failed = [r["name"] for r in results if (not r["skipped"]) and r["returncode"]]
    skipped = [r["name"] for r in results if r["skipped"]]
    if skipped:
        print(f"quality: skipped (no files in scope): {', '.join(skipped)}")
    if failed:
        print(f"quality: FAILED -- {', '.join(failed)}", file=sys.stderr)
        return 1
    print("quality: OK -- all configured checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
