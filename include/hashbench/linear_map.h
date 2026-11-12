#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <vector>

#include "hashbench/hash.h"

namespace hashbench {

// Open addressing with linear probing. Erase leaves a tombstone so that probe
// chains through the slot stay intact. Tombstones count against the load
// limit; when the limit is hit and at most half of it is live entries, the
// table rehashes at the same capacity to clear tombstones instead of growing.
class LinearMap {
 public:
  using key_type = std::uint64_t;
  using mapped_type = std::uint64_t;
  static constexpr std::size_t kMaxLoadNum = 7;
  static constexpr std::size_t kMaxLoadDen = 10;

  LinearMap() { allocate(detail::kMinCapacity); }

  std::size_t size() const noexcept { return size_; }
  std::size_t capacity() const noexcept { return state_.size(); }
  std::size_t tombstones() const noexcept { return tombstones_; }
  double load_factor() const noexcept { return double(size_) / double(capacity()); }

  const mapped_type* find(key_type key) const noexcept {
    std::size_t i = home(key);
    while (true) {
      if (state_[i] == kEmpty) return nullptr;
      if (state_[i] == kFull && keys_[i] == key) return &vals_[i];
      i = (i + 1) & mask_;
    }
  }
  bool contains(key_type key) const noexcept { return find(key) != nullptr; }

  // Returns true if the key was newly inserted, false if it was overwritten.
  bool insert_or_assign(key_type key, mapped_type value) {
    std::size_t i = home(key);
    std::size_t first_tomb = kNone;
    while (state_[i] != kEmpty) {
      if (state_[i] == kFull && keys_[i] == key) {
        vals_[i] = value;
        return false;
      }
      if (state_[i] == kTomb && first_tomb == kNone) first_tomb = i;
      i = (i + 1) & mask_;
    }
    if (first_tomb != kNone) {
      --tombstones_;
      place(first_tomb, key, value);
      return true;
    }
    if (size_ + tombstones_ + 1 > limit_) {
      rehash_for_insert();
      return insert_or_assign(key, value);
    }
    place(i, key, value);
    return true;
  }

  bool erase(key_type key) noexcept {
    std::size_t i = home(key);
    while (state_[i] != kEmpty) {
      if (state_[i] == kFull && keys_[i] == key) {
        state_[i] = kTomb;
        --size_;
        ++tombstones_;
        return true;
      }
      i = (i + 1) & mask_;
    }
    return false;
  }

  void clear() noexcept {
    std::fill(state_.begin(), state_.end(), kEmpty);
    size_ = 0;
    tombstones_ = 0;
  }

  void reserve(std::size_t n) {
    std::size_t cap = detail::capacity_for(n, kMaxLoadNum, kMaxLoadDen);
    if (cap > capacity()) rehash(cap);
  }

  // Rebuilds with capacity >= max(n, what size() needs), dropping tombstones.
  void rehash(std::size_t n) {
    std::size_t cap = detail::round_capacity(n);
    std::size_t need = detail::capacity_for(size_, kMaxLoadNum, kMaxLoadDen);
    if (cap < need) cap = need;
    std::vector<key_type> old_keys = std::move(keys_);
    std::vector<mapped_type> old_vals = std::move(vals_);
    std::vector<std::uint8_t> old_state = std::move(state_);
    allocate(cap);
    for (std::size_t j = 0; j < old_state.size(); ++j) {
      if (old_state[j] != kFull) continue;
      std::size_t i = home(old_keys[j]);
      while (state_[i] != kEmpty) i = (i + 1) & mask_;
      place(i, old_keys[j], old_vals[j]);
    }
  }

  template <class F>
  void for_each(F&& f) const {
    for (std::size_t i = 0; i < state_.size(); ++i)
      if (state_[i] == kFull) f(keys_[i], vals_[i]);
  }

 private:
  static constexpr std::uint8_t kEmpty = 0;
  static constexpr std::uint8_t kFull = 1;
  static constexpr std::uint8_t kTomb = 2;
  static constexpr std::size_t kNone = ~std::size_t{0};

  std::size_t home(key_type key) const noexcept { return mix64(key) & mask_; }

  void place(std::size_t i, key_type key, mapped_type value) noexcept {
    state_[i] = kFull;
    keys_[i] = key;
    vals_[i] = value;
    ++size_;
  }

  void allocate(std::size_t cap) {
    keys_.assign(cap, 0);
    vals_.assign(cap, 0);
    state_.assign(cap, kEmpty);
    mask_ = cap - 1;
    limit_ = cap * kMaxLoadNum / kMaxLoadDen;
    size_ = 0;
    tombstones_ = 0;
  }

  void rehash_for_insert() {
    bool mostly_tombstones = 2 * (size_ + 1) <= limit_;
    rehash(mostly_tombstones ? capacity() : capacity() * 2);
  }

  std::vector<key_type> keys_;
  std::vector<mapped_type> vals_;
  std::vector<std::uint8_t> state_;
  std::size_t mask_ = 0;
  std::size_t limit_ = 0;
  std::size_t size_ = 0;
  std::size_t tombstones_ = 0;
};

}  // namespace hashbench
