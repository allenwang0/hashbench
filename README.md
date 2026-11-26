# hashbench

Three hash maps written from scratch in C++20 (linear probing, Robin Hood hashing and a Swiss-table style design), tested against `std::unordered_map` and benchmarked to show where each design wins. The Swiss-style map finds a random key several times faster than the standard library.

![Top of the report: SwissMap against std::unordered_map lookup time and three key results](docs/report-light.png)

## Highlights

- **4.31 ns lookups** for SwissMap at 70% full on random keys, against 20.5 ns for `std::unordered_map` (median of 5 runs; fastest runs 3.93 vs 18.8 ns).
- **25.4 ns per insert** when building 4,194,304 keys from empty, against 255 ns for `std::unordered_map` (median of 5 runs).
- **SIMD-style probing in plain C++**: SwissMap checks 8 slots at once with bit tricks on one 64-bit integer, no CPU-specific instructions.
- **Fuzzed**: 20 seeds of 200,000 random operations per table, checked step by step against `std::unordered_map`.

## Run it

```sh
scripts/setup.sh                     # local venv with pinned CMake and Ninja
source .venv/bin/activate
cmake --workflow --preset release    # build and run the tests
scripts/run_bench.sh                 # benchmarks + HTML report (about 4 minutes)
```

The full report is at https://allenwang0.github.io/hashbench/ (source: `site/index.html`).

## How it works

- All three maps store entries directly in one array (open addressing) and share one API.
- **LinearMap** checks the next slot on a collision. Simple and fast when the table is sparse, slow once clusters form.
- **RobinHoodMap** lets a new key take the slot of one that sits closer to its home, keeping probe lengths even.
- **SwissMap** keeps one control byte per slot and scans them in groups of 8 before touching any keys.
- `std::unordered_map` wins churn and large inserts on sequential keys, because libc++ hashes integers to themselves while the other maps scatter keys on purpose.

## Limitations

- Results come from one machine (Apple M5 Pro) and one compiler. Lookup ratios against `std::unordered_map` varied from about 3x to 5x between runs.
- Keys and values are fixed at `uint64_t`; no iterators or custom allocators.
