"""Interpretation switches for preregistered LB probes. The defaults are the submitted C2 behaviour; a probe package
differs from C2 only in this file."""

# v10·v11·v13 judged on software services (scope family sw) as on other competition services.
SW_SERVICE_COMPETITIVE = True
# RT-Q: opt-in source-reading correction.
V20_SME41_STATEMENT = False
# RT-Q: opt-in source-reading correction.
V19_BIDDER_AS_ALTERNATIVE = False
# RT-Q: opt-in source-reading correction.
V19_STAGE_ITEM_BOUNDARY = False
# RT-Q: opt-in source-reading correction.
V19_PRE_AWARD_ROLE = False
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
# Extend V9_X3 with component floors, open example lists and the 등등 equivalence typo.
# Effective only together with V9_X3; default-off experiment.
V9_X3_PLUS = False
# Quote a stated bidder-region clause for positive v6/v7 cells with no evidence.
# Evidence only; never changes a verdict and never invents a metadata quote.
EVIDENCE_REGION_FILL = False
# Probe (expert audit X3 direction b): v9 also fires on a vehicle purchase naming the car model or a goods line naming a
# maker's brand product, without 동등 wording. False = current behaviour.
V9_X3B = False
# Continue the existing v9 brand/code fallbacks when x3b finds no evidence.
V9_FALLBACK_CHAIN = False
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
# Literal supplied-goods model codes across adjacent physical lines.
RTD_V9_WRAPPED_MODEL = False
# Literal maker/model fields and named supplied computer components.
RTD_V9_SPEC_FIELDS = False
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
# 컴퓨터서버: a specification naming only a non-x86 processor is outside the designation (특이사항 'x86 서버'). False = current.
X4_SERVER_ARCH = False
# Probe (audit RB): competition-product scope (catalog.classify) follows the audit rounds 1–3 identification rules — 조항호
# designation, title labels, 디자인 plans/IP, 감리, 회의록, 저수조 licences, SW objects — and nothing else changes. False =
# current behaviour.
SCOPE_FIXES = False
# v10·v11·v13 (items listed here) keep a service object only when catalog.classify under SCOPE_FIXES also finds
# the listed service (디자인 plans and IP rights, 감리, 회의록, 저수조 licences, SW objects and management systems are not the
# listed service); objects that classification adds are not taken. () = current behaviour.
COMP_SCOPE_EXCLUDE = ()
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
# Red team R3 (switch V7_ADJACENT): below T, v7 also fires on a bidder-location clause that extends the region to adjacent
# 시·도 without naming them ("경상남도 또는 인접 시·도", "관할 시·도 및 인접 시·도") or names a multi-시·도 region group
# (수도권, 충청권, 호남권, 영남권, 동남권, 대경권); 지방계약법 시행규칙 제25조③ (인접 시·도는 예외 사유가 있을 때만).
# False = current behaviour.
V7_ADJACENT = False
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
# User-directed budget-axis hypothesis: a stated 기초금액 >= 1,000,000 won
# disagrees when no labelled notice budget amount equals registered 입찰추정가격.
# 'base' applies to all awards; 'nego' requires 낙찰방법 == 협상에의한계약.
# VAT-inclusive amounts are compared literally; False preserves current behaviour.
V24_BUDGET_VS_P = False
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
# Independent X3 filters. Final candidate: legacy=False, ALT=True, VERIFY=False.
V12_X3_ALT = False
V12_X3_VERIFY = False
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
# v3: a 공고문 line outside the evaluation text and the document list that sets the amount the record must reach ("※ 실적은
# 단일 건 기준 3억원 이상이어야 합니다", "※ 실적은 사업예산 이상의 단일 건이어야 합니다") is the record requirement's floor,
# compared with the budget as a record line's amount is. False = current behaviour.
V3_NOTE_FLOOR = False
# Probe (expert audit X2): v3 takes the single-record amount of "단일 … 또는 누적 …" (집행기준 제5조①: one past contract).
# False = current behaviour.
V3_DISJUNCTIVE = False
# v3: a required record measured against the project itself in words the ratio reader lacks is read against the budget:
# 기초가격, 계약예정금액·예정금액, 발주금액, 용역비, 사업 규모, "사업비 전액", a base followed by a parenthetical
# ("기초금액(부가세 포함) 이상", "추정가격(부가세 포함)의 1.2배"), and 예정가격 (VAT included, like the budget) is compared
# with the budget instead of the 추정가격. Only when no ratio was found. False = current.
V3_BASE_WORDS = False
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
# v4 (item name "특정실적"; 정부 입찰·계약 집행기준 제5조④2 and ①: a record of the same or a similar kind counts): when a
# record is required, a 공고문 qualification line, 유의사항 line or the record clause itself that refuses similar records ("유사
# 사업 실적은 인정하지 않음", "유사 용역 실적 불인정"), admits only records of the same name ("동일 명칭의 용역 수행실적만
# 인정") or excludes records of like kinds in a parenthetical ("…실적에 한함(시민회관·강당 설치 실적은 제외)") limits the record to a
# named one; evaluation, document-list, subcontract and joint-contract text is not. False = current behaviour.
V4_NAMED_RECORD = False
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
# Red team R3 (switch X2_HELD_VERBS): the held-record readers that work without a perf reading (PERF_UNREAD, and x2_unread_records
# for v2/v8 and V4_UNREAD) also take a firm that installed, built, developed, provided, acted for, sold, leased or
# maintained the object, alone or chained ("납품하고 설치한 업체", "납품 및 설치를 완료한 업체"), or that has N years or
# times of career or experience ("운영 경력 3년 이상인 업체"). False = current behaviour.
X2_HELD_VERBS = False
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
# Independent code audit: corrected common-base interpretation is measured
# separately from the 9/27 item probes, whose published baselines stay fixed.
CODE_AUDIT_BASE = False
# Probe (h08 group C, 2026-09-27): v24's region axis also reads, on the CPU, a 공고문 qualification clause naming the bidder's
# location with a 시·도 that the region family never returned (its selector families.REGION_ANY misses a full 시·도 name followed
# by a particle: "경상남도에 둔 자", "충청북도인 업체"; t2500 195 of 1,181 location restrictions, dev DEV-049) and compares that
# 시·도 set with 나라장터 제한지역. False = current behaviour.
V24_REGION_CPU = False
# Probe (h08 group C): v19 also fires on a third-party pledge demanded at the 적격심사 stage ("적격심사 시 제출", "낙찰자 결정
# 전까지 제출"; X6 stage class QUAL_STAGE), which V19_STAGE leaves out as the ordinary award procedure. False = current behaviour.
V19_QUAL_STAGE = False
# Probes (2026-09-27 literal sweep B: v10·v11·v13), each default = current behaviour.
# v13 also takes a 소기업·소상공인 restriction the model read as 참가자격 제한 outside the qualification section (a BID·EVAL
# declaration or an attachment clause), as the absence items do, when the qualification section states no class.
V13_ANY_SECTION = False
# v13 takes a 나라장터 조항호 registration of the small class ("소기업,소상공인제한") when no text clause states a class.
V13_REGISTERED = False
# v13 judged on 수의계약 too (as V10_PRIVATE / V11_PRIVATE).
V13_PRIVATE = False
# Items for which a 나라장터 조항호 registration of a 중기간 경쟁제품 designation (catalog.DESIGNATED_REGISTRATION) makes a notice with no
# catalog or title match a competition-product bid, without the AUDIT_FIXES scope change; () = current behaviour.
COMP_REGISTERED = ()
# Items also judged on goods lists with at least one listed 세부품명 (scope goods:mixed); () = current behaviour.
COMP_MIXED = ()
# v10: the 1천만원 floor (판로지원법 제9조①: 수의계약 1천만원 이상) applies to 수의계약 only; a competitive bid is judged at any amount.
V10_FLOOR_PRIVATE_ONLY = False
# Items decided by a dedicated stage (pps_c/dedicated) instead of the shared rule; () keeps every item on the shared rule.
DEDICATED = ()
# Items that keep the shared verdict and also fire where their dedicated stage fires (OR); () adds nothing.
DEDICATED_OR = ()
# Probe (h08 G1): a v10·v11·v13 firing of the competition-product stage obeys X4's object exclusions (a 급식·간식 basket under a
# food 세부품명, a product outside the designation's 특이사항), which the shared rule applies under X4_OBJECT. False = current.
DEDICATED_X4 = False
# Probe (h08 G1): the competition-product stage fires v13 only when its evidence clause names no 중기업, 중·소기업 or
# 중소기업(자) once statute and rule names and refused certificates are removed; conservative, so a small-only clause that
# also names 중소기업 in another role is dropped too. False = current.
V13_STAGE_SMALL_ONLY = False
# Probe (h08 group A): v9 also fires on a line the model read as designating the procured item when it names the maker or
# model without a Latin model code or label wording — a company written with 사·社·(주)·㈜·Co./Ltd., a "제조사 <name>" table
# cell, a model code glued to a particle ("DDC400의") or a CPU model ("i5-3550"). False = current behaviour.
V9_MAKER = False
# Probe (h08 group A): v12 does not follow the model's "과업과 같은 종류" reading when an informative title names the procured
# object with none of the cited product's own words (judge.V12_PRODUCT_WORDS), the clause names the 직접생산확인 certificate and
# offers no licence or supply route in its place. False = current behaviour.
V12_OBJECT_VOCAB = False
# v12 (판로지원법 제9조; with the procured object deciding v12): a title, band-tagged line, attachment project name or
# opening statement that ends with research, a training course, education, 수련활동, a school trip, 현장체험학습, consulting,
# advice, translation, evaluation, analysis, laundry, disinfection, recruitment, lodging or travel, or 나라장터 licences
# that are all research, travel, youth-training, university or education trades, name a work that
# is no listed competition product: a 직접생산확인 demand for a listed product then restricts the bid even when the model
# read the certificate as the procured kind, and a withheld-title service the certificate alone made competitive is judged
# by that work. Names mentioning an event and licences of other trades keep the current reading. False = current behaviour.
V12_OBJECT_WORK = False
# v12 (판로지원법 제9조; with the procured object deciding v12): a 직접생산확인 demand citing only 소프트웨어 진흥법
# 제48조 products, in a service notice whose names use no SW product word and whose 나라장터 licences name no SW, IT or
# content trade, restricts the bid even when the model read the certificate as the procured kind. Requires
# V12_OBJECT_WORK code (not its value). False = current behaviour.
V12_OBJECT_SW = False
# v12 (판로지원법 제9조; with V12_OBJECT_WORK or V12_OBJECT_SW on): a certificate line citing a listed product that
# those rules show is not the procured work is read wherever V12_EXPLICIT_WIDE and V12_MORE_FORMS read certificate lines
# (any 공고문 section but evaluation, any attachment section). False = current behaviour.
V12_OBJECT_WIDE = False
# v12 (판로지원법 제9조; with the procured object deciding v12): a 직접생산확인 demand citing only event, exhibition,
# festival or conference products, in a service notice whose names end with maintenance, repair, inspection,
# construction, design, survey, waste, cleaning, security, catering, rental, purchase, testing, insurance, translation or
# printing work (no name mentioning an event, no event, exhibition, advertising, design or interior licence), restricts the
# bid even when the model read the certificate as the procured kind. Requires the V12_OBJECT_WORK, V12_OBJECT_SW and
# V12_OBJECT_WIDE code (not their values). False = current behaviour.
V12_OBJECT_EVENT = False
# v12 (판로지원법 제9조; with the procured object deciding v12): a 직접생산확인 demand citing listed services none of
# whose related trades is among the 나라장터 licences (at least one licence other than 기타자유업종, no name using the
# cited products' words) certifies a product other than the registered trades' work and restricts the bid even when the
# model read the certificate as the procured kind; a withheld-title service that the cited certificate alone made
# competitive is judged by v12 when its licences show such another trade. Requires the V12_OBJECT_WORK, V12_OBJECT_SW,
# V12_OBJECT_WIDE and V12_OBJECT_EVENT code (not their values). False = current behaviour.
V12_OBJECT_TRADE = False
# Red team A2 (switch V8_SAME_CLAUSE): v8 takes a qualification clause that states the bidder's location and a held record together
# when no region line was read and none is registered. False = current behaviour.
V8_SAME_CLAUSE = False
# Red team A2 (switch V20_PROGRAM_SW): V20_CONTENT does not refuse "…프로그램 운영" when a business word names the program as
# software ("업무관리 프로그램 운영", "회계 프로그램 운영"). False = current behaviour.
V20_PROGRAM_SW = False
# Red team A2 (switch V24_METHOD_WIDE): v24's method-field axis also reads other method labels, value-only table lines,
# "본 입찰은 … 제한경쟁입찰입니다" declarations, heading parentheses and "지역제한 경쟁". False = current behaviour.
V24_METHOD_WIDE = False
# Red team A2 (switch V24_AMOUNT_LABELS): v24's CPU amount readers also take 용역금액, 용역(기초)금액, 과업예산, 물품·구매금액, a label
# closed by a parenthesis ("기초금액(사업예산):", "(예비가격기초금액)") and a backslash won sign. False = current behaviour.
V24_AMOUNT_LABELS = False
# Red team A2 (switch V24_AMOUNT_TOTAL): v24's CPU amount readers accept "총"/"총액" between the label and the amount
# ("사업금액 : 총 88,000,000원"). False = current behaviour.
V24_AMOUNT_TOTAL = False
# Red team A2 (switch V1_INST_WORDS): v1's institution gate also accepts 교육기관, 전문기관, 학회, 지방공사·공단, 출연·출자·투자기관,
# 진흥원·평가원 as institution kinds. False = current behaviour.
V1_INST_WORDS = False
# v1: more institution kinds a limit with an explicit limiting phrase can name (공기업, 준정부기관, 정부·국가기관, 산하·소속·부설기관, 언론사·신문사·방송사, 상공회의소,
# 박물관·미술관, 의료기관, 지방자치단체), a parenthetical that narrows the eligible kind ("4년제 대학교(전문대학 제외)",
# "대학, 연구기관(영리법인 제외)") is no alternative clause, "…로 참가자격을 제한" is a limiting phrase, and the CPU list path
# (V1_INST_LIST) takes the same kinds as list members. False = current behaviour.
V1_INST_KINDS = False
# Red team A2 (switch V24_METHOD_VERTICAL): v24's method-field axis takes the next line as the value of a label that stands
# alone (vertical table). False = current behaviour.
V24_METHOD_VERTICAL = False
# Red team A2 (switch V1_NATIONWIDE_WIDE): v1 reads every qualification-section line for a nationwide or multi-region holding of
# branches, service centres, repair shops, offices, plants or warehouses (wider nouns and verbs than NATIONWIDE_HOLDING) or a
# held staff count of ten or more (STAFF_SCALE), with no institution-family candidacy needed. False = current behaviour.
V1_NATIONWIDE_WIDE = False
# v1: a participation requirement stated outside the qualification section is judged like one inside it: a 공고문 유의사항,
# 입찰 or other-section line with a bidder subject or a participation phrase ("※ 본 입찰은 대학(산학협력단 포함)만 참가할 수
# 있으며", "※ 참가업체는 상시 근로자 50인 이상을 고용하여야 합니다"), and an attachment line labelled "제안참가자격:" or
# under a 참가자격 heading with a bidder subject or a participation phrase (also for the CPU list path, and a labelled
# attachment line that limits bidders to institution kinds in its own words counts without a model reading); evaluation, document-list and joint-contract text is not. False = current.
V1_PLACEMENT = False
# v1 (item: 법령상 근거 없는 시설·인력·장비 규모 요구): a qualification that the bidder own or run a facility of a stated size
# ("자체 물류창고(연면적 3,000㎡ 이상)를 보유한 업체", "2,000석 이상 규모의 공연장을 … 운영하는 업체") or keep a site in every
# 시·군 or per region ("시·군별 1개소 이상의 영업소를 두고 있는 업체", "도내 모든 시·군에 배송거점을 보유한 업체"), found by
# the CPU in any qualification line; a record, a place of performance, a rented venue, a partner and a facility a named
# statute requires are not. False = current behaviour.
V1_FACILITY_SCALE = False
# Red team A2 (switch V24_LICENCE_PARTIAL): v24 fires when the 공고문's qualification licence codes and 나라장터's each hold a code
# the other lacks (a partial overlap; the licence axes fire only on disjoint sets). False = current behaviour.
V24_LICENCE_PARTIAL = False
# Red team A2 (switch V4_ONLY_RECORDS): with a participation record required, v4 also fires on a 공고문 line (not the evaluation
# section) limiting the counted records to schools or a public-buyer kind ("(중·고등학교 실적만 해당)"). False = current behaviour.
V4_ONLY_RECORDS = False
# Red team A2 (switch V24_BASIC_SCOPE): v24 fires when 나라장터 registers only 시·군·구 and the 공고문 restricts the bidder to the
# whole 시·도 naming no 시·군·구 (a scope mismatch the 시·도 comparison cannot see). False = current behaviour.
V24_BASIC_SCOPE = False
# Red team A2 (switch V24_BARE_TAG): v24 reads a bracketed method tag without an amount band in the first 12 공고문 lines
# ("(제한경쟁)") against 나라장터 계약방법. False = current behaviour.
V24_BARE_TAG = False
# Red team A2 (switch V4_BUYER_NOUN): a buyer kind directly qualifying the record noun ("공공기관(…) 통근버스 운행 실적") limits the
# record to that buyer although no ordering verb or particle follows it. False = current behaviour.
V4_BUYER_NOUN = False
# Red team R3 (switch V4_BUYER_VERBS): after a buyer kind with the particle "에" ("학교에 … 공급한", "대학병원에 … 설치한"), v4 also reads
# supplying, installing, providing, selling, leasing, operating, building or producing verbs as the record's buyer relation
# (BUYER_VERB names ordering, delivery and performance only). False = current behaviour.
V4_BUYER_VERBS = False
# v4: more ways to limit the record to one buyer (정부 집행기준 제5조④3, 지방 집행기준 제1장 7.나.1)5)): the orderer itself as
# a plain word ("해당 수요기관에 … 납품한", "본 기관 발주 … 실적"), a named public body with its affiliates ("한국전력공사 및 그
# 자회사에 납품한"), LH/SH-type abbreviations with an ordering verb, "군(軍) 부대"·육해공군, a buyer kind with "와/과 …
# 이행한", and a buyer kind directly qualifying an operation or delivery experience ("공공기관 급식소 위탁운영 경험"). False = current.
V4_BUYER_MORE = False
# Red team R3 (switch V4_NAMED_BUYER): v4 also reads a record limited to one named buyer when no buyer kind decides the
# clause: a public body named with its own name ("한국전력공사에 납품한", "서울특별시에서 발주한"), an anonymised institution token, the orderer
# token directly qualifying the record ("[수요기관(공기업)] 3년 내 납품실적"), the orderer itself ("당 기관에 납품한") or a clause
# that refuses private records ("민간 발주 실적은 인정하지 아니함"); 정부 입찰·계약 집행기준 제5조④3, 지방 집행기준 제1장 7.나.1)5).
# False = current behaviour.
V4_NAMED_BUYER = False
# Red team A2 (switch V8_TOKEN_REGION): v8 also takes a 공고문 qualification clause that restricts the bidder's location to the
# anonymised orderer/local-government token ("본점소재지가 [수요기관(기초자치단체)]내에 소재"), which the region family never
# selects. False = current behaviour.
V8_TOKEN_REGION = False
# Red team R3 (switch V8_REGION_WORDS): the CPU bidder-location reader v8 uses when no region line was read and none is registered
# (x2_cpu_region) also takes "<시·도> 소재 업체", "도내·관내·시내·군내 업체" and multi-시·도 region groups ("수도권 소재 업체"),
# unless the clause is a preference, a partner or a subcontract. False = current behaviour.
V8_REGION_WORDS = False
# v8: the CPU bidder-location reader (x2_cpu_region, used when a record is required, no region line was read and none is
# registered) also takes a labelled participation region ("입찰참가지역 : 부산광역시", "참가 가능지역 : 충청남도", "지역제한 :
# 강원특별자치도", "참가자격 지역 : 대구광역시", in the qualification section "소재지 : 서울특별시"), "관내(울산광역시) 업체", a joined
# 시·도 pair ("전라남·북도 소재 업체"), a region group or region token before "소재 업체" and a 유의사항 line admitting only bidders of a region; "없음" values and preference, partner or place clauses are not. False = current.
V8_REGION_LABEL = False
# Red team A2 (switch V4_UNREAD): v4 also reads held-record clauses the perf family never selected (x2_unread_records, as v2
# under X2_HELD_RECORD_X) and fires when such a clause limits the record to a named kind of buyer. False = current behaviour.
V4_UNREAD = False
# Red team A2 (switch V24_BASIC_REGION): v24 compares 시·군·구 tokens too: 나라장터 registers 시·군·구 and the 공고문's
# bidder-location clause names only other 시·군·구 (anonymiser ids are shared by doc and meta), no orderer token. False = current.
V24_BASIC_REGION = False
# Red team A2 (switch V24_POW10): v24 fires when, in a field group none of whose labelled values agrees with 나라장터, a value
# equals the registered amount x 10^k (k = ±1..±3; a dropped or added zero). False = current behaviour.
V24_POW10 = False
# v24, organizer ruling (talkboard 418042, 9/28): a participation restriction stated on one side only is compared like any
# other. V24_REGION_ONESIDED: 나라장터 지역제한여부 'N' while the notice restricts the bidder's location as a qualification.
# V24_LICENCE_ONESIDED: 업종제한여부 'N' while a qualification line requires a 나라장터 업종 by code. False = current behaviour.
V24_REGION_ONESIDED = False
V24_LICENCE_ONESIDED = False
# v24, the reverse of the one-sided restriction (talkboard 418042 나: 나라장터 registers a restriction the notice does not
# have). *_NONE_STATED: the notice states there is no region / industry restriction while 나라장터 registers one.
# *_SILENT: 나라장터 registers one and no document of the notice mentions a bidder-location / industry requirement at all.
V24_REGION_NONE_STATED = False
V24_LICENCE_NONE_STATED = False
V24_REGION_SILENT = False
V24_LICENCE_SILENT = False
# Red team A2 (switch V20_STATEMENT_STRICT): v20's statement reader takes a 제48조 citation only of the 소프트웨어 진흥법 (not
# 지방계약법 시행령 제48조) and not a bare list entry naming the 중소 SW사업자 지침 among applicable rules. False = current behaviour.
V20_STATEMENT_STRICT = False
# Red team A2 (switch V4_BUYER_WIDE): v4's buyer reader also takes 중앙부처, 공공단체, 관급, 국공립, 시·도, 시·군·구, 공사·공단, 공단
# and public facilities (도서관·박물관·미술관·복지관) (complementing C2_BUYER_VOCAB), reads "(민간 제외)" as no private admission,
# and takes a clientele-limited record ("중학생 대상 … 실적"). False = current behaviour.
V4_BUYER_WIDE = False
# Red team C2: the region family also selects bidder-location clauses whose 시·도 REGION_ANY misses ("충청남도에 둔") or that
# name the orderer's jurisdiction token (families.region_sel_sido). 1: 공고문 qualification section, 2: any line. 0 = current.
REGION_SEL_SIDO = 0
# Red team C2: v4 buyer kinds outside BUYER (행정기관, 정부기관, 출연·투자·산하기관, 군부대·국방부, 시·군·구청·도청, 청-level
# agencies, 공공부문) and the relations "…과 체결한" / "…로부터 수주한" (judge.BUYER_C2). False = current behaviour.
C2_BUYER_VOCAB = False
# Red team C2: v19 bid-time wording ("입찰서와 함께 제출", "입찰참가 신청 시", "개찰 전까지") and maker 공급확인서 /
# 기술지원확인서 as pledge documents in the X6 stage check (judge.X6_PRE_TIME_C2, X6_PLEDGE_C2). False = current behaviour.
C2_PLEDGE_VOCAB = False
# Red team C2: v10's presence test skips lines that only verify the direct-production certificate (v12's
# DP_VERIFY with the certificate as subject; judge.dp_only_verifies). False = current behaviour.
C2_DP_PRESENT_LITERAL = False
# Red team C2: v12 treats a line as verification-only only when the certificate is the verified subject and no possession
# wording (incl. 발급받은, 받은 업체, 갖춘, 소지 업체) is present (judge.dp_verify_only). False = current behaviour.
C2_DP_VERIFY_SUBJECT = False

