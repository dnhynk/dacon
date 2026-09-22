# 스트리밍 실행기 계약 (submission/stream.py, submission/prep.py)

`python script.py`의 기본 실행기다. 목표는 하나다: **1853건 비공개 공고를 7200초 안에, 매 공고 24항목이 채워진 CSV로 끝낸다.**
`--executor cohort`는 이전의 32공고 묶음 실행기(`submission/runtime.py`)이며 재생·비교 도구가 계속 쓴다.

## 측정값 (무엇이 시간을 쓰는가)

A100-SXM4-40GB 스트리밍 실행 두 건(`runs/stream_executor_20260919/gpu_verify_01/recovered_01`, 2026-09-19)의 요청별 로그를 90초 창으로 회귀한 엔진 비용:

| 항목 | 값 |
|---|---|
| prefill 토큰당 | 157µs (6.4k 토큰/초; MoE Triton 커널이 "default config"로 동작, A100 peak의 16%) |
| decode 토큰당 | 0.35ms (동시성 2~3에서 MoE는 활성 expert가 적어 싸다) |
| 회귀 잔차 | 6.6% rms |
| canonical 200건 | 적재 420s + 생성 1,453s(7.27s/건) = 1,915s, 새 추론 dev F1 0.8628 |
| KV 캐시 | 36,473 토큰, 16k 요청 동시성 2.2 |

엔진 시간의 85% 이상이 prefill이다. 레코드당 prefill 토큰(캐시 제외)이 비용을 정하고, decode·동시성은 부차적이다.
prefix 캐시는 같은 레코드의 요청이 연속 투입될 때만 산다: A10은 71% 적중, A19는 대기열이 깊을 때(inflight 32) 63/200건이 0%였다.
그래서 투입 순서를 A1→A19→A10으로 두고 대기열을 8로 줄인다(동시성은 KV가 정하므로 깊은 대기열은 이득이 없고 캐시만 밀어낸다).

CPU 준비는 별도 프로세스라 엔진 시간에 포함되지 않는다(`prep.py`, BGE는 GPU float32·TF32 off; A100 검증에서 CPU 준비본과 패킷 토큰 동일).

## 1853건 투영 (A100-40GB 실측 비용, 적재 420초 포함)

| tier | 실행 프로파일 | dev200 F1 (동결 응답 재판정) | s/건 | 1853건 |
|---|---|---|---|---|
| 0 canonical | A1 A10 A19 L19 Q10 W20 S9 | 0.8775 | 7.1 | 13,560 |
| 1 no_optional_reviews | A1 A10 A19 Q10 | 0.8744 | 4.8 | 9,360 |
| 2 source_rules_a10 | A1 A19 Q10 (10–18 원문규칙 후 Q10 덮어씀) | 0.8886 | 3.5–3.9 | 6,920–7,680 |
| 3 source_rules_a10_a19 | A1 Q10 (10–24 원문규칙 후 Q10 덮어씀) | 0.8709 | 2.7 | 5,420 |
| 4 a1_only | A1 | 0.7960 | 1.8 | 3,770 |
| 마감 fallback | 없음(원문규칙만) | 측정 안 함 | 0 | – |

- A100-40GB에서는 tier3가 2시간 안에 들어오는 가장 높은 tier다. tier2는 경계선이다(A100-80GB 3차에서는 cold 적재가 328초로 짧아 tier2 고정 환산이 6,570초로 들어왔다). L40S 비용과 그로 정한 기본 tier 배분은 아래 "고정 tier 계획"에 있다.
- dev200 F1은 규칙이 그 200건으로 튜닝됐으므로 미지 데이터 증거가 아니고, 같은 패킷의 새 추론은 동결 응답 재판정보다 약 0.015 낮았다(0.8628 vs 0.8775).
- **기본 상한은 tier2다**(`--tier-ceiling 2`, 2026-09-19 사용자 승인). tier0/1은 tier2보다 비싸면서 새 추론 F1이 두 번 모두 낮았다(canonical 0.8587·0.8628 vs tier2 0.8809). 상한 2는 tier2 고정이 아니다: 고정 계획이 예산에 맞춰 tier2와 tier3를 섞는다. `--tier-ceiling 0`으로 canonical 재현이 가능하다.
- 기존 3단계였던 "A1+A19, Q10 제거"(0.8136)는 A19를 규칙판정으로 바꾸는 편(0.8709)보다 비싸고 나빠 폐기했다.

