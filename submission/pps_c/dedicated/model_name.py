# -*- coding: utf-8 -*-
"""v9 dedicated stage (switches.DEDICATED = ('v9',)): 규격서·제안요청서·과업지시서(또는 규격을 담은 공고문)의 특정 제조사·모델명 명시.

CPU: 문서의 각 줄을 점수화해 발췌 줄(최대 MAX_EXCERPTS)을 고르고, 최고 점수가 GATE_SCORE 이상인 공고만 모델에 묻는다.
Model: 발췌 안에서 제조사·브랜드·모델·제품명으로 보이는 표현을 모두 찾아 종류(kind)와 역할(role)로 분류한 JSON 하나.
CPU rule: kind ∈ FIRE_KINDS 이고 role ∈ FIRE_ROLES 인 표현이 하나라도 있으면 위반, 근거는 그 표현이 실제로 적힌 원문 줄.
판독이 없으면(모의 엔진의 빈 답, journal 없는 replay) 명시적 이름표 줄("제조사·모델명 : …")만 보는 CPU 규칙으로 판정한다.
Design: scratchpad h08/clean2/model_name/BLUEPRINT.md.
"""
import json
import re

FAM = 'd_model_name'
PROMPT_TOKEN_LIMIT = 12000
MAX_TOKENS = 400
MAX_ITEMS = 12
MAX_EXCERPTS = 24
MIN_CAP = 6
MAX_EXCERPT_CHARS = 150
CONTEXT_CHARS = 60
MIN_SCORE = 3
GATE_SCORE = 5
KINDS = ('maker_or_brand', 'model_or_product_name', 'material_grade_or_standard', 'generic_or_unit', 'requirement_id_or_code')
ROLES = ('supplied_item', 'supplied_component', 'performance_equipment', 'computer_component', 'existing_equipment',
         'environment_or_tool', 'bidder_form_or_instruction', 'example_only')
FIRE_KINDS = ('maker_or_brand', 'model_or_product_name')
FIRE_ROLES = ('supplied_item', 'supplied_component', 'performance_equipment')
# Literal alternative (blueprint §6): CPU/GPU/chipset/OS named inside a supplied computer's spec also fire. 6/6 dev zeros say no.
COMPUTER_PARTS_FIRE = False
DOC_ORDER = {'규격서': 0, '제안요청서': 1, '과업지시서': 2, '공고문': 3}

# ------------------------------------------------------------------------------------------------ text normalisation
ANON_RE = re.compile(r'\[(?:지역|기관|수요기관|상세주소|시설명|URL|담당자|전화|이메일|주소|성명|인명)[^\]]*\]')
WS_RE = re.compile(r'[ \t　]+')


def clean(text):
    return WS_RE.sub(' ', ANON_RE.sub(' ', text or '').replace('​', ' ')).strip()


# ------------------------------------------------------------------------------------------------ candidate patterns
LABEL_WORDS = (r'(?:모델\s?명|모델|제조사\s?명|제조사|제조업체|제조원|제작사|메이커|브랜드|상표\s?명|상표|제품\s?명|형식\s?명|형명|기종'
               r'|Model(?:\s?(?:No|Name))?|MODEL|Brand|Maker|Manufacturer|Mfr)')
