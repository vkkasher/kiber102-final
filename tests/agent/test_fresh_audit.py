"""Regressions found by rereading the literal PDF, beyond the older suites."""
import asyncio
from datetime import date
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from apps.api.app.agent import SearchFilters, extract_filters, llm_extract
from apps.api.app.main import create_app
from apps.bot.service import BotService, StateStore
from tests.evals.run_blind import load_cases, run, seed


def test_frozen_new_requests_remain_unchanged_and_pass(tmp_path):
    report = run(tmp_path/'internal-frozen.json')
    assert report['passed'] == report['case_count'] == 10


def test_natural_planner_honors_count_and_preserves_refined_inputs(tmp_path):
    app = create_app('sqlite:///' + str(tmp_path/'TEST_DATA.sqlite'))
    with TestClient(app) as client:
        seed(app, load_cases()['fixtures'])
        response = client.post('/api/v1/agent/query', json={'message':'Сделай план в Уфе 10.07.2098: три события, до 1600 рублей на двоих, с 10:00 до 22:00, переезды до 0 минут', 'offline':True})
        assert response.status_code == 200
        body = response.json()
        assert body['question'] is None and body['plan']['feasible']
        assert len(body['events']) == 3 and body['plan']['total_max'] == 1400
        assert body['tool_calls'] <= 5 and '1400' in body['text']
        assert body['state']['plan_inputs']['event_count'] == 3
        refined = client.post('/api/v1/agent/query', json={'message':'Бюджет 1000 рублей', 'state':body['state'], 'offline':True}).json()
        assert not refined['plan']['feasible'] and refined['plan']['minimal_change'] == {
            'field':'budget', 'value':1400, 'delta':400,
            'evidence_event_ids':['blind-free', 'blind-cheap', 'blind-second'],
            'evidence_source_urls':['https://example.org/TEST_DATA/blind-free', 'https://example.org/TEST_DATA/blind-cheap', 'https://example.org/TEST_DATA/blind-second']}
        assert refined['state']['plan_inputs']['window_start'] == '10:00:00'


def test_natural_planner_asks_one_missing_question_then_continues(tmp_path):
    app = create_app('sqlite:///' + str(tmp_path/'TEST_DATA.sqlite'))
    with TestClient(app) as client:
        seed(app, load_cases()['fixtures'])
        first = client.post('/api/v1/agent/query', json={'message':'Сделай план в Уфе 10.07.2098 до 1600 рублей на двоих, переезды до 0 минут', 'offline':True}).json()
        assert 'окно' in first['question'] and first['events'] == [] and first['plan'] is None
        second = client.post('/api/v1/agent/query', json={'message':'С 10:00 до 22:00', 'state':first['state'], 'offline':True}).json()
        assert second['question'] is None and second['plan']['feasible']


def test_absolute_hours_and_duration_are_separate_constraints():
    filters, _ = extract_filters('В Уфе 10.07.2098 после 18 часов, закончить до 22 часов, не дольше 90 минут', {}, ['Уфа'], date(2026, 11, 13))
    assert (filters.start_after, filters.end_before, filters.duration_max) == ('18:00', '22:00', 90)


def test_local_llm_has_real_zero_api_cost_without_key_or_fake_tariffs(monkeypatch):
    for key in ['LLM_API_KEY', 'LLM_INPUT_PRICE_PER_MILLION', 'LLM_OUTPUT_PRICE_PER_MILLION', 'LLM_PRICE_CURRENCY']:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('LLM_PROVIDER', 'local')
    monkeypatch.setenv('LLM_MODEL', 'TEST_DATA-local-model')
    monkeypatch.setenv('LLM_BASE_URL', 'http://127.0.0.1:18080/v1')
    calls = []
    def reserve(identity, cost, tokens, currency):
        assert cost == 0 and currency == 'RUB' and tokens > 450
        calls.append('reserve')
        return True
    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, **kwargs):
            assert calls == ['reserve'] and kwargs['headers'] == {}
            assert kwargs['json']['chat_template_kwargs']['enable_thinking'] is False
            return httpx.Response(200, request=httpx.Request('POST', url), json={
                'model':'TEST_DATA-served-model', 'choices':[{'message':{'content':SearchFilters(city='Уфа').model_dump_json()}}],
                'usage':{'prompt_tokens':321, 'completion_tokens':22}})
    monkeypatch.setattr(httpx, 'AsyncClient', FakeClient)
    result = asyncio.run(llm_extract('В Уфе', SearchFilters(city='Уфа'), 'a'*64, reserve))
    assert result[1]['actual_api_cost'] == result[1]['estimated_cost'] == 0
    assert result[1]['served_model'] == 'TEST_DATA-served-model'
    assert result[1]['tokens']['prompt_tokens'] == 321


