# 빈 컨텍스트에서 시작하기

이 저장소는 DACON 236754의 나라장터 공고 24항목 판정 시스템이다.
목표는 공식 Macro F1 0.8(중간 목표 0.75)이다. 공식 제출 점수와 대기 중인 제출은 STATE.json에 있다.
지난 몇 주의 개발 경과, 방향 재설정의 이유, 정해진 방향과 남은 가설은
[DEVELOPMENT_NARRATIVE.md](docs/DEVELOPMENT_NARRATIVE.md)에 있다. 작업을 고르기 전에 먼저 읽는다.

## 먼저 구분할 기록

현재 점수·소스·실행 상태는 [STATE.json](docs/STATE.json)과
`python -B tools/project_status.py`에서 확인한다. 이 안내문에 최신 점수를
복제하지 않는다. 전체 기록이 필요할 때만 `--history`를 사용한다.

저장 응답의 CPU 재판정, 일부 새 응답을 결합한 진단, 전체 새 추론,
공식 제출 점수는 서로 다른 측정이다. 높은 CPU 점수를 새 추론 점수로
표현하거나, 실패한 형식 응답을 제외하고 전체 비교가 끝났다고 하지 않는다.
평가 시간은 모델 적재·초기화도 포함한다. A100 외삽과 L40S 실측도 구분한다.

## 3분 시작 순서

1. `git status --short`로 다른 작업을 확인한다.
2. `python -B tools/project_status.py`로 점수·현재 과제·실행 진입점을 읽는다.
3. 로컬에 있는 `docs/LOCAL_HANDOFF.md`에서 기존 Run,
   파일 소유권, GPU의 마지막 관측과 다음 작업을 확인한다. 공개 저장소에는 없는 파일이다.
   `active_task.status`가 완료이고 handoff에 활성 자원이 없으면 과거 보고서의
   `ACTIVE`나 Colab 주소를 따라 실행을 재개하지 않는다.
4. 자기 작업에 필요한 실험 보고서만 읽고 이어간다. `runs/`와 `research/` 전체를
   다시 조사하거나, 끝난 환경 설정·제품 진단·A/T 비교를 재개하지 않는다.

Windows Python은 `.venv/Scripts/python.exe`, Linux에서는 `.venv/bin/python`을 쓴다.
로컬 보존물의 무결성을 확인하려면 `python -B tools/project_status.py --verify`를 실행한다.
이 검사는 해시·기록 확인이지 새 추론이나 새 채점이 아니다. 공개 복제본에서는
비공개 원본이 없다는 오류가 정상이며, 없는 데이터를 임의로 만들지 않는다.

## 어느 코드를 실행할까?

실행·개선·패키징하는 본체는 하나다:

```text
python script.py --help
python tools/build_submission.py <이름>
```

루트 `script.py`는 `submission.main.main()`을 부르고, 본체는 `submission/pps_c/`(파이프라인 C,
계약 [DESIGN_C.md](runs/rebuild_c/DESIGN_C.md))다. 빌드 도구는 `artifacts/rebuild_c/<이름>/submit.zip`에
`submission/script.py`·`requirements.txt`·`pps_c/`를 담고, 풀어 낸 ZIP에서 mock 실행으로 확인한다.
Colab GPU 키트(`runs/rebuild_c/colab/`)도 이 본체를 담는다. 검증된 개선은 이 패키지에 계속 합치며
새 현행 source 브랜치를 만들지 않는다. **현재 본체의 CPU/GPU 검증 상태는 STATE.json을 확인한다.**

이전 트랙(track A/B 런타임, 동결 실험 소스, 옛 주석·재현 도구)은 현행 트리에 없고 `archive/pre-cleanup-20260926`
태그에 있다(`git checkout archive/pre-cleanup-20260926 -- <경로>`). 로컬 `runs/`·`experiments/`의 보존물(응답·예측·
영수증)은 증거로 그대로 둔다. 새 추론 점수가 떨어지면 그 하락을 먼저 처리하고, 낮아진 값을 묵시적으로 새 기준으로
삼지 않는다.

## 이어서 읽을 문서

- [저장소 지도](docs/REPO_MAP.md): 코드·데이터·기록의 위치와 수정 경계.
- [실행 도구](tools/README.md): 빌드와 상태 도구.
- [작업 원칙과 실행](docs/WORKFLOW.md): 연구→실측→결정, 원본 보존, GPU·공개 경계.

`STATUS.md`와 `HANDOFF.md`는 호환용 안내판만 남긴다. 옛 문서의 `CURRENT`,
`ACTIVE`, `NEXT`는 당시 기록이지 지금의 실행 지시가 아니다.
