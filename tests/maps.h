#pragma once

#include <gtest/gtest.h>

#include "hashbench/linear_map.h"
#include "hashbench/robin_hood_map.h"
#include "hashbench/swiss_map.h"

using AllMaps =
    ::testing::Types<hashbench::LinearMap, hashbench::RobinHoodMap, hashbench::SwissMap>;

class MapNames {
 public:
  template <class T>
  static std::string GetName(int) {
    if (std::is_same_v<T, hashbench::LinearMap>) return "Linear";
    if (std::is_same_v<T, hashbench::RobinHoodMap>) return "RobinHood";
    return "Swiss";
  }
};
