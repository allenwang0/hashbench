#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <random>
#include <string>
#include <unordered_map>
#include <vector>

#include "hashbench/linear_map.h"
#include "hashbench/robin_hood_map.h"
#include "hashbench/swiss_map.h"

namespace bench {

// All measured lookups run in a table with this many slots (16 MiB of keys and
// values for the open-addressing maps), so load factor is the only variable.
inline constexpr std::size_t kSlots = std::size_t{1} << 20;
inline constexpr std::uint64_t kSeed = 20261110;
inline constexpr std::uint64_t kMissBit = std::uint64_t{1} << 63;

// std::unordered_map with the same calls as the open-addressing maps. Its
// default std::hash<uint64_t> is used as-is, since that is what a caller gets.
class StdMap {
 public:
  using Impl = std::unordered_map<std::uint64_t, std::uint64_t>;
  static constexpr std::size_t kMaxLoadNum = 1;
  static constexpr std::size_t kMaxLoadDen = 1;

  const std::uint64_t* find(std::uint64_t k) const {
    auto it = m_.find(k);
    return it == m_.end() ? nullptr : &it->second;
  }
  bool insert_or_assign(std::uint64_t k, std::uint64_t v) {
    return m_.insert_or_assign(k, v).second;
  }
  bool erase(std::uint64_t k) { return m_.erase(k) == 1; }
  void rehash(std::size_t n) { m_.rehash(n); }
  std::size_t size() const { return m_.size(); }
  std::size_t capacity() const { return m_.bucket_count(); }

 private:
  Impl m_;
};

enum class Dist { kUniform, kSequential };

inline const char* dist_name(Dist d) { return d == Dist::kUniform ? "uniform" : "sequential"; }

// Keys that will be stored: distinct, bit 63 clear. Uniform keys are random
// 63-bit values; sequential keys are 1..n.
inline std::vector<std::uint64_t> make_keys(Dist d, std::size_t n, std::uint64_t seed) {
  std::vector<std::uint64_t> keys(n);
  if (d == Dist::kSequential) {
    for (std::size_t i = 0; i < n; ++i) keys[i] = i + 1;
    return keys;
  }
  std::mt19937_64 rng(seed);
  for (auto& k : keys) k = rng() & ~kMissBit;
  std::sort(keys.begin(), keys.end());
  keys.erase(std::unique(keys.begin(), keys.end()), keys.end());
  while (keys.size() < n) keys.push_back(rng() & ~kMissBit);  // collisions are ~1e-7 likely
  std::shuffle(keys.begin(), keys.end(), rng);
  return keys;
}

// Keys guaranteed absent: bit 63 set (uniform) or past the stored range.
inline std::vector<std::uint64_t> make_miss_keys(Dist d, std::size_t n, std::uint64_t seed) {
  std::vector<std::uint64_t> keys(n);
  std::mt19937_64 rng(seed ^ 0x5bd1e995);
  for (std::size_t i = 0; i < n; ++i)
    keys[i] = d == Dist::kUniform ? (rng() | kMissBit) : (kMissBit + i);
  std::shuffle(keys.begin(), keys.end(), rng);
  return keys;
}

template <class Map>
bool load_allowed(double load) {
  return load * double(Map::kMaxLoadDen) <= double(Map::kMaxLoadNum);
}

template <class Map>
std::size_t entries_at(double load) {
  return static_cast<std::size_t>(load * double(kSlots));
}

}  // namespace bench