### build_02 A100 재검증

수정 정책을 같은 A100-40GB에서 새 추론한 결과는 `runs/stream_executor_20260919/gpu_verify_02/REPORT.md`에 있다.

| 실행 | 200건 wall time | 새 추론 dev200 F1 | 1853건 선형 환산 |
|---|---:|---:|---:|
| 투영 정책(tier3 199, a1_only 1) | 968.9s | 0.8587445 | 5,345–5,640s |
| tier2 고정 | 890.8s | **0.8809058** | 7,292–7,473s |
| tier3 고정 | 644.0s | 0.8503043 | 5,312–5,496s |
| canonical 고정, prefill 8192 | 1,663.7s | 0.8587449 | 14,742–14,920s |

범위의 앞 값은 cold 최초 투입 전 439.5초 + engine-busy/건 선형 환산, 뒤 값은 cold 엔진 적재 403.7초 + 적재 후 wall time/건 선형 환산이다. 신뢰구간이나 전체 1853 실측이 아니다. tier3는 두 계산 모두 2시간 안에 들고 tier2는 92–273초 넘는다. 투영의 최종 회귀값은 prefill 150.185µs/토큰, decode 0.348097ms/토큰이다. 16384 prefill은 첫 완료 전에 A100 CUDA OOM이므로 기본 8192를 유지한다.

이 실행들에서 확인된 사실과 그에 따른 수정(build_03):
- 엔진은 첫 투입부터 마지막 완료까지 유휴 0초였다(요청별 로그 기준). 준비 풀은 병목이 아니었다.
- 첫 투입은 엔진 적재 완료 후 41초였다. 준비 작업이 실행 루프에서만 투입됐기 때문이며, 이제 풀 시작 직후 첫 48건을 미리 투입한다(`prefeed`).
- 투영 실행은 32번째 투입에서 측정 전 prior 판단이 존재율 추정 변동으로 a1_only로 한 건 내려갔다 복귀했다. 측정 전 prior 판단은 첫 결정 한 번만 하고 측정 가능 시점까지 유지하도록 고쳤다.
- 인코더 영수증의 `encoded_texts=0`은 워커 시작 시점의 스냅샷이었다. 완료 시 인코더 서비스에 현재 카운터를 요청해 `run_report.json`의 `preparation.encoder`에 기록한다. `timing`에 엔진 준비 완료·첫 투입·생성 창·엔진 바쁜/유휴 시간을 함께 남긴다.
- `VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS=0`은 환경에 전달됐지만 0.26.0에서 CUDA-graph 메모리 추정(9초)을 막지 못했다. cold 적재의 미귀속 112초(warmup 완료 → CUDA-graph 추정 시작)는 compile 캐시가 있는 warm 실행에서는 1초라 compile 관련이지만 로그에 원인이 없다.
- 고정 tier2의 실측 wall은 3.67s/건으로 회귀 모델 3.44s보다 7% 크다. 모델은 tier 순서 판단에는 충분하지만 시간 예측은 약 10% 낙관할 수 있다.

## 고정 tier 계획 (`FixedTierPlan`, 기본 `--tier-plan fixed`)

레코드별 tier는 첫 투입 전에 정해지고, 같은 입력이면 엔진 속도와 무관하게 같은 요청 집합이 나간다. 측정 정책만 쓰면 같은 코드·입력의 두 실행이 A19를 받는 레코드에서 실행 시간에 따라 달라진다. 그러면 리더보드 비교와 수상 재현이 흔들린다.

