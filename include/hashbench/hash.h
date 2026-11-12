#pragma once

#include <bit>
#include <cstddef>
#include <cstdint>

namespace hashbench {

// Finalizer from splitmix64 (Steele, Lea, Flood 2014). Every input bit affects
// every output bit, so sequential keys spread across the table.
constexpr std::uint64_t mix64(std::uint64_t x) noexcept {
  x ^= x >> 30;
  x *= 0xbf58476d1ce4e5b9ULL;
  x ^= x >> 27;
  x *= 0x94d049bb133111ebULL;
  x ^= x >> 31;
  return x;
}

namespace detail {

inline constexpr std::size_t kMinCapacity = 16;

// Smallest power of two >= max(n, kMinCapacity).
constexpr std::size_t round_capacity(std::size_t n) noexcept {
  return std::bit_ceil(n < kMinCapacity ? kMinCapacity : n);
}

// Smallest power-of-two capacity whose load limit num/den admits n entries.
constexpr std::size_t capacity_for(std::size_t n, std::size_t num, std::size_t den) noexcept {
  std::size_t cap = round_capacity(n);
  while (cap * num / den < n) cap *= 2;
  return cap;
}

}  // namespace detail
}  // namespace hashbench
