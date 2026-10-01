# DACON 236754 · 나라장터 공고 법령 위반 탐지

나라장터 입찰공고마다 24개 항목의 위반 여부를 판정한 대회 제출 런타임(파이프라인 C)입니다.
대회는 2026-09-29에 끝났고, 이 저장소는 보관용입니다. 공식 최고점은 BIGSHOT0928_v6의 Macro F1 0.7535786066이며,
비공개 상위 15팀에는 들지 못했습니다. 공식 제출 원장과 스위치 정의는 [docs/STATE.json](docs/STATE.json)에 있습니다.

## 구조

| 경로 | 내용 |
| --- | --- |
| `script.py` → `submission/` | 제출 런타임(`submission/pps_c/`). 탐침 패키지는 `pps_c/switches.py`의 값만 다릅니다 |
| `tools/build_submission.py` | 제출 ZIP 빌드. 스위치를 지정하고, 풀어 낸 ZIP으로 모의 실행합니다 |
| `tools/project_status.py` | STATE 요약. `--ledger`는 모든 공식 제출을 보여 줍니다 |
| `tests/` | 런타임 합성 검사 |
| [docs/DEVELOPMENT_NARRATIVE.md](docs/DEVELOPMENT_NARRATIVE.md) | 9월 초~9/22의 흐름: 무엇이 틀렸고 무엇이 정해졌나 |
| [docs/REBUILD_BASIS.md](docs/REBUILD_BASIS.md) | 평가셋·라벨·모델에 관해 확인된 사실과 무효가 된 결론 |

두 문서가 인용하는 `runs/`·`experiments/` 경로는 로컬 증거였고 지금은 없습니다. 이전 트랙(track A/B 런타임,
동결 실험 소스, 옛 도구)은 `archive/pre-cleanup-20260926` 태그에 있습니다:
`git checkout archive/pre-cleanup-20260926 -- <경로>`.

## 파이프라인 C

```text
공고(JSONL: 문서들 + 나라장터 meta)
  → script.py → submission.main → pps_c.main.run
      → pps_c.facts.build: 줄·절 구분, meta 해석, 항목군별 후보 줄 (CPU)
      → 항목군 판독 요청 (pps_c.families: 문법 제한 JSON, 정해진 선택지만 고른다)
           첫 패스 inst → size·dp·region·perf·pledge·brief·sw·model
           스위치에 따라 v9 2단 판독(v9obj)과 v24 전용 판독(pps_c/v24)
           마감이 예측되면 남은 요청은 CPU 기본값으로 둔다
      → pps_c.facts.apply_model: 판독이 CPU 기본값을 덮어쓴다('불명'은 기본값 유지)
      → pps_c.judge.judge: 항목표 v1..v24 CPU 판정
      → pps_c.csvout: submission.csv
```

모델(`google/gemma-4-26B-A4B-it`, vLLM 0.26 오프라인, `pps_c.runner`)은 사실만 읽고, 위반 판정은 `judge.py`의
항목별 규칙이 합니다. 사고 모드는 `switches.THINKING_FAMILIES`의 항목군에만 켭니다.

## 실행

```text
python -B -m pytest -q tests
python script.py --help
python tools/build_submission.py <이름> [NAME=VALUE ...]
```

Python 3.12. Windows에서는 `.venv/Scripts/python.exe`, Linux에서는 `.venv/bin/python`을 씁니다.
개발 의존성은 `requirements-dev.txt`, GPU 환경 버전은 `requirements-gpu.lock`에 있습니다.
빌드에는 제공 데이터(`data_open/data`)와 그 사본 `submission/pps_c/assets/`가 필요합니다([tools/README.md](tools/README.md)).

## 로컬 전용 보존물

git에 없고 이 PC에만 있습니다.

| 경로 | 내용 |
| --- | --- |
| `artifacts/rebuild_c/BIGSHOT0928_v6_clean/submit.zip` | 공식 최고 0.7535786066 제출본 |
| `artifacts/rebuild_c/BIGSHOT0929_v8o_clean/submit.zip` | 최종 main 제출본(스펙 `runs/rebuild_c/bigshot0928/spec_v8o.json`) |
| `runs/rebuild_c/DESIGN_C.md`, `v24_pipeline/DESIGN_V24.md` | 파이프라인 C와 v24 단계의 설계 계약 |
| `runs/rebuild_c/transfer_20260925/audit/`, `code_audit_20260926/REPORT.md` | 항목별 판독·코드 감사 보고서 |
| `runs/rebuild_c/submissions/official_submission_*.json` | 공식 제출 영수증 |
| `runs/rebuild_c/colab/` | Colab GPU 실행 키트 |
| `data_open/`, `docs/ORGANIZER_NOTICES.md`, `submission/pps_c/assets/` | 제공 데이터와 주최 공지 원문(재배포 금지) |

## 공개 범위

소스·설정·합성 테스트·집계 결과만 공개합니다. 제공 원문, 가공 데이터, 라벨, 저장 응답, 공고별 예측, 가중치,
인증정보는 포함하지 않으므로 공개 소스만으로 개발 점수를 재계산할 수 없습니다.