# Red-team D: isolated candidate, default preserves the measured union.
RTD_V21_SHARE_TEXT = False

# Red-team D: isolated candidate, default preserves the measured union.
RTD_V3_AMOUNT_TEXT = False

# Red-team D: isolated candidate, default preserves the measured union.
RTD_V23_EVENT_DATES = False

# Red-team D: isolated candidate, default preserves the measured union.
RTD_REGION_BIDDER_CLAUSE = False

# Red-team D: isolated candidate, default preserves the measured union.
RTD_BRIEF_ATTENDANCE = False

# Red-team D: explicit joint-contract mode ownership.
RTD_V21_MODE_SCOPE = False
# Red team C2: amounts.ratios reads "사업예산 이상" (no multiple) as 1 x the base and "…의 100분의 N" as N/100 (v3 and the
# perf CPU form). False = current behaviour.
C2_BUDGET_WORDS = False
# Red team C2: size_words reads "중기업 참가 불가" / "중기업을 제외" / "중소기업 중 소기업" as small-only (families.NO_MEDIUM_C2).
# False = current behaviour.
C2_SIZE_NO_MEDIUM = False

# Red-team D: isolated candidate, default preserves the measured union.
RTD_V2_COMPLETED_EXPERIENCE = False
# Probe (red team B2): v13 keeps a small-only firing when the 조항호 registers the 판로지원법 제7조의2 small-business
# restricted competition (talkboard v13: amount bands are no requirement, the only exception is 소액수의). False = current.
V13_REG72_KEEP = False
# A 공고문 participation clause that literally limits the bid to 소기업·소상공인 (statute and rule names removed; 중기업,
# 중·소기업, 중소기업자 or 중견기업 not admitted) counts when the model read no size restriction in the qualification
# section: for v13 as the small-only limit, for v11 as a present SME restriction. Items listed here; () = current behaviour.
SMALL_TEXT_CLAUSE = ()
# Probe (red team B2, with C2_DP_PRESENT_LITERAL): v10's presence test also skips validity notes (※ … 발급된 것으로 유효기간
# 내에 있어야) and competition-conditioned lines, and X4 ignores them. False = current.
V10_NOTE_NOT_REQ = False
# Probe (red team B2): v13 reads a clause limiting bidding to 소기업·소상공인 ("…에 한함", "…만 참여", "…로 제한") or barring
# 중기업 in other words as the small class even inside an SME frame (other size items unchanged). False = current.
V13_SMALL_LIMIT = False
# Probe (red team B2): items judged as buying 동영상제작서비스 when a service notice without a catalog object admits only video
# producers (비디오물제작업, 방송영상독립제작사) and names video work in its title or overview; () = current.
OBJ_VIDEO_LICENSE = ()
# Probe (red team B2): items judged with AUDIT_FIXES2 alone (the round-2 size-class reading without the AUDIT_FIXES
# sibling-option rule); () = current behaviour.
AF2_ITEMS = ()
# Probe (red team B2): v11 treats the SME restriction as absent when every restriction line is only a method statement,
# a certificate issue/verification note or an admission of 특별법인·비영리법인. False = current.
V11_NOTE_NOT_RESTRICT = False
# Probe (red team B2): in a goods purchase whose 세부품명 are all outside the 경쟁제품 list, v12 does not follow the model's
# "과업과 같은 종류" reading of a certificate for a listed product. False = current.
V12_GOODS_OBJECT = False
# Probe (red team B2): a v12 firing of the dedicated stage is dropped when its evidence is a document-list entry, a DOCS
# line, a certificate name with no holder or possession wording, or a verification-only note. False = current.
DED_V12_REQ_ONLY = False

