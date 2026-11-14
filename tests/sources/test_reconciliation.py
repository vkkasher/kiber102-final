import httpx

from apps.worker.main import reconcile_published


def test_moderator_source_disable_overrides_config():
    from apps.worker.main import source_enabled
    config = {'enabled': True, 'url': 'https://example.org/events'}
    assert source_enabled(config, [])
    assert not source_enabled(config, [{'url': config['url'], 'status': 'disabled'}])
    assert not source_enabled(config, [{'url': config['url'], 'status': 'blocked'}])
    assert not source_enabled(config | {'enabled': False}, [])
    assert not source_enabled({'enabled': True, 'url': 'https://www.example.org/events?month=1'},
                              [{'url': 'https://example.org', 'status': 'disabled'}])


def test_monthly_schedule_does_not_stay_on_archived_november():
    from datetime import datetime
    from apps.worker.main import scheduled_sources
    config = {'sources': [{'id': 'TEST_DATA', 'url_template': 'https://example.org/{month}-{year}/'}]}
    assert scheduled_sources(config, datetime(2027, 1, 1))[0]['url'] == 'https://example.org/yanvar-2027/'


def test_missing_from_successful_source_is_quarantined_not_called_cancelled():
    mutations = []

    def handle(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'items': [
                {'id': 'one', 'status': 'published', 'title': 'TEST_DATA old',
                 'date_start': '2030-01-01T14:00:00Z', 'source_url': 'https://rusdram.ru/afisha.html'}]})
        mutations.append((request.method, request.url.path, request.content.decode()))
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(handle), base_url='http://test') as api:
        result = reconcile_published(api, 'https://rusdram.ru/afisha.html', [])
    assert result == {'refreshed': 0, 'quarantined': 1, 'cancelled': 0}
    assert '"status":"draft"' in mutations[0][2]


def test_unchanged_published_session_rechecks_exact_facts():
    paths = []
    event = {'id': 'one', 'status': 'published', 'title': 'TEST_DATA same',
             'date_start': '2030-01-01T14:00:00Z', 'source_url': 'https://rusdram.ru/afisha.html'}

    def handle(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={'items': [event]} if request.method == 'GET' else {})

    parsed = [{**event, 'date_start': '2030-01-01T19:00:00+05:00', 'status': 'черновик'}]
    with httpx.Client(transport=httpx.MockTransport(handle), base_url='http://test') as api:
        result = reconcile_published(api, event['source_url'], parsed)
    assert result['refreshed'] == 1
    assert paths[-1] == '/api/v1/admin/events/one/verify'
