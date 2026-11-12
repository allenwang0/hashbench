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


def synthetic_bench(skip=()):
    rows = []
    for t_i, table in enumerate(mr.TABLES):
        for dist in ("uniform", "sequential"):
            loads = [0.25, 0.5, 0.7] + ([] if table == "LinearMap" else [0.85])
            for op in ("lookup_hit", "lookup_miss", "churn"):
                for load in loads:
                    rows.append((op, table, dist, f"{load:.2f}", 5 + 10 * load + t_i, "ns"))
            for lg in (10, 13, 16):
                n = 1 << lg
                rows.append(("insert", table, dist, str(n), n * (20 + t_i) / 1e6, "ms"))
    out = []
    for op, table, dist, param, t, unit in rows:
        if (op, table) in skip:
            continue
        out.append({"run_name": f"{op}/{table}/{dist}/{param}", "run_type": "aggregate",
                    "aggregate_name": "min", "repetitions": 3, "real_time": t,
                    "time_unit": unit, "final_capacity": 1 << 20})
    return {"context": {"compiler": "synthetic", "cxx_flags": "-O3", "slots": str(1 << 20)},
            "benchmarks": out}


def synthetic_latency():
    rows = [{"table": t, "load": load, "lookup": lk, "p50_ns": 10.0 + i, "p90_ns": 15.0 + i,
             "p99_ns": 30.0 + 2 * i}
            for i, t in enumerate(mr.TABLES) for load in (0.5, 0.7) for lk in ("hit", "miss")]
    return {"context": {"compiler": "synthetic", "cxx_flags": "-O3", "slots": 1 << 20, "seed": 1,
                        "batch": 64, "batches": 100, "passes": 1, "clock_tick_ns": 41.0}, "rows": rows}


MACHINE = {"cpu": "synthetic CPU", "logical_cpus": 1, "os": "test", "arch": "test"}


class ReportTest(unittest.TestCase):
    def page(self, bench=None):
        return mr.build_page(bench or synthetic_bench(), synthetic_latency(), MACHINE, "cmd", SHA,
                             "2026-01-01T00:00:00Z")

    def test_every_chart_has_axis_labels_and_a_data_table(self):
        html = self.page()
        figures = html.count("<figure>")
        self.assertEqual(figures, 10)
        self.assertEqual(html.count("<details>"), figures)
        line_charts = re.findall(r'<svg viewBox="0 0 640 340".*?</svg>', html)
        self.assertEqual(len(line_charts), 8)
        for svg in line_charts:
            self.assertEqual(svg.count('class="label"'), 2, "x and y axis labels")
            self.assertIn("(ns)", svg, "y axis names its unit")

    def test_no_nan_or_infinity_reaches_the_svg(self):
        self.assertNotRegex(self.page(), r"NaN|inf\b")

    def test_takeaways_quote_the_numbers(self):
        html = self.page()
        # Synthetic miss times at load 0.70 are 12 + table index: SwissMap is 14 ns.
        self.assertIn("At load 0.70, LinearMap takes 12.0 ns per miss", html)

    def test_missing_series_shows_empty_state(self):
        skip = {("insert", t) for t in mr.TABLES}
        self.assertIn("No data for this view.", self.page(synthetic_bench(skip)))

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
