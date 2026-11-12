// Google Benchmark suite. Names are op/table/distribution/parameter so
// scripts/make_report.py can parse them.
#include <benchmark/benchmark.h>

#include <cstdio>
#include <deque>
#include <string>

#include "common.h"

namespace {

using bench::Dist;

constexpr double kLoads[] = {0.25, 0.5, 0.7, 0.85};
constexpr int kInsertLog2[] = {10, 13, 16, 19, 22};

template <class Map>
bool fill(benchmark::State& state, Map& m, const std::vector<std::uint64_t>& keys) {
  m.rehash(bench::kSlots);
  for (std::uint64_t k : keys) m.insert_or_assign(k, k);
  if constexpr (!std::is_same_v<Map, bench::StdMap>) {
    if (m.capacity() != bench::kSlots) {
      state.SkipWithError("table grew past the fixed slot count");
      return false;
    }
  }
  return true;
}

template <class Map>
void lookup(benchmark::State& state, Dist d, double load, bool hit) {
  std::size_t n = bench::entries_at<Map>(load);
  auto keys = bench::make_keys(d, n, bench::kSeed);
  Map m;
  if (!fill(state, m, keys)) return;
  auto probes = hit ? keys : bench::make_miss_keys(d, n, bench::kSeed);
  std::size_t i = 0;
  std::uint64_t found = 0;
  for (auto _ : state) {
    found += m.find(probes[i]) != nullptr;
    if (++i == probes.size()) i = 0;
  }
  benchmark::DoNotOptimize(found);
  if (found != (hit ? std::uint64_t(state.iterations()) : 0)) state.SkipWithError("lookup result mismatch");
  state.counters["load"] = double(m.size()) / double(m.capacity());
}

template <class Map>
void insert(benchmark::State& state, Dist d, std::size_t n) {
  auto keys = bench::make_keys(d, n, bench::kSeed);
  for (auto _ : state) {
    Map m;
    for (std::uint64_t k : keys) m.insert_or_assign(k, k);
    benchmark::DoNotOptimize(m.size());
  }
  state.counters["n"] = double(n);
}

// Steady state at a fixed live size: each iteration erases the oldest key and
// inserts a new one, so the open-addressing maps accumulate and clear
// tombstones the way a cache or session table does.
template <class Map>
void churn(benchmark::State& state, Dist d, double load) {
  std::size_t n = bench::entries_at<Map>(load);
  auto all = bench::make_keys(d, 2 * n, bench::kSeed);
  std::vector<std::uint64_t> live(all.begin(), all.begin() + std::ptrdiff_t(n));
  Map m;
  if (!fill(state, m, live)) return;
  std::deque<std::uint64_t> fifo(live.begin(), live.end());
  std::size_t next = n;
  for (auto _ : state) {
    m.erase(fifo.front());
    fifo.pop_front();
    std::uint64_t k = all[next];
    if (++next == all.size()) next = 0;
    m.insert_or_assign(k, k);
    fifo.push_back(k);
  }
  state.counters["final_capacity"] = double(m.capacity());
}

template <class Map>
void register_table(const std::string& name) {
  for (Dist d : {Dist::kUniform, Dist::kSequential}) {
    std::string tail = name + "/" + bench::dist_name(d) + "/";
    for (double load : kLoads) {
      if (!bench::load_allowed<Map>(load)) continue;
      char l[16];
      std::snprintf(l, sizeof l, "%.2f", load);
      benchmark::RegisterBenchmark("lookup_hit/" + tail + l,
                                   [=](benchmark::State& s) { lookup<Map>(s, d, load, true); });
      benchmark::RegisterBenchmark("lookup_miss/" + tail + l,
                                   [=](benchmark::State& s) { lookup<Map>(s, d, load, false); });
      benchmark::RegisterBenchmark("churn/" + tail + l,
                                   [=](benchmark::State& s) { churn<Map>(s, d, load); });
    }
    for (int lg : kInsertLog2) {
      std::size_t n = std::size_t{1} << lg;
      benchmark::RegisterBenchmark("insert/" + tail + std::to_string(n),
                                   [=](benchmark::State& s) { insert<Map>(s, d, n); })
          ->Unit(benchmark::kMillisecond);
    }
  }
}

}  // namespace

int main(int argc, char** argv) {
  benchmark::Initialize(&argc, argv);
  benchmark::AddCustomContext("compiler", __VERSION__);
  benchmark::AddCustomContext("cxx_flags", HB_CXX_FLAGS);
  benchmark::AddCustomContext("slots", std::to_string(bench::kSlots));
  benchmark::AddCustomContext("seed", std::to_string(bench::kSeed));
  register_table<hashbench::LinearMap>("LinearMap");
  register_table<hashbench::RobinHoodMap>("RobinHoodMap");
  register_table<hashbench::SwissMap>("SwissMap");
  register_table<bench::StdMap>("std::unordered_map");
  benchmark::RunSpecifiedBenchmarks();
  benchmark::Shutdown();
}