@pytest.mark.parametrize('url', ['http://127.0.0.1.evil.test:18080/v1', 'http://localhost@evil.test/v1', 'https://example.org/v1', 'http://127.0.0.1:18080/v1?leak=yes'])
def test_local_mode_never_routes_to_remote_or_credential_urls(monkeypatch, url):
    monkeypatch.setenv('LLM_PROVIDER', 'local')
    monkeypatch.setenv('LLM_MODEL', 'TEST_DATA-local-model')
    monkeypatch.setenv('LLM_BASE_URL', url)
    def forbidden(*args, **kwargs):
        raise AssertionError('Network or reservation attempted for invalid local URL')
    monkeypatch.setattr(httpx, 'AsyncClient', forbidden)
    assert asyncio.run(llm_extract('Уфа', SearchFilters(city='Уфа'), 'a'*64, forbidden)) is None


def test_bot_serializes_concurrent_turns_and_unsubscribe_invalidates_pending(tmp_path):
    received = []
    async def transport(request):
        body = json.loads(request.content)
        received.append(body)
        await asyncio.sleep(.01)
        filters = {**body.get('state', {}).get('filters', {}), ('city' if len(received) == 1 else 'price_max'):('Уфа' if len(received) == 1 else 0)}
        return httpx.Response(200, json={'state':{'filters':filters}, 'events':[], 'mode':'fallback'})
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport), base_url='http://api') as client:
            store = StateStore(tmp_path/'bot.sqlite')
            service = BotService(client, store)
            await asyncio.gather(service.query(100, 'Уфа'), service.query(100, 'только бесплатные'))
            assert received[1]['state']['filters']['city'] == 'Уфа'
            assert store.get(100)['search']['filters'] == {'city':'Уфа', 'price_max':0}
            state = store.get(100)
            state['agent_pending'] = {'nonce':'TEST_DATA-unsafe-after-unsubscribe'}
            store.put(100, state)
            await service.unsubscribe(100)
            assert 'agent_pending' not in store.get(100)
    asyncio.run(check())


@pytest.mark.parametrize('sort', ['date', 'price'])
def test_more_cursor_covers_catalog_beyond_two_hundred_without_repeats(sort):
    from apps.api.app.agent import validate_events
    from tests.agent.test_agent import catalog_event
    from apps.api.app.agent import make_search_cursor
    events = [catalog_event(f'paging-{number:03}', date_start='2098-07-10T14:00:00+05:00', date_end='2098-07-10T15:00:00+05:00',
                            price_min=None if number % 7 == 0 else 100, price_max=None if number % 7 == 0 else 200+number % 4) for number in range(230)]
    state = {'filters':{'city':'Уфа', 'date_from':'2098-07-10', 'date_to':'2098-07-10', 'sort':sort}}
    seen = set()
    for _ in range(50):
        filters, _ = extract_filters('а ещё', state, ['Уфа'], date(2026, 11, 13))
        page = validate_events(events, filters)[:5]
        ids = {event['id'] for event in page}
        assert not seen.intersection(ids)
        seen.update(ids)
        state = {'filters':filters.model_dump(exclude_none=True), 'last_ids':list(ids),
                 'pagination_cursor':make_search_cursor(page[-1], filters) if page else state.get('pagination_cursor')}
        if not page:
            break
    assert len(seen) == 230
    refined, _ = extract_filters('только бесплатные', state, ['Уфа'], date(2026, 11, 13))
    assert refined.after_key is None and refined.exclude_ids == []


def test_api_roundtrips_stable_cursor_for_all_230_records(tmp_path):
    app = create_app('sqlite:///' + str(tmp_path/'TEST_DATA.sqlite'))
    fixture = load_cases()['fixtures'][0]
    records = [{**fixture, 'id':f'large-catalog-{number:03}'} for number in range(230)]
    with TestClient(app) as client:
        seed(app, records)
        state, seen = {}, set()
        for turn in range(50):
            response = client.post('/api/v1/agent/query', json={'message':'Уфа 10.07.2098' if not turn else 'а ещё', 'state':state, 'offline':True})
            assert response.status_code == 200
            body = response.json()
            ids = {event['id'] for event in body['events']}
            assert not seen.intersection(ids)
            seen.update(ids)
            state = body['state']
            if not ids:
                break
        assert len(seen) == 230 and len(state['filters']['exclude_ids']) <= 200


def test_origin_reply_is_remembered_without_claiming_route_or_changing_title(tmp_path):
    app = create_app('sqlite:///' + str(tmp_path/'TEST_DATA.sqlite'))
    with TestClient(app) as client:
        seed(app, load_cases()['fixtures'])
        first = client.post('/api/v1/agent/query', json={'message':'В Уфе 10.07.2098 поближе к центру', 'offline':True}).json()
        assert first['state']['filters'].get('origin_pending') is True
        second = client.post('/api/v1/agent/query', json={'message':'От улицы Ленина, 10', 'state':first['state'], 'offline':True}).json()
        filters = second['state']['filters']
        assert filters['origin'] == 'улицы Ленина, 10' and not filters['origin_pending']
        assert not filters.get('q') and second['question'] is None
        assert 'не подтверждены' in second['text']
        assert second['events'] and 'ближайшие' not in second['text'].lower()
        from apps.api.app.models import AgentTrace
        with app.state.session_factory() as session:
            trace = session.get(AgentTrace, second['trace_id']).payload
            assert 'Ленина' not in json.dumps(trace, ensure_ascii=False)
