# 실행 도구

현행 제출 구현은 `script.py` → `submission/` 하나다. 도구 이름이 같아도
과거 보고서의 실행 명령을 지금의 성능 검사로 사용하지 않는다.

| 작업 | 사용할 도구 |
| --- | --- |
| 현재 상태와 보존 해시 확인 | `project_status.py`, 선택적으로 `--verify` |
| 실제 공고의 새 추론 | 루트 `script.py` |
| 동결 입력·단일 엔진 전체 실행과 회수 가능한 체크포인트 | `run_frozen_canonical.py`, `checkpoint_native_run.py` |
| 동일 코드 ZIP / Colab 노트북 생성 | `build_submission.py` / `create_colab_notebook.py` |
| 완성된 예측 CSV 채점·세 기준과 비교 | `evaluate.py --labels ... --predictions ... --baseline ... --out 새파일` |
| 보존한 .751807의 CPU 재현 | `replay_preserved_reference.py --output 새폴더` |
| 현행 판정기로 저장 응답 재생 | `replay_canonical.py --source-run 보존폴더 --output 새폴더` |
| SW 관계의 원문 의미 검사 재생 | `replay_software_contract.py --diagnostic 보존폴더 --prior-replay 이전검사 --output 새폴더` |
| 모델의 적용조건 미확정 진술과 양성 비트 대조 | `audit_fact_consistency.py --run 보존응답 --replay 현행CPU재생 --output 새폴더` |
| 고정 원문 예산의 검색 비교 | `compare_notice_retrieval.py`, `compare_notice_sparse.py`, `compare_notice_colbert.py` |
| 검색 누락의 후보 순위·문맥 비용 분해 | `audit_retrieval_misses.py` |
| 질의 결합과 실제 원문 사용량을 맞춘 비교 | `compare_notice_query_fusion.py --budget-policy lexical_used` |
| 원문 묶음의 공동 선택·교환 비교 | `compare_notice_query_fusion.py --comparison evidence_selection --budget-policy hybrid_used` |
| 한 후보 제거 후 여러 후보로 다시 채우는 선택 비교 | `compare_notice_query_fusion.py --comparison evidence_refill --budget-policy hybrid_used` |
| 사람이 검토하지 않은 공고의 검색 실행 비용 측정 | `audit_notice_optimizer.py` |
| 동일 입력을 반복한 새 응답의 변동 검사 | `audit_repeated_retrieval.py` |
| 고정 벡터·예산에서 번호 구조와 상위 문맥 비교 | `compare_notice_headings.py` |
| 변경된 검색 입력의 새 추론 준비·사후 채점 | `prepare_retrieval_contrast.py`, `score_retrieval_contrast.py` |
| 기존 원문 선택을 고정한 고시 조건 소비 진단 준비 | `prepare_catalog_conditions.py --parent 보존준비폴더 --output 새폴더` |
| 같은 입력 대조군의 실행 사이 변동 확인 | `audit_fixed_control_drift.py --before 이전실행 --after 새실행 --output 새폴더` |
| 보존한 모든 검색 arm을 새 CPU 소비기로 재판정 | `replay_retrieval_consumer.py --run 보존실행 --original-audit 이전검증 ... --output 새폴더` |
| 이전 Colab 결과 묶음의 안전한 회수 | `ingest_results.py`; 기존 목적지는 덮어쓰지 않음 |

