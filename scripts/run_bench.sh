#!/usr/bin/env bash
# Builds the release preset, runs both benchmark binaries and writes
# results/*.json, site/index.html and site/card.json.
# HB_REPS sets the number of interleaved repetitions (default 10). The load
# average is recorded just before and just after the timed runs.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -d .venv ]]; then export PATH="$PWD/.venv/bin:$PATH"; fi
reps="${HB_REPS:-10}"

cmake --preset release >/dev/null
cmake --build --preset release

loadavg() {
  if [[ "$(uname)" == Darwin ]]; then sysctl -n vm.loadavg | tr -d '{}'; else cut -d' ' -f1-3 /proc/loadavg; fi
}

mkdir -p results/raw
loadavg > results/raw/load_before.txt
./build/release/hb_bench \
  --benchmark_repetitions="$reps" \
  --benchmark_enable_random_interleaving=true \
  --benchmark_display_aggregates_only=true \
  --benchmark_out=results/raw/bench.json \
  --benchmark_out_format=json
./build/release/hb_latency > results/raw/latency.json
loadavg > results/raw/load_after.txt

python3 scripts/collect_results.py results/raw results
python3 scripts/make_report.py --results results --site site --command "scripts/run_bench.sh"
