# 완료 실험 요약 — 현재 지시가 아닌 역사

최신 상태는 [STATE.json](STATE.json), 시작은 [START_HERE.md](../START_HERE.md).
아래는 끝난 연구를 새 세션이 다시 준비하지 않도록 남기는 목록이다.
세부 원문·응답은 공개하지 않으며 로컬 원래 경로에서 보존한다.

| 연구 | 결과와 한계 | 다음 세션의 처리 |
| --- | --- | --- |
| 스트리밍 A100 개발200 검증(2026-09-19) | canonical 새 추론 F1 .862785/1,914.7초, 1853건 투영 실행 F1 .796967/tier4 164건. canonical 1853건 A100 추정 12,425~14,269초. GPU 패킷 새 불일치 0; 런타임 삭제 | [상세](../runs/stream_executor_20260919/gpu_verify_01/REPORT.md). 2시간·품질 동시 달성 및 L40S 미검증. 고정 tier2 처리량·새 점수 측정 전 기본 tier 변경하지 않음 |
| 법령 원문 추가 | 당시 개발 점수 하락 | 법령을 더 넣는 일반론으로 재개하지 않음 |
| 제품 공통 상태·F/L 개입 | 미소비/불확정과 유효 개입을 구분 | 입력 소비 위치 없이 사실 문자열만 바꿔 효과를 주장하지 않음 |
| v11 상태 자동 보완 | 개발 두 오류 회복, 새11건은 제목 조건으로 미진입 | 개발 결과 보존, 전이 검증 통과로 표기하지 않음 |
| v20 기존 추가 호출 | 역사적 결합 .718474 | 이전 비교 기준 보존 |
| 정밀감사 CPU + 기존 v20 | 역사적 결합 .751807 | B4 기준 보존, 새 추론 성능과 구분 |
| B4 + G/WS v9 | 저장 응답 결합 CPU .758950 | 측정한 최고 CPU 후보 보존; 새 전체 추론 미검증·동등품 해석 잔여 문제 |
| r3 CPU | 과거480응답 .750246, 3회 경로 | .751의 4회 경로와 단순 우열 비교 금지 |
| A/C/L 실제224 | 전체 새 입력 교체 회복7/새12, 부분 개선 | 전체 C 교체 기각, 완료 실험 |
| typed A/T192 | 실제314응답(재시도122), T 최종57요청 결측 | 완료·전체 비채택. 결측을0으로 채우지 않음 |
| T1 사후 실패 격리 | 노출32건 국소 개선, 의미 근거 미해결 | 연구용 보존, 정확도 개선본 비채택 |
| v24 G/Q64 | 입력·소비 연결이 불충분 | 새 모델 실행 전 종료, 새 추론 점수 없음 |
| 새 B4 개발160 | 실제640응답 .706466, 과거 대비 회복10/새21 | 완료. 후속 재현 차이 진단도 종료; 아래384응답 대조 참조 |
| 조건부 v20 CPU | 같은 새 응답498개 .710923 | 추가 GPU 결과 아님. SW 범위 미해결, 비채택 |
| v9 조건별 도구 루프 | 실제49응답, 고정/선택형 모두 소비0·보류15 | 현 정책 기각. 참조/출력 계약 문제를 모델 한계와 구분 |
| .751 순서 재현 대조 | 새384응답·공통128. 과거 순서가 과거 응답에 더 가까움, 최종 오류32→30 | KV 용량·첫 호출 변동의 한계. 전체160 F1/새 .751 복원/생산 채택 아님 |
| 단일 canonical ZIP 새640 | .740544 FP61/FN23, .706 대비 +.034078. 과거 .751 대비 회복2/새9 | 실행·보정 경로 통합 및 일부 재현 손실 복구. 최고점 완전 재현과2시간 처리량은 미해결 |
| 원문 묶음 선택 + V10 새18 | 같은 혼합 검색 실제 예산에서 묶음6→7/12, 새14개 판정의 대조군 대비 오류2개 감소. 혼합 응답 F1 .7634→.7845 | [상세 감사](../runs/independent_audit_20260913/evidence_selection_audit.md). 작은 예산 묶음4→3 회귀, 노출 사례의 선택 옵션으로 보존. 전체 새 추론·.85·기본 채택 아님. GPU 종료 |
| V12b V9 관계 구조화 새90 | 같은 원문41공고의 두 방식 비교와8개 반복. 형식 오류0. 비교군 대비 오탐4 감소/미탐1 증가, 최고 저장본 대비 회귀 | [상세 감사](../runs/independent_audit_20260913/specification_v12b_review_v1/report_v2.md). 기본 경로 비채택, GPU 삭제. 실제 원응답에서 품목 간 허용 범위 연결 오류를 확인하고 진단 보완. 반복 응답 선택·전체 새 추론 점수로 사용 금지 |
| V17·V24 CPU 등록 조건 감사 | 원문 업종 코드 관측120→133, 목록 경계 혼입 수정. 저장1280응답 재판정 두 CSV 동일, F1 상승 없음 | [상세 감사](../runs/independent_audit_20260913/qualification_comparison_audit_v1/report.md). 라벨 불일치와 코드 결함 분리, GPU0. 다음 V10/V13/V16/V18 범위 감사 |
| 구매 범위·자격 CPU 감사 | 표제·원문 경계·정산 역할·미등재 코드·복합어 검증 수정. 1236검사+35하위검사, GPU0 | [상세](../runs/independent_audit_20260913/purchase_qualification_audit_v1/report.md). 저장 CPU .803220/.781954, 최고 기준 대비 복수품목 V18 미탐1건 남아 승격 보류 |
| 복수 품목·고시·직접생산 감사 | 1297검사+35하위검사, 원문 경계/확인 의무/공통 규모 조건 보완, CPU 검색180비교, GPU0 | [상세](../runs/independent_audit_20260913/mixed_purchase_scope_v1/report.md). 저장 CPU .803220/.781954 유지; 혼합 검색의 판정용 근거 묶음 증가 없어 승격 보류 |
| V13 구매 피드백 새42 | 고정 예산의 구매 근거 묶음은3→5였으나 혼합 F1 하락 | [상세](../runs/independent_audit_20260913/purchase_fresh_attribution_v1/report.md). 핵심 사실 질의가 품목 질의로 대체되는 문제 확인. GPU 회수·삭제, 기본 비채택 |
| V14 원문 위치·질문군 새42 | 다른 위치의 동일 문장 보존, 자격 문맥 회복. 후처리 고정 F1 전체 개선 없음 | [상세](../runs/independent_audit_20260913/purchase_v14_attribution_v1/report.md). 동일 대조 입력14개 중3개 판정 변화, GPU80/40GB 차이 포함. GPU 회수·삭제 |
| 기업 자격·필드 주장 CPU 소비 | 1363검사+35하위검사. 저장640 V3 오탐1 감소, V5 동일; 고정 V13/V14에서 기업규모 오탐 교정 | [상세](../runs/independent_audit_20260913/consumer_relations_v1/report.md). 전체 새 추론 아님. V14 근거 회수 이득 유지, 최고 저장 CPU 및 미해결 회귀 별도 보존, 추가 GPU0 |
| 금액 원문·불확실성 경계 | 1403검사+35하위검사. 숫자/한글 병기 검증, 공백 손상·메타 대체 방지, 실제 금액 관측3개 회복 | [상세](../runs/independent_audit_20260913/money_literal_v1/report.md). 저장640 두 묶음 및 기존 검색84응답의 모든 예측 동일. F1 상승 없음, 추가 GPU0 |
| 표 구조 후보·원문 보존 | 1426검사+35하위검사. 준수사항·첨부 경계 수정, 누락 칸을 보존하는 배치 후보·제약 검사 | [상세](../runs/independent_audit_20260913/table_structure_v1/report.md). 합성 확정관계51정확/0오류, 미확정 후보60정확/30오류. BGE 질의4군의 원문·근거 확보 동일. CPU 점수 동일, 새 Gemma/GPU0 |
| 질의의 괄호 조건·숫자 공백 보존 | 1444검사+35하위검사. 헤더 정규화로 다른 질의를 합치던 오류 수정 | [상세](../runs/independent_audit_20260913/query_identity_v1/report.md). units 후보22개 복구, 32개 상한 안 추가10/밀림7. BGE 비교 원문 동일, 새 F1 측정·Gemma/GPU0 |
| 기관·지역 토큰의 공통 해석 | 1474검사+35하위검사. 지역 및 실적·지역 중복 경로에서 속성·손상 처리 반례13개 수정 | [상세](../runs/independent_audit_20260913/region_tokens_v1/report.md). 실제160과 표기변환234에서 변화0, CPU 두 모집단 점수 동일. 새 Gemma/BGE/GPU0 |
| 고시 특이사항 조건 계산 | 1545검사+35하위검사. 미처리171행 중 포함 설명7/조건식108행 처리, 미처리56행 유지 | [상세](../runs/independent_audit_20260913/catalog_predicates_v1/report.md). 조건식 해석과 사실 확보를 구분. 실제160 분류·저장640 두 모집단 예측 동일, F1 상승 없음. CAT-01의 원문 범위 연결·나머지 조건 미완료, GPU0 |