`evaluate.py`의 `--baseline`은 반복할 수 있다. 최고 CPU 후보, 최신 전체 새 추론,
원래 역사적 기준의 정확한 점수와 경로는 `docs/STATE.json`에 있다.
저장 응답의 재판정을 새 모델 호출 성능으로 보고하지 않는다.
`run_frozen_canonical.py`는 준비된 입력의 호출 대상과 결정론적 skip을 먼저 고정하고,
원응답을 파싱하기 전에 보존한다. 결과 점수는 회수·무결성 검증·CPU 조립을 거친 뒤에만
등록한다. 이미 종료한 Colab 주소나 과거 handoff의 PID를 실행 근거로 사용하지 않는다.
`prepare_catalog_conditions.py`는 검색을 다시 실행하지 않고 속성·대상·필수 여부의
원문 참조를 받는 선택적 입력을 만든다. 실제 토크나이저로 예산·좌표·스키마를
검사하고, 현재 코드가 읽을 수 있는 값에 낙관적인 대상 연결을 가정한 CPU 진단을
함께 남긴다. 이 가정은 모델 응답이나 정답이 아니다. 결과는 일부 필드 갱신 또는
보류이므로, 완전한 factored 출력을 요구하는 `score_retrieval_contrast.py`로 채점하면
안 된다. 새 추론과 별도로 동결한 기준 예측을 확인한 뒤 보류 필드를 유지하는
결합·채점 검증이 필요하다. 기본 제출 호출에는 자동 추가되지 않는다.
`replay_software_contract.py`는 동결된 최종 응답 전체를 유지하며, 형식 오류를
0으로 채우지 않는다. 새 의미 검사의 보류 사유와 최종 비트 변화를 따로 기록한다.
`audit_fact_consistency.py`는 현재 소스로 동결한 재생 전체에서 모델 스스로 필수
적용조건을 미확정이라고 적은 양성을 찾는다. 명시한 보류 정책의 가정 CSV를 먼저
고정하며 라벨로 후보를 선택하지 않는다. 판정기의 같은 검사는 독립된 원문 분류·SW
과업을 우선한다. 원문 상태가 unknown이라는 이유만으로 적용하지 않으며, 보류 출력0과
법적 정상은 구별한다. 가정 비교·저장 응답 재생을 새 추론 점수로 표시하지 않는다.
SW 관계 검사는 행위별 원문 위치·주체·대상·양태의 검사 내역도 남긴다. 문서 작성,
실적·교육 표현, 다른 주체의 행위를 실제 SW 과업으로 확정하지 않는다. 이 검사를
통과했다는 것만으로 의미가 인증되는 것은 아니며, 별도의 유효한 행위 근거는 유지한다.
품목명만 있는 인용에서 제공 행위를 확인하지 못했다는 것은 그 품목의 공급 의무가
없다는 뜻이 아니다. 상위 목록·공급 조건이 포함된 추가 근거가 필요하다.
`ingest_results.py`의 자동 검색은 이전 development/holdout 폴더 형식용이다.
holdout 라벨은 기본적으로 열지 않으며, 해당 범위가 승인된 경우에만
`--include-holdout`으로 명시적으로 활성화한다.
현행 실행의 `submission.csv`(회수 후 `final_B4.csv`)는 `evaluate.py`에
명시적으로 전달한다.

검색 비교는 관련 인용 한 개와 검토 근거 묶음 전체를 따로 센다. 상위 제목·표·예외도
같은 원문 토큰 상한에 포함한다. 문서 확인 범위는 부재 증명이나 최종 F1과 다르다.
`compare_notice_colbert.py`는 `--baseline`, `--context-control`, `--input`, `--output`을
명시하고 고정 BGE-M3의 CPU 토큰 벡터를 사용한다. `NoticeSearch`의 `colbert` 및
`hybrid_colbert`는 선택 옵션이며 기본 제출 설정에 추가 호출을 만들지 않는다.
검색 효과가 검증된 후보의 최종 판정 효과에는 고정 Gemma·후처리로 새 추론이 필요하다.
동일 상한에서도 실제 사용량이 다르면 더 많은 원문을 붙인 효과가 섞일 수 있다.
`compare_notice_query_fusion.py`는 `--baseline`, `--control`, `--inputs`, `--output`을
받는다. `--inputs`는 동결된 `current_inputs.jsonl.gz`이며 문서·청크 해시를 대조한다.
`lexical_used`는 공고별 보존 키워드 검색의 실제 사용량을 공통 상한으로 사용하고,
기존 키워드 선택이 재현되는지 확인한다. 구간 단위 선택의 잔여 예산도 실제 사용량에
드러낸다. `query_aggregation='best'`는 선택 실험이며 기본 평균 결합은 유지한다.
인간 인용을 이용한 최소 문맥 비용은 실패 원인 분석용 수치로만 보고한다.
`evidence_cover`는 온전히 반환한 원문 줄에 대해 겹친 청크의 점수를 중복 합산하지
않고, 같은 질문의 추가 근거에도 점차 줄어드는 가치를 부여한다. 병합된 문맥의
실제 토큰 비용으로 선택하고 한 차례 교환을 검토한다. 선택·교환 내역과 잔여
후보의 비용을 기록하며, 최적해·법적 확률·부재 확인을 주장하지 않는다. 문맥
확장과 함께 사용하는 선택 옵션이다. 기본 RRF는 그대로다.
`hybrid_used`는 기존 혼합 검색의 실제 사용량을 공통 상한으로 고정한다. 이는
혼합 검색 안에서 선택기의 효과를 확인하는 대조군이며, 키워드와 BGE의 비교에는
여전히 `lexical_used` 결과도 함께 제시한다.
`NoticeSearch`의 `ancestors`는 공백 없는 절 번호와 번호가 붙은 본문 문장도 구조
경계로 사용한다. 소수 수치·단위·날짜는 절 번호에서 구별하며, 예전 입력의 대조군은
`heading_context='legacy_ancestors'`로 재현한다. 원문의 번호 연결 자체가 해당 문장의
법적 적용 범위를 증명하는 것은 아니다. 원문을 추가한 비용과 근거 회수도 따로 측정한다.
새 추론 준비는 `--case-manifest`, `--arms`, `--exclude-identical-source`,
`--batch-size`로 이미 측정한 범위를 명시할 수 있다. 동일 원문 사례를 제외한 이유와
공고별 상한을 동결한다. `score_retrieval_contrast.py`는 원응답·실제 샘플러·CPU 판정의
일치를 확인하고, 겹치지 않는 사전 지정 항목만 동일 저장 기준에 결합한다. 형식
오류가 남은 군은 점수를 보류한다. 이 혼합 응답 점수는 전체 새 추론 성능이 아니다.
`audit_fixed_control_drift.py`는 실제 입력 토큰·샘플러가 같은 대조군만 비교하고,
하드웨어·캐시·동반 요청 차이를 보존한다. 다른 실행의 변화 원인을 무작위성 하나로 단정하지 않는다.
`replay_retrieval_consumer.py`는 `--baseline-run`, `--baseline-replay`, `--labels`와 반복 가능한
`--baseline`도 받는다. 원래 입력과 응답, 모든 arm·반복을 유지하고 소비기만 바꾼다.
모든 예측을 먼저 동결하며, 결과는 저장 응답 재판정으로만 해석한다.

