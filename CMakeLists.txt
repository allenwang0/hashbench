cmake_minimum_required(VERSION 3.25)
project(hashbench LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

option(HB_WERROR "Treat compiler warnings as errors" OFF)
option(HB_SANITIZE "Build with AddressSanitizer and UndefinedBehaviorSanitizer" OFF)
option(HB_BENCHMARKS "Build the benchmark binaries" ON)

add_library(hb_warnings INTERFACE)
target_compile_options(hb_warnings INTERFACE
  -Wall -Wextra -Wpedantic -Wconversion -Wshadow $<$<BOOL:${HB_WERROR}>:-Werror>)

if(HB_SANITIZE)
  add_compile_options(-fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=all)
  add_link_options(-fsanitize=address,undefined)
endif()

add_library(hashbench INTERFACE)
target_include_directories(hashbench INTERFACE include)

include(FetchContent)
set(INSTALL_GTEST OFF CACHE BOOL "" FORCE)
FetchContent_Declare(googletest
  GIT_REPOSITORY https://github.com/google/googletest.git
  GIT_TAG v1.15.2
  GIT_SHALLOW TRUE
  SYSTEM)
FetchContent_MakeAvailable(googletest)

enable_testing()
include(GoogleTest)
add_executable(hb_tests tests/map_test.cpp tests/fuzz_test.cpp tests/robin_hood_test.cpp
  tests/tombstone_test.cpp)
target_link_libraries(hb_tests PRIVATE hashbench hb_warnings GTest::gtest_main)
gtest_discover_tests(hb_tests DISCOVERY_TIMEOUT 60)

if(HB_BENCHMARKS)
  set(BENCHMARK_ENABLE_TESTING OFF CACHE BOOL "" FORCE)
  set(BENCHMARK_ENABLE_GTEST_TESTS OFF CACHE BOOL "" FORCE)
  FetchContent_Declare(benchmark
    GIT_REPOSITORY https://github.com/google/benchmark.git
    GIT_TAG v1.9.1
    GIT_SHALLOW TRUE
  SYSTEM)
  FetchContent_MakeAvailable(benchmark)

  add_executable(hb_bench bench/throughput.cpp)
  target_link_libraries(hb_bench PRIVATE hashbench hb_warnings benchmark::benchmark)
  add_executable(hb_latency bench/latency.cpp)
  target_link_libraries(hb_latency PRIVATE hashbench hb_warnings)
endif()
