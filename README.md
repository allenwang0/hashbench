# hashbench

Three open-addressing hash maps written from scratch in C++20 (linear probing, Robin Hood hashing with backward-shift deletion, and a Swiss-table style group probe), tested against `std::unordered_map` with a differential fuzzer under AddressSanitizer and UndefinedBehaviorSanitizer, and benchmarked across load factors with Google Benchmark.

![Report: lookup time against load factor for the four tables](docs/report-light.png)

The full report is [site/index.html](site/index.html) (open it locally; it has no JavaScript and follows the system light or dark setting). A dark-mode capture is in [docs/report-dark.png](docs/report-dark.png).

## Results

One run on an Apple M5 Pro, Apple clang 21 (`-O3 -DNDEBUG`, C++20), macOS. Keys and values are `uint64_t`. Lookups and churn run in a table fixed at 1,048,576 slots, so load factor is the only variable. Each number is the fastest of 5 interleaved repetitions; the raw output is in [results/bench.json](results/bench.json) and [results/latency.json](results/latency.json). All keys are synthetic, generated from seed 20261110.

Time per operation in ns, uniform random keys, load 0.70 (the highest load all four tables support):

| Operation | SwissMap | LinearMap | RobinHoodMap | std::unordered_map |
| --- | ---: | ---: | ---: | ---: |
| Successful lookup | 3.9 | 9.6 | 14.2 | 18.8 |
| Unsuccessful lookup | 6.6 | 20.5 | 14.5 | 15.6 |
| Erase + insert (churn) | 29.6 | 43.4 | 73.5 | 216.6 |
| Insert 4,194,304 keys from empty, per key | 20.3 | 26.4 | 57.7 | 253.7 |

What the run shows:

- SwissMap is fastest on every uniform-key lookup. At load 0.85 a hit still takes 5.2 ns, against 20.9 ns for RobinHoodMap and 22.0 ns for `std::unordered_map`.
- LinearMap misses degrade fastest with load: 4.2 ns at 0.25, 20.5 ns at 0.70. A miss scans to the end of the cluster, and clusters grow quickly under linear probing.
- Robin Hood keeps miss latency tight (p50 15.0 ns, p99 17.6 ns at load 0.70) but pays for it on writes. Backward-shift deletion moves the rest of the cluster on every erase, and churn at load 0.85 costs 202.5 ns per erase plus insert.
- With sequential keys, `std::unordered_map` wins churn (13.8 ns) and insert (17.0 ns per key). libc++ hashes integers with the identity function, so consecutive keys land in consecutive buckets; the open-addressing maps mix the key with a splitmix64 finalizer and scatter them on purpose.
- During churn, LinearMap doubles capacity at starting loads of 0.50 and above and SwissMap at 0.70 and above (see `final_capacity` in results/bench.json). Tombstones count against the load limit, and the rule below chooses to grow when live entries exceed half of it.

Per-lookup latency, uniform keys, load 0.70, ns (each sample is a batch of 64 lookups divided by 64, because the clock tick on this machine is 41 ns):

| Table | Hit p50 | Hit p99 | Miss p50 | Miss p99 |
| --- | ---: | ---: | ---: | ---: |
| SwissMap | 4.5 | 6.5 | 6.5 | 10.4 |
| LinearMap | 9.8 | 13.7 | 18.9 | 37.8 |
| RobinHoodMap | 13.0 | 16.9 | 15.0 | 17.6 |
| std::unordered_map | 14.3 | 20.2 | 17.6 | 24.8 |

## Design

All three maps share one API: `insert_or_assign`, `find` (pointer or `nullptr`), `contains`, `erase`, `size`, `capacity`, `load_factor`, `reserve`, `rehash`, `clear` and `for_each`. Capacity is a power of two. Keys pass through the splitmix64 finalizer ([include/hashbench/hash.h](include/hashbench/hash.h)) so keys that differ only in high bits do not collide.

