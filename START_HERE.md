# 빈 컨텍스트에서 시작하기

DACON 236754: 나라장터 입찰공고마다 24개 항목의 위반 여부를 판정한다. 목표는 공식 Macro F1 0.8이다.
본체는 파이프라인 C다. 지정 Gemma가 항목군별 후보 줄에서 정해진 사실을 읽고, CPU 규칙이 항목을 판정한다.
방향이 정해진 이유와 남은 가설은 [개발 경과](docs/DEVELOPMENT_NARRATIVE.md)에 있다.

## 시작 순서

1. `git status --short`로 다른 세션의 작업을 확인한다.
2. `python -B tools/project_status.py`로 공식 최고점, 점수를 기다리는 제출, 다음 행동을 본다.
   `--ledger`는 모든 공식 제출을 보여 준다. 근거는 [STATE.json](docs/STATE.json)이다.
3. 로컬 `docs/LOCAL_HANDOFF.md`에서 실행 중인 세션, GPU 소유, 이어갈 작업을 확인한다. 공개 저장소에는 없다.
4. 자기 작업에 필요한 보고서만 읽는다. 과거 보고서의 `ACTIVE`, Colab 주소, 타이머를 따라 작업을 재개하지 않는다.

Windows에서는 `.venv/Scripts/python.exe`, Linux에서는 `.venv/bin/python`을 쓴다.
`--verify`는 로컬 보존물의 해시를 확인한다. 새 추론이나 채점은 아니다. 공개 복제본에는 해시 목록이 없어 실패한다.

## 측정의 종류

공식 LB 점수, 새 추론, 저장 판독의 CPU 재판정, 겹치지 않는 Δ를 더한 조건부 추정은 서로 다른 측정이다.
하나를 다른 것으로 대신하거나 추정을 공식 점수로 적지 않는다. 평가 시간에는 모델 적재도 포함된다.

## 코드

```text
python script.py --help
python tools/build_submission.py <이름> [NAME=VALUE ...]
```

루트 `script.py`는 `submission.main.main()`을 부르고, 본체는 `submission/pps_c/`다. 설계 계약은 로컬
`runs/rebuild_c/DESIGN_C.md`에 있다. 기본 스위치는 LB로 잰 기준(P3a)이다. 탐침 패키지는 `NAME=VALUE`로
`switches.py`의 값만 바꾼다. 검증된 개선은 이 본체에 합치고, 새 현행 소스 브랜치를 만들지 않는다.

이전 트랙(track A/B 런타임, 동결 실험 소스, 옛 도구)은 `archive/pre-cleanup-20260926` 태그에 있다.
로컬 `runs/`·`experiments/`의 보존물은 증거로 그대로 둔다.

## 이어서 읽을 문서

- [저장소 지도](docs/REPO_MAP.md): 코드·데이터·기록의 위치와 수정 경계.
- [작업 원칙](docs/WORKFLOW.md): 사전등록과 결정, 재현, GPU·제출·공개 경계, 인계.
- [도구](tools/README.md): 빌드와 상태 도구.
