#include <cstdint>
#include <random>
#include <vector>

#include <gtest/gtest.h>

#include "hashbench/robin_hood_map.h"

using hashbench::RobinHoodMap;

// validate() checks that every entry sits dist slots from home and that
// distances never jump by more than one along a cluster. Checking after every
// operation catches a bad swap or a backward shift that moves the wrong range.
TEST(RobinHood, InvariantsHoldAfterEveryOperation) {
  std::mt19937_64 rng(7);
  RobinHoodMap m;
  std::vector<std::uint64_t> keys;
  for (int step = 0; step < 30'000; ++step) {
    if (keys.empty() || rng() % 3 != 0) {
      std::uint64_t k = rng() % 5000;
      m.insert_or_assign(k, k);
      keys.push_back(k);
    } else {
      m.erase(keys[rng() % keys.size()]);
    }
    ASSERT_TRUE(m.validate()) << "step " << step;
  }
}

// Backward shift must leave no gap: after erasing every key, the table is
// empty in every slot, which validate() confirms via the count check.
TEST(RobinHood, EraseAllLeavesEmptyTable) {
  RobinHoodMap m;
  for (std::uint64_t k = 0; k < 10'000; ++k) m.insert_or_assign(k, k);
  for (std::uint64_t k = 0; k < 10'000; ++k) ASSERT_TRUE(m.erase(k));
  EXPECT_EQ(m.size(), 0u);
  EXPECT_TRUE(m.validate());
  EXPECT_EQ(m.max_probe_distance(), 0u);
}

// At the 7/8 load limit the longest probe stays short with a mixed hash.
// A broken mixer or a missing swap pushes this far past the bound.
TEST(RobinHood, ProbeDistanceStaysSmallAtMaxLoad) {
  RobinHoodMap m;
  m.rehash(1 << 16);
  std::size_t n = (std::size_t{1} << 16) * 7 / 8;
  for (std::uint64_t k = 0; k < n; ++k) m.insert_or_assign(k, k);
  ASSERT_EQ(m.capacity(), std::size_t{1} << 16);
  EXPECT_LT(m.max_probe_distance(), 64u);
  EXPECT_TRUE(m.validate());
}
