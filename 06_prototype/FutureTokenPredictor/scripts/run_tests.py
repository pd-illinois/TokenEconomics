#!/usr/bin/env python3
"""Run a test target and surface RAW evidence: exit code + captured output.

This is the framework's source of truth. An agent calls this and cannot
fabricate the result -- the verdict is the test runner's real exit code,
captured here, not anything an agent claims.

--expect makes red-first mechanical:
    run_tests.py <target> --expect fail   # succeeds ONLY if the test is red
    run_tests.py <target> --expect pass   # succeeds ONLY if the test is green

The runner is selectable so brownfield (TypeScript/JS) projects work too:
    run_tests.py <target> --runner vitest --expect pass
    run_tests.py --runner vitest --all --expect pass   # whole suite, no target

Default runner is pytest and a positional target behaves exactly as before, so
existing Python blocks are unaffected.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

RUNNERS = ("pytest", "vitest", "jest")


def command_for(runner: str, target):
    """Build the runner's argv. target=None means the whole suite."""
    if runner == "pytest":
        return [sys.executable, "-m", "pytest", "-q"] + ([target] if target else [])
    if runner == "vitest":
        return ["npx", "vitest", "run"] + ([target] if target else [])
    if runner == "jest":
        return ["npx", "jest"] + ([target] if target else [])
    raise ValueError(f"unknown runner: {runner}")


def verdict(returncode: int) -> str:
    """0 -> 'pass', anything else -> 'fail'."""
    return "pass" if returncode == 0 else "fail"


def reconcile(actual: str, expect):
    """Compare an actual verdict to an expectation. Returns (ok, message).

    expect=None means no expectation -- any verdict is acceptable.
    """
    if expect is None:
        return True, f"verdict: {actual}"
    if actual == expect:
        return True, f"verdict: {actual} (as expected)"
    return False, f"verdict: {actual} but expected {expect}"


def capture(cmd):
    """Run cmd, return (exit_code, combined_output). The raw evidence."""
    env = os.environ.copy()
    source = str(ROOT / "src")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = source if not existing else os.pathsep.join((source, existing))
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env)
    return r.returncode, (r.stdout + r.stderr)


def run(target, expect, runner: str = "pytest") -> int:
    code, output = capture(command_for(runner, target))
    print(output, end="")
    ok, msg = reconcile(verdict(code), expect)
    label = target or f"{runner} suite"
    print(f"\nEVIDENCE: {label} -> {msg} (exit {code})")
    return 0 if ok else 1


USAGE = "usage: run_tests.py <target> [--runner pytest|vitest|jest] [--all] [--expect pass|fail]"


def main(argv) -> int:
    args = argv[1:]
    expect = None
    runner = "pytest"
    run_all = False
    if "--all" in args:
        run_all = True
        args.remove("--all")
    if "--runner" in args:
        i = args.index("--runner")
        try:
            runner = args[i + 1]
        except IndexError:
            print(USAGE, file=sys.stderr)
            return 2
        if runner not in RUNNERS:
            print(f"--runner must be one of {', '.join(RUNNERS)}", file=sys.stderr)
            return 2
        del args[i:i + 2]
    if "--expect" in args:
        i = args.index("--expect")
        try:
            expect = args[i + 1]
        except IndexError:
            print(USAGE, file=sys.stderr)
            return 2
        if expect not in ("pass", "fail"):
            print("--expect must be 'pass' or 'fail'", file=sys.stderr)
            return 2
        del args[i:i + 2]
    if run_all:
        if args:
            print(USAGE, file=sys.stderr)
            return 2
        return run(None, expect, runner)
    if len(args) != 1:
        print(USAGE, file=sys.stderr)
        return 2
    return run(args[0], expect, runner)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
