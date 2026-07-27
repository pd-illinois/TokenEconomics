#!/usr/bin/env python3
"""Build and verify the manifest-enforced TE-001.5 Plan release directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

INVENTORY_NAME = "release_inventory.json"


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _copy_entry(source_root: Path, relative: str, destination: Path) -> list[Path]:
    source = source_root / relative
    if not source.exists():
        raise FileNotFoundError(f"Allowlisted release path is missing: {relative}")
    target = destination / relative
    if source.is_dir():
        shutil.copytree(
            source,
            target,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
        )
        return [path for path in target.rglob("*") if path.is_file()]
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return [target]


def build(root: Path, manifest_path: Path, destination: Path) -> dict:
    root = root.resolve()
    manifest_path = manifest_path.resolve()
    destination = destination.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if destination == root or root in destination.parents:
        raise ValueError("release destination must be outside the source tree")
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    copied: list[Path] = []
    for relative in manifest["include"]:
        copied.extend(_copy_entry(root, relative, destination))

    predictor = manifest["predictor_component"]
    predictor_source = root / predictor["path"]
    predictor_destination = destination / predictor["path"]
    for relative in predictor["include"]:
        copied.extend(_copy_entry(predictor_source, relative, predictor_destination))

    files = {
        path.relative_to(destination).as_posix(): _hash(path)
        for path in sorted(set(copied))
    }
    inventory = {
        "schema_version": "1.0",
        "release_gate": manifest["release_gate"],
        "scope": manifest["scope"],
        "built_at": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": _hash(manifest_path),
        "files": files,
    }
    (destination / INVENTORY_NAME).write_text(
        json.dumps(inventory, indent=2, sort_keys=True), encoding="utf-8"
    )
    return inventory


def verify(destination: Path) -> list[str]:
    destination = destination.resolve()
    inventory_path = destination / INVENTORY_NAME
    if not inventory_path.exists():
        return [f"missing: {INVENTORY_NAME}"]
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    expected = inventory.get("files", {})
    actual_files = {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file() and path.name != INVENTORY_NAME
    }
    for relative, expected_hash in expected.items():
        path = destination / relative
        if not path.exists():
            problems.append(f"missing: {relative}")
        elif _hash(path) != expected_hash:
            problems.append(f"hash mismatch: {relative}")
    for relative in sorted(actual_files - set(expected)):
        problems.append(f"unexpected: {relative}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.verify:
        problems = verify(args.destination)
        if problems:
            print("\n".join(problems))
            return 1
        print("Plan release inventory verified")
        return 0
    inventory = build(root, root / "plan_release_manifest.json", args.destination)
    print(f"Built {len(inventory['files'])} allowlisted files at {args.destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
