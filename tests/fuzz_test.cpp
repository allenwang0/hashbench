// Differential test: a seeded random program runs against each map and
// std::unordered_map, and every result must agree.
#include <cstdint>
#include <random>
#include <unordered_map>
#include <vector>

#include "maps.h"

namespace {

constexpr std::uint64_t kSeeds = 20;
constexpr int kOpsPerSeed = 200'000;

enum class KeyShape { kSmallRange, kClustered, kWide };

std::uint64_t draw_key(std::mt19937_64& rng, KeyShape shape) {
  switch (shape) {
    case KeyShape::kSmallRange:  // many repeats, so overwrite and erase hit often
      return rng() % 4096;
    case KeyShape::kClustered:  // low 20 bits zero: tests the hash mixer
      return (rng() % 20'000) << 20;
    case KeyShape::kWide:
      return rng();
  }
  return 0;
}

template <class Map>
void run_program(std::uint64_t seed, KeyShape shape) {
  std::mt19937_64 rng(seed);
  Map m;
  std::unordered_map<std::uint64_t, std::uint64_t> ref;
  std::vector<std::uint64_t> seen_keys;
  for (int step = 0; step < kOpsPerSeed; ++step) {
    std::uint64_t key = (!seen_keys.empty() && rng() % 2)
                            ? seen_keys[rng() % seen_keys.size()]
                            : draw_key(rng, shape);
    unsigned op = static_cast<unsigned>(rng() % 100);
    if (op < 45) {
      std::uint64_t v = rng();
      bool fresh = m.insert_or_assign(key, v);
      ASSERT_EQ(fresh, ref.find(key) == ref.end()) << "seed " << seed << " step " << step;
      ref[key] = v;
      seen_keys.push_back(key);
    } else if (op < 75) {
      ASSERT_EQ(m.erase(key), ref.erase(key) == 1) << "seed " << seed << " step " << step;
    } else if (op < 99) {
      auto it = ref.find(key);
      const std::uint64_t* got = m.find(key);
      ASSERT_EQ(got != nullptr, it != ref.end()) << "seed " << seed << " step " << step;
      if (got) ASSERT_EQ(*got, it->second);
    } else if (rng() % 50 == 0) {
      m.clear();
      ref.clear();
      seen_keys.clear();
    }
    ASSERT_EQ(m.size(), ref.size());
  }
  std::size_t visited = 0;
  m.for_each([&](std::uint64_t k, std::uint64_t v) {
    ++visited;
    auto it = ref.find(k);
    ASSERT_NE(it, ref.end());
    EXPECT_EQ(it->second, v);
  });
  EXPECT_EQ(visited, ref.size());
}

}  // namespace

template <class Map>
class FuzzTest : public ::testing::Test {};
TYPED_TEST_SUITE(FuzzTest, AllMaps, MapNames);

TYPED_TEST(FuzzTest, MatchesUnorderedMapOnSmallKeyRange) {
  for (std::uint64_t s = 0; s < kSeeds; ++s) run_program<TypeParam>(1000 + s, KeyShape::kSmallRange);
}

TYPED_TEST(FuzzTest, MatchesUnorderedMapOnClusteredKeys) {
  for (std::uint64_t s = 0; s < kSeeds; ++s) run_program<TypeParam>(2000 + s, KeyShape::kClustered);
}

TYPED_TEST(FuzzTest, MatchesUnorderedMapOnWideKeys) {
  for (std::uint64_t s = 0; s < kSeeds; ++s) run_program<TypeParam>(3000 + s, KeyShape::kWide);
}
