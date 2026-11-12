#include <cstdint>
#include <set>

#include "maps.h"

template <class Map>
class MapTest : public ::testing::Test {};
TYPED_TEST_SUITE(MapTest, AllMaps, MapNames);

TYPED_TEST(MapTest, EmptyMapFindsNothing) {
  TypeParam m;
  EXPECT_EQ(m.size(), 0u);
  EXPECT_EQ(m.find(0), nullptr);
  EXPECT_FALSE(m.erase(0));
}

// Key 0 is also the value stored in unused slots; it must still be a real key.
TYPED_TEST(MapTest, KeyZeroIsAnOrdinaryKey) {
  TypeParam m;
  EXPECT_TRUE(m.insert_or_assign(0, 7));
  ASSERT_NE(m.find(0), nullptr);
  EXPECT_EQ(*m.find(0), 7u);
  EXPECT_TRUE(m.erase(0));
  EXPECT_FALSE(m.contains(0));
}

TYPED_TEST(MapTest, InsertOrAssignOverwritesWithoutGrowingSize) {
  TypeParam m;
  EXPECT_TRUE(m.insert_or_assign(42, 1));
  EXPECT_FALSE(m.insert_or_assign(42, 2));
  EXPECT_EQ(m.size(), 1u);
  EXPECT_EQ(*m.find(42), 2u);
}

TYPED_TEST(MapTest, EraseRemovesOnlyThatKey) {
  TypeParam m;
  for (std::uint64_t k = 0; k < 100; ++k) m.insert_or_assign(k, k * 3);
  EXPECT_TRUE(m.erase(50));
  EXPECT_FALSE(m.erase(50));
  EXPECT_EQ(m.size(), 99u);
  for (std::uint64_t k = 0; k < 100; ++k) {
    if (k == 50) {
      EXPECT_FALSE(m.contains(k));
    } else {
      ASSERT_NE(m.find(k), nullptr) << k;
      EXPECT_EQ(*m.find(k), k * 3);
    }
  }
}

// Growth must keep the load limit and must not lose a key while moving entries.
TYPED_TEST(MapTest, GrowthKeepsLoadLimitAndEveryKey) {
  TypeParam m;
  std::size_t last_cap = m.capacity();
  int growths = 0;
  for (std::uint64_t k = 1; k <= 200'000; ++k) {
    m.insert_or_assign(k << 20, k);
    ASSERT_LE(m.size() * TypeParam::kMaxLoadDen, m.capacity() * TypeParam::kMaxLoadNum);
    if (m.capacity() != last_cap) {
      EXPECT_EQ(m.capacity(), last_cap * 2) << "capacity doubles";
      last_cap = m.capacity();
      ++growths;
    }
  }
  EXPECT_GE(growths, 10);
  for (std::uint64_t k = 1; k <= 200'000; ++k) ASSERT_EQ(*m.find(k << 20), k);
}

TYPED_TEST(MapTest, ReserveAvoidsLaterGrowth) {
  TypeParam m;
  m.reserve(10'000);
  std::size_t cap = m.capacity();
  for (std::uint64_t k = 0; k < 10'000; ++k) m.insert_or_assign(k, k);
  EXPECT_EQ(m.capacity(), cap);
}

TYPED_TEST(MapTest, RehashNeverShrinksBelowWhatSizeNeeds) {
  TypeParam m;
  for (std::uint64_t k = 0; k < 1000; ++k) m.insert_or_assign(k, k);
  m.rehash(16);
  EXPECT_LE(m.size() * TypeParam::kMaxLoadDen, m.capacity() * TypeParam::kMaxLoadNum);
  for (std::uint64_t k = 0; k < 1000; ++k) ASSERT_TRUE(m.contains(k));
}

TYPED_TEST(MapTest, ClearEmptiesButKeepsWorking) {
  TypeParam m;
  for (std::uint64_t k = 0; k < 1000; ++k) m.insert_or_assign(k, k);
  m.clear();
  EXPECT_EQ(m.size(), 0u);
  EXPECT_FALSE(m.contains(5));
  m.insert_or_assign(5, 9);
  EXPECT_EQ(*m.find(5), 9u);
}

TYPED_TEST(MapTest, ForEachVisitsEachEntryOnce) {
  TypeParam m;
  for (std::uint64_t k = 0; k < 500; ++k) m.insert_or_assign(k, k + 1);
  for (std::uint64_t k = 0; k < 500; k += 2) m.erase(k);
  std::set<std::uint64_t> seen;
  m.for_each([&](std::uint64_t k, std::uint64_t v) {
    EXPECT_EQ(v, k + 1);
    EXPECT_TRUE(seen.insert(k).second);
  });
  EXPECT_EQ(seen.size(), 250u);
}
