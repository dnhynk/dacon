"""Action-local modality shared by direct pledges and explicit references.

An exemption from submitting a document does not exempt obtaining or holding it.
Unresolved or withdrawn statements can only block a deterministic conclusion.
"""
import re


TARGET = re.compile(r'확\s*약\s*서|협약서')
ACTION = re.compile(r'제출|보유|소지|발급\s*받|발급')
EARLY = re.compile(r'(?:전자\s*)?입찰(?:서)?\s*(?:제출\s*)?마감일?\s*전|입찰\s*(?:전(?:일|까지|에)?|시)|낙찰통보\s*(?:이전|전)')
LATE = re.compile(r'낙찰(?:자\s*결정)?\s*(?:후|이후)|계약\s*(?:체결\s*)?(?:시|전|후)|착수\s*전|납품\s*(?:전|후)')
SENTENCE = re.compile(r'[^。;；]+?(?:[.。;；](?=\s|$)|$)')
REFERENCE = re.compile(r'^\s*(?:위|상기|해당|당해|이|그)\s*(?:제출|보유|발급)?\s*(?:의무|요구|조건|규정|확약서|협약서)')
WITHDRAWN = re.compile(r'예시|가정|참고용|(?:조항|규정|문구|해석|주장).{0,35}(?:삭제|철회|폐지|적용하지|타당하지|틀리|잘못)|(?:삭제|철회|폐지)(?:한다|합니다|함|되었|된)|없다는\s*(?:해석|주장)|(?:제외|면제)하지|없지\s*않')
OTHER_DOCUMENT = re.compile(r'(?!확약서|협약서)[가-힣A-Za-z]+(?:서|증|서류)(?:은|는|이|가|을|를)')


def analyze(text):
    events, relevant, uncertain = [], [], False
    for match in SENTENCE.finditer(text):
        unit = match.group()
        target = TARGET.search(unit)
        if target is None:
            if relevant and REFERENCE.search(unit) and WITHDRAWN.search(unit):
                relevant.append((match.start(), match.end()))
                uncertain = True
            continue
        relevant.append((match.start(), match.end()))
        uncertain |= bool(WITHDRAWN.search(unit))
        actions = list(ACTION.finditer(unit))
        for i, action in enumerate(actions):
            # A separately named document starts its own subject. A pledge's
            # waiver must not borrow the guarantee's holding requirement.
            other = [m for m in OTHER_DOCUMENT.finditer(unit, target.end(), action.start())]
            if other:
                continue
            stop = actions[i+1].start() if i+1 < len(actions) else len(unit)
            tail = re.sub(r'\s+', '', unit[action.end():stop])
            prefix = unit[:action.start()]
            early = list(EARLY.finditer(prefix))
            late = list(LATE.finditer(prefix))
            stage = ('explicit_pre_bid' if early and (not late or early[-1].start() > late[-1].start())
                     else 'explicit_later_stage' if late else 'unresolved')
            negated = bool(re.match(r'(?:할|받을|을)?(?:필요|의무)(?:가|는|도)?없|'
                r'(?:대상|의무)(?:에서|은|는|를|을)?(?:제외|면제|불요)|'
                r'(?:은|는|이)?(?:면제|불요)|하지(?:않|아니)|하지않아도', tail))
            capability = bool(re.match(r'(?:이)?가능|(?:할|받을|을)수있', tail))
            required = bool(re.match(r'(?:하여야|해야|받아야|아야|어야|하여|하|받|해야만)|'
                r'(?:한|받은|된)(?:자|업체)|(?:할것|함|한다|합니다)', tail)) and not negated and not capability
            # Bare list-style '입찰 전 제출' remains the existing extractor's
            # responsibility; this analyzer does not manufacture its modality.
            action_name = ('submit' if action.group() == '제출' else
                           'hold' if action.group() in {'보유', '소지'} else 'issue_or_receive')
            events.append({'action': action_name, 'stage': stage,
                'modality': 'negated' if negated else 'capability' if capability else 'required' if required else 'unresolved',
                'start': match.start(), 'end': match.end(), 'quote': unit})
    return {'events': events, 'uncertain': uncertain, 'relevant_ranges': relevant,
        'required_early': any(e['stage'] == 'explicit_pre_bid' and e['modality'] == 'required' for e in events),
        'negated_early': any(e['stage'] == 'explicit_pre_bid' and e['modality'] == 'negated' for e in events)}


def apply(pledge):
    source = pledge.get('clause_evidence', pledge['evidence'])
    facts = analyze(source['quote'])
    pledge['action_modality'] = facts
    pledge['source_subject_text'] = ' '.join(source['quote'][a:b] for a,b in facts['relevant_ranges'])
    if facts['uncertain']:
        pledge['uncertain_context'] = True
    elif facts['required_early']:
        pledge['timing'] = 'explicit_pre_bid'
        pledge['explicit_no_bid_time_requirement'] = False
        pledge['submission_capability_only'] = False
    elif facts['negated_early']:
        pledge['explicit_no_bid_time_requirement'] = True
    return facts
