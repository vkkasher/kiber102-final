import json

import pytest

from apps.worker.kassy import discover_sessions, parse_session, public_session_url


URL = 'https://ufa.kassy.ru/events/teatr/2-25325/'
VENUE = 'https://ufa.kassy.ru/venue/gbukii-russkijj-dramaticheskijj-teatr-rb-18/'


def page(availability='InStock', **changes):
    data = {'@type': 'TheaterEvent', 'name': 'Палата бизнес-класса', 'url': URL,
            'startDate': '2026-11-18T19:00:00+05:00', 'endDate': '2026-11-18T21:20:00+05:00',
            'image': 'https://ufa.kassy.ru/media/8b/example.jpg',
            'eventStatus': 'https://schema.org/EventScheduled',
            'location': {'name': 'ГБУКиИ Русский драматический театр РБ, Большой зал', 'url': VENUE},
            'offers': {'priceCurrency': 'RUB', 'price': 300,
                       'availability': 'https://schema.org/' + availability}}
    data.update(changes)
    return '<h1>Палата бизнес-класса</h1><div class="price_line"><p>Купить билеты: 300 — 800 ₽</p></div><script type="application/ld+json">' + json.dumps(data) + '</script>'


def test_session_extracts_its_image_prices_and_explicit_stock():
    item = parse_session(page(), URL)
    assert item['title'] == 'Палата бизнес-класса'
    assert item['date_start'] == '2026-11-18T19:00:00+05:00'
    assert item['availability'] == 'available'
    assert (item['price_min'], item['price_max']) == (300, 800)
    assert item['image_url'].endswith('/media/8b/example.jpg')


@pytest.mark.parametrize('value,expected', [('SoldOut','sold_out'),('OutOfStock','sold_out'),('Discontinued','off_sale'),('Mystery',None)])
def test_stock_is_explicit_and_unknown_stays_unknown(value, expected):
    assert parse_session(page(value), URL)['availability'] == expected


def test_other_session_title_or_venue_cannot_supply_facts():
    assert parse_session(page(url=URL.replace('25325','25326')), URL) is None
    assert parse_session(page(name='Совсем другой спектакль'), URL) is None
    assert parse_session(page(location={'url':'https://evil.example/venue/18/'}), URL) is None


def test_discovery_never_visits_buy_search_or_external_urls():
    html = '<a href="/events/teatr/2-25325/">Палата</a><a href="/events/teatr/2-25325/buy/">Купить</a><a href="https://evil.example/events/teatr/2-9/">x</a>'
    assert discover_sessions(html, VENUE) == [URL]
    assert public_session_url(URL+'buy/') == URL
    assert public_session_url('https://evil.example/events/teatr/2-25325/buy/') is None


def test_single_lowest_price_does_not_invent_a_maximum():
    item = parse_session(page().replace('300 — 800 ₽','от 300 ₽'), URL)
    assert item['price_min'] == 300 and item['price_max'] is None


def test_no_seats_notice_without_offers_means_sold_out():
    item = parse_session(page(offers=None).replace('Купить билеты: 300 — 800 ₽','Свободных мест нет'), URL)
    assert item['availability'] == 'sold_out'
    assert item['price_min'] is None and item['price_max'] is None


def test_image_is_bound_to_the_ticket_site_and_cancelled_is_unavailable():
    item = parse_session(page(image='https://evil.example/image.jpg', eventStatus='https://schema.org/EventCancelled'), URL)
    assert item['image_url'] is None and item['availability'] == 'cancelled'
