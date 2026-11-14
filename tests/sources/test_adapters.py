import json

from apps.worker.sources import BashOperaAdapter, RusdramAdapter, SourceRegistry, parse_price


def test_price_does_not_confuse_unknown_with_free():
    assert parse_price('') == (None, None, False)
    assert parse_price('Вход свободный') == (0, 0, True)
    assert parse_price('от 300 до 800 ₽') == (300, 800, False)
    assert parse_price('от 300 ₽') == (300, None, False)


def test_opera_explicit_year_multiple_sessions_and_unknown_price():
    html = '''<h1><span>Ноябрь 2026</span></h1>
    <div class="item content" id="session-1"><div class="date">14 ноября</div>
    <div class="name"><a href="/repertoire/opera/15866/">Травиата</a></div>
    <ul class="b-tags"><li>16+</li></ul><div class="hall">Большой зал, 18:00</div>
    <div class="buy"><a href="/repertoire/opera/15866/15869/">Билеты</a></div></div>
    <div class="item content" id="session-2"><div class="date">15 ноября</div>
    <div class="name"><a href="/repertoire/opera/15866/">Травиата</a></div>
    <ul class="b-tags"><li>16+</li></ul><div class="hall">Большой зал, 19:00</div></div>'''
    records = BashOperaAdapter().parse(html, 'https://www.bashopera.ru/affiche/noyabr-2026/')
    assert len(records) == 2
    assert records[0]['date_start'] == '2026-11-14T18:00:00+05:00'
    assert records[0]['price_min'] is None
    assert records[0]['age_limit'] == 16
    assert records[0]['source_url'].startswith('https://www.bashopera.ru/affiche/')
    assert records[0]['org_link'].endswith('/15869/')
    assert records[0]['date_start'] != records[1]['date_start']


def test_missing_year_does_not_guess_current_year():
    html = '<h1>Ноябрь</h1><div class="item content"><div class="date">14 ноября</div></div>'
    assert BashOperaAdapter().parse(html, 'https://www.bashopera.ru/affiche/') == []


def test_december_listing_explicit_january_season_session_rolls_forward():
    html = '''<h1>Декабрь 2026</h1><div class="item content"><div class="date">8 января</div>
    <div class="name"><a href="/repertoire/concert/1/">TEST_DATA Новый год</a></div>
    <div class="hall">Большой зал, 18:00</div></div>'''
    row = BashOperaAdapter().parse(html, 'https://www.bashopera.ru/affiche/dekabr-2026/')[0]
    assert row['date_start'] == '2027-01-08T18:00:00+05:00'


def test_session_identity_is_stable_when_date_changes():
    html = '''<h1>Ноябрь 2026</h1><div class="item content" id="session-42">
    <div class="date">14 ноября</div><div class="hall">Большой зал, 18:00</div>
    <div class="name"><a href="/repertoire/opera/1/">TEST_DATA</a></div>
    <div class="buy"><a href="/repertoire/opera/1/42/">Билеты</a></div></div>'''
    a = BashOperaAdapter().parse(html, 'https://www.bashopera.ru/affiche/noyabr-2026/')[0]
    b = BashOperaAdapter().parse(html.replace('14 ноября', '15 ноября'), a['source_url'])[0]
    assert a['provider_id'] == b['provider_id'] == '42'


def test_raw_snapshot_hashes_exact_received_bytes(tmp_path, monkeypatch):
    import hashlib
    import httpx
    from apps.worker import sources
    body = '<html>TEST_DATA\r\nстрока\r\n</html>'.encode('utf8')

    def respond(request):
        return httpx.Response(404) if request.url.path == '/robots.txt' else httpx.Response(
            200, content=body, headers={'content-type': 'text/html; charset=utf-8'})

    monkeypatch.setattr(sources, 'assert_public_url', lambda *args: None)
    monkeypatch.setattr(sources, 'PinnedSourceTransport', lambda: httpx.MockTransport(respond))
    result = sources.fetch_source('https://example.org/events', tmp_path, {'example.org'})
    assert result.sha256 == hashlib.sha256(body).hexdigest()
    assert (tmp_path / f'{result.sha256}.raw').read_bytes() == body


def test_registry_has_partner_disabled_and_real_sources():
    registry = SourceRegistry()
    assert registry.status()['proculture'] == 'waiting_for_partner_key'
    assert 'bashopera' in registry.adapters


def test_pinned_transport_timeout_is_reported_as_retryable_http_error(monkeypatch):
    import httpx
    import pytest
    from urllib.parse import urlsplit
    from apps.api.app import security
    from apps.worker.sources import PinnedSourceTransport
    monkeypatch.setattr(security, 'validate_source_url', lambda url, resolve: (urlsplit(url), '93.184.216.34'))
    closed = []
    class Connection:
        def __init__(self, *args):
            pass
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            raise TimeoutError('TEST_DATA socket timeout')
        def close(self):
            closed.append(True)
    monkeypatch.setattr(security, 'PinnedHTTPSConnection', Connection)
    with pytest.raises(httpx.TimeoutException):
        PinnedSourceTransport().handle_request(httpx.Request('GET', 'https://example.org/robots.txt'))
    assert closed == [True]


def test_transient_robots_timeout_is_retried_without_ignoring_policy(tmp_path, monkeypatch):
    import httpx
    from apps.worker import sources
    calls = []
    def respond(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            raise httpx.ReadTimeout('TEST_DATA transient', request=request)
        return httpx.Response(200, text='User-agent: *\nDisallow: /events')
    monkeypatch.setattr(sources, 'assert_public_url', lambda *args: None)
    monkeypatch.setattr(sources, 'PinnedSourceTransport', lambda: httpx.MockTransport(respond))
    result = sources.fetch_source('https://example.org/events', tmp_path, {'example.org'})
    assert result.status == 'robots_disallowed'
    assert calls == ['/robots.txt', '/robots.txt']


def test_rusdram_rejects_touring_venue_and_keeps_unknown_prices():
    record = {'@type': 'TheaterEvent', 'name': 'TEST_DATA спектакль',
              'startDate': '2026-11-14T18:00:00+05:00', 'typicalAgeRange': '12+',
              'url': 'https://rusdram.ru/test', 'eventStatus': 'https://schema.org/EventScheduled'}
    html = '<script type="application/ld+json">' + json.dumps(record) + '</script>'
    row = '''<article class="show" data-date="2026-11-14"><h3 class="show__title">TEST_DATA спектакль</h3>
    <span class="show__time">18:00</span><p class="show__meta">{place}</p></article>'''
    assert RusdramAdapter().parse(html + row.format(place='На сцене РДК (Иглино)'), 'https://rusdram.ru/afisha.html') == []
    events = RusdramAdapter().parse(html + row.format(place='Большая сцена'), 'https://rusdram.ru/afisha.html')
    assert len(events) == 1
    assert events[0]['price_min'] is None
    assert events[0]['city'] == 'Уфа'