## 주요 로컬 증거 위치

- 최고 CPU 후보: `runs/review_sprint_20260909/supervisor_controls_20260909/carryover_751_20260912/results/`
- B4 과거 기준: `experiments/precision_joined_v1/results/old_response_join/`
- 이전 새 전체 실행(.706): `experiments/precision_joined_v1/full160_b4_actual_20260912/results/`
- 최신 단일 ZIP 새640(.740544): `runs/single_submission_20260912/results/`
- 재현 계약: `experiments/precision_joined_v1/reproduction_751_20260912/`
- v9 루프: `experiments/precision_joined_v1/v9_condition_loop_20260912/results/`
- 감독 독립 검토: `runs/review_sprint_20260909/supervisor_controls_20260909/`

입력·결과·해시를 참조하는 오래된 경로는 그대로 둔다. `research/`의 `current_*`,
과거 `STATUS.md`/`HANDOFF.md`의 `ACTIVE` 등은 당시 스냅샷이다. 현재 작업 권한은
여기서 상속하지 않는다. 오래된 공개 안내 원문은 git 이력에도 보존되어 있다.

- 2026-09-13 V11: JSON source-coordinate contract fixed; boundedrefill had no review-bundle gain. Same-cap factual-query lexical improved evidence6→8bundles but fresh mixedF1 .780069 < control.784533. Identical-input repeatedcontrol changed2/9judgmentrequests. GPU recovered/closed, not promoted. [Audit](../runs/independent_audit_20260913/query_lexical_and_repeat_audit.md).

