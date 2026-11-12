"""Build site/index.html (inline SVG charts) and site/card.json from the
benchmark JSON in results/. Standard library only.

Usage: python3 scripts/make_report.py --results results --site site --command "<cmd>"
"""
import argparse
import datetime
import html
import json
import math
import os
import subprocess

TABLES = ["LinearMap", "RobinHoodMap", "SwissMap", "std::unordered_map"]
OPEN_TABLES = TABLES[:3]
DASHES = ["", "7 4", "2 3", "9 3 2 3"]
MARKERS = ["circle", "square", "triangle", "diamond"]
CARD_LOAD = 0.70
W, H = 640, 340
PAD_L, PAD_R, PAD_T, PAD_B = 64, 20, 16, 52


# ---------- loading ----------

def load_results(results_dir):
    def read(name):
        with open(os.path.join(results_dir, name), encoding="utf-8") as f:
            return json.load(f)
    return read("bench.json"), read("latency.json"), read("machine.json")


def medians(bench):
    """Map (op, table, dist, param) -> median benchmark entry."""
    out = {}
    for b in bench["benchmarks"]:
        if b.get("run_type") == "aggregate" and b.get("aggregate_name") != "median":
            continue
        if b.get("error_occurred"):
            continue
        op, table, dist, param = b["run_name"].split("/")
        out[(op, table, dist, param)] = b
    return out


def ns(entry):
    scale = {"ns": 1.0, "us": 1e3, "ms": 1e6, "s": 1e9}[entry["time_unit"]]
    return entry["real_time"] * scale


def line_series(med, op, dist):
    """table -> sorted [(x, ns per op)]; insert is divided by n."""
    series = {}
    for (o, table, d, param), b in med.items():
        if o != op or d != dist:
            continue
        x = float(param)
        y = ns(b) / x if op == "insert" else ns(b)
        series.setdefault(table, []).append((x, y))
    return {t: sorted(series[t]) for t in TABLES if t in series}


# ---------- formatting helpers ----------

def fmt_ns(v):
    return f"{v:.1f}" if v < 100 else f"{v:.0f}"


def fmt_count(n):
    n = int(n)
    if n >= 1 << 20:
        return f"{n >> 20}M"
    if n >= 1 << 10:
        return f"{n >> 10}K"
    return str(n)


def nice_max(v):
    if v <= 0:
        return 1.0
    exp = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * exp:
            return m * exp
    return 10 * exp


def ticks(top, count=5):
    step = top / count
    return [step * i for i in range(count + 1)]


def esc(s):
    return html.escape(str(s), quote=True)


# ---------- SVG pieces ----------

def marker(kind, x, y, cls, label):
    t = f"<title>{esc(label)}</title>"
    if kind == "circle":
        return f'<circle class="{cls}" cx="{x:.1f}" cy="{y:.1f}" r="4.5">{t}</circle>'
    if kind == "square":
        return f'<rect class="{cls}" x="{x - 4:.1f}" y="{y - 4:.1f}" width="8" height="8">{t}</rect>'
    if kind == "triangle":
        pts = f"{x:.1f},{y - 5.5:.1f} {x + 5:.1f},{y + 4:.1f} {x - 5:.1f},{y + 4:.1f}"
        return f'<polygon class="{cls}" points="{pts}">{t}</polygon>'
    pts = f"{x:.1f},{y - 5.5:.1f} {x + 5.5:.1f},{y:.1f} {x:.1f},{y + 5.5:.1f} {x - 5.5:.1f},{y:.1f}"
    return f'<polygon class="{cls}" points="{pts}">{t}</polygon>'


def legend(names):
    items = []
    for name in names:
        i = TABLES.index(name)
        dash = f' stroke-dasharray="{DASHES[i]}"' if DASHES[i] else ""
        items.append(
            f'<li><svg width="34" height="14" aria-hidden="true">'
            f'<line class="ln s{i + 1}" x1="1" y1="7" x2="33" y2="7"{dash}/>'
            f'{marker(MARKERS[i], 17, 7, f"mk s{i + 1}", name)}</svg>{esc(name)}</li>')
    return f'<ul class="legend">{"".join(items)}</ul>'


def empty_svg(msg="No data for this view."):
    return (f'<svg viewBox="0 0 {W} 120" role="img" aria-label="{esc(msg)}">'
            f'<text class="empty" x="{W / 2}" y="64" text-anchor="middle">{esc(msg)}</text></svg>')


