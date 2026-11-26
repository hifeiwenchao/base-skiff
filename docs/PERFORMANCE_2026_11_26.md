# Redis incremental loading — 2026-11-26

## Repeated 500-key native tree batches

Measured on macOS 26.5 arm64, Python 3.14.10 and PySide6 6.12.0, using the
project Conda interpreter and `QT_QPA_PLATFORM=offscreen`. These are single-run
exploratory timings. Background development/test activity was not isolated;
there are no confidence intervals or native interactive latency guarantees.

The baseline uses the actual `append_redis_keys` implementation from commit
`5141b41b80a3521844342023eaab28a124d2e9ba`. Each baseline batch clears the mounted
tree, creates a fresh database root and reconstructs every accumulated key. The
new helper appends only the next 500 keys to the existing root. Both use a native
300 × 700 `QTreeWidget`, expand the database root, leave folders collapsed and
call `QApplication.processEvents()` after **every batch**. Thus this measures
repeated native item construction/insertion and visible event processing, rather
than only one helper call. It excludes key generation, network I/O, service
merging, `MainWindow` worker dispatch, search filtering and teardown after the
final batch. Icon/font caches are warmed before timing.

| Key order / shape | Keys | Batches | Accumulated rebuild | Incremental append | Final descendant items |
| --- | ---: | ---: | ---: | ---: | ---: |
| Ordered / flat | 10,000 | 20 | 0.6637 s | 0.1939 s | 10,000 |
| Ordered / flat | 50,000 | 100 | 17.4633 s | 1.0985 s | 50,000 |
| Ordered / grouped | 10,000 | 20 | 0.7719 s | 0.1749 s | 10,101 |
| Ordered / grouped | 50,000 | 100 | 22.5964 s | 1.5638 s | 50,501 |
| Shuffled / flat | 10,000 | 20 | 0.6875 s | 0.1889 s | 10,000 |
| Shuffled / flat | 50,000 | 100 | 22.2353 s | 2.9896 s | 50,000 |
| Shuffled / grouped | 10,000 | 20 | 1.2388 s | 0.3184 s | 10,101 |
| Shuffled / grouped | 50,000 | 100 | 31.7304 s | 1.9108 s | 50,501 |

Flat keys are `key000000` through the requested count. Grouped keys are
`app:tenant0000:key000000`, with 100 keys per tenant. The shuffled workload uses
`random.Random(20261126).shuffle(tables)`, then sorts each consecutive group of
500 by raw key, matching the adapter's per-page sorting without assuming global
SCAN order. The ordered workload favors append-at-end insertion; the shuffled
workload also exercises insertion among existing siblings. This is still a
synthetic fixed-depth workload, not a guarantee for deep or adversarial keys.

Every incremental batch asserts that the first loaded key's `QTreeWidgetItem`
**is the same Python/Qt object**. Final descendant counts above verify retention
of all keys and expected folder counts; the baseline recreates these objects.
The incremental helper binary-searches the insertion position of each new node
and never detaches an existing child. A separate regression mounts a `QLineEdit`
on the search row and confirms that both its row and widget survive subsequent
batches, with search first and folders sorted. The workspace also has to use the
incremental path; these helper timings alone do not measure that integration.

The benchmark's core loop is reproducible with the project interpreter and
`QT_QPA_PLATFORM=offscreen`; use the baseline source from the commit above in a
separate namespace, and the working-tree helper for the new path:

```python
# Build TableInfo inputs outside the timed block; optionally shuffle/sort pages.
# root is mounted in a shown 300 x 700 QTreeWidget, expanded, folders collapsed.
started = time.perf_counter()
for stop in range(500, count + 1, 500):
    if incremental:
        leaves = append_redis_keys(root, connection, tables[stop-500:stop], 'empty', 'keys')
        # Retain the first leaf from batch one and assert `is` on later batches.
    else:
        if stop != 500:
            tree.clear()
            root = QTreeWidgetItem(tree, ['DB0'])
            root.setExpanded(True)
        leaves = baseline_append(root, connection, tables[:stop], 'empty', 'keys')
    app.processEvents()
elapsed = time.perf_counter() - started
```

No resident-memory peak, real display paint cost, scrolling latency, variable
search-widget height, external Redis key scan latency or operating systems other
than this macOS host were benchmarked. Retaining all loaded native nodes still
uses memory proportional to loaded keys/folders. Qt insertion among many flat
siblings still has a cost, as the shuffled flat workload illustrates. This
measurement supports avoiding cumulative reconstruction; it does not establish
that arbitrarily large trees are free of performance limits.

## Real Redis collection continuation

New opt-in integration tests in
`tests/integration/test_redis_continuation_live.py` passed **8 cases in 1.10 s**:
Hash, List, Set and ZSet, each over a direct connection and a real loopback SSH
forward using `TestSSHServer`. The user-provided local Redis was reachable during
this run; credentials stayed in a private JSON configuration outside the repo.
Each case writes a unique temporary key, sets a 120-second expiry and cleans it
up. The test reads 1,205 entries through `DatabaseService`, verifies bounded
initial/continuation batches, merges until completion and checks every identity
and value, including a binary key, binary member/field and binary hash payload.
The serialized `DUMP` remains unchanged and the original expiry keeps counting
down. SSH cases also verify the actual forwarding destination.

Run with a private configuration matching `tests/integration/conftest.py`:

```sh
BASESKIFF_LIVE_CONFIG=/absolute/path/private-live.json \
  /Users/feiwenchao/miniconda3/envs/base-skiff/bin/python -m pytest -q \
  tests/integration/test_redis_continuation_live.py
```

Hash/Set/ZSet use HSCAN/SSCAN/ZSCAN with a maximum of eight scan calls and 1,000
returned entries per load. The continuation retains overflow responses and
byte-identity deduplication state; COUNT remains a server hint, so Redis may send
more than the display batch in one response. Lists use bounded LRANGE offsets.
Continuation is bound to connection, database, full raw key and Redis type;
changed/expired types require refresh. Scanning a mutable collection is weakly
consistent: same-type replacement, concurrent insertions/removals and list index
shifts do not form a stable server snapshot. Refresh after concurrent changes.
Stream stays a 1,000-entry preview. List row deletion remains restricted to a
complete list of at most 1,000 entries, even after larger lists are fully loaded.

These live cases establish actual service/SSH continuation correctness on this
server. They do not measure native value-widget responsiveness, large-payload
memory usage, Redis Cluster behavior or remote-network performance. Unit tests
separately cover bounded sparse scans, overflow/deduplication, scope/type
rejection, stale Qt cache recovery, widget continuation controls and preservation
of previously displayed value rows when appending.