- 2026-09-13 SW action modality: negation/optional/clipped-source guards fixed in canonical sourceadf71e;950tests+35subtests PASS. Five CPU populations3200responses unchanged; typed30valid/6invalid,5extra relation reviews,0bit changes. No GPU. [Audit](../runs/independent_audit_20260913/software_obligation_audit_v1/report.md).

- 2026-09-13 Heading context: compact numbering, decimal/date exclusions, numbered operative sibling boundaries fixed;997tests+35subtests PASS. Frozen BGE comparison at3caps has0bundle gain and one larger-budget hybrid quote loss; no GPU. CPUv3/v5 unchanged. [Audit](../runs/independent_audit_20260913/heading_context_audit_v1/report.md).

- 2026-09-13 Heading final source23d760: Unicode captions/Jamo placeholders preserved;1000tests+35subtestsPASS. Review-source contexts unchanged fromb6; CPUv3/v5 byte-identical. NoGPU. [Final audit](../runs/independent_audit_20260913/heading_context_unicode_v1/report.md).

- 2026-09-13 Sourcee64eb0: SW actor/object/action guards and exact action traces;1068tests+35subtestsPASS. V3/V5 CPU and all36 typed final bits unchanged,0newGPU. [Audit](../runs/independent_audit_20260913/software_roles_audit_v1/report.md).

- 2026-09-13 Sourced99677: explicit model applicability uncertainty/source guards; CPU V3.810164,V5.788899,0newerrors across5response populations.1096tests+35subtests; repeated-control final differences2->1,0GPU. [Audit](../runs/independent_audit_20260913/fact_consistency_audit_v1/report.md).

- 2026-09-13 Sourcec4df12: v9 explicit-locator/named-source citation repair.1280CPUresponses keep all bits,3e9repairs on one notice across stored responses;1118tests+35subtests. HR01 scope-consumption variability remains;0GPU. [Audit](../runs/independent_audit_20260913/v9_citation_validation_v2/report.md).

- 2026-09-14 V15 고시 조건 검색 새 추론: 고유18개,0재시도,원본/CPU재현 통과. 검색 근거는 개선됐으나 최종36판정 모두 동일;절대F1 미측정,기본 승격 없음,GPU 삭제. [보고서](../runs/independent_audit_20260913/condition_v15_analysis_v1/report.md)

- 2026-09-14 V16 고시 문맥·조건 소비: 새12응답의 최종36판정과 인용은 동일, 조건 경로4/4보류. 회수 후 부정관계·boolean 생성 필드를 보강해1856검사+35하위검사 통과; 새 스키마는 CPU 검증만 완료. 저장640 CSV 동일, 기본 비채택, GPU 삭제. [보고서](../runs/independent_audit_20260913/catalog_semantics_v1/report.md)