- 입력은 레코드 수 N(`--projection-records`가 더 크면 그 값), 프로파일별 토큰 prior(`PRIOR_TOKENS`·`PRIOR_PRESENCE`), 엔진 prior 묶음 `PLAN_PRIORS`뿐이다. 측정 시간과 레코드 내용은 읽지 않는다.
- `PLAN_PRIORS`는 prefill 133.8µs/토큰, decode 1.314ms/토큰, 엔진 준비 320초다.
  - 앞의 둘은 손대지 않은 코퍼스 1,821건을 공식 조건(공식 이미지·L40S·기본 인자)으로 돌린 build_15 실행의 최종 회귀값이다(`runs/harness_improve_20260919/l40s_measure_01/`). 이 표본은 길이 분포가 평가 입력에 가깝다(문자 중앙값: 코퍼스 16k, 평가 샘플 18k, dev 13k).
  - 준비 시각은 L40S 측정(241–304초)보다 늦게 잡았다.
  - 이 값의 계획 비용은 레코드당 tier2 3.603초, tier3 2.625초다. 같은 실행의 tier별 벽시계(tier2 3.50–3.56초/건, tier3 2.57–2.63초/건)보다 크다.
  - 공식 서버의 build_13 실행(1,853건, 6,306초)은 같은 코드의 코퍼스 실행(1,821건, 6,330초)보다 건당 약 2% 빨랐다.
  - 값을 바꿀 때는 이 한 묶음만 고친다. 공식 이미지 실행의 tier별 벽시계보다 계획 비용이 작아지면 안 된다.
- a/b 분할 자체는 의미가 없다. 한 요청 혼합에서는 a와 b가 공선이라 분할이 정해지지 않는다. 같은 실행에서 회귀 decode는 0.35→1.31ms로, prefill은 149→134µs로 움직였지만 tier2 100건당 벽시계는 340–365초로 평평했다. 계획에 쓰이는 것은 tier별 합계 비용이다.
- 기각: 항목별 최댓값을 섞는 prior(prefill 162.8µs·decode 1.314ms). tier2를 4.21초/건으로 계획해, 1853건 계획의 tier2가 255건으로 준다.
- 예산 규칙은 측정 정책과 같다: 계획 작업 ≤ 남은 시간 × (1 − `margin_fraction` 10%).
  - 남은 시간 = 7,200 − 180(margin) − 320(계획 준비) − 지연 예약 = 6,700초. 지연 예약은 prior 지연 60초에서 0이다.
  - 상한 tier가 전부 들어가면 전부 상한이다. 아니면 들어가는 가장 높은 tier t와 그 위 t−1을 섞고, t−1의 수를 최대로 한다. 바닥 tier도 안 들어가면 전부 바닥이다.
- 싼 tier는 입력 순서에 고르게 퍼뜨린다. 싼 tier 수가 k일 때 레코드 i는 `(i+1)·k // N > i·k // N`이면 싼 tier다. 어느 앞부분에서도 싼 tier 수가 비례 몫과 1건 미만 차이다.
- N=1853이면 tier2 1,192건 + tier3 661건, 계획 작업 약 6,030초, 여유 약 670초다. 계획 동일성은 `plan_sha256`으로 확인한다.
- Q10·S9 실행 여부는 전과 같이 레코드별 패킷 유무와 S9 게이트가 정한다. tier는 어느 프로파일을 투입할지만 정한다.
- 선택형 focused verification(기본 off)은 여전히 측정 예산으로 게이트된다.

가드:
- 레코드 i의 계획 시작 = 320초 + 앞 레코드들의 계획 비용 합이다. 투입 시각이 이보다 slack 넘게 늦으면 그 레코드부터 끝까지 측정 정책(`TierPolicy`, 아래)이 tier를 정한다.
- 전환 시 측정 정책은 마지막 계획 tier에서 최근 전환이 없는 상태로 시작한다. 정상상태 전이면 prior 첫 결정을 한 번 한다.
- slack = max(`guard_min_slack_seconds` 120초, `guard_reserve_fraction` 0.5 × 여유). N=1853이면 335.2초다.
- 근거:
  - 발동하지 않은 실행의 마지막 투입은 계획 시각(≤ 6,349.7초) + slack 안이다. 투입 마감(7,020초)까지 여유의 절반(335초)이 남고, 그 안에서 마지막 요청들이 끝난다.
  - 발동 시점의 남은 시간은 남은 계획 작업 + (여유 − 지연), 곧 남은 계획 작업 + 약 여유의 절반이다. 측정 정책은 이 시간을 받는다.
  - 시뮬레이션에서는 계획 비용보다 약 3.9% 느린 엔진까지 계획이 유지된다(아래 표).
