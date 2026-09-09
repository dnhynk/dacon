"""Fail before expensive downloads when the allocated Colab runtime is unsuitable."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    report = {"python": sys.version, "disk_free_gib": round(shutil.disk_usage('.').free / 2**30, 1)}
    issues = []
    try:
        text = subprocess.check_output(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"], text=True)
        gpu, memory, driver = [s.strip() for s in text.splitlines()[0].split(",")]
        report.update(gpu=gpu, vram_mib=int(memory), driver=driver)
        if int(memory) < 39000:
            issues.append("지정 모델의 INT8 실험은 이 노트북에서 40GB 이상 GPU를 요구합니다. A100급 GPU를 선택하세요.")
        if int(driver.split('.')[0]) < 580:
            issues.append("평가 서버의 vLLM 0.26.0/CUDA 13 재현에는 NVIDIA 드라이버 580 이상이 필요합니다. 현재 런타임은 호환되지 않습니다.")
    except (OSError, subprocess.CalledProcessError, ValueError):
        issues.append("NVIDIA GPU를 찾지 못했습니다. 런타임 유형에서 GPU를 선택하세요.")
    meminfo = Path('/proc/meminfo')
    if meminfo.exists():
        values = {line.split(':')[0]: int(line.split()[1]) for line in meminfo.read_text().splitlines()}
        report['ram_gib'] = round(values['MemTotal'] / 2**20, 1)
        if report['ram_gib'] < 55:
            issues.append("모델 로드를 위해 RAM 60GiB 수준의 고용량 메모리 런타임이 필요합니다.")
    if report['disk_free_gib'] < 75:
        issues.append("모델과 런타임을 받을 여유 디스크가 부족합니다. 75GiB 이상을 확보하세요.")
    report['issues'] = issues
    Path('artifacts').mkdir(exist_ok=True)
    Path('artifacts/colab_preflight.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if issues:
        raise SystemExit("환경 확인에서 중단했습니다. 위 결과를 Codex에 전달하세요. 모델 다운로드·추론은 시작하지 않았습니다.")


if __name__ == '__main__':
    main()
