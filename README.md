# DACON 236754 · 입찰 공고 법령 위반 탐지

나라장터 자체입찰 공고와 첨부 문서에서 24개 위반 항목을 판단하는 연구 코드입니다.
지정 Gemma 모델의 문서별 추론에 검색, 사실 추출, 원문 근거 확인, 조건별 후처리를 결합합니다.

## 현재 결과

2026-09-09 기준 개발용 160건의 결과입니다. **공식 리더보드 점수가 아닙니다.**

| 후보 | 개발 Macro F1 | FP / FN | 측정 방식 |
| --- | ---: | ---: | --- |
| v7 기준 후보 | 0.601432 | 57 / 44 | 실제 모델 응답 480개 |
| fact_compact, thinking 768 | 0.671429 | 64 / 34 | 실제 모델 응답 480개 |
| fact_compact + 역할·적용조건 보정 | 0.697220 | 60 / 30 | 위 compact 응답을 CPU에서 재판정 |

최신 후보의 8개 오류 회복은 같은 개발 자료의 재판정 결과입니다. 새 공고에서의 일반화,
최종 통합본의 새 GPU 추론, L40S 전체 실행 시간은 아직 검증하지 않았습니다.
compact 후보의 A100 개발 160건 실행은 적재 포함 약 989초였으며,
1,853건으로 단순 외삽하면 2시간을 넘습니다. 이는 L40S 실측이 아닙니다.
공식 제출은 아직 없고, 0.85 목표를 달성한 상태도 아닙니다.

루트의 `pps/`와 `model/config.json`은 기존 기본 구현을 보존합니다.
위 0.697220에 대응하는 최신 코드는 별도
[실험 스냅샷](experiments/v7_fact_compact_v2_quote/README.md)에 있습니다.
기본 설정이나 제출용 ZIP으로 자동 승격하지 않았습니다.

## 구성

- `pps/`: 문서 검색, 프롬프트, 추론, 판정 및 근거 검증
- `model/`: 실험 설정 JSON. 모델 가중치는 포함하지 않습니다.
- `tests/`: 기본 구현의 단위·계약 테스트
- `tools/`: 데이터 준비, 실행, 평가 및 제출 파일 생성 도구
- `experiments/v7_fact_compact_v2_quote/`: 최신 연구 후보와 독립 합성 테스트
- `notebooks/dacon_colab.ipynb`: 이전 기본 후보 비교용 노트북. 최신 후보의 실행기가 아닙니다.

## 로컬 사용

Python 3.12 환경에서 실행합니다. 다음 명령은 Linux 기준이며 Windows에서는
`.venv/bin/python` 대신 `.venv/Scripts/python.exe`를 사용합니다.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python script.py --help
.venv/bin/python -m pytest -q tests/test_artifacts.py tests/test_ingest.py
.venv/bin/python -m unittest discover -s experiments/v7_fact_compact_v2_quote/tests -p "test_*.py"
```

마지막 두 명령은 GPU나 대회 원본 데이터 없이 실행할 수 있습니다.
전체 `tests/` 중에는 공식 배포 법령·품목 목록 또는 로컬 토크나이저를 사용하는 검사도 있습니다.

대회 데이터는 [공식 배포 페이지](https://dacon.io/competitions/official/236754/data)에서
이용 조건을 확인한 뒤 별도로 준비합니다. `tools/prepare_data.py`는 공식 ZIP의
SHA256을 확인하고 `data_open/`에 풉니다. 원본·가공 데이터와 정답, 저장 응답은
이 저장소에 재배포하지 않으므로 개발 점수를 소스만으로 재현할 수는 없습니다.

GPU 추론용 의존성은 `requirements-gpu.lock`에 기록했습니다.
고정 평가 환경과 같은 라이브러리를 사용해야 하며, 설치 성공만으로 실행 가능 시간이나
메모리 적합성이 검증되는 것은 아닙니다. 노트북의 전체 실행은 유료 GPU를 사용할 수 있습니다.

## 실행 계약과 제출 파일

`python script.py`는 대회 환경의 `PPS_DATA_DIR/test.jsonl.gz`를 읽어
`PPS_OUTPUT_DIR/submission.csv`를 생성합니다. 모델 경로는 `PPS_MODEL_DIR`로 받으며,
제출 추론 중 외부 API나 데이터 다운로드를 사용하지 않습니다.
`--mock`은 입출력 검사 전용이고 점수 측정·제출용이 아닙니다.

```bash
.venv/bin/python tools/build_submission.py --output artifacts/baseline_unverified.zip
```

이 명령은 **루트 기본 구현**을 패키징합니다. 최신 실험 스냅샷이나 검증된 제출물이 아닙니다.
GitHub 공개와 DACON 제출은 별개이며, 자동 제출은 수행하지 않습니다.

## 공개 범위와 출처

이 저장소에는 소스 코드, 설정, 합성 테스트, 출력 없는 노트북, 집계 결과만 포함합니다.
원본·가공 데이터, 사례별 라벨·예측, 모델 가중치, 인증정보, 브라우저·에이전트 로그,
내부 작업 기록은 제외했습니다.

- [대회 규칙](https://dacon.io/competitions/official/236754/overview/rules)
- [평가 안내](https://dacon.io/competitions/official/236754/overview/evaluation)
- [공식 베이스라인](https://dacon.io/competitions/official/236754/codeshare/14154)
- 지정 모델: `google/gemma-4-26B-A4B-it`
- 모델 revision: `4d7ae4984b7db7de8f8457170b3f1a419ee76d52`

제출용 판단에는 대회 제공 자료를 사용합니다. 파인튜닝, LoRA, 추가 판정모델 학습은 하지 않습니다.
