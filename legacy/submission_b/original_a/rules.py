"""Deterministic checks grounded in the supplied law snapshot, per notice only."""
from __future__ import annotations

import re

from .data import clean_evidence
from .temporal import predict as temporal_checks
from .performance import performance_facts
from .other_checks import predict as other_checks


def narrow_region_check(rec):
    """Conservative v6 positive check for explicit basic-municipality tokens.

    Plain locality names, unknown authority ceilings, quote procedures and
    unrecognized clauses remain model decisions. This does not infer geography
    from a place of delivery, an address, or corpus-level region statistics.
    """
    meta = rec["meta"]
    law, price = meta.get("적용계약법"), meta.get("입찰추정가격")
    if (law not in {"국가계약법", "지방계약법"} or type(price) not in (int, float)
            or price <= 0 or meta.get("계약방법") != "제한경쟁"
            or meta.get("업무구분") not in {"일반용역", "물품(내자)"}):
        return None
    token = re.compile(r"\[지역:[^\]\n]*단위=기초[^\]\n]*\]|\[수요기관\(기초자치단체\)\]")
    for doc in rec["docs"]:
        if doc["type"] != "공고문":
            continue
        text = doc["text"]
        if re.search(r"수의\s*계약\s*(?:안내|공고)|계\s*약\s*방\s*법[^\n]{0,20}수의|견적\s*(?:제출)?\s*(?:안내|공고)", text[:3000]):
            return None  # Actual quote procedure can contradict a generic meta label.
        for match in token.finditer(text):
            paragraph = text.rfind("\n\n", 0, match.start())
            left = max(paragraph + 2 if paragraph >= 0 else 0, match.start()-420, 0)
            end = text.find("\n\n", match.end())
            right = min(end if end >= 0 else len(text), match.end()+160)
            context = text[left:right]
            prefix, suffix = text[left:match.start()], text[match.end():right]
            if not re.search(r"본점|주된\s*영업소|본사", prefix):
                continue
            if not (re.search(r"소재|둔|두고|있는", suffix) and re.search(r"업체|갖춘\s*자", suffix)):
                continue
            if re.search(r"견적|수의계약|해제|지역제한\s*없", context):
                continue
            ceiling = 230_000_000
            if law == "지방계약법" and "[수요기관(기초자치단체)]" in context:
                ceiling = 500_000_000
            if price >= ceiling:
                continue
            evidence = clean_evidence(context, rec)
            if evidence:
                return {"item": 6, "value": 1, "evidence": evidence,
                        "source": "국가 시행규칙25조③ / 지방 시행규칙25조③",
                        "estimated_price": price, "ceiling": ceiling,
                        "matched_region_token": match.group()}
    return None


def joint_share_check(rec):
    """Article 9 / local joint-contract guideline: explicit minimum shares.

    Missing share wording alone is not labeled a violation. The requirement
    concerns each joint-performance member, not the lead member or a divided
    performance agreement. No corpus statistics or IDs are used.
    """
    scope = str(rec["meta"].get("적용계약법", ""))
    if scope not in {"국가계약법", "지방계약법"}:
        return None
    if "공사" in str(rec["meta"].get("업무구분", "")):
        return None
    local = scope == "지방계약법"
    threshold = 5. if local else 10.
    found = []
    pattern = re.compile(r"최소\s*(?:계약\s*)?(?:참여\s*)?(?:지분율|지분|출자\s*비율|참여\s*비율)"
                         r"[^\d%％]{0,25}(\d+(?:\.\d+)?)\s*(?:[%％]|퍼센트)")
    for doc in rec["docs"]:
        text = doc["text"]
        mode = str(rec["meta"].get("공동도급구성방식", ""))
        if "분담" in mode and "공동이행" not in mode and "공동이행" in text:
            return None  # Conflicting metadata cannot negate an explicit clause.
        if (re.search(r"서로\s*다른\s*법령|업종\s*간\s*공동", text)
                and re.search(r"최소\s*지분율.{0,35}적용하지", text)):
            return None  # The model must assess the inter-industry exception.
        doc_found = False
        for match in pattern.finditer(text):
            paragraph = text.rfind("\n\n", 0, match.start())
            lo = max(paragraph + 2 if paragraph >= 0 else 0, match.start() - 230, 0)
            hi = min(len(text), match.end() + 170)
            context = text[lo:hi]
            if not any(word in context for word in ("공동", "구성원", "수급", "업체별")):
                continue
            if "대표자" in text[max(lo, match.start()-35):match.start()] and "구성원" not in context:
                continue
            if "분담" in mode and "공동이행" not in mode:
                continue
            if "분담이행" in context and "공동이행" not in context:
                continue
            tail = text[match.end():match.end()+65]
            if re.match(r"\s*범위.{0,25}조정", tail):
                continue  # A permitted adjustment percentage is not a share.
            if re.match(r"\s*(?:미만|이하|에서)", tail):
                return None
            value = float(match.group(1))
            adjusted = (not local and bool(re.search(
                r"최소\s*지분율.{0,30}20\s*(?:[%％]|퍼센트)\s*범위.{0,20}조정", context))
                and any(w in context for w in ("계약담당", "제9조", "특성 및 규모")))
            permitted_minimum = threshold * .8 if adjusted else threshold
            doc_found = True
            found.append({"value": value, "minimum": permitted_minimum,
                          "violation": value < permitted_minimum,
                          "evidence": clean_evidence(context, rec)})
        if (not doc_found and re.search(r"공동이행|구성원별", text)
                and re.search(r"(?:지분|출자\s*비율|참여\s*비율).{0,35}\d+(?:\.\d+)?\s*(?:[%％]|퍼센트)", text)
                and not ("분담이행" in text and "공동이행" not in text)):
            return None  # Unrecognized share wording is not proof of compliance.
    if not found:
        return None  # No recognized condition cannot certify the model's positive as normal.
    bad = next((x for x in found if x["violation"]), None)
    return {"item": 21, "value": int(bad is not None),
            "evidence": bad["evidence"] if bad else "", "parsed": found,
            "source": "공동계약운용요령 제9조⑤ / 지방 집행기준 제6장 구성원 수 등"}


