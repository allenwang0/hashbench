"""Build site/index.html and site/card.json (and optionally the README results
block) from the committed benchmark JSON in results/.

Usage:
  python3 scripts/make_report.py --results results --site site --command "<cmd>"
      [--readme README.md]

Python standard library only. Every number on the page is computed here from
results/bench.json (Google Benchmark, every repetition kept),
results/latency.json (hb_latency, every pass kept), results/machine.json, or
from constants in the committed sources; none is typed by hand. Medians are
the headline; min and max show the spread across repetitions. Page components
and charts come from the vendored report kit in report-kit/.
"""
import argparse
import datetime
import json
import math
import os
import pathlib
import re
import statistics
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "report-kit"))
import report_kit as rk  # noqa: E402
from report_kit import Raw, esc  # noqa: E402

REPO = "allenwang0/hashbench"
REPO_URL = f"https://github.com/{REPO}"
PAGE_URL = "https://allenwang0.github.io/hashbench/"
HOME_URL = "https://allenwang0.github.io/"
TABLES = ["LinearMap", "RobinHoodMap", "SwissMap", "std::unordered_map"]
OPEN_TABLES = TABLES[:3]
SUBJECT = "SwissMap"
BASELINE = "std::unordered_map"
HERO_LOAD = "0.70"
README_START = "<!-- results:start -->"
README_END = "<!-- results:end -->"
THIN = "\u202f"  # narrow no-break space: a number never wraps away from its unit
DIST_LABEL = {"uniform": "random", "sequential": "sequential"}


# ---------------------------------------------------------------- loading

def load_results(results_dir):
    def read(name):
        with open(os.path.join(results_dir, name), encoding="utf-8") as f:
            return json.load(f)
    return read("bench.json"), read("latency.json"), read("machine.json")


def to_ns(entry):
    scale = {"ns": 1.0, "us": 1e3, "ms": 1e6, "s": 1e9}[entry["time_unit"]]
    return entry["real_time"] * scale


def spread(values):
    return {"median": statistics.median(values), "min": min(values), "max": max(values), "n": len(values),
            "runs": list(values)}


def repetitions(bench):
    """(op, table, dist, param) -> spread of ns per operation over repetition rows.

    Only per-repetition rows count; Google Benchmark's aggregate rows (mean,
    median, min) are ignored so the statistics here are computed one way.
    Insert rows time a whole build, so they are divided by the key count.
    """
    runs = {}
    for b in bench["benchmarks"]:
        if b.get("run_type", "iteration") != "iteration" or b.get("error_occurred"):
            continue
        op, table, dist, param = b["run_name"].split("/")[:4]
        value = to_ns(b) / float(param) if op == "insert" else to_ns(b)
        runs.setdefault((op, table, dist, param), []).append(value)
    if not runs:
        raise ValueError("bench.json has no per-repetition rows; rerun scripts/run_bench.sh")
    return {key: spread(v) for key, v in runs.items()}


def latency_stats(latency):
    """(table, lookup, load) -> {p50, p90, p99} medians across passes, plus pass count."""
    out = {}
    for r in latency["rows"]:
        stats = {}
        for p in ("p50", "p90", "p99"):
            vals = r[f"{p}_ns"]
            vals = vals if isinstance(vals, list) else [vals]
            stats[p] = statistics.median(vals)
            stats["passes"] = len(vals)
        if not (stats["p50"] <= stats["p90"] <= stats["p99"]) or not all(
                math.isfinite(stats[p]) for p in ("p50", "p90", "p99")):
            raise ValueError(f"bad latency row for {r['table']}: {stats}")
        out[(r["table"], r["lookup"], f"{r['load']:.2f}")] = stats
    return out


def git_last_commit(rel):
    """Commit that last changed a committed file, so links point at a SHA."""
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%H", "--", rel], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return out or None


def source_text(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def constant(rel, name):
    match = re.search(rf"\b{re.escape(name)}\s*=\s*([^;]+);", source_text(rel))
    if not match:
        raise ValueError(f"{name} not found in {rel}")
    return int(match.group(1).replace("'", "").strip())


def line_of(rel, needle, end=None):
    lines = source_text(rel).splitlines()
    first = next(i for i, line in enumerate(lines, 1) if needle in line)
    if end is None:
        return first, first
    return first, next(i for i, line in enumerate(lines, 1) if i >= first and end in line)


def facts_from_sources():
    loads = {}
    for table, rel in (("LinearMap", "include/hashbench/linear_map.h"),
                       ("RobinHoodMap", "include/hashbench/robin_hood_map.h"),
                       ("SwissMap", "include/hashbench/swiss_map.h")):
        loads[table] = (constant(rel, "kMaxLoadNum"), constant(rel, "kMaxLoadDen"))
    return {
        "max_load": loads,
        "group": constant("include/hashbench/swiss_map.h", "kGroupWidth"),
        "fuzz_seeds": constant("tests/fuzz_test.cpp", "kSeeds"),
        "fuzz_ops": constant("tests/fuzz_test.cpp", "kOpsPerSeed"),
        "fuzz_shapes": source_text("tests/fuzz_test.cpp").count("TYPED_TEST(FuzzTest,"),
        "fuzz_maps": len(OPEN_TABLES),
    }


# ---------------------------------------------------------------- formatting

def fmt_ns(v):
    """Nanoseconds: one decimal under 100 ns (sub-ns resolution from millions of
    iterations), integers above."""
    return f"{v:.1f}" if v < 100 else f"{v:,.0f}"


def ns(v):
    return f"{fmt_ns(v)}{THIN}ns"


def ratio_text(v):
    return f"{v:.2f}×"


def pct(v):
    return f"{v * 100:.1f}%" if abs(v) < 0.1 else f"{v * 100:.0f}%"


def count(n):
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1e6:g}{THIN}M" if n % 1_000_000 == 0 else f"{n:,}"
    return f"{n:,}"


def load_pct(param):
    return f"{float(param) * 100:.0f}%"


def runs_text(n):
    return "single run" if n == 1 else f"median of {n} runs"


def utc_stamp(date):
    return datetime.datetime.fromisoformat(date).astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def loadavg_text(values):
    return " / ".join(f"{v:.2f}" for v in values) if values else None


# ---------------------------------------------------------------- derived numbers

