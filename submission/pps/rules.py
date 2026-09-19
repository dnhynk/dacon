"""Deterministic checks grounded in the supplied law snapshot, per notice only."""
from __future__ import annotations

from .legal_context import applicable_law
import re

from .data import clean_evidence
from .temporal import predict as temporal_checks
from .performance import performance_facts
from .other_checks import predict as other_checks
from .assertions import assertion_scope, unresolved_assertion, has_withdrawal
from .anonymized_tokens import (
    anonymous_tokens, basic_notice_authority, registered_region_tokens,
)
from .region_thresholds import regional_price_bounds


def narrow_region_check(rec):
    """Conservative v6 positive check for explicit basic-municipality tokens.

    Plain locality names, unknown authority ceilings, quote procedures and
    unrecognized clauses remain model decisions. This does not infer geography
    from a place of delivery, an address, or corpus-level region statistics.
    """
    if has_withdrawal(rec, 'region'):
        return None
    meta = rec["meta"]
    from .prices import project_prices
    from .regions import regional_competition_scope
    law = applicable_law(rec)
    price = project_prices(rec)['estimated_price']['value_won']
    if (law not in {"국가계약법", "지방계약법"} or type(price) not in (int, float)
            or price <= 0 or not regional_competition_scope(rec)
            or meta.get("업무구분") not in {"일반용역", "물품(내자)"}):
        return None
    bounds = regional_price_bounds(rec)
    ceiling = bounds['below_ceiling']
    if ceiling is None:
        return None
    registered = registered_region_tokens(rec)
    structured_basic_scope = (
        meta.get('지역제한여부') == 'Y'
        and bool(registered)
        and all(token.attribute('단위') == '기초' for token in registered)
    )
    for doc in rec["docs"]:
        if doc["type"] != "공고문":
            continue
        text = doc["text"]
        for token in anonymous_tokens(text):
            if token.errors or not (
                    token.kind == 'region' and token.attribute('단위') == '기초'
                    or token.kind == 'institution' and token.value == '기초자치단체'):
                continue
            if unresolved_assertion(assertion_scope(text, token.start, token.end, 'region')):
                continue
            paragraph = text.rfind("\n\n", 0, token.start)
            left = max(paragraph + 2 if paragraph >= 0 else 0, token.start-420, 0)
            end = text.find("\n\n", token.end)
            right = min(end if end >= 0 else len(text), token.end+160)
            context = text[left:right]
            prefix, suffix = text[left:token.start], text[token.end:right]
            office_bound = bool(re.search(r"본점|주된\s*영업소|본사", prefix))
            # A notice may state the restriction directly as
            # ``[basic-municipality token] 지역 업체`` under bidder
            # qualifications without repeating "head office".  The typed token
            # supplies the administrative level; qualification language keeps
            # delivery/place mentions out.
            direct_local_bidder = bool(
                re.match(r'\s*(?:지역\s*)?(?:소재한\s*)?(?:업체|사업자)', suffix)
                and re.search(r'입찰|참가|자격|부정당\s*업체|제재를\s*받지\s*않은', context)
            )
            if not office_bound and not direct_local_bidder:
                continue
            if office_bound and not (re.search(r"소재|둔|두고|있는", suffix) and re.search(r"업체|갖춘\s*자", suffix)):
                continue
            if re.search(r"견적|수의계약|해제|지역제한\s*없", context):
                continue
            if price >= ceiling:
                continue
            evidence = clean_evidence(context, rec)
            if evidence:
                return {"item": 6, "value": 1, "evidence": evidence,
                        "source": "국가 시행규칙25조③ / 지방 시행규칙25조③",
                        "estimated_price": price, "ceiling": ceiling,
                        "regional_price_bounds": bounds,
                        "matched_region_token": token.text}
        # The official input also carries the registered restriction regions.
        # Their r-symbols cannot be matched to visible names, but an all-basic
        # structured list plus an independently observed operative office
        # restriction is sufficient for item 6.  Keep the source clause as the
        # evidence and use the metadata only for its declared administrative
        # unit; a delivery location or an unbound region flag is insufficient.
        if structured_basic_scope and price < min(ceiling, 230_000_000):
            for office in re.finditer(r'본점|주된\s*(?:영업소|사무소)|본사', text):
                target = assertion_scope(text, office.start(), office.end(), 'region')
                n = re.sub(r'\s+', '', target)
                if unresolved_assertion(target):
                    continue
                if not (re.search(r'(?:소재지|소재|둔|두고|있는)', n)
                        and re.search(r'(?:업체|사업자|갖춘자|이어야|제한)', n)):
                    continue
                if re.search(r'납품(?:지|장소)|사업현장|공사현장|운행구간|대상시설', n):
                    continue
                evidence = clean_evidence(target, rec)
                if evidence:
                    return {"item": 6, "value": 1, "evidence": evidence,
                            "source": "official_structured_region_units_and_observed_office_clause",
                            "estimated_price": price, "ceiling": ceiling,
                            "regional_price_bounds": bounds,
                            "registered_region_units": [token.attribute('단위') for token in registered],
                            "region_symbols_are_record_local": True}
    return None


