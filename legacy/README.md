# legacy/

Retired runtime kept for reading and rollback. Nothing here is packaged, imported or tested by the canonical
runtime (`submission/`, pipeline C).

| Directory | Contents |
|---|---|
| `submission_b/` | Track-B runtime (build_19 + P2g rules) that was `submission/` before pipeline C replaced it |
| `tests_b/` | Tests that import that runtime, or tools that import it (with `fixtures/`) |
| `tools/` | Tools that import that runtime, its build tool and its Colab notebook generator |
| `notebooks/` | Its Colab notebook |

These files are not importable in place: they import `submission.*` and `tools.*` as the old layout named them.
To run them, restore the tree from snapshot commit `1e4263c` ("Snapshot track-B runtime and pipeline C as
submitted in P3a"), e.g. `git worktree add <dir> 1e4263c`.
