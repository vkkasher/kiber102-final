import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from tests.api.test_finish import auth as auth, client as client, publish
from tests.api.test_api import record, upload
from tests.sources.test_kassy import URL, page as source_page

START = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=5)


def page(availability='InStock'):
    local = timezone(timedelta(hours=5))
    return source_page(availability, startDate=START.astimezone(local).isoformat(),
                       endDate=(START+timedelta(hours=2)).astimezone(local).isoformat())


def snapshot(content):
    return {'source_url':URL, 'content':content, 'sha256':hashlib.sha256(content.encode()).hexdigest()}


def test_ticket_snapshot_enriches_only_exact_session_and_hides_sold_out(client, auth):
    item = record(title='Палата бизнес-класса', venue='Русский драматический театр РБ',
                  date_start=START.isoformat(), price_min=None, price_max=None)
    event_id = publish(client, auth, item)
    other = publish(client, auth, item | {'date_start':(START+timedelta(days=1)).isoformat()})
    endpoint = '/api/v1/admin/ticket-evidence'
    assert client.post(endpoint,json=snapshot(page())).status_code == 401
    response = client.post(endpoint,headers=auth,json=snapshot(page()))
    assert response.status_code == 200 and response.json()['matched'] == 1
    event=client.get('/api/v1/events/'+event_id).json()
    assert event['image_url'].endswith('/media/8b/example.jpg')
    assert event['ticket_availability'] == 'available'
    assert (event['price_min'],event['price_max']) == (300,800)
    assert event_id in {row['id'] for row in client.get('/api/v1/events',params={'price_max':400}).json()['items']}
    assert client.get('/api/v1/events/'+other).json()['image_url'] is None
    assert client.post(endpoint,headers=auth,json=snapshot(page('SoldOut'))).status_code == 200
    assert event_id not in {row['id'] for row in client.get('/api/v1/events').json()['items']}
    assert client.get('/api/v1/events/'+event_id).json()['ticket_availability'] == 'sold_out'
    # An unknown result or a source outage cannot reopen confirmed sold-out sales.
    client.post(endpoint,headers=auth,json=snapshot(page('Mystery')))
    assert client.get('/api/v1/events/'+event_id).json()['ticket_availability'] == 'sold_out'
    client.post(endpoint,headers=auth,json=snapshot(page('InStock')))
    assert event_id in {row['id'] for row in client.get('/api/v1/events').json()['items']}


def test_ticket_snapshot_validates_hash_and_preserves_moderation(client, auth):
    item=record(title='Палата бизнес-класса',venue='Русский драматический театр РБ', date_start=START.isoformat())
    event_id=publish(client,auth,item)
    assert client.post('/api/v1/admin/ticket-evidence',headers=auth,json=snapshot(page())|{'sha256':'0'*64}).status_code == 422
    client.patch('/api/v1/admin/events/'+event_id,headers=auth,json={'status':'cancelled'})
    client.post('/api/v1/admin/ticket-evidence',headers=auth,json=snapshot(page()))
    assert client.get('/api/v1/events/'+event_id).json()['status'] == 'cancelled'


@pytest.mark.parametrize('change', [
    {'title': 'Новый спектакль'}, {'city': 'Стерлитамак'}, {'venue': 'Другой зал'},
    {'date_start': (START + timedelta(days=2)).isoformat()},
])
@pytest.mark.parametrize('method', ['patch', 'import'])
def test_identity_change_removes_active_ticket_facts_but_retains_raw_history(client, auth, change, method):
    from sqlalchemy import select
    from apps.api.app.models import RawSourceRecord

    item = record(title='Палата бизнес-класса', venue='Русский драматический театр РБ',
                  date_start=START.isoformat(), provider_id='stable-session')
    event_id = publish(client, auth, item)
    body = snapshot(page())
    assert client.post('/api/v1/admin/ticket-evidence', headers=auth, json=body).json()['matched'] == 1
    if method == 'patch':
        response = client.patch('/api/v1/admin/events/'+event_id, headers=auth, json=change)
        assert response.status_code == 200
    else:
        assert upload(client, auth, [item | change]).json()['updated'] == 1
    event = client.get('/api/v1/admin/events', headers=auth).json()['items'][0]
    assert event['ticket_availability'] is None
    assert event['ticket_checked_at'] is None
    assert event['price_min'] == item['price_min']
    assert event['date_end'] is None
    assert event['ticket_links'] == []
    with client.app.state.session_factory() as session:
        raw = session.scalar(select(RawSourceRecord).where(
            RawSourceRecord.event_id == event_id, RawSourceRecord.source_hash == body['sha256']))
        assert raw is not None and raw.payload == body['content']


