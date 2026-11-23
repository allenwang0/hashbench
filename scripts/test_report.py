"""Checks that the published report, README block and portfolio card say what
the committed benchmark results say, since readers take those numbers as-is.

Expected values are recomputed here straight from results/*.json, independently
of make_report.py's helpers, so a generator bug that shifts a number (fastest
run instead of the median, aggregate rows mixed with repetitions, rounding
before dividing) fails a test instead of reaching the page.

Run: python3 -m unittest discover -s scripts -p "test_*.py"
"""
import html
import json
import os
import pathlib
import re
import statistics
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
SCHEMA = ROOT / "scripts" / "card.schema.json"


def reps(op, table, dist, param):
    """Per-repetition wall-clock ns for one benchmark (aggregate rows excluded)."""
    scale = {"ns": 1.0, "us": 1e3, "ms": 1e6}
    out = []
    for b in json.loads((RESULTS / "bench.json").read_text())["benchmarks"]:
        if b.get("run_type") == "iteration" and b["run_name"].split("/")[:4] == [op, table, dist, param]:
            out.append(b["real_time"] * scale[b["time_unit"]] / (float(param) if op == "insert" else 1.0))
    return out


def text_of(page):
    return html.unescape(re.sub(r"<[^>]+>", "", page)).replace(" ", " ")


class ReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp())
        readme = cls.tmp / "README.md"
        readme.write_text((ROOT / "README.md").read_text())
        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_report.py"), "--results", str(RESULTS),
                        "--site", str(cls.tmp), "--command", "scripts/run_bench.sh", "--readme", str(readme)],
                       check=True, cwd=ROOT)
        cls.page = (cls.tmp / "index.html").read_text()
        cls.text = text_of(cls.page)
        cls.readme = readme.read_text()
        cls.card = json.loads((cls.tmp / "card.json").read_text())

    def test_hero_is_the_ratio_of_medians_not_of_fastest_runs(self):
        # The old headline used the fastest of 5 runs on a busy machine. The
        # hero must be the median of at least 10 repetitions, and positive.
        sw = reps("lookup_hit", "SwissMap", "uniform", "0.70")
        std = reps("lookup_hit", "std::unordered_map", "uniform", "0.70")
        self.assertGreaterEqual(len(sw), 10, "the report promises at least 10 repetitions")
        ratio = statistics.median(std) / statistics.median(sw)
        self.assertGreater(ratio, 1, "owner rule: the hero never states a zero or negative result")
        hero = re.search(r'<p class="hero-metric"><span class="value">([^<]+)</span>', self.page)
        self.assertIsNotNone(hero, "hero metric missing")
        self.assertEqual(hero.group(1), f"{ratio:.2f}×")
        self.assertIn(f"{statistics.median(sw):.1f} ns vs {statistics.median(std):.1f} ns per lookup, "
                      f"median of {len(sw)} runs", self.text)
        if min(sw) != statistics.median(sw):
            self.assertNotIn(f"{min(std) / min(sw):.2f}×", self.text.split("Evidence ledger")[0][:4000],
                             "hero must not use the fastest-run ratio")

    def test_miss_percentiles_are_medians_across_passes(self):
        rows = json.loads((RESULTS / "latency.json").read_text())["rows"]
        row = next(r for r in rows if r["table"] == "SwissMap" and r["lookup"] == "miss" and r["load"] == 0.7)
        self.assertGreaterEqual(len(row["p99_ns"]), 10)
        self.assertIn(f"SwissMap p50 {statistics.median(row['p50_ns']):.1f}, p90 "
                      f"{statistics.median(row['p90_ns']):.1f}, p99 {statistics.median(row['p99_ns']):.1f} ns",
                      self.text)

    def test_insert_and_sequential_effect_match_results(self):
        n = max(int(b["run_name"].split("/")[3]) for b in json.loads((RESULTS / "bench.json").read_text())["benchmarks"]
                if b["run_name"].startswith("insert/"))
        u = statistics.median(reps("insert", "std::unordered_map", "uniform", str(n)))
        s = statistics.median(reps("insert", "std::unordered_map", "sequential", str(n)))
        self.assertLess(s, u, "the page explains why sequential keys are faster for the baseline")
        self.assertIn(f"std::unordered_map inserts sequential keys {u / s:.2f}× faster than random ones", self.text)

    def test_page_names_measured_and_results_commits(self):
        # Three SHAs used to disagree (card, site, HEAD). Both commits on the
        # page are computed: the one measured and the one holding results/.
        machine = json.loads((RESULTS / "machine.json").read_text())
        res = subprocess.run(["git", "log", "-1", "--format=%H", "--", "results/bench.json"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertIn(f"/tree/{machine['git_sha']}", self.page)
        self.assertIn(f"/tree/{res}", self.page)
        self.assertEqual(self.card["sha"], res)
        lb = machine["load_before"]
        self.assertIn(" / ".join(f"{v:.2f}" for v in lb) + " before", self.text)

    def test_committed_page_card_and_readme_are_current(self):
        for name in ("index.html", "card.json"):
            self.assertEqual((self.tmp / name).read_text(), (ROOT / "site" / name).read_text(),
                             f"site/{name} is stale: run python3 scripts/make_report.py --readme README.md")
        self.assertEqual(self.readme, (ROOT / "README.md").read_text(), "README results block is stale")
        self.assertNotIn("3.9 ns", self.readme, "old best-of-5 headline left in the README")

    def test_card_validates(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed; run scripts/setup.sh")
        jsonschema.validate(self.card, json.loads(SCHEMA.read_text()))
        self.assertLessEqual(len(self.card["takeaway"]), 140)

    def test_every_figure_has_title_desc_and_data(self):
        figures = re.findall(r'<figure class="figure".*?</figure>', self.page, re.S)
        self.assertEqual(len(figures), 3)
        for fig in figures:
            self.assertIn("Show data table", fig)
            self.assertIn("Data: ", fig)
            for svg in re.findall(r"<svg.*?</svg>", fig, re.S):
                self.assertIn("<title", svg)
                self.assertIn("<desc", svg)
        self.assertNotRegex(self.page, r"NaN|\binf\b")

    def test_evidence_ids_resolve(self):
        cited = set(re.findall(r'href="#evidence-(e\d+)"', self.page))
        rows = set(re.findall(r'id="evidence-(e\d+)"', self.page))
        self.assertTrue(cited)
        self.assertLessEqual(cited, rows, f"unresolved evidence ids {cited - rows}")


if __name__ == "__main__":
    unittest.main()