- 기각: slack = max(120초, 남은 계획의 5%). 끝으로 갈수록 slack이 120초로 줄어 2.9% 느린 엔진에서도 발동한다. 끝 무렵의 지연은 남은 작업이 적어 가장 덜 위험한데, 이 규칙은 그때 가장 엄격하다.
- 모든 마감 정지(`submit_deadline`, 지연 예약, abort)는 그대로다. 가드는 tier만 바꾼다.
- `run_report.json`의 `tier_plan`은 `planned_counts`, `plan_sha256`, tier별 계획 초/건, `priors`, `time_left_seconds`, `planned_work_seconds`, `reserve_seconds`, `guard_slack_seconds`, `max_lag_seconds`(전환 전 최대 지연), `guard_triggered`, `switch_record_index`, `switch_elapsed`, `switch_lag_seconds`를 담는다. 적응형은 `{"mode": "adaptive"}`다.

시뮬레이션: 가짜 엔진·시계로 1853건을 돌렸다. 요청은 직렬로 a × 새 prefill + b × decode초를 쓴다. 레코드별 토큰은 dev200 요청 분포를 prior 평균에 맞췄다. 스크립트와 결과는 `runs/harness_improve_20260919/precision_analysis/W4/`의 `simulate_tier_plan.py`, `simulation.json`이다. GPU 측정이 아니다. 모든 행에서 모델 호출 없는 레코드는 0이다.

| 엔진(실제 비용) | 고정 계획 | 종료(투입 마감 전 여유) | 적응형 종료 |
|---|---|---:|---:|
| L40S dev200 회귀 비용(162.8µs·0.352ms, tier2 3.622초/건), 준비 304초 | 미발동, 최대 지연 100초 | 6,451초 (569초) | 6,180초 |
| 코퍼스 실측 처리량(144.9µs·0.770ms), 준비 280초 | 미발동, −40초 | 6,181초 (839초) | 6,002초 |
| 코퍼스 회귀값이 실제라고 가정 | 미발동, −40초 | 6,113초 (908초) | 6,307초 |
| L40S 비용의 1.1배 | 레코드 886(3,539초)에서 발동 | 6,738초 (282초) | 6,511초 |
| L40S 비용의 1.2배 | 레코드 478(2,212초)에서 발동 | 6,648초 (372초) | 6,609초 |

- 가드가 발동하지 않은 실행의 tier는 계획과 같다(tier2 1,192 · tier3 661).
- 같은 입력을 1.00배·1.04배 속도로 돌리면 고정 계획의 요청 집합은 같다. 적응형은 tier2가 869건 대 777건으로 다르다.
- L40S 비용에 5개 난수 시드를 준 반복은 모두 미발동이었다(최대 지연 60–139초, 종료 6,419–6,497초).
- 가드가 발동하는 조건:
  - 계획 비용보다 균일하게 약 3.9% 이상 느림
  - 엔진 준비가 약 539초 이상
  - prefill 146µs·준비 280초에서 decode 1.27ms/토큰 이상
- 모델 호출 없는 레코드가 처음 생기는 균일 감속은 고정 계획 1.84배, 적응형 1.93배다. 차이는 가드가 발동 전에 쓰는 slack에서 온다. slack을 여유의 1%로 줄이면 1.92배, 90%로 늘리면 1.76배다(`breaking_point_vs_slack.txt`).
- `--tier-plan adaptive`는 변경 전 코드와 요청 순서·tier·전환 기록·종료 시각이 같다(1.0배, 1.2배).

## 측정 정책 (`CostModel`, `TierPolicy`: 가드 발동 이후, `--tier-plan adaptive`)