# v16/v18: an absence is no violation when the notice itself states that the SME-priority rule does not apply to it or that
# its exception is applied (item table: 판로지원 예외 명시). False = current.
RTD_SIZE_EXPLICIT_EXCEPTION = False

# v14-v18: class words split by layout or written with other middle dots (중⋅소기업, 중‧소기업) are normalized before the
# size class is read, so a whole-SME restriction is not read as small-only. False = current.
RTD_SIZE_TYPOGRAPHY = False
# The one retry of a reply that did not parse gets max_tokens x this factor, within the engine context; at temperature 0
# a reply cut at max_tokens otherwise fails again. 1 = current.
RETRY_TOKEN_FACTOR = 1

# v20 stage: a notice whose own qualification asks for SW사업자 registration (or an IT-service code) is an SW project also when
# the model reads its object as hardware or equipment only (DEV-132: 전산기기 임차·유지보수 under that qualification is
# v20 = 1); a general service or goods reading still keeps it out. False = current.
V20_STAGE_SELF_SW_HW = False
# v20 stage: a candidate found by SW-project vocabulary also fires when the model reads its object as hardware or unclear
# (a general service or goods reading still keeps it out). False = current.
V20_STAGE_CONTENT_HW = False

# v9 stage: a maker or model named for a part of a supplied computer (CPU, GPU, chipset, board, OS: 'Intel Xeon Gold 6544Y',
# 'Windows 11 Pro 탑재') fires like any supplied item. False = current.
V9_STAGE_COMPUTER_PARTS = False
# v9 stage: in a service notice a maker or model named for the item the contractor supplies fires too. False = current.
V9_STAGE_SERVICE_ITEMS = False
# v9 stage, service notices (with V9_STAGE_SERVICE_ITEMS off): a maker or model the model read as an item the contractor
# supplies still fires when the line demands that product: the name carries a Latin letter or a digit (a product or model,
# not a person, place or event) and the line installs, mounts, uses, rents, supplies, introduces or maintains it ("… 탑재",
# "… 방식 사용", "… 유지보수"), or the name is followed by a class floor or an equivalent ("GRAND MA2이상급", "… 동등"). Examples,
# travel itineraries and flight codes stay out. False = current behaviour.
V9_SERVICE_PRODUCT = False

