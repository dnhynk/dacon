"""Verify the installed native runtime before downloading model weights."""
import importlib.metadata
import json
import shutil
import subprocess
from pathlib import Path


def main():
    import torch
    import vllm
    import transformers
    from vllm.sampling_params import StructuredOutputsParams

    ninja = shutil.which("ninja")
    if ninja is None:
        raise RuntimeError("ninja is not on PATH; add the GPU virtual environment's bin directory before launching Python")
    ninja_version = subprocess.check_output([ninja, "--version"], text=True).strip()
    expected = {"vllm": "0.26.0", "torch": "2.11.0", "transformers": "5.14.1", "xgrammar": "0.2.3"}
    actual = {key: importlib.metadata.version(key) for key in expected}
    for name, version in expected.items():
        if actual[name].split("+")[0] != version:
            raise RuntimeError(f"Unexpected {name}: {actual[name]} (required {version})")
    if not torch.cuda.is_available():
        raise RuntimeError("The installed PyTorch runtime cannot access the NVIDIA GPU")
    x = torch.arange(256, dtype=torch.float32, device="cuda").reshape(16, 16)
    value = float((x @ x.T).sum().cpu())
    torch.cuda.synchronize()
    report = {"packages": actual, "torch_cuda": torch.version.cuda,
              "ninja": {"path": ninja, "version": ninja_version},
              "gpu": torch.cuda.get_device_name(), "cuda_matrix_check": value,
              "structured_output_api_available": StructuredOutputsParams is not None,
              "gemma4_config_available": hasattr(transformers, "Gemma4Config")}
    if not report["gemma4_config_available"]:
        raise RuntimeError("Gemma 4 model support missing from installed transformers")
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/gpu_runtime_check.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