P1_RE = re.compile(LABEL_WORDS + r'\s*[:：|]\s*([^\s|:：]{2,})')
LABEL_ONLY_RE = re.compile(r'^\(?\s*' + LABEL_WORDS + r'\s*\)?\s*[:：]?\s*$')
TABLE_LABEL_RE = re.compile(r'제품\s?명|제조사\s?명|제조사|모델\s?명|브랜드|상표|제작사|제조원|공급자|원산지|제조국|메이커')
INSTRUCTION_RE = re.compile(r'명시|표시|표기|기재|제출|작성|부착|첨부|확인서|증명서|명판|라벨|각인|필히|하여야|해야|바랍니다|기입|제안서에|서식')
P4A_RE = re.compile(r'[가-힣]{2,}\s?\(\s?((?:[A-Z][A-Za-z]{2,}|[A-Z][a-z]+[A-Z][A-Za-z]+)(?:\s(?:[A-Z][A-Za-z]{1,}|&))*)\s?\)')
P4B_RE = re.compile(r'[A-Za-z][A-Za-z0-9 .+-]{2,}\d[A-Za-z0-9]*\s?\(\s?[가-힣][가-힣 ]{1,}\s?\)')
ACRONYM_GLOSS_RE = re.compile(r'[가-힣]\s?\(\s?[A-Z][A-Z&-]{1,7}\s?\)')
GLOSS_RE = re.compile(r'\(\s?[A-Z][A-Z&-]{1,7}\s?\)')
MAKER_LIST_RE = re.compile(
    r'삼성전자|엘지전자|LG전자|한화비전|한화에어로|한화정밀|현대자동차|기아자동차|두산밥캣|두산인프라|대동공업|LS엠트론|엘에스엠트론|국제종합기계|아세아텍|동양물산|구보다|쿠보타|얀마|존디어|'
    r'지멘스|필립스|도시바|캐논|니콘|소니|파나소닉|히타치|미쓰비시|후지필름|올림푸스|시마즈|애질런트|아질런트|써모피셔|퍼킨엘머|워터스|브루커|자이스|라이카|보쉬|힐티|마끼다|디월트|'
    r'신젠타|팜한농|경농|농협케미컬|동방아그로|성보화학|한국삼공|팜아그로텍|바이엘크롭|'
    r'경동나비엔|귀뚜라미|린나이|캐리어에어컨|위니아|쿠쿠전자|코웨이|퍼시스|리바트|시디즈|듀오백|아이디스|하이크비전|다후아|엑시스|'
    r'애플|Apple|Samsung|Hanwha|Siemens|Philips|Toshiba|Canon|Nikon|Sony|Panasonic|Hitachi|Mitsubishi|Fujifilm|Olympus|Shimadzu|Agilent|'
    r'Thermo\s?Fisher|PerkinElmer|Waters|Bruker|Zeiss|Leica|Bosch|Hilti|Makita|DeWalt|'
    r'Trimble|Topcon|Sokkia|Garmin|Honeywell|Emerson|Yokogawa|Endress\s?\+?\s?Hauser|Schneider|ABB|Rockwell|Omron|Keyence|Festo|SMC|Parker|'
    r'Danfoss|Grundfos|Wilo|KSB|Ebara|'
    r'Cisco|Juniper|Fortinet|Palo\s?Alto|Arista|Aruba|Ubiquiti|Huawei|Nutanix|VMware|Veeam|Synology|QNAP|NetApp|Supermicro|GIGABYTE|ASUS|Lenovo|Dell|HPE|'
    r'Hikvision|Dahua|Axis|Uniview|Avigilon|Milestone|Genetec|Vivotek|IDIS|'
    r'DJI|Parrot|Autel|Skydio|Yuneec|Durridge|Airtame|Cetac|Labogene|LABOGENE|Akron\s?Brass|Kubota|Yanmar|Deere|Caterpillar|Komatsu|Volvo|Doosan|'
    r'Hyundai|Kia|Daedong|TYM')
TOKEN_RE = re.compile(
    r'(?<![A-Za-z0-9가-힣])('
    r'[A-Za-z]{1,10}[- ]?\d{1,5}[A-Za-z0-9]*(?:[-/][A-Za-z0-9]+)*'
    r'|[A-Z]{2,}(?:-[A-Z]{2,})?\s\d{3,5}[A-Za-z]*'
    r'|\d{2,5}[A-Z]{2,}[A-Za-z0-9]*'
    r'|[A-Z]{2,}[A-Z0-9]*-[A-Z0-9]{2,}'
    r')(?![A-Za-z0-9])')
UNIT_SUFFIX_RE = re.compile(
    r'^\d+(?:hz|khz|mhz|ghz|kw|kva|kwh|kcal|mah|wh|ml|mm|cm|km|kg|kgf|ea|pcs|set|ppm|ppb|rpm|psi|bar|lm|lx|db|gb|tb|mb|kb|gbps|mbps|kbps|bps'
    r'|inch|nm|um|mpa|kpa|hpa|kn|ton|hr|min|sec|ms|fps|dpi|ppi|px|bit|byte|ch|port|pole|phase|ph|lpm|gpm|cfm|ah|hp|ps|cc|nit|nits|lux|deg'
    r'|rad|mmhg|mtr|m|g|w|v|a|t|p|k|x|d|c|f|s|l|n|h)$', re.I)
