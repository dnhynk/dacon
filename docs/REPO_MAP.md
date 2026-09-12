# 저장소 지도

## 수정하는 곳과 보존하는 곳

| 위치 | 역할 | 운영 원칙 |
| --- | --- | --- |
| `START_HERE.md`, `docs/STATE.json` | 시작점·현재 상태 | 라운드 종료 시 갱신 |
| `docs/LOCAL_HANDOFF.md` | 로컬 Run·소유권·이어갈 작업 | 비공개, 짧게 유지 |
| `script.py`, `submission/` | 유일한 실행·개선 본체 | 검증된 변경을 여기에 통합, 실제 측정 소스 고정 |
| `pps/`, `model/` | 이전 기본 구현 | 제출 실행·패키징에서는 사용하지 않음 |
| `experiments/*/source/`, `stages/*source*/` | 동결 비교 코드 | 원본 보존, 새 현행 구현으로 분기하지 않음 |
| `experiments/precision_joined_v1/` | 통합·새 추론·재현 진단 | 기존 Continue 작업 소유권 확인 |
| `experiments/precision_audit_fix_v2/` | 독립 정밀감사 | 기존 감사 세션과 조율 |
| `tools/`, `tests/` | 공용 보조 도구·현행 모듈 합성 검사 | 실행 도구는 `tools/README.md` 참고; legacy CLI는 실행 차단 |
| `runs/` | 과거 실행·원문 검토·감독 증거 | 비공개. 경로·해시 보존 |
| `research/` | 이전 분석·사람 검토 자료 | 비공개 역사 자료. 현재 지시 아님 |
| `artifacts/` | 로컬 데이터 준비물·결과·보관함 | 비공개. 정답/검증셋 임의 열람 금지 |
| `data_archive/`, `data_open/`, `models/` | 제공 데이터·모델 | 재배포·불필요한 재다운로드 금지 |
| `notebooks/` | 단일 제출 본체 실행 노트북 | 준비된 런타임에서 동일 코드 실행, 비용 발생 |

원본 결과와 동결 소스는 많은 검증 영수증이 상대경로와 SHA로 참조한다.
정리는 이들을 폴더명만 보고 합치거나 삭제하는 작업이 아니다. 진행 안내를 분리하고,
기존 증거 경로를 유지하면서 역할별 목록을 제공한다.

## B4 연결 관계

```text
현재 공고 + 제공 지식
  → script.py → submission.main
      → submission.original_a: 원래 A 입력 생성 → A1 / A10 / A19
      → submission.v20_legacy: 원래 L19 입력 생성
      → 고정 Gemma의 새 응답
      → submission.pps: 통합 CPU 소비기
      → A 결과에 L19의 v20/e20만 반영
```

루트 실행기·빌드 도구·노트북은 위의 같은 연결을 사용한다. 과거 실험의
`frozen/script.py`는 현행 진입점이 아니다.
과거 `.751807`은 두 과거 엔진의 저장 응답을 결합한 기록이다. 새 B4 실행의
엔진 공유·호출 순서는 별도 실험 변수이며, 입력 메시지가 같다는 이유로 무시하지 않는다.

## 자료 찾기

먼저 `docs/STATE.json`의 정확한 경로를 사용한다. 없으면 좁은 폴더에서 검색한다.

```text
rg -n -uu "찾을 표현" experiments/precision_joined_v1/reproduction_751_20260912
```

공개 소스만 있는 복제본에는 원문·라벨·응답·실행 패킷이 없다.
소스의 합성 검사 가능 여부와 개발 점수의 재현 가능 여부를 구분한다.