- 비용 = a × 새 prefill 토큰 + b × decode 토큰. a, b는 엔진이 바쁜 60초 창마다 완료 요청의 토큰 합을 모아 온라인 ridge 회귀로 추정한다. prior(a0=157µs, b0=0.35ms; `--prior-prefill-us`, `--prior-decode-ms`)는 처음에 창 하나 값어치이고 창이 쌓일수록 줄어든다. 한 가지 요청 혼합만 관측되면 a/b 분리가 불가능한데, 그때는 prior에 가장 가까운 분할을 쓰고 관측 혼합의 총비용은 정확히 맞춘다.
- 프로파일별 토큰(캐시 제외 prefill, decode, 캐시 비율)과 Q10/S9 존재율은 지수이동평균으로 갱신한다. 레코드당 비용 = Σ 존재율 × (a·prefill + b·decode).
- 결정: 투입 시점마다 `remaining × 비용(tier) ≤ 남은시간 × (1 − 10%)`를 만족하는 가장 높은 tier. 정상상태 전(완료 32건·바쁜 시간 120초·창 2개 미만)에는 prior로만 판단한다. 측정 이후 하강은 한 번에 한 단계, 8건 dwell(부족분이 25%를 넘으면 3건). 상승은 여유 10%p 추가와 16건 dwell. 모든 전환은 `run_report.json`의 `tier_timeline`에 a, b, 창 수, tier별 필요 시간과 함께 남는다.
- 이전 단일 가중 모델(κ=25)은 실측 κ≈2.2와 10배 어긋나 prefill 위주 tier로 갈수록 처리량이 낮게 읽혀 a1_only까지 자기강화 하강했다(A100 투영 실행: 164/200건 a1_only, F1 0.7970). 그 실행의 로그가 `tests/fixtures/stream_gpu_verify_20260919.json`이고, 정책 테스트는 이 로그로 tier3 정착을 검증한다.
- 개발 실행에서 1853건 마감을 흉내 내려면 `--projection-records 1853`을 쓴다. 고정 계획은 1853건으로 계획하고, 실제 레코드는 그 계획 앞부분의 tier를 받는다. 측정 정책은 남은 레코드 수만 그 값으로 계산한다. 정지 규칙은 둘 다 실제 마감을 쓴다.

## 구조

```
입력 레코드 ──▶ 준비 풀(워커 N개, 프로세스)            ──▶ 패킷 + 원문규칙 행
                 └ 인코더 서비스(BGE-M3 fp32, GPU 우선, OOM 시 CPU)
        ──▶ 스트리밍 실행기(엔진 프로세스): 레코드 순서대로 A1,A19,A10,Q10,L19 를 연속 add_request
        ──▶ 응답 → 워커에서 parse/consume ──▶ 행 조립 ──▶ submission.csv
```

- 준비 워커는 각자 `B4Pipeline`을 가지며 `bundle(record)`와 `consume(record, packet, response)`를 그대로 실행한다. 프롬프트·스키마·판정기는 canonical과 동일하다.
- 엔진 투입은 `LLMEngine.add_request`/`step`이며 `FINAL_ONLY` 출력을 쓴다. `inflight_requests`(기본 8, `--inflight-requests`)는 대기열 깊이다.
- 형식 실패는 즉시 thinking 0 재시도(최대 `max_response_retries`), 그래도 실패하면 그 항목군은 원문규칙 행으로 채운다.
- S9는 A1 소비 결과의 v9 또는 후보 수로 게이트한다(기존과 동일). 코드확정 A10(`source_questions`)은 워커가 준비 시 계산해 모델 호출을 생략한다.
- `--max-num-batched-tokens`는 엔진 prefill 청크 크기(설정 8192)만 바꾸는 실험 플래그다. 판정 입력은 바뀌지 않지만 배치 구성이 달라지므로 출력 잡음 수준의 변화는 있을 수 있다. 적용값은 `engine_overrides.json`과 `execution_config.json`에 남는다.

## 마감 규칙 (`StreamOptions`)

- `submit_deadline = 시작 + total_runtime_seconds − margin_seconds(180)`: 이후 새 레코드를 투입하지 않는다. 예상 레코드 지연이 `abort_grace_seconds`(60)를 넘으면 그만큼 더 일찍 멈춘다.
- `abort_deadline = submit_deadline + 60`: 진행 중 요청을 `abort_request`하고, 답이 없는 항목군은 원문규칙 행으로 채운다.
- 투입되지 못한 레코드는 준비된 원문규칙 행으로, 준비조차 안 된 레코드는 풀에 `rules` 작업을 보내 받은 행으로, 그것도 없으면 0으로 채운다(`filled_zero_cells`에 기록).
- 엔진 예외가 나도 `failure.json`을 남긴 뒤 완성 CSV를 쓰고 예외를 다시 던진다. **CSV를 보류하는 경로는 모델 응답을 한 번도 받지 못한 실행 하나다**: 대회 규정(LLM 호출 1회 이상)에 따라 규칙만으로 채운 CSV를 쓰지 않고 `emergency_csv_failure.json`을 남긴 채 실패한다.
- 모델 호출 0회로 끝난 레코드 수는 `run_report.json`의 `records_without_model_call`이다. 대회 규정상 이 값은 0이어야 하며, 0이 아니면 시간 부족의 증거다.

