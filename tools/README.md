# 실행 도구

현행 제출 구현은 `script.py` → `submission/` 하나다. 도구 이름이 같아도
과거 보고서의 실행 명령을 지금의 성능 검사로 사용하지 않는다.

| 작업 | 사용할 도구 |
| --- | --- |
| 현재 상태와 보존 해시 확인 | `project_status.py`, 선택적으로 `--verify` |
| 실제 공고의 새 추론 | 루트 `script.py` |
| 동일 코드 ZIP / Colab 노트북 생성 | `build_submission.py` / `create_colab_notebook.py` |
| 완성된 예측 CSV 채점·세 기준과 비교 | `evaluate.py --labels ... --predictions ... --baseline ... --out 새파일` |
| 보존한 .751807의 CPU 재현 | `replay_preserved_reference.py --output 새폴더` |
| 이전 Colab 결과 묶음의 안전한 회수 | `ingest_results.py`; 기존 목적지는 덮어쓰지 않음 |

`evaluate.py`의 `--baseline`은 반복할 수 있다. 최고 CPU 후보 .758950,
보존 B4 .751807, 이전 새 추론 .706466의 정확한 경로는 `docs/STATE.json`에 있다.
저장 응답의 재판정을 새 모델 호출 성능으로 보고하지 않는다.
`ingest_results.py`의 자동 검색은 이전 development/holdout 폴더 형식용이다.
holdout 라벨은 기본적으로 열지 않으며, 해당 범위가 승인된 경우에만
`--include-holdout`으로 명시적으로 활성화한다.
현행 실행의 `submission.csv`(회수 후 `final_B4.csv`)는 `evaluate.py`에
명시적으로 전달한다.

## 종료한 옛 실행 경로

`run_experiments.py`, `audit_overlay.py`, `audit_output_contract.py`,
`audit_retrieval.py`, `evaluate_cache_pair.py`, `replay_saved_outputs.py`는
루트의 옛 `pps/`와 과거 데이터 형식에 묶여 있다. 특히 `audit_overlay.py`는
현행 전체 보정 사슬을 평가하지 않는다. 이 여섯 CLI는 실행을 거부하고
안내만 출력한다. 원함수는 역사 추적용으로 그대로 남겼다.

`create_experiment_cell.py`의 분기별 노트북 생성도 종료했다. 새 GPU 실행은
항상 현재 canonical ZIP/노트북을 사용한다. 과거 동결 스크립트를 복사하여
새 현행 실행 경로로 만들지 않는다.

자료 준비·다운로드·환경 검사 도구는 개발 지원용이며 성능 실험이 아니다.
기존 데이터와 준비된 런타임을 먼저 재사용하고, 봉인된 확인셋을 자동으로
열거나 외부 자원을 새로 할당하지 않는다.