STD_PREFIX = set(
    """ISO IEC KS KSA KSB KSC KSD KSF KSM KSL KSE EN DIN JIS ASTM ANSI BS UL IP IPX IK MIL STD USB HDMI DDR GDDR LPDDR PCIE PCI SATA NVME RJ RS
    CAT WIFI BT IEEE H DMX E AC DC V A W HZ KHZ MHZ GHZ KRCCS KCS KDS KRC SPS SMPTE ST HAST ROS CUDA BIL NICE SCR KIS KR TOPS SDI
    SFR PER IFR DAR TER SER QUR COR PMR PSR ECR MAR PLR DER FUR
    STS SUS SS SM SC SCP SCPH SPP SPPS STPG SGP SPHC SPCC SB WC MC BB FA D DN PN SCH M PT NO VER REV Q AAA AA BBB CCC A1 A2 A3 B1 B2 B3 C1 C2 C3
    CO NO2 SO2 O3 PM CO2 NOX SOX H2S NH3 CH4 UTP FTP STP OM OS1 OS2 SFP QSFP RJ45 RS232 RS485 IE HD FHD UHD QHD WQHD K P I MPEG H264 H265
    HEVC AVC IPV4 IPV6 TCP UDP HTTP HTTPS SNMP NTP LTE G KC CE FCC ROHS REACH GMP GIP GLP HACCP ISO9001 ISO14001 TTA ETRI
    UCP UCF UCPA UCFL UCT NTN SKF FAG NSK KOYO XLR BNC DVI VGA DP TYPE GEN E1 T1 AWG SWG NPT BSP PF ZN AL CU FE""".split())
ENGLISH_GENERIC = set(
    """Processor Memory Storage Network Networking Chipset Graphics Rear Ports Audio Output Operating System Power Supply Chassis Volume
    Weight Dimensions Included Box Adapter Cord Warranty Year Feature Specification Main Type Gen Up Unified Interface Bandwidth Ethernet
    Wireless Bluetooth Full Ultra High Low Max Min Yes No On Off Auto Manual Standard Option Optional Set Sets Unit Units Item Items Total
    Sub Note Remark Remarks Description Name Model Brand Maker Size Color Colour Length Width Height Depth Input Display Control Controller
    Sensor Sensors Battery Charger Cable Cables Kit Case Cover Body Board Module Modules Port Device Devices Camera Lens Data Cloud Software
    Hardware Service Services Support Version Series Pro Plus Mini Micro Nano Mega Giga Smart Digital Analog Mobile Portable Remote Local
    Global Public Private Open Free Basic Advanced Premium Professional Enterprise Business Home Office Lab Test Table Chair Drawer Utility
    Exhaust Facilities Facility Light Lighting Console Computer Application Applications Solution Solutions Platform Platforms Program
    Programs Project Projects Management Manager Monitor Monitoring Analysis Analyzer Report Reports Result Results Status Mode Modes Level
    Levels Class Classes Range Rate Speed Time Date Day Week Month Quarter Half Zone Area Region Site Sites Place Location Address Phone Fax
    Email Web Page Pages File Files Folder Document Documents Form Forms List Lists Field Fields Value Values Key Keys Code Codes Number
    Numbers Text Image Images Video Videos Voice Sound Music Art Design Style Format Formats Layout Print Printer Scan Scanner Copy Copier
    Machine Machines Engine Motor Pump Fan Valve Pipe Tank Filter Heater Cooler Chiller Boiler Compressor Generator Transformer Switch Router
    Server Client Edge Core Base Station Point Points Access Security Safety Fire Water Air Gas Oil Steam Heat Cold Hot Ice Snow Rain Wind
    Sun Solar Green Blue Red Black White Gray Grey Silver Gold Copper Iron Steel Stainless Aluminum Plastic Rubber Glass Wood Paper Cloth
    Fabric Leather Stone Concrete Asphalt Cement Sand Gravel Soil Clay Brick Tile Block Panel Sheet Plate Bar Rod Wire Rope Chain Belt Gear
    Bearing Shaft Wheel Tire Tyre Axle Frame Door Window Roof Floor Wall Ceiling Stair Elevator Escalator Bridge Road Rail Track Way Path
    Line Lines Grid Net Mesh Loop Ring Band Bands Tone Tones""".split())
