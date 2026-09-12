# Start here

Read `START_HERE.md`, then `docs/STATE.json`. If present, read
`docs/LOCAL_HANDOFF.md` for live ownership and the current bounded task.
Run `python -B tools/project_status.py`; it does not allocate a GPU or read labels.
Do not load all historical reports into context before choosing the current task.

## One canonical submission; preserve evidence

- Root `script.py` calls `submission.main.main()`. `submission/` is the ONE mutable
  submission runtime. Integrate validated improvements here; do not create another
  active `experiments/*/source` baseline. The build tool and notebook use this same source.
- Root `pps/` and `model/` are legacy, not imports or packaging inputs for submission.
- Historical CPU replay, fresh inference, mixed-response experiments, and official
  scores are different measurements. Do not replace one with another.
- Historical frozen `source/`, inputs, responses, labels, predictions and receipts
  are immutable rollback/evidence. New result directories record runs of the
  canonical runtime, not competing submission implementations.
- If fresh inference drops substantially, resolve and account for that regression
  before normal feature promotion. Never silently reset the baseline to a lower score.
- Do not rerun old checkpoint/finalization scripts: some overwrite status files or
  already completed output directories. Use fresh output paths.
- Before editing, check `git status --short` and the live ownership notes. Preserve
  other agents' work. Do not create a replacement Orca Run or reconnect a GPU on
  the strength of an old report's `ACTIVE` heading.
- Research artifacts are mostly ignored by git. Use `rg -uu` in the relevant
  directory, excluding model weights, environments and data when unnecessary.

## Keep the next session informed

`docs/STATE.json` is the sole current score/status registry. Update it and the
small local handoff when a round ends. Put detailed evidence in that round's
directory; do not prepend another running history to the root documents.
See `docs/WORKFLOW.md` for execution, evidence, cost and publication boundaries.

## Computer Use preference

Use the official OpenAI Computer Use plugin for Codex desktop UI interaction.
Read `C:/Users/dongh/.codex/skills/openai-computer-use/SKILL.md` for the installed
official entry point. Do not use or reinstall Orca Computer Use, including
`orca computer`. If the official runtime is unavailable, report the connection
limitation instead of falling back to Orca. This does not restrict Orca CLI
worktrees, terminals, orchestration, or its embedded browser.
