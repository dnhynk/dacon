# 빈 컨텍스트에서 시작하기

이 저장소는 DACON 236754의 나라장터 공고 24항목 판정 시스템이다.
목표는 공식 Macro F1 0.85이며, 아직 공식 제출 점수는 없다.

## 먼저 구분할 기록

| 기록 | 의미 | 현재 판단 |
| --- | --- | --- |
| **0.758950** | 과거 B4 + 고정 G/WS v9 응답의 개발160 CPU 재판정 | 측정한 최고 CPU 후보. 보존·비교 대상; 동등품 해석의 미해결 문제와 새 전체 추론 미검증 |
| **0.751807** | 과거 실제 응답 640개 + 동결 CPU 보정, 개발160 | 소스·원본·예측 보존. 저장 응답으로 재현 가능 |
| **0.740544** | 현행 단일 ZIP의 새640응답, 개발160 | 최신 새 추론. 이전 .706466 대비 +.034078, 최고점 완전 재현은 미완 |
| **0.706466** | 통합 전 A/L 입력·CPU 경로의 새 응답640, 개발160 | 이전 새 추론 기준으로 보존 |
| **0.718474** | 이전 4회 호출 결합의 저장 응답 재판정 | 이전 비교 기준으로 보존 |

현재 수치·경로·검증 상태의 기준은 [STATE.json](docs/STATE.json) 하나다.
더 높은 과거 CPU 점수를 새 추론이나 공식 점수로 표현하지 않는다.
현행 실행의 A100 처리량으로1853건을 단순 환산하면 약4.13시간이다.
이는 L40S 실측은 아니지만 **제출2시간 충족을 아직 주장할 수 없는 위험**이다.

## 3분 시작 순서

1. `git status --short`로 다른 작업을 확인한다.
2. `python -B tools/project_status.py`로 점수·현재 과제·실행 진입점을 읽는다.
3. 로컬에 있는 `docs/LOCAL_HANDOFF.md`에서 기존 Run,
   파일 소유권, GPU의 마지막 관측과 다음 작업을 확인한다. 공개 저장소에는 없는 파일이다.
4. 자기 작업에 필요한 실험 보고서만 읽고 이어간다. `runs/`와 `research/` 전체를
   다시 조사하거나, 끝난 환경 설정·제품 진단·A/T 비교를 재개하지 않는다.

Windows Python은 `.venv/Scripts/python.exe`, Linux에서는 `.venv/bin/python`을 쓴다.
로컬 보존물의 무결성을 확인하려면 `python -B tools/project_status.py --verify`를 실행한다.
이 검사는 해시·기록 확인이지 새 추론이나 새 채점이 아니다. 공개 복제본에서는
비공개 원본이 없다는 오류가 정상이며, 없는 데이터를 임의로 만들지 않는다.

보존된 **640응답을 실제 CPU 판정기에 다시 넣는** 별도 명령:

```text
python -B tools/replay_preserved_reference.py --output artifacts/reference_replays/my_new_check
```

2026-09-12 이 명령으로 .751807/FP53/FN24와 CSV 바이트 일치를 다시 확인했다.
새 빈 출력 경로만 허용한다. 비공개 개발 자료가 필요하며 새 모델 호출은0회다.
이 명령을 제출 추론이나 .751의 새 GPU 재현으로 사용하지 않는다.

## 어느 코드를 실행할까?

실행·개선·패키징하는 본체는 하나다:

```text
python script.py --help
python tools/build_submission.py --output artifacts/submission.zip
```

루트 `script.py`는 `submission.main.main()`을 부른다. 원래 A 입력, 최고 기록의
CPU 보정, L19의 v20/e20을 같은 `submission/` 패키지에 통합한다. 검증된 개선은
이 패키지에 계속 합치며 새 현행 source 브랜치를 만들지 않는다. ZIP과 노트북도
이 본체를 사용한다. **현재 통합본의 CPU/GPU 검증 상태는 STATE.json을 확인한다.**

루트 `pps/`, `model/`은 이전 구현이며 제출에서 읽지 않는다. 과거 동결
`experiments/*/source/`와 응답은 증거·롤백용으로 보존한다.
`precision_joined_v1/source/script.py`를 직접 실행해도 역사적 B4와 다른 A 입력이
생긴다. 점수 이름만 보고 실행 파일을 바꾸지 않는다. 새 추론 점수가 떨어지면
그 하락을 먼저 처리하고, 낮아진 값을 묵시적으로 새 기준으로 삼지 않는다.

## 이어서 읽을 문서

- [저장소 지도](docs/REPO_MAP.md): 코드·데이터·기록의 위치와 수정 경계.
- [실행 도구](tools/README.md): 현행 명령과 차단된 legacy 감사·실행 경로.
- [작업 원칙과 실행](docs/WORKFLOW.md): 연구→실측→결정, 원본 보존, GPU·공개 경계.
- [실험 목록](experiments/README.md): 후보별 역할과 종료 상태.
- [완료 실험 요약](docs/EXPERIMENT_HISTORY.md): 다시 시작하면 안 되는 과거 라운드.
- [0.751 재현 진단](docs/REPRODUCTION.md): 보존본 복원, 순서384 대조와 단일 ZIP 신규640의 회복·잔여 손실.

`STATUS.md`와 `HANDOFF.md`는 호환용 안내판만 남긴다. 옛 문서의 `CURRENT`,
`ACTIVE`, `NEXT`는 당시 기록이지 지금의 실행 지시가 아니다.
