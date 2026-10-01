# Start here

The competition (DACON 236754) ended on 2026-09-29; this repository is an archive.
Read `README.md` for the structure, the final result and the local-only files, and
`docs/STATE.json` for the official submission ledger and the runtime switches.
There is no live task, GPU, scheduled submission or other agent to coordinate with.

- `submission/` is the one submission runtime; root `script.py` calls `submission.main.main()`.
  `tools/build_submission.py` builds a zip from it.
- Earlier tracks live under the tag `archive/pre-cleanup-20260926`
  (`git checkout archive/pre-cleanup-20260926 -- <path>`).
- The two final zips under `artifacts/rebuild_c/` are local evidence; do not overwrite them.
  Their SHA256 values are in `docs/STATE.json`.
- Provided data, organizer notices and `submission/pps_c/assets/` are local copies
  and never go into git (the GitHub repository is public).