ENGLISH_GENERIC_UPPER = {w.upper() for w in ENGLISH_GENERIC} | set(
    """CPU GPU RAM ROM SSD HDD NIC LAN WAN LED LCD OLED USB HDMI VGA DVI PDF XML JSON HTML API SDK URL WWW HTTP TCP UDP IOT ICT GIS BIM CAD
    ERP CRM SCM MES PLC HMI DCS UPS PTZ NVR DVR CCTV RFID NFC QR OCR VOC SLA KPI RFP RFI RFQ TBD ETC WDH LWH SET SETS EA PCS KTX SRT ITX GTX
    BRT LRT AGT PRT LTE WCDMA GSM CDMA OTT VOD IPTV DMB DAB FM AM MW SW LW HF VHF UHF SHF EHF GNSS GPS GLONASS QZSS SBAS WAAS EGNOS MSAS
    GAGAN RTK PPP DGPS NTRIP DXF SHP DWG DGN GML KML GPX CSV TXT DOC DOCX XLS XLSX PPT PPTX HWP HWPX ZIP RAR JPG JPEG PNG GIF BMP TIF TIFF
    SVG MP3 MP4 AVI MKV MOV WMV WAV FLAC AAC OGG ISO IEC KS KC CE UL FCC ROHS REACH GMP GIP GLP GCP HACCP ISMS CSAP GS NEP NET EPC OEM ODM
    SI SM ITO BPO PMO QA QC PM PL PMS BOM BOQ LPG LNG CNG DDP AI ML DX AX GX ESG CSR ROI TF TFT MOU MOA NDA VIP CEO CTO CFO COO IR PR HR ITS
    LMS CMS DMS EMS BMS FMS VMS WAS DBMS RDBMS CSS JS REST SOAP SSO PKI DMZ IDS WAF DRM DLP NAC EDR SIEM SOC NOC MSP SAAS PAAS IAAS KOLAS
    KTL KTR KCL FITI KATRI KOTITI KISA NIA NIPA KISTI KIST KAIST POSTECH UNIST GIST DGIST KEPCO KOGAS KORAIL BMC UNESCO UN OECD WHO FAO
    ASEAN APEC EU USA UK MICE B2B B2C B2G G2B O2O R&D RND POC MVP AS A/S ASAP FAQ TBA TBC ETA ETD MIL SPEC STD CAL PPK LIDAR RADAR SONAR
    EIRP RSSI SNR THD PF EMC EMI ESD RH DB DBA SPL HVAC BEMS HEMS EV ESS PCS PV BIPV LOTO HAZOP HSE OHSAS GHG LCA EPD VOCS BOD COD SS TN TP
    DO TOC TDS NTU SUS STS SCPH SPP STPG SGP SPHC SPCC AL CU FE ZN PE PP PVC PET PU PA POM ABS PC FRP GRP CFRP EPDM NBR PTFE UHMW
    PLR DER SER FER NFR SFR TR UR CR BR SR AR VR XR MR""".split())
CAP_WORD_RE = re.compile(r'(?<![A-Za-z])([A-Z][a-z]{2,}|[A-Z]{2,})(?![a-z])')
KOREAN_MAKER_RE = re.compile(r'㈜|\(주\)|주식회사|社|[가-힣]{2,}사\s?제품|정품|OEM|제조\s?:|제작\s?:|공급자|주재료 공급자|원산지|수입원|제조국')
CONTEXT_RE = re.compile(r'동등|동급|이상|호환|기종|제품|장비|기기|모델|사양|규격')
TRADEMARK_RE = re.compile(r'[®™Ⓡ]')
NOISE_RE = re.compile(r'https?://|www\.|@|\d{2,4}[-.]\d{1,2}[-.]\d{1,2}|\d{6,}|\d{2,4}-\d{3,4}-\d{4}|공고번호|입찰공고|접수|개찰|마감')
BOILERPLATE_RE = re.compile(r'신용평가|평가정보|신용정보|기업평가|한/글 뷰어|정품을 구매하시면|신용등급|회사채|기업어음')
REQ_ID_RE = re.compile(r'^[A-Z]{2}R-\d{3}$')
EQUIV_NEXT_RE = re.compile(r'동등|동급|타사|호환|또는 동|이상의 (제품|물품|장비)')
SECTION_HINT_RE = re.compile(r'현황|보유|기존|운영\s?중|도입|납품|구매|구성|사양|규격|목록|내역|제공|투입|유지보수|대상|장비|물품|품목')
VALUE_STOP_RE = re.compile(r'^(등|및|을|를|이|가|은|는|의|과|와|에|으로|로|또는)$')


