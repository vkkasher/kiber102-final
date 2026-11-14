import json
from types import SimpleNamespace

from apps.worker.pagination import collect_pages


def test_full_api_pages_are_combined_with_each_raw_hash():
    def page(url, values):
        return SimpleNamespace(url=url, sha256='TEST_DATA_HASH', body=json.dumps({'events': values, 'total': 3}))
    first = page('https://example.org/events?limit=2', [1, 2])
    calls = []
    def fetch(url):
        calls.append(url)
        return page(url, [3])
    data, hashes, complete = collect_pages(first.url, first, fetch, lambda result: result.body)
    assert data['events'] == [1, 2, 3] and complete
    assert 'offset=2' in calls[0] and len(hashes) == 2


def test_partial_api_failure_keeps_rows_but_never_claims_full_snapshot():
    first = SimpleNamespace(url='https://example.org/events', sha256='TEST_DATA', body=json.dumps({'values': [1], 'total': 3}))
    failed = SimpleNamespace(body=None)
    data, hashes, complete = collect_pages(first.url, first, lambda url: failed, lambda result: result.body)
    assert data['values'] == [1] and len(hashes) == 1 and not complete


def test_timepad_uses_documented_skip_and_string_count():
    first = SimpleNamespace(url='https://api.timepad.ru/v1/events?limit=1', sha256='TEST_DATA',
                            body=json.dumps({'values': [1], 'total': '2'}))
    def fetch(url):
        assert 'skip=1' in url and 'offset=' not in url
        return SimpleNamespace(url=url, sha256='TEST_DATA2', body=json.dumps({'values': [2], 'total': '2'}))
    data, _, complete = collect_pages(first.url, first, fetch, lambda result: result.body)
    assert complete and data['values'] == [1, 2]
