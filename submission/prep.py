"""Parallel packet preparation and CPU consumption in worker processes.

The engine process must never wait on per-record CPU work. Each worker owns a
private canonical B4Pipeline; search embeddings come from one encoder service
so the fixed BGE-M3 weights are loaded once, on the GPU when one is usable and
otherwise on the CPU. Nothing crosses records: every task carries one record
and returns only that record's packets or rows. Workers never see the engine,
labels, other records or predictions.
"""
from __future__ import annotations

import multiprocessing
import os
import queue
import time
import traceback

PROFILE_ITEMS = {'A1': tuple(range(1, 10)), 'A10': tuple(range(10, 19)), 'A19': tuple(range(19, 25))}


def rules_only_row(pipe, record, items):
    """The deterministic source-only judgment for one item group.

    This is the canonical consumer with every response-dependent channel
    removed: the same rule checks, the same supplied-catalog qualification, no
    model facts, no model citations. Unknown source states stay 0; that is a
    withheld positive, never a certification of normality.
    """
    from .pps.data import make_row
    from .pps.rules import apply_rules
    items = tuple(items)
    config = pipe.config
    knowledge = pipe.knowledge.for_source(record) if config.source_verified_services else pipe.knowledge
    row = make_row(record, [0] * 24, [''] * 24)
    details = [{'source': 'source_only_judgment', 'items': list(items), 'model_called': False,
                'unknown_source_state_is_not_normality': True}]
    if config.rule_checks:
        comparison = None
        if config.cross_source_facts and 24 in items:
            from .pps.comparison import compare
            comparison = compare(record)
        row, applied = apply_rules(record, row, knowledge, comparison=comparison, items=items)
        details.extend(applied)
    qualification_items = set(items).intersection(range(10, 19))
    if config.qualification_checks and qualification_items:
        candidate, facts = knowledge.qualification_decisions(record, row)
        for k in qualification_items:
            row[f'v{k}'], row[f'e{k}'] = int(candidate[f'v{k}']), candidate[f'e{k}']
        details.append({'source': 'supplied_catalog_qualification_v2',
                        'items': sorted(qualification_items), 'facts': facts})
    return ({f'{field}{k}': int(row[f'v{k}']) if field == 'v' else row[f'e{k}']
             for k in items for field in ('v', 'e')}, details)


def prepare_record(pipe, record):
    """Every packet plus the source-only rows the executor may need instead."""
    packets = pipe.bundle(record)
    fallback = {profile: rules_only_row(pipe, record, items) for profile, items in PROFILE_ITEMS.items()}
    code_only = {}
    for packet in packets:
        if packet.get('batch') == 'A10' and 'source_questions' in packet and not packet['source_questions']['model_items']:
            from .pps.source_questions import code_only as source_code_only
            row, decision = source_code_only(record, packet, pipe.knowledge)
            code_only[packet['request_key']] = {'row': row, 'decision': decision}
    return {'packets': packets, 'fallback_rows': fallback, 'code_only': code_only}


def consume_response(pipe, record, packet, response):
    """Format validation first; CPU defects are reported, never retried on the model."""
    from .b4_entry import parse_error
    error = parse_error(packet, response)
    if error is not None:
        return {'parse_error': error, 'cpu_error': None, 'row': None, 'details': None}
    try:
        row, details = pipe.consume(record, packet, response)
    except (ValueError, TypeError, KeyError) as exc:
        return {'parse_error': None, 'cpu_error': 'CPU: ' + type(exc).__name__ + ': ' + str(exc),
                'row': None, 'details': None}
    return {'parse_error': None, 'cpu_error': None, 'row': row, 'details': details}


def execute_task(pipe, task):
    kind = task['kind']
    if kind == 'prepare':
        return prepare_record(pipe, task['record'])
    if kind == 'consume':
        return consume_response(pipe, task['record'], task['packet'], task['response'])
    if kind == 'rules':
        row, details = rules_only_row(pipe, task['record'], task['items'])
        return {'row': row, 'details': details}
    raise ValueError('Unknown preparation task: ' + str(kind))