## 기록 (출력 디렉터리)

공고 원문·판정이 담기는 파일(`stream_*.jsonl.gz` 네 종, `B3.csv`, `input_contract.json`)은 `PPS_STREAM_JOURNAL=1`일 때만 쓴다. 공식 실행에는 이 변수가 없으므로 `submission.csv`와 집계 JSON만 남는다. 개발 실행(노트북, GPU 캠페인 러너, `tools/replay_stream.py`)은 이 변수를 켠다. 켜지 않으면 `tools/cell_reconsume.py`·`tools/verify_stream_run.py`가 읽을 패킷·원응답이 없다.

| 파일 | 내용 |
|---|---|
| `stream_packets_*.jsonl.gz` | 투입된 모든 패킷(토큰 ID·span·스키마 해시) |
| `stream_native_*.jsonl.gz` | 요청별 원시 출력, 종료 사유, 캐시 토큰, 투입/완료 시각, 프롬프트 토큰 일치 여부 |
| `stream_consumed_*.jsonl.gz` | 요청별 파싱 응답·판정 행·규칙 상세·오류 |
| `stream_records_*.jsonl.gz` | 레코드별 tier, 계획 프로파일, 각 행의 출처(model/format_recovery/source_rules/gate_skip), 최종 행 |
| `run_report.json` | 카운터, tier 분포·전환 타임라인, `tier_plan`(계획 수·`plan_sha256`·여유·slack·최대 지연·가드 발동과 전환 레코드), `cost_model`(a, b, 창 수), 프로파일별 토큰·캐시·존재율, 마감 상태 |
| `submission.csv`, `B3.csv`, `prediction_freeze.json` | 제출본과 해시 |

## 검증 절차와 현재 상태

1. 로컬(무GPU): `tools/replay_stream.py`가 동결 dev200 응답으로 실행기를 돌리고 `tools/verify_stream_run.py`가 패킷 토큰·호출 집합·CSV 셀을 대조한다. 2026-09-19: 200건 전부 호출 집합 동일, CSV 셀 동일, 새 모델 호출 0 (`runs/stream_executor_20260919/replay_verification_summary.json`).
2. GPU 1차(Colab A100-40GB, 2026-09-19, `gpu_verify_01/REPORT.md`): canonical 고정 200건 새 추론 1,915초, dev F1 0.8628. 단일 가중 모델의 1853건 투영은 a1_only 164건·F1 0.7970으로 실패했고 위의 비용 모델로 교체했다.
3. GPU 2차(build_02, 같은 A100, `gpu_verify_02/REPORT.md`): 수정 정책은 tier3 199+a1_only 1에 정착해 F1 0.8587. 고정 tier2/tier3는 각각 F1 0.8809/0.8503. 1853 환산은 tier3 5,312–5,496초, tier2 7,292–7,473초. 16384 prefill은 OOM, 8192는 완주했다. 네 완주 실행 모두 1,106/1,114 패킷 토큰이 기준과 같고 나머지는 이미 알려진 8개 차이다.
4. GPU 3차(build_04, Colab A100-80GB, `gpu_verify_03/REPORT.md`): 기본 상한 2의 1853 투영은 tier3 160 + tier2 40건, 849.6초(cold 적재 321초), F1 0.8652. tier2 고정은 생성 창 673.4초(3.37s/건), F1 0.8740 → 1853 환산 6,570초로 투입 마감(7,020초)에 450초 여유. 정책의 tier2 추정(2.97s/건)은 실측보다 12% 낮아 10% 안전 여유를 거의 다 쓰며, 그래서 정책은 tier2/tier3 경계에서 세 번 전환했다. 모든 실행에서 미해결 요청 0, 엔진 유휴 0초, 첫 투입은 엔진 준비 6.7초 뒤. BGE GPU 인코더는 200건에 9,488 텍스트·156초로 병목이 아니다. 세 실행 모두 1,106/1,114 패킷 토큰이 기준과 같고 나머지는 이미 알려진 8개 차이다.
5. L40S(공식 이미지 재현, `runs/harness_improve_20260919/l40s_measure_01/`): dev200 tier2는 3.54초/건이다. 손대지 않은 코퍼스 1,821건 적응형 실행 둘(build_13·build_15)은 6,321·6,341초에 끝났고 모델 호출 없는 레코드는 0이다. tier2는 3.50–3.56초/건, tier3는 2.57–2.63초/건이다. 공식 실행(build_13, 적응형)은 1853건을 6,306초에 끝냈고, 점수 외 실행 정보(tier 분포, `records_without_model_call`)는 돌아오지 않는다. 고정 tier 계획의 GPU 실행은 아직 없다.

