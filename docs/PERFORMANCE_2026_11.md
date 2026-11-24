# Redis tree measurement — 2026-11-24

Measured locally with `/Users/feiwenchao/miniconda3/envs/base-skiff/bin/python`
(Python 3.14.10), PySide6, and `QT_QPA_PLATFORM=offscreen`. Synthetic keys
require no database service. Timings are single runs, not statistical estimates.

| Shape | Keys | Native items | Construct and sort | Initial visible layout |
| --- | ---: | ---: | ---: | ---: |
| Flat `key000000` | 10,000 | 10,000 | 35.6 ms | 5.5 ms |
| Flat `key000000` | 50,000 | 50,000 | 197.1 ms | 1.4 ms |
| `app:tenant0000:key000000`, 100 keys per tenant | 10,000 | 10,101 | 44.8 ms | 0.3 ms |
| Same grouped shape | 50,000 | 50,501 | 259.2 ms | 0.3 ms |

`append_redis_keys` was called once per fresh `QTreeWidgetItem` database root,
using `TableInfo` inputs and a Redis `Connection`. Construction timing excludes
input generation. Layout timing covers `QApplication.processEvents()` after
showing a 300 × 700 tree with the database root expanded and folders collapsed.
A separate grouped-tree run expanded every folder: `expandAll()` plus event
processing took 7.6 ms at 10,000 keys and 9.0 ms at 50,000 keys. Twenty alternating
top/bottom scrolls including event processing took 36.2 ms and 32.9 ms respectively.

The helper creates one native leaf per distinct loaded raw key and one folder
per distinct prefix. Work scales with total key segments plus sibling sorting;
these measurements do not establish a helper-only bottleneck requiring a model
rewrite. The surrounding workspace rebuild currently reconstructs accumulated
keys on each load, so repeated batches can accumulate substantially more work
than a single construction. The existing 500-key server batch limit and retention
of all loaded keys were not changed by this audit.

Do not enable `uniformRowHeights` globally without redesigning the scoped Redis
search row: the tree embeds a widget with an explicitly variable height.

Verification: **59 passed in 0.88 s** across `tests/test_redis_tree_idle.py`,
`tests/test_backend_performance.py`, `tests/test_icon_reuse.py`, and
`tests/test_grid_performance.py`. No Redis tree implementation changes were
justified by this isolated measurement. Offscreen layout is not a substitute for
native interactive measurements; memory growth, external services, deep keys,
and end-to-end repeated 500-key loading were not benchmarked here.
