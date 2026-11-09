"""Offline contracts for the synthetic pilot, including no-spend failure paths."""
import json
import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "agent_pilot_contract", ROOT / "benchmark/agent_comparison/pilot.py",
    submodule_search_locations=[str(ROOT / "benchmark/agent_comparison")])
pilot = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pilot
spec.loader.exec_module(pilot)
CASES = pilot.CASES


def transport_fixture(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_plan_is_offline_and_answers_are_not_in_requests(tmp_path):
    directory = tmp_path / 'pilot'
    value = pilot.plan(directory)
    assert pilot.verify(directory) == value
    for item in value['inputs']:
        body = pilot.body_for(value, item, pilot.MODELS[0], {'prompt': 0, 'completion': 0})
        assert 'expected' not in json.dumps(body)
    with pytest.raises(FileExistsError):
        pilot.plan(directory)
    (directory / 'keys.json').write_text('{}')
    with pytest.raises(ValueError, match='Key digest'):
        pilot.verify(directory)


@pytest.mark.parametrize('field,value', [('limit', None), ('limit', 51), ('limit_reset', 'daily'), ('include_byok_in_limit', False), ('limit_remaining', 0), ('usage', float('nan'))])
def test_budget_rejects_unsafe_key(field, value):
    data = dict(limit=50, limit_remaining=12, usage=38, limit_reset=None, include_byok_in_limit=True)
    data[field] = value
    with pytest.raises(ValueError):
        pilot.validate_key(data)


def test_preflight_failure_never_posts_and_prevents_replay(tmp_path):
    directory = tmp_path / 'pilot'
    pilot.plan(directory)
    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={'data': dict(limit=50, limit_remaining=12, usage=38, limit_reset=None, include_byok_in_limit=False)})
    with transport_fixture(handler) as client:
        with pytest.raises(ValueError):
            pilot.run(directory, 'fake-test-secret', client)
        with pytest.raises(FileExistsError):
            pilot.run(directory, 'fake-test-secret', client)
    assert calls == ['GET']
    assert 'fake-test-secret' not in (directory / 'report.json').read_text()


@pytest.mark.parametrize('failure', ['none', 'transport', 'cost', 'model'])
def test_dispatch_accounting_and_stop(tmp_path, failure):
    directory = tmp_path / 'pilot'
    pilot.plan(directory)
    posts = []
    def handler(request):
        if request.url.path.endswith('/key'):
            return httpx.Response(200, json={'data': dict(limit=50, limit_remaining=12, usage=38, limit_reset=None, include_byok_in_limit=True)})
        if request.url.path.endswith('/models'):
            return httpx.Response(200, json={'data': [{'id': model, 'pricing': {'prompt': '0.0000001', 'completion': '0.0000001'}} for model in pilot.MODELS]})
        body = json.loads(request.content)
        posts.append(body)
        assert (directory / f'{len(posts)-1:02d}-pending.json').exists()
        if failure == 'transport':
            raise httpx.ReadTimeout('ambiguous', request=request)
        case = CASES[(len(posts)-1)//3]
        return httpx.Response(200, json={'id': 'generation-test', 'provider': 'test', 'model': 'wrong' if failure == 'model' else body['model'], 'usage': {} if failure == 'cost' else {'cost': 0.00001}, 'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(case['expected'])}}]})
    with transport_fixture(handler) as client:
        report = pilot.run(directory, 'fake-test-secret', client)
    assert len(posts) == (18 if failure == 'none' else 1)
    if failure == 'none':
        assert report['stop_reason'] is None
        assert all(item['score']['passed'] for item in report['results'])
    else:
        assert report['stop_reason']


def test_score_rejects_duplicate_or_extra_evidence():
    expected = CASES[0]['expected']
    assert pilot.score(json.dumps(expected), expected)['passed']
    assert not pilot.score(json.dumps({**expected, 'evidence_ids': ['E1', 'E1']}), expected)['passed']
    assert not pilot.score(json.dumps({**expected, 'evidence_ids': ['E2']}), expected)['passed']
    assert not pilot.score('```json\n{}\n```', expected)['passed']