def apply_rules(rec, row, knowledge=None, *, comparison=None):
    result = dict(row)
    checks = [joint_share_check(rec), narrow_region_check(rec)]
    # Dates and metadata extraction only prove specific violations. Their
    # explicit negatives or abstentions cannot certify a whole legal item.
    checks.extend(check for key, check in temporal_checks(rec).items()
                  if check["value"] == 1 and (key != 'v24' or comparison is None))
    if comparison is not None:
        from .comparison import positive_decision
        checks.append(positive_decision(rec, comparison))
    performance = performance_facts(rec)
    from .v2_quote_check import check as v2_quote_check
    checks.append(v2_quote_check(rec, performance))
    for item, decision in performance["overlays"].items():
        # Item2 specifically concerns experience restrictions below the notice
        # amount. A resolved, in-scope project price above that amount rules
        # out item2 even when another experience clause was not extracted.
        # It does not clear item3 (excessive required experience), item4 or8.
        if (item == 'v2' and decision['value'] == 0
                and decision['reason'] == 'known_estimate_not_below_supplied_notice'):
            checks.append({'item': 2, 'value': 0, 'evidence': '',
                           'reason': decision['reason'], 'source': 'supplied_performance_applicability'})
        # Partial extraction cannot rule out a different operative condition.
        # Only proven positive conditions override the model here.
        if decision["value"] == 1:
            evidence = next((clean_evidence(e["text"], rec) for e in decision["evidence"]
                             if clean_evidence(e["text"], rec)), "")
            if evidence:
                checks.append({"item": int(item[1:]), "value": 1, "evidence": evidence,
                               "reason": decision["reason"], "source": "supplied_performance_rules"})
    for item, decision in other_checks(rec).items():
        if decision["value"] is not None:
            checks.append({"item": int(item[1:]), "value": decision["value"],
                           "evidence": clean_evidence(decision["evidence"], rec),
                           "reason": decision["reason"], "source": "supplied_pledge_SW_briefing_rules"})
    if knowledge is not None:
        for item, decision in knowledge.sme_record_facts(rec)["decisions"].items():
            k, value = int(item[1:]), decision["value"]
            # Positive absence/size branches without observed development
            # activation remain model decisions pending further review.
            if value is None or value == 1 and k not in {12, 14}:
                continue
            spans = decision["evidence"]
            if k == 12:
                spans = list(reversed(spans))  # Prefer the operative certificate requirement.
            evidence = next((clean_evidence(s["text"], rec) for s in spans
                             if clean_evidence(s["text"], rec)), "") if value else ""
            if value and not evidence:
                continue
            checks.append({"item": k, "value": value, "evidence": evidence,
                           "reason": decision["reason"], "source": "supplied_SME_catalog_and_qualification_rules"})
    for check in checks:
        if check is not None:
            k = check["item"]
            result[f"v{k}"] = check["value"]
            result[f"e{k}"] = check["evidence"]
    return result, [check for check in checks if check is not None]
