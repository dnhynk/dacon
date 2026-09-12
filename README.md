# DACON 236754 · 나라장터 공고 법령 위반 탐지

지정 Gemma와 원문 검색·전문 판정·근거 검증을 결합하는 연구 저장소입니다.
새 세션은 **[START_HERE.md](START_HERE.md)**부터 읽으세요.
현재 수치·실행 경로의 기준은 [docs/STATE.json](docs/STATE.json)입니다.

## 현재 위치

- **0.758950**: B4+G/WS v9의 측정된 최고 CPU 후보. 보존되어 있으며 전체 새 추론·동등품 해석은 미검증.
- **0.751807**: 위 추가 보정 전 B4의 과거 응답640개 CPU 재판정 기록.
- **0.740544**: 현행 단일 ZIP으로 새640응답을 생성한 최신 개발160 기록. FP61/FN23.
- **0.706466**: 통합 전 새640응답의 개발160 비교 기준.
- **0.718474**: 이전 4회 호출 비교 기준. 원본과 코드 모두 보존.

공식 리더보드 점수는 없으며 목표0.85는 미달성입니다. 두 상위 후보 모두 보존했지만,
새 전체 추론에서는 재현하지 못했습니다. 저장 응답 재판정과 새 추론을 구분합니다.
최신 재현 진단·진행 상태는 위 단일 상태 파일을 확인하세요.
통합본은 원본640개 회수·CPU 재소비·독립 채점을 통과했습니다. 과거 B4 대비 남은
새 오류9개는 모두 원시 판정부터 달랐습니다. 제출 시간은 미해결이며, 같은 A100의
처리량을1853건에 단순 환산한 약4.13시간을 L40S2시간 통과로 해석하면 안 됩니다.

## 읽을 곳

| 문서 | 내용 |
| --- | --- |
| [시작 안내](START_HERE.md) | 3분 안에 현재 작업과 실행 경로 파악 |
| [저장소 지도](docs/REPO_MAP.md) | 코드·데이터·증거·수정 경계 |
| [작업/실행 원칙](docs/WORKFLOW.md) | 새 실험, 재현성, 비용, 인계, 공개 |
| [실행 도구](tools/README.md) | 현행 도구와 종료한 legacy 실행 경로 |
| [실험 목록](experiments/README.md) | 과거 비교·롤백용 동결 기록 |
| [완료 실험 요약](docs/EXPERIMENT_HISTORY.md) | 기각·종료된 연구를 다시 시작하지 않기 |

## 비용 없는 시작

```text
python -B tools/project_status.py
python -B -m pytest -q tests/test_project_status.py tests/test_artifacts.py tests/test_ingest.py
```

Python3.12 환경을 사용합니다. Windows에서는 `.venv/Scripts/python.exe`,
Linux에서는 `.venv/bin/python`을 사용할 수 있습니다. 개발 의존성은
`requirements-dev.txt`, GPU 환경의 버전은 `requirements-gpu.lock`에 있습니다.
합성 검사는 새 추론·점수·제출 검증을 대신하지 않습니다.

실행·개선 경로는 **[script.py](script.py) → submission/** 하나입니다.
최고 기록의 A 입력·CPU 보정·L19 연결을 이 본체로 통합하며, 다음 개선도 여기에 합칩니다.
과거 실험과 루트 `pps/`는 기록용이며 제출 ZIP에 들어가지 않습니다.

```text
python script.py --help
python tools/build_submission.py --output artifacts/submission.zip
```

빌드와 노트북은 동일한 제출 본체를 사용합니다. 현재 통합·실측 상태는 STATE.json을
보세요. ZIP 생성 성공이 최고점 새 추론 재현이나 L40S 2시간 검증을 뜻하지는 않습니다.

## 공개 범위

소스·설정·합성 테스트·집계 결과만 공개합니다. 제공 원문, 가공 데이터, 라벨,
저장 응답, 공고별 예측, 가중치, 인증정보, 브라우저/에이전트 로그는 포함하지 않습니다.
따라서 공개 소스만으로 개발 점수를 재계산할 수 없습니다.
로컬 자료가 있을 때 `tools/project_status.py --verify`로 원본·동결 소스의 해시와
점수 기록을 확인할 수 있지만, 이것도 새 추론은 아닙니다.

대회 제공 자료와 지정 모델 `google/gemma-4-26B-A4B-it`
(revision `4d7ae4984b7db7de8f8457170b3f1a419ee76d52`)을 사용합니다.
실행 중 외부 모델 호출이나 추가 판정모델 학습은 하지 않습니다.

[대회 규칙](https://dacon.io/competitions/official/236754/overview/rules) ·
[평가 안내](https://dacon.io/competitions/official/236754/overview/evaluation) ·
[제공 데이터](https://dacon.io/competitions/official/236754/data)
