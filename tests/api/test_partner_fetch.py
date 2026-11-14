import json
from urllib.parse import parse_qs, urlsplit

import pytest


def fake_partner(monkeypatch, payload):
    from apps.api.app import security
    monkeypatch.setenv('SOURCE_ALLOWLIST', 'api.timepad.ru,pro.culture.ru,rusdram.ru')
    monkeypatch.setattr(security, 'resolve_public_addresses', lambda host: ['93.184.216.34'])
    requests = []

    class Connection:
        def __init__(self, host, address):
            self.host = host

        def request(self, method, path, headers):
            self.path = path
            requests.append((self.host, path, headers))

        def getresponse(self):
            body = json.dumps(payload(self.path)).encode()
            class Response:
                status = 200

                def getheader(self, name, default=''):
                    return 'application/json' if name == 'Content-Type' else default

                def read(self, limit):
                    return body[:limit]
            return Response()

        def close(self):
            pass
    monkeypatch.setattr(security, 'PinnedHTTPSConnection', Connection)
    return requests


def test_timepad_verification_paginates_skip_and_scopes_bearer(monkeypatch):
    from apps.api.app.security import fetch_source
    def payload(path):
        skip = int(parse_qs(urlsplit(path).query).get('skip', ['0'])[0])
        return {'total': '101', 'values': [{'id': index} for index in range(skip, min(skip + 100, 101))]}
    requests = fake_partner(monkeypatch, payload)
    monkeypatch.setenv('TIMEPAD_API_TOKEN', 'private-timepad-token')
    result = json.loads(fetch_source('https://api.timepad.ru/v1/events?cities=Уфа&limit=100'))
    assert len(result['values']) == 101
    assert len(requests) == 2
    assert all(headers['Authorization'] == 'Bearer private-timepad-token' for _, _, headers in requests)
    assert parse_qs(urlsplit(requests[1][1]).query)['skip'] == ['100']
    assert 'private-timepad-token' not in json.dumps(result)


def test_proculture_private_query_key_never_becomes_source_evidence(monkeypatch):
    from apps.api.app.security import fetch_source
    requests = fake_partner(monkeypatch, lambda path: {'total': 1, 'events': [{'id': 1}]})
    monkeypatch.setenv('PROCULTURE_API_KEY', 'private-proculture-key')
    source_url = 'https://pro.culture.ru/api/2.5/events/?limit=100&status=accepted'
    result = fetch_source(source_url)
    assert json.loads(result)['events'] == [{'id': 1}]
    assert parse_qs(urlsplit(requests[0][1]).query)['apiKey'] == ['private-proculture-key']
    assert 'private-proculture-key' not in result
    assert 'Authorization' not in requests[0][2]


def test_missing_partner_key_and_incomplete_pagination_fail_closed(monkeypatch):
    from apps.api.app.security import fetch_source
    requests = fake_partner(monkeypatch, lambda path: {'total': 9999, 'events': [{'id': 1}]})
    monkeypatch.delenv('PROCULTURE_API_KEY', raising=False)
    with pytest.raises(ValueError, match='ключ'):
        fetch_source('https://pro.culture.ru/api/2.5/events/?limit=100')
    assert requests == []
    monkeypatch.setenv('PROCULTURE_API_KEY', 'private-proculture-key')
    with pytest.raises(ValueError, match='полностью|пагинац|непол'):
        fetch_source('https://pro.culture.ru/api/2.5/events/?limit=100')
