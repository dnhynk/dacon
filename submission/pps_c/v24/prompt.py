"""v24p 프롬프트와 출력 스키마: 발췌에 적힌 값을 그대로 옮기는 과제. 숫자 문법 제약은 두지 않는다(옛 values 패밀리는 그 제약으로
예산 숫자 0/1,839를 냈다). 항목마다 목록이라 '값 없음'은 빈 목록이다."""
from .. import families as F

SYSTEM = ('너는 나라장터 입찰공고문 발췌에서 정해진 항목의 값을 찾아 적힌 그대로 옮긴다. 위반 여부는 판단하지 않는다.\n'
          '- 값은 발췌의 문자열을 그대로 복사한다. 계산·환산·요약·정규화·띄어쓰기 수정을 하지 않는다.\n'
          '- 발췌에 적혀 있지 않은 항목은 빈 목록으로 둔다. 추측하지 않는다.\n'
          '- 출력은 JSON 하나다.')

FIELDS = (
    ('예산', '배정예산·사업예산·예산액·소요예산·사업비·총사업비·용역비·사업금액으로 적힌 총액. 단가, 연차별·항목별 내역, 보증금 비율은 옮기지 않는다'),
    ('추정가격', '추정가격으로 적힌 금액(부가가치세 제외)'),
    ('추정금액', '추정금액으로 적힌 금액(부가가치세 포함)'),
    ('기초금액', '기초금액(예비가격기초금액)으로 적힌 금액'),
    ('계약방법', '계약방법·입찰방법·계약방식 난 또는 제목 괄호에 적힌 계약 방법 표기(예: 제한경쟁, 일반경쟁·1억원미만). 낙찰방법과 전자입찰 같은 입찰 수단은 옮기지 않는다'),
    ('지역제한', '입찰참가자(법인 본점·주된 영업소)의 소재지를 제한하는 문구에 적힌 지역명. 공동수급·분담이행 상대방의 소재지, 발주기관 주소, 납품·이행 장소는 옮기지 않는다'),
    ('업종', '입찰참가자격으로 요구하는 업종·면허의 명칭과 4자리 업종코드가 적힌 그대로(예: 학술연구용역(1169)). 허용업종 안내, 참여 불가 업종, "또는 …을 갖춘 자" 같은 대안 조건은 옮기지 않는다'),
)
FIELD_NAMES = tuple(name for name, _ in FIELDS)
MAX_ITEMS = 8
MAX_TOKENS = 900
TASK = ('발췌에서 아래 항목의 값을 찾아 적힌 그대로 옮긴다. 항목마다 목록이며, 목록의 원소는 '
        '{"줄": 값이 적힌 줄의 L번호, "이름표": 그 줄에 적힌 항목 이름(예: 사업예산), "값": 적힌 그대로의 값(예: 61,560,000원)}이다. '
        '같은 항목이 여러 줄에 적혀 있으면 모두 옮긴다. 이름표 줄의 다음 줄(표의 다음 칸)에 값이 있으면 값이 적힌 줄의 L번호를 쓴다.')


def messages(bundle, cands):
    work = bundle.notice.meta.get('업무구분')
    title = (bundle.titles[0] if bundle.titles else '')[:80]
    info = ' / '.join(x for x in (f'업무구분: {work}' if work else '', f'사업명: {title}' if title else '') if x)
    items = '\n'.join(f'- {name}: {help_}' for name, help_ in FIELDS)
    user = (f'[공고 정보] {info}\n'
            f'[과제] {TASK}\n'
            f'[항목]\n{items}\n'
            f'[발췌]\n{F.excerpt(bundle.notice, cands, 0)}')
    return [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user}]


def schema():
    item = {'type': 'object', 'additionalProperties': False, 'required': ['줄', '이름표', '값'],
            'properties': {'줄': {'type': 'integer', 'minimum': 0},
                           '이름표': {'type': 'string', 'maxLength': 30},
                           '값': {'type': 'string', 'maxLength': 80}}}
    props = {name: {'type': 'array', 'maxItems': MAX_ITEMS, 'items': item} for name in FIELD_NAMES}
    return {'type': 'object', 'additionalProperties': False, 'required': list(FIELD_NAMES), 'properties': props}
