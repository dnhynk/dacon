"""Fixed cohort execution; preserve native outputs before parsing or CPU decisions."""
from __future__ import annotations

import gzip
import copy
import gc
import hashlib
import json
from pathlib import Path
import time
import traceback

from .b4_entry import B4Pipeline, PROFILES, assemble, parse_error
from .engine import POLICY, serial
from .pps.data import records, write_csv, missing_evidence_items

COHORT_SIZE = 32


def require_generation_progress(responses):
    if any(r.get('generation_stall') for r in responses):
        raise RuntimeError('JSON generation stalled; native evidence preserved, further batches and identical retries stopped')


def format_recovery_packet(packet, attempt):
    """Preserve every input token, source span and schema during recovery.

    Only the native thinking budget changes, reserving the fixed output budget
    for a complete answer. Never shrink the evidence or reroll valid judgments.
    """
    if type(attempt) is not int or attempt < 1:
        raise ValueError('Recovery attempts start at one')
    return {**packet, 'generation': {**packet['generation'], 'thinking_budget': 0},
            'format_recovery': {'attempt': attempt, 'primary_prompt_sha256': packet['prompt_sha256'],
                                'source_and_schema_unchanged': True}}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_manifest():
    root = Path(__file__).resolve().parent
    return {p.relative_to(root).as_posix(): sha256(p) for p in sorted(root.rglob('*'))
            if p.is_file() and p.suffix in ('.py', '.json', '.txt')}


