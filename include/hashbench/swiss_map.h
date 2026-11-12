#pragma once

#include <algorithm>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <vector>

#include "hashbench/hash.h"

namespace hashbench {

// Swiss-table style map: one control byte per slot, slots probed in aligned
// groups of 8. A control byte is kEmpty, kDeleted, or the low 7 bits of the
// key's hash (h2) when full. A lookup loads a group's 8 control bytes as one
// uint64_t and finds h2 matches with SWAR bit tricks, so it compares keys only
// for slots that very likely match. No SIMD intrinsics are used; the same code
// runs on any 64-bit little-endian CPU. Groups are visited in triangular order
// (g, g+1, g+3, g+6, ...), which covers every group when the group count is a
// power of two.
class SwissMap {
 public:
  using key_type = std::uint64_t;
  using mapped_type = std::uint64_t;
  static constexpr std::size_t kMaxLoadNum = 7;
  static constexpr std::size_t kMaxLoadDen = 8;
  static constexpr std::size_t kGroupWidth = 8;

  static_assert(std::endian::native == std::endian::little,
                "group matching assumes little-endian byte order");

  SwissMap() { allocate(detail::kMinCapacity); }

  std::size_t size() const noexcept { return size_; }
  std::size_t capacity() const noexcept { return ctrl_.size(); }
  std::size_t tombstones() const noexcept { return deleted_; }
  double load_factor() const noexcept { return double(size_) / double(capacity()); }

  const mapped_type* find(key_type key) const noexcept {
    std::size_t i = find_index(mix64(key), key);
    return i == kNone ? nullptr : &vals_[i];
  }
  bool contains(key_type key) const noexcept { return find(key) != nullptr; }

  bool insert_or_assign(key_type key, mapped_type value) {
    std::uint64_t h = mix64(key);
    if (std::size_t i = find_index(h, key); i != kNone) {
      vals_[i] = value;
      return false;
    }
    std::size_t i = first_free(h);
    if (ctrl_[i] == kEmpty && size_ + deleted_ + 1 > limit_) {
      bool mostly_tombstones = 2 * (size_ + 1) <= limit_;
      rehash(mostly_tombstones ? capacity() : capacity() * 2);
      i = first_free(h);
    }
    if (ctrl_[i] == kDeleted) --deleted_;
    place(i, h, key, value);
    return true;
  }

  // A slot may go back to kEmpty only if its group still has another empty
  // slot. Then no probe ever passed through this group, because a probe stops
  // at the first group that has an empty slot, so no lookup can be cut short.
  // Otherwise the slot becomes kDeleted.
  bool erase(key_type key) noexcept {
    std::size_t i = find_index(mix64(key), key);
    if (i == kNone) return false;
    std::size_t g = i / kGroupWidth;
    bool group_has_empty = match_empty(load_group(g)) != 0;
    if (group_has_empty) {
      ctrl_[i] = kEmpty;
    } else {
      ctrl_[i] = kDeleted;
      ++deleted_;
    }
    --size_;
    return true;
  }

  void clear() noexcept {
    std::fill(ctrl_.begin(), ctrl_.end(), kEmpty);
    size_ = 0;
    deleted_ = 0;
  }

  void reserve(std::size_t n) {
    std::size_t cap = detail::capacity_for(n, kMaxLoadNum, kMaxLoadDen);
    if (cap > capacity()) rehash(cap);
  }

  void rehash(std::size_t n) {
    std::size_t cap = detail::round_capacity(n);
    std::size_t need = detail::capacity_for(size_, kMaxLoadNum, kMaxLoadDen);
    if (cap < need) cap = need;
    std::vector<key_type> old_keys = std::move(keys_);
    std::vector<mapped_type> old_vals = std::move(vals_);
    std::vector<std::uint8_t> old_ctrl = std::move(ctrl_);
    allocate(cap);
    for (std::size_t j = 0; j < old_ctrl.size(); ++j) {
      if (!is_full(old_ctrl[j])) continue;
      std::uint64_t h = mix64(old_keys[j]);
      place(first_free(h), h, old_keys[j], old_vals[j]);
    }
  }

