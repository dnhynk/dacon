"""Interpretation switches for preregistered LB probes. The defaults are the submitted C2 behaviour; a probe package
differs from C2 only in this file."""

# v10·v11·v13 judged on software services (scope family sw) as on other competition services.
SW_SERVICE_COMPETITIVE = True
# v20 judged only at or above this 추정가격 (0 = every amount, 지침 제3조②).
V20_MIN_ESTIMATE = 0
# v11 judged on 수의계약 too (판로지원법 제7조① names 입찰; default excludes 수의계약).
V11_PRIVATE = False
# v14–v18 judged on 수의계약 too. False since LB probe P3a (2026-09-25: P3a − C2 = +0.0039; the organizer does not mark them there).
SIZE_PRIVATE = False
# "동등 이상" leaves a designation a v9 violation (approved literal default).
V9_EQUIVALENT_VIOLATION = True
# v10 judged on 수의계약 too (item "경쟁제품 입찰 직생 없음" names 입찰, as v11 does).
V10_PRIVATE = True
# v20 judged on 수의계약 too (item "입찰참가자격 (SW)"; 지침 제3조② is about the 입찰공고).
V20_PRIVATE = True
# v19 judged on 수의계약 too (item "물품공급 확약서 입찰 시 제출"). False = bids only (probe).
V19_PRIVATE = True
# Probe: v9 ignores operating-system and office-suite names as designation evidence and skips an empty "모델명 :" label.
# False = current behaviour.
V9_NOISE = False
# Probe (audit RA): v9 read by the model2 family (families.MODEL2: what the named maker or model is in this contract, and how
# the line asks for it) instead of model; fires on a designation of a delivered item or of the equipment or SW the contractor
# uses, never on serviced or compatible existing equipment or on names that are no maker or model. Changes model requests:
# verify on GPU before a probe. False = current behaviour.
V9_READ2 = False
# Probe (audit RA): v9 second stage — the v9obj family is asked only on the lines v9 takes from the model family's reading and
# drops those naming serviced or compatible existing equipment or no maker or model. Adds model requests after the other
# families: verify on GPU before a probe. False = current behaviour.
V9_STAGE2 = False
# Probe (expert audit X3): v9 drops clause-level 동등 이상 references, SW licence objects, consumables and installed-equipment
# upgrades, service contracts, and grade/colour/flight/same-maker/empty-label/platform noise. False = current behaviour.
V9_X3 = False
# Probe (expert audit X3 direction b): v9 also fires on a vehicle purchase naming the car model or a goods line naming a
# maker's brand product, without 동등 wording. False = current behaviour.
V9_X3B = False
# Probe: goods whose every listed 세부품명 is software are an SW사업 by default without the SW사업자 licence condition
# (SW진흥법 §2 counts 유통; audit L-C). False = current behaviour.
SW_GOODS_ANY = False
# Probe only: (item, field, value) cells written as 0, where field is a key of judge.segment_of (work, method, award, law,
# band, attach). Empty in the canonical package.
SEGMENT_OFF = ()
# v16/v18 fire only when 나라장터 registers a size restriction the notice omits (판로지원법 시행령 제2조의3②). False = P3a.
SIZE_ABSENCE_NEEDS_REG = False
# 추정가격 below this is a unit price or placeholder and counts as unknown (0 = P3a: any value is used).
PRICE_FLOOR = 0
# Audit fixes (runs/rebuild_c/transfer_20260925/audit/A–F.md): general reading, scope and data fixes from the out-of-sample
# corpus audit, applied together so that one probe measures them. False = P3a.
AUDIT_FIXES = False
# Round-2 audit fixes (runs/rebuild_c/transfer_20260925/audit/R2-A..E.md): reading, scope and data fixes found on the
# test-mix sample t2500 after round 1, applied together so that one probe measures them. False = current behaviour.
AUDIT_FIXES2 = False
# With AUDIT_FIXES2: an issuer-named pledge item the model left without a time counts as pre-award when it sits in the
# 공고문 QUAL section or a pre-award submission list. Off by default: dev labels disagree (DEV-037 1, DEV-050 0).
V19_LIST_STAGE = False
# DATASET-FIT probe only: v5–v7 skip a text restriction that equals the registered sido set, v17 skips SME-level registrations.
REG_CONSISTENCY = False
# Round-3 audit fixes (runs/rebuild_c/transfer_20260925/audit/R3-A..C.md): reading, scope and data fixes found on t2500
# after round 2, applied together. CPU-only: model prompts stay those of the current code. False = current behaviour.
AUDIT_FIXES3 = False
# With AUDIT_FIXES3: the round-3 fixes that change model prompts — region candidates for 공고문 qualification lines naming the
# bidder's location (families.region_select), "유경험 업체" performance candidates (families.perf_select, perf_default) and
# project titles from table header rows (catalog.project_titles, shown in every prompt). False = prompts unchanged.
FIX3_PROMPTS = False
# Probe: v9 also fires on a line the model read as designating the procured item when it carries a model code, without
# designation wording (talkboard: 규격서 등에 특정 모델명·제조사명 명시 → 성립). False = current behaviour.
V9_BROAD = False
# Probe: items judged with AUDIT_FIXES and AUDIT_FIXES2 on (AF1_ITEMS: AUDIT_FIXES only) while the bundle (sections,
# candidates, prompts) stays as the global switches build it. () = current behaviour.
AF_ITEMS = ()
AF1_ITEMS = ()
# Probe (audit RD): items judged with AUDIT_FIXES, AUDIT_FIXES2 and AUDIT_FIXES3 on; () = current behaviour.
AF3_ITEMS = ()
# Probe: with AUDIT_FIXES, a list marker such as "(1)" opening an attachment's qualification line is not form wording.
# False = current behaviour.
AF_LIST_MARKER = False
# Probe: v10·v11·v13 also judged on goods whose every listed 세부품명 is a competition product (the literal rule has no
# goods exclusion). () = current behaviour (services only); e.g. ('v10', 'v11', 'v13').
COMPETITIVE_GOODS = ()
# Probe (expert audit X4): v10·v11·v13 skip 급식·간식 baskets registered under a food 세부품명 and products outside the designation's
# 특이사항; v10·v11 read a 시행령 제7조 exception in any document; v13 skips a registered 제7조의2 small-only basis. False = current.
X4_OBJECT = False
# Probe (audit RB): competition-product scope (catalog.classify) follows the audit rounds 1–3 identification rules — 조항호
# designation, title labels, 디자인 plans/IP, 감리, 회의록, 저수조 licences, SW objects — and nothing else changes. False =
# current behaviour.
SCOPE_FIXES = False
# Probe (audit RB): v11 takes a 공고문 eligibility clause ("…소상공인으로서 … 확인서를 소지한 업체") or a declared "소기업 또는
# 소상공인 간 우선조달계약 대상" as the SME restriction when the model read none. False = current behaviour.
V11_ELIGIBLE = False
# Made-violation recall fixes from the paraphrase audit (audit/P-R.md); each False = current behaviour.
# v19: a pledge to be held or obtained by the bid deadline is timed before the award (항목표 비고 "입찰 전 발급, 계약시 제출").
V19_HOLD_BY_BID = False
# v11/v16/v18: certificate-validity lines alone do not make a size restriction present (talkboard).
CERT_ONLY_ABSENT = False
# Region mentions: a short 시·도 alias followed by a case particle ("전남의", "서울시인") names the region.
REGION_PARTICLE = False
# v6: a stated restriction naming no 기초 unit, while 제한지역코드목록 registers 기초 units, is judged at 기초 level.
V6_REG_BASIC = False
# Probe (audit RE): v6 also fires on a 기초-level restriction registered on 나라장터 below T, with or without a restriction line
# (dev DEV-066). False = current behaviour.
V6_META_BASIC = False
# Probes (audit RG), each False = current behaviour: v7 also takes a 2+ 시·도 restriction registered on 나라장터 below T, with or
# without a text line;
V7_META_MULTI = False
# v7 also reads a qualification or 공고문 BID-section clause (wrapped lines joined) stating the bidder's location with 2+ 시·도.
V7_CLAUSE = False
# Probes (expert audit X7), each False = current behaviour: V6_LABEL_BASIC — v6 reads a "지역제한: 여주, 양평" label of 시·군 names;
# V6_ORDERER_ANY — v6 reads the bidder's 본점 "[수요기관(기초자치단체)]내" in any 공고문 section; V6_OFFICE_DUTY — v6 does not take
# an office-setup duty from the start of the work as the bidder's location; V7_SITE_SPAN — v7 does not fire when the work's site
# spans the restricted 시·도 (시행규칙 제25조③1).
V6_LABEL_BASIC = False
V6_ORDERER_ANY = False
V6_OFFICE_DUTY = False
V7_SITE_SPAN = False
# Probe: v24 judged with the audit fixes 1–3 and REGION_PARTICLE (v24 only), licence codes read from the whole clause, and an
# exact VAT relation (stated = registered × 1.1 or ÷ 1.1) counted as agreement (audit V24). False = current behaviour.
V24_FIXES = False
# Probe: v24 without the contract-method axis (body-text 계약방법 vs meta). False = current behaviour.
V24_NO_METHOD = False
# Probes (audit V24R): v24 also reads amounts under other budget labels and in vertical tables (never 기초금액), transposed
# digits in amounts written without 원, and licence codes after an industry name. False = current behaviour.
V24_AMOUNT_WIDE = False
V24_TRANSPOSED_WIDE = False
V24_LICENSE_WIDE = False
# Probe (audit fable_v24 S1): v24 also compares each 기초금액 the 공고문 states with the nearest registered amount (B, 1.1·P, P,
# B/1.1): silent when one agrees within 5%, fires at 0.5–0.95× or 1.05–2×. False = current behaviour.
V24_BASE_ZONE = False
# Families read with the reasoning channel on (main.py; --thinking-families overrides; budget --thinking-budget, 256).
# () = thinking off (current behaviour). Changes model requests and server time: verify on GPU before a probe.
THINKING_FAMILIES = ()
# Probe: v12 no longer takes the model's "과업과 같은 종류" reading when the notice title matches none of the title words
# (catalog.SERVICE_FAMILIES) of the service products the certificate clause cites. False = current behaviour.
V12_TITLE_OBJECT = False
# Probe (audit RA, with V12_TITLE_OBJECT): an SW certificate (the sw family has no title words) also yields to an informative title
# that names no SW work (catalog.sw_service_title, as classify_service tests the sw family). False = current behaviour.
V12_TITLE_SW = False
# Probes (expert audit X3), each False = current behaviour: V12_X3 skips a 직접생산 demand that offers another route or only
# verifies a certificate; V12_MANUF fires on a manufacturer-only demand in a goods purchase outside the competition products.
V12_X3 = False
V12_MANUF = False
# Probe: v12 does not follow the model's "과업과 같은 종류" reading when the title's object is research, education, a training
# course, a school trip or lodging (dev DEV-053, DEV-056). False = current behaviour.
V12_TITLE_OBJECT2 = False
# Probe (audit RC): v19 takes the round-2/3 pledge-document checks (judge.pledge_document, pledge_sentence: the clause names a
# supply/support pledge document, and issuer and document share a sentence without the bidder's own undertakings) without
# the rest of AUDIT_FIXES2/3. False = current behaviour.
V19_PLEDGE_FIXES = False
# Probe (expert audit X6): v19 fires only on a pledge demanded at or before the bid or in a bid-document list (집행기준 제5조의3
# ③: 적격심사-stage, post-award and untimed capability demands are practice), and only on a 확약. False = current behaviour.
V19_STAGE = False
# Probe (expert audit X6 C19a): v19 counts a pledge demand as timed when the CPU stage check places it at or before the bid,
# although the model read no time. False = current behaviour.
V19_CPU_TIMED = False
# Probe (audit RC): the SW-scope reading behind v20 (facts.sw_*; it feeds only v20) and the 제48조 statement use the round-2/3
# audit fixes while other items keep AUDIT_FIXES2/3 as set. CPU-only. False = current behaviour.
V20_SCOPE = False
# Probe (expert audit X6 C20a): v20's SW scope refuses a title whose object is teaching, a programme run, content, insurance,
# recruitment, telemetry upkeep, an ISO management system, a data-provider selection or equipment distribution, and v20
# takes the 시행령 제41조 statement form. CPU-only. False = current behaviour.
V20_CONTENT = False
# Probe (expert audit X6 C20b): v20 skips orderers outside 시행령 제21조 (대학, 협회, 중앙회, 위원회, 조합, 연합회, 학회 tokens).
# False = current behaviour.
V20_ORDERER = False
# Probes (audit RE), each False = current behaviour:
# v22 does not take a briefing line in a presentation context (the proposer's 발표, 평가위원, 기술·제안서 평가) as the orderer's
# briefing unless it names one (dev DEV-193, DEV-054 = 0); probe with AF_ITEMS including 'v22'.
V22_PRESENTATION = False
# The 1.5억 regional threshold applies to 시설물안전법 inspections only (the AUDIT_FIXES regex), not to 산업안전 services.
T_SAFETY_STATUTE = False
# With no qualification-section restriction and no registration, v5 takes a 공고문 NOTE/OTHER/BID line the model read as the
# bidder-location restriction when it names the bidder's location with a firm subject.
V5_ANY_SECTION = False
# v3 counts a required record equal to the budget (항목명 "1배수 이상") and skips 신인도 lines; probe with AF_ITEMS including 'v3'.
V3_EXACT = False
# Probe (expert audit X2): v3 takes the single-record amount of "단일 … 또는 누적 …" (집행기준 제5조①: one past contract).
# False = current behaviour.
V3_DISJUNCTIVE = False
# v23 counts "전일부터 기산하여 N일 전": a gap of N days is short (current: < N).
V23_LEGAL_COUNT = False
# Probes (audit RF), each False = current behaviour: v17 reads the 제2조의2 ①1 단서 widening in every document line (failed
# small-firm bid next to a size word, a 제1호 가목·나목·단서 citation, or an explicit widening to 중기업 under 판로지원).
V17_WIDEN_ANYWHERE = False
# v16/v18: the 나라장터 method header and an empty 대기업/중소기업 checkbox are no size restriction (DEV-039).
SIZE_TAG_NOT_QUAL = False
# Probes (expert audit X5, runs/rebuild_c/transfer_20260925/audit/expert/X5/REPORT.md), each False = current behaviour:
# A v16/v18 skip 폐기물 처리 용역 (운영요령 제44조제3호; dev DEV-149, DEV-151 = 0);
X5_WASTE_EXEMPT = False
# B v14–v18 skip an SW사업 below 20억 (SW진흥법 제48조②, 지침 별표1; dev DEV-132 v16 = 0);
X5_SW_EXEMPT = False
# C v16/v18 take exception wording in any document (의무적용 대상 아님, 제2조의3 제1항 제N호 예외, 유찰로 인한 완화);
X5_EXC_WORDING = False
# D v14/v15/v17 skip a 직생 requirement read as the procured work that cites a listed non-SW 경쟁제품 (판로지원법 제7조);
X5_DP_LISTED_ANY = False
# E v17 reads 소기업·소상공인 with 벤처·창업기업 companions, and "(소기업,소상공인으로 제한)", as the small class;
X5_V17_CLASS = False
# F v14/v15/v17 do not fire on the 나라장터 method tag or a header line alone (DEV-039);
X5_TAG_NOT_POSITIVE = False
# G v16/v18 skip operator-pays contracts, and v14–v18 skip notices titled 수의계약 or 견적 (DEV-144);
X5_NOT_PROCUREMENT = False
# H v16/v18 skip insurance contracts (probe only: no dev case);
X5_INSURANCE_EXEMPT = False
# adds: v16/v18 treat a notice whose only restriction lines are method statements as unrestricted (DEV-039);
X5_METHOD_STATEMENT = False
# adds: v14/v15/v17 read a bid-section 판로지원법 제2조의2 declaration when the qualification section states no class.
X5_DECL_BID = False
# Probes (audit RD), each False = current behaviour: v1 fires on a qualification clause requiring a facility in a named region
# (DEV-072 form);
V1_REGION_FACILITY = False
# a buyer word followed by a certifying verb ("국가에서 인증받은 …") names the certifier, not the record's buyer;
BUYER_CERTIFIER = False
# 공고문 qualification lines stating a held record that the perf family never selected count as record requirements.
PERF_UNREAD = False
# Probes (expert audit X1: runs/rebuild_c/transfer_20260925/audit/expert/X1/REPORT.md), each False = current behaviour:
# v1 skips a type obtained by a statutory 인가·등록·허가·지정·신고 ("…으로 인가를 득한 기관"; 시행령 제12조①2);
V1_REGISTERED_TYPE2 = False
# v1 skips an institution line whose option list ("…중 하나를 갖춘") has a commercial or licensed-business option;
V1_LEAD_OPTIONS = False
# v1 skips institution-type lines in 지방 소액수의 견적 (지방 집행기준 제5장 제3절 1.나.6)아));
V1_LOCAL_PRIVATE_INST = False
# v1 fires on a qualification line naming two or more institution types as the only eligible bidders (DEV-058 form);
V1_INST_LIST = False
# v4: a buyer enumeration that names a private party admits private records (정부 집행기준 제5조④3);
V4_PRIVATE_ENUM = False
# v4 fires on a facility-kind orderer token directly followed by a delivery or operation record.
V4_TOKEN_BUYER = False
# Probes (expert audit X2), each False = current behaviour: v2 and v8 skip a third-party record clause, a statutory definitional
# record (공익활동실적) and a 신인도 line;
X2_RECORD_NOISE = False
# v2 also takes held-record requirements the perf family never selected, in firm-level wording read on the clause
# ("…경력을 보유한 기관", "…수행한 소기업") and
X2_HELD_RECORD_X = False
# under an attachment's 참가자격 heading (no staffing table);
X2_ATTACH_QUAL = False
# v8 takes those records too, and a CPU-read bidder location when no region line was read and none is registered (λ_v8 unknown).
X2_V8_ADDS = False
# v24 전용 판독 단계(pps_c/v24, runs/rebuild_c/v24_pipeline/DESIGN_V24.md): 모델이 공고문의 예산·추정가격·추정금액·기초금액·계약방법·지역제한·업종
# 표기를 그대로 옮기고 CPU가 meta와 비교한다. 발화하면 기존 v24 경로보다 먼저 그 줄을 반환하고, 아니면 기존 경로로 간다(합집합).
# False = current behaviour.
V24_PIPELINE = False
# v24p 발췌에 첨부(과업지시서·제안요청서·규격서)의 예산 표기 줄(최대 10줄)도 넣는다. fable_v24 S5: 단독 발화는 손익분기라 측정용.
V24P_ATTACH = False
# Probe (audit PX, runs/rebuild_c/transfer_20260925/audit/expert/PX/REPORT.md "V24W1P"): v24 guards against natural artifacts
# of the wide CPU axes and the v24p reading — a bare table cell under a larger total of its own field equal to meta (component
# row), a stated amount within 0.01% of the registered one (rounding), a budget line that calls itself an estimate subject to
# change (개산 note), and a licence code in one item of an "any one of" list (alternative eligibility). False = current behaviour.
V24P_GUARDS = False
