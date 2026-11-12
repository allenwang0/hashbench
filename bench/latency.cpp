// Per-lookup latency percentiles. One lookup takes a few nanoseconds, close to
// the steady_clock tick on many machines, so the tool times batches of
// kBatch lookups and reports batch time / kBatch. Percentiles therefore
// describe 64-lookup averages, which hides some single-lookup outliers.
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <string>
#include <vector>

#include "common.h"

namespace {

using Clock = std::chrono::steady_clock;
constexpr std::size_t kBatch = 64;
constexpr std::size_t kBatches = 50'000;
constexpr int kPasses = 5;
constexpr double kLoads[] = {0.5, 0.7, 0.85};

double percentile(std::vector<double>& v, double p) {
  std::size_t idx = static_cast<std::size_t>(std::ceil(p * double(v.size()))) - 1;
  std::nth_element(v.begin(), v.begin() + std::ptrdiff_t(idx), v.end());
  return v[idx];
}

double clock_tick_ns() {
  double best = 1e9;
  for (int i = 0; i < 1000; ++i) {
    auto a = Clock::now();
    auto b = Clock::now();
    while (b == a) b = Clock::now();
    best = std::min(best, std::chrono::duration<double, std::nano>(b - a).count());
  }
  return best;
}

bool first_row = true;

template <class Map>
void measure(const char* name, double load, bool hit) {
  if (!bench::load_allowed<Map>(load)) return;
  std::size_t n = bench::entries_at<Map>(load);
  auto keys = bench::make_keys(bench::Dist::kUniform, n, bench::kSeed);
  Map m;
  m.rehash(bench::kSlots);
  for (std::uint64_t k : keys) m.insert_or_assign(k, k);
  auto probes = hit ? keys : bench::make_miss_keys(bench::Dist::kUniform, n, bench::kSeed);
  std::shuffle(probes.begin(), probes.end(), std::mt19937_64(bench::kSeed + 1));

  // Several passes; keep the one with the lowest median, the pass least
  // disturbed by other processes on a shared machine.
  std::vector<double> best;
  double best_p50 = 1e300;
  std::size_t i = 0;
  std::uint64_t found = 0;
  for (int pass = 0; pass < kPasses; ++pass) {
    std::vector<double> per_op(kBatches);
    for (std::size_t b = 0; b < kBatches; ++b) {
      auto t0 = Clock::now();
      for (std::size_t j = 0; j < kBatch; ++j) {
        found += m.find(probes[i]) != nullptr;
        if (++i == probes.size()) i = 0;
      }
      auto t1 = Clock::now();
      per_op[b] = std::chrono::duration<double, std::nano>(t1 - t0).count() / double(kBatch);
    }
    std::vector<double> copy = per_op;
    if (double p = percentile(copy, 0.50); p < best_p50) {
      best_p50 = p;
      best = std::move(per_op);
    }
  }
  if (found != (hit ? kBatch * kBatches * kPasses : 0)) {
    std::fprintf(stderr, "lookup result mismatch for %s\n", name);
    std::exit(1);
  }
  std::vector<double>& per_op = best;
  double p50 = percentile(per_op, 0.50);
  double p90 = percentile(per_op, 0.90);
  double p99 = percentile(per_op, 0.99);
  std::printf(
      "%s    {\"table\": \"%s\", \"load\": %.2f, \"lookup\": \"%s\", "
      "\"p50_ns\": %.3f, \"p90_ns\": %.3f, \"p99_ns\": %.3f}",
      first_row ? "" : ",\n", name, load, hit ? "hit" : "miss", p50, p90, p99);
  first_row = false;
}

template <class Map>
void measure_all(const char* name) {
  for (double load : kLoads)
    for (bool hit : {true, false}) measure<Map>(name, load, hit);
}

}  // namespace

int main() {
  std::printf(
      "{\n  \"context\": {\"compiler\": \"%s\", \"cxx_flags\": \"%s\", \"slots\": %zu, "
      "\"seed\": %llu, \"batch\": %zu, \"batches\": %zu, \"passes\": %d, \"clock_tick_ns\": "
      "%.2f},\n"
      "  \"rows\": [\n",
      __VERSION__, HB_CXX_FLAGS, bench::kSlots, static_cast<unsigned long long>(bench::kSeed),
      kBatch, kBatches, kPasses, clock_tick_ns());
  measure_all<hashbench::LinearMap>("LinearMap");
  measure_all<hashbench::RobinHoodMap>("RobinHoodMap");
  measure_all<hashbench::SwissMap>("SwissMap");
  measure_all<bench::StdMap>("std::unordered_map");
  std::printf("\n  ]\n}\n");
}
