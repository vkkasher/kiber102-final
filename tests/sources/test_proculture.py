import hashlib
import json

import httpx
import pytest

from apps.worker.proculture import PROCultureAdapter
from apps.api.app.schemas import EventInput


def fixture_event():
    return {'_id': 123, 'name': 'TEST_DATA экспорт PRO', 'status': 'accepted', 'isPublished': True,
            'description': '<p>TEST_DATA описание</p>', 'category': {'sysName': 'koncerty'},
            'ageRestriction': 6, 'price': 500, 'isFree': False,
            'places': [{'name': 'TEST_DATA зал', 'address': {'region': {'name': 'Башкортостан'},
                       'city': {'name': 'Уфа'}, 'street': {'type': 'ул', 'name': 'Тестовая'},
                       'house': {'type': 'д', 'name': '1'}}}],
            'seances': [{'start': 4070908800000, 'end': 4070912400000, 'placeIndex': 0},
                        {'start': 4070995200000, 'end': 4070998800000, 'placeIndex': 0}]}


def test_partner_sessions_keep_utc_milliseconds_unknown_upper_price_and_no_fake_session_id():
    result = PROCultureAdapter().parse(json.dumps({'events': [fixture_event()]}), 'https://pro.culture.ru/api/2.5/events/')
    assert len(result) == 2
    assert result[0]['date_start'] == '2099-01-01T00:00:00+00:00'
    assert result[1]['date_start'] != result[0]['date_start']
    assert result[0]['provider_id'] is None
    assert result[0]['price_min'] == 500 and result[0]['price_max'] is None
    assert result[0]['description'] == 'TEST_DATA описание'
    assert EventInput.model_validate(result[0]).city == 'Уфа'


def test_partner_regions_unpublished_and_broken_dates_do_not_leak_to_regional_queue():
    event = fixture_event()
    event['places'][0]['address']['region']['name'] = 'Амурская область'
    assert PROCultureAdapter().parse(json.dumps({'events': [event]}), 'https://pro.culture.ru/api/2.5/events/') == []
    assert PROCultureAdapter().parse(json.dumps({'events': [fixture_event() | {'isPublished': False}]}), 'https://pro.culture.ru/api/2.5/events/') == []


def test_partner_key_is_only_on_exact_api_request_never_saved(tmp_path, monkeypatch):
    from apps.worker import sources
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(404) if request.url.path == '/robots.txt' else httpx.Response(
            200, content=b'{"events":[],"total":0}', headers={'content-type': 'application/json'})
    monkeypatch.setattr(sources, 'assert_public_url', lambda *args: None)
    monkeypatch.setattr(sources, 'PinnedSourceTransport', lambda: httpx.MockTransport(handle))
    result = sources.fetch_source('https://pro.culture.ru/api/2.5/events/', tmp_path, {'pro.culture.ru'}, api_key='TEST_DATA_SECRET')
    assert 'apiKey' not in str(calls[0].url)
    assert calls[1].url.params['apiKey'] == 'TEST_DATA_SECRET'
    assert all('TEST_DATA_SECRET' not in p.read_text(encoding='utf8') for p in tmp_path.iterdir())
    assert result.sha256 == hashlib.sha256(b'{"events":[],"total":0}').hexdigest()


def test_timepad_token_never_follows_a_misconfigured_source_host(tmp_path, monkeypatch):
    from apps.worker import sources
    monkeypatch.setattr(sources, 'assert_public_url', lambda *args: None)
    with pytest.raises(ValueError, match='api.timepad.ru'):
        sources.fetch_source('https://pro.culture.ru/api/2.5/events/', tmp_path, {'pro.culture.ru'}, token='TEST_DATA_SECRET')
