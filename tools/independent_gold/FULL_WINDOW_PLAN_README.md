# Future full-run window plan (planning only)

`full_window_plan.py` reads the organizer's 20,000 unlabeled notices and the
frozen opaque-alias canary schedule. It creates a self-hashed map of forty
consecutive, organizer-order windows of 500 notices. Each window is split into
sixteen position-modulo-stride shards for each blind role, with unique **future**
staging paths. It creates no staging directories and makes no model calls.

The actual frozen boundary name is `c001-pre-full-run`, followed by
`c002-full-00500` through `c040-full-19500`, then `c041-post-full-run`. A
window's `checkpoint_before` and `checkpoint_after` must both be honored.
The plan binds the compressed input SHA-256, every organizer record's source
hash, the frozen canary plan and epoch observations, and the window-plan code
hash. `verify` recomputes every assignment from the organizer source.

**This is not execution approval.** The referenced `continuity_canary_v3`
candidate `c000-before-qualification` epoch is `closed_failure` / fail-closed.
The v3 role tuple has no qualification to run the 20,000 notices. Its schedule
and source-order layout remain useful only as a planning reference. A new
v4-or-later canary plan and epoch must pass `c000` and fresh official-dev
qualification; then regenerate the full-window plan against that successful
plan before creating any active full-run artifact. No old v3 staging path or
tuple may be promoted by this plan.

To produce or replay a plan-only artifact:

```text
python -B -m tools.independent_gold.full_window_plan build
python -B -m tools.independent_gold.full_window_plan verify
```

The `build` command refuses to overwrite a plan or use an already-existing
staging root. It writes only the future plan under `audit_v3/`, never an active
runner cohort, task, response, label, or result.
