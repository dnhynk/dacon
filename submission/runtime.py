"""Fixed cohort execution; preserve native outputs before parsing or CPU decisions."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import time
import traceback

from .b4_entry import B4Pipeline, PROFILES, assemble, parse_error
from .engine import POLICY, serial
from .pps.data import records, write_csv, missing_evidence_items

COHORT_SIZE = 32


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
        if any(self.root.iterdir()):
            raise FileExistsError('Use an empty output directory; existing results are preserved')
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


def call_plan(recs, packets):
    """Order depends only on input position and profile, never on labels or IDs."""
    index = {(p['record_id'], p['batch']): p for p in packets}
    if len(index) != len(packets) or len(packets) != 4 * len(recs):
        raise ValueError('Expected exactly four distinct packets per input record')
    plan = []
    for phase, profiles in (('A', PROFILES[:3]), ('L', PROFILES[3:])):
        for offset in range(0, len(recs), COHORT_SIZE):
            cohort = recs[offset:offset + COHORT_SIZE]
            for profile in profiles:
                batch = [index[(r['id'], profile)] for r in cohort]
                plan.append({'number': len(plan), 'phase': phase, 'profile': profile,
                             'cohort': offset // COHORT_SIZE,
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
                                  'attempt': 0, 'batch': active['number'],
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
        if attempt != 0:
            raise ValueError('The canonical run does not retry responses')
        name = f'call_{number:05d}_0'
        self.journal.rows(name + '_requests.jsonl.gz', packets)
        self.active = {'packets': packets, 'number': number, 'epoch': time.time(), 'name': name}
        self.captured = []
        response = self.runner.generate(packets, max_tokens=2048)
        if len(response) != len(self.captured) or len(response) != len(packets):
            raise RuntimeError('Missing parsed response; native outputs retained')
        for result, native in zip(response, self.captured):
            if result['raw_output_sha256'] != hashlib.sha256(native['native']['raw_text'].encode()).hexdigest():
                raise RuntimeError('Parsed/native output identity mismatch')
        self.journal.rows(name + '_responses.jsonl.gz', [
            {'request_key': p['request_key'], 'attempt': 0, 'response': r}
            for p, r in zip(packets, response)])
        self.journal.save(name + '.json', {
            'count': len(packets), 'attempt': 0, 'batch': number,
            'seconds': time.time() - self.active['epoch'],
            'input_tokens': sum(len(p['token_ids']) for p in packets),
            'output_tokens': sum(r['output_tokens'] for r in response),
            'cached_input_tokens': [r.get('cached_input_tokens') for r in response]})
        return response

    def close(self):
        self.runner.llm.generate = self.original


def execute(input_path, data_dir, output_dir, runner=None, *, tokenizer=None,
            runner_factory=None, limit=None, generation=None):
    """CLI supplies a lazy factory; label-free tests may inject stored responses."""
    journal = Journal(output_dir)
    started = time.monotonic()
    recorder = None
    owned_runner = False
    submitted = returned = 0
    code = source_manifest()
    try:
        journal.progress(phase='prepare_current_inputs', submitted=0, returned=0)
        recs = list(records(input_path, limit))
        pipeline = B4Pipeline(data_dir, tokenizer if tokenizer is not None else runner.tokenizer)
        if pipeline.config.batch_size != COHORT_SIZE:
            raise ValueError('The canonical cohort size must remain 32')
        packets = pipeline.packets(recs)
        plan = call_plan(recs, packets)
        by_id = {r['id']: r for r in recs}
        by_key = {p['request_key']: p for p in packets}
        journal.rows('current_inputs.jsonl.gz', recs)
        journal.rows('current_packets.jsonl.gz', packets)
        journal.save('call_plan.json', {'policy': POLICY, 'batches': plan})
        journal.save('input_freeze.json', {
            'epoch': time.time(), 'labels_read': False, 'source_sha256': code,
            'input_sha256': sha256(journal.root / 'current_inputs.jsonl.gz'),
            'packets_sha256': sha256(journal.root / 'current_packets.jsonl.gz'),
            'call_plan_sha256': sha256(journal.root / 'call_plan.json'),
            'records': len(recs), 'primary_requests': len(packets), 'policy': POLICY})
        if runner is None:
            if runner_factory is None:
                raise ValueError('A model runner factory is required')
            journal.progress(phase='engine_load', submitted=0, returned=0)
            runner = runner_factory(pipeline.config, journal)
            owned_runner = True
        if runner.config != pipeline.config:
            raise ValueError('The preserved consumer/engine configuration must be retained')
        if generation is None:
            recorder = NativeRecorder(runner, journal)
            generation = recorder.generate
        consumed = {}
        invalid = []
        for planned in plan:
            batch = [by_key[k] for k in planned['request_keys']]
            submitted += len(batch)
            journal.progress(phase='generate', batch=planned['number'],
                             profile=planned['profile'], cohort=planned['cohort'],
                             submitted=submitted, returned=returned)
            responses = generation(batch, planned['number'], 0)
            journal.rows(f"first_part_{planned['number']:05d}.jsonl.gz", [
                {'request_key': p['request_key'], 'response': r} for p, r in zip(batch, responses)])
            returned += len(responses)
            if len(responses) != len(batch):
                raise RuntimeError('Missing primary responses; no automatic retry')
            entries = []
            for packet, response in zip(batch, responses):
                error = parse_error(packet, response)
                row = details = None
                if error is None:
                    try:
                        row, details = pipeline.consume(by_id[packet['record_id']], packet, response)
                    except (ValueError, TypeError, KeyError) as exc:
                        error = 'CPU: ' + type(exc).__name__ + ': ' + str(exc)
                consumed[packet['request_key']] = row
                if error is not None:
                    invalid.append({'request_key': packet['request_key'], 'error': error})
                entries.append({'request_key': packet['request_key'], 'first_parse_error': error,
                                'retries': 0, 'error': error, 'row': row,
                                'rule_details': details, 'final_response': response})
            journal.rows(f"final_part_{planned['number']:05d}.jsonl.gz", entries)
            journal.progress(phase='batch_saved', batch=planned['number'], profile=planned['profile'],
                             submitted=submitted, returned=returned, invalid=len(invalid))
        b3, b4 = assemble(recs, packets, consumed)
        missing = {name: sum(row[f'v{k}'] is None for row in values.values() for k in range(1, 25))
                   for name, values in [('final_B3', b3), ('final_B4', b4)]}
        journal.rows('final_B3.jsonl.gz', list(b3.values()))
        journal.rows('final_B4.jsonl.gz', list(b4.values()))
        journal.save('response_freeze.json', {
            'epoch': time.time(), 'labels_read': False,
            'files': {p.name: sha256(p) for p in sorted(journal.root.glob('call_*_*.jsonl.gz'))}})
        report = {'records': len(recs), 'primary_requests': submitted, 'returned': returned,
                  'requests_with_retries': submitted, 'parse_retries': 0,
                  'new_model_calls': submitted if recorder is not None else 0,
                  'mode': 'fixed_model' if recorder is not None else 'injected_cpu_validation',
                  'single_engine': True, 'policy': POLICY, 'missing_bits': missing,
                  'invalid_responses': invalid, 'official_score': None,
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
                                     'returned': returned, 'parse_retries': 0, 'no_missing_zero_fill': True})
        raise
    finally:
        if recorder is not None:
            recorder.close()
        if owned_runner:
            try:
                runner.close()
                journal.save('engine_shutdown.json', {'epoch': time.time(), 'status': 'shutdown_returned'})
            except Exception as exc:
                journal.save('engine_shutdown.json', {'epoch': time.time(), 'status': 'shutdown_error', 'error': str(exc)})
