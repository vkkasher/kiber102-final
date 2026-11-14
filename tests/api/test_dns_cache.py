import pytest

from apps.api.app import security


@pytest.fixture(autouse=True)
def clear_dns_cache(monkeypatch):
    security._resolve_public_addresses_cached.cache_clear()
    monkeypatch.setenv('SOURCE_DNS_MODE', 'system')
    yield
    security._resolve_public_addresses_cached.cache_clear()


def test_positive_dns_cache_is_short_lived_and_returns_isolated_addresses(monkeypatch):
    ticks = [10.0]
    calls = []
    monkeypatch.setattr(security.time, 'monotonic', lambda: ticks[0])
    def resolve(*args, **kwargs):
        calls.append(args[0])
        return [(None, None, None, None, ('93.184.216.34', 443))]
    monkeypatch.setattr(security.socket, 'getaddrinfo', resolve)
    first = security.resolve_public_addresses('cache.example')
    first.append('127.0.0.1')
    assert security.resolve_public_addresses('cache.example') == ['93.184.216.34']
    assert len(calls) == 1
    ticks[0] = 71.0
    assert security.resolve_public_addresses('cache.example') == ['93.184.216.34']
    assert len(calls) == 2


def test_failed_and_nonpublic_dns_results_are_never_cached(monkeypatch):
    results = iter([OSError('TEST_DATA DNS failure'), '127.0.0.1', '93.184.216.34'])
    def resolve(*args, **kwargs):
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return [(None, None, None, None, (result, 443))]
    monkeypatch.setattr(security.socket, 'getaddrinfo', resolve)
    with pytest.raises(OSError):
        security.resolve_public_addresses('failure.example')
    with pytest.raises(ValueError, match='непубличный'):
        security.resolve_public_addresses('failure.example')
    assert security.resolve_public_addresses('failure.example') == ['93.184.216.34']
    assert security._resolve_public_addresses_cached.cache_info().currsize == 1


def test_dns_cache_is_bounded_and_cannot_bypass_url_allowlist(monkeypatch):
    monkeypatch.setattr(security.socket, 'getaddrinfo', lambda *a, **k: [(None, None, None, None, ('93.184.216.34', 443))])
    for index in range(1030):
        security.resolve_public_addresses(f'host-{index}.example')
    assert security._resolve_public_addresses_cached.cache_info().currsize == 1024
    monkeypatch.setenv('SOURCE_ALLOWLIST', 'allowed.example')
    security.resolve_public_addresses('blocked.example')
    with pytest.raises(ValueError, match='разрешённых'):
        security.validate_source_url('https://blocked.example/afisha', resolve=True)


@pytest.mark.parametrize('addresses', [
    ['2606:4700:4700::1111', '93.184.216.34'],
    ['93.184.216.34', '2606:4700:4700::1111'],
])
def test_dual_stack_dns_prefers_validated_ipv4_for_pinned_connections(monkeypatch, addresses):
    monkeypatch.setenv('SOURCE_ALLOWLIST', 'dual-stack.example')
    monkeypatch.setattr(security.socket, 'getaddrinfo', lambda *a, **k:
                        [(None, None, None, None, (address, 443)) for address in addresses])
    assert security.resolve_public_addresses('dual-stack.example') == ['93.184.216.34', '2606:4700:4700::1111']
    parsed, address = security.validate_source_url('https://dual-stack.example/events', resolve=True)
    assert parsed.hostname == 'dual-stack.example' and address == '93.184.216.34'


@pytest.mark.parametrize('private', ['::1', 'fc00::1', '10.0.0.1'])
def test_ipv4_preference_never_discards_a_private_dns_answer(monkeypatch, private):
    monkeypatch.setattr(security.socket, 'getaddrinfo', lambda *a, **k:
                        [(None, None, None, None, (address, 443))
                         for address in ['93.184.216.34', private]])
    with pytest.raises(ValueError, match='непубличный'):
        security.resolve_public_addresses('mixed.example')
    assert security._resolve_public_addresses_cached.cache_info().currsize == 0


def test_ipv6_only_public_sources_remain_supported(monkeypatch):
    monkeypatch.setattr(security.socket, 'getaddrinfo', lambda *a, **k:
                        [(None, None, None, None, ('2606:4700:4700::1111', 443))])
    assert security.resolve_public_addresses('ipv6-only.example') == ['2606:4700:4700::1111']
