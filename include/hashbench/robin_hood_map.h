#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <utility>
#include <vector>

#include "hashbench/hash.h"

namespace hashbench {

// Robin Hood hashing (Celis 1986): an insert that meets an entry closer to its
// home slot than the insert is to its own takes that slot and carries the
// displaced entry forward. Erase shifts the rest of the cluster back by one
// slot, so the table never holds tombstones.
//
// dist_[i] == 0 marks an empty slot; otherwise dist_[i] - 1 is the entry's
// distance from its home slot.
class RobinHoodMap {
 public:
  using key_type = std::uint64_t;
  using mapped_type = std::uint64_t;
  static constexpr std::size_t kMaxLoadNum = 7;
  static constexpr std::size_t kMaxLoadDen = 8;

  RobinHoodMap() { allocate(detail::kMinCapacity); }

  std::size_t size() const noexcept { return size_; }
  std::size_t capacity() const noexcept { return dist_.size(); }
  double load_factor() const noexcept { return double(size_) / double(capacity()); }

  const mapped_type* find(key_type key) const noexcept {
    std::size_t i = home(key);
    for (unsigned d = 1; dist_[i] >= d; ++d) {
      if (dist_[i] == d && keys_[i] == key) return &vals_[i];
      i = (i + 1) & mask_;
    }
    return nullptr;
  }
  bool contains(key_type key) const noexcept { return find(key) != nullptr; }

  bool insert_or_assign(key_type key, mapped_type value) {
    if (mapped_type* v = const_cast<mapped_type*>(find(key))) {
      *v = value;
      return false;
    }
    if (size_ + 1 > limit_) rehash(capacity() * 2);
    while (!place(key, value)) rehash(capacity() * 2);
    return true;
  }

  bool erase(key_type key) noexcept {
    std::size_t i = home(key);
    for (unsigned d = 1; dist_[i] >= d; ++d) {
      if (dist_[i] == d && keys_[i] == key) {
        std::size_t next = (i + 1) & mask_;
        while (dist_[next] > 1) {
          keys_[i] = keys_[next];
          vals_[i] = vals_[next];
          dist_[i] = static_cast<std::uint8_t>(dist_[next] - 1);
          i = next;
          next = (next + 1) & mask_;
        }
        dist_[i] = 0;
        --size_;
        return true;
      }
      i = (i + 1) & mask_;
    }
    return false;
  }

  void clear() noexcept {
    std::fill(dist_.begin(), dist_.end(), std::uint8_t{0});
    size_ = 0;
  }

  void reserve(std::size_t n) {
    std::size_t cap = detail::capacity_for(n, kMaxLoadNum, kMaxLoadDen);
    if (cap > capacity()) rehash(cap);
  }

  void rehash(std::size_t n) {
    std::size_t cap = detail::round_capacity(n);
    std::size_t need = detail::capacity_for(size_, kMaxLoadNum, kMaxLoadDen);
    if (cap < need) cap = need;
    while (true) {
      RobinHoodMap fresh(cap);
      bool ok = true;
      for (std::size_t j = 0; ok && j < capacity(); ++j)
        if (dist_[j] != 0) ok = fresh.place(keys_[j], vals_[j]);
      if (ok) {
        *this = std::move(fresh);
        return;
      }
      cap *= 2;
    }
  }

  // Checks the Robin Hood invariants. Used by tests; O(capacity).
  // 1. Each entry sits exactly dist - 1 slots after its home slot.
  // 2. Along a cluster, distance grows by at most one per slot, so no entry
  //    could be moved closer to home by a swap.
  bool validate() const noexcept {
    std::size_t count = 0;
    for (std::size_t i = 0; i < capacity(); ++i) {
      if (dist_[i] == 0) continue;
      ++count;
      if (((i - home(keys_[i])) & mask_) != std::size_t(dist_[i] - 1)) return false;
      std::size_t next = (i + 1) & mask_;
      if (dist_[next] > dist_[i] + 1) return false;
    }
    for (std::size_t i = 0; i < capacity(); ++i) {
      std::size_t prev = (i - 1) & mask_;
      if (dist_[i] > 1 && dist_[prev] == 0) return false;
    }
    return count == size_;
  }

  std::size_t max_probe_distance() const noexcept {
    std::uint8_t m = 0;
    for (std::uint8_t d : dist_) m = std::max(m, d);
    return m == 0 ? 0 : m - 1;
  }

  template <class F>
  void for_each(F&& f) const {
    for (std::size_t i = 0; i < capacity(); ++i)
      if (dist_[i] != 0) f(keys_[i], vals_[i]);
  }

 private:
  static constexpr std::uint8_t kMaxDist = 255;

  std::size_t home(key_type key) const noexcept { return mix64(key) & mask_; }

  explicit RobinHoodMap(std::size_t cap) { allocate(cap); }

  // Inserts a key known to be absent. Returns false without changing the
  // table if some entry would need a distance that does not fit in a byte;
  // the caller then grows the table.
  bool place(key_type key, mapped_type value) {
    std::uint8_t carried = 1;
    for (std::size_t j = home(key); dist_[j] != 0; j = (j + 1) & mask_) {
      carried = std::min(carried, dist_[j]);
      if (carried == kMaxDist) return false;
      ++carried;
    }
    std::size_t i = home(key);
    std::uint8_t d = 1;
    while (dist_[i] != 0) {
      if (dist_[i] < d) {
        std::swap(keys_[i], key);
        std::swap(vals_[i], value);
        std::swap(dist_[i], d);
      }
      ++d;
      i = (i + 1) & mask_;
    }
    keys_[i] = key;
    vals_[i] = value;
    dist_[i] = d;
    ++size_;
    return true;
  }

  void allocate(std::size_t cap) {
    keys_.assign(cap, 0);
    vals_.assign(cap, 0);
    dist_.assign(cap, 0);
    mask_ = cap - 1;
    limit_ = cap * kMaxLoadNum / kMaxLoadDen;
    size_ = 0;
  }

  std::vector<key_type> keys_;
  std::vector<mapped_type> vals_;
  std::vector<std::uint8_t> dist_;
  std::size_t mask_ = 0;
  std::size_t limit_ = 0;
  std::size_t size_ = 0;
};

}  // namespace hashbench