def test_paid_ticket_evidence_overrides_old_free_claim(client, auth):
    from apps.api.app.agent import confirmed_price
    from apps.bot.service import event_card

    event_id = publish(client, auth, record(title='Палата бизнес-класса', venue='Русский драматический театр РБ',
                       date_start=START.isoformat(), is_free=True, price_min=0, price_max=0))
    client.post('/api/v1/admin/ticket-evidence', headers=auth, json=snapshot(page()))
    event = client.get('/api/v1/events/'+event_id).json()
    assert event['is_free'] is False
    assert confirmed_price(event) == (300, 800)
    assert 'Бесплатно' not in event_card(event)


@pytest.mark.parametrize('base_end', [None, START + timedelta(hours=4)])
def test_public_catalog_uses_fresh_ticket_end_for_ongoing_and_finished_sessions(client, auth, monkeypatch, base_end):
    import apps.api.app.routes as routes

    event_id = publish(client, auth, record(title='Палата бизнес-класса', venue='Русский драматический театр РБ',
                       date_start=START.isoformat(), date_end=base_end.isoformat() if base_end else None))
    client.post('/api/v1/admin/ticket-evidence', headers=auth, json=snapshot(page()))
    monkeypatch.setattr(routes, 'now', lambda: START + timedelta(hours=1))
    assert event_id in {event['id'] for event in client.get('/api/v1/events').json()['items']}
    monkeypatch.setattr(routes, 'now', lambda: START + timedelta(hours=3))
    assert event_id not in {event['id'] for event in client.get('/api/v1/events').json()['items']}


def test_expired_ticket_end_does_not_override_current_base_end(client, auth, monkeypatch):
    import apps.api.app.routes as routes
    from apps.api.app.models import EventTicketEvidence

    event_id = publish(client, auth, record(title='Палата бизнес-класса', venue='Русский драматический театр РБ',
                       date_start=START.isoformat(), date_end=(START+timedelta(hours=4)).isoformat()))
    client.post('/api/v1/admin/ticket-evidence', headers=auth, json=snapshot(page()))
    with client.app.state.session_factory() as session:
        session.get(EventTicketEvidence, event_id).checked_at = datetime.now(timezone.utc) - timedelta(days=4)
        session.commit()
    monkeypatch.setattr(routes, 'now', lambda: START + timedelta(hours=3))
    assert event_id in {event['id'] for event in client.get('/api/v1/events').json()['items']}


def test_agent_trace_records_ticket_source_hash_and_observation_time(client, auth):
    event_id = publish(client, auth, record(title='Палата бизнес-класса', venue='Русский драматический театр РБ',
                       date_start=START.isoformat()))
    body = snapshot(page())
    client.post('/api/v1/admin/ticket-evidence', headers=auth, json=body)
    result = client.post('/api/v1/agent/query', json={
        'message': 'Покажи спектакли в Уфе', 'offline': True, 'state': {'city': 'Уфа'}}).json()
    assert event_id in {event['id'] for event in result['events']}
    trace = next(trace for trace in client.get('/api/v1/admin/traces', headers=auth).json()
                 if trace['trace_id'] == result['trace_id'])
    assert URL in trace['source_ids']
    evidence = next(item for item in trace['ticket_evidence'] if item['event_id'] == event_id)
    assert evidence['source_hash'] == body['sha256']
    assert evidence['source_url'] == URL
    assert datetime.fromisoformat(evidence['checked_at']).tzinfo is not None


@pytest.mark.parametrize('method', ['patch', 'import'])
def test_equivalent_timezone_timestamp_keeps_ticket_evidence(client, auth, method):
    item = record(title='Палата бизнес-класса', venue='Русский драматический театр РБ',
                  date_start=START.isoformat(), provider_id='same-instant-session')
    event_id = publish(client, auth, item)
    client.post('/api/v1/admin/ticket-evidence', headers=auth, json=snapshot(page()))
    equivalent = START.astimezone(timezone(timedelta(hours=5))).isoformat()
    if method == 'patch':
        response = client.patch('/api/v1/admin/events/'+event_id, headers=auth, json={'date_start': equivalent})
        assert response.status_code == 200
    else:
        assert upload(client, auth, [item | {'date_start': equivalent}]).json()['updated'] == 1
    event = client.get('/api/v1/admin/events', headers=auth).json()['items'][0]
    assert event['ticket_availability'] == 'available'
    assert event['price_min'] == 300 and event['price_max'] == 800


def test_domain_identity_comparison_accepts_non_utc_database_timestamp(client, auth):
    from apps.api.app.models import Event
    from apps.api.app.services import invalidate_ticket_identity

    event_id = publish(client, auth, record(title='Палата бизнес-класса', venue='Русский драматический театр РБ',
                       date_start=START.isoformat()))
    client.post('/api/v1/admin/ticket-evidence', headers=auth, json=snapshot(page()))
    with client.app.state.session_factory() as session:
        event = session.get(Event, event_id)
        # PostgreSQL can return aware timestamps in its configured session zone.
        event.date_start = START.astimezone(timezone(timedelta(hours=5)))
        evidence = event.ticket_evidence
        invalidate_ticket_identity(session, event, {'date_start': START})
        assert event.ticket_evidence is evidence


