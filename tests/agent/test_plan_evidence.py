"""An infeasible plan must retain the actual witness, without recommending it."""
import asyncio

from fastapi import FastAPI
import httpx
import pytest

from apps.api.app.agent import PlanRequest, make_plan, router
from tests.agent.test_agent import catalog_event


def witness_events():
    return [catalog_event('a', price_max=600, source_url='https://example.org/a'),
            catalog_event('b', price_max=1000, source_url='https://example.org/b',
                          date_start='2026-11-14T18:00:00+05:00', date_end='2026-11-14T19:00:00+05:00')]


@pytest.mark.parametrize('field,changes,value,ids,urls', [
    ('budget', {'budget':1000}, 6400, ['a', 'b'], ['https://example.org/a', 'https://example.org/b']),
    ('max_travel_minutes', {'max_travel_minutes':20}, 30, ['a', 'b'], ['https://example.org/a', 'https://example.org/b']),
    ('window', {'window_end':'18:30'}, None, ['a', 'b'], ['https://example.org/a', 'https://example.org/b']),
    ('event_count', {'event_count':3}, 2, ['a', 'b'], ['https://example.org/a', 'https://example.org/b']),
])
def test_minimal_change_carries_only_its_actual_witness(field, changes, value, ids, urls):
    data = dict(city='Уфа', date='2026-11-14', window_start='12:00', window_end='20:00',
                budget=6400, adults=2, children_ages=[6, 10], event_count=2)
    data.update(changes)
    events = witness_events()
    provider = None
    if field == 'max_travel_minutes':
        events[1].update(venue='TEST_DATA Другой зал', address='TEST_DATA Другой адрес')
        def provider(a, b):
            return {'minutes':30, 'source':'TEST_DATA route', 'checked_at':'2026-11-13T10:00:00+05:00'}
    # This record must never appear as evidence merely because it was fetched.
    events.append(catalog_event('cancelled', status='cancelled', source_url='https://example.org/unrelated'))
    result = make_plan(PlanRequest(**data), events, provider)
    assert result['events'] == [] and not result['feasible']
    change = result['minimal_change']
    assert change['field'] == field
    if value is not None:
        assert change['value'] == value
    else:
        assert change['extension_minutes'] == 30
    assert change['evidence_event_ids'] == ids
    assert change['evidence_source_urls'] == urls


def test_unknown_route_does_not_invent_a_minimal_change_witness():
    events = witness_events()
    events[1].update(venue='TEST_DATA Другой зал', address='TEST_DATA Другой адрес')
    result = make_plan(PlanRequest(city='Уфа', date='2026-11-14', window_start='12:00', window_end='20:00', budget=1000), events)
    assert result['minimal_change'] is None and result['events'] == []


def test_negative_budget_query_trace_keeps_basis_ids_but_no_recommendations():
    app, traces = FastAPI(), []
    app.include_router(router)
    app.state.persist_agent_trace = traces.append

    @app.get('/api/v1/cities')
    def cities():
        return ['Уфа']

    @app.get('/api/v1/events')
    def events():
        return {'items':witness_events(), 'total':2}

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/api/v1/agent/query', json={
                'message':'Сделай план в Уфе 14 ноября 2026: два события, два взрослых и дети 6 и 10 лет, бюджет 1000 рублей на всех, с 12:00 до 20:00, переезды до 20 минут',
                'offline':True})
            assert response.status_code == 200
            body = response.json()
            assert not body['plan']['feasible'] and body['events'] == []
            assert body['plan']['minimal_change']['value'] == 6400
            assert traces[-1]['validation'] == 'infeasible_verified'
            assert traces[-1]['event_ids'] == ['a', 'b']
            assert traces[-1]['source_ids'] == ['https://example.org/a', 'https://example.org/b']
    asyncio.run(scenario())
