# Performance Baselines

ArchMarshal keeps scale claims reproducible with
`scripts/benchmark_scale.py`. The script builds bounded fixtures only in a new
system temporary directory, times public read/preview APIs, hashes the complete
fixture tree before and after the run, and fails if an operation changed it.

## Product-scale command

```text
python scripts/benchmark_scale.py \
  --files 10000 --skills 100 --projects 50 \
  --iterations 3 --warmups 1 --pretty
```

The three scenarios are:

- inventory of a code root containing 10,000 regular files;
- adoption preview and complete-package validation for 100 project Skills;
- read-only catalog aggregation for 50 project control planes.

Fixture creation and integrity hashing are excluded from scenario timings.
Results are comparable only on similar Python, operating-system, storage, and
machine configurations. The JSON format is
`archmarshal-scale-benchmark-v1`; fixture sizes and iteration counts are
bounded so an accidental invocation cannot create an unbounded workload.

## 0.12 Windows reference

Reference run on 2026-07-15 with ArchMarshal 0.12.0, Python 3.11.4,
Windows 10 `10.0.19045`, local storage, three measured iterations and one
warmup:

| Scenario | Median | p95/max | Observation |
|---|---:|---:|---|
| Inventory: 10,000 files | 4.544 s | 5.194 s | 10,000 files observed |
| Adoption preview: 100 Skills | 2.900 s | 3.014 s | 100 Skills, 218 operations |
| Catalog: 50 projects | 4.726 s | 4.765 s | 50 projects observed |

The full fixture tree SHA-256 was identical before and after the run. Fixture
construction took 13.794 seconds and is not included above.

These are reference values, not universal CI thresholds. Until heterogeneous
CI runners have enough history, the initial local regression budgets are
advisory: 7 seconds median for each scenario on the reference machine. A result
above that budget should trigger profiling before release, not an automatic
optimization that weakens validation.

## Known scaling work

Inventory, lint, resolver, catalog, and lifecycle commands still perform some
overlapping scans. The next optimization boundary is one immutable,
root-identity-bound `InventorySnapshot` shared within a command. It must retain
the current no-follow checks, complete-package hashes, and stale-source
behavior; caching based only on paths or directory mtimes would be unsafe.

Skill-index and user-store history verification are intentionally linear in the
reachable chain. A future checkpoint/epoch format needs an explicit migration
and verification contract before it can replace full-chain validation.