`audit_money_observations.py --input 동결입력.jsonl.gz --output 새폴더 --baseline 이전관측폴더`는
정답·모델 없이 원문 금액, 범위, 세금 기준과 적용 금액을 동결한다. 관측 복구·출처 변화와
실제 금액 변화를 따로 세며, 이 감사 자체는 최종 F1 검증이 아니다.

`audit_catalog_condition_coverage.py --input 동결입력.jsonl.gz --output 새폴더`는 제공 고시의
조건 처리 범위와 현재 공고의 미확정 조건을 기록한다. 후보 유사도는 조건 충족이 아니다.
조건식 미처리와 해석했지만 사실이 부족한 활성 조건을 별도로 기록한다. `unknown`을
`not_evaluated`에서 분리한 행 수를 실제 조건 충족·전체 구매 판정의 개선 수로 세지 않는다.
`audit_table_structure.py --input 동결입력.jsonl.gz --output 새폴더`는 원문 위치를 유지한
표 구조 후보, 미처리 범위, 합성 표의 올바른 확정·잘못된 연결·구간 비교 가능률을 구분한다.
누락 숫자를 계산으로 채우거나 구간 비교 가능률을 최종 판정 가능률로 해석하지 않는다.
`compare_table_queries.py --help`는 구조 후보 질의와 기존 단위 질의를 동일 원문 예산에서
lexical/BGE 품목 검색과 교차 비교하는 CLI를 안내한다. 이 비교에는 새 Gemma 응답이 없다.
`audit_catalog_queries.py --input 동결입력.jsonl.gz --output 새폴더 --baseline 이전감사폴더`는
같은 seed 원문의 질의 후보 풀·32개 선택 결과를 동결한다. 복구된 질의와 상한 때문에 밀린
질의를 따로 센다. 정답·임베딩·Gemma 없이 실행하며 이 수치는 근거 확보율이나 F1이 아니다.
`audit_region_tokens.py --input 동결입력.jsonl.gz --output 새폴더 --baseline 이전감사폴더`는
실제 원문의 지역 규칙과 익명화 속성·기호 변환 검사를 구분해 보존한다. 변환 입력은 합성
진단이며 공고 원본·라벨·예측을 수정하지 않는다. 새 추론이나 미공개 데이터 검증이 아니다.

`checkpoint_native_run.py --run 실행폴더 --output 새파일.zip`은 실행 중 완료된 배치의
요청·샘플러·원응답·투영 응답을 누적 보존한다. 미완료 native는 별도로 표시하며 전체
공고 완료나 점수 검증으로 취급하지 않는다. 원격 ZIP만 남겨 두면 런타임 소실에 대비할 수 없다.
Colab 진행 셀은 `native_checkpoint_status.recover_progress`로 첫 배치와 고정 간격에
Chrome 다운로드를 요청한다. 다운로드 요청과 실제 로컬 회수 확인은 다른 상태다.
`recover_audit_archive.py`에 화면에서 읽은 SHA256/바이트를 넣어 로컬 파일과 CRC를
확인하고, 정상 실행의 최종 검증은 기존 `verify_runtime_recovery.py`로 수행한다.
이 도구들은 모델을 재실행하거나 유효 응답을 바꿔 고르지 않는다.

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