# v20 stage: strong SW-project vocabulary hits in the head text that make a candidate without an SW사업자 qualification.
V20_STAGE_CONTENT_HITS = 2
# v9 stage: the best excerpt-line score a notice needs before the model reads it for designations.
V9_STAGE_GATE_SCORE = 5

# v22 (organizer answer 9/28): a line naming the proposer's own presentation, or a 제안설명회 line whose consequence is an
# evaluation-stage one (평가 제외, 0점, 협상적격자 제외, 등록 취소), is not the orderer's briefing as a participation condition
# unless it names that briefing. False = current behaviour.
V22_PROPOSER_EVENT = False

# v24 licence axis by name: the 공고문 requires a licence while neither a registered 업종 code nor a registered licence
# name stem occurs anywhere in it (judge.v24_licence_name). False = current behaviour.
V24_LICENCE_NAME = False

# v24 method axis: a bid-attribute list or a lone method cell ("총액입찰, 제한경쟁, 적격심사대상") stating one method other
# than 나라장터 계약방법, with no field or title tag naming the registered one (judge.v24_method_list). False = current behaviour.
V24_METHOD_LIST = False
# v19: a third-party pledge entry of a 공고문 submission-document list is demanded at the bid when the list's heading, its
# intro lines or the heading's parent item set the list at the bid, estimate or proposal submission (입찰참가신청·입찰등록·
# 견적서·제안서 제출) and none of them sets it at the 적격심사, award, contract or delivery stage (talkboard 9/28: an undated
# 확약서 entry is timed by its submission-document section and that section's deadline). False = current behaviour.
V19_LIST_DEADLINE = False
# v19 (talkboard 9/28: the submission-document sections of the 공고문 and its attachments set an undated 확약서 entry):
# V19_LIST_DEADLINE also reads the lists of attached documents (규격서, 제안요청서, 과업지시서). False = current behaviour.
V19_LIST_ATTACH = False
# v9 (talkboard 9/1: v9 holds when the specification names a specific model or maker): in a goods purchase, a line that
# demands compatibility or linkage with a named installed model designates that model. False = current behaviour.
V9_EXISTING_COMPAT = False
# v19: a qualification that the bidder hold or have been issued the third-party pledge ("…확약서를 보유한 업체", "…발급받은
# 업체에 한하여 입찰에 참가") or obtain it in advance ("사전에 발급받아", "입찰참가신청서와 함께 제출") sets the pledge before the
# bid in the X6 stage check instead of reading as a capability (talkboard 9/28: v19 is a pledge submitted at the bid or issued
# or held before it); "제출할 수 있는", "제출 가능한" stay capabilities. False = current behaviour.
V19_HOLD_QUAL = False
# v19 (user reading of the talkboard 9/28 ruling): a participation qualification that the bidder be able to submit the
# third-party pledge ("제조사로부터 물품공급확약서를 확보하여 제출할 수 있는 업체", "정품공급확약서 제출이 가능한 업체")
# demands that it be secured before the bid, unless the clause sets it at the contract, award or 적격심사 stage.
V19_CAP_PRE = False
# v12: a 공고문 line outside the evaluation and document-list sections (or an attachment's qualification line) that names the
# 직접생산확인 certificate and states in its own words that only its holders may bid ("…를 보유한 업체", "…받은 업체만 입찰에
# 참가할 수 있습니다", "…제출하지 않는 업체는 입찰에 참가할 수 없습니다") is a possession requirement whatever section or role
# the model gave it; the other v12 conditions apply unchanged. False = current behaviour.
V12_EXPLICIT_ANY = False
# v14-v18: a line that only names the kind of bid ("소기업·소상공인 간 제한경쟁 입찰입니다", "입찰방법: 제한경쟁(…, 중소기업)",
# the 나라장터 tag) states no participation qualification: v16/v18 find the restriction absent when every size line is
# one and no qualification line names a size class; v14/v15/v17 do not fire on such lines alone. False = current.
RTD4_BID_DECL = False
# v14-v18: a clause stating the size class of who may bid in the notice's own words ("… 소기업 또는 소상공인으로서 …
# 확인서를 소지한 자", "입찰참가업체는 … 소기업 …") counts when the size reading returned no restriction: it removes a
# v16/v18 absence and gives v14/v15/v17 the class (공고문 only for these). False = current.
RTD4_SIZE_UNREAD = False
# v11/v13-v18 size class: "소상공인 또는 중·소기업 확인서를 소지한 업체" admits every holder of the 중·소기업 certificate
# (the whole SME class), not the small class the first word names. False = current.
RTD4_CERT_LIST = False
# Services: the 행사대행업 registration (기타자유업) names who may bid, not what is bought; a service that only this license
# puts in the 행사기획·대행 competition family, with no event work in its title or project-name lines, is a general service
# (v14-v18 apply, v10/v11/v13 do not). False = current.
RTD4_EVENT_LICENSE = False
# v23: the briefing date may be written with a two-digit year ("’26.03.05.", read as 2026-03-05) and may sit on the next
# item of the briefing heading's own list ("- 사업설명회" / "- 일시: 2026. 3. 5."), which the layout parser returns to the
# enclosing section. Requires RTD_V23_EVENT_DATES. False = current behaviour.
V23_DATE_FORMS = False
# v22 (negotiated contracts): attendance at the orderer's briefing is a participation condition also when written as a
# qualification item naming the attendees ("라. 현장설명회 참석 업체"), as a duty or requirement ("참석은 필수", "의무참석",
# "참석은 입찰참가자격 요건"), as only attendees admitted in other words ("참석 업체에 한해 입찰참가 자격을 부여", "참석업체만
# 입찰 가능", "참석업체 외에는 입찰에 참가할 수 없음", "참석자 명부에 서명한 업체만") or as absence barring participation
# ("미참석 시 입찰참가자격 박탈", "현장설명에 참가하지 아니한 자는 입찰에 참가할 수 없다", "참석확인서 미제출 시 입찰 무효"),
# in the 공고문 or an attachment's qualification text; a bare "설명회" counts right below a line naming the orderer's briefing.
# The proposer's presentation and evaluation-stage consequences stay out. Requires RTD_BRIEF_ATTENDANCE. False = current.
V22_ATTEND_FORMS = False
# v21: a joint-member share floor written as "3/100", "3프로", "삼(3)퍼센트", "지분율이 N% 미만인 경우 구성원이 될 수 없음" (the
# floor is N%), "구성원은 각 N% 이상 참여" (a member's own participation floor without the word 지분) or a share field whose
# value line opens with its own bullet ("구성원별 지분율" / "- 최소 3% 이상") is read by the same literal share check.
# Requires RTD_V21_SHARE_TEXT. False = current behaviour.
V21_SHARE_FORMS = False
# v5/v6/v7: a restriction of who may bid by where the bidder is, written as a labelled field ("지역제한: 경기도",
# "| 참가지역 | 부산광역시 |", "입찰참가자격 제한: 지역(대구광역시)"), as "X 소재 업체에 한함", as an exclusion of bidders outside
# X ("X 외 지역 업체는 입찰에 참가할 수 없음"), with a residence or registration predicate ("X에 주소를 둔 자", "X에
# 사업자등록이 되어 있는 업체"), naming the orderer's own 시·군·구 ("당해 시 관내") or naming 시·군 without the anonymised
# token ("여주, 양평 지역 업체"), in the 공고문 outside evaluation and document lists or in an attachment line stating the
# participation qualification; delivery, work-site, contact, facility, joint-partner and bonus clauses stay out. Applies
# after RTD_REGION_BIDDER_CLAUSE finds nothing. False = current behaviour.
REGION_CLAUSE_FORMS = False
# v2, v8 (and v4 through V4_UNREAD, v2-v4 through PERF_UNREAD): a held record stated without the "실적이 있는 업체" shape is a
# record requirement: "…공급한 사실이 있는 업체", "…운영을 해본 업체", "…경험 유무 : 필수", "…수행하였음을 입증할 수 있는
# 업체", "…사례를 증빙할 수 있는 업체", "…개최 경험 2회 이상", "…수행 이력 보유 업체", "…참여 이력이 확인되는 업체", "…수주하여
# 완료한 업체"; an attachment line labelled "제안참가자격:" is under a qualification heading; and a 공고문 line outside the
# evaluation text that bars bidders without the record ("실적증명서 미제출 시 입찰참가를 제한", "실적이 있는 업체만 견적서를
# 제출할 수 있습니다") states the requirement when no other record line does. False = current behaviour.
X3_RECORD_FORMS = False
# v8 (시행규칙 제25조): a participation region written as a requirement label and its value in a line outside the evaluation text
# and the document list ("입찰참가자격 지역 : 충청북도", "참가 지역 요건 : 대구광역시 소재 업체", "소재지 요건 : 광주광역시 관내")
# is the bidder-location restriction; values stating no restriction (없음, 해당 없음, 전국) are not. Used for v8's location side
# only when the other location readers find nothing. False = current behaviour.
U1_V8_REGION_LABEL2 = False
# v8: an attachment sentence outside evaluation and document lists that limits bidders to a region by their location — a bidder
# subject ("입찰참가자는 대전광역시에 본점을 둔 업체로 한다", "제안사는 법인등기부상 본점이 강원특별자치도에 있어야 한다"), a
# location-requirement label ("지역 요건 : 부산광역시 소재 업체") or a participation clause ("울산광역시에 소재하는 업체에 한하여
# 제안서를 제출할 수 있다") — is the bidder-location restriction wherever the notice states it (시행규칙 제25조; context, not
# placement). Partner, subcontract, delivery-place, service-centre, preference and evaluation text is not. Used for v8's location
# side only when the other location readers find nothing. False = current behaviour.
U1_V8_ATTACH_REGION = False
# v4 (item name "특정실적"; 정부 입찰·계약 집행기준 제5조④2: 특정한 명칭의 실적으로 제한함으로써 유사한 실적이 있는 자의
# 입찰참가기회를 제한하는 경우, e.g. "농공단지 조성실적이 있는 업체만"): a 공고문 line outside the evaluation text that admits
# only the record of one named work, without a refusal word ("실적 인정 범위 : 세계유산 축전 운영 실적에 한함", "수행실적은
# 농공단지 조성 실적만을 인정함", "…무대기계 설치 실적이 있는 업체만 참가 가능"), limits the record to a named one. A named work
# is one without kind, time, amount, status, buyer or bidder words (동종·유사·관련, 최근·이내, 원·이상, 완료·준공, 발주·기관 …);
# evaluation, subcontract and joint-contract text is not. False = current behaviour.
U1_V4_NAMED_ONLY = False
# v4 (정부 입찰·계약 집행기준 제5조④3: 특정기관이 발주한 실적만을 요구하고 다른 기관 및 민간의 실적을 인정하지 않는 경우): a
# 공고문 line outside the evaluation text — 유의사항, 제출서류 and other sections included — or an attachment line with a bidder
# subject or a record label that counts only records of a public-buyer kind ("※ 수행실적은 국가기관 및 지방자치단체 발주
# 실적만 인정합니다", "실적증명서(공공기관 발주분에 한함) 1부", "입찰참가자의 실적은 … 계약한 실적만 인정됩니다") or refuses
# private records ("※ 민간 발주 실적은 인정하지 않습니다") limits the record to specific buyers. Lists that admit private
# buyers, proof and certificate notes, and evaluation or record-form text are not. False = current behaviour.
U1_V4_BUYER_NOTE = False
# v4 (정부 입찰·계약 집행기준 제5조④3; item note "특정기관 표현 다양"): a required record whose clause names a ministry, agency
# or commission with its affiliates as the buyer ("행정안전부 또는 그 소속기관의 … 수행 실적", "…청 및 산하기관에서 발주한 …
# 실적"), a public office, constitutional body, public broadcaster or public facility ("행정복지센터에 … 납품한 실적", "국회 또는
# 법원에 … 납품한", "공영방송사가 발주한", "공공 체육시설 위탁운영 실적", "시립도서관 납품 실적") or asks for public-order records
# ("공공발주 실적 1억원 이상 보유 업체") limits the record to specific buyers. Clauses admitting private buyers are not.
# False = current behaviour.
U1_V4_BUYER_KINDS = False
# v2, v8: a bare list item of the 공고문 qualification section that names a held record ("- 동종 물품 공급 경험", "○ 유사 행사
# 대행 경력", "- 최근 3년 이내 동종 과업 수행 경험 보유") under a lead-in requiring the listed items ("아래 요건을 모두 갖춘
# 업체", "다음 요건을 충족하는 자", "입찰참가자는 다음 요건을 갖추어야 함") is a record requirement (시행령 제21조; the organizer
# reads context, not placement). Items with their own predicate, staffing, document, evaluation, exclusion and preference lists
# are not. Consulted only when no other record line is found. False = current behaviour.
U1_RECORD_LIST = False
# v2, v8 (and v4 through V4_UNREAD, v2-v4 through PERF_UNREAD; with X3_RECORD_FORMS): more held-record wordings count as
# X3 record forms: "…수주 후 완료한 업체", "…수행 완료 업체", "…납품 경력 업체", "…수행 여부 : 있음(필수)", "…을 1회 이상
# 완수한 업체", a line ending in "…대행 이력 보유". The X3 staff, evaluation and sanction exclusions apply unchanged.
# False = current behaviour.
U1_RECORD_FORMS2 = False
# v2, v8: a 공고문 line outside the evaluation text and the document list that admits only record holders ("※ 동종 실적 보유
# 업체만 참가 가능합니다", "※ 본 입찰은 동종 물품 납품 경험 보유 업체에 한함") states the record requirement (시행령 제21조;
# context, not placement). Document entries ("증명서(실적 있는 업체에 한함) 1부"), staffing, waivers, preferences and
# evaluation text are not. Consulted only when no other record line is found. False = current behaviour.
U1_NOTE_ONLY = False
# v2, v8: an attachment sentence (과업지시서, 제안요청서, 규격서) outside evaluation and document lists whose subject is the
# bidder ("입찰참가자는", "제안사는", "참가업체 자격 :", "본 사업에 참여하는 업체는") and whose predicate requires holding a
# record ("…실적이 있는 업체이어야 한다", "…실적을 보유하여야 한다", "…실적 보유 업체") is the record requirement wherever the
# notice states it (시행령 제21조; the organizer reads context, not placement). Proof, submission, staffing, subcontract,
# joint-contract and preference text is not. Consulted only when no other record line is found. False = current behaviour.
U1_ATTACH_RECORD = False
# v14/v15/v17: a clause limiting bidders to a size class (…만 참가, …에 한함, …로 제한, …이어야, 참가대상·참가자격: …)
# counts in the 공고문's notes and opening lines and in attachments outside a qualification heading, when the
# qualification section states no class (t4_size_wide). False = current.
T4_SIZE_WIDE = False
# v16/v18: a clause that t4_size_wide reads as limiting bidders to a size class is a stated restriction, so the
# absence items do not fire on it. False = current.
T4_SIZE_WIDE_ABS = False
# v17: when the 공고문's qualification clauses admit the whole SME class (중기업 included) and only an attachment
# names 소기업·소상공인, the 공고문's class decides (t4_v17_notice). False = current.
T4_V17_NOTICE_CLASS = False
# v12 (with V12_EXPLICIT_ANY): a line of any 공고문 section but the evaluation table, or of any attachment section, that names
# the 직접생산확인 certificate and bars bidders without it in its own words ("…가 없는 업체의 입찰 참가를 제한", "…를 받지 않은
# 업체는 … 참여할 수 없음", "…를 첨부하지 않은 경우 … 무효", "입찰자는 …를 보유하여야 합니다", "…를 소지한 중소기업으로
# 제한") is the possession requirement, whether or not the reader selected the line; lines admitting non-holders, sanctions,
# preferences or points for holders, and document-list entries without a bar are not. False = current behaviour.
V12_EXPLICIT_WIDE = False
# v12 (with V12_EXPLICIT_ANY and V12_EXPLICIT_WIDE): more wordings of the requirement to hold the 직접생산확인 certificate,
# on the same lines and with the same exclusions as V12_EXPLICIT_WIDE: losing the award, contract or qualification without it
# ("…미보유 시 낙찰을 취소", "…보유하지 않은 업체는 계약 대상에서 배제", "…가 없으면 입찰할 수 없음"), a required mark ("…보유
# 필수", "직접생산확인 : 필수"), an imperative ("…를 보유할 것", "…를 구비할 것", "…를 보유하고 있어야"), holders named as 법인,
# 제조사 and the like, a demand on the winner or contractor ("낙찰자는 … 제출하여야", "…유지하여야") or to submit it at a stage
# ("입찰참가 신청 시 … 사본을 제출하여야"), "…를 제출할 수 있는 업체", a contract
# limited to holders, certified goods ("…확인을 받은 제품으로 납품"), the statute's wording ("…직접생산 여부를 확인받은 업체",
# "판로지원법 제9조에 따른 확인을 받은 자")
# and holder clauses citing the 고시 "중소기업자간 경쟁제품 직접생산 확인기준". A revocation of the certificate for a breach
# (확인 취소, 제11조, 하청생산, 위반, 부정당) stays a sanction. False = current behaviour.
V12_MORE_FORMS = False
# v12 (판로지원법 제9조; with V12_MORE_FORMS): more holder wordings of the certificate requirement ("발급받아 유효기간
# 내에 있는 업체", "보유하고 있는 중소기업", "보유(세부품명) 업체", "자격요건: … 구비", a qualification table row ending
# in 소지·보유·구비) on the lines V12_MORE_FORMS reads, with its exclusions. False = current behaviour.
V12_MORE_FORMS2 = False
# v19: a third-party pledge whose absence voids the bid or bars the bidder ("…확약서 미제출 시 입찰 무효", "…확약서를
# 제출하지 아니한 자의 입찰은 무효", "…제출하지 않으면 입찰참가 자격이 없습니다", "…를 제출하여야 입찰에 참가할 수 있습니다"), or that is set
# at the bid-participation application ("입찰참가 신청 마감일까지"), in a 참가자격 label ("입찰참가자격: …확약서를 제출한 업체")
# or in a bid-document label ("입찰서류: …확약서 각 1부"), is demanded at the bid although the reader gave no time. The other v19
# conditions (third-party issuer, pledge document, X6 stage check) apply unchanged. False = current behaviour.
V19_BID_BAR = False
# v24: a budget or 추정가격 field whose value is written with Korean units ("사업예산: 3,500만원", "추정가격 | 1억 2,000만원",
# "사업비 금일억이천만원정", "예산: 35,000천원") is read as that amount in won by the CPU amount reader (it read only digit
# amounts, so "3,500만원" was 3,500); a band ("…2억원 미만") is no value, and a unit amount equal to a registered value at its own
# precision agrees with it. False = current behaviour.
V24_AMOUNT_UNITS = False
# v24 (budget axis): a budget or 추정가격 stated only in an attachment (제안요청서, 과업지시서, 규격서) is the notice's statement
# too: an attachment amount whose digits permute a registered value (a transposition), or a labelled attachment budget or
# estimate field that agrees with neither registered value, is not a VAT or rounding relation, differs from its registered
# value by at least a fifth (within half to double) and is stated nowhere in the 공고문, disagrees with 나라장터. Breakdowns,
# notes (※, *), settlement, annual, monthly, per-unit and variable amounts, compound labels ("홍보용역비"), exact shares and
# lines that also state a registered value stay out. False = current behaviour.
V24_ATTACH_AMOUNT = False
# v24: every method/band tag in the 공고문 head, in any bracket or separator shape, is compared with 나라장터
# (another method, or a band neither registered amount falls in), not only the first strict tag. False = current behaviour.
V24_TAG_ANY = False
# v24: a 공고문 region statement ("지역제한(…)", "지역제한 : …"), any 공고문 qualification clause on the
# bidder's seat, or a seat clause elsewhere in the 공고문 that limits bidders, naming 시·도 none of which
# 나라장터 제한지역 registers disagrees, also when another clause agrees. False = current behaviour.
V24_REGION_STATED = False
# v24: a 공고문 line outside the qualification section labelled as the licence field ("업종 :", "등록업종 :")
# or stating the licence the bidder must have registered (outside the joint-contract section), whose codes
# share none with 나라장터 업종, disagrees with 나라장터. False = current behaviour.
V24_LICENCE_FIELD = False
# v24: 나라장터 registers 시·군 tokens only and a 공고문 qualification clause on the bidder's seat names a
# registered token and an unregistered one: the notice admits bidders 나라장터 excludes. False = current behaviour.
V24_BASIC_SUPERSET = False
# v24: a 기초금액 written as a vertical table (label alone, amount on the next line) is judged as
# V24_BASE_ZONE judges an inline 기초금액. False = current behaviour.
V24_BASE_VERTICAL = False
# v24: an attachment line restricting the bidder's seat (본점·주된 영업소) to 시·도 none of which 나라장터
# 제한지역 registers disagrees with 나라장터. False = current behaviour.
V24_ATTACH_REGION = False
# v24: an attachment line outside the qualification section stating the licence the bidder must have
# registered, with codes none of which 나라장터 업종 registers, disagrees with 나라장터. False = current behaviour.
V24_ATTACH_LICENCE = False
# v24: a budget or 추정가격 stated in a sentence ("…소요예산은 금 X원…", "추정가격은 X원…") is compared with
# 나라장터 when no such statement agrees (equal, rounded or VAT) and it lies within half to double. False = current behaviour.
V24_AMOUNT_SENTENCE = False
# v24: labelled budget or 추정가격 statements past the first 150 공고문 lines are judged as the budget readers
# judge the first 150 lines, when none of them agrees with 나라장터. False = current behaviour.
V24_AMOUNT_LATE = False
# v9: a specification line that neither the model family nor the model-name stage was shown, and that names a known maker or
# brand as what must be supplied ("…한샘 제품으로 납품하여야 함", "(주)오텍 제품일 것", "냉장고는 삼성전자 비스포크 제품으로 납품",
# "브랜드: 에이스침대", "규격: 한솔제지 A4 복사용지"), designates the maker (집행기준 제5조④5). Lines about existing or compatible
# equipment, issuers of certificates or pledges, software named as a working environment and negated demands ("…으로 한정하지
# 않음") are not. False = current behaviour.
V9_BRAND_REQ = False
# v9 (with V9_BRAND_REQ, goods): a line no reader was shown that lists a known maker (the V9_BRAND_REQ makers and more office,
# IT, audio-visual, appliance and laboratory makers) with a model code at most two words after it ("HP LaserJet Pro M404dn
# 프린터", "린나이 가스레인지 RTR-T3200", "- 복사기 : 신도리코 D450"; a code of four or more letters and digits with at least one of
# each) names the model to be supplied (집행기준 제5조④5) without any demand word. Lines allowing an equivalent or an
# alternative (동등, 이상, 상당, 동급, 호환, 유사, 재생, 또는), component spec fields of a larger product ("CPU : …", "Main
# Board : …", "메모리 :"), the equipment a purchase serves ("적용대상") and the V9_BRAND_REQ and practitioner (X3) exclusions
# stay out. False = current behaviour.
V9_CODE_LISTING = False
# Items judged as buying a listed competition service when a service notice without a catalog object names that service
# (admitted at its 추정가격) for the 직접생산 certificate in a document list or caution ("직접생산확인증명서(세부품명: …)
# 1부"): the notice subjects its purchase to 판로지원법 제9조 for that service; () = current behaviour.
OBJ_DP_DOCS = ()
# SMALL_TEXT_CLAUSE: "중소기업(소상공인) 확인서" and "중소기업(소기업·소상공인) 확인서" name the certificate of the whole SME
# class, not a small-only bidder class ("중소기업자(소기업·소상공인에 한함)" still excludes 중기업). False = current behaviour.
SMALL_TEXT_CERT_NAME = False
# v10: a line that awards evaluation points or preference for holding the 직접생산 certificate ("…보유한 업체는 적격심사 시
# 신인도 가점", "직접생산 확인 품목 보유 여부(5점)") is an evaluation item, not the participation requirement, whatever its
# section or reading (a clause that also states who may bid stays a requirement). False = current behaviour.
V10_EVAL_NOT_REQ = False
# v10 (with V10_NOTE_NOT_REQ): a validity or check-timing note on the 직접생산 certificate ("…발급된 것으로서 유효기간 내에
# 있어야", "…발급분도 인정", "…기준으로 확인하며") is a note under any bullet (-, ○, ▶, ◈, ★, ㅇ …), not only ※ * 주);
# numbered items (가., 1), ①) and lines with holder wording stay requirements. False = current behaviour.
V10_NOTE_MARKS = False
# v11 (with V11_NOTE_NOT_RESTRICT): a size line whose clause names no bidder class and is a method statement in another
# shape ("입찰방법 : 총액입찰, 중소기업자 간 제한경쟁입찰", "제한경쟁에 의한 방식(중·소기업…)", "※ 중소기업자간 제한경쟁"), a
# certificate verification or issue note ("…확인하므로 … 확인 가능하여야", "미확인 시"), or a document, evaluation,
# information or contract note is no SME restriction. False = current behaviour.
V11_NOTE_TOPICS = False
# v13: a literal small-only participation clause ("…소기업·소상공인만 참여할 수 있습니다", "참가대상: 소기업·소상공인(중기업
# 참가불가)") counts in any 공고문 section (유의사항, 제출서류 and table rows too; evaluation lines aside) and in the attachments,
# under any bullet, also when the qualification section states an SME-level class; notes on certificates, records,
# evaluation or conditional cases, document demands, deemed special corporations and clauses admitting 중소기업자·중기업
# do not. False = current behaviour.
V13_SMALL_ANYWHERE = False
# v20: a statement that names one 사업금액 band of the 지침 (별표1: 20·40·80억; "본 사업은 20억원 미만 사업으로 …") while
# the notice's 사업금액 (budget with VAT, else 추정가격 × 1.1) lies outside that band states no restriction for this project:
# the item asks for the statement that fits the band (organizer 9/28). False = current behaviour.
V20_BAND_MISMATCH = False
# v20: a line that only cites the SW law or the 중소 SW사업자 지침 (a list of applicable laws, "제48조(중소 소프트웨어사업자의
# 사업 참여 지원)", "제48조에 따른 사업금액 산정 시 …") states neither whether the 제48조 restriction applies nor its basis: with
# the 지침 name, such article titles and the amount-computation phrase removed the line must still state it. False = current.
V20_CITATION_ONLY = False
# v20: a sentence that names the SW law or the 중소 SW사업자 지침 (or the 고시 title "대기업인 소프트웨어사업자가 참여할 수 있는
# 사업금액의 하한") and says large (or 중견) firms may not bid states that the 제48조 restriction applies and its basis (지침
# 제3조②), also when it wraps over up to three lines ("「소프트웨어 진흥법」에 의한 「중소 소프트웨어사업자의 사업 참여 지원에 /
# 관한 지침」…에 의거하여 대기업 및 중견기업은 입 / 찰에 참여할 수 없음") or gives the law without an article ("본 사업은 「소프트웨어
# 진흥법」이 적용되는 사업으로 대기업 및 중견기업은 입찰에 참여할 수 없음"); such a notice is compliant for the v20 stage and the
# shared rule. Cross-shareholding (상호출자) sentences without a band word stay out. False = current behaviour.
V20_STATEMENT_WRAP = False
# v20 stage: a line that only cites 소프트웨어 진흥법 제48조 or the 중소 SW사업자 지침 (a law list "적용법령: …", "제48조(중소
# 소프트웨어사업자의 사업 참여 지원)") is a citation for the model to read, not a CPU statement, unless the line itself (names,
# article titles and list labels removed) or its wrapped continuation has an application verb. False = current behaviour.
V20_STAGE_CITATION = False
# v23: a briefing date written with a two-digit year and no apostrophe is read when a weekday in parentheses follows it
# ("사업설명회 : 26.3.5.(목) 14:00" is 2026-03-05), which a section or item number never has. Consulted only when
# V23_DATE_FORMS finds no violation; requires V23_DATE_FORMS. False = current behaviour.
V23_YY_WEEKDAY = False
# v22 (negotiated contracts): attendance at a session named only "설명회" is a participation condition when its absence bars
# the bid or the proposal itself ("설명회 미참석 시 입찰참가자격 박탈", "설명회 참석 업체만 입찰할 수 있음", "설명회 참석자 명부에
# 서명한 업체만 제안서 제출 가능"): such a session precedes bidding, so it is the orderer's briefing, not the proposer's
# presentation. Read with the V22_ATTEND_FORMS shapes only when no briefing or presentation is named on the line or in the
# six lines above, outside evaluation sections and presentation contexts. Also reads, for a named or such a bare briefing,
# attendees granted the right in other words ("과업설명회 참석 업체에 한하여 제안서 제출 자격이 주어짐"), absence that leaves the
# bidder without it ("불참 업체에 대하여는 입찰참가자격을 인정하지 않음"), a qualification item naming the attendance certificate
# ("사업설명회 참석 확인서를 발급받은 업체") and attendance as the bid's condition ("본 입찰은 사업설명회 참석을 조건으로
# 합니다"). Attendance stated as not required stays out. Requires V22_ATTEND_FORMS. False = current behaviour.
V22_ATTEND_FORMS2 = False
# v21: a joint-member share floor stated as a part of the whole ("구성원별 최소 지분은 전체의 3%로 함", "각 구성원의 지분은
# 전체 계약금액의 3% 이상", "구성원별 참여지분은 공동수급 총 지분의 3% 이상"): "전체의" / "총 지분의" / "전체 계약금액의" name the
# base of the percentage, so the line is judged by the same literal share check without them (a representative's share
# stays out); "최저" (최저한도, 최저선) reads as the floor word 최소, "최저가" does not. Requires V21_SHARE_FORMS.
# False = current behaviour.
V21_SHARE_FORMS2 = False
# v5, v6, v7: a bidder-location restriction stated in an attachment line that states the participation qualification
# ("입찰참가자격: X에 본점을 둔 업체", "입찰참가자는 X 관내에 본사가 소재하여야 함", "| 참가자격 | X 소재 업체 |", "본점 소재지가
# X인 업체만 제안서를 제출할 수 있음"; for v7 an attachment's 시·도 count only when the 공고문 states no bidder-location
# restriction of its own, since the 입찰공고 sets the qualification), or in the 공고문 outside evaluation and document lists as
# a limit to firms proving their location ("X 소재 업체임을 증명하는 … 제출한 업체에 한함"), a location field of the
# qualification section ("주사무소 소재지 : X", "소재지 : X"; not a street address), a restriction label valued by a region
# ("| 참가제한 | X 소재 업체 |", "제한사항 | 지역(X)"), "X에 본사가 있는 기업" in the qualification section, "X 업체만 참가
# 가능" / "입찰참가 자격 : X 업체", "X 외의 업체는 … 참가할 수 없음", "X에 소재하지 않는 업체의 입찰서는 무효", "X 지역업체
# 대상 입찰" or "투찰 가능 업체: X 소재 업체". Delivery, work-site, facility, joint-partner and bonus clauses stay out.
# Applies after REGION_CLAUSE_FORMS finds nothing. False = current behaviour.
REGION_CLAUSE_FORMS2 = False
# v11, v13-v18 (the shared size-class reading): a clause that names the SME class only to take its small part names
# 소기업·소상공인: "중소기업(자) 중 / 중에서 / 가운데 [… 에 따른] 소기업·소상공인 …", "중소기업(소기업·소상공인에 한함 / 에
# 한정 / 만 / 만 해당)", "소기업 또는 소상공인에 해당하는(인) 중소기업자", "중소기업자로서 소기업·소상공인에 해당하는 자"
# (u2_size_subset). "중소기업 중소기업" and "(소기업·소상공인 포함)" name no part. False = current behaviour.
U2_SIZE_SUBSET = False
# v11, v13-v18 (the shared size-class reading): a clause that bars 중기업 in words the older readers miss ("중기업의 (입찰)
# 참가를 제한", "중기업 확인서 소지자는 입찰에 참가할 수 없음", "중기업은 입찰참가자격이 없습니다", "중기업은 본 입찰의 참가대상이
# 아닙니다") restricts to 소기업·소상공인; "…제한하지 않습니다", "…제한 없음" and "…제한하는 경우" bar nobody
# (u2_size_subset). False = current behaviour.
U2_MEDIUM_BARRED = False
# v13 (with SMALL_TEXT_CLAUSE or V13_SMALL_ANYWHERE): the literal small-only clause detectors read "중소기업(자) 가운데 /
# 중 [… 에 따른] 소기업·소상공인", "중소기업(소기업·소상공인에 한함)", "소기업·소상공인에 해당하는(인) 중소기업자" and
# "중소기업자로서 소기업·소상공인에 해당하는 자" as naming the small class, not as admitting 중소기업자 (law names are then removed
# as in the shared reading), and such a clause stating who may bid ("…인 자", "…에 해당하는 업체", "…만을 대상으로", "…과의
# 우선조달계약 대상", "…으로서") is a participation clause (u2_size_subset). False = current behaviour.
U2_V13_SUBSET_TEXT = False
# v14/v15/v17 with T4_SIZE_WIDE (and v16/v18 with T4_SIZE_WIDE_ABS): outside the qualification section, a clause that bars
# 중기업 ("중기업 확인서 소지자는 입찰에 참가할 수 없음") or names the small part of the SME class as who may bid ("중소기업
# 중 소기업·소상공인인 자", "중소기업자로서 소기업·소상공인에 해당하는 자") limits bidders to 소기업·소상공인 when the size
# reading gives it that class (U2_SIZE_SUBSET, U2_MEDIUM_BARRED) (u2_size_subset). False = current behaviour.
U2_WIDE_SUBSET = False
# Items judged as buying a competition service when a service notice without a catalog object still asks in its 공고문 for
# the 직접생산 certificate ("⑤ 직접생산확인증명서 1부", "※ 직접생산확인증명서는 … 유효기간 내에 있어야 함"; conditional,
# evaluation and sanction lines aside) and its licence names that service (행사대행업, 소프트웨어사업자, 비디오물제작업 …) at
# an admitted 추정가격: the certificate exists only for 판로지원법 제9조 competition products; () = current behaviour.
OBJ_DP_LICENSE = ()
# v20 stage: a notice that writes out a listed IT-service 세부품명 (정보시스템개발서비스, 소프트웨어유지및지원서비스 …; e.g. in its
# 직접생산 certificate or 세부품명 line) is an SW candidate as when it gives the 8111 code, also when its title is withheld
# and no SW사업자 requirement remains. False = current behaviour.
V20_STAGE_IT_NAME = False
# v20: a service notice whose title names a software deliverable (정보시스템, 홈페이지, 플랫폼, 대시보드, 앱, 솔루션, DB, a business
# 프로그램 …) with development, operation or use work ("CRM 프로그램 2차 개발", "데이터 대시보드 운영·고도화", "스크랩 프로그램 이용")
# is an SW project (소프트웨어 진흥법 제2조) although the SW-scope reading and the v20 stage did not make it one. Education and
# event 프로그램, titles facts.not_sw_title refuses or headed by study, audit, teaching or event work, and objects the v20 stage
# read as non-software stay out; v20 fires only when neither statement reader finds the 제48조 statement. False = current.
V20_TITLE_SW = False
# v12: a service notice whose object the strict classification (catalog.classify under SCOPE_FIXES: the title words 페어 and
# 영상·인쇄 광고물, exhibition and conference wording, the orderer's 나라장터 registration of a designated competition product)
# identifies as a listed competition service is no general-product purchase; v12 does not judge it. False = current behaviour.
V12_STRICT_OBJECT = False
# v10: a clause that only states that the certificates are checked in the 종합정보망 ("‘…확인서’ 및 ‘직접생산확인증명서’가 …
# 확인되지 않거나 … 입찰참가자격이 없습니다", also wrapped over two lines) verifies a certificate and is no possession requirement
# (as C2_DP_PRESENT_LITERAL treats the "확인이 안 될 경우" form). False = current behaviour.
V10_VERIFY_NOTE = False
# v14-v18: an article title in parentheses ("제2조의2(중소기업자의 우선조달계약)") names no size class; it is removed
# before the clause's class is read. False = current behaviour.
W4_SIZE_ARTICLE_TITLE = False
# v4: a 공고문 qualification record clause naming a public buyer kind next to the record ("… 공공기관 고압가스용기 납품 완료
# 이력을 보유하여야 합니다"), read by the model as limited to specific orderers, when the requirement is worded "…이력/실적/경험
# … 보유하여야/있어야" beyond the shared predicate's reach; the other gates of the model-reading path hold (w3_v4_held).
# Consulted only when no other v4 source fires. False = current behaviour.
W3_V4_HELD_HISTORY = False
# v4: a record requirement whose buyers are care or welfare facility kinds outside the buyer vocabulary ("사회복지관련 기관이나,
# 노인관련시설, 또는 장기요양기관에서 … 운영 실적이 있는 업체") is limited to specific institutions (w3_v4_care). Consulted only
# when no other v4 source fires. False = current behaviour.
W3_V4_CARE_KINDS = False
# v2, v4, v8: a 공고문 qualification line stating a held record is a record requirement although a parenthetical says how to
# prove it ("…수행경험을 보유한 기관 (관련 실적증명서류 제출, …)"): the PERF_UNREAD exclusion words are read outside parentheses,
# the other PERF_UNREAD gates hold (w3_rec_paren). Consulted only when no other source fires. False = current behaviour.
W3_REC_PAREN = False
# v4: a statement limiting the counted records to schools or a public-buyer kind ("실적증명서 1부 [중·고등학교 실적만 해당]")
# when the 공고문 qualification section itself requires a record; statements without such a requirement or inside 적격심사·평가
# text stay out (w3_v4_only). Consulted only when no other v4 source fires. False = current behaviour.
W3_V4_ONLY_REQ = False
# v2, v4, v8: a 공고문 line outside the qualification section that the perf family read as a participation condition is a
# record requirement in an evaluation, document or notes block too when its own wording makes it one: a consequence for bidders
# without the record ("…투찰 할 경우 사전 부적격", "참가할 수 없음"), or a bidder predicate ("…실적이 있는 업체") with no scoring
# or 적격심사 wording in it or in the six lines above; forms, document notes and staffing stay out (w3_rec_context). Consulted
# only when no other source fires. False = current behaviour.
W3_REC_CONTEXT = False
# v19: a 확약서 entry of a 공고문 document list whose other entries name the 적격심사 submission ("적격심사 신청서 및 관련
# 증빙자료 제출") is due after the bid opening; a CPU list timing is withdrawn there unless the list heading names the bid or
# estimate submission; a model reading placing the demand before the bid stands (w3_v19_review). False = current behaviour.
W3_V19_REVIEW_LIST = False
# v7 (with REGION_CLAUSE_FORMS2): below T, a bidder-location clause in an attachment's qualification section naming two or more
# 시·도 ("경기도 또는 서울 내 사업장이 위치해 있으며 …", 과업지시서) extends the restriction as the 공고문 clauses do; JV partner,
# title and "지역제한 없음" clauses excluded (w3_v7_attach). False = current behaviour.
W3_V7_ATTACH_REGION = False
# v4: a qualification-section record clause (공고문 or attachment) the perf family read as limited to specific orderers keeps
# that reading when the CPU buyer reader finds no buyer relation at all (a buyer outside its vocabulary: 프로축구단, 제1금융권
# 은행, 대기업 …), the clause admits no private or general party and states a requirement (w3_v4_model). Consulted only
# when no other v4 source fires. False = current behaviour.
W3_V4_MODEL_BUYER = False
# v13: small-only participation clauses in wordings and layouts the V13_SMALL_ANYWHERE reader misses: other bullets
# (▣ ▶ ◎ ➂ ⑴ ㉮ *, table rows, bracketed labels), label-and-value lines ("(입찰참가자격) … 소상공인", "입찰자격: 소기업/소상공인",
# "기업규모: 소기업, 소상공인"), rule shapes ("…에 해당할 것", "…으로 한다", "…전용 입찰", "소기업·소상공인 제한(…)") and bars
# on everybody else ("소기업·소상공인이 아닌 중소기업자는 … 불가", "…확인서가 없는 업체의 입찰은 무효", "중기업 확인서를 소지한
# 업체는 입찰에 참가할 수 없습니다") (x2_v13_forms). Method statements and notes stay out. False = current behaviour.
V13_FORMS2 = False
# v10: a clause that names the 직접생산 certificate only to waive it ("…미소지 업체도 입찰에 참가할 수 있음", "…와 관계없이 … 참가
# 가능", "…요건이 아님", "제출하지 않아도", "생략", "해당 없음", "적용하지 않"), to offer another route ("…또는 동등 이상의 …",
# "…또는 제조사 공급확약서", "…를 소지하거나 …") or to ask it of some firms only ("(해당 업체에 한함)", "(해당 시)", "(필요 시)")
# requires nothing of every bidder: no possession requirement (판로지원법 제9조 checks each bidder). False = current behaviour.
V10_WAIVER = False
# v11: a size clause that admits 중견기업 next to 중소기업 ("중소기업 또는 「중견기업 …특별법」에 따른 중견기업", "중소기업 및
# 중견기업", "중소·중견기업으로서 …") or, naming nobody as who may bid, states no size limit ("기업규모 제한 없음", "중소기업
# 여부와 관계없이", "확인서 제출 불요") or asks the certificate of some firms only ("(해당 시)", "(필요 시)") does not limit a
# competition-product bid to SMEs (판로지원법 제7조①); with only such clauses (and v11 notes) the SME restriction is absent.
# Clauses that bar 중견기업 stay restrictions. False = current behaviour.
V11_WIDENED = False
# Evidence text: a clause the layout wrapped onto the next line is quoted to its end (csvout.wrap). False = current behaviour.
EVIDENCE_WRAP = False
# X3_V19_TIME (v19): a third-party pledge line the other checks leave untimed counts as demanded at the bid when its
# clause sets it at the bid in other words: "입찰에 앞서", 접수·참여·응찰 시, in the bid or proposal documents, a bar on
# bidders without it, a 참가 요건 label, an "(입찰서류)" entry, or issuance/holding before the bid in these words with
# the copy handed over at contract (x3_v19.py). False = current behaviour.
X3_V19_TIME = False
# X3_V19_ISSUER (v19): a pledge line naming a third-party issuer outside the judge lists (생산업체, 제작업체, 개발사,
# 수입사·수입원, 파트너사, 공급권자, 저작권자, an anonymised company token) or the maker's 공급·기술지원 보증서 is judged
# like one naming 제조사 (x3_v19.py). False = current behaviour.
X3_V19_ISSUER = False
# X3_V20_NEEDS_BASIS (v20): a 대기업 참여제한 statement counts only when its clause or the heading above it names its
# basis (소프트웨어 진흥법, 제48조, the 중소 SW사업자 지침 or its 과기정통부 고시): 지침 제3조② "적용 근거 포함" (x3_v20.py).
# False = current behaviour.
X3_V20_NEEDS_BASIS = False
# X3_V20_BAND_MORE (v20): band statements written "N억원 이하", in Korean numerals, in won or as "80억원 이상" name a
# 지침 band too; when every band line misfits the 사업금액 (추정가격 + VAT) the notice states another project's restriction,
# on the main path and in the v20 stage (x3_v20.py). False = current behaviour.
X3_V20_BAND_MORE = False