@pytest.mark.parametrize('budget,feasible', [(2000, True), (100, False)])
def test_standalone_plan_trace_records_ticket_provenance_for_actual_basis(client, auth, budget, feasible):
    local_start = START.astimezone(timezone(timedelta(hours=5))).replace(hour=12)
    ids, hashes = [], set()
    for offset in [0, 3]:
        start = local_start + timedelta(hours=offset)
        event_id = publish(client, auth, record(title='Палата бизнес-класса', venue='Русский драматический театр РБ',
                           date_start=start.isoformat(), price_min=None, price_max=None))
        body = snapshot(source_page(startDate=start.isoformat(), endDate=(start+timedelta(hours=2)).isoformat()))
        assert client.post('/api/v1/admin/ticket-evidence', headers=auth, json=body).json()['matched'] == 1
        ids.append(event_id)
        hashes.add(body['sha256'])
    response = client.post('/api/v1/agent/plan', headers={'X-Request-ID': 'standalone-plan-provenance'}, json={
        'city': 'Уфа', 'date': local_start.date().isoformat(), 'window_start': '11:00',
        'window_end': '20:00', 'budget': budget, 'event_count': 2})
    assert response.status_code == 200 and response.json()['feasible'] is feasible
    trace = next((trace for trace in client.get('/api/v1/admin/traces', headers=auth).json()
                  if trace['request_id'] == 'standalone-plan-provenance'), None)
    assert trace is not None
    assert set(trace['event_ids']) == set(ids)
    assert URL in trace['source_ids']
    assert {item['source_hash'] for item in trace['ticket_evidence']} == hashes
    assert all(datetime.fromisoformat(item['checked_at']).tzinfo for item in trace['ticket_evidence'])


@pytest.mark.parametrize('low,high,is_free', [(0, None, False), (None, None, False), (0, 0, True), (0, 800, False)])
def test_current_ticket_prices_require_both_zero_bounds_before_claiming_free(client, auth, low, high, is_free):
    from apps.api.app.agent import confirmed_price

    event_id = publish(client, auth, record(title='Палата бизнес-класса', venue='Русский драматический театр РБ',
                       date_start=START.isoformat(), is_free=True, price_min=0, price_max=0))
    offers = {'priceCurrency': 'RUB', 'lowPrice': low, 'highPrice': high,
              'availability': 'https://schema.org/InStock'}
    content = source_page(startDate=START.isoformat(), offers=offers).replace('300 — 800 ₽', 'Тариф уточняется')
    client.post('/api/v1/admin/ticket-evidence', headers=auth, json=snapshot(content))
    event = client.get('/api/v1/events/'+event_id).json()
    assert event['is_free'] is is_free
    if high is None:
        assert confirmed_price(event) is None


@pytest.mark.parametrize('endpoint', ['query', 'plan'])
def test_repeated_client_correlation_id_never_reuses_trace_primary_key(client, auth, endpoint):
    payload = {'message': 'Покажи спектакли в Уфе', 'offline': True, 'state': {'city': 'Уфа'}}
    if endpoint == 'plan':
        payload = {'city': 'Уфа', 'date': START.date().isoformat(), 'window_start': '11:00',
                   'window_end': '20:00', 'budget': 2000}
    responses = [client.post('/api/v1/agent/'+endpoint, headers={'X-Request-ID': 'repeated-correlation-id'},
                             json=payload) for _ in range(2)]
    assert [response.status_code for response in responses] == [200, 200]
    traces = [trace for trace in client.get('/api/v1/admin/traces', headers=auth).json()
              if trace['request_id'] == 'repeated-correlation-id']
    assert len(traces) == 2 and len({trace['trace_id'] for trace in traces}) == 2
    assert all(trace['trace_id'] != trace['request_id'] for trace in traces)
    if endpoint == 'query':
        assert {response.json()['trace_id'] for response in responses} == {trace['trace_id'] for trace in traces}


def test_standalone_plan_trace_keeps_validated_constraints_without_location_extras(client, auth):
    constraints = {'city': 'Уфа', 'date': START.date().isoformat(), 'window_start': '11:00:00',
                   'window_end': '20:00:00', 'budget': 2000.0, 'adults': 2, 'children_ages': [8],
                   'max_travel_minutes': 25, 'interests': ['спектакль'], 'event_count': 2}
    response = client.post('/api/v1/agent/plan', headers={'X-Request-ID': 'plan-constraints'}, json={
        **constraints, 'address': 'PRIVATE_TEST_ADDRESS', 'origin': 'PRIVATE_TEST_ORIGIN', 'lat': 54.7, 'lon': 55.9})
    assert response.status_code == 200
    trace = next(trace for trace in client.get('/api/v1/admin/traces', headers=auth).json()
                 if trace['request_id'] == 'plan-constraints')
    assert trace['constraints'] == constraints
    assert 'PRIVATE_TEST' not in str(trace)
    assert not {'address', 'origin', 'lat', 'lon'} & trace['constraints'].keys()