class EncoderClient:
    """Drop-in for BGEDenseEncoder.encode inside a worker; one service, one GPU copy."""
    sparse_enabled = False
    colbert_enabled = False

    def __init__(self, worker_id, requests, reply):
        self.worker_id, self.requests, self.reply = worker_id, requests, reply
        kind, payload = reply.recv()
        if kind != 'ready':
            raise RuntimeError('Encoder service unavailable: ' + str(payload))
        self.receipt = dict(payload)
        self.receipt['served_by'] = 'encoder_service'

    def encode(self, texts):
        import numpy as np
        texts = list(texts)
        if not texts:
            return np.empty((0, 1024), dtype=np.float32)
        self.requests.put((self.worker_id, texts))
        kind, payload = self.reply.recv()
        if kind != 'ok':
            raise RuntimeError('Encoder service failed: ' + str(payload))
        return payload

    def encode_features(self, texts):
        raise ValueError('Joint sparse/ColBERT features are not served by the encoder service')


def build_encoder(options):
    """CUDA float32 with TF32 off when a GPU is usable; CPU float32 otherwise."""
    from .pps.embeddings import BGEDenseEncoder
    preference = options.get('device', 'auto')
    kwargs = {'model_dir': options.get('model_dir'), 'threads': options.get('threads', 7),
              'batch_size': options.get('batch_size', 8)}
    attempts = []
    if preference in {'auto', 'cuda'}:
        try:
            import torch
            if torch.cuda.is_available():
                return BGEDenseEncoder(device='cuda', dtype=options.get('dtype', 'float32'), **kwargs), attempts
            attempts.append('cuda_unavailable')
        except Exception as exc:  # OOM at load, missing kernels: keep the run alive on CPU.
            attempts.append('cuda_failed: ' + type(exc).__name__ + ': ' + str(exc)[:200])
        if preference == 'cuda':
            raise RuntimeError('CUDA encoder requested but unavailable: ' + '; '.join(attempts))
    return BGEDenseEncoder(device='cpu', **kwargs), attempts


def _is_cuda_memory_error(exc):
    name = type(exc).__name__
    return 'OutOfMemory' in name or 'out of memory' in str(exc).lower()


def encoder_service_main(requests, replies, options):
    os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
    try:
        encoder, attempts = build_encoder(options)
    except BaseException as exc:
        for connection in replies.values():
            connection.send(('fatal', type(exc).__name__ + ': ' + str(exc)))
        return
    receipt = {**encoder.receipt, 'device_attempts': attempts}
    for name, connection in replies.items():
        if name != '__control__':
            connection.send(('ready', receipt))
    while True:
        item = requests.get()
        if item is None:
            break
        worker_id, texts = item
        if worker_id == '__receipt__':
            control = replies.get('__control__')
            if control is not None:
                control.send(('receipt', {**encoder.receipt, 'device_attempts': attempts}))
            continue
        try:
            vectors = encoder.encode(texts)
        except Exception as exc:
            if getattr(encoder, 'device', 'cpu') == 'cuda' and _is_cuda_memory_error(exc):
                # Memory pressure from the engine: finish this run on the CPU
                # with the identical float32 arithmetic rather than fail it.
                try:
                    encoder = build_encoder({**options, 'device': 'cpu'})[0]
                    vectors = encoder.encode(texts)
                except Exception as inner:
                    replies[worker_id].send(('error', type(inner).__name__ + ': ' + str(inner)))
                    continue
            else:
                replies[worker_id].send(('error', type(exc).__name__ + ': ' + str(exc)))
                continue
        replies[worker_id].send(('ok', vectors))