def derive(bench, latency, machine):
    """Every number the page, card and README show."""
    reps = repetitions(bench)
    lat = latency_stats(latency)
    loads = sorted({p for (op, _, d, p) in reps if op == "lookup_hit" and d == "uniform"}, key=float)
    sizes = sorted({p for (op, _, d, p) in reps if op == "insert"}, key=int)
    big = sizes[-1]
    hit = lambda t, dist="uniform", load=HERO_LOAD: reps[("lookup_hit", t, dist, load)]
    sw, std = hit(SUBJECT), hit(BASELINE)
    lat_load = HERO_LOAD
    miss = {t: lat[(t, "miss", lat_load)] for t in TABLES if (t, "miss", lat_load) in lat}
    ins = {(t, d): reps[("insert", t, d, big)] for t in TABLES for d in ("uniform", "sequential")}
    n_reps = {s["n"] for s in reps.values()}
    fastest_at = {p: min((t for t in TABLES if ("lookup_hit", t, "uniform", p) in reps),
                         key=lambda t: hit(t, load=p)["median"]) for p in loads}
    sw_curve = [hit(SUBJECT, load=p)["median"] for p in loads]
    seq_lookup = {t: hit(t, "sequential")["median"] / hit(t)["median"] for t in TABLES}
    ins_seq = {t: ins[(t, "sequential")]["median"] / ins[(t, "uniform")]["median"] for t in TABLES}
    lc = latency["context"]
    iters = {b["iterations"] for b in bench["benchmarks"]
             if b["run_name"].startswith("lookup_hit/") and b.get("run_type", "iteration") == "iteration"}
    return {
        "lookup_iters": min(iters),
        "reps": reps, "lat": lat, "loads": loads, "big": big,
        "n": min(n_reps), "n_max": max(n_reps),
        "sw": sw, "std": std, "ratio": std["median"] / sw["median"],
        "ratio_floor": std["min"] / sw["max"],
        "fastest_at": fastest_at, "sw_curve": sw_curve,
        "miss": miss, "lat_load": lat_load,
        "passes": min(s["passes"] for s in lat.values()),
        "resolution": lc["clock_tick_ns"] / lc["batch"],
        "ins": ins, "ins_seq": ins_seq, "seq_lookup": seq_lookup,
        "keys_at": round(float(HERO_LOAD) * int(bench["context"]["slots"])),
        "load_before": machine.get("load_before"), "load_after": machine.get("load_after"),
    }


# ---------------------------------------------------------------- charts

def hero_chart(d, uid, width):
    rows = [{"label": t, "value": d["reps"][("lookup_hit", t, "uniform", HERO_LOAD)]["median"],
             "lo": d["reps"][("lookup_hit", t, "uniform", HERO_LOAD)]["min"],
             "hi": d["reps"][("lookup_hit", t, "uniform", HERO_LOAD)]["max"], "accent": t == SUBJECT}
            for t in TABLES]
    desc = "; ".join(f"{r['label']} median {fmt_ns(r['value'])} ns (range {fmt_ns(r['lo'])} to {fmt_ns(r['hi'])})"
                     for r in rows) + f"; {d['n']} repetitions each."
    return rk.bar_chart(rows, uid=uid, width=width,
                        title=f"Median successful lookup time at {load_pct(HERO_LOAD)} load, random keys",
                        desc=desc,
                        axis_title="ns per lookup, bars from zero, whiskers min–max" if width >= 420
                        else "ns per lookup; whiskers min–max",
                        fmt=lambda v: f"{fmt_ns(v)}{THIN}ns")


def band_chart(d, uid, width, height, tables, op="lookup_hit", dist="uniform"):
    """Median line per table with a min–max band across repetitions; x = load factor."""
    reps, loads = d["reps"], d["loads"]
    series = []
    for t in tables:
        pts = [(float(p), reps[(op, t, dist, p)]) for p in loads if (op, t, dist, p) in reps]
        if pts:
            series.append((t, pts))
    narrow = width < 420
    label_w = max(rk.text_width(t, 12 if narrow else 13) for t, _ in series) + 14
    ml, mr, mt, mb = 40, 16 if narrow else label_w, 52 if narrow else 30, 46
    ymax = max(s["max"] for _, pts in series for _, s in pts)
    yt = rk.nice_ticks(0, ymax, 5)
    xs = [float(p) for p in loads]
    x0, x1 = 0.2, max(xs) + 0.05
    px = lambda v: ml + (width - ml - mr) * (v - x0) / (x1 - x0)
    py = lambda v: height - mb - (height - mt - mb) * v / yt[-1]
    parts = []
    for t in yt:
        parts.append(f'<path class="c-grid" d="M{ml} {rk.coord(py(t))}H{rk.coord(width - mr)}"/>')
        parts.append(rk.svg_text(ml - 8, py(t) + 4, f"{t:g}", "c-tick", "end"))
    for x in xs:
        parts.append(rk.svg_text(px(x), height - mb + 18, f"{x * 100:.0f}%", "c-tick", "middle"))
    parts.append(f'<path class="c-axis" d="M{ml} {rk.coord(height - mb)}H{rk.coord(width - mr)}"/>')
    ends = []
    for t, pts in sorted(series, key=lambda s: s[0] == SUBJECT):
        accent = t == SUBJECT
        top = "".join(("M" if k == 0 else "L") + f"{rk.coord(px(x))} {rk.coord(py(s['max']))}" for k, (x, s) in enumerate(pts))
        bottom = "".join(f"L{rk.coord(px(x))} {rk.coord(py(s['min']))}" for x, s in reversed(pts))
        fill = "var(--accent)" if accent else "var(--data-muted)"
        if t in (SUBJECT, BASELINE):  # two bands stay legible; the rest are in the data table
            parts.append(f'<path d="{top}{bottom}Z" style="fill:{fill};fill-opacity:{0.22 if accent else 0.3}"/>')
        line = "".join(("M" if k == 0 else "L") + f"{rk.coord(px(x))} {rk.coord(py(s['median']))}" for k, (x, s) in enumerate(pts))
        parts.append(f'<path class="{"c-line-accent" if accent else "c-stroke-muted"}" stroke-width="{2 if accent else 1.5}" d="{line}"/>')
        for x, s in pts:
            parts.append(f'<circle cx="{rk.coord(px(x))}" cy="{rk.coord(py(s["median"]))}" r="{3.5 if accent else 3}" '
                         f'class="{"c-accent" if accent else "c-muted"}"/>')
        lx, ls = pts[-1]
        ends.append([py(ls["median"]) + 4, px(lx) + 8, t, accent])
    # keep end labels at least 15 px apart, top to bottom
    ends.sort(key=lambda e: e[0])
    for i in range(1, len(ends)):
        ends[i][0] = max(ends[i][0], ends[i - 1][0] + 15)
    if narrow:  # too little room for end labels: a legend above the plot, in plotting order
        lx = 0.0
        for _, _, t, accent in sorted(ends, key=lambda e: not e[3]):
            cls = "c-line-accent" if accent else "c-stroke-muted"
            parts.append(f'<path class="{cls}" stroke-width="2" d="M{rk.coord(lx)} 34H{rk.coord(lx + 16)}"/>')
            parts.append(rk.svg_text(lx + 21, 38, t, "c-label c-hi" if accent else "c-label"))
            lx += 21 + rk.text_width(t, 12) + 18
    else:
        for y, x, t, accent in ends:
            parts.append(rk.svg_text(x, y, t, "c-label c-hi" if accent else "c-label"))
    parts.append(rk.svg_text(ml if not narrow else 0, 14, "ns per lookup (median; band = min–max)"
                             if not narrow else "ns per lookup (median, min–max band)", "c-title"))
    parts.append(rk.svg_text(width - mr, height - 8, "Load factor (entries ÷ slots)", "c-title", "end"))
    desc = "; ".join(f"{t}: " + ", ".join(f"{x * 100:.0f}% {fmt_ns(s['median'])} ns" for x, s in pts) for t, pts in series)
    return rk.svg(uid, width, height, f"Median successful lookup time by load factor, {DIST_LABEL[dist]} keys",
                  desc + f". Median of {d['n']} repetitions; band spans min to max.", "".join(parts))


