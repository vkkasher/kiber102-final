import csv
import io

import pytest

from apps.api.app.services import parse_rows
from tests.api.test_api import auth as auth, client as client, record


@pytest.mark.parametrize('delimiter', [';', '\t'])
def test_excel_csv_delimiters_preserve_quoted_description(delimiter):
    stream = io.StringIO()
    writer = csv.writer(stream, delimiter=delimiter)
    writer.writerow(['title', 'description', 'tags', 'price_min', 'is_free'])
    writer.writerow(['TEST_DATA Сцена', 'Текст, точка; строка\nи продолжение', 'дети|театр', '', ''])
    rows = parse_rows('csv', '\ufeff' + stream.getvalue())
    assert rows == [{'title': 'TEST_DATA Сцена', 'description': 'Текст, точка; строка\nи продолжение',
                     'tags': ['дети', 'театр'], 'price_min': None}]


def test_repeated_csv_headers_cannot_silently_replace_source_fields(client, auth):
    response = client.post('/api/v1/admin/import', headers=auth, json={
        'format': 'csv', 'content': 'title,address, address\nTEST_DATA Зал,Адрес зала,Адрес владельца\n'})
    assert response.status_code == 422
    assert 'address' in response.json()['message']
    assert client.get('/api/v1/admin/events', headers=auth).json()['total'] == 0


@pytest.mark.parametrize('content', ['title,city\nTEST_DATA,Уфа,лишнее\n',
                                    'title,city\n"TEST_DATA,Уфа\n'])
def test_broken_csv_is_a_controlled_validation_error(client, auth, content):
    response = client.post('/api/v1/admin/import', headers=auth, json={'format': 'csv', 'content': content})
    assert response.status_code == 422
    assert client.get('/api/v1/admin/events', headers=auth).json()['total'] == 0


def test_excel_csv_imports_100_events_idempotently_into_review(client, auth):
    rows = [record(title=f'TEST_DATA Сеанс {i}', source_url=f'https://www.culture.ru/events/{i}/test')
            for i in range(100)]
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=rows[0].keys(), delimiter=';')
    writer.writeheader()
    writer.writerows(rows)
    payload = {'format': 'csv', 'content': stream.getvalue()}
    first = client.post('/api/v1/admin/import', headers=auth, json=payload)
    assert first.status_code == 200
    assert first.json()['added'] == 100 and first.json()['rejected'] == 0
    second = client.post('/api/v1/admin/import', headers=auth, json=payload)
    assert second.status_code == 200
    assert second.json()['duplicate'] == 100
    assert client.get('/api/v1/admin/events', headers=auth).json()['total'] == 100
    assert client.get('/api/v1/events').json()['total'] == 0


def test_csv_limit_and_missing_nullable_cells():
    with pytest.raises(ValueError, match='1000'):
        parse_rows('csv', 'title\n' + 'TEST_DATA\n' * 1001)
    assert parse_rows('csv', ' title ,price_min\nTEST_DATA\n') == [{'title': 'TEST_DATA', 'price_min': None}]


@pytest.mark.parametrize('delimiter', [',', ';', '\t'])
def test_csv_escaped_quotes_round_trip_without_changing_event_fields(delimiter):
    stream = io.StringIO()
    writer = csv.writer(stream, delimiter=delimiter)
    writer.writerow(['title', 'city', 'description'])
    writer.writerow(['TEST_DATA "Куклы"', 'Уфа', 'Текст "цитаты" и ещё'])
    assert parse_rows('csv', stream.getvalue()) == [
        {'title': 'TEST_DATA "Куклы"', 'city': 'Уфа', 'description': 'Текст "цитаты" и ещё'}]


@pytest.mark.parametrize('value', ['"Уфа"garbage', '"Уфа""'])
def test_csv_invalid_closing_quotes_are_rejected(value):
    with pytest.raises(ValueError, match='CSV'):
        parse_rows('csv', 'title,city\nTEST_DATA,' + value + '\n')
