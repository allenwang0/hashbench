#include <cstdint>

#include <gtest/gtest.h>

#include "hashbench/linear_map.h"
#include "hashbench/swiss_map.h"

using hashbench::LinearMap;
using hashbench::SwissMap;

// Insert/erase churn with a small live set. Without a same-capacity cleanup,
// tombstones would fill every slot and each miss would scan the whole table,
// or the table would keep doubling with only 1,000 live keys.
template <class Map>
void churn_stays_bounded() {
  Map m;
  constexpr std::uint64_t kLive = 1000;
  for (std::uint64_t k = 0; k < kLive; ++k) m.insert_or_assign(k, k);
  std::size_t cap_after_fill = m.capacity();
  for (std::uint64_t k = kLive; k < 1'000'000 + kLive; ++k) {
    ASSERT_TRUE(m.erase(k - kLive));
    m.insert_or_assign(k, k);
    ASSERT_LE((m.size() + m.tombstones()) * Map::kMaxLoadDen, m.capacity() * Map::kMaxLoadNum);
  }
  EXPECT_EQ(m.size(), kLive);
  EXPECT_LE(m.capacity(), 2 * cap_after_fill);
  for (std::uint64_t k = 1'000'000; k < 1'000'000 + kLive; ++k) ASSERT_TRUE(m.contains(k));
  EXPECT_FALSE(m.contains(0));
}

TEST(Tombstones, LinearChurnStaysBounded) { churn_stays_bounded<LinearMap>(); }
TEST(Tombstones, SwissChurnStaysBounded) { churn_stays_bounded<SwissMap>(); }

TEST(Tombstones, LinearEraseLeavesTombstoneThatInsertReuses) {
  LinearMap m;
  m.insert_or_assign(1, 1);
  m.erase(1);
  EXPECT_EQ(m.tombstones(), 1u);
  m.insert_or_assign(1, 2);
  EXPECT_EQ(m.tombstones(), 0u);
  EXPECT_EQ(*m.find(1), 2u);
}

// Lookups must walk past tombstones to reach keys inserted after a collision.
TEST(Tombstones, LinearLookupSkipsTombstones) {
  LinearMap m;
  for (std::uint64_t k = 0; k < 10; ++k) m.insert_or_assign(k, k);
  for (std::uint64_t k = 0; k < 10; k += 2) m.erase(k);
  for (std::uint64_t k = 1; k < 10; k += 2) EXPECT_TRUE(m.contains(k));
}

// In a sparse Swiss table every group keeps empty slots, so erase can write
// kEmpty and never needs a tombstone.
TEST(Tombstones, SwissSparseEraseWritesEmpty) {
  SwissMap m;
  m.reserve(1 << 12);
  for (std::uint64_t k = 0; k < 100; ++k) m.insert_or_assign(k, k);
  for (std::uint64_t k = 0; k < 100; ++k) m.erase(k);
  EXPECT_EQ(m.tombstones(), 0u);
}

// A full group must get kDeleted, or a probe that passed through it would
// stop early and miss a key stored in a later group.
TEST(Tombstones, SwissDenseEraseUsesDeletedAndKeepsLaterKeys) {
  SwissMap m;
  m.rehash(64);
  std::size_t n = 64 * 7 / 8;
  for (std::uint64_t k = 0; k < n; ++k) m.insert_or_assign(k, k);
  ASSERT_EQ(m.capacity(), 64u);
  for (std::uint64_t k = 0; k < n; k += 2) m.erase(k);
  EXPECT_GT(m.tombstones(), 0u);
  for (std::uint64_t k = 1; k < n; k += 2) EXPECT_TRUE(m.contains(k)) << k;
}