def ladder(d, uid, width):
    """Miss latency p50 (dot), p90 (small dot), p99 (ring, labelled) per table, linear axis from zero."""
    rows = [(t, d["miss"][t]) for t in TABLES if t in d["miss"]]
    narrow = width < 420
    name_w = 0 if narrow else max(rk.text_width(t) for t, _ in rows) + 16
    row_h = 46 if narrow else 34
    top = 44
    plot_l, plot_r = name_w or 12, width - 56
    xt = rk.nice_ticks(0, max(s["p99"] for _, s in rows) * 1.05, 5)
    x = lambda v: plot_l + (plot_r - plot_l) * v / xt[-1]
    bottom = top + row_h * len(rows)
    parts = []
    for t in xt:
        parts.append(f'<path class="c-grid" d="M{rk.coord(x(t))} {top - 8}V{rk.coord(bottom)}"/>')
        parts.append(rk.svg_text(x(t), bottom + 16, f"{t:g}", "c-tick", "middle"))
    res = d["resolution"]
    # key, top-right: ring p99, small dot p90, dot p50
    kx = plot_r + 40
    for label, shape in (("p99", "ring"), ("p90", "small"), ("p50", "dot")):
        parts.append(rk.svg_text(kx, 14, label, "c-title", "end"))
        cx = kx - rk.text_width(label, 12) - 12
        parts.append({"ring": f'<circle cx="{rk.coord(cx)}" cy="10" r="4" class="c-stroke-muted" stroke-width="1.5"/>',
                      "small": f'<circle cx="{rk.coord(cx)}" cy="10" r="3" class="c-muted"/>',
                      "dot": f'<circle cx="{rk.coord(cx)}" cy="10" r="4.5" class="c-muted"/>'}[shape])
        kx = cx - 16
    for i, (t, s) in enumerate(rows):
        accent = t == SUBJECT
        cy = top + i * row_h + (row_h - 8 if narrow else row_h / 2)
        if narrow:
            parts.append(rk.svg_text(0, cy - 14, t, "c-label c-hi" if accent else "c-label"))
        else:
            parts.append(rk.svg_text(name_w - 12, cy + 4.5, t, "c-label c-hi" if accent else "c-label", "end"))
        stroke = "c-stroke-accent" if accent else "c-stroke-muted"
        fill = "c-accent" if accent else "c-muted"
        parts.append(f'<g data-row="{esc(t)}">')
        parts.append(f'<path class="{stroke}" stroke-width="2" d="M{rk.coord(x(s["p50"]))} {rk.coord(cy)}H{rk.coord(x(s["p99"]))}"/>')
        parts.append(f'<circle cx="{rk.coord(x(s["p90"]))}" cy="{rk.coord(cy)}" r="3" class="{fill}"/>')
        parts.append(f'<circle cx="{rk.coord(x(s["p50"]))}" cy="{rk.coord(cy)}" r="5" class="{fill}"/>')
        parts.append(f'<circle cx="{rk.coord(x(s["p99"]))}" cy="{rk.coord(cy)}" r="4.5" class="{stroke}" stroke-width="2" style="fill:var(--surface)"/>')
        parts.append(rk.svg_text(x(s["p99"]) + 9, cy + 4, fmt_ns(s["p99"]), "c-tick"))
        parts.append("</g>")
    if not narrow:  # on a phone the labels sit above the rows, where an axis line would cross them
        parts.append(f'<path class="c-axis" d="M{rk.coord(plot_l)} {top - 8}V{rk.coord(bottom)}"/>')
    parts.append(rk.svg_text(plot_r, bottom + 36, "ns per lookup (64-lookup batch average), axis from zero", "c-title", "end")
                 if not narrow else rk.svg_text(plot_r, bottom + 36, "ns per lookup, from zero", "c-title", "end"))
    desc = "; ".join(f"{t} p50 {fmt_ns(s['p50'])}, p90 {fmt_ns(s['p90'])}, p99 {fmt_ns(s['p99'])} ns" for t, s in rows)
    return rk.svg(uid, width, bottom + 44,
                  f"Unsuccessful lookup latency percentiles at {load_pct(d['lat_load'])} load",
                  desc + f". Median across {d['passes']} passes; timer resolution about {res:.1f} ns per lookup.",
                  "".join(parts))


def insert_chart(d, uid, width):
    rows = []
    for t in TABLES:
        for dist in ("uniform", "sequential"):
            s = d["ins"][(t, dist)]
            rows.append({"label": f"{t}, {DIST_LABEL[dist]}", "value": s["median"], "lo": s["min"], "hi": s["max"],
                         "accent": t == SUBJECT})
    desc = "; ".join(f"{r['label']} {fmt_ns(r['value'])} ns per key (range {fmt_ns(r['lo'])} to {fmt_ns(r['hi'])})"
                     for r in rows)
    return rk.bar_chart(rows, uid=uid, width=width,
                        title=f"Insert time per key, building {int(d['big']):,} keys from empty",
                        desc=desc + f"; median of {d['n']} repetitions.",
                        axis_title="ns per insert, growth included; bars from zero, whiskers min–max" if width >= 420
                        else "ns per insert; whiskers min–max",
                        fmt=lambda v: f"{fmt_ns(v)}{THIN}ns")


