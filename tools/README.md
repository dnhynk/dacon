# 도구

| 도구 | 용도 |
| --- | --- |
| `build_submission.py <이름> [NAME=VALUE ...]` | `submission/`으로 제출 ZIP을 만든다 |
| `project_status.py [--json] [--ledger]` | `docs/STATE.json`의 공식 최고점과 제출 원장 |

`build_submission.py`는 `artifacts/rebuild_c/<이름>/submit.zip`을 쓰고 SHA256을 출력한다. 같은 이름의 ZIP이 있으면 거부한다.
`NAME=VALUE`는 `submission/pps_c/switches.py`에서 정확히 한 번 나오는 대입만 바꾸므로, 탐침 패키지는 기준 패키지와
`switches.py`만 다르다. 빌드 뒤 ZIP을 풀어 제공 표본 10건(로컬 `data_open/data`)에 `script.py --mode mock`을 실행해
구성과 import를 확인한다. 이 확인은 새 추론 점수나 L40S 시간 검증이 아니다.
`submission/pps_c/assets/`의 `catalog.csv`(제공 `법령패키지/중기부고시/중기부고시_경쟁제품_세부품명.csv`)와
`items.json`(제공 `항목표.json`)은 제공 자료 사본이라 git에 없다. 새 복제본에서는 빌드 전에 `data_open/data`에서
같은 이름으로 복사한다. 런타임은 데이터 폴더에 이 고시 파일이 없을 때 `catalog.csv`를 쓰므로, 빌드는 이 파일이
없으면 거부한다.

`project_status.py`는 `docs/STATE.json`을 요약한다. `--ledger`는 모든 공식 제출을 보여 준다. `--verify`가 대조하던
보존물 해시 목록(`runs/evidence_manifest.json`)은 보존물과 함께 지웠으므로 `--verify`는 항상 실패한다.

이전 트랙의 실행·재생·주석 도구는 태그 `archive/pre-cleanup-20260926`의 `tools/`와 `legacy/tools/`에 있다.