def token_is_standard(tok):
    t = tok.strip()
    if UNIT_SUFFIX_RE.match(t):
        return True
    letters = re.sub(r'[^A-Za-z]', '', t).upper()
    if letters in STD_PREFIX:
        return True
    head = re.match(r'[A-Za-z]+', t)
    if head and head.group(0).upper() in STD_PREFIX and len(head.group(0)) <= 4:
        return True
    if re.match(r'^(?:S\d{2}C|SS\d{3}|SM\d{2,3}|STS\d{3}|SUS\d{3}|SCPH?\d{2}|A\d{3}|WC-\d|MC-\d|BB-\d|FA-\d|SB-\d|D\d{2}|No\.?\s?\d+|Ver\.?\s?\d'
                r'|Rev\.?\s?\d|v\d)', t, re.I):
        return True
    if re.fullmatch(r'[A-Z]{1,2}\d', t) or REQ_ID_RE.match(t):
        return True
    return False


# A standard reference is one unit ("KS F 2357", "ISO 9001", "MIL-STD-810G", "KRCCS 67 90 09"): masked before the token
# and gloss rules, which would otherwise read its number as a model code.
STD_REF_RE = re.compile(r'(?<![A-Za-z0-9])(?:KS|ISO|IEC|EN|JIS|ASTM|ANSI|DIN|BS|KRCCS|KCS|KDS|KRC|SPS|SMPTE|IEEE|MIL-STD|MIL|UL)'
                        r'(?:\s?[A-Z]{1,2})?\s?\d[\d.\-:/ ]*(?:[A-Z](?![A-Za-z]))?')


def mask_standards(s):
    return STD_REF_RE.sub(' ', s)


def model_tokens(s):
    return [t for t in TOKEN_RE.findall(mask_standards(s)) if not token_is_standard(t)]


def score_line(s, doc_type):
    """(score, matched substring) of a cleaned line."""
    if not s or len(s) < 4:
        return 0, ''
    score, matched = 0, ''
    if NOISE_RE.search(s) and not P1_RE.search(s):
        score -= 2
    if BOILERPLATE_RE.search(s):
        score -= 6
    m = P1_RE.search(s)
    if m and not VALUE_STOP_RE.match(m.group(1)):
        score += 5
        matched = m.group(0)
        if INSTRUCTION_RE.search(s):
            score -= 3
    else:
        m = None
    masked = mask_standards(s)
    m4 = P4A_RE.search(masked) or P4B_RE.search(masked)
    if m4 and not (P4A_RE.search(masked) and re.match(r'^[A-Z]+$', P4A_RE.search(masked).group(1).replace(' ', ''))):
        score += 3
        matched = matched or m4.group(0)
    mk = MAKER_LIST_RE.search(s)
    if mk:
        score += 3
        matched = matched or mk.group(0)
    toks = model_tokens(s)
    if toks:
        score += 3 + (1 if any('-' in t for t in toks) else 0)
        matched = matched or toks[0]
    caps = [w for w in CAP_WORD_RE.findall(s) if w not in ENGLISH_GENERIC and w.upper() not in STD_PREFIX and len(w) >= 3]
    if caps:
        glossed = {g.strip('() ').strip() for g in GLOSS_RE.findall(s)} if ACRONYM_GLOSS_RE.search(s) else set()
        strong = [w for w in caps if w.isupper() and w.upper() not in ENGLISH_GENERIC_UPPER and w not in glossed]
        if strong:
            score += 3
        elif any(not w.isupper() for w in caps):
            score += 2 if (toks or re.search(r'[가-힣]', s) or ':' in s) else 1
        else:
            score += 1
        matched = matched or caps[0]
    km = KOREAN_MAKER_RE.search(s)
    if km:
        score += 2
        matched = matched or km.group(0)
    if TRADEMARK_RE.search(s):
        score += 2
    if CONTEXT_RE.search(s) and score > 0:
        score += 1
    if INSTRUCTION_RE.search(s) and not m:
        score -= 2
    if doc_type == '공고문':
        score -= 1
    return score, matched


