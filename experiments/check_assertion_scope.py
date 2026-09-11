"""Independent synthetic check: negation must bind to the matched duty.

These are program-behavior checks, not real-notice labels or an F1 estimate.
Source under review is selected explicitly; no source or results are modified.
"""
import argparse
import json
import sys
from pathlib import Path


def record(text):
    return {
        "id": "SYNTHETIC-NOT-A-COMPETITION-NOTICE",
        "meta": {"적용계약법": "국가계약법", "입찰추정가격": 100_000_000,
                 "계약방법": "제한경쟁", "업무구분": "일반용역",
                 "소관구분": "국가기관", "공동도급구성방식": "공동이행"},
        "docs": [{"type": "공고문", "text": text, "doc_id": "D0"}],
        "input_completeness": {"완전관측": True}, "dropped_doc_counts": {},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source.resolve()))
    from pps.rules import narrow_region_check, joint_share_check
    from pps.other_checks import sw_check

    cases = [
        ("region_active", narrow_region_check,
         "본점이 [지역:가상시;단위=기초]에 소재한 업체이어야 한다.", 1),
        ("region_withdrawn", narrow_region_check,
         "본점이 [지역:가상시;단위=기초]에 소재한 업체이어야 한다는 조건은 삭제한다.", "not_positive"),
        ("region_unrelated_negation_same_sentence", narrow_region_check,
         "본점이 [지역:가상시;단위=기초]에 소재한 업체이어야 하며 실적증명서 제출은 요구하지 않는다.", 1),
        ("region_unrelated_negation_next_sentence", narrow_region_check,
         "본점이 [지역:가상시;단위=기초]에 소재한 업체이어야 한다. 실적증명서 제출은 요구하지 않는다.", 1),
        ("share_active", joint_share_check,
         "공동이행 구성원의 최소 지분율은 5% 이상이어야 한다.", 1),
        ("share_withdrawn", joint_share_check,
         "공동이행 구성원의 최소 지분율은 5% 이상이라는 조건은 삭제한다.", "not_positive"),
        ("share_unrelated_negation_same_sentence", joint_share_check,
         "공동이행 구성원의 최소 지분율은 5% 이상이어야 하며 실적증명서 제출은 요구하지 않는다.", 1),
        ("share_unrelated_negation_next_sentence", joint_share_check,
         "공동이행 구성원의 최소 지분율은 5% 이상이어야 한다. 실적증명서 제출은 요구하지 않는다.", 1),
        ("sw_active", sw_check,
         "본 사업은 소프트웨어 사업이다.", 1),
        ("sw_negative_polite", sw_check,
         "본 사업은 소프트웨어 사업이 아닙니다.", "not_positive"),
        ("sw_withdrawn", sw_check,
         "본 사업은 소프트웨어 사업이라는 문구는 삭제한다.", "not_positive"),
        ("sw_unrelated_negation_same_sentence", sw_check,
         "본 사업은 소프트웨어 사업이며 실적증명서 제출은 요구하지 않는다.", 1),
    ]
    results = []
    for name, function, text, expected in cases:
        output = function(record(text))
        value = output.get("value") if output else None
        passed = value != 1 if expected == "not_positive" else value == expected
        results.append({"name": name, "expected": expected, "value": value,
                        "passed": passed, "text": text})
    print(json.dumps({"source": str(args.source.resolve()), "synthetic_only": True,
                      "failures": sum(not r["passed"] for r in results),
                      "results": results}, ensure_ascii=False, indent=2))

    if any(not r["passed"] for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
