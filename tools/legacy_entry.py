"""Stop obsolete root-pps CLIs before they can run or read research data."""
from __future__ import annotations

import argparse


def retired_main(name):
    parser = argparse.ArgumentParser(
        prog=name,
        description=(
            "RETIRED: this historical tool uses the old root pps pipeline, not the "
            "canonical submission. Its implementation is retained as historical evidence. "
            "Run script.py for inference, tools/evaluate.py for explicit CSV comparisons, "
            "or tools/replay_preserved_reference.py for the frozen .751807 replay. "
            "See tools/README.md."
        ),
    )
    parser.parse_args()
    parser.error("Retired legacy pipeline; no model, labels or output were opened.")