def analyse(notice):
    """{line index: {'score', 'matched', 'text'}} for every excerpt-worthy line; text is what the model is shown."""
    out = {}
    by_doc = {}
    for ln in notice.lines:
        by_doc.setdefault(ln.doc, []).append(ln)
    for doc_lines in by_doc.values():
        cleaned = [clean(ln.text) for ln in doc_lines]
        hints, last_hint, last_k = [], '', -999
        for k, s in enumerate(cleaned):
            if s and len(s) <= 40 and SECTION_HINT_RE.search(s) and not TOKEN_RE.search(s):
                last_hint, last_k = s, k
            hints.append(last_hint if k - last_k <= 30 else '')
        for k, ln in enumerate(doc_lines):
            s = cleaned[k]
            if not s:
                continue
            if LABEL_ONLY_RE.match(s):
                nxt = [x for x in cleaned[k + 1:k + 6] if x][:3]
                sc = 4 if any(TOKEN_RE.search(x) or CAP_WORD_RE.search(x) for x in nxt) else 2
                out[ln.i] = {'score': sc, 'matched': s, 'text': (s + ' | ' + ' | '.join(nxt))[:MAX_EXCERPT_CHARS]}
                continue
            if '|' in s and TABLE_LABEL_RE.search(s) and not INSTRUCTION_RE.search(s):
                hdr = s[:90]
                for j, x in [(j, x) for j, x in enumerate(cleaned[k + 1:k + 12], k + 1) if x and '|' in x][:6]:
                    row = doc_lines[j]
                    if row.i not in out or out[row.i]['score'] < 5:
                        out[row.i] = {'score': 5, 'matched': x[:40], 'text': ('[표] ' + hdr + ' → ' + x)[:MAX_EXCERPT_CHARS]}
            sc, matched = score_line(s, ln.doc_type)
            if sc < MIN_SCORE or (ln.i in out and out[ln.i]['score'] >= sc):
                continue
            text = s
            if len(text) > MAX_EXCERPT_CHARS:
                pos = text.find(matched) if matched else 0
                start = max(0, pos - 60)
                text = ('…' if start else '') + text[start:start + MAX_EXCERPT_CHARS] + '…'
            elif len(text) < 30:
                prev = next((x for x in reversed(cleaned[max(0, k - 3):k]) if x), '')
                nxt = next((x for x in cleaned[k + 1:k + 4] if x), '')
                text = (prev[:CONTEXT_CHARS] + ' ‖ ' if prev else '') + text + (' ‖ ' + nxt[:CONTEXT_CHARS] if nxt else '')
            for x in cleaned[k + 1:k + 3]:
                if x and EQUIV_NEXT_RE.search(x):
                    text = text + ' / ' + x[:80]
                    break
            if hints[k] and hints[k] != s and hints[k] not in text:
                text = '[§' + hints[k][:30] + '] ' + text
            out[ln.i] = {'score': sc, 'matched': matched, 'text': text}
    return out


def select(notice, cap=MAX_EXCERPTS, info=None):
    """Excerpt lines: the top `cap` by score, returned in document order (규격서 → 제안요청서 → 과업지시서 → 공고문)."""
    info = analyse(notice) if info is None else info
    order = sorted(info, key=lambda i: (-info[i]['score'], DOC_ORDER.get(notice.lines[i].doc_type, 9), i))[:cap]
    return sorted((notice.lines[i] for i in order), key=lambda ln: (DOC_ORDER.get(ln.doc_type, 9), ln.i))


def gated(notice, info=None):
    info = analyse(notice) if info is None else info
    return bool(info) and max(x['score'] for x in info.values()) >= GATE_SCORE


# ------------------------------------------------------------------------------------------------ prompt and schema
SYSTEM = ('당신은 공공조달 규격서 검토자입니다. 입찰공고 문서(규격서·제안요청서·과업지시서·공고문)의 발췌문에서 특정 제조사·브랜드·상표명 또는 '
          '특정 모델명·제품명으로 보이는 표현을 모두 찾아 분류합니다. 위반 여부는 판단하지 않습니다. 출력은 JSON 하나입니다.')

