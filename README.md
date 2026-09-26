# DACON 236754 · 나라장터 공고 법령 위반 탐지

나라장터 입찰공고마다 24개 항목의 위반 여부를 판정하는 제출 런타임(파이프라인 C)입니다.
지정 Gemma가 후보 줄에서 정해진 사실을 읽고, CPU 규칙(`submission/pps_c/judge.py`)이 항목을 판정합니다.
새 세션은 **[START_HERE.md](START_HERE.md)**부터 읽으세요.
현재 점수·상태의 기준은 [docs/STATE.json](docs/STATE.json)입니다.

## 구조

| 경로 | 내용 |
| --- | --- |
| `script.py` → `submission/` | 유일한 제출 런타임(`submission/pps_c/`). 탐침 패키지는 `pps_c/switches.py`만 다릅니다 |
| `tools/build_submission.py` | 제출 ZIP 빌드. 스위치를 지정하고, 풀어 낸 ZIP으로 모의 실행합니다 |
| `tools/project_status.py` | 점수·현재 작업 요약. `--verify`는 로컬 보존물의 해시를 확인합니다 |
| `tests/` | 런타임 합성 검사 |
| `docs/` | 상태 레지스트리와 작업 문서 |

## 읽을 곳

| 문서 | 내용 |
| --- | --- |
| [시작 안내](START_HERE.md) | 현재 작업과 실행 경로 |
| [저장소 지도](docs/REPO_MAP.md) | 코드·데이터·증거의 위치와 수정 경계 |
| [작업 원칙](docs/WORKFLOW.md) | 실험, 재현성, 비용, 인계, 공개 |
| [실행 도구](tools/README.md) | 빌드와 상태 도구 |
| [개발 경과](docs/DEVELOPMENT_NARRATIVE.md) | 방향이 정해진 이유와 남은 가설 |

## 시작

```text
python -B tools/project_status.py
python -B -m pytest -q tests
python script.py --help
python tools/build_submission.py <이름> [NAME=VALUE ...]
```

Python 3.12 환경을 사용합니다. Windows에서는 `.venv/Scripts/python.exe`, Linux에서는 `.venv/bin/python`을 씁니다.
개발 의존성은 `requirements-dev.txt`, GPU 환경의 버전은 `requirements-gpu.lock`에 있습니다.
합성 검사는 새 추론·점수·제출 검증을 대신하지 않습니다.

이전 트랙(track A/B 런타임, 동결 실험 소스, 옛 주석·재현 도구와 그 테스트)은 현행 트리에 없습니다.
`archive/pre-cleanup-20260926` 태그에서 복구합니다: `git checkout archive/pre-cleanup-20260926 -- <경로>`.

## 공개 범위

소스·설정·합성 테스트·집계 결과만 공개합니다. 제공 원문, 가공 데이터, 라벨,
저장 응답, 공고별 예측, 가중치, 인증정보, 브라우저/에이전트 로그는 포함하지 않습니다.
따라서 공개 소스만으로 개발 점수를 재계산할 수 없습니다.
로컬 자료가 있을 때 `tools/project_status.py --verify`로 보존물의 해시와 점수 기록을 확인할 수 있지만,
이것도 새 추론은 아닙니다.
