"""Build a self-contained notebook from the same source tested locally."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ["script.py", "requirements.txt", "requirements-gpu.txt", "requirements-gpu.lock", "README.md",
           "model/config.json", "model/reasoned_v2.json", "model/reasoned_rules_v3.json", "model/factored_groups_v4.json",
           *[str(p.relative_to(ROOT)).replace("\\", "/") for p in sorted((ROOT / "pps").glob("*.py"))],
           *["tools/" + name for name in ("colab_preflight.py", "check_gpu_runtime.py", "prepare_data.py",
                                          "download_model.py", "inspect_data.py", "evaluate.py", "run_experiments.py",
                                          "build_submission.py", "export_results.py")]]


def cell(kind, source, title=None):
    result = {"cell_type": kind, "metadata": {}, "source": source.splitlines(keepends=True),
              "id": hashlib.sha256(source.encode()).hexdigest()[:12]}
    if kind == "code":
        result.update(execution_count=None, outputs=[])
        if title:
            result["metadata"]["cellView"] = "form"
            result["source"] = [f"#@title {title}\n", *result["source"]]
    return result


def main():
    source_files = {name: (ROOT / name).read_text(encoding="utf-8") for name in SOURCES}
    cells = [cell("markdown", """# DACON 236754 · 이전 기본 후보 비교 실험

이 노트북은 루트 기본 구현의 초기 비교 도구이며, experiments/의 최신 후보 실행기가 아닙니다.
아래 전체 실행에는 모델 다운로드와 유료 GPU 추론이 포함될 수 있습니다.
GitHub 공개 시점의 최신 결과와 미검증 범위는 저장소 README를 먼저 확인하세요.