# Red team X3 (round 6, x3_v148.placed_inst / placed_network): a v1 limit to institution kinds written with the bidding
# act or a target label as subject ("입찰참가는 … 산학협력단만 가능", "※ 입찰참가는 … 연구기관에 한합니다", "| 참가대상 | … |"),
# with general or for-profit firms barred ("일반 영리업체 참가 불가"), or in an attachment for the performing institution
# ("과업 수행기관은 … 산학협력단으로 한정", under "수행기관 자격"); and a nationwide network the bidder must set up ("각 시·도에
# 지사를 두어야"). Consulted only when no other v1 source fires. False = current behaviour.
X3_V1_PLACED = False

# Red team X3 (round 6, x3_v148.placed_buyer): a record counted only for a buyer kind (집행기준 제5조④3) in a 공고문 note
# or an attachment line with a bidder subject or participation label: school, kindergarten, university and hospital
# buyers too ("납품 실적은 공공기관 및 학교 납품분에 한하여 인정"), private records refused in other words ("민간 실적은 …
# 보지 않습니다"), or an attachment or 공고문 bidding line whose bidder or performing firm must hold a record for a named
# public buyer kind. Consulted only when no other v4 source fires. False = current behaviour.
X3_V4_PLACED = False

# Red team X3 (round 6, x3_v148.placed_region / placed_record): v8 when one side (record or bidder location) is written
# only in an attachment or a 공고문 bidding/notes line: a location under a procedure-prefixed participation label
# ("제안참가자격 : 전라북도에 주된 사무소를 둔 업체"), a participation area ("입찰참가 가능 지역은 강원특별자치도"), a location
# limit on the firm that performs the work ("과업 수행업체는 충청북도 내에 본사를 둔 업체로 한정"), or a held record of that
# firm or under such a label ("과업 수행업체는 … 실적이 있어야"). Consulted only when no other v8 source fires. False =
# current behaviour.
X3_V8_PLACED = False
# v7: below T, the union of the region-restriction lines extends a restriction to an adjacent area only when one clause
# itself names two or more 시·도; clauses that each name one (a 시·군 clause next to another clause on a different 시·도)
# restrict to conflicting areas, not to one area and its neighbours. The per-clause readers are unchanged.
# False = current behaviour.
V7_ONE_CLAUSE = False
# v24 (with V24_REGION_NONE_STATED): a "지역제한 없음" statement about some bidders only (공동수급 구성원·참여업체, the lead's
# partners, a licence-holder class as its subject: "…허가 업체는 지역제한 하지 않음", "대표사 이외 구성원은 지역제한을 하지
# 않습니다") or next to a restriction that applies ("대표사 지역 제한적용") leaves the notice's own restriction in place; it
# is no statement that the notice has none. False = current behaviour.
V24_NONE_SCOPED = False
# Consumer-only: compare typed total 추정가격 with registered P and budget with B.
# Keep select(), prompt/schema, consume() and the legacy OFF path unchanged.
V24_TYPED_AMOUNT = False
# RT-B: compare explicit competitive method fields and complete licence alternatives.
RTB_V24_METHOD_BODY = False
RTB_V24_LICENCE_OR = False

