"""
dashboard.py - render a self-contained HTML dashboard from dashboard_data.json.

Run:  python demo.py         # produces dashboard_data.json
      python dashboard.py    # produces dashboard.html  (open in any browser)

Zero dependencies, fully offline (no CDN). Colors use the validated data-viz
reference palette (categorical slots validated for CVD; every bar is direct-labeled
and a table view is included, per the light-mode contrast relief rule).
"""

from __future__ import annotations
import html
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# --- validated palette (light | dark) ---
CSS = """
:root{
  --page:#f9f9f7; --surface:#fcfcfb; --tp:#0b0b0b; --ts:#52514e; --muted:#898781;
  --grid:#e1e0d9; --baseline:#c3c2b7; --border:rgba(11,11,11,.10);
  --s1:#2a78d6; --s2:#1baf7a; --s3:#eda100; --green:#008300;
  --good:#006300; --warning:#b8830f; --critical:#d03b3b; --track:#eeede9;
}
@media (prefers-color-scheme:dark){
  :root{
    --page:#0d0d0d; --surface:#1a1a19; --tp:#ffffff; --ts:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --baseline:#383835; --border:rgba(255,255,255,.10);
    --s1:#3987e5; --s2:#199e70; --s3:#c98500; --green:#008300;
    --good:#0ca30c; --warning:#fab219; --critical:#d03b3b; --track:#242423;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--tp);
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.45}
.wrap{max-width:1040px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:22px;margin:0 0 2px}
.sub{color:var(--ts);font-size:13px;margin:0 0 24px}
.badge{display:inline-block;font-size:11px;font-weight:600;padding:2px 8px;border-radius:10px;
  border:1px solid var(--border);color:var(--ts);vertical-align:middle;margin-left:8px}
h2{font-size:14px;text-transform:uppercase;letter-spacing:.04em;color:var(--ts);
  margin:34px 0 14px;font-weight:600}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:18px 20px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 16px}
.tile .k{font-size:12px;color:var(--ts);margin:0 0 6px}
.tile .v{font-size:26px;font-weight:650;font-variant-numeric:tabular-nums}
.tile .v.good{color:var(--good)} .tile .v.warn{color:var(--warning)} .tile .v.crit{color:var(--critical)}
.tile .n{font-size:11px;color:var(--muted);margin-top:4px}
.row{display:flex;align-items:center;gap:10px;margin:9px 0}
.row .lab{width:180px;min-width:180px;font-size:13px;color:var(--ts);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.track{flex:1;background:var(--track);border-radius:5px;height:22px;position:relative;overflow:hidden}
.fill{height:100%;border-radius:0 5px 5px 0;min-width:2px}
.val{font-size:12px;font-variant-numeric:tabular-nums;color:var(--tp);width:120px;min-width:120px;text-align:right}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:7px 10px;border-bottom:1px solid var(--grid)}
th:first-child,td:first-child{text-align:left}
th{color:var(--ts);font-weight:600}
.chg{font-size:13px;color:var(--ts);margin:6px 0;padding-left:14px;border-left:2px solid var(--s1)}
.legend{font-size:12px;color:var(--muted);margin-top:8px}
.dot{display:inline-block;width:9px;height:9px;border-radius:2px;margin:0 4px 0 12px;vertical-align:middle}
footer{color:var(--muted);font-size:11px;margin-top:40px;border-top:1px solid var(--grid);padding-top:14px}
"""


def esc(s):
    return html.escape(str(s))


def bar_row(label, value, vmax, color, display):
    pct = (value / vmax * 100.0) if vmax else 0.0
    return (f'<div class="row"><div class="lab" title="{esc(label)}">{esc(label)}</div>'
            f'<div class="track"><div class="fill" style="width:{pct:.1f}%;background:{color}" '
            f'title="{esc(label)}: {esc(display)}"></div></div>'
            f'<div class="val">{esc(display)}</div></div>')


def tile(k, v, note="", cls=""):
    return (f'<div class="tile"><p class="k">{esc(k)}</p>'
            f'<div class="v {cls}">{esc(v)}</div>'
            f'<div class="n">{esc(note)}</div></div>')


SCEN_COLORS = {
    "Baseline (premium all)": "var(--muted)",
    "Governed (Tier 2)": "var(--s2)",
    "Our governance": "var(--s2)",
    "Foundry Model Router": "var(--s1)",
    "Regression (cost mode)": "var(--warning)",
    "After auto-revert": "var(--green)",
}
MODEL_COLORS = {"cheap": "var(--s2)", "premium": "var(--s1)", "cache": "var(--green)",
                "router": "var(--s1)", "none": "var(--muted)"}


