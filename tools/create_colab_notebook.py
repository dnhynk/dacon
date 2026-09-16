"""Generate a thin Colab launcher for the SAME canonical submission package.

This notebook never selects experiment branches, installs a second runtime, or
opens validation labels. Prepare the approved fixed runtime/model/data once and
set their paths below.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_submission import payload_manifest, source_files, source_payload

SOURCES = source_files()


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
    payload = source_payload()
    embedded = {name: value.decode("utf-8") for name, value in payload.items()}
    manifest = payload_manifest(payload)
    cells = [
        cell("markdown", """# DACON 236754 · 단일 제출 코드 실행

로컬 script.py → submission/과 정확히 같은 소스입니다. 다른 실험 후보를 선택하거나
개발/검증 자료를 자동 분할하지 않습니다. 검증된 대회 고정 런타임과 모델, 제공 데이터를
먼저 준비하고 다음 셀의 경로를 맞추세요. 이 노트북은 환경 재설치·모델 재다운로드를 하지 않습니다.

유료 GPU를 사용합니다. 입력 한 묶음에 대해 새 응답을 생성하며, 실행 셀을 다시 누르면
새 비용이 발생합니다. 모델은 한 번 적재하고 A1/A10/A19를 32공고씩, 이후 L19와
대상 공고의 고시 품목 검토(Q10), 조건에 맞는 규격 검토(S9)를 실행합니다.
재현 제어와 native 응답 기록은 별도 노트북 코드가 아닌 제출 본체가 담당합니다.

실행 성공과 점수 향상, L40S 2시간 충족은 서로 다른 확인입니다.
끝나면 결과를 먼저 내려받고 런타임 연결 해제 및 삭제를 완료하세요.
"""),
        cell("code", """from pathlib import Path
import datetime
import hashlib
import json
import os
import subprocess
import sys
import zipfile

WORK = Path('/content/dacon_submission')
PY = Path(os.environ.get('PPS_PYTHON', sys.executable))
DATA_DIR = Path(os.environ.get('PPS_DATA_DIR', '/content/data'))
MODEL_DIR = Path(os.environ.get('PPS_MODEL_DIR', '/opt/models/gemma-4-26B-A4B-it'))
INPUT = DATA_DIR / 'test.jsonl.gz'
WORK.mkdir(parents=True, exist_ok=True)
print('Python:', PY, 'Input:', INPUT, 'Model:', MODEL_DIR)
""", "준비된 런타임·데이터·모델 경로"),
        cell("code", "SOURCE_FILES = " + repr(embedded) + "\nSOURCE_MANIFEST = " + repr(manifest) + "\n" + """conflicts = [name for name, content in SOURCE_FILES.items()
             if (WORK / name).exists() and (WORK / name).read_bytes() != content.encode('utf-8')]
if conflicts:
    raise RuntimeError('다른 소스가 있는 폴더입니다. WORK를 새 폴더로 지정하세요: ' + str(conflicts))
for name, content in SOURCE_FILES.items():
    target = WORK / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content.encode('utf-8'))
    assert hashlib.sha256(target.read_bytes()).hexdigest() == SOURCE_MANIFEST['files'][name]['sha256']
print('Canonical source:', SOURCE_MANIFEST['source_fingerprint'])
""", "동일한 제출 소스 준비"),
        cell("code", """for path in (PY, INPUT, DATA_DIR, MODEL_DIR):
    if not path.exists():
        raise FileNotFoundError(path)
stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
OUTPUT_DIR = WORK / 'runs' / stamp
LOG = WORK / 'runs' / (stamp + '.log')
LOG.parent.mkdir(parents=True, exist_ok=True)
args = [str(PY), '-B', str(WORK / 'script.py'), '--input', str(INPUT),
        '--data-dir', str(DATA_DIR), '--model-dir', str(MODEL_DIR),
        '--output-dir', str(OUTPUT_DIR)]
with LOG.open('x', encoding='utf-8') as stream:
    with subprocess.Popen(args, cwd=WORK, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, bufsize=1) as proc:
        for line in proc.stdout:
            print(line, end='', flush=True)
            stream.write(line)
            stream.flush()
        return_code = proc.wait()
print('Exit:', return_code, 'Outputs:', OUTPUT_DIR)
if return_code:
    raise RuntimeError('추론 실패 원문과 로그를 보존했습니다. 재실행 전에 다음 셀로 회수하세요.')
""", "단일 제출 경로로 실제 추론 1회"),
        cell("code", """from google.colab import files

archive_path = WORK / ('result_' + stamp + '.zip')
with zipfile.ZipFile(archive_path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
    if LOG.exists():
        archive.write(LOG, 'runtime.log')
    for path in sorted(OUTPUT_DIR.rglob('*')):
        if path.is_file() and not path.is_symlink():
            archive.write(path, 'results/' + path.relative_to(OUTPUT_DIR).as_posix())
    archive.writestr('source_manifest.json', json.dumps(SOURCE_MANIFEST, ensure_ascii=False, indent=2))
print('Private result SHA256:', hashlib.sha256(archive_path.read_bytes()).hexdigest())
files.download(str(archive_path))
""", "실패를 포함한 결과·실행 소스 해시 회수"),
        cell("markdown", """결과 ZIP은 개발용 비공개 자료입니다. 공개 GitHub에 올리지 마세요.
제출용 소스 ZIP은 로컬 tools/build_submission.py로 만듭니다.
다운로드와 원문 개수·해시를 확인한 뒤 런타임 → 연결 해제 및 삭제로 과금을 종료하세요.
"""),
    ]
    notebook = {"nbformat": 4, "nbformat_minor": 5, "metadata": {
        "colab": {"name": "dacon_colab.ipynb", "provenance": []},
        "kernelspec": {"display_name": "Python 3", "name": "python3"},
        "language_info": {"name": "python"}, "accelerator": "GPU"}, "cells": cells}
    destination = ROOT / "notebooks/dacon_colab.ipynb"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(notebook, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"notebook": str(destination), "cells": len(cells),
                      "embedded_files": len(payload), "source_fingerprint": manifest["source_fingerprint"]}))


if __name__ == "__main__":
    main()