def worker_main(worker_id, consume_tasks, prepare_tasks, results, options, encoder_link):
    os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
    try:
        from transformers import AutoTokenizer
        from .b4_entry import B4Pipeline
        from .engine import serial
        tokenizer = AutoTokenizer.from_pretrained(options['tokenizer_dir'], local_files_only=True,
                                                  trust_remote_code=False)
        encoder = None
        if encoder_link is not None:
            encoder = EncoderClient(worker_id, encoder_link[0], encoder_link[1])
        pipe = B4Pipeline(options['data_dir'], tokenizer, encoder=encoder, **options.get('source_options', {}))
        results.put({'kind': 'ready', 'worker': worker_id, 'config': serial(pipe.config),
                     'encoder': None if encoder is None else encoder.receipt})
    except BaseException as exc:
        results.put({'kind': 'ready', 'worker': worker_id, 'error': type(exc).__name__ + ': ' + str(exc),
                     'traceback': traceback.format_exc()})
        return
    while True:
        try:
            task = consume_tasks.get_nowait()
        except queue.Empty:
            try:
                task = prepare_tasks.get(timeout=0.05)
            except queue.Empty:
                continue
        if task is None:
            break
        began = time.monotonic()
        try:
            payload = execute_task(pipe, task)
            results.put({'kind': task['kind'], 'id': task['id'], 'worker': worker_id,
                         'seconds': time.monotonic() - began, **payload})
        except Exception as exc:
            results.put({'kind': task['kind'], 'id': task['id'], 'worker': worker_id,
                         'seconds': time.monotonic() - began,
                         'error': type(exc).__name__ + ': ' + str(exc), 'traceback': traceback.format_exc()})


def default_worker_count():
    try:
        cores = len(os.sched_getaffinity(0))   # container cpusets; not a CPU quota
    except AttributeError:
        cores = os.cpu_count() or 4
    # The engine process and the encoder service each need a core of their own.
    return max(1, min(6, cores - 2))


