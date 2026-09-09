"""Local/Colab preparation only. Never bundled in the offline submission."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

MODEL = "google/gemma-4-26B-A4B-it"
REVISION = "4d7ae4984b7db7de8f8457170b3f1a419ee76d52"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--target", type=Path, default=Path("models/gemma"))
    p.add_argument("--tokenizer-only", action="store_true")
    a = p.parse_args()
    from huggingface_hub import snapshot_download
    patterns = ["*.json", "*.jinja"]
    if not a.tokenizer_only:
        patterns.append("*.safetensors")
    snapshot_download(MODEL, revision=REVISION, local_dir=str(a.target), allow_patterns=patterns)
    provenance = {"model": MODEL, "revision": REVISION, "tokenizer_only": a.tokenizer_only}
    (a.target / "competition_provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    print(json.dumps(provenance))


if __name__ == "__main__":
    main()
