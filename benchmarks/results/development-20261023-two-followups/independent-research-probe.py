"""Independent maximum-size legal source checks; no novel or source writes."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'tests'))
import test_research as fixture

research, rank = fixture.research, fixture.research.rank
snapshot = rank.template()
first = snapshot['entries'][0]
first.update(book_id='max-a', book_url='https://example.invalid/max-a',
             category='类' * 1000, tags=[f'{i:03}' + '🧭' * 997 for i in range(100)])
second = deepcopy(first)
second.update(book_id='max-b', book_url='https://example.invalid/max-b', rank=2)
snapshot['entries'].append(second)
rows = []
for legacy, algorithm in ((False, 2), (True, 1), (True, 2)):
    sample = rank._build_sample([snapshot], limit=2, algorithm_version=algorithm, legacy=legacy)
    original = rank._json(sample)
    decision = research.initialize(sample)
    assert rank._json(sample) == original == rank._json(decision['sample'])
    complete = '；'.join(sample['books'][0]['reasons'])
    summary = decision['topics'][0]['books'][0]['reason']
    assert len(complete) > 40000 and len(summary) <= 8000
    assert sample['books'][0]['book_key'] in summary and 'sample.books' in summary
    assert sample['books'][0]['reasons'][-1] in summary
    assert research.validate_decision(decision) == decision
    assert decision['sample']['sample_id'] == sample['sample_id']
    bad = deepcopy(decision)
    bad['topics'][0]['books'][0]['reason'] = '界' * 8001
    try:
        research.validate_decision(bad)
    except rank.RankError:
        pass
    else:
        raise AssertionError('User prose limit was relaxed')
    rows.append({'legacy': legacy, 'algorithm': algorithm,
                 'complete_reason_chars': len(complete), 'summary_chars': len(summary),
                 'sample_id': sample['sample_id'], 'source_identical': True})
legacy_path = ROOT / 'tests/fixtures/rank-samples/decision-v1-released.json'
legacy_bytes = legacy_path.read_bytes()
legacy_decision = json.loads(legacy_bytes)
assert research.validate_decision(legacy_decision) == legacy_decision
assert legacy_path.read_bytes() == legacy_bytes
print(json.dumps({'passed': True, 'maximum_legal_tags': rows,
                  'legacy_verified_decision_unchanged': True,
                  'source_sha256': hashlib.sha256((ROOT / 'skills/story-skill/scripts/story_research.py').read_bytes()).hexdigest()},
                 ensure_ascii=False, indent=2))