def joint_share_check(rec):
    """Article 9 / local joint-contract guideline: explicit minimum shares.

    Missing share wording alone is not labeled a violation. The requirement
    concerns each joint-performance member, not the lead member or a divided
    performance agreement. No corpus statistics or IDs are used.
    """
    if has_withdrawal(rec, 'share'):
        return None
    scope = applicable_law(rec)
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
            if unresolved_assertion(assertion_scope(text, match.start(), match.end(), 'share')):
                continue
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


def apply_rules(rec, row, knowledge=None, *, comparison=None, items=tuple(range(1, 25))):
    result = dict(row)
    wanted = set(items)
    from .regions import multiple_region_check, above_ceiling_region_check
    checks = [fn(rec) for k, fn in ((21, joint_share_check), (6, narrow_region_check),
                                  (7, multiple_region_check), (5, above_ceiling_region_check)) if k in wanted]
    if 1 in wanted:
        from .eligibility_restrictions import direct_facility_ownership_check
        checks.append(direct_facility_ownership_check(rec))
    # Dates and metadata extraction only prove specific violations. Their
    # explicit negatives or abstentions cannot certify a whole legal item.
    if wanted & {23, 24}:
        checks.extend(check for key, check in temporal_checks(rec).items()
                      if int(key[1:]) in wanted
                      and (check["value"] == 1
                           or key == 'v23' and check.get('reason') == 'urgent_intervals_sufficient')
                      and (key != 'v24' or comparison is None))
    if comparison is not None and 24 in wanted:
        from .comparison import positive_decision
        checks.append(positive_decision(rec, comparison))
    performance = performance_facts(rec) if wanted & {2, 3, 4, 8} else {'overlays': {}}
    from .performance import validate_model_witness
    for k in sorted(wanted & {2, 3, 4, 8}):
        checks.append(validate_model_witness(rec, row, k, performance))
    from .v2_quote_check import check_item as local_quote_check
    if 2 in wanted:
        checks.append(local_quote_check(rec, performance, 2))
    if 8 in wanted:
        checks.append(local_quote_check(rec, performance, 8))
    for item, decision in performance["overlays"].items():
        if int(item[1:]) not in wanted:
            continue
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
    other = other_checks(rec, wanted)
    if 19 in wanted:
        from .pledge_witness import validate_model_witness as validate_pledge_witness
        checks.append(validate_pledge_witness(rec, row, other['v19']['facts']))
    for item, decision in other.items():
        if decision["value"] is not None:
            checks.append({"item": int(item[1:]), "value": decision["value"],
                           "evidence": clean_evidence(decision["evidence"], rec),
                           "reason": decision["reason"], "source": "supplied_pledge_SW_briefing_rules"})
    if knowledge is not None and wanted & set(range(10, 19)):
        for item, decision in knowledge.sme_record_facts(rec)["decisions"].items():
            k, value = int(item[1:]), decision["value"]
            if k not in wanted:
                continue
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