def swar_svg(uid, width, group):
    """Schematic of one SwissMap probe step: 8 control bytes compared at once."""
    cell = min(40, (width - 8) / group)
    h = 104
    parts = [rk.svg_text(0, 14, f"One probe step reads {group} control bytes as one 64-bit word", "c-title")]
    kinds = ["full", "empty", "full", "match", "deleted", "full", "empty", "full"][:group]
    for i, k in enumerate(kinds):
        x = i * cell
        cls = {"match": "c-accent", "empty": "c-band"}.get(k, "c-node-sunk")
        parts.append(f'<rect x="{rk.coord(x + 2)}" y="28" width="{rk.coord(cell - 4)}" height="{rk.coord(cell - 4)}" rx="6" '
                     f'class="{cls}"{" style=\"stroke:var(--text-3);stroke-dasharray:3 2;fill:none\"" if k == "empty" else ""}/>')
        mark = {"full": "h2", "match": "h2", "empty": "", "deleted": "×"}[k]
        if mark:
            parts.append(f'<text x="{rk.coord(x + cell / 2)}" y="{rk.coord(28 + cell / 2 + 1)}" '
                         f'class="{"c-label" if k != "match" else "c-value"}" text-anchor="middle" '
                         f'{"style=\"fill:var(--surface)\" " if k == "match" else ""}>{mark}</text>')
    mx = 3 * cell + cell / 2
    parts.append(f'<path class="c-line-accent" d="M{rk.coord(mx)} {rk.coord(28 + cell)}V{rk.coord(28 + cell + 14)}"/>')
    parts.append(rk.svg_text(min(mx, width - 150), 28 + cell + 30, "tag matches: compare this key", "c-label c-hi"))
    return rk.svg(uid, width, h, "SwissMap probe step",
                  f"A group of {group} control bytes: full slots hold a 7-bit tag of the key's hash, others are empty or "
                  f"deleted. One 64-bit comparison flags the slots whose tag matches; only those keys are compared.",
                  "".join(parts))


# ---------------------------------------------------------------- card and README

def build_card(d, latency, command, sha, generated_at):
    sw = d["sw"]
    rows = [{"label": t, "low": round(s["min"], 2), "mid": round(s["median"], 2), "high": round(s["max"], 2)}
            for t in TABLES for s in [d["reps"][("lookup_hit", t, "uniform", HERO_LOAD)]]]
    takeaway = (f"{SUBJECT} median lookup {fmt_ns(sw['median'])} ns at {load_pct(HERO_LOAD)} load vs "
                f"{fmt_ns(d['std']['median'])} ns for {BASELINE} ({runs_text(d['n'])}).")
    return {
        "schema_version": 1,
        "repo": "hashbench",
        "sha": sha,
        "generated_at": generated_at,
        "command": command,
        "data_note": (f"Synthetic keys from a fixed seed ({latency['context']['seed']}), "
                      f"{latency['context']['slots']} slots, random 64-bit keys, one machine."),
        "metric": {"label": f"{SUBJECT} median successful lookup at load {HERO_LOAD}",
                   "value": round(sw["median"], 2), "unit": "ns", "higher_is_better": False},
        "takeaway": takeaway[:140],
        "chart": {
            "type": "interval",
            "x_label": f"Successful lookup at load {HERO_LOAD}, min / median / max of {d['n']} runs",
            "x_unit": "ns",
            "x_scale": "linear",
            "rows": rows,
        },
    }


def readme_block(d, facts, machine):
    total = facts["fuzz_seeds"] * facts["fuzz_ops"] * facts["fuzz_shapes"]
    lines = [README_START,
             f"- **{ratio_text(d['ratio'])} faster lookups** for SwissMap than `{BASELINE}` at "
             f"{load_pct(HERO_LOAD)} full: {ns(d['sw']['median'])} vs {ns(d['std']['median'])}, random keys, "
             f"{runs_text(d['n'])} on an {machine['cpu']}.",
             f"- **SIMD-style probing in plain C++**: SwissMap checks {facts['group']} slots at once with bit tricks "
             f"on one 64-bit integer, no CPU-specific instructions.",
             f"- **Fuzzed**: {total:,} random operations per map ({facts['fuzz_seeds']} seeds × "
             f"{facts['fuzz_ops']:,} × {facts['fuzz_shapes']} key shapes), checked step by step against "
             f"`{BASELINE}`, also under ASan and UBSan.",
             "",
             f"| Table | Lookup, {load_pct(HERO_LOAD)} load | Insert per key, {int(d['big']):,} keys |",
             "| --- | ---: | ---: |"]
    for t in TABLES:
        s = d["reps"][("lookup_hit", t, "uniform", HERO_LOAD)]
        lines.append(f"| `{t}` | {fmt_ns(s['median'])} ns | {fmt_ns(d['ins'][(t, 'uniform')]['median'])} ns |")
    lines.append("")
    lines.append(f"Random 64-bit keys; median of {d['n']} runs. Spread and load average are in the report.")
    lines.append(README_END)
    return "\n".join(lines)


def update_readme(path, d, facts, machine):
    text = pathlib.Path(path).read_text(encoding="utf-8")
    if README_START not in text or README_END not in text:
        raise SystemExit(f"{path} has no {README_START} … {README_END} block")
    head, rest = text.split(README_START, 1)
    tail = rest.split(README_END, 1)[1]
    pathlib.Path(path).write_text(head + readme_block(d, facts, machine) + tail, encoding="utf-8")


# ---------------------------------------------------------------- page

