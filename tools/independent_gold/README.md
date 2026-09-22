# Independent 20k gold construction

This package builds an annotation dataset without importing or executing the
competition submission.  The only admissible semantic inputs are:

- `data_open/train_unlabeled.jsonl.gz`
- `data_open/dev.jsonl.gz` and `data_open/dev_labels.csv`
- `data_open/data/항목표.json`
- `data_open/data/법령패키지/`
- source-grounded review notes whose quoted text can be traced back to the
  supplied data

`submission/`, saved Gemma responses, and historical prediction CSVs are
forbidden as annotation inputs.  They may be compared only after an annotation
snapshot is frozen.

The workflow deliberately separates four artifacts:

1. `candidate`: an independent model's source-grounded proposal;
2. `verification`: a second model family's blind review;
3. `adjudication`: a resolved decision for every disagreement or uncertainty;
4. `gold`: the final 49-column CSV plus a richer JSONL audit ledger.

A candidate model is not promoted merely because it filled every cell.  The
dev gate in `score_dev.py` must pass, every positive evidence quote must occur
verbatim in the supplied record, and the final build refuses unresolved cells.

## Typical commands

```powershell
python -B tools/independent_gold/annotate.py `
  --input data_open/dev.jsonl.gz `
  --output runs/self_label_20000_20260918/qwen_dev.jsonl `
  --endpoint http://127.0.0.1:18080/v1/chat/completions `
  --model Qwen3-30B-A3B-Q4_K_M.gguf

python -B tools/independent_gold/score_dev.py `
  --annotations runs/self_label_20000_20260918/qwen_dev.jsonl
```

The audited Codex topology-B path makes one complete 24-item call per record.
Preparation is the default; add `--execute` only after reviewing the frozen
manifest and cohort plan.  Candidate and verifier must be staged separately.

```powershell
python -B tools/independent_gold/codex_full_record_annotator.py `
  --input data_open/dev.jsonl.gz `
  --staging-dir runs/my_candidate_dev_shard0 `
  --model gpt-5.6-sol --reasoning-effort xhigh `
  --annotator-role candidate `
  --prompt-profile candidate-full-record-source-direct-v2 `
  --phase official_dev --shard-index 0 --shard-count 4

python -B tools/independent_gold/codex_full_record_annotator.py `
  --input data_open/dev.jsonl.gz `
  --staging-dir runs/my_verifier_dev_smoke `
  --model gpt-6-astra --reasoning-effort xhigh `
  --annotator-role verifier `
  --prompt-profile verifier-full-record-falsification-v2 `
  --phase development_diagnostic --record-id PPS-DEV-11
```

All shards use organizer-order stride partitioning.  Their cohort plans are
hash-bound and the full-dev gate accepts them only when their disjoint union is
exactly 200 records / 4,800 cells.  An opaque hosted alias still requires the
separate fail-closed before/during/after drift-canary continuity gate; a staged
or completed annotator run does not by itself satisfy that requirement.

The v2 model answer contains only `source_span_ids` and 24 decisions.  Exact
coordinates, quotes, and document hashes are reconstructed from the supplied
organizer record's deterministic gapless registry and retained in the ledger;
the raw model answer and event stream remain immutable.  The v1 output/profile
tuple and its failed canary observations cannot qualify this v2 wire format.
Freeze a fresh v3 canary plan, run fresh before-qualification canaries for both
roles, and requalify the complete official dev cohort under the new tuple
before any unlabeled 20,000-record pass.  No old checkpoint is reusable.

The 20,000-record run is resumable: existing valid IDs in the output JSONL are
skipped.  Raw responses and request hashes are retained beside each decision.
Candidate/verifier disagreements, abstentions, and low confidence must be
resolved by an independent qualified adjudicator or source-first human; their
original votes remain in the ledger.  `build_gold.py` refuses to emit
`gold.csv` while even one final cell is unresolved, lacks the required vote
lineage, or is backed by invalid evidence.

## Assemble, adjudicate, and seal the review ledger

Do not merge checkpoint JSONL directly.  After both 20,000-record runs and
their official-dev qualifications are complete, the workflow verifier replays
the run manifest, every task/attempt artifact, raw event stream, output ledger,
and source binding.  It freezes a preflight plan *before* the first ledger
event, regenerates every model snapshot, and emits only a label-blind queue.

```powershell
python -B tools/independent_gold/review_ledger_workflow.py prepare-model-passes `
  --input data_open/train_unlabeled.jsonl.gz `
  --audit-policy runs/my_gold/audit/audit_policy.json `
  --candidate-run-dir runs/my_gold/candidate/shard-0 `
  --candidate-run-dir runs/my_gold/candidate/shard-1 `
  --verifier-run-dir runs/my_gold/verifier/shard-0 `
  --verifier-run-dir runs/my_gold/verifier/shard-1 `
  --candidate-qualification runs/my_gold/qualification/candidate.json `
  --verifier-qualification runs/my_gold/qualification/verifier.json `
  --preflight-plan runs/my_gold/review/preflight.json `
  --ledger runs/my_gold/review/review_ledger.jsonl `
  --blind-queue runs/my_gold/review/human_queue.jsonl `
  --prepare-manifest runs/my_gold/review/prepare_manifest.json
```