  template <class F>
  void for_each(F&& f) const {
    for (std::size_t i = 0; i < capacity(); ++i)
      if (is_full(ctrl_[i])) f(keys_[i], vals_[i]);
  }

 private:
  static constexpr std::uint8_t kEmpty = 0x80;    // 0b1000'0000
  static constexpr std::uint8_t kDeleted = 0xFE;  // 0b1111'1110
  static constexpr std::uint64_t kLsbs = 0x0101010101010101ULL;
  static constexpr std::uint64_t kMsbs = 0x8080808080808080ULL;
  static constexpr std::size_t kNone = ~std::size_t{0};

  static bool is_full(std::uint8_t c) noexcept { return (c & 0x80) == 0; }
  static std::uint8_t h2(std::uint64_t h) noexcept { return h & 0x7F; }

  std::uint64_t load_group(std::size_t g) const noexcept {
    std::uint64_t word;
    std::memcpy(&word, ctrl_.data() + g * kGroupWidth, sizeof word);
    return word;
  }

  // High bit set in each byte equal to tag. May also flag a byte just above a
  // true match (a borrow artifact); callers compare keys, so that is harmless.
  static std::uint64_t match_tag(std::uint64_t group, std::uint8_t tag) noexcept {
    std::uint64_t x = group ^ (kLsbs * tag);
    return (x - kLsbs) & ~x & kMsbs;
  }
  // kEmpty is the only control value with bit 7 set and bit 1 clear.
  static std::uint64_t match_empty(std::uint64_t group) noexcept {
    return group & ~(group << 6) & kMsbs;
  }
  static std::uint64_t match_free(std::uint64_t group) noexcept { return group & kMsbs; }
  static std::size_t lowest_byte(std::uint64_t mask) noexcept {
    return static_cast<std::size_t>(std::countr_zero(mask)) / 8;
  }

  std::size_t find_index(std::uint64_t h, key_type key) const noexcept {
    std::size_t g = (h >> 7) & group_mask_;
    for (std::size_t step = 1;; ++step) {
      std::uint64_t group = load_group(g);
      for (std::uint64_t m = match_tag(group, h2(h)); m != 0; m &= m - 1) {
        std::size_t i = g * kGroupWidth + lowest_byte(m);
        if (keys_[i] == key && is_full(ctrl_[i])) return i;
      }
      if (match_empty(group) != 0) return kNone;
      g = (g + step) & group_mask_;
    }
  }

  std::size_t first_free(std::uint64_t h) const noexcept {
    std::size_t g = (h >> 7) & group_mask_;
    for (std::size_t step = 1;; ++step) {
      if (std::uint64_t m = match_free(load_group(g)); m != 0) {
        return g * kGroupWidth + lowest_byte(m);
      }
      g = (g + step) & group_mask_;
    }
  }

  void place(std::size_t i, std::uint64_t h, key_type key, mapped_type value) noexcept {
    ctrl_[i] = h2(h);
    keys_[i] = key;
    vals_[i] = value;
    ++size_;
  }

  void allocate(std::size_t cap) {
    keys_.assign(cap, 0);
    vals_.assign(cap, 0);
    ctrl_.assign(cap, kEmpty);
    group_mask_ = cap / kGroupWidth - 1;
    limit_ = cap * kMaxLoadNum / kMaxLoadDen;
    size_ = 0;
    deleted_ = 0;
  }

  std::vector<key_type> keys_;
  std::vector<mapped_type> vals_;
  std::vector<std::uint8_t> ctrl_;
  std::size_t group_mask_ = 0;
  std::size_t limit_ = 0;
  std::size_t size_ = 0;
  std::size_t deleted_ = 0;
};

}  // namespace hashbench