Google 계정으로 실행하는 독립 노트북입니다. **런타임 → 런타임 유형 변경 → A100급 GPU / 고용량 RAM**을 먼저 선택하세요.
40GB급 VRAM이 필요합니다. A100 선택이 불가능하면 아래 환경 확인만 실행하고 결과를 전달하세요.
유료 요금제도 특정 GPU를 보장하지 않습니다. [Colab 공식 FAQ](https://research.google.com/colaboratory/faq.html)

첫 환경 확인을 통과하면 필요한 소스, 공식 데이터, 지정 모델을 준비합니다.
8건의 실제 추론이 성공한 뒤 공식 프롬프트와 두 후보를 개발 160건에서 비교하고, 선택한 후보를 별도 40건에서 확인합니다.
결과는 마지막에 `dacon_results.zip`으로 내려받습니다. 완료 후 **런타임 연결 해제 및 삭제**로 사용을 종료하세요.

현재 노트북의 GPU 실행은 미검증이며 순위나 점수를 보장하지 않습니다.
Colab 실행 성공 후에도 실제 평가 GPU인 L40S에서 2시간 제한을 확인해야 합니다.
"""), cell("code", """from pathlib import Path
import datetime
import json
import os
import subprocess
import sys

WORK = Path('/content/dacon236754')
WORK.mkdir(parents=True, exist_ok=True)
os.chdir(WORK)
PREFLIGHT_OK = False
RUNTIME_OK = False

def execute(args, *, log_file=None):
    stream = open(log_file, 'w', encoding='utf-8') if log_file else None
    try:
        with subprocess.Popen([str(x) for x in args], cwd=WORK, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, bufsize=1) as proc:
            for line in proc.stdout:
                print(line, end='', flush=True)
                if stream:
                    stream.write(line)
                    stream.flush()
            code = proc.wait()
        if code:
            raise subprocess.CalledProcessError(code, args)
    finally:
        if stream:
            stream.close()

print('작업 폴더:', WORK)
""", "작업 폴더 준비"), cell("code", "SOURCE_FILES = " + repr(source_files) + "\n" + """for name, content in SOURCE_FILES.items():
    target = WORK / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
print(f'{len(SOURCE_FILES)}개 소스 파일 준비 완료')
""", "검사한 실행 소스 준비"), cell("code", """execute([sys.executable, 'tools/colab_preflight.py'])
PREFLIGHT_OK = True
""", "먼저 GPU·메모리·드라이버 확인 — 실패하면 여기서 중단"), cell("markdown", """환경 확인이 통과한 경우에만 아래로 진행하세요.
설치는 별도 Python 환경을 만들며 Colab 노트북 커널의 PyTorch를 교체하지 않습니다.
대회 지정 버전의 vLLM, PyTorch, Transformers, xgrammar를 사용합니다.
"""), cell("code", """assert PREFLIGHT_OK, '먼저 환경 확인을 통과해야 합니다.'
execute([sys.executable, '-m', 'pip', 'install', '--quiet', 'uv'])
execute([sys.executable, '-m', 'uv', 'venv', '--python', '3.12.13', '--seed', '.gpu-venv'])
PY = WORK / '.gpu-venv/bin/python'
os.environ['PATH'] = str(PY.parent) + os.pathsep + os.environ.get('PATH', '')
execute([sys.executable, '-m', 'uv', 'pip', 'install', '--python', PY,
         '--torch-backend', 'cu130', '--no-cache', '-r', 'requirements-gpu.lock'])
execute([PY, 'tools/check_gpu_runtime.py'])
RUNTIME_OK = True
""", "대회 핵심 런타임 설치 및 CUDA 연산 확인"), cell("code", """assert RUNTIME_OK, '런타임 검사가 통과해야 합니다.'
execute([PY, 'tools/prepare_data.py'])
execute([PY, 'tools/inspect_data.py'])
""", "공식 데이터 다운로드·해시 확인·개발/검증 분할"), cell("code", """assert RUNTIME_OK, '런타임 검사가 통과해야 합니다.'
execute([PY, 'tools/download_model.py', '--target', 'models/gemma'])
""", "지정 모델의 고정 revision 다운로드"), cell("markdown", """다음 셀에서 모델을 한 번 로드하여 실험합니다. 첫 로드와 커널 준비에는 시간이 걸릴 수 있습니다.
`SMOKE_ONLY=True`로 바꾸면 8건의 추론/CSV 확인까지만 실행합니다. 기본값은 전체 비교입니다.
시간 예산은 배치 사이에 검사하므로 이미 시작된 배치는 완료될 수 있습니다.
실패하더라도 저장된 로그와 결과를 다운로드할 수 있도록 구성했습니다.
"""), cell("code", """assert RUNTIME_OK, '런타임 검사가 통과해야 합니다.'
SMOKE_ONLY = False #@param {type:'boolean'}
EXPERIMENT_MINUTES = 75 #@param {type:'number'}
stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d_%H%M%S')
experiment_dir = WORK / 'runs' / stamp
log_path = WORK / 'runs' / f'{stamp}.log'
log_path.parent.mkdir(parents=True, exist_ok=True)
args = [PY, 'tools/run_experiments.py', '--model-dir', 'models/gemma',
        '--out', experiment_dir, '--max-minutes', str(EXPERIMENT_MINUTES)]
if SMOKE_ONLY:
    args.append('--smoke-only')
try:
    execute(args, log_file=log_path)
    if (experiment_dir / 'completed.json').exists():
        selection = json.loads((experiment_dir / 'selection.json').read_text())
        if selection['config'] is not None:
            execute([PY, 'tools/build_submission.py', '--experiment-dir', experiment_dir])
        else:
            print('공식 프롬프트가 우세했습니다. 결과를 분석한 뒤 후보를 개선합니다.')
finally:
    execute([PY, 'tools/export_results.py'])
    from google.colab import files
    files.download(str(WORK / 'dacon_results.zip'))
""", "첫 비교 실험 실행 및 결과 ZIP 다운로드"), cell("markdown", """다운로드한 `dacon_results.zip`을 Codex 작업공간에 전달하면 항목별 오류와 실행 시간을 분석해 다음 후보를 개선할 수 있습니다.
다운로드가 막혔다면 왼쪽 파일 목록에서 `/content/dacon236754/dacon_results.zip`을 직접 내려받으세요.

**결과가 저장된 것을 확인한 후 런타임 → 연결 해제 및 런타임 삭제를 선택하세요.**
결과 ZIP에는 모델 가중치가 없습니다. 생성된 제출 후보도 L40S에서 아직 검증되지 않았으므로 자동 제출하지 않습니다.
""")]
    notebook = {"nbformat": 4, "nbformat_minor": 5, "metadata": {
        "colab": {"name": "dacon_colab.ipynb", "provenance": []},
        "kernelspec": {"display_name": "Python 3", "name": "python3"},
        "language_info": {"name": "python"}, "accelerator": "GPU"}, "cells": cells}
    destination = ROOT / "notebooks/dacon_colab.ipynb"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(notebook, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"notebook": str(destination), "cells": len(cells), "embedded_files": len(SOURCES), "bytes": destination.stat().st_size}))


if __name__ == "__main__":
    main()