Queue packets expose the organizer source and unresolved item IDs, but no
candidate/verifier label, confidence, evidence, rationale, trigger, or route.
Human judgment rows are self-hashed and bind the packet, source, initial model
snapshot, reviewer, UTC review time, exact premise quotes, and final decision.
They are embedded verbatim in the append-only ledger.  A low-confidence vote,
`U`, uncertainty flag, inexact quote, missing cell, extra cell, stale packet,
or candidate/verifier identity overlap aborts before sealing.

For the 853 organizer records that declare dropped documents, every item is
routed to adjudication, including apparent model consensus.  The blind judgment
must name each dropped document type and explain, from competition-supplied
material alone, why the missing content cannot change that particular item's
decision.  If this cannot be established, the cell stays unresolved and the
entire 20,000-record answer key is not sealed.  External archives,
procurement sites, and crawling are forbidden annotation inputs under the
[competition rules](https://dacon.io/competitions/official/236754/overview/rules).

```powershell
python -B tools/independent_gold/review_ledger_workflow.py finalize-human `
  --input data_open/train_unlabeled.jsonl.gz `
  --ledger runs/my_gold/review/review_ledger.jsonl `
  --preflight-plan runs/my_gold/review/preflight.json `
  --prepare-manifest runs/my_gold/review/prepare_manifest.json `
  --blind-queue runs/my_gold/review/human_queue.jsonl `
  --judgments runs/my_gold/review/human_judgments.jsonl `
  --human-roster runs/my_gold/review/human_roster.json `
  --export-output-dir runs/my_gold/review/export `
  --human-receipt runs/my_gold/review/human_adjudicator_receipt.json
```

If every model cell is independent consensus, omit the three human arguments.
The command then seals directly and correctly emits no human receipt.  Export
is built in a fresh staging directory, replayed against the sealed active
snapshots, and only then published.  A stopped run can resume only when each
already-written snapshot regenerates byte-for-byte from the frozen inputs.
The final `build_gold.py` CLI also requires `--preflight-plan` and
`--prepare-manifest`; it replays both preserved raw model-run trees and the
initial ledger events instead of trusting the sealed ledger alone.  Its audit
report v3 must bind the blind session, source packets, and individual judgments,
which the builder rechecks against the reported PASS results.

## Opaque hosted-alias continuity

`gpt-5.6-sol` and `gpt-6-astra` are requested hosted aliases; the CLI does not
attest their resolved service-side revisions.  `opaque_alias_canary.py` never
turns an alias into a claimed revision.  It freezes disjoint, label-free dev
canaries for candidate and verifier, then requires a separate role-specific
epoch at these exact boundaries: before qualification, immediately before the
full run, every predeclared full-run interval, and after the final task.

```powershell
python -B tools/independent_gold/opaque_alias_canary.py plan `
  --output runs/my_gold/canary/plan.json

python -B tools/independent_gold/opaque_alias_canary.py runner-command `
  --plan runs/my_gold/canary/plan.json `
  --role candidate `
  --checkpoint c000-before-qualification `
  --staging-dir runs/my_gold/canary/candidate/c000-before-qualification

python -B tools/independent_gold/opaque_alias_canary.py observe `
  --plan runs/my_gold/canary/plan.json `
  --role candidate `
  --checkpoint c000-before-qualification `
  --staging-dir runs/my_gold/canary/candidate/c000-before-qualification `
  --output runs/my_gold/canary/candidate/result-000.json

python -B tools/independent_gold/opaque_alias_canary.py advance `
  --plan runs/my_gold/canary/plan.json `
  --result runs/my_gold/canary/candidate/result-000.json `
  --output runs/my_gold/canary/candidate/epoch-000.json
```

Later `advance` calls add `--previous-epoch`.  A label, positive-evidence
coordinate/quote, schema, source/context/request hash, semantic tuple, profile,
model identity, or safety-event change closes that role's epoch.  The manifest
then names the exact organizer-order task IDs since the previous passing
canary that must be discarded.  A complete epoch is continuity evidence only,
not cryptographic model attestation.  The orchestrator must still place each
canary at its declared workload boundary; an epoch manifest cannot prove wall-
clock placement relative to work performed outside these runner artifacts.

## Precommitted human audit

Freeze the audit policy before any full-run review event.  The supplied policy
for the current epoch already exists at
`runs/self_label_20000_20260918/audit_v2/audit_policy.json`; changing it starts
a new epoch.

The frozen v2 policy samples 30 agreement cells per item/category/completeness
stratum.  Even if all 30 pass, the one-sided 95% defect-rate upper bound is
about 9.5% for that stratum under a genuinely random, accurate audit.  It
does not certify all 480,000 labels or show that gold beats production.

For a **new, not-yet-started annotation epoch**, v3 can precommit a secret
256-bit seed by publishing only
`SHA256("dacon-independent-audit-v3-seed\0" + lowercase_hex_seed)`.
The seed itself stays outside the policy until the review ledger is sealed.
V3 refuses to freeze without an organizer-only notice-family manifest that
can be replayed exactly from the supplied 20,000 records.  Family IDs are
sampling blocks, never permission to propagate labels between notices.
The default primary sample is 300 per nonmandatory item/category/completeness
stratum, or its entire population if smaller.  Every adjudicated cell remains
mandatory, including all 24 cells for each of the 853 incomplete notices.
Separately, v3 selects at least **one** cell per nonmandatory
recurring-family/item/category/completeness stratum;
`--recurring-family-per-stratum` can increase that minimum *before*
policy freeze.  The default one is coverage, not a family accuracy bound, and
does not automatically census all 5,523 notices in repeated families.

```powershell
python -B tools/independent_gold/gold_audit.py freeze-policy `
  --version v3 `
  --input data_open/train_unlabeled.jsonl.gz `
  --family-manifest runs/self_label_20000_20260918/audit_v3/notice_families.json `
  --seed-commitment-sha256 <SHA256-OF-SECRET-SEED> `
  --output runs/my_new_epoch/audit/policy.json
```

Only after sealing the new epoch's review export, provide the seed via
`freeze-selection --seed-reveal-file` from a restricted-access file.  The v3
plan and final builder verify the commitment, organizer-only family replay,
result-independent rank, and exact selection.  The v3 report records
zero-defect 95% *primary-stratum sampling* limits.  Those limits are
conditional detection bounds, not a guarantee of whole-key correctness or
production superiority; a separate blind expert paired comparison is needed
for the latter claim.  The code checks the policy's declared freeze UTC is no
later than the first review-ledger event UTC and that the plan binds the same
policy file hash.  These self-declared timestamps and local hashes do **not**
independently prove real-world pre-annotation publication or that the seed was
never leaked.  Keep the seed with an independent custodian until ledger seal;
without credible custody and time evidence, do not describe the ranked sample
as manipulation-proof random sampling or use its confidence bounds as a
strong accuracy claim.  The existing v2 policy and command remain unchanged:

```powershell
python -B tools/independent_gold/gold_audit.py freeze-policy `
  --input data_open/train_unlabeled.jsonl.gz `
  --output runs/my_gold/audit/audit_policy.json `
  --per-stratum 30
```

After the independent review ledger has been fully resolved and sealed,
derive the selection and label-free packets mechanically:

```powershell
python -B tools/independent_gold/gold_audit.py freeze-selection `
  --ledger runs/my_gold/review/review_ledger.jsonl `
  --review-export-manifest runs/my_gold/review/review_export_manifest.json `
  --policy runs/my_gold/audit/audit_policy.json `
  --selection-output runs/my_gold/audit/audit_selection.jsonl `
  --plan-output runs/my_gold/audit/audit_plan.json `
  --blind-packets-output runs/my_gold/audit/blind_packets.jsonl
```

An independently attested human auditor receives only `blind_packets.jsonl`.
Start a session with the auditor roster, collect one source-first blind
judgment per selected cell, then finalize.  Any label mismatch, failed check,
low-confidence judgment, incomplete source assessment, missing judgment, or
non-independent auditor produces a FAIL report that final gold rejects.

```powershell
python -B tools/independent_gold/gold_audit.py start-session `
  --plan runs/my_gold/audit/audit_plan.json `
  --blind-packets runs/my_gold/audit/blind_packets.jsonl `
  --auditor-roster runs/my_gold/audit/auditor_roster.json `
  --output runs/my_gold/audit/audit_session.json

python -B tools/independent_gold/gold_audit.py finalize `
  --ledger runs/my_gold/review/review_ledger.jsonl `
  --review-export-manifest runs/my_gold/review/review_export_manifest.json `
  --plan runs/my_gold/audit/audit_plan.json `
  --session runs/my_gold/audit/audit_session.json `
  --judgments runs/my_gold/audit/blind_judgments.jsonl `
  --results-output runs/my_gold/audit/audit_results.jsonl `
  --report-output runs/my_gold/audit/audit_report.json
```

The final builder independently replays the sealed review chain,
qualifications, opaque-alias continuity epochs, policy freeze time, complete
audit selection, blind results, and all 480,000 binary decisions before it
publishes the CSV.
