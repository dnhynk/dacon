"""Read-only semantic fixtures for the proposed v7 source/role boundary.

Synthetic execution checks, not real-notice labels or a measured F1 score.
"""
import argparse
import json
import sys
from pathlib import Path


def record(text):
    return {
        "id": "SYNTHETIC-REGION-SCOPE",
        "meta": {"적용계약법": "국가계약법", "업무구분": "일반용역",
                 "계약방법": "제한경쟁", "소관구분": "국가기관",
                 "입찰추정가격": 50_000_000},
        "docs": [{"doc_id": "D0", "type": "공고문", "text": text}],
        "input_completeness": {"완전관측": True}, "dropped_doc_counts": {},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source.resolve()))
    from pps.regions import multiple_region_check

    duty = "본점이 충청남도 또는 충청북도에 소재한 업체이어야 한다."
    cases = [
        ("active", "2. 입찰참가자격\n" + duty, 1),
        ("office_vs_contact", "2. 입찰참가자격\n본점이 충청남도에 소재한 업체이어야 하며 공고 문의처는 충청북도에 있다.", None),
        ("office_vs_issuer", "2. 입찰참가자격\n본점이 충청남도에 소재한 업체이어야 하며 인증서 발급기관은 충청북도에 있다.", None),
        ("exception_inline", "관리대상: 충청남도 및 충청북도의 시설\n2. 입찰참가자격\n" + duty, None),
        ("exception_wrapped", "관리대상:\n충청남도 및 충청북도의 시설\n2. 입찰참가자격\n" + duty, None),
        ("exception_table_wrapped", "관리대상\n소재 지역\n충청남도, 충청북도\n2. 입찰참가자격\n" + duty, None),
        ("explicit_withdrawal_later", "2. 입찰참가자격\n" + duty + "\n3. 정정사항\n위 본점 소재지의 지역제한 조건은 삭제한다.", None),
        ("unrelated_negation", "2. 입찰참가자격\n본점이 충청남도 또는 충청북도에 소재한 업체이어야 하며 실적증명서 제출은 요구하지 않는다.", 1),
        ("supplier_exception_inline", "2. 입찰참가자격\n" + duty + "\n해당 지역에 자격을 갖춘 업체가 10인 미만이다.", None),
        ("supplier_exception_wrapped", "2. 입찰참가자격\n" + duty + "\n해당 지역에 자격을 갖춘 업체가\n10인 미만이다.", None),
    ]
    results = []
    for name, text, expected in cases:
        decision = multiple_region_check(record(text))
        value = decision.get("value") if decision else None
        results.append({"name": name, "expected": expected, "actual": value,
                        "passed": value == expected, "text": text,
                        "evidence": decision.get("evidence") if decision else None})
    print(json.dumps({"source": str(args.source.resolve()), "synthetic_only": True,
                      "failures": sum(not r["passed"] for r in results),
                      "results": results}, ensure_ascii=False, indent=2))

    if any(not r["passed"] for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
