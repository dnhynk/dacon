"""Class-word typography only. No title/parenthesis deletion or catalog/method changes."""
import re
from . import judge,families
DOTS=str.maketrans({c:'·' for c in '⸱∙⋅•‧・･․ㆍ'})
WORDS=re.compile(r'중\s*소\s*기\s*업|중\s*·\s*소\s*기\s*업|중\s*기\s*업|소\s*기\s*업|소\s*상\s*공\s*인')

def normal(text):
    from . import switches
    if switches.U2_SIZE_SUBSET:
        from .u2_size_subset import subset_small
        text = subset_small(text)
    return WORDS.sub(lambda m:re.sub(r'\s+','',m.group()),text.translate(DOTS))

def class_of(b,ln):
    text=judge.clause_text(b.notice,ln)
    normalized=normal(text)
    if normalized==text:return judge.size_class(b,ln)
    cls=families.size_words(normalized)
    return cls if cls else judge.size_class(b,ln)
