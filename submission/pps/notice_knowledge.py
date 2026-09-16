"""A response-local fact context; no mutable hooks, cross-record state or IDs."""
from __future__ import annotations

import copy
import json

from . import qualification, sme
from .service_identity import provide


def adapt_sme(base, packet):
    result = copy.deepcopy(base)
    result['status'] = {'general': 'general_in_supplied_catalog'}.get(packet['status'], packet['status'])
    result['uncertainty'] = list(dict.fromkeys([*base['uncertainty'], *packet['uncertainty']]))
    result['supported_products'] = copy.deepcopy(packet['products'])
    identity = []
    for product in packet['products']:
        original = [e for e in base['identity_evidence'] if e['code'] == product['code']]
        proofs = [e for e in packet['identity_evidence'] if product['code'] in e.get('text', '')]
        if not original and not proofs:
            raise ValueError('Source-verified identity must retain the corroborating code evidence')
        identity.extend(copy.deepcopy(original) or [
            {'code': product['code'], 'evidence': copy.deepcopy(e),
             'identity_support': 'automatic_source_role_link'} for e in proofs])
    result['identity_evidence'] = identity
    return result


class NoticeKnowledge:
    def __init__(self, knowledge, rec, response):
        self.base = knowledge
        self.record = rec
        self.packet = None
        self.sme_packet = None
        self.provider_log = {'accepted': False, 'reason': 'incomplete_response'}
        if response is not None and response.get('finish_reason') not in {'stop', 'eos_token'}:
            return
        # provide() resolves source predicates, not the model's category claim.
        # A None response is an explicit pre-generation source context, never
        # a generated response or an empty model judgment.
        facts = json.loads(response['text']).get('facts', {}) if response is not None else {}
        if not isinstance(facts, dict):
            return
        pf = knowledge._product_facts
        parts = qualification.inventory(rec)
        product = qualification.purchase_scope(rec, pf, parts[0], parts[4])
        eligible = qualification.qualification_facts(rec, parts)
        self.sme_packet = sme.extract_sme_facts(rec, pf)
        self.packet, self.provider_log = provide(rec, facts, self.sme_packet['product'],
            {'product': product, 'qualification': eligible}, knowledge.products)
        if self.packet is not None:
            product_override = adapt_sme(self.sme_packet['product'], self.packet)
            self.sme_packet = sme.extract_sme_facts(rec, pf, product_override=product_override)

    def _check_record(self, rec):
        if rec is not self.record:
            raise ValueError('Response facts cannot be reused for another notice')

    def sme_record_facts(self, rec):
        self._check_record(rec)
        return self.sme_packet if self.sme_packet is not None else self.base.sme_record_facts(rec)

    def qualification_decisions(self, rec, row):
        self._check_record(rec)
        return qualification.infer(rec, row, self.base._product_facts, product_override=self.packet)

    def __getattr__(self, name):
        return getattr(self.base, name)