def build(data):
    k = data["kpis"]
    parts = []
    parts.append(f'<h1>{esc(data["title"])}<span class="badge">{esc(data["mode"]).upper()}</span></h1>')
    parts.append('<p class="sub">Reusable cost-governance architecture (file 06) - '
                 'costs are illustrative; the point is the mechanism.</p>')

    # KPI tiles
    parts.append('<div class="tiles">')
    parts.append(tile("Baseline cost", f'${k["baseline_cost"]:.4f}', "premium, no governance"))
    parts.append(tile("Governed cost", f'${k["governed_cost"]:.4f}', "routing + cache + caps"))
    parts.append(tile("Saved", f'{k["saved_pct"]:.1f}%', f'${k["saved_usd"]:.4f} lower', "good"))
    q = k["quality"]
    qcls = "good" if q >= 0.8 else "crit"
    parts.append(tile("Live quality", f'{q:.3f}', "judged vs golden set", qcls))
    parts.append(tile("Cache hit rate", f'{k["cache_hit_rate"]:.0f}%', "semantic cache"))
    parts.append(tile("Avg latency", f'{k["avg_latency_ms"]:.0f} ms', "incl. gateway tax"))
    parts.append(tile("Requests", f'{k["requests"]}', "this run"))
    parts.append('</div>')

    # Scenario cost comparison
    scen = data["scenarios"]
    vmax = max((s["cost"] for s in scen), default=0) or 1
    parts.append('<h2>Cost by scenario</h2><div class="card">')
    for s in scen:
        color = SCEN_COLORS.get(s["name"], "var(--s1)")
        parts.append(bar_row(s["name"], s["cost"], vmax, color,
                             f'${s["cost"]:.4f}  (q {s["quality"]:.2f})'))
    parts.append('</div>')

    # Per-tenant + per-model side by side
    parts.append('<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">')
    tenants = data.get("tenants", [])
    tmax = max((t["cost"] for t in tenants), default=0) or 1
    parts.append('<div><h2>Cost by tenant</h2><div class="card">')
    for t in tenants:
        parts.append(bar_row(t["tenant"], t["cost"], tmax, "var(--s1)",
                             f'${t["cost"]:.4f} / {t["requests"]}req'))
    parts.append('</div></div>')
    models = data.get("models", [])
    mmax = max((m["cost"] for m in models), default=0) or 1
    parts.append('<div><h2>Cost by model served</h2><div class="card">')
    for m in models:
        parts.append(bar_row(m["model"], m["cost"], mmax,
                             MODEL_COLORS.get(m["model"], "var(--s3)"),
                             f'${m["cost"]:.4f} / {m["requests"]}req'))
    parts.append('</div></div></div>')

    # Quality by difficulty + closed loop
    qbd = data.get("quality_by_difficulty", {})
    if qbd:
        floor = data.get("closed_loop", {}).get("floor", 0.8)
        parts.append('<h2>Quality by difficulty (floor '
                     f'{floor:.2f})</h2><div class="card">')
        for diff, score in qbd.items():
            color = "var(--good)" if score >= floor else "var(--critical)"
            parts.append(bar_row(diff, score, 1.0, color, f'{score:.3f}'))
        parts.append('</div>')

    cl = data.get("closed_loop", {})
    if cl.get("before") and cl.get("after"):
        floor = cl.get("floor", 0.8)
        parts.append('<h2>Closed loop: regression -> auto-revert</h2><div class="card">')
        parts.append('<table><tr><th>segment</th><th>before (cost mode)</th>'
                     '<th>after auto-revert</th></tr>')
        for seg in sorted(set(cl["before"]) | set(cl["after"])):
            b = cl["before"].get(seg, "-"); a = cl["after"].get(seg, "-")
            bcol = "var(--critical)" if isinstance(b, (int, float)) and b < floor else "var(--tp)"
            acol = "var(--good)" if isinstance(a, (int, float)) and a >= floor else "var(--tp)"
            parts.append(f'<tr><td>{esc(seg)}</td>'
                         f'<td style="color:{bcol}">{b}</td>'
                         f'<td style="color:{acol}">{a}</td></tr>')
        parts.append('</table></div>')

    # Changelog
    chg = data.get("changelog", [])
    if chg:
        parts.append('<h2>Config changelog (audit trail - no code deploy)</h2><div class="card">')
        for e in chg:
            parts.append(f'<p class="chg"><b>{esc(e["knob"])}</b>: {esc(e["from"])} '
                         f'&rarr; {esc(e["to"])} <span style="color:var(--muted)">'
                         f'({esc(e["reason"])})</span></p>')
        parts.append('</div>')

    # Accessible table view (relief rule)
    parts.append('<h2>Data table (accessible view)</h2><div class="card"><table>'
                 '<tr><th>scenario</th><th>cost $</th><th>quality</th></tr>')
    for s in scen:
        parts.append(f'<tr><td>{esc(s["name"])}</td><td>{s["cost"]:.4f}</td>'
                     f'<td>{s["quality"]:.3f}</td></tr>')
    parts.append('</table></div>')

    parts.append('<footer>Generated by dashboard.py from dashboard_data.json. '
                 'In production this view is an Azure Monitor Workbook over Application Insights '
                 '(see dashboard/workbook.json) plus the Foundry Agent Monitoring dashboard.</footer>')
    return "\n".join(parts)


def main():
    src = os.path.join(HERE, "dashboard_data.json")
    if not os.path.exists(src):
        print("dashboard_data.json not found. Run `python demo.py` (or `--live`) first.")
        return
    with open(src, encoding="utf-8") as fh:
        data = json.load(fh)
    body = build(data)
    doc = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<title>{esc(data["title"])}</title><style>{CSS}</style></head>'
           f'<body><div class="wrap">{body}</div>'
           f'<script type="application/json" id="raw">{json.dumps(data)}</script></body></html>')
    out = os.path.join(HERE, "dashboard.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(doc)
    print(f"[dashboard written to {out}] - open it in a browser.")


if __name__ == "__main__":
    main()
