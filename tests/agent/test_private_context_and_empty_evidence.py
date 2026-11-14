"""Private state and counterfactual grounding at their real HTTP boundaries."""
import asyncio
import json

from fastapi import FastAPI
import httpx
import pytest

from apps.api.app import agent
from tests.agent.test_agent import catalog_event


def test_direct_extraction_with_private_origin_never_contacts_provider(monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'local')
    monkeypatch.setenv('LLM_BASE_URL', 'http://127.0.0.1:18080/v1')
    monkeypatch.setenv('LLM_MODEL', 'TEST_DATA-model')
    monkeypatch.setenv('LLM_MAX_INPUT_TOKENS', '32000')
    reservations = []

    def forbidden(*args, **kwargs):
        raise AssertionError('Private origin reached reservation or provider')

    monkeypatch.setattr(httpx, 'AsyncClient', forbidden)
    filters = agent.SearchFilters(city='Уфа', origin='TEST_DATA PRIVATE ADDRESS', proximity_requested=False)
    def reserve(*args):
        reservations.append(args)
        return True
    assert asyncio.run(agent.llm_extract('Подешевле', filters, 'a'*64, reserve)) is None
    assert reservations == []


def test_safe_provider_context_omits_origin_even_when_empty(monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'local')
    monkeypatch.setenv('LLM_BASE_URL', 'http://127.0.0.1:18080/v1')
    monkeypatch.setenv('LLM_MODEL', 'TEST_DATA-model')
    contexts = []

    class Provider:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, **kwargs):
            context = kwargs['json']['messages'][0]['content'].split('Prior filters: ', 1)[1]
            contexts.append(json.loads(context))
            return httpx.Response(200, request=httpx.Request('POST', url), json={
                'choices':[{'message':{'content':'{"city":"Уфа"}'}}]})

    monkeypatch.setattr(httpx, 'AsyncClient', Provider)
    assert asyncio.run(agent.llm_extract('В Уфе', agent.SearchFilters(city='Уфа'), 'a'*64, lambda *args:True))
    assert 'origin' not in contexts[0]


def catalog_app(events):
    app, traces = FastAPI(), []
    app.include_router(agent.router)
    app.state.persist_agent_trace = traces.append

    @app.get('/api/v1/cities')
    def cities():
        return ['Уфа']

    @app.get('/api/v1/events')
    def inventory():
        return {'items':events, 'total':len(events)}
    return app, traces


def test_client_cannot_clear_proximity_flag_to_send_origin_to_model(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError('Untrusted flag allowed private state into extraction')
    monkeypatch.setattr(agent, 'llm_extract', forbidden)
    app, _ = catalog_app([catalog_event()])

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/api/v1/agent/query', json={
                'message':'В Уфе 14 ноября 2026', 'state':{'filters':{
                    'origin':'TEST_DATA PRIVATE ADDRESS', 'proximity_requested':False}}})
            assert response.status_code == 200 and response.json()['mode'] == 'fallback'
    asyncio.run(scenario())


@pytest.mark.parametrize('constraint,event_changes,claim', [
    ('до 100 рублей', {}, '400 ₽'),
    ('после 18:00', {}, '14:00'),
    ('закончить до 14:30', {}, '15:00'),
    ('не дольше 30 минут', {}, '60 минут'),
    ('с ребёнком 5 лет', {}, '6+'),
    ('до 100 рублей', {'price_min':None, 'price_max':None}, 'нет полной цены'),
    ('закончить до 14:30', {'date_end':None, 'duration_min':None}, 'не опубликованы'),
    ('не дольше 30 минут', {'date_end':None, 'duration_min':None}, 'не опубликована'),
    ('с ребёнком 5 лет', {'age_limit':None}, 'не опубликовано'),
    ('', {'date_start':'2026-11-15T14:00:00+05:00', 'date_end':'2026-11-15T15:00:00+05:00'}, '2026-11-15'),
])
def test_empty_search_numeric_or_missing_data_reason_keeps_actual_basis(constraint, event_changes, claim):
    event = catalog_event('basis', source_url='https://example.org/basis', **event_changes)
    unrelated = catalog_event('unrelated', status='cancelled', source_url='https://example.org/unrelated')
    app, traces = catalog_app([event, unrelated])

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/api/v1/agent/query', json={
                'message':'В Уфе 14 ноября 2026 ' + constraint, 'offline':True})
            assert response.status_code == 200
            body = response.json()
            assert body['events'] == [] and claim in body['text']
            assert traces[-1]['event_ids'] == ['basis']
            assert traces[-1]['source_ids'] == ['https://example.org/basis']
            assert body['tool_calls'] <= 5
    asyncio.run(scenario())


def test_empty_search_chooses_price_witness_and_does_not_fabricate_empty_inventory_basis():
    async def scenario(events, expected_ids, expected_urls):
        app, traces = catalog_app(events)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/api/v1/agent/query', json={
                'message':'В Уфе 14 ноября 2026 до 100 рублей', 'offline':True})
            assert response.status_code == 200 and response.json()['events'] == []
            assert traces[-1]['event_ids'] == expected_ids
            assert traces[-1]['source_ids'] == expected_urls
    events = [catalog_event('expensive', price_max=800, source_url='https://example.org/expensive'),
              catalog_event('cheapest', price_max=400, source_url='https://example.org/cheapest')]
    asyncio.run(scenario(events, ['cheapest'], ['https://example.org/cheapest']))
    asyncio.run(scenario([], [], []))


def test_bot_unknown_prices_explains_limit_and_can_relax_it_without_losing_city(tmp_path, monkeypatch):
    from apps.bot.service import BotService, StateStore, answer_text
    monkeypatch.setenv('TRUSTED_PROXY_SECRET', 'TEST_DATA_' + 'x' * 40)
    async def no_model(*args, **kwargs):
        return None
    monkeypatch.setattr(agent, 'llm_extract', no_model)
    app, traces = catalog_app([catalog_event('unknown-price', price_min=None, price_max=None)])

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            bot = BotService(client, StateStore(tmp_path / 'TEST_DATA-bot.sqlite'))
            first = await bot.query(102, 'В Уфе 14 ноября 2026 до 1500 рублей')
            assert first['events'] == []
            assert 'нет полной цены' in answer_text(first)
            assert '1500 ₽' in answer_text(first)
            assert 'без ограничений по бюджету' in answer_text(first)
            assert traces[-1]['event_ids'] == ['unknown-price']
            second = await bot.query(102, 'Покажи события без ограничений по бюджету')
            assert [event['id'] for event in second['events']] == ['unknown-price']
            assert second['state']['filters']['city'] == 'Уфа'
            assert second['state']['filters'].get('price_max') is None
            assert second['events'][0]['price_min'] is None
            assert second['events'][0]['price_max'] is None
    asyncio.run(scenario())
