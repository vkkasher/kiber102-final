import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def quota(tmp_path, monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'local')
    monkeypatch.setenv('LLM_BASE_URL', 'http://127.0.0.1:18080/v1')
    monkeypatch.setenv('LLM_DAILY_MAX_COST', '0')
    monkeypatch.setenv('LLM_DAILY_REQUEST_LIMIT', '2')
    monkeypatch.setenv('LLM_DAILY_TOKEN_LIMIT', '1000')
    from apps.api.app.main import create_app
    with TestClient(create_app(f'sqlite:///{tmp_path / "local.db"}')) as client:
        yield client.app.state.reserve_llm_usage


def test_local_zero_cost_still_enforces_request_and_token_caps(quota):
    assert quota('a' * 64, 0, 400, 'RUB')
    assert quota('a' * 64, 0, 400, 'RUB')
    assert not quota('a' * 64, 0, 1, 'RUB')
    assert quota('b' * 64, 0, 800, 'RUB')
    assert not quota('b' * 64, 0, 300, 'RUB')
    assert not quota('c' * 64, 0, 100, 'USD')


@pytest.mark.parametrize('url', [
    'https://api.example.org/v1', 'http://localhost.example.org/v1',
    'http://user@localhost/v1', 'http://127.0.0.1/v1?redirect=example.org',
    'http://127.0.0.1/v1#x', 'file:///v1', 'http://127.0.0.1:bad/v1',
    'http://127.0.0.1/remote-proxy',
])
def test_zero_cost_rejects_nonlocal_or_ambiguous_endpoint(quota, monkeypatch, url):
    monkeypatch.setenv('LLM_BASE_URL', url)
    assert not quota('a' * 64, 0, 100, 'RUB')


def test_zero_cost_needs_explicit_local_provider(quota, monkeypatch):
    monkeypatch.delenv('LLM_PROVIDER')
    assert not quota('a' * 64, 0, 100, 'RUB')


@pytest.mark.parametrize('url', ['http://localhost:18080/v1/', 'http://[::1]:18080/v1'])
def test_exact_loopback_hosts_are_supported(quota, monkeypatch, url):
    monkeypatch.setenv('LLM_BASE_URL', url)
    assert quota('a' * 64, 0, 100, 'RUB')


def test_switch_from_zero_cost_local_to_usd_preserves_request_limits(quota, monkeypatch):
    assert quota('a' * 64, 0, 100, 'RUB')
    monkeypatch.setenv('LLM_PROVIDER', 'remote')
    monkeypatch.setenv('LLM_DAILY_MAX_COST', '0.03')
    assert quota('a' * 64, 0.001, 100, 'USD')
    assert not quota('a' * 64, 0.001, 100, 'USD')


def test_currency_switch_never_erases_existing_monetary_reservation(quota, monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'remote')
    monkeypatch.setenv('LLM_DAILY_MAX_COST', '0.03')
    assert quota('a' * 64, 0.001, 100, 'RUB')
    assert not quota('a' * 64, 0.001, 100, 'USD')
