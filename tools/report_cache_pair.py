"""Summarize a completed H3 recovery, including failed-attempt cost and scope."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import statistics


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def telemetry(path):
    with path.open(encoding="utf-8") as stream:
        rows = [{k.strip(): v.strip() for k, v in r.items()} for r in csv.DictReader(stream)]
    values = lambda key: [float(r[key].split()[0]) for r in rows]
    times = [datetime.strptime(r["timestamp"], "%Y/%m/%d %H:%M:%S.%f") for r in rows]
    return {"samples": len(rows), "first_utc": times[0].isoformat(), "last_utc": times[-1].isoformat(),
            "observed_seconds": (times[-1]-times[0]).total_seconds(),
            "mean_gpu_utilization_pct_entire_attempt": statistics.mean(values("utilization.gpu [%]")),
            "max_gpu_memory_mib": max(values("memory.used [MiB]")),
            "note": "2-second nvidia-smi observations include validation/load/shutdown; not isolated inference or per-step batching"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair-dir", type=Path, required=True)
    parser.add_argument("--original-dir", type=Path, required=True)
    parser.add_argument("--failed-recovery-dir", type=Path, required=True)
    parser.add_argument("--evaluation-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    assert not args.out.exists() and not args.report.exists(), "Preserve existing summaries"
    pair, original, failed = args.pair_dir, args.original_dir, args.failed_recovery_dir
    comparison, status = read(pair/"comparison.json"), read(pair/"status.json")
    evaluation = read(args.evaluation_dir/"evaluation.json")
    assert status["status"] in {"PAIR_COMPLETED_EQUIVALENT", "PAIR_COMPLETED_WITH_DRIFT"}
    assert evaluation["status"] == "SCORED_ACTUAL_NEW_GPU_PAIR"
    assert evaluation["pair_comparison_sha256"] == hashlib.sha256((pair/"comparison.json").read_bytes()).hexdigest()
    arms = {}
    for size in (32, 8):
        calls = read(pair/f"arm{size}/calls.json")["calls"]
        result = read(pair/f"arm{size}/result.json")
        batches = read(pair/f"arm{size}/batches.json")["batches"]
        assert len(calls) == 96 and all(c["generation_executed"] is True and c["status"] == "MODEL_OUTPUT_VALIDATED" for c in calls)
        assert all(c["sample_id"] == result["sample_id"] for c in calls)
        total = {k: sum(c[k] for c in calls) for k in ("input_tokens", "generated_tokens", "answer_tokens", "thought_tokens", "cached_input_tokens")}
        arms[str(size)] = {"requests": 96, "actual_generate_invocations": len(batches),
            "requests_per_invocation": [len(b["requests"]) for b in batches],
            "tokens": total, "cached_fraction": total["cached_input_tokens"]/total["input_tokens"],
            "output_tokens_per_generate_second": total["generated_tokens"]/result["generate_wall_seconds"],
            "raw_output_failures": result["failed_outputs"],
            "token_limit_outputs": sum(c["token_limit_reached"] for c in calls),
            "stop_outputs": sum(c["finish_reason"] == "stop" for c in calls),
            "raw_max_whitespace_run": max(c["raw_whitespace"]["max_whitespace_run"] for c in calls),
            "raw_max_newline_run": max(c["raw_whitespace"]["max_newline_run"] for c in calls),
            "ttft_available_calls": result["ttft_available_calls"],
            "phase_seconds": {k: result[k] for k in ("pre_engine_validation_seconds", "engine_load_seconds",
                "post_engine_validation_seconds", "generate_wall_seconds", "inference_and_audit_seconds",
                "request_preparation_seconds", "parsing_seconds", "explicit_shutdown_seconds", "child_body_seconds")},
            "generate_seconds_by_group": {str(g):sum(b["generate_wall_seconds"] for b in batches if b["requests"][0][1] == g) for g in (1,2,3)}}
    attempt_cost = {"original_pair_seconds": read(original/"status.json")["parent_wall_seconds"],
                    "failed_recovery_seconds": read(failed/"recovery_timing.json")["recovery_parent_wall_seconds"],
                    "successful_recovery_seconds": status["recovery_parent_wall_seconds"]}
    telemetry_results = {str(i+1): telemetry(p.parent/(p.name+"_gpu.csv")) for i,p in enumerate((original,failed,pair))}
    first = datetime.fromisoformat(telemetry_results["1"]["first_utc"])
    last = datetime.fromisoformat(telemetry_results["3"]["last_utc"])
    process_sum = sum(attempt_cost.values())
    cost = {**attempt_cost, "sum_process_wall_seconds": process_sum,
            "observed_first_start_to_last_end_seconds": (last-first).total_seconds(),
            "intervening_orchestration_idle_and_packaging_seconds_approx": (last-first).total_seconds()-process_sum,
            "currency_charge": None, "billing_note": "Existing Colab A100 allocation; no new paid service. Compute-unit debit not captured.",
            "new_model_outputs_entire_experiment": 192, "reused_model_outputs_in_successful_recovery": 96,
            "failed_arm8_model_outputs": 0}
    fast = arms["8"]["phase_seconds"]["generate_wall_seconds"] < arms["32"]["phase_seconds"]["generate_wall_seconds"]
    cache = comparison["later_group_cache_near_shared_boundary"]
    cache_pass = cache["arm8"] >= 58 and cache["arm32"] < 58
    record = {"status":"MEASURED_H3_PAIR_SUMMARY", "records":32, "arms":arms, "cost":cost,
              "cache_criterion_passed":cache_pass, "generation_faster":fast,
              "later_group_cache_near_shared_boundary":cache, "telemetry":telemetry_results,
              "scope":"Frozen-input inference-stage comparison on A100; not full production pipeline, L40S, dev160, or leaderboard",
              "unmeasured":["isolated GPU prefill/decode", "actual scheduler step sizes", "TTFT when RequestOutput metrics absent", "currency debit"],
              "comparison_sha256":evaluation["pair_comparison_sha256"]}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding="utf-8")
    a,b = (evaluation["per_arm"][str(s)] for s in (32,8))
    lines = ["# H3 실제 배치 비교 결과", "", "2026-09-08. 개발 첫32건×3항목군, 동일 v6 원본 입력/모델/스키마/0·768·0 thinking/후처리. 전체 개발160건이나 리더보드 점수가 아니다.", "",
             f"batch32 → batch8: F1 **{a['macro_f1']:.8f} → {b['macro_f1']:.8f}**, 고친 오류 **{evaluation['corrections']}**, 새 오류 **{evaluation['regressions']}**.", "",
             "| 항목 | 양성 정답 수 | F1 32 | F1 8 | FP/FN 32 | FP/FN 8 |", "|---|---:|---:|---:|---:|---:|"]
    for k in range(1,25):
        x,y=a["per_item"][f"v{k}"],b["per_item"][f"v{k}"]
        lines.append(f"|v{k}|{x['support']}|{x['f1']:.4f}|{y['f1']:.4f}|{x['fp']}/{x['fn']}|{y['fp']}/{y['fn']}|")
    lines += ["", "양성 정답이 없는 항목의 F1=0은 이 표본에서 재현율을 평가할 수 없다는 의미도 갖는다. 32건 수치를 전체 개발 점수와 직접 비교하지 않는다.", "",
              "| 실측 항목 | batch32 | batch8 |", "|---|---:|---:|"]
    for label,key in (("입력 토큰","input_tokens"),("생성 토큰","generated_tokens"),("최종 답변 토큰","answer_tokens"),("thinking 토큰","thought_tokens"),("재사용된 KV 입력 토큰","cached_input_tokens")):
        lines.append(f"|{label}|{arms['32']['tokens'][key]:,}|{arms['8']['tokens'][key]:,}|")
    for label,key in (("실제 generate 호출 횟수","actual_generate_invocations"),("잘림","token_limit_outputs"),("형식/파싱 등 출력 실패","raw_output_failures"),("원시 최대 연속 공백","raw_max_whitespace_run"),("원시 최대 연속 개행","raw_max_newline_run")):
        lines.append(f"|{label}|{arms['32'][key]}|{arms['8'][key]}|")
    for label,key in (("생성 시간(초)","generate_wall_seconds"),("진단 포함 추론(초)","inference_and_audit_seconds"),("요청 준비(초)","request_preparation_seconds"),("JSON 파싱(초)","parsing_seconds"),("엔진 로드(초)","engine_load_seconds"),("가중치 등 사전 검증(초)","pre_engine_validation_seconds"),("프로세스 내부 전체(초)","child_body_seconds")):
        lines.append(f"|{label}|{arms['32']['phase_seconds'][key]:.3f}|{arms['8']['phase_seconds'][key]:.3f}|")
    lines += ["",f"후속 항목군 공통 prefix 경계까지 재사용한 요청은 **{cache['arm32']}/64 → {cache['arm8']}/64**였다. 사전 기준 통과 여부={cache_pass}. 생성 단계가 빨라졌는지={fast}.", "",
              "각 호출에 요청32개 또는8개를 넣은 사실과 실제 생성 벽시계/캐시 토큰은 측정했다. 엔진 max_num_seqs=32, max_num_batched_tokens=8192는 동일하다. 실제 스케줄러의 매 스텝 활성 요청 수나 독립 prefill/decode 시간은 수집하지 않았으므로 이를 실측했다고 주장하지 않는다. TTFT도 기존 RequestOutput 경로에서는 제공되지 않았다.", "",
              "두 표본 모두 새 프로세스의 빈 KV 캐시로 시작했다. 모델 파일과 컴파일 캐시는 기존 런타임 것을 사용해 엔진 로드 시간을 엄격한 동일 cold-start 비교로 해석할 수 없다. batch8은 공식 num_gpu_blocks_override로 batch32 실제69,370블록/201,624토큰 용량을 재현했다. 이 제어 필드만 다르고 나머지 설정은 동일하다.", "",
              "근거: 음성 및 허용된 부재 항목 공란 규칙/정확한 원문 인용/길이/참조 범위를 양쪽 독립 검사했다. 비부재 양성의 비어 있지 않은 근거 수는 아래와 같다. 문자열 일치 자체가 법적 충분성을 보증하지는 않는다.", ""]
    for size in (32,8):
        ev=evaluation["evidence"][str(size)]
        lines.append(f"- batch{size}: 비부재 양성 {ev['positive_nonabsence']}개, 유효 원문 비공란 {ev['nonempty_exact_source']}개, 공란 {ev['empty']}개.")
    lines += ["", "오류 변경 상세는 평가 JSON의 changes에 저장했다. 변경된 판정의 근거 의미 검토는 별도 기록한다.", "",
              f"최초 실패 실험 {cost['original_pair_seconds']:.3f}초 + 설정 직렬화 검사 실패 복구 {cost['failed_recovery_seconds']:.3f}초 + 성공 복구 {cost['successful_recovery_seconds']:.3f}초 = **프로세스 비용 {process_sum:.3f}초({process_sum/60:.2f}분)**. 준비·가중치 검증·로드·종료·실패 비용을 포함한다.", "",
              f"처음부터 마지막 GPU 관측까지 **{cost['observed_first_start_to_last_end_seconds']/60:.2f}분**이며, 그 중 약{cost['intervening_orchestration_idle_and_packaging_seconds_approx']/60:.2f}분은 실패 진단·로컬 작업·대기·포장 사이 시간이다. 연결된 A100의 이 시간도 비용 범위에 남긴다. 정확한 원화/컴퓨팅 단위 차감은 측정하지 않았다. 새 유료 서비스와 제출은0회다.", "",
              "성공한 batch32 출력96개를 그대로 보존·재사용했으며, 성공 복구에서 새로 생성한 것은 batch8의96개뿐이다. 실패한 두 arm8 시도는 생성 전 중단됐다. 총 실제 새 모델 응답은192개이고, 복사된96개를 더해288개로 부풀리지 않는다. 모델 출력 실패를0 판정으로 대체한 사례는 없다.", "",
              f"양쪽 원시 출력에 적용한 동일 후처리 CPU 시간은 Windows에서 {evaluation['local_cpu_postprocessing_seconds']['32']:.3f}/{evaluation['local_cpu_postprocessing_seconds']['8']:.3f}초다. 이를 A100 또는 L40S에서 측정한 시간으로 더하지 않는다. 입력 검색/프롬프트 생성은 동결된 결과를 재사용했으므로 이 실험 전체 비용과 생산용 전체 파이프라인 비용을 구분한다.", "",
              "기존 개발160 최선 F1 **0.5504169442**와 미커밋 생산 코드는 보존했다. 실제 L40S 제한/전체 리더보드/0.88 달성 여부는 검증하지 않았다.", "",
              "근거 자료: [vLLM v0.26 KV 배정 구현](https://github.com/vllm-project/vllm/blob/v0.26.0/vllm/v1/core/kv_cache_utils.py), [캐시 설정](https://github.com/vllm-project/vllm/blob/v0.26.0/vllm/config/cache.py)."]
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({"report":str(args.report),"cache_pass":cache_pass,"generation_faster":fast,"cost":cost},ensure_ascii=False))


if __name__ == "__main__":
    main()
