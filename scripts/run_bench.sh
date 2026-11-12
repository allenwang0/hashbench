#!/usr/bin/env bash
# Builds the release preset, runs both benchmark binaries and writes
# results/*.json, site/index.html and site/card.json.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -d .venv ]]; then export PATH="$PWD/.venv/bin:$PATH"; fi

cmake --preset release >/dev/null
cmake --build --preset release

mkdir -p results/raw
./build/release/hb_bench \
  --benchmark_repetitions=5 \
  --benchmark_enable_random_interleaving=true \
  --benchmark_report_aggregates_only=true \
  --benchmark_out=results/raw/bench.json \
  --benchmark_out_format=json
./build/release/hb_latency > results/raw/latency.json

python3 scripts/collect_results.py results/raw results
python3 scripts/make_report.py --results results --site site --command "scripts/run_bench.sh"
