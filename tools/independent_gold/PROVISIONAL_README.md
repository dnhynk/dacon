# Provisional source-checked release — not a gold answer key

`provisional_release.py` makes no model calls. It combines an exact 20,000-row
organizer source file, an independent first-pass JSONL, and optionally
a subset of blind, different-family second-pass JSONL. It does **not** import
production predictions or official labels. The output is **not** suitable as
an automatic training, tuning, or scoring target for the Gemma production
system: independent model quality, actual blind execution, expert audit, and
superiority to production are not established by this adapter.

The input pass JSONL has one object per annotated notice:

```json
{
  "schema_version": "dacon.independent.provisional_pass_row.v1",
  "record_id": "PPS-D-...",
  "source_sha256": "<SHA-256 of canonical organizer record>",
  "annotator_role": "candidate",
  "lineage": {
    "provider": "openai",
    "model_family": "openai:gpt-5.6",
    "model_name": "<model alias or snapshot>",
    "prompt_sha256": "<64 lowercase hex digits>",
    "receipt_sha256": "<64 lowercase hex digits>",
    "input_boundary": "competition_source_only",
    "blind_first_pass": true,
    "peer_answer_visible": false,
    "production_output_visible": false
  },
  "ledger": {"schema_version": "dacon.independent.full_record_cell_ledger.v1", "record_id": "PPS-D-...", "source_spans": [], "cells": []}
}
```

The `ledger` must be the actual 24-cell output of
`full_record_output.canonical_ledger_projection`, not the abbreviated
placeholder above. The second-pass role is `verifier`. Every first-pass ID
must occur exactly once; second-pass IDs may be a subset. Each ID and source
hash must match the organizer record. Missing first-pass rows are retained as
24 blank `missing` cells, not silently deleted or filled with zero. Every
present span, quote, coordinate, document
hash, evidence reference, completeness state, and ordered v1–v24 decision is
replayed from the organizer's own bytes. A second pass must declare a different
provider, model family, prompt hash, and receipt hash. These lineage fields are *claims*
from the input producer, not verified attestations.

```text
python -B tools/independent_gold/provisional_release.py \
  --records data_open/train_unlabeled.jsonl.gz \
  --primary path/to/candidate_pass.jsonl \
  --secondary path/to/verifier_subset.jsonl \
  --output-dir runs/my_new_provisional_release
```

Omit `--secondary` for a one-pass draft. The default exact record count is
20,000; `--expected-count` exists only for synthetic tests or a separately
declared partial source cohort. The output directory must not already exist.
A failed build never publishes the requested directory; a uniquely named
`.incomplete-*` sibling is retained as diagnostic evidence.

`provisional_cells.jsonl` has 24 rows per organizer notice, in organizer/item
order. `provisional_label` is a **non-gold** working vote and `gold_label` is
always `null`. Tiers are:

- `draft`: one binary source-checked vote, unverified by a second model;
- `corroborated`: two declared different-family binary votes agree;
- `contested`: two binary votes disagree, so no provisional label;
- `abstain`: either vote is `U` or organizer-declared source is incomplete,
  so no provisional label even if two binary votes agree;
- `missing`: no first-pass row, so no provisional label.

Every cell carries both available votes, risk flags, `eligible_as_gold=false`,
and review priority. `unresolved=true` for drafts, contests, abstentions, and missing rows;
agreement only means no immediate vote conflict, **not** proven correctness.
`summary.json` counts tiers and risks. `manifest.json` hashes all inputs and
outputs and explicitly states that there are zero gold cells, no expert audit,
no established production superiority, and no permission to tune production
on this release as if it were ground truth.

No label CSV is emitted by default. `--emit-provisional-csv` is an explicit
opt-in for `provisional_opt_in.csv`: only high-confidence, source-complete,
non-ambiguous `corroborated` cells receive a 0/1. Draft, contested, abstain,
missing, medium/low-confidence, and other boundary cells remain empty strings.
Even this optional file remains provisional, not a gold or automatically safe
development target.
