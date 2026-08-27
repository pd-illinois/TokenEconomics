#!/usr/bin/env python3
"""Render mindmap.md and kanban.md from .weave/state.yaml.

Those two files are generated artifacts -- never hand-edit them. Edit
state.yaml, run validate.py, then run this.
"""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".weave" / "state.yaml"
MINDMAP = ROOT / "mindmap.md"
KANBAN = ROOT / "kanban.md"

COLUMNS = ["baseline", "unverified", "todo", "building", "in-review", "done", "reverted"]
BANNER = ("<!-- GENERATED from .weave/state.yaml by scripts/render.py. "
          "Do not edit by hand. -->\n\n")

# Status -> ASCII glyph for the dependency tree. Renders in any monospace block,
# unlike Mermaid which needs a live renderer.
GLYPH = {
    "done": "[x]", "baseline": "[x]",
    "building": "[~]", "in-review": "[~]",
    "todo": "[ ]", "unverified": "[!]", "reverted": "[-]",
}
GLYPH_LEGEND = ("[x] done/baseline   [~] building/in-review   "
                "[ ] todo   [!] unverified   [-] reverted")


def _ascii_box(title, rows) -> list:
    """A boxed two-column block: title bar, then `left  right` rows, all padded
    to a common width. Pure ASCII (renders everywhere, no encoding traps)."""
    left_w = max((len(l) for l, _ in rows), default=0)
    body = [f"{l.ljust(left_w)}   {r}".rstrip() for l, r in rows]
    inner = max([len(title)] + [len(b) for b in body] + [0])
    out = ["+- " + title + " " + "-" * (inner - len(title) - 1) + "+"]
    for b in body:
        out.append("| " + b.ljust(inner) + " |")
    out.append("+" + "-" * (inner + 2) + "+")
    return out


def _dep_tree(blocks) -> list:
    """Render the block DAG as an ASCII dependents-tree (parent depends_on ->
    indented child). A node reached by more than one parent is shown in full
    under the first and referenced as '(see above)' elsewhere, so any DAG
    terminates and stays readable. Pure ASCII -> renders everywhere."""
    by_id = {b["id"]: b for b in blocks}
    children = {b["id"]: [] for b in blocks}
    indeg = {b["id"]: 0 for b in blocks}
    for b in blocks:
        for dep in b.get("depends_on", []):
            if dep in children:
                children[dep].append(b["id"])
                indeg[b["id"]] += 1
    roots = [b["id"] for b in blocks if indeg[b["id"]] == 0]
    lines, seen = [], set()

    def label(bid):
        b = by_id[bid]
        return f'{GLYPH.get(b["status"], "[ ]")} {bid}  {b["title"]}'

    def walk(bid, prefix, is_last, is_root):
        connector = "" if is_root else ("`-- " if is_last else "|-- ")
        if bid in seen:
            lines.append(f"{prefix}{connector}{label(bid)}  (see above)")
            return
        seen.add(bid)
        lines.append(f"{prefix}{connector}{label(bid)}")
        new_prefix = prefix if is_root else prefix + ("    " if is_last else "|   ")
        kids = children[bid]
        for i, k in enumerate(kids):
            walk(k, new_prefix, i == len(kids) - 1, False)

    for i, r in enumerate(roots):
        walk(r, "", i == len(roots) - 1, True)
    return lines


def render_mindmap(state) -> str:
    p = state["product"]
    out = [BANNER, f"# {p['name']}\n\n", f"**Status:** {p['status']}\n\n",
           "_The design view: the idea, the chosen tech stack, and how the "
           "blocks fit together. For live status, see `kanban.md`._\n\n",
           f"## Core idea\n\n{p['idea'].strip()}\n\n"]

    # ── Tech stack (architecture view, ASCII box) ──
    stack = p.get("tech_stack") or []
    if stack:
        out.append("## Tech stack\n\n")
        out.append("The components MVPWeaver assembles:\n\n")
        rows = [(s["name"], s["role"]) for s in stack]
        out.append("```text\n")
        out.append("\n".join(_ascii_box("Tech stack", rows)))
        out.append("\n```\n\n")

    # ── Block dependency graph (ASCII tree) ──
    blocks = state["blocks"]
    if blocks:
        out.append("## Block dependency graph\n\n")
        out.append("Indented children depend on the block above them.\n\n")
        out.append("```text\n")
        out.append(GLYPH_LEGEND + "\n\n")
        out.append("\n".join(_dep_tree(blocks)))
        out.append("\n```\n\n")

    # ── Decisions (provenance & purpose) ──
    # Only blocks that carry a decision record appear here, so a board with no
    # decisions renders exactly as before (keeps verify.py's drift check stable).
    decided = [b for b in blocks if b.get("decision")]
    if decided:
        out.append("## Decisions\n\n")
        out.append("Why each block is built the way it is -- recorded provenance "
                   "and purpose, so the source can stay light on comments.\n\n")
        for b in decided:
            d = b["decision"]
            out.append(f"### {b['id']} - {b['title']}\n")
            out.append(f"- Pattern: {d['pattern'].strip()}\n")
            out.append(f"- Complexity: {d['complexity'].strip()}\n")
            out.append(f"- Provenance: `{d['provenance'].strip()}`\n")
            out.append(f"- Rationale: {d['rationale'].strip()}\n\n")

    out.append("## Block specs\n\n")
    for b in blocks:
        deps = ", ".join(b.get("depends_on", [])) or "none"
        out.append(f"### {b['id']} - {b['title']}  `[{b['status']}]`\n")
        out.append(f"- Goal: {b['goal'].strip()}\n")
        out.append(f"- Depends on: {deps}\n")
        iface = b.get("interface", {})
        if iface:
            out.append(f"- Inputs: {', '.join(iface.get('inputs', [])) or '-'}\n")
            out.append(f"- Outputs: {', '.join(iface.get('outputs', [])) or '-'}\n")
        for key in ("module", "runner", "acceptance_test", "contract_test", "checkpoint_tag"):
            if b.get(key):
                out.append(f"- {key.replace('_', ' ').title()}: `{b[key]}`\n")
        out.append("\n")
    return "".join(out)


def render_kanban(state) -> str:
    buckets = {c: [] for c in COLUMNS}
    for b in state["blocks"]:
        buckets.setdefault(b["status"], []).append(b)
    out = [BANNER, "# Kanban\n\n",
           "_The status board: every block by where it is right now. For the "
           "design, tech stack, and dependencies, see `mindmap.md`._\n\n"]
    for c in COLUMNS:
        out.append(f"## {c}  ({len(buckets[c])})\n\n")
        for b in buckets[c]:
            out.append(f"- **{b['id']}** {b['title']}\n")
        out.append("\n")
    return "".join(out)


def main() -> int:
    state = yaml.safe_load(STATE.read_text())
    MINDMAP.write_text(render_mindmap(state))
    KANBAN.write_text(render_kanban(state))
    print(f"rendered {MINDMAP.name} and {KANBAN.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