def line_chart(series, x_label, y_label, x_log=False, x_fmt=lambda v: f"{v:.2f}", unit="ns"):
    if not series or not any(series.values()):
        return empty_svg()
    xs = sorted({x for pts in series.values() for x, _ in pts})
    ymax = nice_max(max(y for pts in series.values() for _, y in pts) * 1.05)
    pw, ph = W - PAD_L - PAD_R, H - PAD_T - PAD_B
    fx = (lambda v: math.log2(v)) if x_log else (lambda v: v)
    x0, x1 = fx(xs[0]), fx(xs[-1])
    span = (x1 - x0) or 1.0
    pad = span * 0.04

    def sx(v):
        return PAD_L + (fx(v) - x0 + pad) / (span + 2 * pad) * pw

    def sy(v):
        return PAD_T + ph - v / ymax * ph

    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{esc(y_label)} by {esc(x_label)}">']
    for t in ticks(ymax):
        y = sy(t)
        out.append(f'<line class="grid" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text class="tick" x="{PAD_L - 8}" y="{y + 4:.1f}" text-anchor="end">{t:g}</text>')
    for x in xs:
        out.append(f'<text class="tick" x="{sx(x):.1f}" y="{PAD_T + ph + 18}" '
                   f'text-anchor="middle">{esc(x_fmt(x))}</text>')
    out.append(f'<line class="axis" x1="{PAD_L}" x2="{W - PAD_R}" y1="{PAD_T + ph}" y2="{PAD_T + ph}"/>')
    out.append(f'<text class="label" x="{PAD_L + pw / 2}" y="{H - 8}" text-anchor="middle">{esc(x_label)}</text>')
    out.append(f'<text class="label" transform="translate(16 {PAD_T + ph / 2}) rotate(-90)" '
               f'text-anchor="middle">{esc(y_label)}</text>')
    for name, pts in series.items():
        i = TABLES.index(name)
        path = " ".join(f"{'M' if j == 0 else 'L'}{sx(x):.1f},{sy(y):.1f}" for j, (x, y) in enumerate(pts))
        dash = f' stroke-dasharray="{DASHES[i]}"' if DASHES[i] else ""
        out.append(f'<path class="ln s{i + 1}" d="{path}"{dash}/>')
        for x, y in pts:
            out.append(marker(MARKERS[i], sx(x), sy(y), f"mk s{i + 1}",
                              f"{name}, {x_fmt(x)}: {fmt_ns(y)} {unit}"))
    out.append("</svg>")
    return "".join(out)


def interval_chart(rows, x_label):
    """rows: [(label, p50, p90, p99)] drawn as p50-p99 bars with a p90 tick."""
    if not rows:
        return empty_svg()
    row_h, top, left = 34, 12, 150
    h = top + row_h * len(rows) + 48
    xmax = nice_max(max(r[3] for r in rows) * 1.05)
    pw = W - left - PAD_R

    def sx(v):
        return left + v / xmax * pw

    out = [f'<svg viewBox="0 0 {W} {h}" role="img" aria-label="{esc(x_label)}">']
    base = top + row_h * len(rows)
    for t in ticks(xmax):
        out.append(f'<line class="grid" x1="{sx(t):.1f}" x2="{sx(t):.1f}" y1="{top}" y2="{base}"/>')
        out.append(f'<text class="tick" x="{sx(t):.1f}" y="{base + 18}" text-anchor="middle">{t:g}</text>')
    out.append(f'<text class="label" x="{left + pw / 2}" y="{h - 6}" text-anchor="middle">{esc(x_label)}</text>')
    for k, (label, p50, p90, p99) in enumerate(rows):
        i = TABLES.index(label)
        y = top + row_h * k + row_h / 2
        tip = f"{label}: p50 {fmt_ns(p50)} ns, p90 {fmt_ns(p90)} ns, p99 {fmt_ns(p99)} ns"
        out.append(f'<text class="rowlabel" x="{left - 10}" y="{y + 4:.1f}" text-anchor="end">{esc(label)}</text>')
        out.append(f'<g><title>{esc(tip)}</title>'
                   f'<line class="rng s{i + 1}" x1="{sx(p50):.1f}" x2="{sx(p99):.1f}" y1="{y:.1f}" y2="{y:.1f}"/>'
                   f'<line class="p90 s{i + 1}" x1="{sx(p90):.1f}" x2="{sx(p90):.1f}" y1="{y - 7:.1f}" y2="{y + 7:.1f}"/>'
                   f'{marker(MARKERS[i], sx(p50), y, f"mk s{i + 1}", tip)}'
                   f'<line class="p99" x1="{sx(p99):.1f}" x2="{sx(p99):.1f}" y1="{y - 5:.1f}" y2="{y + 5:.1f}"/></g>')
    out.append("</svg>")
    return "".join(out)


