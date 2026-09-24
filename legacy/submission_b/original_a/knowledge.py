"""Reference material is read exclusively from the competition data directory."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from .retrieval import QUERIES
from .products import ProductFacts
from .sme import extract_sme_facts

GUIDANCE = {
    1: "입찰 가능한 기관 유형 자체를 특정 기관·대학·산학협력단 등으로 부당하게 한정하는지 확인. 단순 발주기관명, 제출처, 비영리법인 추가 허용과 구별한다.",
    2: "실적을 참가자격의 필수조건으로 요구하는지와 해당 계약의 금액·유형·예외를 확인. 평가 배점용 실적, 서식 제목만으로 참가 제한을 단정하지 않는다.",
    3: "참가 필수 실적의 금액·규모를 현 사업 기준과 같은 단위로 대조. 사업예산 기준이라는 항목 비고를 반영. 단일 건·합산·부가세·배수를 구별한다.",
    4: "금액 적용 범위를 확인한 뒤 특정 발주기관의 실적만 인정하거나 실질적으로 같은 실적을 배제하는 조건을 찾는다. 단순 유사업무 수행경험과 구별한다.",
    5: "지역제한에 적용되는 국가·지방 및 기관 유형별 금액을 구별한다. 판로지원법의 우선조달 고시금액을 지방 지역제한 상한으로 일괄 사용하지 않는다.",
    6: "참가업체 본점·영업소를 광역 시도보다 좁은 시군구로 제한하는지 확인. 단순 납품장소는 지역제한이 아니다. 지방 소액수의 예외를 확인한다.",
    7: "여러 시도로 지역을 확대한 제한을 찾고 인접 지역 납품·사업범위·자격업체 수 등 허용 사유를 확인. 무조건 모든 복수 지역을 위반 처리하지 않는다.",
    8: "실적과 지역이 동시에 참가 필수자격인지 확인. 중소기업 제한·업종 등록과의 병용 자체는 이 항목이 아니다. 법정 예외를 함께 확인한다.",
    9: "첨부 규격서·과업지시서에서 특정 모델·제조사·상표의 납품을 요구하는지 확인. 기존 보유 장비의 설명과 신규 구매조건, 동등 이상 허용과 배제를 구별한다.",
    10: "대상 제품이 제공 고시의 경쟁제품인지 먼저 판단하고 참가자격의 직접생산 보유 요건을 검토. 제출서류 목록·일반 경고의 단순 언급과 실질 자격요건을 구별한다.",
    11: "경쟁제품 해당 여부와 중소기업자 참가요건을 검토. 중소기업공공구매 종합정보망 주소가 있다는 것만으로 중소기업 제한이 기재됐다고 간주하지 않는다.",
    12: "직접생산을 참가요건으로 요구하는 대상 품목을 특정하고 고시 목록·특이사항과 대조. 메타 품명 누락만으로 일반제품이라 단정하지 않는다.",
    13: "경쟁제품 입찰을 중소기업 전체보다 좁은 소기업·소상공인만으로 제한했는지 검토. 중소기업 문구와 소기업 확인서 문구의 모순도 확인한다.",
    14: "일반 물품·용역이고 우선조달 고시금액 이상인데 중소기업 참가 제한을 요구하는지 검토. 경쟁제품과 법정 예외를 구별한다.",
    15: "일반 물품·용역에서 추정가격 1억원 이상~우선조달 고시금액 미만인데 소기업만 허용하는지 확인. 중기업 허용 여부와 확인서 조건을 함께 읽는다.",
    16: "동일 금액구간의 일반 물품·용역에서 중소기업 참가 제한이 누락됐는지 확인. 명시된 판로지원 예외·비영리 참가 허용 등 적용 사유를 검토한다.",
    17: "1억원 미만 일반 물품·용역에서 소기업·소상공인보다 넓은 중소기업을 허용하는지 검토. 소기업 부족·유찰 등의 예외가 있으면 적용을 검토한다.",
    18: "1억원 미만 일반 물품·용역에서 소기업·소상공인 참가 제한이 빠졌는지 확인. 예외 기재 여부와 계약유형을 반드시 확인한다.",
    19: "제조사 물품공급·기술지원 확약서의 발급·보유·제출 시점을 구별. 입찰 전 발급 의무는 계약 때 제출한다고 해도 검토 대상. 낙찰 후 발급·제출과 구별한다.",
    20: "실제 SW 사업인지 확인하고 사업금액 구간별 대기업·상호출자제한기업 참가제한 및 근거 기재를 검토. SW사업자 등록요건만으로 하한제도 안내를 대체하지 않는다.",
    21: "공동이행 구성원의 최소지분율을 국가·지방 기준과 대조. 국가 일반 공동이행 10%, 지방 5% 기준과 명시적 예외·조정, 분담이행 제외를 구별한다.",
    22: "협상에 의한 계약에만 적용. 현장·사업·제안요청 설명회 참석을 참가자격 또는 제안서 제출 필수조건으로 삼았는지 확인. 선택 참석·미개최는 구별한다.",
    23: "지방계약의 협상계약에만 적용. 실제 설명회가 있을 때 공고일~설명회 및 설명회~제안서 마감 간 기간을 금액구간별 규정과 대조한다.",
    24: "동일 개념의 공고문 값과 메타를 대조. 추정가격과 부가세 포함 예산의 차이, 제한경쟁과 협상 낙찰방법의 차이를 모순으로 오인하지 않는다. 명백한 불일치를 찾는다.",
}

ALIASES = {
    "국가계약법 시행규칙": "국가를 당사자로 하는 계약에 관한 법률 시행규칙.txt",
    "국가계약법 시행령": "국가를 당사자로 하는 계약에 관한 법률 시행령.txt",
    "지방계약법 시행규칙": "지방자치단체를 당사자로 하는 계약에 관한 법률 시행규칙.txt",
    "지방계약법 시행령": "지방자치단체를 당사자로 하는 계약에 관한 법률 시행령.txt",
    "판로지원법 시행령": "중소기업제품 구매촉진 및 판로지원에 관한 법률 시행령.txt",
    "판로지원법": "중소기업제품 구매촉진 및 판로지원에 관한 법률.txt",
    "공동계약": "(계약예규) 공동계약운용요령.txt",
    "집행기준": "(계약예규) 정부 입찰·계약 집행기준.txt",
    "지방집행기준": "지방자치단체 입찰 및 계약 집행기준.txt",
    "지방낙찰기준": "지방자치단체 입찰시 낙찰자 결정기준.txt",
    "SW지침": "중소 소프트웨어사업자의 사업 참여 지원에 관한 지침.txt",
    "고시금액": "국가를 당사자로 하는 계약에 관한 법률 등의 재정경제부장관이 정하는 고시금액.txt",
}


class Knowledge:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.table = json.loads((self.data_dir / "항목표.json").read_text(encoding="utf-8"))["항목"]
        product_path = self.data_dir / "법령패키지/중기부고시/중기부고시_경쟁제품_세부품명.csv"
        with product_path.open(encoding="utf-8-sig", newline="") as f:
            self.products = {r["세부품명번호"]: r for r in csv.DictReader(f)}
        self.laws = {alias: (self.data_dir / "법령패키지/법령" / name).read_text(encoding="utf-8")
                     for alias, name in ALIASES.items()}
        self._product_facts = None

    def detailed_product_facts(self, rec):
        if self._product_facts is None:
            self._product_facts = ProductFacts(self.data_dir / "법령패키지/중기부고시/중기부고시_경쟁제품_세부품명.csv")
        return self._product_facts.extract(rec, top_k=3)

    def sme_record_facts(self, rec):
        if self._product_facts is None:
            self._product_facts = ProductFacts(self.data_dir / "법령패키지/중기부고시/중기부고시_경쟁제품_세부품명.csv")
        return extract_sme_facts(rec, self._product_facts)

    def qualification_decisions(self, rec, row):
        from .qualification import infer
        if self._product_facts is None:
            self._product_facts = ProductFacts(self.data_dir / "법령패키지/중기부고시/중기부고시_경쟁제품_세부품명.csv")
        return infer(rec, row, self._product_facts)

    def for_response(self, rec, response):
        from .notice_knowledge import NoticeKnowledge
        self.detailed_product_facts(rec)
        return NoticeKnowledge(self, rec, response)

    def product_matches(self, rec):
        text = "\n".join(d["text"] for d in rec["docs"])
        meta = json.dumps(rec["meta"].get("세부품명번호목록"), ensure_ascii=False)
        result = []
        for code in sorted(set(re.findall(r"(?<!\d)\d{10}(?!\d)", text + "\n" + meta))):
            p = self.products.get(code)
            result.append({"코드": code, "고시등재": bool(p), "메타기재": code in meta,
                           **({"품명": p["세부품명"], "특이사항": p["특이사항"]} if p else {})})
        # Name matches assist cases whose meta lacks commodity codes; do not assert identity.
        compact = re.sub(r"\s+", "", text)
        names = []
        for p in self.products.values():
            name = re.sub(r"\s+", "", p["세부품명"])
            if len(name) >= 5 and name in compact:
                names.append({"고시품명": p["세부품명"], "코드": p["세부품명번호"], "특이사항": p["특이사항"]})
        return {"코드대조": result[:30], "명칭언급_동일품목여부확인필요": names[:12],
                "주의": "코드·명칭이 실제 조달 대상인지와 특이사항을 본문에서 확인. 매칭 없음은 일반제품이라는 확정이 아님."}

    def _article(self, alias, article):
        text = self.laws[alias]
        # Match the first current main-text article, not later amendments or form samples.
        m = re.search(r"^" + re.escape(article) + r"\(", text, re.M)
        if not m:
            return ""
        following = re.search(r"^제\d+조(?:의\d+)?\(", text[m.end():], re.M)
        end = m.end() + following.start() if following else len(text)
        return text[m.start():end].strip()

    def legal_context(self, rec, items, max_chars):
        local = "지방" in str(rec["meta"].get("적용계약법", ""))
        scope = "지방계약법" if local else "국가계약법"
        candidates = []
        if any(k in items for k in range(1, 9)):
            candidates.append((scope + " 시행규칙", "제25조", self._article(scope + " 시행규칙", "제25조")))
        if 5 in items and local:
            candidates.insert(0, (scope + " 시행규칙", "제24조", self._article(scope + " 시행규칙", "제24조")))
        if any(k in items for k in range(14, 19)):
            for article in ("제2조의2", "제2조의3"):
                candidates.append(("판로지원법 시행령", article, self._article("판로지원법 시행령", article)))
        if 19 in items:
            candidates.append(("집행기준", "제5조의3", self._article("집행기준", "제5조의3")))
        text = "\n".join(d["text"] for d in rec["docs"])
        if 21 in items and any(w in text for w in ("공동수급", "공동이행")):
            if local:
                law = self.laws["지방집행기준"]
                pos = law.find("구성원별 계약참여 최소지분율")
                if pos >= 0:
                    candidates.insert(0, ("지방집행기준", "공동수급체", law[max(0, pos-80):pos+520]))
            else:
                candidates.insert(0, ("공동계약", "제9조", self._article("공동계약", "제9조")))
        if 20 in items and any(w in text for w in ("소프트웨어", "SW사업", "정보화")):
            candidates.insert(0, ("SW지침", "제3조", self._article("SW지침", "제3조")))
        if 23 in items and local and "설명" in text:
            law = self.laws["지방낙찰기준"]
            pos = law.find("제안요청서 설명은 제안서 제출마감일")
            if pos >= 0:
                candidates.insert(0, ("지방낙찰기준", "협상 제안요청서", law[max(0,pos-75):pos+330]))
        # Extract legal paragraphs, not a truncated prefix of every long article.
        terms = set(q for k in items for q in QUERIES[k])
        blocks = []
        for alias, article, content in candidates:
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            ranked = sorted(enumerate(lines), key=lambda p: (-sum(q in p[1] for q in terms), p[0]))
            chosen = sorted(i for i, _ in ranked[:3])
            body = "\n".join(lines[i] for i in chosen)
            blocks.append(f"[{ALIASES[alias]} / {article} 발췌]\n{body}")
        out = []
        used = 0
        for block in blocks:
            if used + len(block) > max_chars:
                continue
            out.append(block)
            used += len(block)
        return "\n\n".join(out)

    def legal_context_v2(self, rec, items, max_chars, *, return_metadata=False):
        """Opt-in, source-linked context; diagnostics are available without changing callers."""
        from .legal_context import build_legal_context

        packet = build_legal_context(rec, items, max_chars, self.laws, self.table, ALIASES)
        return packet if return_metadata else packet["text"]

    def item_instructions(self, items):
        return "\n".join(f"v{k} {self.table[f'v{k}']['항목명']}: {GUIDANCE[k]}" for k in items)
