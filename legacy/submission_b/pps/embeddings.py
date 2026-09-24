"""Offline BGE-M3 dense, sparse and token-vector retrieval; no legal judgments."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import time

BGE_MODEL = 'BAAI/bge-m3'
BGE_REVISION = '5617a9f61b028005a4858fdac845db406aefb181'
SPARSE_HEAD_SHA256 = '45c93804d2142b8f6d7ec6914ae23a1eee9c6a1d27d83d908a20d2afb3595ad9'
COLBERT_HEAD_SHA256 = '19bfbae397c2b7524158c919d0e9b19393c5639d098f0a66932c91ed8f5f9abb'


def sparse_weights(ids, weights, ignored=()):
    """Official M3 pooling: max nonnegative weight per original token ID."""
    if len(ids) != len(weights):
        raise ValueError('Sparse token/weight lengths differ')
    result = {}
    for token, weight in zip(ids, weights):
        if type(token) is not int or token < 0 or type(weight) not in (int, float) or not math.isfinite(weight) or weight < 0:
            raise ValueError('Invalid sparse token weight')
        if token not in ignored and weight > 0:
            result[token] = max(result.get(token, 0.), float(weight))
    return dict(sorted(result.items()))


def sparse_similarity(left, right):
    """Lexical relevance; not normalized or interpreted as a probability."""
    return math.fsum(left[token] * right[token] for token in sorted(left.keys() & right.keys()))


def colbert_similarity(query, document):
    """Official M3 late interaction: mean of each query token's best match.

    Inputs contain real tokens after CLS, including EOS, without padding.
    The score is directional relevance, not a probability; negative maxima
    remain negative. This function never makes a legal or absence decision.
    """
    import numpy as np
    if (not isinstance(query, np.ndarray) or not isinstance(document, np.ndarray)
            or query.ndim != 2 or document.ndim != 2 or not query.shape[0]
            or not document.shape[0] or not query.shape[1] or query.shape[1] != document.shape[1]
            or not np.issubdtype(query.dtype, np.floating) or not np.issubdtype(document.dtype, np.floating)
            or not np.isfinite(query).all() or not np.isfinite(document).all()):
        raise ValueError('Invalid ColBERT token vectors')
    scores = query @ document.T
    value = float(scores.max(axis=1).mean())
    if not math.isfinite(value):
        raise ValueError('Non-finite ColBERT relevance')
    return value


class BGEDenseEncoder:
    """CLS + L2 normalization, as specified by the fixed BGE-M3 checkpoint.

    The default CPU float32 path is the reference. A CUDA float32 encoder with
    TF32 disabled is the same arithmetic on the GPU; it exists so that search
    embeddings do not serialize the whole preparation behind one CPU. All inputs
    must fit; silent embedding truncation is refused. There is no cross-notice
    document or answer cache here.
    """
    def __init__(self, model_dir=None, *, threads=7, batch_size=8, max_length=512, sparse=False, colbert=False,
                 device='cpu', dtype='float32'):
        import torch
        from transformers import AutoModel, AutoTokenizer
        if type(threads) is not int or not 1 <= threads <= 7:
            raise ValueError('CPU retrieval requires 1..7 threads')
        if device not in {'cpu', 'cuda'} or dtype not in {'float32', 'float16', 'bfloat16'}:
            raise ValueError('Retrieval encoder device must be cpu/cuda with a float32/float16/bfloat16 dtype')
        if device == 'cpu' and dtype != 'float32':
            raise ValueError('CPU retrieval keeps float32; reduced precision is a GPU-only option')
        if device == 'cuda' and not torch.cuda.is_available():
            raise RuntimeError('CUDA retrieval encoder requested without a usable GPU')
        if type(batch_size) is not int or batch_size <= 0:
            raise ValueError('batch_size must be positive')
        if type(max_length) is not int or not 8 <= max_length <= 8192:
            raise ValueError('Invalid BGE maximum length')
        if type(sparse) is not bool:
            raise ValueError('Sparse mode must be explicitly boolean')
        if type(colbert) is not bool:
            raise ValueError('ColBERT mode must be explicitly boolean')
        path = Path(model_dir or os.environ.get('PPS_EMBED_DIR', '/opt/models/BAAI/bge-m3'))
        if not path.is_dir():
            raise FileNotFoundError(f'Offline BGE directory is unavailable: {path}')
        self.torch = torch
        torch.set_num_threads(threads)
        self.device, self.dtype_name = device, dtype
        weight_dtype = getattr(torch, dtype)
        if device == 'cuda':
            # Same IEEE float32 arithmetic as the CPU reference; TF32 would
            # silently round matmul inputs and move dense rankings.
            torch.backends.cuda.matmul.allow_tf32 = False
            torch.backends.cudnn.allow_tf32 = False
        began = time.monotonic()
        self.tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)
        self.model = AutoModel.from_pretrained(path, local_files_only=True, trust_remote_code=False,
                                               dtype=weight_dtype, attn_implementation='sdpa')
        if self.model.config.model_type != 'xlm-roberta' or self.model.config.hidden_size != 1024:
            raise ValueError('Expected the supplied BGE-M3 XLM-R checkpoint')
        self.model.eval().to(device)
        self.sparse_enabled = sparse
        self.colbert_enabled = colbert
        self.sparse_linear = None
        self.colbert_linear = None
        if sparse:
            head = path / 'sparse_linear.pt'
            if not head.is_file() or hashlib.sha256(head.read_bytes()).hexdigest() != SPARSE_HEAD_SHA256:
                raise ValueError('The fixed BGE-M3 sparse head is missing or has changed')
            self.sparse_linear = torch.nn.Linear(1024, 1)
            self.sparse_linear.load_state_dict(torch.load(head, map_location='cpu', weights_only=True), strict=True)
            self.sparse_linear.eval().to(device, dtype=weight_dtype)
        if colbert:
            head = path / 'colbert_linear.pt'
            if not head.is_file() or hashlib.sha256(head.read_bytes()).hexdigest() != COLBERT_HEAD_SHA256:
                raise ValueError('The fixed BGE-M3 ColBERT head is missing or has changed')
            self.colbert_linear = torch.nn.Linear(1024, 1024)
            self.colbert_linear.load_state_dict(torch.load(head, map_location='cpu', weights_only=True), strict=True)
            self.colbert_linear.eval().to(device, dtype=weight_dtype)
        if self.tokenizer.padding_side != 'right':
            raise ValueError('BGE pooling requires the supplied right-padding tokenizer')
        self.ignored_tokens = {self.tokenizer.cls_token_id, self.tokenizer.eos_token_id,
                               self.tokenizer.pad_token_id, self.tokenizer.unk_token_id}
        self.batch_size, self.max_length = batch_size, max_length
        self.receipt = {'model': BGE_MODEL, 'required_revision': BGE_REVISION,
            'device': device, 'dtype': dtype, 'pooling': 'CLS_L2',
            'tf32_disabled': device == 'cuda',
            'threads': threads, 'batch_size': batch_size, 'max_length': max_length,
            'load_seconds': time.monotonic() - began, 'encoded_texts': 0,
            'encoded_tokens': 0, 'encode_seconds': 0., 'truncated_inputs': 0,
            'tokenizer_sha256': hashlib.sha256((path / 'tokenizer.json').read_bytes()).hexdigest()}
        if sparse:
            self.receipt.update(sparse_head_sha256=SPARSE_HEAD_SHA256,
                sparse_pooling='ReLU_linear_then_token_ID_max', sparse_forward_shared_with_dense=True)
        if colbert:
            self.receipt.update(colbert_head_sha256=COLBERT_HEAD_SHA256,
                colbert_pooling='fixed_linear_L2_each_real_token_after_CLS_including_EOS',
                colbert_similarity='query_mean_of_token_MaxSim', colbert_forward_shared_with_dense=True)
        provenance = path / 'competition_provenance.json'
        if provenance.is_file():
            supplied = json.loads(provenance.read_text(encoding='utf-8'))
            if supplied.get('revision') != BGE_REVISION or supplied.get('model') != BGE_MODEL:
                raise ValueError('Unexpected embedding checkpoint provenance')
            self.receipt['local_provenance'] = supplied

    def encode(self, texts):
        return self._encode(texts, sparse=False, colbert=False)['dense']

    def encode_features(self, texts):
        if not self.sparse_enabled and not self.colbert_enabled:
            raise ValueError('Joint features require an explicitly enabled fixed retrieval head')
        return self._encode(texts, sparse=self.sparse_enabled, colbert=self.colbert_enabled)

    def _encode(self, texts, *, sparse, colbert=False):
        import numpy as np
        if not texts:
            return {'dense': np.empty((0, 1024), dtype=np.float32),
                    'sparse': [] if sparse else None, 'colbert': [] if colbert else None}
        encoded = self.tokenizer(list(texts), add_special_tokens=True, truncation=False)['input_ids']
        lengths = list(map(len, encoded))
        if max(lengths) > self.max_length:
            raise ValueError(f'Embedding input exceeds {self.max_length} tokens; split source before encoding')
        # Stable length ordering cuts padding without changing the returned order.
        order = sorted(range(len(texts)), key=lambda i: (lengths[i], i))
        result = np.empty((len(texts), 1024), dtype=np.float32)
        lexical = [None] * len(texts) if sparse else None
        multi = [None] * len(texts) if colbert else None
        began = time.monotonic()
        for start in range(0, len(order), self.batch_size):
            indices = order[start:start + self.batch_size]
            batch = self.tokenizer.pad({'input_ids': [encoded[i] for i in indices]},
                                       padding=True, return_tensors='pt')
            if self.device != 'cpu':
                batch = {key: value.to(self.device) for key, value in batch.items()}
            with self.torch.inference_mode():
                states = self.model(**batch).last_hidden_state
                vectors = self.torch.nn.functional.normalize(states[:, 0].float(), p=2, dim=1)
                if sparse:
                    weights = self.torch.relu(self.sparse_linear(states.float())).squeeze(-1).cpu().tolist()
                if colbert:
                    projected = self.colbert_linear(states[:, 1:].float())
                    projected *= batch['attention_mask'][:, 1:, None].float()
                    token_vectors = self.torch.nn.functional.normalize(projected, p=2, dim=-1).cpu().numpy()
            result[indices] = vectors.cpu().numpy()
            if sparse:
                for index, row in zip(indices, weights):
                    lexical[index] = sparse_weights(encoded[index], row[:lengths[index]], self.ignored_tokens)
            if colbert:
                for index, row in zip(indices, token_vectors):
                    multi[index] = row[:lengths[index]-1].copy()
                    if not np.isfinite(multi[index]).all():
                        raise ValueError('Non-finite ColBERT token vectors')
        if not np.isfinite(result).all():
            raise ValueError('Non-finite retrieval vectors')
        self.receipt['encoded_texts'] += len(texts)
        self.receipt['encoded_tokens'] += sum(lengths)
        self.receipt['encode_seconds'] += time.monotonic() - began
        return {'dense': result, 'sparse': lexical, 'colbert': multi}
