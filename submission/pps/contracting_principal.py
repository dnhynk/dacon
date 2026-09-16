"""Identify the narrow case where a public body only runs a private party's bid.

The competition-product and priority-purchase checks apply to the purchaser's
contract.  A notice can be posted by a local authority even though the winner
must contract directly with a private subsidy recipient.  Treat that as a
different contract only when the notice states every link in that relation;
isolated words such as ``subsidy`` or ``bid agent`` are not enough.
"""
from __future__ import annotations

import re


_PRIVATE_PROJECT = re.compile(
    r"(?:민간\s*행사\s*사업\s*보조\s*사업|민간\s*행사\s*사업자.{0,180}?시행(?:하|되)는?\s*사업)",
    re.S,
)
_PUBLIC_BID_AGENT = re.compile(
    r"(?:입찰\s*의뢰.{0,260}?입찰(?:을|만)?\s*대행|입찰(?:을|만)?\s*대행(?:하|하는|하고|하는\s*사업))",
    re.S,
)
_DIRECT_PRIVATE_CONTRACT = re.compile(
    r"(?:계약\s*상대자(?:로\s*결정된\s*자)?|낙찰자).{0,220}?"
    r"민간\s*행사\s*사업자(?:인)?.{0,260}?(?:와|과)\s*직접\s*계약(?:을)?\s*체결",
    re.S,
)
_DELEGATED_BID = re.compile(
    r"(?:계약\s*\(\s*입찰\s*\)|입찰|계약)\s*대행",
    re.S,
)
_DIRECT_SUBSIDY_CONTRACT = re.compile(
    r"(?:낙찰자|계약\s*상대자).{0,260}?보조사업자.{0,260}?"
    r"(?:와|과)\s*직접\s*계약\s*체결",
    re.S,
)
_SUBSIDY_PRINCIPAL_DUTIES = re.compile(
    r"(?:용역\s*)?계약.{0,80}?관리.{0,80}?감독.{0,80}?대금\s*지급"
    r".{0,160}?권한과\s*의무.{0,100}?보조사업자에게",
    re.S,
)


def _evidence(record, doc_index, match):
    doc = record["docs"][doc_index]
    return {
        "doc_index": doc_index,
        "doc_id": doc.get("doc_id"),
        "document_role": doc["type"],
        "start": match.start(),
        "end": match.end(),
        "text": doc["text"][match.start():match.end()],
    }


def review(record):
    """Return a source-only contracting-principal finding.

    All three statements must occur in the same notice: a private subsidy
    project, public-body bid agency, and a direct contract with the private
    operator.  This intentionally does not infer the private party's identity
    from anonymized institution or region tokens.
    """
    report = {
        "status": "unresolved_or_public_contract",
        "reason": "explicit_private_contract_chain_not_complete",
        "evidence": [],
        "public_body_is_only_bid_agent": False,
        "private_contracting_principal_verified": False,
        "external_contracting_principal_verified": False,
        "outside_public_purchase_checks": False,
    }
    for doc_index, doc in enumerate(record.get("docs", [])):
        if doc.get("type") != "공고문":
            continue
        text = doc.get("text", "")
        project = _PRIVATE_PROJECT.search(text)
        agent = _PUBLIC_BID_AGENT.search(text)
        direct = _DIRECT_PRIVATE_CONTRACT.search(text)
        if not (project and agent and direct):
            continue
        report.update(
            status="private_contracting_principal",
            reason="notice_says_public_body_only_agents_bid_and_winner_contracts_private_operator",
            evidence=[_evidence(record, doc_index, match) for match in (project, agent, direct)],
            public_body_is_only_bid_agent=True,
            private_contracting_principal_verified=True,
            external_contracting_principal_verified=True,
            outside_public_purchase_checks=True,
        )
        return report

    # A notice need not use the event-specific phrase above.  Some public
    # bodies conduct only the bid for a subsidy recipient.  Admit that broader
    # form only when the same notice also makes the recipient the winner's
    # direct counterparty and assigns it contract administration, supervision
    # and payment.  A lone "subsidy project" or delivery location is not this.
    for doc_index, doc in enumerate(record.get("docs", [])):
        if doc.get("type") != "공고문":
            continue
        text = doc.get("text", "")
        agent = _DELEGATED_BID.search(text)
        direct = _DIRECT_SUBSIDY_CONTRACT.search(text)
        duties = _SUBSIDY_PRINCIPAL_DUTIES.search(text)
        if not (agent and direct and duties):
            continue
        report.update(
            status="subsidy_recipient_contracting_principal",
            reason="notice_assigns_direct_contract_and_all_contract_duties_to_subsidy_recipient",
            evidence=[_evidence(record, doc_index, match) for match in (agent, direct, duties)],
            public_body_is_only_bid_agent=True,
            external_contracting_principal_verified=True,
            outside_public_purchase_checks=True,
        )
        return report
    return report