def data_table(caption, headers, rows):
    head = "".join(f"<th scope=col>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in rows)
    return (f"<details><summary>Data table</summary><table><caption>{esc(caption)}</caption>"
            f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></details>")


# ---------- takeaways (generated from the numbers) ----------

def at(series, table, x):
    for px, py in series.get(table, []):
        if abs(px - x) < 1e-9:
            return py
    return None


def rank_at(series, x, tables):
    vals = [(at(series, t, x), t) for t in tables if at(series, t, x) is not None]
    return sorted(vals)


def lookup_takeaway(series, what):
    r = rank_at(series, CARD_LOAD, TABLES)
    if len(r) < 2:
        return "Not enough data for a comparison."
    (fast_v, fast), (slow_v, slow) = r[0], r[-1]
    s = (f"At load {CARD_LOAD:.2f}, {fast} takes {fmt_ns(fast_v)} ns per {what} and "
         f"{slow} {fmt_ns(slow_v)} ns ({slow_v / fast_v:.1f}x).")
    hi = rank_at(series, 0.85, ["RobinHoodMap", "SwissMap"])
    if len(hi) == 2:
        s += f" At 0.85, {hi[0][1]} takes {fmt_ns(hi[0][0])} ns and {hi[1][1]} {fmt_ns(hi[1][0])} ns."
    return s


def insert_takeaway(series):
    xmax = max(x for pts in series.values() for x, _ in pts)
    r = rank_at(series, xmax, TABLES)
    if len(r) < 2:
        return "Not enough data for a comparison."
    return (f"Building {fmt_count(xmax)} keys from empty, {r[0][1]} averages {fmt_ns(r[0][0])} ns per "
            f"insert and {r[-1][1]} {fmt_ns(r[-1][0])} ns, growth included.")


def churn_takeaway(series, med, dist, slots):
    r = rank_at(series, CARD_LOAD, TABLES)
    if len(r) < 2:
        return "Not enough data for a comparison."
    grew = sorted({t for (o, t, d, p), b in med.items()
                   if o == "churn" and d == dist and t in OPEN_TABLES
                   and b.get("final_capacity", 0) > slots})
    s = (f"At load {CARD_LOAD:.2f}, an erase plus insert costs {fmt_ns(r[0][0])} ns in {r[0][1]} "
         f"and {fmt_ns(r[-1][0])} ns in {r[-1][1]}.")
    if grew:
        s += (" " + " and ".join(grew) + " doubled capacity during churn at some load, because "
              "live entries passed half the tombstone-inclusive limit.")
    return s


def latency_rows(latency, lookup, load):
    rows = [(r["table"], r["p50_ns"], r["p90_ns"], r["p99_ns"]) for r in latency["rows"]
            if r["lookup"] == lookup and abs(r["load"] - load) < 1e-9]
    return sorted(rows, key=lambda r: TABLES.index(r[0]))


def latency_takeaway(rows, lookup):
    if len(rows) < 2:
        return "Not enough data for a comparison."
    best = min(rows, key=lambda r: r[3])
    worst = max(rows, key=lambda r: r[3])
    return (f"{lookup.capitalize()} p99 at load {CARD_LOAD:.2f}: {best[0]} {fmt_ns(best[3])} ns, "
            f"{worst[0]} {fmt_ns(worst[3])} ns.")


# ---------- card.json ----------

def git_sha():
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()


def build_card(latency, command, sha, generated_at):
    rows = latency_rows(latency, "miss", CARD_LOAD)
    for label, p50, p90, p99 in rows:
        if not (all(math.isfinite(v) for v in (p50, p90, p99)) and p50 <= p90 <= p99):
            raise ValueError(f"bad latency row for {label}: {p50}, {p90}, {p99}")
    open_rows = [r for r in rows if r[0] in OPEN_TABLES]
    best = min(open_rows, key=lambda r: r[3])
    std = next((r for r in rows if r[0] == "std::unordered_map"), None)
    takeaway = f"{best[0]} p99 miss lookup {fmt_ns(best[3])} ns at load {CARD_LOAD:.2f}"
    if std:
        takeaway += f" vs {fmt_ns(std[3])} ns for std::unordered_map"
    takeaway += "."
    return {
        "schema_version": 1,
        "repo": "hashbench",
        "sha": sha,
        "generated_at": generated_at,
        "command": command,
        "data_note": (f"Synthetic keys from a fixed seed ({latency['context']['seed']}), "
                      f"{latency['context']['slots']} slots, uniform 64-bit keys, one machine."),
        "metric": {"label": f"{best[0]} p99 unsuccessful lookup at load {CARD_LOAD:.2f}",
                   "value": round(best[3], 2), "unit": "ns", "higher_is_better": False},
        "takeaway": takeaway[:140],
        "chart": {
            "type": "interval",
            "x_label": f"Unsuccessful lookup latency at load {CARD_LOAD:.2f} (p50, p90, p99)",
            "x_unit": "ns",
            "x_scale": "linear",
            "rows": [{"label": r[0], "low": round(r[1], 2), "mid": round(r[2], 2), "high": round(r[3], 2)}
                     for r in rows],
        },
    }


# ---------- page ----------

CSS = """
:root{color-scheme:light;--surface:#fcfcfb;--page:#f4f3ef;--text:#0b0b0b;--text2:#52514e;--muted:#898781;
--grid:#e1e0d9;--border:rgba(11,11,11,.10);--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--surface:#1a1a19;
--page:#111110;--text:#f0efec;--text2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--border:rgba(255,255,255,.10);
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500}}
:root[data-theme="dark"]{color-scheme:dark;--surface:#1a1a19;--page:#111110;--text:#f0efec;--text2:#c3c2b7;
--muted:#898781;--grid:#2c2c2a;--border:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--text);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:760px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:0 0 4px}
p{margin:0 0 8px}.sub{color:var(--text2)}
.meta{font-size:13px;color:var(--text2);border:1px solid var(--border);border-radius:8px;padding:10px 12px;
background:var(--surface);margin:16px 0 8px}
.meta dt{float:left;clear:left;width:92px;color:var(--muted)}.meta dd{margin:0 0 2px 92px}
figure{margin:20px 0;padding:16px;background:var(--surface);border:1px solid var(--border);border-radius:10px}
.take{color:var(--text2);font-size:14px}
svg{width:100%;height:auto;display:block}
.grid{stroke:var(--grid);stroke-width:1}.axis{stroke:var(--muted);stroke-width:1}
.tick{fill:var(--muted);font-size:12px}.label,.rowlabel{fill:var(--text2);font-size:13px}
.empty{fill:var(--muted);font-size:14px}
.ln{fill:none;stroke-width:2}.mk{stroke:var(--surface);stroke-width:2}
.rng{stroke-width:6;stroke-linecap:round;opacity:.45}.p90{stroke-width:2}.p99{stroke:var(--text2);stroke-width:2}
.ln.s1,.rng.s1,.p90.s1{stroke:var(--s1)}.ln.s2,.rng.s2,.p90.s2{stroke:var(--s2)}
.ln.s3,.rng.s3,.p90.s3{stroke:var(--s3)}.ln.s4,.rng.s4,.p90.s4{stroke:var(--s4)}
.mk.s1{fill:var(--s1)}.mk.s2{fill:var(--s2)}.mk.s3{fill:var(--s3)}.mk.s4{fill:var(--s4)}
.legend{list-style:none;display:flex;flex-wrap:wrap;gap:4px 16px;padding:0;margin:8px 0;font-size:13px;color:var(--text2)}
.legend li{display:flex;align-items:center;gap:6px}.legend svg{width:34px;height:14px}
details{margin-top:8px;font-size:13px}summary{cursor:pointer;color:var(--text2)}
table{border-collapse:collapse;margin-top:6px;width:100%}caption{text-align:left;color:var(--muted)}
th,td{text-align:left;padding:3px 8px 3px 0;border-bottom:1px solid var(--grid)}
td{font-variant-numeric:tabular-nums}
code{font-size:13px}
"""


def figure(title, takeaway, legend_names, svg, table):
    leg = legend(legend_names) if legend_names else ""
    return (f"<figure><h2>{esc(title)}</h2><p class=take>{esc(takeaway)}</p>{leg}{svg}{table}</figure>")


def line_figure(title, takeaway, series, x_label, y_label, x_log=False, x_fmt=lambda v: f"{v:.2f}",
                x_head="Load factor"):
    rows = [(t, x_fmt(x), fmt_ns(y)) for t, pts in series.items() for x, y in pts]
    return figure(title, takeaway, list(series), line_chart(series, x_label, y_label, x_log, x_fmt),
                  data_table(title, ["Table", x_head, "ns per op"], rows))


def interval_figure(title, takeaway, rows, x_label):
    trows = [(r[0], fmt_ns(r[1]), fmt_ns(r[2]), fmt_ns(r[3])) for r in rows]
    return figure(title, takeaway, [r[0] for r in rows], interval_chart(rows, x_label),
                  data_table(title, ["Table", "p50 ns", "p90 ns", "p99 ns"], trows))


def build_page(bench, latency, machine, command, sha, generated_at):
    med = medians(bench)
    ctx = bench["context"]
    figs = []
    for dist in ("uniform", "sequential"):
        for op, what, title in (("lookup_hit", "hit", "Successful lookup"),
                                ("lookup_miss", "miss", "Unsuccessful lookup")):
            s = line_series(med, op, dist)
            figs.append(line_figure(f"{title}, {dist} keys", lookup_takeaway(s, what) if s else "",
                                    s, "Load factor (entries / slots)", "Time per lookup (ns)"))
        s = line_series(med, "churn", dist)
        figs.append(line_figure(f"Erase-heavy churn, {dist} keys", churn_takeaway(s, med, dist, int(ctx["slots"])) if s else "",
                                s, "Starting load factor (entries / slots)", "Time per erase + insert (ns)"))
        s = line_series(med, "insert", dist)
        figs.append(line_figure(f"Insert from empty, {dist} keys", insert_takeaway(s) if s else "",
                                s, "Keys inserted (log scale)", "Time per insert (ns)",
                                x_log=True, x_fmt=fmt_count, x_head="Keys"))
    for lookup in ("hit", "miss"):
        rows = latency_rows(latency, lookup, CARD_LOAD)
        figs.append(interval_figure(
            f"Per-lookup latency spread, {'successful' if lookup == 'hit' else 'unsuccessful'}",
            latency_takeaway(rows, lookup), rows,
            f"ns per lookup at load {CARD_LOAD:.2f}: dot p50, tick p90, bar end p99"))

    lc = latency["context"]
    meta = [
        ("Machine", f"{machine['cpu']}, {machine['logical_cpus']} logical CPUs, {machine['os']} ({machine['arch']})"),
        ("Compiler", ctx.get("compiler", lc["compiler"])),
        ("Flags", f"{ctx.get('cxx_flags', lc['cxx_flags'])}, C++20"),
        ("Run", f"{generated_at}, commit {sha[:12]}, median of {bench['benchmarks'][0].get('repetitions', 1)} repetitions"),
        ("Data", f"Synthetic keys from seed {lc['seed']}. Lookups and churn use a fixed table of "
                 f"{lc['slots']:,} slots; load factor is the only variable."),
        ("Latency", f"Each sample times a batch of {lc['batch']} lookups and divides by {lc['batch']} "
                    f"({lc['batches']:,} batches); the clock ticks every {lc['clock_tick_ns']:.0f} ns, too "
                    f"coarse for single lookups."),
        ("Command", command),
    ]
    meta_html = "".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in meta)
    return f"""<!doctype html>
<html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>hashbench results</title><style>{CSS}</style></head>
<body><main>
<h1>hashbench results</h1>
<p class=sub>Linear probing, Robin Hood and Swiss-style open-addressing maps against std::unordered_map, uint64 keys and values. Lower is better on every chart. Hover a point for its value or open the data table.</p>
<dl class=meta>{meta_html}</dl>
{''.join(figs)}
<p class=sub>LinearMap stops at load 0.70, its growth limit. std::unordered_map uses its default std::hash and its bucket count as "slots". Generated by scripts/make_report.py.</p>
</main></body></html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--site", required=True)
    ap.add_argument("--command", required=True)
    ap.add_argument("--sha", help="commit that produced the run (default: git HEAD)")
    args = ap.parse_args()

    bench, latency, machine = load_results(args.results)
    sha = args.sha or git_sha()
    generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(args.site, exist_ok=True)
    with open(os.path.join(args.site, "index.html"), "w", encoding="utf-8") as f:
        f.write(build_page(bench, latency, machine, args.command, sha, generated_at))
    with open(os.path.join(args.site, "card.json"), "w", encoding="utf-8") as f:
        json.dump(build_card(latency, args.command, sha, generated_at), f, indent=1, allow_nan=False)
        f.write("\n")


if __name__ == "__main__":
    main()
