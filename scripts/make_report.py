"""Build site/index.html (report-kit v1 page) and site/card.json from the
benchmark JSON in results/. Standard library only.

The page loads the vendored kit from site/kit/ (copied by
report-design-system/kit/sync-kit.sh; never edit it)
and draws its charts client-side from one inline JSON block. Every number on
the page is computed here from results/*.json.

Statistic: the headline and the plotted lines use the median of the
repetitions. The fastest repetition ("min") and the standard deviation are
shown next to it so the spread is visible.

Usage: python3 scripts/make_report.py --results results --site site --command "<cmd>" [--sha <run commit>]
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
# Kit semantics: series-1 (blue) is the headline candidate, series-2 (orange)
# the baseline. RobinHoodMap gets a magenta instead of kit slot 4 (yellow),
# because yellow sits too close to the orange baseline (validated in
# LOCAL_CSS below).
COLORS = {"SwissMap": "var(--series-1)", "std::unordered_map": "var(--series-2)",
          "LinearMap": "var(--series-3)", "RobinHoodMap": "var(--hb-4)"}
ORDER = ["SwissMap", "std::unordered_map", "LinearMap", "RobinHoodMap"]
CARD_LOAD = 0.70
LOADS = ["0.25", "0.50", "0.70", "0.85"]
REPO = "https://github.com/allenwang0/hashbench"
HUB = "https://allenwang0.github.io/"


# ---------- loading ----------

def load_results(results_dir):
    def read(name):
        with open(os.path.join(results_dir, name), encoding="utf-8") as f:
            return json.load(f)
    return read("bench.json"), read("latency.json"), read("machine.json")


def ns(entry):
    scale = {"ns": 1.0, "us": 1e3, "ms": 1e6, "s": 1e9}[entry["time_unit"]]
    return entry["real_time"] * scale


def stats(bench):
    """(op, table, dist, param) -> {median, min, mean, sd, reps} in ns per operation.

    Insert benchmarks time n inserts per iteration, so they are divided by n.
    """
    out = {}
    for b in bench["benchmarks"]:
        if b.get("error_occurred") or b.get("run_type") != "aggregate":
            continue
        name = b.get("aggregate_name")
        if name not in ("median", "min", "mean", "stddev"):
            continue
        op, table, dist, param = b["run_name"].split("/")[:4]
        v = ns(b) / (float(param) if op == "insert" else 1.0)
        rec = out.setdefault((op, table, dist, param), {"reps": b.get("repetitions", 1)})
        rec["sd" if name == "stddev" else name] = v
    return {k: v for k, v in out.items() if "median" in v and "min" in v}


def get(st, op, table, dist, param):
    return st.get((op, table, dist, param))


def med(st, op, table, dist, param):
    r = get(st, op, table, dist, param)
    return r["median"] if r else None


def params(st, op, dist):
    ps = {p for (o, _, d, p) in st if o == op and d == dist}
    return sorted(ps, key=float)


def repetitions(st):
    return max((r["reps"] for r in st.values()), default=1)


# ---------- formatting (spec section 21) ----------

def f3(v):
    """3 significant figures."""
    if v is None or not math.isfinite(v):
        return "n/a"
    if v >= 100:
        return f"{v:.0f}"
    if v >= 10:
        return f"{v:.1f}"
    return f"{v:.2f}"


def ratio(a, b):
    r = a / b
    return f"{r:.2f}×" if r < 10 else f"{r:.1f}×" if r < 100 else f"{r:.0f}×"


def pct(load):
    return f"{round(float(load) * 100)}&#8239;%"


def fmt_count(n):
    n = int(n)
    if n >= 1 << 20:
        return f"{n >> 20}M"
    if n >= 1 << 10:
        return f"{n >> 10}K"
    return str(n)


def esc(s):
    return html.escape(str(s), quote=True)


# ---------- claims computed from the data ----------

def fastest_table(st, op, dist, param, tables=TABLES):
    vals = [(med(st, op, t, dist, param), t) for t in tables]
    vals = [v for v in vals if v[0] is not None]
    return min(vals)[1] if vals else None


def swiss_leads_from(st, ops, dists):
    """Smallest load from which SwissMap has the lowest median for every op and
    dist at that load and every higher load; None if it never does."""
    ok_from = None
    for p in reversed(LOADS):
        if all(fastest_table(st, o, d, p) == "SwissMap" for o in ops for d in dists
               if params(st, o, d) and p in params(st, o, d)):
            ok_from = p
        else:
            break
    return ok_from


def std_wins_all(st, op, dist):
    ps = params(st, op, dist)
    return bool(ps) and all(fastest_table(st, op, dist, p) == "std::unordered_map" for p in ps)


def latency_rows(latency, lookup, load):
    rows = [(r["table"], r["p50_ns"], r["p90_ns"], r["p99_ns"]) for r in latency["rows"]
            if r["lookup"] == lookup and abs(r["load"] - load) < 1e-9]
    return sorted(rows, key=lambda r: TABLES.index(r[0]))


def grown_tables(bench, dist, slots):
    """Open-addressing tables whose churn run ended above the fixed slot count."""
    out = set()
    for b in bench["benchmarks"]:
        op, table, d, _ = b["run_name"].split("/")[:4]
        if op == "churn" and d == dist and table in OPEN_TABLES and b.get("final_capacity", 0) > slots:
            out.add(table)
    return sorted(out)


# ---------- chart data for the page ----------

def panel(st, op, dist):
    ps = params(st, op, dist)
    xs = [math.log2(float(p)) if op == "insert" else float(p) for p in ps]
    series = []
    for t in ORDER:
        recs = [get(st, op, t, dist, p) for p in ps]
        if not any(recs):
            continue
        series.append({
            "key": t, "name": t, "color": COLORS[t],
            "y": [round(r["median"], 3) if r else None for r in recs],
            "min": [round(r["min"], 3) if r else None for r in recs],
            "sd": [round(r.get("sd", 0.0), 3) if r else None for r in recs],
            # Band: median +/- 1 sd, cut off at the fastest repetition
            # (no repetition was faster than "min").
            "lo": [round(max(r["min"], r["median"] - r.get("sd", 0.0)), 3) if r else None for r in recs],
            "hi": [round(r["median"] + r.get("sd", 0.0), 3) if r else None for r in recs],
        })
    return {"x": xs, "params": ps, "series": series}


def chart_data(st, latency):
    data = {"panels": {}, "latency": {}}
    for op in ("lookup_hit", "lookup_miss", "churn", "insert"):
        data["panels"][op] = {d: panel(st, op, d) for d in ("uniform", "sequential")}
    for lk in ("hit", "miss"):
        data["latency"][lk] = [{"table": t, "color": COLORS[t], "p50": p50, "p90": p90, "p99": p99}
                               for t, p50, p90, p99 in sorted(latency_rows(latency, lk, CARD_LOAD), key=lambda r: ORDER.index(r[0]))]
    return data


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
    takeaway = f"{best[0]} p99 miss lookup {f3(best[3])} ns at load {CARD_LOAD:.2f}"
    if std:
        takeaway += f" vs {f3(std[3])} ns for std::unordered_map"
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

LOCAL_CSS = """
/* hashbench local styles (report.local). Fourth series colour: kit slot 4 (yellow)
   is within dE 14 of the orange baseline, so this report uses magenta instead.
   Validated with the dataviz palette validator, all pairs, light and dark. */
