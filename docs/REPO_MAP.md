# 저장소 지도

## 수정하는 곳과 보존하는 곳

| 위치 | 역할 | 운영 원칙 |
| --- | --- | --- |
| `START_HERE.md`, `docs/STATE.json` | 시작점·현재 상태 | 라운드 종료 시 갱신 |
| `docs/LOCAL_HANDOFF.md` | 로컬 Run·소유권·이어갈 작업 | 비공개, 짧게 유지 |
| `script.py`, `submission/` | 유일한 실행·개선 본체(`submission/pps_c/`) | 검증된 변경을 여기에 통합. 탐침 패키지는 `pps_c/switches.py`만 다르고, 기본값은 LB로 잰 기준 동작이다 |
| `tools/`, `tests/` | 빌드·상태 도구, 런타임 합성 검사 | `tools/README.md` 참고 |
| `runs/rebuild_c/` | 파이프라인 C의 설계·GPU 실행·감사·제출 스케줄러·영수증 | 비공개. 경로·해시 보존 |
| 그 밖의 `runs/`, `research/`, 로컬 `experiments/` | 이전 트랙의 실행·검토·동결 증거 | 비공개 역사 자료. 현재 지시 아님 |
| `artifacts/` | 빌드한 제출 ZIP·로컬 결과 | 비공개. 빌드 도구는 기존 ZIP을 덮어쓰지 않는다 |
| `data_archive/`, `data_open/`, `models/` | 제공 데이터·모델 | 재배포·불필요한 재다운로드 금지 |

이전 트랙의 코드(track A/B 런타임, 동결 실험 소스, 옛 주석·재현 도구와 그 테스트)는
`archive/pre-cleanup-20260926` 태그에 있다. 로컬 증거의 상대경로·SHA는 그 코드를 기준으로 기록되어 있으므로
증거 폴더를 옮기거나 합치지 않는다.

## 파이프라인 C 연결 관계

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

모델 호출은 `pps_c.runner`(vLLM 0.26 오프라인)가 맡는다. 사고 모드는 `switches.THINKING_FAMILIES`의 항목군에만 켠다.
모델은 위반 여부를 판단하지 않고 사실만 읽으며, 위반 판정은 `judge.py`의 항목별 규칙이 한다.

## 자료 찾기

먼저 `docs/STATE.json`과 로컬 `docs/LOCAL_HANDOFF.md`의 경로를 쓴다. 파이프라인 C의 계약은 로컬
`runs/rebuild_c/DESIGN_C.md`, 제출 사전등록은 로컬 `runs/harness_improve_20260919/precision_analysis/PREREGISTRATION.md`,
저장 판독의 CPU 재생은 `runs/rebuild_c/transfer_20260925/replay_switch.py`에 있다. 그 밖은 좁은 폴더에서
`rg -n -uu`로 찾는다.

공개 소스만 있는 복제본에는 원문·라벨·응답·실행 패킷이 없다.
소스의 합성 검사 가능 여부와 개발 점수의 재현 가능 여부를 구분한다.