## MoE 커널 자체 튜닝 (`submission/moe_tune.py`, 기본 off)

vLLM은 `E=128,N=704,... dtype=int8_w8a16` 튜닝 설정 파일이 없어 기본 Triton 블록 크기를 쓴다. `--moe-tuning auto`(또는 `PPS_MOE_TUNING=auto`)를 주면 엔진 적재 직전(준비 워커는 이미 동작 중)에 다음을 한다.

- 별도 프로세스(`python -m submission.moe_tune`)가 실제 expert 형상(E=128, topk 8, hidden 2816, intermediate 704)의 int8 가중치와 무작위 입력으로 vLLM의 `fused_experts`를 벤치마크한다. quant 설정은 weight-only인 `int8_w8a16_moe_quant_config`여야 한다(`FusedMoEQuantConfig.make(quant_dtype=int8)`은 W8A8 경로라 bfloat16 입력에서 실패한다). 후보는 기본 설정 + coarse 16개(BLOCK_M 64/128 × BLOCK_N 128/256 × BLOCK_K 64/128 × warps 4/8) + 최선 근방 refinement이고, 토큰 수 8192(실제 prefill 청크)와 4096에서 잰다. 예산은 `--moe-tuning-seconds`(기본 180초).
- 최선 후보가 기본보다 5% 이상 빠르고 같은 입력에서 출력이 기본 tiling과 일치(상대 2% 이내)할 때만 `moe_tuned_configs/`에 JSON을 쓰고 `VLLM_TUNED_CONFIG_FOLDER`를 설정한다. 작은 토큰 수(1~2048)에는 vLLM 기본값을 그대로 적어 decode tiling은 바뀌지 않는다. 그 외(실패·시간 초과·이득 부족·불일치)에는 파일을 쓰지 않고 엔진은 이전과 정확히 같게 동작한다. 결과는 `moe_tuning.json`, `moe_tuning_report.json`, `moe_tuning.log`에 남는다.
- **기본값이 off인 이유**(A100-80GB 실측, `gpu_verify_03/REPORT.md`): 커널은 8192 토큰에서 12.3%, 4096에서 15.7% 빨라지고 출력은 비트 동일했지만, tier2 200건 생성 창은 673.4 → 661.8초(−1.7%)였다. MoE 커널이 prefill의 약 18%뿐이기 때문이다. 1853건 환산 절감 약 107초가 튜닝 비용 160초보다 작다. 설정 파일명에 장치명이 들어가므로 다른 GPU에서 만든 파일을 동봉해도 L40S에는 적용되지 않는다.

## 하지 않은 것
- speculative decoding·fp8 KV 캐시: decode가 엔진 시간의 15% 미만이라 기대 이득이 작다.
- thinking 예산 축소, 더 강한 양자화, L19/Q10 프롬프트 재구성: 판정 입력을 바꾸는 실험이다.
- 남은 prefill 시간(약 80%)은 attention과 dense 층이다. 다른 MoE backend(`moe_backend='humming'` 등)는 최선의 경우에도 한 자릿수 % 이득이고 수치가 달라져 채택하지 않았다. 더 줄이려면 레코드당 새 prefill 토큰(프롬프트 구성) 자체를 줄여야 하며 그것은 판정 입력 변경이다.