class Journal:
    def __init__(self, output_dir):
        self.root = Path(output_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        if (self.root / 'started.json').exists():
            raise FileExistsError('This output directory holds a prior run; existing results are preserved')
        with (self.root / 'started.json').open('x', encoding='utf-8') as stream:
            json.dump({'epoch': time.time(), 'duplicate_execution_forbidden': True}, stream)

    def save(self, name, obj):
        path = self.root / name
        temp = path.with_suffix(path.suffix + '.partial')
        temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(path)

    def rows(self, name, rows):
        path = self.root / name
        temp = path.with_suffix(path.suffix + '.partial')
        with gzip.open(temp, 'wt', encoding='utf-8') as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        temp.replace(path)

    def progress(self, **values):
        self.save('progress.json', {'epoch': time.time(), **values})
        print('SUBMISSION_PROGRESS ' + json.dumps(values, ensure_ascii=False), flush=True)


def call_plan(recs, packets, *, a_cohort_size=COHORT_SIZE):
    """Order depends only on input position and profile, never on labels or IDs."""
    if type(a_cohort_size) is not int or not 1 <= a_cohort_size <= COHORT_SIZE:
        raise ValueError('A cohort size must be an integer from1 through32')
    index = {(p['record_id'], p['batch']): p for p in packets}
    required = {(r['id'], profile) for r in recs for profile in PROFILES}
    optional = {(r['id'], profile) for r in recs for profile in ('S9','Q10','W20')}
    if len(index) != len(packets) or not required <= index.keys() or not index.keys() <= required | optional:
        raise ValueError('Expected four base packets per input record and only declared optional review packets')
    for packet in packets:
        if 'source_questions' in packet:
            from .pps.source_questions import validate_structure
            validate_structure(packet)
    for key in optional & index.keys():
        packet = index[key]
        family,items,formats={'S9':('A',[9],{'specification_candidates'}),
            'Q10':('Q',list(range(10,19)),{'catalog_scope','goods_scope'}),
            'W20':('W',[20],{'software_refs'})}[key[1]]
        if (packet['family']!=family or packet['items']!=items
                or packet['generation']['response_format'] not in formats):
            raise ValueError('Optional review must retain its declared family, item set and format')
    plan = []
    for phase, profiles in (('A', PROFILES[:3]), ('L', PROFILES[3:]),
                            ('Q',('Q10',)),('W',('W20',)),('S', ('S9',))):
        size = a_cohort_size if phase == 'A' else COHORT_SIZE
        for offset in range(0, len(recs), size):
            cohort = recs[offset:offset + size]
            for profile in profiles:
                batch = [index[(r['id'], profile)] for r in cohort if (r['id'], profile) in index]
                if not batch:
                    continue
                plan.append({'number': len(plan), 'phase': phase, 'profile': profile,
                             'cohort': offset // size,
                             'request_keys': [p['request_key'] for p in batch]})
    if {k for b in plan for k in b['request_keys']} != {p['request_key'] for p in packets}:
        raise ValueError('Execution plan does not consume every packet exactly once')
    return plan


class NativeRecorder:
    def __init__(self, runner, journal):
        self.runner = runner
        self.journal = journal
        self.original = runner.llm.generate
        self.active = None
        self.captured = []
        runner.llm.generate = self.capture

    def capture(self, *args, **kwargs):
        active = self.active
        packets = active['packets']
        actual_inputs = args[0] if args else kwargs.get('prompts')
        expected_inputs = [{'prompt_token_ids': p['token_ids']} for p in packets]
        if actual_inputs != expected_inputs:
            raise ValueError('Actual LLM input differs from the recorded packet')
        params = kwargs.get('sampling_params')
        before = serial(params)
        sampling = {'request_keys': [p['request_key'] for p in packets],
                    'before_llm_generate': before,
                    'observation': 'Actual objects supplied to LLM.generate; internal processor clones are not observed'}
        self.journal.save(active['name'] + '_sampling.json', sampling)
        output = self.original(*args, **kwargs)
        self.captured = []
        returned = time.time()
        for index, row in enumerate(output):
            packet = packets[index] if index < len(packets) else None
            candidates = list(row.outputs or [])
            generated = candidates[0] if candidates else None
            native = {'request_id': row.request_id,
                      'raw_text': generated.text if generated else None,
                      'output_token_ids': list(generated.token_ids) if generated else [],
                      'finish_reason': generated.finish_reason if generated else None,
                      'stop_reason': getattr(generated, 'stop_reason', None),
                      'cached_input_tokens': getattr(row, 'num_cached_tokens', None),
                      'prompt_token_ids': serial(getattr(row, 'prompt_token_ids', None)),
                      'metrics': serial(getattr(row, 'metrics', None)),
                      'num_outputs': len(candidates)}
            if len(candidates) > 1:
                native['all_outputs'] = [serial(item) for item in candidates]
            self.captured.append({'request_key': packet['request_key'] if packet else None,
                                  'prompt_sha256': packet['prompt_sha256'] if packet else None,
                                  'token_ids_sha256': packet['token_ids_sha256'] if packet else None,
                                  'attempt': active['attempt'], 'batch': active['number'],
                                  'started_epoch': active['epoch'], 'returned_epoch': returned,
                                  'native': native})
        # Even missing/extra native results are persisted before rejecting the batch.
        self.journal.rows(active['name'] + '_native.jsonl.gz', self.captured)
        self.journal.save(active['name'] + '_sampling.json', {
            **sampling, 'after_llm_generate': serial(params)})
        if len(output) != len(packets) or any(n['native']['num_outputs'] != 1 for n in self.captured):
            raise RuntimeError('Incomplete native results preserved; no automatic resubmission')
        for packet, native in zip(packets, self.captured):
            actual = native['native']['prompt_token_ids']
            if actual is not None and actual != packet['token_ids']:
                raise RuntimeError('Native prompt tokens differ; original outputs preserved')
        return output

    def generate(self, packets, number, attempt):
        if type(attempt) is not int or attempt < 0:
            raise ValueError('Invalid attempt number')
        name = f'call_{number:05d}_{attempt}'
        self.journal.rows(name + '_requests.jsonl.gz', packets)
        self.active = {'packets': packets, 'number': number, 'attempt': attempt,
                       'epoch': time.time(), 'name': name}
        self.captured = []
        response = self.runner.generate(packets, max_tokens=2048)
        if len(response) != len(self.captured) or len(response) != len(packets):
            raise RuntimeError('Missing parsed response; native outputs retained')
        for result, native in zip(response, self.captured):
            if result['raw_output_sha256'] != hashlib.sha256(native['native']['raw_text'].encode()).hexdigest():
                raise RuntimeError('Parsed/native output identity mismatch')
        self.journal.rows(name + '_responses.jsonl.gz', [
            {'request_key': p['request_key'], 'attempt': attempt, 'response': r}
            for p, r in zip(packets, response)])
        self.journal.save(name + '.json', {
            'count': len(packets), 'attempt': attempt, 'batch': number,
            'seconds': time.time() - self.active['epoch'],
            'input_tokens': sum(len(p['token_ids']) for p in packets),
            'output_tokens': sum(r['output_tokens'] for r in response),
            'cached_input_tokens': [r.get('cached_input_tokens') for r in response]})
        return response

    def close(self):
        self.runner.llm.generate = self.original


def execute(input_path, data_dir, output_dir, runner=None, *, tokenizer=None,
            runner_factory=None, limit=None, generation=None, batch_observer=None, source_policy=None,
            specification_review=None, legal_policy=None, started_at=None, catalog_review=None,
            software_review=None, a10_thinking_budget=None, a_cohort_size=None, close_runner=True,
            a10_question_policy=None, catalog_source_policy=None, catalog_task_groups=None):
    """CLI supplies a lazy factory; label-free tests may inject stored responses."""
    journal = Journal(output_dir)
    started = time.monotonic() if started_at is None else started_at
    recorder = None
    owned_runner = False
    submitted = returned = 0
    primary_requests = parse_retries = 0
    code = source_manifest()
    try:
        journal.progress(phase='prepare_current_inputs', submitted=0, returned=0)
        original_input_sha256 = sha256(input_path) if Path(input_path).is_file() else None
        recs = list(records(input_path, limit))
        if original_input_sha256 is not None and sha256(input_path) != original_input_sha256:
            raise ValueError('Original input file changed while reading')
        from .pps.input_contract import diagnostics, VERSION as INPUT_CONTRACT_VERSION
        journal.save('input_contract.json', {'version':INPUT_CONTRACT_VERSION,
            'original_input_sha256':original_input_sha256,
            'records':[diagnostics(rec) for rec in recs],
            'prediction_features_added':False})
        source_options = {'source_policy':source_policy} if source_policy is not None else {}
        if specification_review is not None:
            source_options['specification_review'] = specification_review
        if legal_policy is not None:
            source_options['legal_policy'] = legal_policy
        for name,value in (('catalog_review',catalog_review),('catalog_source_policy',catalog_source_policy),
                           ('catalog_task_groups',catalog_task_groups),('software_review',software_review),
                           ('a10_thinking_budget',a10_thinking_budget),('a_cohort_size',a_cohort_size),
                           ('a10_question_policy',a10_question_policy)):
            if value is not None:
                source_options[name]=value
        pipeline = B4Pipeline(data_dir, tokenizer if tokenizer is not None else runner.tokenizer, **source_options)
        total_budget = getattr(pipeline.config, 'total_runtime_seconds', None)
        deadline = started + total_budget - 15 if total_budget is not None else float('inf')

        def check_budget():
            if time.monotonic() >= deadline:
                raise TimeoutError('Runtime budget exhausted during preparation or model loading; CSV withheld')

        check_budget()
        pipeline.preparation_guard = check_budget
        if pipeline.config.batch_size != COHORT_SIZE:
            raise ValueError('The canonical cohort size must remain 32')
        packets = pipeline.packets(recs)
        check_budget()
        retrieval_encoder=getattr(pipeline,'_source_encoder',None)
        encoder_receipt=copy.deepcopy(getattr(retrieval_encoder,'receipt',None))
        encoder_will_release=retrieval_encoder is not None
        if any(getattr(pipeline.config,k,'current')!='current' for k in ('specification_review','catalog_review','software_review')):
            journal.save('specialist_preparation.json', pipeline.specialist_preparation)
        if getattr(pipeline.config, 'notice_source_policy', 'current') != 'current':
            journal.save('source_retrieval_receipt.json', {
                'policy':pipeline.config.notice_source_policy,
                'preparation':pipeline.source_preparation,
                'encoder':encoder_receipt,
                'document_cache_scope':'one_current_notice', 'gpu_embedding_used':False,
                'encoder_released_before_model_load':encoder_will_release})
        if getattr(pipeline.config,'catalog_source_policy','shared') != 'shared':
            journal.save('catalog_source_retrieval_receipt.json', {
                'policy':pipeline.config.catalog_source_policy,
                'task_groups':pipeline.config.catalog_task_groups,
                'preparation':pipeline.catalog_source_preparation,
                'encoder':encoder_receipt,
                'source_budget':'per-notice unchanged shared-Q source token cap',
                'document_cache_scope':'one_current_notice','gpu_embedding_used':False,
                'retrieval_is_not_absence_proof':True,
                'encoder_released_before_model_load':encoder_will_release})
        if retrieval_encoder is not None:
            # Search is complete and packets contain immutable token IDs. Drop
            # the CPU BGE weights before allocating Gemma; no retrieval state is
            # consulted while consuming the already frozen model responses.
            pipeline._source_encoder=None
            goods_catalog=getattr(pipeline,'_goods_catalog',None)
            if goods_catalog is not None:
                goods_catalog.encoder=None
            del retrieval_encoder
            gc.collect()
            check_budget()
        cohort_size = getattr(pipeline.config,'a_cohort_size',COHORT_SIZE)
        plan = call_plan(recs, packets,a_cohort_size=cohort_size)
        policy = {**POLICY,'a_cohort_size':cohort_size,
                  'a_order':f'input-order cohorts of {cohort_size}; A1, A10, A19 within each cohort'}
        by_id = {r['id']: r for r in recs}
        by_key = {p['request_key']: p for p in packets}
        journal.rows('current_inputs.jsonl.gz', recs)
        journal.rows('current_packets.jsonl.gz', packets)
        journal.save('execution_config.json',serial(pipeline.config))
        journal.save('call_plan.json', {'policy': policy, 'batches': plan})
        journal.save('input_freeze.json', {
            'epoch': time.time(), 'labels_read': False, 'source_sha256': code,
            'original_input_sha256':original_input_sha256,
            'input_contract_sha256':sha256(journal.root / 'input_contract.json'),
            'input_sha256': sha256(journal.root / 'current_inputs.jsonl.gz'),
            'packets_sha256': sha256(journal.root / 'current_packets.jsonl.gz'),
            'call_plan_sha256': sha256(journal.root / 'call_plan.json'),
            'execution_config_sha256':sha256(journal.root / 'execution_config.json'),
            'records': len(recs), 'primary_requests': len(packets), 'policy': policy})
        if runner is None:
            if runner_factory is None:
                raise ValueError('A model runner factory is required')
            check_budget()
            journal.progress(phase='engine_load', submitted=0, returned=0)
            runner = runner_factory(pipeline.config, journal)
            owned_runner = True
        check_budget()
        if total_budget is not None:
            runner.deadline = min(getattr(runner, 'deadline', float('inf')),
                                  deadline)
        if runner.config != pipeline.config:
            raise ValueError('The preserved consumer/engine configuration must be retained')
        if generation is None:
            recorder = NativeRecorder(runner, journal)
            generation = recorder.generate
        consumed = {}
        invalid = {}
        resolved = {}
        skipped = {}
        retryable = set()
        primary_a1={p['record_id']:p['request_key'] for p in packets if p['batch']=='A1'}
        for planned in plan:
            if time.monotonic() >= getattr(runner, 'deadline', float('inf')):
                raise TimeoutError('Runtime budget exhausted; completed batches preserved, CSV withheld')
            batch = [by_key[k] for k in planned['request_keys']]
            if planned['profile'] == 'A10':
                from .pps.source_questions import code_only
                selected = []
                for packet in batch:
                    if ('source_questions' in packet and not packet['source_questions']['model_items']):
                        key = packet['request_key']
                        row, skip = code_only(by_id[packet['record_id']], packet, pipeline.knowledge)
                        consumed[key] = row
                        skipped[key] = skip
                    else:
                        selected.append(packet)
                if len(selected) != len(batch):
                    journal.save('skipped_requests.json', skipped)
                batch = selected
                if not batch:
                    continue
            if (planned['profile']=='S9' and getattr(pipeline.config,'specification_review','current') in {'gated_candidates','gated_source_candidates'}):
                selected=[]
                for packet in batch:
                    parent=primary_a1[packet['record_id']]
                    a1=consumed[parent]
                    value=a1.get('v9') if a1 is not None else None
                    count=len(packet['specification_inventory']['candidates'])
                    if value==1 or count:
                        selected.append(packet)
                    else:
                        key=packet['request_key'];consumed[key]={}
                        skipped[key]={'rule':'shared CPU v9 positive OR nonempty original-source candidate inventory',
                            'parent_request_key':parent,'shared_v9':value,'candidate_count':count,
                            'model_called':False,'prediction_inferred':False}
                batch=selected
                journal.save('skipped_requests.json',skipped)
                if not batch:
                    continue
            submitted += len(batch)
            primary_requests += len(batch)
            journal.progress(phase='generate', batch=planned['number'],
                             profile=planned['profile'], cohort=planned['cohort'],
                             submitted=submitted, returned=returned)
            responses = generation(batch, planned['number'], 0)
            journal.rows(f"first_part_{planned['number']:05d}.jsonl.gz", [
                {'request_key': p['request_key'], 'response': r} for p, r in zip(batch, responses)])
            returned += len(responses)
            if len(responses) != len(batch):
                raise RuntimeError('Missing primary responses; no automatic retry')
            require_generation_progress(responses)
            entries = []
            for packet, response in zip(batch, responses):
                error = parse_error(packet, response)
                if error is not None:
                    retryable.add(packet['request_key'])
                row = details = None
                if error is None:
                    try:
                        row, details = pipeline.consume(by_id[packet['record_id']], packet, response)
                    except (ValueError, TypeError, KeyError) as exc:
                        error = 'CPU: ' + type(exc).__name__ + ': ' + str(exc)
                consumed[packet['request_key']] = row
                if error is not None:
                    invalid[packet['request_key']] = {'request_key': packet['request_key'], 'error': error}
                resolved[packet['request_key']] = {'request_key': packet['request_key'], 'attempt': 0,
                                                   'packet': packet, 'response': response, 'error': error}
                entries.append({'request_key': packet['request_key'], 'first_parse_error': error,
                                'retries': 0, 'error': error, 'row': row,
                                'rule_details': details, 'final_response': response})
            journal.rows(f"final_part_{planned['number']:05d}.jsonl.gz", entries)
            journal.progress(phase='batch_saved', batch=planned['number'], profile=planned['profile'],
                             submitted=submitted, returned=returned, invalid=len(invalid))
            if batch_observer is not None:
                # A development controller may inspect already saved results
                # and stop a costly run. It receives no mutable response/packet
                # objects and cannot silently replace inputs or judgments.
                batch_observer(journal.root, dict(planned))
        # Recover format/termination failures only, after *all* primary calls.
        # This keeps cache history and batch membership of every valid primary
        # response unchanged. CPU exceptions are software defects, not reasons
        # to ask the model a second time.
        number = len(plan)
        for attempt in range(1, getattr(pipeline.config, 'max_response_retries', 0) + 1):
            pending = [p for planned in plan for key in planned['request_keys']
                       if key in retryable for p in [by_key[key]]]
            if not pending:
                break
            for offset in range(0, len(pending), COHORT_SIZE):
                if time.monotonic() >= getattr(runner, 'deadline', float('inf')):
                    raise TimeoutError('Runtime budget exhausted before recovery; raw responses preserved')
                batch = [format_recovery_packet(p, attempt) for p in pending[offset:offset + COHORT_SIZE]]
                submitted += len(batch)
                parse_retries += len(batch)
                journal.progress(phase='format_recovery', batch=number, attempt=attempt,
                                 submitted=submitted, returned=returned, invalid=len(invalid))
                responses = generation(batch, number, attempt)
                journal.rows(f'repair_raw_{number:05d}_{attempt}.jsonl.gz', [
                    {'request_key': p['request_key'], 'attempt': attempt, 'response': r}
                    for p, r in zip(batch, responses)])
                returned += len(responses)
                if len(responses) != len(batch):
                    raise RuntimeError('Missing recovery responses; observed responses preserved')
                require_generation_progress(responses)
                entries = []
                for packet, response in zip(batch, responses):
                    key = packet['request_key']
                    error = parse_error(packet, response)
                    row = details = None
                    if error is None:
                        retryable.discard(key)
                        try:
                            row, details = pipeline.consume(by_id[packet['record_id']], packet, response)
                        except (ValueError, TypeError, KeyError) as exc:
                            error = 'CPU: ' + type(exc).__name__ + ': ' + str(exc)
                    consumed[key] = row
                    if error is None:
                        invalid.pop(key, None)
                    else:
                        invalid[key] = {'request_key': key, 'error': error, 'attempt': attempt}
                    resolved[key] = {'request_key': key, 'attempt': attempt, 'packet': packet,
                                     'response': response, 'error': error}
                    entries.append({**resolved[key], 'row': row, 'rule_details': details})
                journal.rows(f'repair_final_{number:05d}_{attempt}.jsonl.gz', entries)
                number += 1
        journal.rows('resolved_responses.jsonl.gz', [resolved[p['request_key']] for p in packets if p['request_key'] in resolved])
        b3, b4 = assemble(recs, packets, consumed)
        missing = {name: sum(row[f'v{k}'] is None for row in values.values() for k in range(1, 25))
                   for name, values in [('final_B3', b3), ('final_B4', b4)]}
        journal.rows('final_B3.jsonl.gz', list(b3.values()))
        journal.rows('final_B4.jsonl.gz', list(b4.values()))
        journal.save('response_freeze.json', {
            'epoch': time.time(), 'labels_read': False,
            'files': {p.name: sha256(p) for p in sorted(journal.root.glob('call_*_*.jsonl.gz'))}})
        report = {'records': len(recs), 'prepared_requests':len(packets),'primary_requests': primary_requests, 'returned': returned,
                  'skipped_requests':len(skipped),
                  'requests_with_retries': submitted, 'parse_retries': parse_retries,
                  'new_model_calls': submitted if recorder is not None else 0,
                  'mode': 'fixed_model' if recorder is not None else 'injected_cpu_validation',
                  'single_engine': True, 'policy': policy, 'missing_bits': missing,
                  'invalid_responses': list(invalid.values()), 'official_score': None,
                  'seconds': time.monotonic() - started, 'l40s_time_verified': False,
                  'missing_required_evidence': sum(len(missing_evidence_items(r)) for r in b4.values()),
                  'source_unchanged': source_manifest() == code}
        journal.save('run_report.json', report)
        if not report['source_unchanged']:
            raise RuntimeError('Canonical source changed during execution; outputs preserved')
        if missing['final_B4']:
            raise ValueError('Unresolved required predictions; submission CSV withheld instead of filling zero')
        write_csv(journal.root / 'submission.csv', list(b4.values()), recs=recs,
                  require_positive_evidence=pipeline.config.require_positive_evidence)
        if not missing['final_B3']:
            write_csv(journal.root / 'B3.csv', list(b3.values()), recs=recs,
                      require_positive_evidence=pipeline.config.require_positive_evidence)
        journal.save('prediction_freeze.json', {
            'epoch': time.time(), 'labels_read': False,
            'files': {p.name: sha256(p) for p in sorted(journal.root.glob('*.csv'))}})
        journal.progress(phase='complete', submitted=submitted, returned=returned)
        return report
    except BaseException as exc:
        journal.save('failure.json', {'type': type(exc).__name__, 'message': str(exc),
                                     'traceback': traceback.format_exc(), 'submitted': submitted,
                                     'returned': returned, 'parse_retries': parse_retries, 'no_missing_zero_fill': True})
        raise
    finally:
        if recorder is not None:
            recorder.close()
        if owned_runner and not close_runner:
            journal.save('engine_shutdown.json',{'epoch':time.time(),'status':'lifecycle_delegated_to_development_controller'})
        if owned_runner and close_runner:
            try:
                runner.close()
                journal.save('engine_shutdown.json', {'epoch': time.time(), 'status': 'shutdown_returned'})
            except Exception as exc:
                journal.save('engine_shutdown.json', {'epoch': time.time(), 'status': 'shutdown_error', 'error': str(exc)})
