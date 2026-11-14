import httpx

from apps.api.app.routing import OSRMTravelProvider


def test_routing_uses_coordinates_evidence_rounds_up_and_caches():
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={'code': 'Ok', 'routes': [{'duration': 61}]})
    provider = OSRMTravelProvider('https://routing.example', transport=httpx.MockTransport(handle))
    a, b = {'lat': 54.7, 'lon': 55.9}, {'lat': 54.8, 'lon': 56.0}
    result = provider(a, b)
    assert result['minutes'] == 2 and result['checked_at']
    assert '/55.900000,54.700000;56.000000,54.800000' in result['source']
    assert provider(a, b) == result and len(requests) == 1


def test_missing_coordinates_and_network_failures_are_unknown():
    def fail(request):
        raise httpx.ConnectError('offline')
    provider = OSRMTravelProvider('https://routing.example', transport=httpx.MockTransport(fail))
    assert provider({}, {'lat': 54, 'lon': 55}) is None
    assert provider({'lat': 54, 'lon': 55}, {'lat': 54.1, 'lon': 55}) is None