| Table | Probe | Erase | Max load |
| --- | --- | --- | --- |
| [LinearMap](include/hashbench/linear_map.h) | next slot | tombstone | 0.70 |
| [RobinHoodMap](include/hashbench/robin_hood_map.h) | next slot, insert swaps with any entry closer to its home | backward shift, no tombstones | 0.875 |
| [SwissMap](include/hashbench/swiss_map.h) | aligned groups of 8 control bytes, triangular group order | `empty` if the group still has an empty slot, otherwise `deleted` | 0.875 |

- Tombstones (LinearMap, SwissMap) count toward the load limit. When an insert reaches the limit, the table rehashes at the same capacity if live entries are at most half the limit, and doubles otherwise. A churn loop therefore cannot fill the table with tombstones.
- RobinHoodMap stores each entry's probe distance in one byte. If an insert would need a distance that does not fit in that byte, the table doubles instead.
- SwissMap matches a group with SWAR bit operations on one `uint64_t` instead of SSE2 or NEON intrinsics, so the same code runs on any 64-bit little-endian CPU. A `static_assert` rejects big-endian targets.

## Build and test

Requires Python 3, a C++20 compiler and network access (CMake fetches GoogleTest v1.15.2 and Google Benchmark v1.9.1).

```sh
scripts/setup.sh                     # .venv with pinned CMake, Ninja, clang-format, jsonschema, Playwright
source .venv/bin/activate
cmake --workflow --preset release    # configure, build, run 45 tests
cmake --workflow --preset sanitize   # same tests under ASan and UBSan
python3 -m unittest discover -s scripts -p "test_*.py"   # 6 report tests
```

The tests:

- [tests/fuzz_test.cpp](tests/fuzz_test.cpp): 20 seeds x 200,000 random insert, overwrite, erase, find and clear operations per table and key shape (small range, clustered `k << 20`, full 64-bit), compared step by step with `std::unordered_map`, then a full content comparison.
- [tests/robin_hood_test.cpp](tests/robin_hood_test.cpp): checks after every one of 30,000 operations that each entry sits exactly its recorded distance from home and that distance grows by at most one per slot along a cluster.
- [tests/tombstone_test.cpp](tests/tombstone_test.cpp): 1,000,000 erase plus insert cycles on 1,000 live keys keep capacity within 2x of the starting size; a full Swiss group gets `deleted`, not `empty`, so later keys stay reachable.
- [tests/map_test.cpp](tests/map_test.cpp): typed tests for all three maps, including load-limit checks during growth from 16 slots to over 200,000 entries.

## Reproduce the benchmarks and report

```sh
scripts/run_bench.sh
```

This builds the release preset, runs `hb_bench` and `hb_latency`, writes `results/*.json` (host name and local paths removed), and regenerates `site/index.html` and `site/card.json` with [scripts/make_report.py](scripts/make_report.py). It took about 4 minutes on the machine above. `python3 scripts/screenshot.py` redraws the PNGs in `docs/` with headless Chrome.

`site/card.json` follows the schema in [scripts/card.schema.json](scripts/card.schema.json). It records the commit that produced the run, the command, and the p50, p90 and p99 unsuccessful lookup latency per table.

## Limitations

- One machine and one compiler. The CI workflow builds and tests with GCC 14 and Clang 18 on Linux and Apple clang on macOS, but does not run benchmarks.
- The machine was shared with other work during the run. Reporting the fastest of 5 interleaved repetitions removes most of that noise, but single repetitions varied by up to 41% (coefficient of variation in results/bench.json).
- Latency percentiles are averages over batches of 64 lookups, so they understate single-lookup outliers.
- `std::unordered_map` uses its default `std::hash`, which is the identity on libc++. That is what a caller gets by default, but it is a different hash from the one the other three tables use.
- Keys and values are fixed at `uint64_t`; there is no iterator type and no custom allocator.

## License

MIT, see [LICENSE](LICENSE).
