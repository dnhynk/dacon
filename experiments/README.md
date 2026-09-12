# 실험·동결 소스 목록

현재 상태는 [STATE.json](../docs/STATE.json), 시작은 [START_HERE.md](../START_HERE.md).
현행 실행·개선은 [루트 script.py](../script.py)와 `submission/` 한 곳에서 한다.
여기는 비교·롤백 증거이며 별도의 현재 제출본 목록이 아니다.
이 폴더의 모든 `source`를 최신판으로 취급하거나 가장 큰 점수의 이름만 골라
실행하지 않는다. 각 점수는 입력 생성기·저장/신규 응답·CPU 소비기의 조합에 붙는다.

| 경로 | 역할 | 검증 범위 |
| --- | --- | --- |
| [B4 통합 원본](precision_joined_v1/b4_entry_candidate_20260912/README.md) | 현행 submission/으로 옮긴 A/L+CPU 연결의 보존 원본 | CPU 패킷/저장 응답 검증 기록; 현재 실행 파일 아님 |
| [precision_joined_v1/source](precision_joined_v1/source/) | 최고 .751807의 동결 CPU 소비 코드 | 과거640응답 재판정. 이 폴더 기본 입력은 다른 v6이므로 단독 실행 주의 |
| [precision_audit_fix_v1/source](precision_audit_fix_v1/source/) | 금액·범위·지역 CPU 보정 | 과거480응답 .746692 |
| [v20_uniform_route_v1/source](v20_uniform_route_v1/source/) | 이전 추가 v20 경로 | 과거640응답 .718474 |
| [r3 CPU](precision_audit_fix_v2/stages/cpu_candidate_source_r3/) | 역할·구성품·동일 필드 보완 | 과거480응답 .750246, 3회 경로 |
| [typed r3](precision_audit_fix_v2/stages/typed_candidate_source_r3_768/) | 구조화 사실·인용 계약 | A/T 실제 비교 완료, 전체 비채택 |
| [v7 compact 보존본](v7_fact_compact_v2_quote/README.md) | 이전 역할·적용조건 보정 | 과거480응답 .697220 |

새 전체 B4 고정 패킷 실행은 개발160·새640응답 **.706466**이었다.
동일 자료의 조건부 v20 CPU 비교 **.710923**은 별도 미채택 후보이며 새 GPU 점수가 아니다.
최고 과거 기록 **.751807**과 두 원본 응답 묶음은 그대로 보존한다.

끝난 A/C224, typed A/T192, v24 준비, v9 도구 루프의 결정은
[완료 실험 요약](../docs/EXPERIMENT_HISTORY.md)에 있다. 과거 문서에서 `NEXT` 또는
`approved`를 봤다고 새 실행을 시작하지 않는다.

로컬 연구 실행기·원본·라벨·응답·예측·검증 영수증은 대부분 git에서 제외된다.
실제 작업에는 `docs/LOCAL_HANDOFF.md`의 소유권과 정확한 원본 경로를 사용한다.
동결 파일을 덮어쓰거나, 기존 결과 디렉터리에 실행 결과를 덮어쓰지 않는다.
