"""End-to-end whole-policy scoring and a label-access ordering regression."""
import json

import pytest

from tests.test_integrated_comparison_run import prepared_run
from tools.run_integrated_comparison import consume
from tools.score_integrated_comparison import score, sha
from tools.analyze_integrated_comparison import analyze


def test_all_policies_scored_and_changed_csv_blocks_before_label_access(prepared_run, tmp_path):
    root, prepared, run, native, resolved = prepared_run
    cpu = tmp_path / 'cpu'
    predictions = consume(prepared, run, root / 'data_open/data', cpu)
    labels = tmp_path / 'synthetic_labels.csv'
    labels.write_bytes((cpu / 'current.csv').read_bytes())
    baseline = tmp_path / 'references.json'
    baseline.write_text(json.dumps({
        'references': [{'id': 'synthetic_control',
                        'prediction': str(cpu / 'current.csv'),
                        'sha256': sha(cpu / 'current.csv')}],
        'exposed_development_labels_sha256': sha(labels),
    }), encoding='utf8')
    report = score(cpu, prepared, baseline, labels, tmp_path / 'scored')
    assert set(report['measurements']) == set(predictions)
    assert report['all_declared_combinations_complete']
    assert report['measurements']['current']['fp'] == 0
    assert report['measurements']['current']['fn'] == 0
    assert report['deployment_call_order_verified'] is False
    analysis = analyze(prepared, run, cpu, tmp_path / 'scored', tmp_path / 'analysis.json')
    assert analysis['policies']['current']['error_count'] == 0
    assert analysis['policies']['current+no_L']['logical_calls'] == 3
    assert analysis['labels_reopened'] is False
    assert analysis['shared_comparison_cost']['observed_attempts'] == len(native)
    target = cpu / 'factual_lexical+law+v9.csv'
    target.write_bytes(target.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='Frozen CSV changed'):
        score(cpu, prepared, baseline, tmp_path / 'unopened_missing_labels.csv',
              tmp_path / 'blocked')
    assert not (tmp_path / 'blocked').exists()