def evidence_rows(d, facts, src_sha, res_sha):
    def blob(rel, sha, span=None):
        anchor = ""
        if span:
            anchor = f"#L{span[0]}" + (f"-L{span[1]}" if span[1] != span[0] else "")
        return f"{REPO_URL}/blob/{sha}/{rel}{anchor}"

    bench_href = blob("results/bench.json", res_sha)
    lat_href = blob("results/latency.json", res_sha)
    fuzz = line_of("tests/fuzz_test.cpp", "constexpr std::uint64_t kSeeds", "kOpsPerSeed")
    swiss = line_of("include/hashbench/swiss_map.h", "// Swiss-table style map", "// power of two.")
    mix = line_of("include/hashbench/hash.h", "constexpr std::uint64_t mix64", "return x;")
    stdmap = line_of("bench/common.h", "// std::unordered_map with the same calls", "class StdMap")
    sw, std = d["sw"], d["std"]
    m_sw, m_std = d["miss"][SUBJECT], d["miss"][BASELINE]
    i_sw, i_std = d["ins"][(SUBJECT, "uniform")], d["ins"][(BASELINE, "uniform")]
    total_fuzz = facts["fuzz_seeds"] * facts["fuzz_ops"] * facts["fuzz_shapes"]
    return [
        ("E1", f"{SUBJECT} {ns(sw['median'])} vs {BASELINE} {ns(std['median'])} per successful lookup at "
               f"{load_pct(HERO_LOAD)} load, {runs_text(d['n'])}", "results/bench.json (lookup_hit/…/uniform/0.70)",
         bench_href, "Measured"),
        ("E2", f"{SUBJECT} lookups {ratio_text(d['ratio'])} faster; slowest {SUBJECT} run vs fastest {BASELINE} run "
               f"{ratio_text(d['ratio_floor'])}", "ratios of E1 statistics", bench_href, "Derived"),
        ("E3", f"Miss p99 {ns(m_sw['p99'])} ({SUBJECT}) vs {ns(m_std['p99'])} ({BASELINE}) at "
               f"{load_pct(d['lat_load'])} load, median of {d['passes']} passes", "results/latency.json", lat_href,
         "Measured"),
        ("E4", f"Insert {ns(i_sw['median'])} vs {ns(i_std['median'])} per key, {int(d['big']):,} random keys",
         "results/bench.json (insert/…)", bench_href, "Measured"),
        ("E5", f"{BASELINE} inserts sequential keys {ratio_text(1 / d['ins_seq'][BASELINE])} faster than random keys",
         "ratio of E4-style medians", bench_href, "Derived"),
        ("E6", f"0 mismatches: {facts['fuzz_seeds']} seeds × {facts['fuzz_ops']:,} operations × "
               f"{facts['fuzz_shapes']} key shapes = {total_fuzz:,} per map, checked against {BASELINE}",
         f"tests/fuzz_test.cpp:{fuzz[0]}–{fuzz[1]}, run by ctest in CI (release and ASan + UBSan)",
         blob("tests/fuzz_test.cpp", src_sha, fuzz), "Test"),
        ("E7", f"{SUBJECT} compares {facts['group']} control bytes per step with 64-bit bit tricks, no intrinsics",
         f"include/hashbench/swiss_map.h:{swiss[0]}–{swiss[1]}", blob("include/hashbench/swiss_map.h", src_sha, swiss),
         "Code"),
        ("E8", "Open-addressing maps mix keys with the splitmix64 finalizer; the baseline uses std::hash as shipped",
         f"include/hashbench/hash.h:{mix[0]}–{mix[1]}; bench/common.h:{stdmap[0]}–{stdmap[1]}",
         blob("include/hashbench/hash.h", src_sha, mix), "Code"),
    ]


def prose(content):
    """Body paragraph held to the reading measure."""
    return rk.tag("p", content, class_="prose")


def toc(items):
    """Tier B inline contents list: the kit's nav style, not sticky."""
    lis = rk.join(Raw(f'<li>{rk.tag("a", label, href="#" + sid)}</li>') for sid, label in items)
    return Raw(f'<nav class="report-nav" aria-label="Contents" style="position:static"><ul>{lis}</ul></nav>')


