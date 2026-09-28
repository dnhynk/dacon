"""Explicit SME software-bidder qualification with the SW decree as basis."""
import re

SPACE = re.compile(r'\s+')
SME41 = re.compile(
    r'소프트웨어(?:산업)?진흥법시행령[」｣』]?제?41조'
    r'(?:[（(]중소소프트웨어사업자의기준등[)）])?'
    r'(?:제1항)?제1호(?:또는|및|,|·)제2호에해당하는자')


def statement(text):
    # A registration statement, title/citation alone, or negative qualification
    # cannot satisfy this positive Article 41(1)(1)/(2) qualification.
    return bool(SME41.search(SPACE.sub('', text)))


def present(notice):
    return any(statement(ln.text) for ln in notice.lines)