class PreparationPool:
    """Main-process handle: submit tasks, poll results; inline mode runs in-process."""

    def __init__(self, data_dir, tokenizer_dir, source_options=None, *, workers=None,
                 encoder='auto', encoder_dir=None, encoder_threads=7, inline_pipeline=None):
        self.data_dir, self.tokenizer_dir = str(data_dir), str(tokenizer_dir)
        self.source_options = dict(source_options or {})
        self.workers = default_worker_count() if workers is None else int(workers)
        if self.workers < 0:
            raise ValueError('Worker count must be nonnegative; zero runs preparation inline')
        self.encoder_preference = encoder
        self.encoder_dir = encoder_dir
        self.encoder_threads = encoder_threads
        self.inline_pipeline = inline_pipeline
        self.processes, self.encoder_process = [], None
        self.consume_tasks = self.prepare_tasks = self.results = self.encoder_requests = None
        self.encoder_connections = {}
        self.encoder_control = None
        self.ready = {}
        self.encoder_receipt = None
        self.outstanding = 0
        self._inline_results = []
        self.started = False

    @property
    def inline(self):
        return self.workers == 0

    def start(self):
        if self.started:
            return
        self.started = True
        if self.inline:
            if self.inline_pipeline is None:
                from transformers import AutoTokenizer
                from .b4_entry import B4Pipeline
                tokenizer = AutoTokenizer.from_pretrained(self.tokenizer_dir, local_files_only=True,
                                                          trust_remote_code=False)
                self.inline_pipeline = B4Pipeline(self.data_dir, tokenizer, **self.source_options)
            self.ready[0] = {'kind': 'ready', 'worker': 0, 'inline': True}
            return
        context = multiprocessing.get_context('spawn')
        self.consume_tasks, self.prepare_tasks = context.Queue(), context.Queue()
        self.results = context.Queue()
        encoder_links = {}
        if self.encoder_preference != 'none':
            self.encoder_requests = context.Queue()
            sends = {}
            for worker_id in range(self.workers):
                receive, send = context.Pipe(duplex=False)
                encoder_links[worker_id] = (self.encoder_requests, receive)
                sends[worker_id] = send
            self.encoder_control, sends['__control__'] = context.Pipe(duplex=False)
            options = {'device': self.encoder_preference, 'model_dir': self.encoder_dir,
                       'threads': self.encoder_threads}
            self.encoder_process = context.Process(target=encoder_service_main, name='pps-encoder',
                                                   args=(self.encoder_requests, sends, options), daemon=True)
            self.encoder_process.start()
        options = {'data_dir': self.data_dir, 'tokenizer_dir': self.tokenizer_dir,
                   'source_options': self.source_options}
        for worker_id in range(self.workers):
            process = context.Process(target=worker_main, name=f'pps-prep-{worker_id}',
                                      args=(worker_id, self.consume_tasks, self.prepare_tasks, self.results,
                                            options, encoder_links.get(worker_id)), daemon=True)
            process.start()
            self.processes.append(process)

    def submit(self, task):
        if not self.started:
            raise RuntimeError('Start the preparation pool before submitting work')
        if 'kind' not in task or 'id' not in task:
            raise ValueError('Preparation tasks need a kind and an identity')
        self.outstanding += 1
        if self.inline:
            began = time.monotonic()
            try:
                payload = execute_task(self.inline_pipeline, task)
                self._inline_results.append({'kind': task['kind'], 'id': task['id'], 'worker': 0,
                                             'seconds': time.monotonic() - began, **payload})
            except Exception as exc:
                self._inline_results.append({'kind': task['kind'], 'id': task['id'], 'worker': 0,
                                             'seconds': time.monotonic() - began,
                                             'error': type(exc).__name__ + ': ' + str(exc),
                                             'traceback': traceback.format_exc()})
            return
        (self.consume_tasks if task['kind'] == 'consume' else self.prepare_tasks).put(task)

    def poll(self, timeout=0.0):
        """Completed results; readiness messages are absorbed, failures surface."""
        if self.inline:
            collected, self._inline_results = self._inline_results, []
            self.outstanding -= len(collected)
            if not collected and timeout > 0:
                time.sleep(min(timeout, .005))
            return collected
        collected = []
        first = True
        while True:
            try:
                if first and timeout > 0:
                    item = self.results.get(timeout=timeout)
                else:
                    item = self.results.get_nowait()
            except queue.Empty:
                break
            first = False
            if item.get('kind') == 'ready':
                self.ready[item['worker']] = item
                if item.get('error'):
                    raise RuntimeError('Preparation worker failed to start: ' + item['error'] + '\n' + item.get('traceback', ''))
                if item.get('encoder') is not None and self.encoder_receipt is None:
                    self.encoder_receipt = item['encoder']
                continue
            self.outstanding -= 1
            collected.append(item)
        for process in self.processes:
            if not process.is_alive() and process.exitcode not in (None, 0):
                raise RuntimeError(f'Preparation worker {process.name} exited with code {process.exitcode}')
        return collected

    def receipt(self, live=False):
        """Pool facts; live=True asks the encoder service for its current counters."""
        if live and self.encoder_process is not None and self.encoder_process.is_alive() and self.encoder_control is not None:
            try:
                self.encoder_requests.put(('__receipt__', None))
                if self.encoder_control.poll(10):
                    kind, payload = self.encoder_control.recv()
                    if kind == 'receipt':
                        self.encoder_receipt = payload
            except Exception as exc:
                self.encoder_receipt = {**(self.encoder_receipt or {}), 'live_receipt_error': repr(exc)}
        return {'workers': self.workers, 'inline': self.inline, 'encoder': self.encoder_receipt,
                'ready_workers': sorted(self.ready), 'encoder_preference': self.encoder_preference}

    def close(self):
        if not self.started or self.inline:
            return
        for _ in self.processes:
            try:
                self.prepare_tasks.put(None)
            except Exception:
                pass
        if self.encoder_requests is not None:
            try:
                self.encoder_requests.put(None)
            except Exception:
                pass
        for process in [*self.processes, *([self.encoder_process] if self.encoder_process else [])]:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