def build_page(bench, latency, machine, command, src_sha, res_sha):
    d = derive(bench, latency, machine)
    facts = facts_from_sources()
    ev = {row[0]: row for row in evidence_rows(d, facts, src_sha, res_sha)}
    R = rk.ref
    ctx, lc = bench["context"], latency["context"]
    sw, std, n = d["sw"], d["std"], d["n"]
    runs = runs_text(n)
    slots = int(ctx["slots"])
    cpu = machine["cpu"]
    lb = loadavg_text(d["load_before"])
    la = loadavg_text(d["load_after"])
    bench_href, lat_href = ev["E1"][3], ev["E3"][3]
    total_fuzz = facts["fuzz_seeds"] * facts["fuzz_ops"] * facts["fuzz_shapes"]
    lin_num, lin_den = facts["max_load"]["LinearMap"]
    sw_num, sw_den = facts["max_load"]["SwissMap"]

    hero = rk.hero(
        eyebrow="Data structures · C++20 · Benchmark",
        title="hashbench",
        subtitle=Raw("Three open-addressing hash maps written from scratch — linear probing, Robin Hood and a "
                     "Swiss-table design — checked against <code>std::unordered_map</code> and timed on the same keys."),
        value=ratio_text(d["ratio"]),
        label=Raw(f"faster successful lookups for {SUBJECT} than <code>{BASELINE}</code>, "
                  f"{load_pct(HERO_LOAD)} full{R('E2')}"),
        support=Raw(f"{ns(sw['median'])} vs {ns(std['median'])} per lookup, {runs}{R('E1')}"),
        context=(f"Random 64-bit keys, {d['keys_at']:,} entries in {slots:,} slots; one thread on an {cpu}; "
                 f"load average {d['load_before'][0]:.1f} at the start (shared machine)." if d["load_before"] else
                 f"Random 64-bit keys, {d['keys_at']:,} entries in {slots:,} slots; one thread on an {cpu}."),
        actions=[("Source", REPO_URL, True), ("Run the benchmark", "#reproduce", False)],
        figure=rk.join([rk.tag("p", f"Successful lookup at {load_pct(HERO_LOAD)} load, {runs}", class_="card-label"),
                        rk.variants(hero_chart(d, "hero-d", 398), hero_chart(d, "hero-t", 640),
                                    hero_chart(d, "hero-p", 290))]),
    )

    nav = toc([("findings", "Findings"), ("design", "Designs"), ("lookup", "Lookups"), ("misses", "Misses"),
               ("inserts", "Inserts"), ("correctness", "Correctness"), ("methodology", "Method"),
               ("limitations", "Limits"), ("reproduce", "Reproduce"), ("evidence", "Evidence")])

    m_sw, m_std = d["miss"][SUBJECT], d["miss"][BASELINE]
    i_sw, i_std = d["ins"][(SUBJECT, "uniform")], d["ins"][(BASELINE, "uniform")]
    findings = rk.bento([
        (8, 2, rk.join([
            rk.tag("h3", f"{SUBJECT} checks {facts['group']} slots with one comparison", class_="card-heading"),
            Raw(f'<div style="max-width:360px;margin:4px 0 12px">{swar_svg("mini", 340, facts["group"])}</div>'),
            rk.tag("p", Raw(f"Each slot has a one-byte tag. A probe loads {facts['group']} tags as one 64-bit integer "
                            f"and flags matches with bit arithmetic, so most slots are ruled out without reading "
                            f"their keys.{R('E7')} <a href=\"#design\">The three designs</a>")),
        ])),
        (4, 1, rk.join([
            rk.tag("h3", f"Checked against {BASELINE}", class_="card-heading"),
            rk.metric("Fuzzed operations per map, 0 mismatches", count(total_fuzz)),
            rk.tag("p", Raw(f"Random inserts, erases and lookups compared step by step with the standard map; "
                            f"also run under ASan and UBSan in CI.{R('E6')}")),
        ])),
        (4, 3, rk.join([
            rk.tag("h3", "Slowest 1% of misses", class_="card-heading"),
            rk.comparison([(SUBJECT, m_sw["p99"], ns(m_sw["p99"]), True),
                           (BASELINE, m_std["p99"], ns(m_std["p99"]), False)], max(m_sw["p99"], m_std["p99"])),
            rk.tag("p", Raw(f"p99 of {lc['batch']}-lookup batches for absent keys, {load_pct(d['lat_load'])} "
                            f"load.{R('E3')} <a href=\"#misses\">Percentiles</a>")),
        ])),
        (4, 4, rk.join([
            rk.tag("h3", "Insert per key", class_="card-heading"),
            rk.comparison([(SUBJECT, i_sw["median"], ns(i_sw["median"]), True),
                           (BASELINE, i_std["median"], ns(i_std["median"]), False)],
                          max(i_sw["median"], i_std["median"])),
            rk.tag("p", Raw(f"Building {int(d['big']):,} random keys from empty, growth included.{R('E4')}")),
        ])),
        (4, 5, rk.join([
            rk.tag("h3", "Sequential keys help only the baseline", class_="card-heading"),
            rk.metric(f"{BASELINE} insert time, sequential vs random keys", f"−{pct(1 - d['ins_seq'][BASELINE])}"),
            rk.tag("p", Raw(f"The open-addressing maps scramble keys on purpose, so key order barely moves "
                            f"them.{R('E5')} <a href=\"#inserts\">Why</a>")),
        ])),
    ])
    findings_sec = rk.section("findings", "What the measurements show", findings, eyebrow="Findings")

    # Designs
    design_rows = []
    rule = {"LinearMap": "Next slot on collision",
            "RobinHoodMap": "Next slot; a newcomer farther from home takes the slot",
            "SwissMap": f"Groups of {facts['group']} slots, filtered by one-byte tags"}
    for t in OPEN_TABLES:
        num, den = facts["max_load"][t]
        design_rows.append([Raw(f"<code>{esc(t)}</code>"), rule[t], f"{num}/{den} ({num / den * 100:.1f}%)"])
    design_rows.append([Raw(f"<code>{esc(BASELINE)}</code>"), "Linked list per bucket (separate chaining)",
                        "1.0 (default max_load_factor)"])
    design_body = rk.join([
        prose(Raw(f"All three maps store keys and values directly in one array (open addressing) and share one "
                        f"API. A key's hash picks a home slot; on a collision the map probes further slots until it "
                        f"finds the key or an empty slot. The baseline instead allocates a node per entry and chains "
                        f"nodes per bucket. Every measured lookup uses a table fixed at {slots:,} slots, so the load "
                        f"factor — entries ÷ slots — is the only variable.")),
        rk.table("The four tables and their growth limits", ["Table", "Collision rule", "Grows past load"],
                 design_rows, wrap_cols={1}),
        prose(Raw(f"LinearMap grows at {lin_num / lin_den * 100:.0f}% full, so it has no point at {load_pct(d['loads'][-1])}. "
                        f"Robin Hood and SwissMap allow {sw_num / sw_den * 100:.1f}%.")),
    ])
    design_sec = rk.section("design", "Open addressing keeps every entry in one array", design_body, eyebrow="Primer")

    # Lookups by load
    loads = d["loads"]
    always = all(t == SUBJECT for t in d["fastest_at"].values())
    lo_load, hi_load = loads[0], loads[-1]
    curve = d["sw_curve"]
    take1 = (f"{SUBJECT}'s median rises only from {ns(curve[0])} to {ns(curve[-1])} between {load_pct(lo_load)} "
             f"and {load_pct(hi_load)} full")
    head1 = (f"{SUBJECT} has the fastest lookups at every load tested" if always
             else "Lookup time by load factor")
    std_band = [d["reps"][("lookup_hit", BASELINE, "uniform", p)] for p in loads]
    widest = max((s["max"] / s["median"] - 1, t, p) for t in TABLES for p in loads
                 if ("lookup_hit", t, "uniform", p) in d["reps"] for s in [d["reps"][("lookup_hit", t, "uniform", p)]])
    phone_note = Raw(f'<p class="card-label">Phone view: {SUBJECT} and {esc(BASELINE)}; the table has all four.</p>')
    fig1 = rk.figure(
        "fig-lookup", take1,
        rk.variants(band_chart(d, "lk-d", 1000, 400, TABLES), band_chart(d, "lk-t", 640, 340, TABLES),
                    rk.join([phone_note, band_chart(d, "lk-p", 300, 280, [SUBJECT, BASELINE])])),
        Raw(f"Successful lookups of random keys in a {slots:,}-slot table, probe order shuffled; median of {n} "
            f"interleaved repetitions of {d['lookup_iters']:,} lookups each. Bands for {SUBJECT} and {BASELINE} span "
            f"the fastest to the slowest repetition; the data table has every range. The widest band is {widest[1]} at {load_pct(widest[2])}, whose "
            f"slowest run was {pct(widest[0])} above its median — consistent with interference from other "
            f"processes on a shared machine.{R('E1')}"),
        question="How does lookup time change as the table fills?",
        table=rk.data_table("Successful lookup, ns", ["Table", "Load", "Median", "Min", "Max"],
                            [[t, load_pct(p), fmt_ns(s["median"]), fmt_ns(s["min"]), fmt_ns(s["max"])]
                             for t in TABLES for p in loads if ("lookup_hit", t, "uniform", p) in d["reps"]
                             for s in [d["reps"][("lookup_hit", t, "uniform", p)]]], numeric={2, 3, 4}),
        data_href=bench_href)
    lookup_sec = rk.section(
        "lookup", head1, rk.join([
            fig1,
            prose(Raw(f"The baseline's median rises from {ns(std_band[0]['median'])} to "
                            f"{ns(std_band[-1]['median'])} across the same loads. Every lookup in it follows a "
                            f"pointer to a separately allocated node, and more entries mean more nodes competing "
                            f"for cache. The open-addressing maps read from one array, and {SUBJECT} usually needs "
                            f"one group of tags plus one key comparison — consistent with its flatter curve, though "
                            f"cache misses were not counted. Even the slowest {SUBJECT} run at "
                            f"{load_pct(HERO_LOAD)} beat the fastest {BASELINE} run by "
                            f"{ratio_text(d['ratio_floor'])}.{R('E2')}")),
        ]), eyebrow="Lookups")

    # Misses
    rows = [(t, d["miss"][t]) for t in TABLES if t in d["miss"]]
    best_p99 = min(rows, key=lambda r: r[1]["p99"])
    fig2 = rk.figure(
        "fig-misses",
        f"{best_p99[0]}'s slowest 1% of misses take {ns(best_p99[1]['p99'])} or less at "
        f"{load_pct(d['lat_load'])} load" if best_p99[0] == SUBJECT else
        f"Miss latency percentiles at {load_pct(d['lat_load'])} load",
        rk.variants(ladder(d, "ms-d", 1000), ladder(d, "ms-t", 640), ladder(d, "ms-p", 300)),
        Raw(f"Lookups of keys that are absent, random keys, {load_pct(d['lat_load'])} load. Each sample times a "
            f"batch of {lc['batch']} lookups and divides by {lc['batch']}, because one lookup is shorter than the "
            f"{lc['clock_tick_ns']:.0f}{THIN}ns clock tick; resolution is about {d['resolution']:.1f}{THIN}ns per "
            f"lookup, and batching hides single-lookup outliers. {lc['batches']:,} batches per pass; each percentile "
            f"is the median across {d['passes']} passes.{R('E3')}"),
        question="How bad is the tail when the key is not there?",
        table=rk.data_table("Unsuccessful lookup percentiles, ns", ["Table", "p50", "p90", "p99"],
                            [[t, fmt_ns(s["p50"]), fmt_ns(s["p90"]), fmt_ns(s["p99"])] for t, s in rows],
                            numeric={1, 2, 3}),
        data_href=lat_href)
    lin = d["miss"].get("LinearMap")
    widest_tail = max(rows, key=lambda r: r[1]["p99"] - r[1]["p50"])[0]
    miss_text = ("A miss is the worst case for open addressing: the probe runs until it reaches an empty slot. "
                 f"{SUBJECT} stops at the first group of {facts['group']} that holds an empty tag, so long runs of "
                 f"full slots cost it whole groups at a time rather than one slot each.")
    if lin and widest_tail == "LinearMap":
        miss_text += (f" LinearMap has the widest gap between median and p99 ({ns(lin['p50'])} to "
                      f"{ns(lin['p99'])}), consistent with the long clusters that linear probing forms as the "
                      f"table fills.")
    misses_sec = rk.section("misses", f"{SUBJECT} keeps the miss tail lowest" if best_p99[0] == SUBJECT
                            else "Miss latency percentiles",
                            rk.join([fig2, rk.tag("p", miss_text)]), eyebrow="Tail latency")

    # Inserts
    seq_std = d["ins_seq"][BASELINE]
    open_change = max(abs(1 - d["ins_seq"][t]) for t in OPEN_TABLES)
    fig3 = rk.figure(
        "fig-inserts",
        f"{BASELINE} inserts sequential keys {ratio_text(1 / seq_std)} faster than random ones; the "
        f"open-addressing maps change by {pct(open_change)} or less",
        rk.variants(insert_chart(d, "in-d", 1000), insert_chart(d, "in-t", 640), insert_chart(d, "in-p", 300)),
        Raw(f"Time to build a map of {int(d['big']):,} keys from empty, divided by the key count; includes every growth "
            f"step. Sequential keys are 1, 2, 3, …; random keys are 63-bit values from seed {ctx['seed']}. Median of "
            f"{n} repetitions, whiskers min–max.{R('E4')}{R('E5')}"),
        question="Does key order change insert cost?",
        table=rk.data_table("Insert, ns per key", ["Table", "Keys", "Median", "Min", "Max"],
                            [[t, DIST_LABEL[dist], fmt_ns(s["median"]), fmt_ns(s["min"]), fmt_ns(s["max"])]
                             for t in TABLES for dist in ("uniform", "sequential") for s in [d["ins"][(t, dist)]]],
                            numeric={2, 3, 4}),
        data_href=bench_href)
    seq_lk = d["seq_lookup"][BASELINE]
    insert_text = rk.join([
        prose(Raw(f"The open-addressing maps pass every key through the splitmix64 finalizer, which spreads "
                        f"1, 2, 3, … across the table as if they were random.{R('E8')} The baseline uses "
                        f"<code>std::hash&lt;uint64_t&gt;</code> as a caller gets it; in libc++, the standard library "
                        f"Apple clang uses here, that hash returns the integer unchanged.")),
        prose(Raw(f"With an identity hash, consecutive keys land in consecutive buckets, so no two keys share "
                        f"a bucket and nodes allocated one after another sit near each other in memory. That is "
                        f"consistent with the sequential speed-up above and with sequential lookups at "
                        f"{load_pct(HERO_LOAD)} load taking {pct(seq_lk)} of the random-key time; the benchmark does "
                        f"not measure bucket occupancy or cache misses directly. The same identity hash would "
                        f"cluster badly in an open-addressing table, which is why these maps mix first.")),
    ])
    inserts_sec = rk.section("inserts", "Sequential keys speed up std::unordered_map, not the open-addressing maps",
                             rk.join([fig3, insert_text]), eyebrow="Inserts")

    # Correctness
    corr_body = rk.join([
        prose(Raw(f"A seeded random program of inserts, overwrites, erases, lookups and clears runs against "
                        f"each map and <code>std::unordered_map</code> side by side; every return value and the size "
                        f"must agree after every step, and a final walk must visit exactly the reference's entries. "
                        f"That is {facts['fuzz_seeds']} seeds × {facts['fuzz_ops']:,} operations for each of "
                        f"{facts['fuzz_shapes']} key shapes (small range, keys with the low 20 bits zero, full 64-bit), "
                        f"{total_fuzz:,} operations per map.{R('E6')}")),
        prose(Raw("Separate tests check Robin Hood's ordering invariant after every operation, that tombstones "
                        "stay bounded under churn, and growth and reserve behaviour. CI builds with GCC and Clang "
                        "on Linux and Apple clang on macOS, and repeats the tests under AddressSanitizer and "
                        "UndefinedBehaviorSanitizer.")),
    ])
    corr_sec = rk.section("correctness", f"Every map agrees with {BASELINE} on {count(total_fuzz)} random operations",
                          corr_body, eyebrow="Correctness")

    # Methodology
    method = rk.methodology([
        ("Hardware", f"{cpu}, {machine['logical_cpus']} logical CPUs; one benchmark thread"),
        ("Operating system", f"{machine['os']} ({machine['arch']})"),
        ("Compiler", Raw(f"{esc(ctx['compiler'])}, <code>{esc(ctx['cxx_flags'])}</code>, C++20, CMake release preset")),
        ("Harness", f"Google Benchmark {ctx['library_version']}; random interleaving of repetitions"),
        ("Code measured", Raw(f'<a class="src" href="{REPO_URL}/tree/{esc(src_sha)}">{esc(src_sha[:7])}</a>')),
        ("Results committed in", Raw(f'<a class="src" href="{REPO_URL}/tree/{esc(res_sha)}">{esc(res_sha[:7])}</a>')),
        ("Data", f"Synthetic keys from seed {ctx['seed']}: random 63-bit keys or 1…n; absent keys have bit 63 set"),
        ("Table size", f"{slots:,} slots for every lookup run; load = entries ÷ slots"),
        ("Repetitions", f"{n} per benchmark" + (f" ({d['n_max']} for some)" if d["n_max"] != n else "")),
        ("Aggregation", "median across repetitions; min and max shown as band or whiskers; wall-clock time"),
        ("Latency timer", f"std::chrono::steady_clock around {lc['batch']}-lookup batches; tick "
                          f"{lc['clock_tick_ns']:.0f} ns; {lc['batches']:,} batches × {d['passes']} passes; "
                          f"percentiles per pass (nearest rank), median across passes"),
        ("Load average", f"{lb} before, {la} after (1 / 5 / 15 min)" if lb and la else lb),
        ("Run date", ctx["date"][:10]),
    ])
    method_sec = rk.section("methodology", "Methodology", method, eyebrow="Conditions")

    limits = rk.limitations([
        ("One machine, one compiler.", f"All numbers come from an {cpu} with Apple clang and libc++; another CPU or "
                                       f"standard library can change both the ratio and the sequential-key effect."),
        ("Shared machine.", f"Load average was {lb} when the run started, so other processes competed for the CPU. "
                            f"Medians resist that better than single runs; bands show how far it reached."
         if lb else "Load average not recorded."),
        ("Batch-averaged percentiles.", f"Tail figures describe {lc['batch']}-lookup averages, not single lookups."),
        ("Fixed key and value types.", "Keys and values are uint64_t; no strings, iterators or custom allocators."),
        ("Synthetic keys.", "Random and sequential keys only; real key sets with structure may hash differently."),
    ])
    limits_sec = rk.section("limitations", "Results cover one machine, one compiler and synthetic keys", limits,
                            eyebrow="Limitations")

    steps_html = rk.reproduce([
        ("Create a local environment with the pinned CMake, Ninja, clang-format and jsonschema.",
         "scripts/setup.sh && source .venv/bin/activate"),
        ("Build in Release and run the tests (downloads GoogleTest and Google Benchmark). Expect "
         "“100% tests passed”.", "cmake --workflow --preset release"),
        (f"Run the benchmark ({n} interleaved repetitions; set HB_REPS to change) and the latency tool, then rebuild "
         f"this page, the card and the README table from results/.", "scripts/run_bench.sh"),
        ("Sanitizer build (Debug, ASan + UBSan), tests only.", "cmake --workflow --preset sanitize"),
        ("Check that the page's numbers match the committed results.",
         'python3 -m unittest discover -s scripts -p "test_*.py"'),
    ])
    repro_sec = rk.section("reproduce", "Reproduce it in five commands", steps_html, eyebrow="Reproduce",
                           intro=Raw(f'Commands run from a clone of <a class="src" href="{REPO_URL}">{REPO}</a>.'))

    ev_table = rk.table("Evidence ledger", ["ID", "Claim", "Source", "Class"],
                        [[Raw(f'<span id="evidence-{eid.lower()}">{eid}</span>'), claim,
                          Raw(f'<a class="src" href="{esc(href)}">{esc(src)}</a>'), cls]
                         for eid, claim, src, href, cls in ev.values()], wrap_cols={1, 2})
    ev_sec = rk.section("evidence", "Every number traces to a committed file", ev_table, eyebrow="Sources")

    main = rk.join([hero, nav, findings_sec, design_sec, lookup_sec, misses_sec, inserts_sec, corr_sec, method_sec,
                    limits_sec, repro_sec, ev_sec])
    footer = rk.footer(
        Raw(f"Generated by <code>scripts/make_report.py</code> from <code>results/</code> committed in "
            f"{esc(res_sha[:7])} · code {esc(src_sha[:7])} · run {esc(ctx['date'][:10])} on {esc(cpu)}"),
        Raw(f'<a href="{HOME_URL}">Allen Wang</a> · <a href="{REPO_URL}">Source</a>'))
    return rk.page(
        title="hashbench · Allen Wang",
        description=(f"Three open-addressing hash maps in C++20 against std::unordered_map: {SUBJECT} lookups "
                     f"{ratio_text(d['ratio'])} faster at {load_pct(HERO_LOAD)} load ({runs}, one thread, {cpu})."),
        canonical=PAGE_URL,
        header=rk.report_header(HOME_URL, "← Allen Wang", [("Source", REPO_URL)]),
        main=main, footer=footer)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default="results")
    ap.add_argument("--site", default="site")
    ap.add_argument("--command", default="scripts/run_bench.sh")
    ap.add_argument("--readme", help="rewrite the results block between the README markers")
    args = ap.parse_args()

    bench, latency, machine = load_results(args.results)
    rel = os.path.relpath(os.path.abspath(os.path.join(args.results, "bench.json")), ROOT)
    # The page names the commit that holds these results and the commit that was
    # measured; both are computed, never typed. Uncommitted results fall back to
    # the measured commit until they are committed and the page is rebuilt.
    src_sha = machine.get("git_sha") or git_last_commit(rel) or "0" * 40
    res_sha = git_last_commit(rel) or src_sha
    d = derive(bench, latency, machine)
    os.makedirs(args.site, exist_ok=True)
    with open(os.path.join(args.site, "index.html"), "w", encoding="utf-8") as f:
        f.write(build_page(bench, latency, machine, args.command, src_sha, res_sha))
    with open(os.path.join(args.site, "card.json"), "w", encoding="utf-8") as f:
        json.dump(build_card(d, latency, args.command, res_sha, utc_stamp(bench["context"]["date"])), f,
                  indent=1, allow_nan=False, ensure_ascii=False)
        f.write("\n")
    if args.readme:
        update_readme(args.readme, d, facts_from_sources(), machine)


if __name__ == "__main__":
    main()
