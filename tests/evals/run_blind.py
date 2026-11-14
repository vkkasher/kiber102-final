"""Run the frozen new internal requests in a disposable TEST_DATA database."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from apps.api.app.main import create_app
from apps.api.app.models import AgentTrace, Event, EventSource, Feedback, Subscription
from apps.api.app.schemas import normalize

HERE = Path(__file__).parent
FROZEN_SHA256 = '4591ecfa974f244d91256f176d3c01705592956fc7b9dffb27a0e73583cc5e00'


def load_cases():
    dataset = json.loads((HERE/'blind-20261113.json').read_text(encoding='utf-8'))
    content = {key:dataset[key] for key in ['cases', 'fixtures']}
    actual = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if actual != FROZEN_SHA256 or dataset['metadata']['content_sha256'] != actual:
        raise ValueError('Frozen request/oracle file changed')
    return dataset


def seed(app, fixtures):
    with app.state.session_factory() as session:
        for fixture in fixtures:
            record = dict(fixture)
            start = datetime.fromisoformat(record.pop('date_start')).astimezone(timezone.utc)
            end = datetime.fromisoformat(record.pop('date_end')).astimezone(timezone.utc)
            price_min, price_max = record['price_min'], record['price_max']
            url = 'https://example.org/TEST_DATA/' + record['id']
            checked = datetime.now(timezone.utc)
            fingerprint = hashlib.sha256(json.dumps(fixture, sort_keys=True).encode()).hexdigest()
            status = record.pop('status', 'published')
            event = Event(**record, normalized_title=normalize(record['title']), city='Уфа',
                          venue='TEST_DATA Общая площадка', address='TEST_DATA Один адрес', tags=[],
                          description='TEST_DATA Только в изолированной БД.', date_start=start,
                          date_end=end, duration_min=int((end-start).total_seconds()/60),
                          is_free=price_min == price_max == 0, language='русский', source_url=url,
                          source_type='TEST_DATA', source_hash=fingerprint, dedup_key=fingerprint,
                          verified_at=checked, confidence=1, status=status)
            event.sources = [EventSource(url=url, name='TEST_DATA Frozen request fixture', verified_at=checked)]
            session.add(event)
        session.commit()


def count_writes(app):
    with app.state.session_factory() as session:
        return sum(session.scalar(select(func.count()).select_from(model)) for model in [Feedback, Subscription])


def run(output):
    dataset = load_cases()
    answers = []
    with TemporaryDirectory(prefix='kiber102-blind-TEST_DATA-') as folder:
        app = create_app('sqlite:///' + str(Path(folder)/'TEST_DATA.sqlite'))
        with TestClient(app) as client:
            seed(app, dataset['fixtures'])
            for case in dataset['cases']:
                payload = case.get('payload') or {'message':case['message'], 'state':case.get('state', {}), 'offline':True}
                before = count_writes(app)
                started = perf_counter()
                response = client.post(case.get('endpoint', '/api/v1/agent/query'), json=payload)
                elapsed = (perf_counter()-started)*1000
                body, oracle = response.json(), case['oracle']
                ids = [event['id'] for event in body.get('events', [])]
                problems = []
                if response.status_code != 200:
                    problems.append(f'HTTP {response.status_code}')
                if set(ids) != set(oracle['event_ids']):
                    problems.append(f"event_ids expected {oracle['event_ids']} got {ids}")
                for field, expected in oracle.get('filters', {}).items():
                    actual = body.get('state', {}).get('filters', {}).get(field)
                    if actual != expected:
                        problems.append(f'filter {field}: expected {expected!r}, got {actual!r}')
                if 'question' in oracle and bool(body.get('question')) != oracle['question']:
                    problems.append('unexpected clarification state')
                for identifier, expected in oracle.get('event_statuses', {}).items():
                    actual = next((event.get('status') for event in body.get('events', []) if event['id'] == identifier), None)
                    if actual != expected:
                        problems.append(f'event status {identifier}: expected {expected}, got {actual}')
                if oracle.get('text_contains') and oracle['text_contains'] not in body.get('text', '').casefold().replace('ё', 'е'):
                    problems.append('missing status/explanation text')
                pending = body.get('pending_action') or {}
                if oracle.get('pending_tool') and pending.get('tool') != oracle['pending_tool']:
                    problems.append('wrong pending tool')
                for field, expected in oracle.get('pending_arguments', {}).items():
                    if pending.get('arguments', {}).get(field) != expected:
                        problems.append(f'wrong pending argument {field}')
                if 'writes' in oracle and count_writes(app)-before != oracle['writes']:
                    problems.append('unexpected mutation without confirmation')
                for field in ['feasible', 'minimal_change']:
                    actual = body.get(field)
                    # Frozen labels predate additive provenance. Compare every
                    # original constraint exactly, without changing the oracle.
                    if field == 'minimal_change' and isinstance(actual, dict):
                        actual = {key:value for key, value in actual.items() if key not in {'evidence_event_ids', 'evidence_source_urls'}}
                    if field in oracle and actual != oracle[field]:
                        problems.append(f'wrong {field}: expected {oracle[field]}, got {body.get(field)}')
                trace = {}
                if body.get('trace_id'):
                    with app.state.session_factory() as session:
                        row = session.get(AgentTrace, body['trace_id'])
                        trace = row.payload if row else {}
                if oracle.get('validation') and trace.get('validation') != oracle['validation']:
                    problems.append('wrong trace validation status')
                tools = [call for call in trace.get('tool_calls', []) if call['name'] != 'reference_cities']
                if oracle.get('primary_tool') and (not tools or tools[0]['name'] != oracle['primary_tool']):
                    problems.append('wrong primary tool')
                if body.get('tool_calls', 0) > 5:
                    problems.append('more than five tools')
                answers.append({'id':case['id'], 'passed':not problems, 'problems':problems,
                                'response':body, 'trace':trace, 'latency_ms':round(elapsed, 3)})
    report = {'created_at':datetime.now(timezone.utc).isoformat(), 'dataset_sha256':FROZEN_SHA256,
              'scope':dataset['metadata']['prior_author_context'], 'model':'disabled, offline=True',
              'model_calls':0, 'model_cost':0, 'case_count':len(answers),
              'passed':sum(answer['passed'] for answer in answers), 'answers':answers}
    path = Path(output)
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    for answer in answers:
        print(answer['id'], 'PASS' if answer['passed'] else 'FAIL', '; '.join(answer['problems']))
    print(f"{report['passed']}/{report['case_count']} frozen new internal contracts")
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='.local/validation/blind-current.json')
    arguments = parser.parse_args()
    result = run(arguments.output)
    raise SystemExit(result['passed'] != result['case_count'])