GUIDE = """판정 기준
- 종류 (표현이 무엇인가)
  - maker_or_brand: 제조사·브랜드·상표명 (예: Akron Brass, DJI, Agilent, 아크론브라스, 남도, 구보다, 팜아그로텍, RedHat, Dell)
  - model_or_product_name: 특정 제조사의 모델명·제품명·형식명 (예: TurboJet, DPM-8000GMP, Matrice 4T, ICP-OES 5900, RAD8, ND-800, D851, 펙사론, Xeon Gold 6544Y)
  - material_grade_or_standard: 재질 등급·규격·표준·인증·프로토콜·연료/골재 등급 (예: STS304, SCPH22, S45C, KS F 2357, ISO 9001, IP67, MIL-STD-810G, DMX512, USB 3.2, RS-232, RSC-3, WC-2, JP-8)
  - generic_or_unit: 일반 기술용어·분석기법·단위·형식 분류·검사 항목·표준화된 검사명 (예: ICP-OES 기법 자체, HST 변속기, 4기통 4-CYCLE, 60Hz, 4K, LED, C-ARM, PSA·CEA 같은 분석항목, MMPI-2 같은 표준화 심리검사)
  - requirement_id_or_code: 문서 내부 번호·요구사항 ID·물품분류번호·신용등급 (예: PLR-001, 4321150102, A20, BBB+)
- 역할 (문맥상 무엇에 대한 표현인가)
  - supplied_item: 이 공고로 납품·구매·설치할 물품 자체, 그 구성품·액세서리(배터리, 조종기, 노즐, 타겟 등), 납품할 SW 라이선스, 또는 '○○ 또는 동등이상'처럼 기준으로 지정된 참조 제품
  - supplied_component: 납품 물품 내부의 부품·부속 사양 (엔진, 감속기, 모터, 베어링, 체인, 필터, 밸브, 센서 등)의 제조사·모델
  - performance_equipment: 용역 수행을 위해 계약상대자가 투입·사용·보유해야 한다고 지정된 장비·계측기·자재 (예: 중계용 카메라 HDC-3500, 점검 계측기 HHT-3000)
  - computer_component: 납품할 컴퓨터·서버·노트북의 구성부품(CPU, GPU, 칩셋, 메인보드, 메모리, 저장장치, NIC, 무선랜)이나 탑재 OS·오피스 (예: CPU: Intel Xeon Gold 6544Y, Chipset: Intel W880, Windows 11 Pro 탑재)
  - existing_equipment: 발주기관이 이미 보유·운영 중인 장비·시스템 (호환·연계·유지보수·부품 교체 대상, 도입연도가 적힌 장비 현황표, 기관이 제공하는 평가용 노트북, 그 장비용 순정 부품·소모품 지정 포함)
  - environment_or_tool: 산출물이 호환·구동·제출되어야 하는 SW 환경·파일 형식·플랫폼, 범용 사무·분석 SW (예: Windows 지원, AutoCAD로 확인 가능, Google Play 배포, HEC-RAS로 검토, MS 365 활용 교육)
  - bidder_form_or_instruction: 입찰자가 채울 빈칸·서식, 제안서에 모델명을 적으라는 지시, 명판·포장에 제조사명을 표시하라는 의무
  - example_only: '예:', '등'으로 든 단순 예시로서 그 제품을 요구하지 않는 경우 ('○○ 또는 동등이상'은 예시가 아니라 supplied_item)
- 규칙: 발췌문에 실제로 적힌 표현만 "표현"에 그대로(원문 철자 그대로, 80자 이내) 옮기고 그 줄의 L번호를 "줄"에 적으십시오. 표현이 하나도 없으면 빈 배열을 내십시오. 세계 지식으로 '이 이름이 실제 특정 회사의 상품인가'를 판단하되, 확실하지 않으면 종류를 generic_or_unit 또는 material_grade_or_standard로 두십시오. 같은 표현이 여러 줄에 반복되면 대표 하나만 적으십시오. 발췌문의 ' ‖ ' 앞뒤는 원문의 앞줄·뒷줄, ' / ' 뒤는 바로 다음 줄, '[§ …]'는 그 줄이 속한 절의 제목(현황·도입·납품 등 역할 판단 근거)입니다."""

TASK = ('출력 형식: {"designations": [{"줄": L번호(정수), "표현": "적힌 그대로", "종류": "...", "역할": "..."}]}')


def messages(b, cands, info=None):
    notice = b.notice
    info = analyse(notice) if info is None else info
    work = notice.meta.get('업무구분') or ''
    items = str(notice.meta.get('세부품명번호목록') or '')[:120]
    title = (b.titles[0] if getattr(b, 'titles', None) else '')[:80]
    header = ' / '.join(x for x in (f'업무구분={work}' if work else '', f'세부품명={items}' if items else '', f'제목={title}' if title else '') if x)
    rows = []
    for ln in cands:
        shown = info[ln.i]['text'] if ln.i in info else clean(ln.text)[:MAX_EXCERPT_CHARS]
        rows.append(f'L{ln.i}: ({ln.doc_type}) {shown}')
    user = f'{GUIDE}\n\n[공고 정보] {header}\n\n[발췌]\n' + '\n'.join(rows) + f'\n\n{TASK}\nJSON으로만 답하십시오.'
    return [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user}]


