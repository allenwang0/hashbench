"""Smoke tests for make_report.py on synthetic numbers (not benchmark results).

Run: python3 -m unittest discover -s scripts -p "test_*.py"
"""
import json
import os
import re
import unittest

import make_report as mr

SHA = "a" * 40
SCHEMA = os.path.join(os.path.dirname(__file__), "card.schema.json")


def synthetic_bench(skip=(), noisy_swiss=False):
    """Five aggregates per case. The fastest repetition (min) is always 1 ns
    below the median, so a page that headlines min instead of median fails."""
    rows = []
    for t_i, table in enumerate(mr.TABLES):
        for dist in ("uniform", "sequential"):
            loads = [0.25, 0.5, 0.7] + ([] if table == "LinearMap" else [0.85])
            for op in ("lookup_hit", "lookup_miss", "churn"):
                for load in loads:
                    # SwissMap (index 2) fastest, std::unordered_map (3) slowest.
                    base = {2: 4.0, 0: 8.0, 1: 9.0, 3: 20.0}[t_i]
                    rows.append((op, table, dist, f"{load:.2f}", base + 10 * load, "ns"))
            for lg in (10, 13, 16):
                n = 1 << lg
                rows.append(("insert", table, dist, str(n), n * (20 + t_i) / 1e6, "ms"))
    out = []
    for op, table, dist, param, t, unit in rows:
        if (op, table) in skip:
            continue
        n = float(param) if op == "insert" else 1.0
        one = 1e-6 * n if unit == "ms" else 1.0  # 1 ns per op, in the row's unit
        sd = 3 * one if (noisy_swiss and table == "SwissMap") else 0.5 * one
        for agg, v in (("median", t), ("min", t - one), ("mean", t + 0.2 * one), ("stddev", sd)):
            out.append({"run_name": f"{op}/{table}/{dist}/{param}", "run_type": "aggregate",
                        "aggregate_name": agg, "repetitions": 5, "real_time": v,
                        "time_unit": unit, "final_capacity": 1 << 20})
    return {"context": {"compiler": "synthetic", "cxx_flags": "-O3", "slots": str(1 << 20),
                        "date": "2026-01-01T00:00:00Z"}, "benchmarks": out}


def synthetic_latency():
    rows = [{"table": t, "load": load, "lookup": lk, "p50_ns": 10.0 + i, "p90_ns": 15.0 + i,
             "p99_ns": 30.0 + 2 * i}
            for i, t in enumerate(mr.TABLES) for load in (0.5, 0.7) for lk in ("hit", "miss")]
    return {"context": {"compiler": "synthetic", "cxx_flags": "-O3", "slots": 1 << 20, "seed": 1,
                        "batch": 64, "batches": 100, "passes": 1, "clock_tick_ns": 41.0}, "rows": rows}


MACHINE = {"cpu": "synthetic CPU", "logical_cpus": 1, "os": "test", "arch": "test"}


def page(bench=None):
    return mr.build_page(bench or synthetic_bench(), synthetic_latency(), MACHINE, "cmd", SHA,
                         "2026-01-01T00:00:00Z")


def page_data(html):
    m = re.search(r'<script type="application/json" id="hb-data">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


class ReportTest(unittest.TestCase):
    def test_headline_uses_the_median_not_the_fastest_run(self):
        # Medians at 0.70: SwissMap 11.0, std 27.0 -> 2.45x. Mins would give 26/10 = 2.60x.
        html = page()
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S).group(1)
        self.assertIn("2.45×", h1)
        self.assertNotIn("2.60×", html)
        self.assertNotIn("averages", html, "min-of-N must never be called an average")

    def test_spread_is_shown_next_to_the_headline(self):
        html = page(synthetic_bench(noisy_swiss=True))
        hero = html.split('aria-label="Key results"')[0]
        self.assertIn("fastest 10.0 ns", hero)
        self.assertIn("standard deviation 3.00 ns", hero)

    def test_every_plotted_point_carries_its_spread(self):
        data = page_data(page())
        for op, dists in data["panels"].items():
            for dist, p in dists.items():
                for s in p["series"]:
                    for y, lo, hi, mn in zip(s["y"], s["lo"], s["hi"], s["min"]):
                        if y is None:
                            continue
                        self.assertLessEqual(lo, y, (op, dist, s["key"]))
                        self.assertGreaterEqual(hi, y)
                        self.assertGreaterEqual(lo, mn, "band never goes below the fastest run")

    def test_std_wins_card_only_when_std_actually_wins(self):
        # In the synthetic data SwissMap is fastest everywhere, including sequential churn.
        self.assertNotIn("Where std::unordered_map wins", page())
        self.assertNotIn("identity hash makes it the cheapest", page())

    def test_linear_map_has_no_085_point(self):
        p = page_data(page())["panels"]["lookup_hit"]["uniform"]
        lin = next(s for s in p["series"] if s["key"] == "LinearMap")
        self.assertIsNone(lin["y"][p["params"].index("0.85")])

    def test_baseline_and_candidate_use_kit_slots(self):
        self.assertEqual(mr.COLORS["SwissMap"], "var(--series-1)")
        self.assertEqual(mr.COLORS["std::unordered_map"], "var(--series-2)")

    def test_no_nan_or_infinity_reaches_the_page(self):
        self.assertNotRegex(page(), r"NaN|Infinity|\binf\b")

    def test_missing_lookup_baseline_fails_loudly(self):
        skip = {("lookup_hit", "std::unordered_map")}
        with self.assertRaises(ValueError):
            page(synthetic_bench(skip))

    def test_card_validates_and_reports_best_open_addressing_p99(self):
        card = mr.build_card(synthetic_latency(), "cmd", SHA, "2026-01-01T00:00:00Z")
        self.assertEqual(card["metric"]["value"], 30.0)
        self.assertLessEqual(len(card["takeaway"]), 140)
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed; run scripts/setup.sh")
        with open(SCHEMA, encoding="utf-8") as f:
            jsonschema.validate(card, json.load(f))

    def test_card_rejects_unordered_percentiles(self):
        lat = synthetic_latency()
        lat["rows"][3]["p90_ns"] = 1.0
        with self.assertRaises(ValueError):
            mr.build_card(lat, "cmd", SHA, "2026-01-01T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