:root{--hb-4:#c2449a}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--hb-4:#c255b0}}
:root[data-theme="dark"]{--hb-4:#c255b0}
.hb-pair{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:8px}
.hb-pair h4{font-size:14px;font-weight:600;color:var(--ink-2);margin:12px 0 0}
.hb-pair .legend{display:none}
@media (max-width:767px){.hb-pair{grid-template-columns:1fr;gap:8px}}
.hb-legend{margin-top:16px}
.hb-legend .key{display:inline-block;width:16px;height:3px;border-radius:2px;vertical-align:middle}
.hero .caveat{margin-top:24px;max-width:720px}
.paired .track .b{min-width:4px}
h1,h2,h3{overflow-wrap:break-word}
"""

LOCAL_JS = r"""
addEventListener("DOMContentLoaded", function () {
  var D = RK.data("hb-data"), f3 = function (v) { return v == null ? "—" : v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2); };
  var cnt = function (v) { var n = Math.round(Math.pow(2, v)); return n >= 1048576 ? (n / 1048576) + "M" : n >= 1024 ? (n / 1024) + "K" : String(n); };
  function fresh(id) { var el = document.getElementById(id), n = el.cloneNode(false); el.replaceWith(n); return n; }
  // One persistent chart element per (figure, op, dist): kit charts keep a ResizeObserver on their element,
  // so charts are drawn once and shown/hidden rather than redrawn into a replaced node.
  function slot(fig, op, d) {
    var host = document.getElementById(fig + "-" + d), id = fig + "-" + op + "-" + d, el = document.getElementById(id), made = !el;
    if (made) { el = document.createElement("div"); el.id = id; host.appendChild(el); }
    Array.prototype.forEach.call(host.children, function (c) { c.hidden = c !== el; });
    return made ? el : null;
  }
  function draw(fig, op) {
    var cfg = FIGS[fig], P = D.panels[op], ymax = 0;
    ["uniform", "sequential"].forEach(function (d) { P[d].series.forEach(function (s) { s.hi.forEach(function (v) { if (v != null && v > ymax) ymax = v; }); }); });
    ["uniform", "sequential"].forEach(function (d) {
      var p = P[d], isIns = op === "insert", el = slot(fig, op, d);
      if (!el) return;
      RKCharts.line(el, {
        x: p.x, series: p.series.map(function (s) { return { key: s.key, name: s.name, color: s.color, y: s.y, lo: s.lo, hi: s.hi }; }),
        xLabel: cfg.xLabel, yLabel: cfg.yLabel, yUnit: "ns", ymax: ymax, endLabels: false, format: function (v) { return +v.toPrecision(3) + ""; },
        xTicks: p.x, xFormat: isIns ? cnt : function (v) { return Math.round(v * 100) + " %"; },
        title: cfg.title + ", " + (d === "uniform" ? "random" : "sequential") + " keys", height: 260
      });
    });
    var rows = [];
    ["uniform", "sequential"].forEach(function (d) { var p = D.panels[op][d]; p.series.forEach(function (s) { p.x.forEach(function (x, i) { if (s.y[i] == null) return;
      rows.push({ k: d === "uniform" ? "Random" : "Sequential", t: s.name, x: op === "insert" ? cnt(x) : Math.round(x * 100) + " %", m: f3(s.y[i]), f: f3(s.min[i]), sd: f3(s.sd[i]) }); }); }); });
    RKCharts.table(fresh(fig + "-table"), { caption: cfg.title + " — ns per operation, " + D.reps + " repetitions", columns: [
      { key: "k", label: "Keys" }, { key: "t", label: "Table", align: "left" }, { key: "x", label: op === "insert" ? "Keys inserted" : "Load" },
      { key: "m", label: "Median (ns)" }, { key: "f", label: "Fastest (ns)" }, { key: "sd", label: "Std dev (ns)" }], rows: rows });
    var seg = document.querySelector("#" + fig + " [data-hb-view] [aria-checked='true']"), table = seg && seg.dataset.value === "table";
    document.getElementById(fig + "-table").hidden = !table; document.getElementById(fig + "-plots").hidden = table;
  }
  var FIGS = {
    "f-lookup": { title: "Lookup time vs load", xLabel: "Load factor (entries / slots)", yLabel: "Time per lookup (ns)" },
    "f-churn": { title: "Erase + insert time vs load", xLabel: "Starting load factor", yLabel: "Time per erase + insert (ns)" },
    "f-insert": { title: "Insert time vs keys inserted", xLabel: "Keys inserted from empty (log scale)", yLabel: "Time per insert, growth included (ns)" }
  };
  // Chart/Table toggles for the two-panel figures (kit toggles handle .plot, these hide the panel wrapper).
  document.querySelectorAll("[data-hb-view]").forEach(function (seg) {
    var fig = seg.closest(".figure").id;
    RK.segmented(seg, function (v) { document.getElementById(fig + "-plots").hidden = v !== "chart"; document.getElementById(fig + "-table").hidden = v !== "table"; });
  });
  RK.segmented("#lookup-op", function (v) { draw("f-lookup", v); document.getElementById("lookup-live").textContent = v === "lookup_hit" ? "Showing successful lookups." : "Showing unsuccessful lookups."; }, { param: "lookup" });
  draw("f-churn", "churn"); draw("f-insert", "insert");
  function lat(lk) {
    var L = D.latency[lk];
    return { label: lk === "hit" ? "Successful" : "Unsuccessful", series: L.reduce(function (o, r) { o[r.table] = { lo: r.p50, mid: r.p50, hi: r.p99, mark: r.p99, tip: "p50 " + f3(r.p50) + " · p90 " + f3(r.p90) + " · p99 " + f3(r.p99) + " ns" }; return o; }, {}) };
  }
  var LS = D.latency.hit.map(function (r) { return { key: r.table, name: r.table, color: r.color }; });
  RKCharts.dotRange("#lat-plot", { rows: [lat("hit"), lat("miss")], series: LS, xLabel: "Time per lookup, batch mean (ns)", unit: "ns", xmin: 0, title: "Per-lookup latency percentiles at 70 % load" });
  var lrows = []; ["hit", "miss"].forEach(function (lk) { D.latency[lk].forEach(function (r) { lrows.push({ k: lk === "hit" ? "Successful" : "Unsuccessful", t: r.table, a: f3(r.p50), b: f3(r.p90), c: f3(r.p99) }); }); });
  RKCharts.table("#lat-table", { caption: "Per-lookup latency at 70 % load, ns (each sample = mean of one batch)", columns: [{ key: "k", label: "Lookup" }, { key: "t", label: "Table", align: "left" }, { key: "a", label: "p50" }, { key: "b", label: "p90" }, { key: "c", label: "p99" }], rows: lrows });
  document.getElementById("lat-table").hidden = true;
});
"""


def legend_html():
    items = "".join(f'<span><span class="key" style="background:{COLORS[t]}"></span>{esc(t)}</span>' for t in ORDER)
    return f'<div class="legend hb-legend" aria-hidden="true">{items}</div>'


def two_panel_figure(fid, title, sub, extra_controls=""):
    view = ('<div class="seg" role="radiogroup" aria-label="View" data-hb-view>'
            '<button role="radio" aria-checked="true" data-value="chart">Chart</button>'
            '<button role="radio" aria-checked="false" data-value="table">Table</button></div>')
    return f"""<figure class="figure" id="{fid}" aria-labelledby="{fid}-h">
  <div class="fig-head"><div><h3 id="{fid}-h">{title}</h3><p class="sub">{sub}</p></div>{extra_controls}</div>
  {legend_html()}
  <div id="{fid}-plots" class="hb-pair">
    <div><h4>Random keys</h4><div id="{fid}-uniform"></div></div>
    <div><h4>Sequential keys</h4><div id="{fid}-sequential"></div></div>
  </div>
  <div id="{fid}-table" hidden></div>
  <div class="toolbar">{view}</div>"""  # caller adds the figcaption and closes </figure>


def card(label, value, unit, cmp, foot, href):
    return (f'<a class="card span-4" href="{href}"><span class="label">{label}</span>'
            f'<span class="metric num">{value} <span class="unit">{unit}</span></span>'
            f'<span class="cmp">{cmp}</span><span class="foot">{foot}</span></a>')


def build_page(bench, latency, machine, command, sha, generated_at):
    st = stats(bench)
    ctx, lc = bench["context"], latency["context"]
    reps = repetitions(st)
    slots = int(ctx.get("slots", lc["slots"]))
    L = f"{CARD_LOAD:.2f}"
    S, B = "SwissMap", "std::unordered_map"
    run_date = (ctx.get("date") or generated_at)[:10]

    hit_s, hit_b = med(st, "lookup_hit", S, "uniform", L), med(st, "lookup_hit", B, "uniform", L)
    if hit_s is None or hit_b is None:
        raise ValueError("results lack lookup_hit at load 0.70 for SwissMap and std::unordered_map")
    hs, hb = get(st, "lookup_hit", S, "uniform", L), get(st, "lookup_hit", B, "uniform", L)
    miss_s, miss_b = med(st, "lookup_miss", S, "uniform", L), med(st, "lookup_miss", B, "uniform", L)
    big = params(st, "insert", "uniform")[-1]
    ins_s, ins_b = med(st, "insert", S, "uniform", big), med(st, "insert", B, "uniform", big)
    ins_seq_s, ins_seq_b = med(st, "insert", S, "sequential", big), med(st, "insert", B, "sequential", big)
    ch_seq_s, ch_seq_b = med(st, "churn", S, "sequential", L), med(st, "churn", B, "sequential", L)
    ch_uni_s, ch_uni_b = med(st, "churn", S, "uniform", L), med(st, "churn", B, "uniform", L)
    rh85 = med(st, "churn", "RobinHoodMap", "uniform", "0.85")
    lead = swiss_leads_from(st, ("lookup_hit", "lookup_miss"), ("uniform", "sequential"))
    std_seq_churn = std_wins_all(st, "churn", "sequential")
    grew = grown_tables(bench, "uniform", slots) + [t for t in grown_tables(bench, "sequential", slots)
                                                    if t not in grown_tables(bench, "uniform", slots)]
    lat_hit = {r[0]: r for r in latency_rows(latency, "hit", CARD_LOAD)}
    lat_miss = {r[0]: r for r in latency_rows(latency, "miss", CARD_LOAD)}
    tails_below = (S in lat_hit and B in lat_hit and S in lat_miss and B in lat_miss
                   and lat_hit[S][3] < lat_hit[B][1] and lat_miss[S][3] < lat_miss[B][1])
    rh70 = med(st, "churn", "RobinHoodMap", "uniform", L)
    churn_fig = ("RobinHoodMap's churn cost roughly doubles between 70 % and 85 % full"
                 if rh85 and rh70 and rh85 >= 1.8 * rh70 else "Erase + insert cost by load factor")
    std_ins = [med(st, "insert", B, "uniform", p) for p in params(st, "insert", "uniform")]
    insert_fig = ("std::unordered_map's insert cost rises with table size on random keys"
                  if all(a is not None and b is not None and b > a for a, b in zip(std_ins, std_ins[1:]))
                  else "Insert cost by number of keys")
    low = LOADS[0]
    beaten_low = sorted({t for o in ("lookup_hit", "lookup_miss") for d in ("uniform", "sequential")
                         for t in OPEN_TABLES if t != S and med(st, o, t, d, low) is not None
                         and med(st, o, S, d, low) is not None and med(st, o, t, d, low) <= med(st, o, S, d, low)})
    low_note = (f" At {pct(low)} full, {' and '.join(beaten_low)} match or beat it in some cases."
                if beaten_low else "")
    lookup_fig = (f"SwissMap's line is lowest from {pct(lead)} full upward" if lead
                  else "Lookup time by load factor")
    others = [t for t in TABLES if t != S]
    lat_sep = all(S in rows and all(t in rows and rows[S][3] < rows[t][1] for t in others)
                  for rows in (lat_hit, lat_miss))
    lat_fig = ("SwissMap's p99 is below every other table's p50" if lat_sep
               else "Latency percentiles by table")
    seq_ins_note = (f"On sequential keys it is the fastest at {fmt_count(big)} ({f3(ins_seq_b)} ns against "
                    f"{f3(ins_seq_s)} ns for SwissMap)." if fastest_table(st, "insert", "sequential", big) == B else "")
    tick = lc["clock_tick_ns"]
    quantum = tick / lc["batch"]

    hero_ratio = ratio(hit_b, hit_s)
    h1 = f"SwissMap finds random keys {hero_ratio} faster than std::<wbr>unordered_map"
    lookup_h2 = (f"From {pct(lead)} full upward, SwissMap has the fastest lookups on both key patterns"
                 if lead else "No single table has the fastest lookups at every load")
    churn_h2 = ("Churn: SwissMap is cheapest on random keys, but std::unordered_map wins on sequential keys"
                if std_seq_churn and fastest_table(st, "churn", "uniform", L) == S
                else "Erase-plus-insert churn cost depends on the key pattern")
    insert_h2 = (f"Building {fmt_count(big)} random keys from empty, SwissMap takes {f3(ins_s)} ns per insert "
                 f"and {B} {f3(ins_b)} ns")
    lat_h2 = ("SwissMap's p99 lookup time is below std::unordered_map's median" if tails_below
              else "Per-lookup latency percentiles at 70 % load")

    data = chart_data(st, latency)
    data["reps"] = reps
    data_json = json.dumps(data, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")

    wb = max(hit_s, hit_b)
    paired = (f'<div class="paired" role="img" aria-label="Successful lookup, random keys, {CARD_LOAD:.0%} full: '
              f'SwissMap {f3(hit_s)} ns, {B} {f3(hit_b)} ns, median of {reps} runs. Lower is better.">'
              f'<span class="who">SwissMap<small>built here</small></span>'
              f'<span class="track"><span class="b" style="background:var(--series-1);width:{hit_s / wb * 70:.1f}%"></span>'
              f'<span class="v num">{f3(hit_s)} ns</span></span>'
              f'<span class="who">{B}<small>libc++ baseline</small></span>'
              f'<span class="track"><span class="b" style="background:var(--series-2);width:{hit_b / wb * 70:.1f}%"></span>'
              f'<span class="v num">{f3(hit_b)} ns</span></span></div>')

    cards = "".join([
        card(f"Unsuccessful lookup, {pct(L)} full", f3(miss_s), "ns",
             f"vs {f3(miss_b)} ns for {B} ({ratio(miss_b, miss_s)} faster)",
             f"Random keys · median of {reps} runs", "#lookup"),
        card(f"Insert {fmt_count(big)} keys from empty", f3(ins_s), "ns/key",
             f"vs {f3(ins_b)} ns for {B} ({ratio(ins_b, ins_s)} faster)",
             f"Random keys · resizes included · median of {reps}", "#insert"),
        card(f"Where {B} wins", f3(ch_seq_b), "ns",
             f"per erase + insert on sequential keys, vs {f3(ch_seq_s)} ns for SwissMap",
             f"{pct(L)} full · libc++ hashes integers to themselves", "#churn")
        if ch_seq_b < ch_seq_s else
        card("Erase + insert, sequential keys", f3(ch_seq_s), "ns",
             f"vs {f3(ch_seq_b)} ns for {B}", f"{pct(L)} full · median of {reps}", "#churn"),
    ])

    def row_stats(r):
        return f"median {f3(r['median'])} ns, fastest {f3(r['min'])} ns, standard deviation {f3(r.get('sd', 0))} ns"

    shows = [
        f"On random keys at {pct(L)} full, SwissMap's median successful lookup is {f3(hit_s)} ns against "
        f"{f3(hit_b)} ns for {B}.",
        f"Building {fmt_count(big)} random keys takes SwissMap {f3(ins_s)} ns per insert and {B} {f3(ins_b)} ns.",
    ] + ([
        "On sequential keys, std::unordered_map's identity hash makes it the cheapest table for churn.",
    ] if std_seq_churn else []) + [
    ]
    not_shows = [
        "Other machines or compilers: one Apple M5 Pro laptop, Apple LLVM, libc++.",
        "Real key distributions: keys are seeded random or consecutive integers.",
        "Keys or values other than uint64_t, or memory use.",
        f"Statistical certainty: {reps} repetitions in one session; a rerun moves the ratios.",
    ]
    env = [
        ("CPU", f"{machine['cpu']}, {machine['logical_cpus']} logical CPUs"),
        ("OS", f"{machine['os']} ({machine['arch']})"),
        ("Compiler", ctx.get("compiler", lc["compiler"])),
        ("Flags", f"{ctx.get('cxx_flags', lc['cxx_flags'])}, C++20"),
        ("Google Benchmark", ctx.get("library_version", "n/a")),
        ("Repetitions", f"{reps}, interleaved in random order"),
        ("Key seed", str(lc["seed"])),
        ("Table size", f"{slots:,} slots for lookups and churn"),
        ("Load average at start", ", ".join(f"{v:.1f}" for v in ctx.get("load_avg", [])) or "n/a"),
        ("Run date", run_date),
        ("Run commit", sha[:12]),
        ("Data", "results/bench.json, results/latency.json, results/machine.json"),
    ]
    env_rows = "".join(f'<tr><th scope="row">{esc(k)}</th><td>{esc(v)}</td></tr>' for k, v in env)
    grew_note = (f" {' and '.join(grew)} doubled capacity during churn at some load, because live entries "
                 "plus tombstones passed the growth limit." if grew else "")

    lookup_op_seg = ('<div class="seg" role="radiogroup" aria-label="Lookup type" id="lookup-op">'
                     '<button role="radio" aria-checked="true" data-value="lookup_hit">Successful</button>'
                     '<button role="radio" aria-checked="false" data-value="lookup_miss">Unsuccessful</button></div>')

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Hash map benchmark · Allen Wang</title>
<meta name="description" content="Three open-addressing hash maps written in C++20, benchmarked against std::unordered_map on lookups, churn and inserts across load factors.">
<link rel="icon" href="data:,">
<meta name="theme-color" content="#f5f5f7" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0b0b0b" media="(prefers-color-scheme: dark)">
<script>try{{var t=localStorage.getItem("theme");if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t)}}catch(e){{}}</script>
<link rel="stylesheet" href="kit/report.css">
<style>{LOCAL_CSS}</style>
<script src="kit/report.js" defer></script>
<script src="kit/charts.js" defer></script>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="bar"><nav class="wrap" aria-label="Report">
  <a class="back" href="{HUB}">← Allen Wang</a>
  <span class="title" aria-hidden="true">Hash map benchmark</span>
  <a class="pill" href="{REPO}">Code ↗</a>
</nav></header>
<main id="main">
  <section class="hero wrap" aria-labelledby="h1">
    <p class="eyebrow">Benchmark · C++20</p>
    <h1 id="h1">{h1}</h1>
    <p class="lede">Three open-addressing hash maps written from scratch (LinearMap, RobinHoodMap and SwissMap), timed against libc++'s <span class="mono">{B}</span> on 64-bit integer keys.</p>
    <p class="conditions"><span class="key">Synthetic keys</span><span>Median of {reps} interleaved runs</span><span>Apple M5 Pro, one machine</span></p>
    <div class="ratio"><span class="fig">{hero_ratio}</span><span class="cap">Successful lookups in a table {pct(L)} full, random keys: SwissMap against libc++'s {B}.</span></div>
    {paired}
    <p class="caveat"><b>{"Noisy point" if hs.get("sd", 0) > 0.2 * hs["median"] else "Spread"}.</b> SwissMap's {reps} runs here: {row_stats(hs)}; {B}: {row_stats(hb)}. This is one benchmark session, and a rerun gives a different ratio.</p>
  </section>

  <section class="wrap" aria-label="Key results">
    <div class="bento">{cards}</div>
  </section>

  <section class="s wrap" id="lookup" aria-labelledby="h-lookup">
    <p class="eyebrow">Lookups</p>
    <h2 id="h-lookup">{lookup_h2}</h2>
    <p class="lede">SwissMap compares 8 one-byte tags at once before it reads a key, so a lookup rarely touches more than one key.{low_note}</p>
    {two_panel_figure("f-lookup", lookup_fig, f"Median ns per lookup · {slots:,} slots · {reps} runs per point", lookup_op_seg)}
    <p class="vh" aria-live="polite" id="lookup-live"></p>
    <figcaption>Lines are medians of {reps} repetitions; shaded bands span ±1 standard deviation, cut off at the fastest repetition. Lower is better. LinearMap stops at {pct(L)} because that is its maximum load.</figcaption>
    </figure>
  </section>

  <section class="s wrap" id="churn" aria-labelledby="h-churn">
    <p class="eyebrow">Erase-heavy churn</p>
    <h2 id="h-churn">{churn_h2}</h2>
    <p class="lede">libc++ hashes an integer to itself, so consecutive keys land in consecutive buckets with no collisions. The open-addressing maps scramble keys on purpose, which costs them that free spread. On random keys at {pct(L)} full, an erase plus insert costs {f3(ch_uni_s)} ns in SwissMap and {f3(ch_uni_b)} ns in {B}.</p>
    {two_panel_figure("f-churn", churn_fig, f"Median ns per erase + insert · {reps} runs per point")}
    <figcaption>Each operation erases one key and inserts a new one, keeping the load roughly constant. Lines are medians; bands span ±1 standard deviation, cut off at the fastest run. At {pct('0.85')}, RobinHoodMap takes {f3(rh85)} ns on random keys.{grew_note}</figcaption>
    </figure>
  </section>

  <section class="s wrap" id="insert" aria-labelledby="h-insert">
    <p class="eyebrow">Building a table</p>
    <h2 id="h-insert">{insert_h2}</h2>
    <p class="lede">Each run starts from an empty table and includes every resize. {B} allocates a node for every entry, and its cost per random-key insert climbs from {f3(std_ins[0])} ns at {fmt_count(params(st, "insert", "uniform")[0])} keys to {f3(ins_b)} ns at {fmt_count(big)}. {seq_ins_note}</p>
    {two_panel_figure("f-insert", insert_fig, f"Median ns per insert, resizes included · {reps} runs per point")}
    <figcaption>The x axis doubles 3 times between ticks (1K to 4M keys, log scale). Lines are medians; bands span ±1 standard deviation, cut off at the fastest run.</figcaption>
    </figure>
  </section>

  <section class="s wrap" id="latency" aria-labelledby="h-lat">
    <p class="eyebrow">Per-lookup latency</p>
    <h2 id="h-lat">{lat_h2}</h2>
    <p class="lede">The clock ticks every {tick:.0f} ns, too coarse for a single lookup, so each sample times a batch of {lc['batch']} lookups and divides by {lc['batch']} ({lc['batches']:,} batches per table, {pct(L)} full, random keys).</p>
    <figure class="figure" aria-labelledby="f-lat-h">
      <div class="fig-head"><div><h3 id="f-lat-h">{lat_fig}</h3><p class="sub">ns per lookup, batch means · {lc['batches']:,} batches · resolution about {quantum:.1f} ns</p></div></div>
      <div id="lat-plot"></div><div id="lat-table" hidden></div>
      <div class="toolbar"><div class="seg" role="radiogroup" aria-label="View" data-view-toggle><button role="radio" aria-checked="true" data-value="chart">Chart</button><button role="radio" aria-checked="false" data-value="table">Table</button></div></div>
    <figcaption>Each line runs from p50 (filled dot) to p99 (ring); p90 is in the table and tooltip. Best of {lc['passes']} pass{'es' if lc['passes'] != 1 else ''} by median.</figcaption>
    </figure>
    <p class="caveat"><b>Batch means.</b> Each sample is the mean of {lc['batch']} lookups, so a single slow lookup is diluted. The true single-lookup tail is longer than shown.</p>
  </section>

  <section class="s wrap" id="method" aria-labelledby="h-method">
    <p class="eyebrow">Methodology</p>
    <h2 id="h-method">How this was measured</h2>
    <div class="prose" style="margin-top:24px">
      <p>Lookups and churn use a table pre-sized to {slots:,} slots and filled to a load factor of 25, 50, 70 or 85 %, so load is the only variable. Keys are 64-bit integers, either drawn from a seeded generator (random) or consecutive (sequential). Misses use keys that are guaranteed absent.</p>
      <p>Google Benchmark ran every case {reps} times in random interleaved order. The page reports the median of those {reps} runs. The fastest run and the standard deviation are in every table; the bench code also records the fastest run because it is the least disturbed by other processes.</p>
    </div>
    <div class="two">
      <div><h3>What this shows</h3><ul>{''.join(f'<li>{s}</li>' for s in shows)}</ul></div>
      <div><h3>What it doesn't show</h3><ul>{''.join(f'<li>{s}</li>' for s in not_shows)}</ul></div>
    </div>
  </section>

  <section class="s wrap" id="reproduce" aria-labelledby="h-repro">
    <p class="eyebrow">Reproduce</p>
    <h2 id="h-repro">Run it yourself</h2>
    <div class="codewrap"><pre><code>git clone {REPO}.git
cd hashbench
scripts/setup.sh                     # local venv with pinned CMake and Ninja
source .venv/bin/activate
cmake --workflow --preset release    # build and run the tests
scripts/run_bench.sh                 # benchmarks + this page (about 4 minutes)</code></pre></div>
    <details>
      <summary>Environment of the published run · {len(env)} rows</summary>
      <div class="tablewrap" tabindex="0" role="region" aria-label="Environment table"><table><thead><tr><th scope="col">Field</th><th scope="col">Value</th></tr></thead><tbody>{env_rows}</tbody></table></div>
    </details>
  </section>
</main>
<footer><div class="wrap"><span><a href="{HUB}">← Allen Wang</a><a href="{REPO}">Code ↗</a></span><span>Data from the run at commit <span class="mono">{sha[:7]}</span> · {run_date}</span></div></footer>
<script type="application/json" id="hb-data">{data_json}</script>
<script>{LOCAL_JS}</script>
</body>
</html>
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
