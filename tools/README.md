# 도구

| 도구 | 용도 |
| --- | --- |
| `build_submission.py <이름> [NAME=VALUE ...]` | `submission/`으로 제출 ZIP을 만든다 |
| `project_status.py [--verify] [--json] [--history]` | `docs/STATE.json`의 점수·현재 작업 요약 |

`build_submission.py`는 `artifacts/rebuild_c/<이름>/submit.zip`을 쓰고 SHA256을 출력한다. 같은 이름의 ZIP이 있으면 거부한다.
`NAME=VALUE`는 `submission/pps_c/switches.py`에서 정확히 한 번 나오는 대입만 바꾸므로, 탐침 패키지는 기준 패키지와
`switches.py`만 다르다. 빌드 뒤 ZIP을 풀어 제공 표본 10건(로컬 `data_open/data`)에 `script.py --mode mock`을 실행해
구성과 import를 확인한다. 이 확인은 새 추론 점수나 L40S 시간 검증이 아니다.

`project_status.py`는 GPU를 잡거나 라벨을 읽지 않는다. `--verify`는 로컬 보존물(예측·원응답·동결 소스)의 해시와
기록 점수를 대조할 뿐 재판정이나 새 추론이 아니다.

이전 트랙의 실행·재생·주석 도구는 태그 `archive/pre-cleanup-20260926`의 `tools/`와 `legacy/tools/`에 있다.

## Colab MCP

루트 `.mcp.json`이 Claude Code 세션에 `colab-mcp`(googlecolab/colab-mcp, 커밋 고정)를
등록한다. `uvx`가 필요하다. 서버는 세션마다 따로 뜨고, `open_colab_browser_connection`을
호출하면 새 Colab 탭(빈 scratch 노트북)을 열어 연결한 뒤 노트북 편집 도구를 추가한다.
서버에는 계정 옵션이 없으므로 `env.BROWSER`로 donghyun9282@gmail.com이 로그인된 Chrome
프로필(`Profile 1`)을 지정한다. 이 값이 없으면 OS 기본 브라우저(이 PC는 Edge)로 열린다.
프로필 폴더명은 PC마다 다르며 `Chrome/User Data/Local State`의 `info_cache`에서 확인한다.
도구가 보인다는 것은 연결 권한이 아니다. 호출 전에 `docs/LOCAL_HANDOFF.md`의 소유자를
확인하고, 다른 세션이 Chrome·Colab·GPU를 소유하면 호출하지 않는다(`docs/WORKFLOW.md`).
Codex는 `.mcp.json`을 읽지 않으며, 이 서버가 요구하는 `tools/list_changed` 지원은
Codex에서 검증하지 않았다. 업스트림 갱신은 `args`의 커밋 해시를 직접 올려 반영한다.