- 2026-09-14 원문 역할: 과업이 아닌 후보5개 제거·반복 원문 위치104개 보존, 요구사항 범위 및 선택적 생성 역할 제약 보강.1881검사+35하위검사,저장1280응답 CSV 동일. 같은 원문8요청 준비만 완료,새Gemma/GPU0. [보고서](../runs/independent_audit_20260913/catalog_source_roles_v1/report.md)

- 2026-09-14 V17 원문 역할 새8응답: A4/4·B2/4 형식 통과, B의 반복 출력2건은 길이 제한으로 실패하여 최종 비교 보류. 원응답 전부 회수·검증, 재생성0, GPU 삭제. 이후 각 원문 역할을 한 번만 출력하는 슬롯 제약을 CPU에서 검증하여1893검사+35하위검사 통과, 저장640응답 CSV 동일. 새 슬롯의 Gemma 추론과 F1 효과는 미검증. [보고서](../runs/independent_audit_20260913/gpu_roles_v17/report.md)

- 2026-09-14 고시 잔여56 조건: 전체 조건식·원문 사실 타입 연결,2012검사+35하위검사 통과. 고시 미처리56→0,사실필요178;3D 적용범위와 별도법령 분류는 미확정 보존. source160 관측·저장640 CSV·V17 유효6상세 모두 동일,새F1/GPU0. 다음 공고별 생성필드 제한 및 허용관계 소비. [보고서](../runs/independent_audit_20260913/catalog_remaining_conditions_v2/report.md)

- 2026-09-14 공고별 고시 필드 계약: 프롬프트·생성·소비기에 같은 품목/필드 쌍 적용,2031검사+35하위검사 통과. 고정원문8입력82807→60200토큰,원문·기존유효상세·저장640 CSV 유지. 새F1/GPU0, 다음 허용관계 소비 감사. [보고서](../runs/independent_audit_20260913/catalog_field_contract_v1/report.md)

- 2026-09-14 허용관계 소비: 검색어에 없는 모델 permission 누락12합성 수정,실제 허용문장1개 줄바꿈 보존.2066검사+35하위검사,source160/저장640 CSV/기존유효6판정 동일.역할3은CPU검증,새F1/GPU0,다음CAT02. [보고서](../runs/independent_audit_20260913/catalog_permission_consumption_v1/report.md)

- 2026-09-14 직접생산 예외 범위: 지정 소액수의 전체 추정가격 경계 누락4합성 수정.2096검사+35하위검사,source160 관측/V3·V5 저장640 CSV/기존유효6판정 동일.새F1·GPU0,다음SPEC05. [보고서](../runs/independent_audit_20260913/production_exception_scope_v1/report.md)

- 2026-09-14 자격절 계층: 명시적 부모번호/필수목록/미완결 범위6합성 수정.2131검사+35하위검사,source160 핵심관측 및 V3/V5 저장640 CSV 동일.새F1·GPU0,다음SPEC03B. [보고서](../runs/independent_audit_20260913/qualification_hierarchy_v1/report.md)

- 2026-09-14 지역토큰 잔여소비: temporal/comparison의 임의속성 지명유출 수정,등록지역·기관 장소역할 분리.2155검사+35하위검사,source160양성/V3·V5 CSV동일.새F1·GPU0,다음SPEC04/06. [보고서](../runs/independent_audit_20260913/region_consumers_v2/report.md)

- 2026-09-14 입력 계약: 원본 meta 상태/관리 타입/완전성 모순과 보조 SME 열린 자격절 수정.2209검사+35하위검사,V3·V5 입력/meta/CSV동일,새F1·GPU0. [보고서](../runs/independent_audit_20260913/input_contract_v1/report.md)

- 2026-09-17 V30 종결: canonical 전체160 새 추론은 Gemma 원응답762개와 결정론적
  skip130개, 형식 재시도0으로 개발 Macro F1 `.8581487956`를 기록했다. 같은 원응답에
  검증된 결정론적 소비기만 적용한 frontier12는 `.9106120731`(FP15/FN8)이며 새 추론이
  아니다. 잔여23셀을 전부 원문에 귀속했고, DEV122 자격 제목 소유권을 수정했으며,
  DEV22 품목 표면어 오연결을 막는 다음 입력 계약을 canonical에 연결했다. 그 입력 변화의
  효과는 새 추론 전까지 점수에 포함하지 않는다. 전체3439테스트+35하위검사 통과,
  148파일 최종 ZIP과 노트북 동기화, GPU·Colab 종료. 공식점수와 L40S 실측은 없다.
  [종결 보고서](../runs/independent_audit_20260913/final_canonical_v30/report.md)