# RT-C: literal software development/maintenance tasks in full task documents,
# with a conservative whole-document participation-statement absence check.
V20_TASK_SW = False

READ_SKIP_EMPTY_LABELS = False

# RT-E2: all-document industry absence, independent of the legacy switch.
V24_LICENCE_SILENT2 = False

# RT-P: narrow source-reading correction; opt-in.
RTP_V24_ANNUAL_PARTS = False

# RT-P: narrow source-reading correction; opt-in.
RTP_V24_CAPABILITY_OR = False

# RT-P: narrow source-reading correction; opt-in.
RTP_V24_FACTORY_CODE = False

# RT-P: narrow source-reading correction; opt-in.
RTP_V24_PARTNER_REGION = False

# RT-P: narrow source-reading correction; opt-in.
RTP_V24_REGION_ALIASES = False

# RT-R: delivery record OR prior quality approval; CPU matching only.
V4_CERT_ALTERNATIVE = False

# Explicit installed maker plus a mandatory same-maker acquisition.
V9_NEW_SAME_MAKER = False

# RT-P2: narrow source-reading correction; opt-in.
RTP2_V13_NOTE_BOUNDARY = False

# RT-P2: narrow source-reading correction; opt-in.
RTP2_V13_CONDITIONAL_HEADER = False
RTS_V14_SW_ADMISSION = False
RTS_V15_NONPROFIT_NOTE = False

# RT-T: opt-in witness correction.
RTT_V2_NONE = False

# RT-T: opt-in witness correction.
RTT_V2_NONRECORD_OR = False

# RT-T: opt-in witness correction.
RTT_V2_EVAL_CONTEXT = False
