"""Preserved input states and declared coverage, separate from legal decisions.

Optional legacy management fields remain readable. Present fields are typed;
contradictory coverage cannot prove completeness. Metadata states are diagnostic
and never overwrite raw registration values or become violation features.
"""
from __future__ import annotations

import json
import math


VERSION = 1
COMPLETENESS_FIELDS = ('공고문_실재','추출_성공','무탈락','완전관측')
META_FIELDS = ('적용계약법','업무구분','계약방법','낙찰방법','낙찰하한율','배정예산금액',
    '입찰추정가격','소관구분','공동도급구성방식','정보화사업여부','세부품명번호목록',
    '제한지역코드목록','지역제한여부','면허업종제한목록','업종제한여부','조항호내용',
    '공고게시일자','개찰예정일자','긴급공고여부','입찰방법','조달방식')
FLAG_FIELDS = frozenset(('긴급공고여부','지역제한여부','업종제한여부','정보화사업여부'))
OBSERVED_ASSEMBLY = 'assembly-v3-0820'


def metadata_states(record):
    meta = record.get('meta', {})
    if not isinstance(meta, dict):
        return {}
    result = {}
    for key in dict.fromkeys((*META_FIELDS, *sorted(meta))):
        present, value = key in meta, meta.get(key)
        if not present:
            state = 'missing_field'
        elif value is None:
            state = 'null_value'
        elif isinstance(value, str) and value.strip() == '미입력':
            state = 'unregistered'
        elif isinstance(value, str) and value.strip() in ('해당 없음','해당없음'):
            state = 'explicit_not_applicable'
        elif isinstance(value, str) and not value.strip():
            state = 'empty_string'
        elif key in FLAG_FIELDS:
            state = 'known_negative' if value == 'N' else 'known_positive' if value == 'Y' else 'unrecognized_flag_value'
        else:
            state = 'present_value'
        result[key] = {'state':state,'present':present,'raw_value':value}
    return result


def coverage(record):
    raw = record.get('input_completeness', {})
    counts = record.get('dropped_doc_counts', {})
    errors, contradictions = [], []
    if not isinstance(raw, dict):
        errors.append('input_completeness_must_be_object')
        flags = {}
    else:
        flags = raw
        errors.extend('completeness_must_be_boolean:'+key for key in COMPLETENESS_FIELDS
            if key in flags and type(flags[key]) is not bool)
    if not isinstance(counts, dict):
        errors.append('dropped_doc_counts_must_be_object')
        counts = {}
    else:
        errors.extend('dropped_count_must_be_nonnegative_integer:'+str(key) for key,value in counts.items()
            if not isinstance(key,str) or type(value) is not int or value < 0)
    dropped = any(type(value) is int and value > 0 for value in counts.values())
    declared = flags.get('완전관측') if type(flags.get('완전관측')) is bool else None
    if declared is True:
        contradictions.extend('complete_but_component_false:'+key for key in COMPLETENESS_FIELDS[:-1]
            if flags.get(key) is False)
        if dropped:
            contradictions.append('complete_but_documents_dropped')
    if flags.get('무탈락') is True and dropped:
        contradictions.append('no_drop_but_positive_dropped_count')
    return {'declared_complete':declared,
        'provided_complete':declared is True and not errors and not contradictions and not dropped,
        'type_errors':errors,'contradictions':contradictions,
        'missing_component_fields':[key for key in COMPLETENESS_FIELDS if key not in flags],
        'positive_dropped_count_observed':dropped,
        'selected_model_input_complete':'not_established_by_input_metadata'}


def provided_complete(record):
    return coverage(record)['provided_complete']


def management_errors(record):
    errors = list(coverage(record)['type_errors'])
    if 'anon_applied' in record and type(record['anon_applied']) is not bool:
        errors.append('anon_applied_must_be_boolean')
    if 'assembly_policy_version' in record and (not isinstance(record['assembly_policy_version'],str)
            or not record['assembly_policy_version'].strip()):
        errors.append('assembly_policy_version_must_be_nonempty_string')
    return errors


def diagnostics(record):
    version = record.get('assembly_policy_version')
    return {'id':record.get('id'),'contract_version':VERSION,'metadata_states':metadata_states(record),
        'coverage':coverage(record),'management_type_errors':management_errors(record),
        'anon_applied':{'present':'anon_applied' in record,'raw_value':record.get('anon_applied'),
            'used_for_prediction':False},
        'assembly':{'present':'assembly_policy_version' in record,'raw_value':version,
            'status':'missing' if version is None else 'observed_version' if version==OBSERVED_ASSEMBLY
                else 'unrecognized_version_fields_checked','used_for_prediction':False},
        'metadata_state_usage':'diagnostic_only_raw_meta_retained'}


def _unique_object(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            raise ValueError('Duplicate input JSON key: '+key)
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError('Non-finite input JSON number: '+value)


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        _nonfinite(value)
    return number


def load_record_json(text):
    return json.loads(text,object_pairs_hook=_unique_object,parse_constant=_nonfinite,parse_float=_finite_float)
