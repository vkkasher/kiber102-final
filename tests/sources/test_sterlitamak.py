import json
from datetime import datetime


def test_schedule_covers_current_and_next_month_across_year_boundary():
    from apps.worker.main import scheduled_sources
    config = {'sources': [{'id': 'bashopera', 'url_template': 'https://example.org/{month}-{year}/', 'month_offsets': [0, 1]}]}
    rows = scheduled_sources(config, datetime(2026, 12, 10))
    assert [row['url'] for row in rows] == ['https://example.org/dekabr-2026/', 'https://example.org/yanvar-2027/']


def test_sterlitamak_joins_explicit_dated_session_and_named_tariff_without_inventing_seats():
    from apps.worker.sterlitamak import SterlitamakAdapter
    listing = '''<div class="nspArt"><a class="nspImageWrapper" href="/afisha/test-15-11-2026"></a>
    <div class="nspText"><h2><a href="/repertuar/test">TEST_DATA спектакль</a></h2><h5>16+</h5>
    <p>Описание тестового спектакля</p><p>2ч. 30 мин.</p><button data-kassy-event_id="42">Билеты</button></div>
    <p class="nspInfo">15 ноября, ВС 18:00</p></div>
    <footer>453124, РБ, Стерлитамак, ул. Худайбердина, 18</footer>'''
    tariff = '<h3>Спектакль «TEST_DATA спектакль»</h3><ul><li>1 - 4 ряды - 1200 руб.</li><li>5 - 8 ряды - 500 руб.</li></ul>'
    bundle = json.dumps({'format': 'catalog-evidence-v1', 'listing': listing,
                         'documents': {'https://rusdramteatr.ru/bilety': tariff}})
    result = SterlitamakAdapter().parse(bundle, 'https://rusdramteatr.ru/afisha')
    assert len(result) == 1
    event = result[0]
    assert event['date_start'] == '2026-11-15T18:00:00+05:00'
    assert event['duration_min'] == 150
    assert (event['price_min'], event['price_max']) == (500, 1200)
    assert 'Опубликованный тариф спектакля' in event['description']
    assert 'наличие' in event['description']
    assert event['provider_id'] == '/afisha/test-15-11-2026'
    assert event['city'] == 'Стерлитамак' and event['address'].endswith('Худайбердина, 18')
    assert event['org_link'] == 'https://rusdramteatr.ru/afisha/test-15-11-2026'


def test_sterlitamak_rejects_tour_missing_year_and_keeps_unknown_prices():
    from apps.worker.sterlitamak import SterlitamakAdapter
    html = '''<div class="nspArt"><a class="nspImageWrapper" href="/afisha/test-15-11-2026"></a>
    <h2>TEST_DATA спектакль</h2><h5>6+</h5><p>1 ч.</p><p class="nspInfo">15 ноября 12:00</p></div>'''
    parsed = SterlitamakAdapter().parse(html, 'https://rusdramteatr.ru/afisha')
    assert parsed[0]['price_min'] is None and parsed[0]['price_max'] is None
    assert parsed[0]['duration_min'] == 60
    assert SterlitamakAdapter().parse(html.replace('1 ч.', 'Гастроли в г. Мелеуз'), 'https://rusdramteatr.ru/afisha') == []
    assert SterlitamakAdapter().parse(html.replace('-15-11-2026', ''), 'https://rusdramteatr.ru/afisha') == []


def test_tariff_from_single_open_floor_or_from_price_is_not_a_maximum():
    from apps.worker.sterlitamak import tariff_ranges
    html = '<h3>Спектакль «TEST_DATA»</h3><p>от 500 руб.</p>'
    result = tariff_ranges(html)
    assert result['test_data'] == (500, None)


def test_real_tariff_accordion_targets_its_panel_and_duration_accepts_source_punctuation():
    from apps.worker.sterlitamak import duration_minutes, tariff_ranges
    html = '''<div aria-controls="prices"><h3>Спектакль «TEST_DATA»</h3></div>
    <div id="prices"><ul><li>1 - 4 ряды - 1200 руб.</li><li>5 - 8 ряды - 500 руб.</li></ul></div>
    <div aria-controls="other"><h3>Спектакль «OTHER_TEST_DATA»</h3></div>
    <div id="other"><p>Единая цена - 800 руб.</p></div>'''
    assert tariff_ranges(html) == {'test_data': (500, 1200), 'other_test_data': (800, 800)}
    assert duration_minutes('1ч. 30 мин..') == 90