def schema():
    item = {'type': 'object', 'additionalProperties': False, 'required': ['줄', '표현', '종류', '역할'],
            'properties': {'줄': {'type': 'integer', 'minimum': 0},
                           '표현': {'type': 'string', 'maxLength': 80},
                           '종류': {'type': 'string', 'enum': list(KINDS)},
                           '역할': {'type': 'string', 'enum': list(ROLES)}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['designations'],
            'properties': {'designations': {'type': 'array', 'maxItems': MAX_ITEMS, 'items': item}}}


def request(engine, b, k, request_cls):
    """One request per gated notice; the excerpt shrinks by 75% steps until the prompt fits the token limit."""
    info = analyse(b.notice)
    if not gated(b.notice, info):
        return None
    cap = MAX_EXCERPTS
    while True:
        cands = select(b.notice, cap, info)
        if not cands:
            return None
        ids = engine.token_ids(messages(b, cands, info))
        if len(ids) + MAX_TOKENS <= min(PROMPT_TOKEN_LIMIT, engine.max_model_len - 64):
            break
        if cap > MIN_CAP:
            cap = max(MIN_CAP, int(cap * 0.75))
        elif cap > 1:
            cap = max(1, int(cap * 0.75))
        else:
            raise ValueError(f'{FAM}: minimum reading request exceeds the engine token limit')
    return request_cls(k, FAM, cands, (), ids, schema(), MAX_TOKENS, 0)


# ------------------------------------------------------------------------------------------------ consume and verdict
def _squash(text):
    return re.sub(r'\s+', '', text or '')


def locate(notice, cands, i, expr):
    """The original line where the copied expression is written: the line the model numbered, else one of its neighbours
    that the excerpt showed as context. None when the expression is written nowhere shown (invented or edited)."""
    key = _squash(expr)
    if len(key) < 2 or not isinstance(i, int) or i < 0 or i >= len(notice.lines):
        return None
    shown = {ln.i for ln in cands}
    if i not in shown:
        return None
    order = [notice.lines[i]] + [w for w in notice.window(i, 3, 3) if w.i != i]
    for ln in order:
        if key in _squash(ln.text):
            return ln
    return None


def consume(b, cands, text):
    """Parse the model's JSON, validate it against schema(), store b.d_model_name. False = invalid (retry)."""
    try:
        obj = json.loads(text.split('<channel|>', 1)[-1])
    except (ValueError, AttributeError, TypeError):
        return False
    if not isinstance(obj, dict) or not isinstance(obj.get('designations'), list) or len(obj['designations']) > MAX_ITEMS:
        return False
    for it in obj['designations']:
        if (not isinstance(it, dict) or type(it.get('줄')) is not int or not isinstance(it.get('표현'), str)
                or len(it['표현']) > 80 or it.get('종류') not in KINDS or it.get('역할') not in ROLES):
            return False
    kept = []
    for it in obj['designations']:
        ln = locate(b.notice, cands, it['줄'], it['표현'])
        if ln is not None:
            kept.append((ln, it['표현'].strip(), it['종류'], it['역할']))
    setattr(b, FAM, {'designations': kept, 'shown': [ln.i for ln in cands]})
    return True


def fires(kind, role):
    if kind not in FIRE_KINDS:
        return False
    return role in FIRE_ROLES or (COMPUTER_PARTS_FIRE and role == 'computer_component')


def cpu_fallback(notice):
    """Without a model reading: only an explicit designation label whose value is a model code, a Latin-glossed name or a
    listed maker ("제조사·모델명 : 아크론브라스(Akron Brass) 터보젯(TurboJet)", "모델명 : AP5114"), never an instruction or form."""
    best = None
    for ln in notice.lines:
        s = clean(ln.text)
        m = P1_RE.search(s)
        if not m or VALUE_STOP_RE.match(m.group(1)) or INSTRUCTION_RE.search(s) or BOILERPLATE_RE.search(s):
            continue
        value = s[m.start(1):]
        if not (model_tokens(value) or MAKER_LIST_RE.search(value) or P4A_RE.search(value)):
            continue
        rank = (DOC_ORDER.get(ln.doc_type, 9), ln.i)
        if best is None or rank < best[0]:
            best = (rank, ln)
    return best[1] if best else None


def verdict(b):
    """Evidence line of the first firing designation; None otherwise. Without a reading, the CPU fallback decides."""
    reading = getattr(b, FAM, None)
    if reading is None:
        return cpu_fallback(b.notice)
    for ln, _expr, kind, role in reading.get('designations', ()):
        if fires(kind, role):
            return ln
    return None
