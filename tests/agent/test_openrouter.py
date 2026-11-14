"""OpenRouter routing must enforce the tariffs used by the durable budget."""
import asyncio

import httpx
import pytest

from apps.api.app import agent


@pytest.fixture
def provider(monkeypatch):
    for key, value in {
        'LLM_PROVIDER': 'remote', 'LLM_API_KEY': 'TEST_DATA-key',
        'LLM_BASE_URL': 'https://openrouter.ai/api/v1',
        'LLM_MODEL': 'deepseek/deepseek-v4.1-flash-1017',
        'LLM_INPUT_PRICE_PER_MILLION': '0.1', 'LLM_OUTPUT_PRICE_PER_MILLION': '0.5',
        'LLM_PRICE_CURRENCY': 'USD', 'LLM_MAX_CALL_COST': '0.003',
    }.items():
        monkeypatch.setenv(key, value)
    calls = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs['timeout'] == 4.0
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, **kwargs):
            calls.append(kwargs['json'])
            return httpx.Response(200, request=httpx.Request('POST', url), json={
                'model': 'deepseek/deepseek-v4.1-flash-1017',
                'choices': [{'message': {'content': '{"city":"Уфа"}'}}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'cost': 0.000006},
            })
    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    return calls


def test_openrouter_enforces_tariffs_schema_privacy_and_disables_reasoning(provider):
    filters, info = asyncio.run(agent.llm_extract('В Уфе', agent.SearchFilters(), 'a'*64, lambda *args: True))
    body = provider[0]
    assert body['provider'] == {
        'require_parameters': True, 'allow_fallbacks': False, 'data_collection': 'deny',
        'sort': 'latency', 'max_price': {'prompt': 0.1, 'completion': 0.5},
    }
    assert body['reasoning'] == {'effort': 'none'}
    assert body['response_format']['type'] == 'json_schema'
    assert filters.city == 'Уфа'
    assert info['estimated_cost'] == pytest.approx(0.00002)
    assert info['actual_api_cost'] == pytest.approx(0.000006)


def test_exhausted_quota_never_contacts_openrouter(provider):
    assert asyncio.run(agent.llm_extract('В Уфе', agent.SearchFilters(), 'a'*64, lambda *args: False)) is None
    assert provider == []


def test_return_to_city_is_refinement_instead_of_event_title():
    filters, question = agent.extract_filters('Вернёмся в Уфу', {
        'filters': {'city':'Стерлитамак', 'category':'спектакль'}
    }, ['Уфа', 'Стерлитамак'])
    assert question is None
    assert filters.city == 'Уфа' and filters.category == 'спектакль'
    assert filters.q is None


def test_slow_provider_falls_back_within_total_deadline(provider, monkeypatch):
    original = httpx.AsyncClient

    class SlowClient(original):
        async def post(self, *args, **kwargs):
            await asyncio.sleep(4.2)
            return await super().post(*args, **kwargs)

    monkeypatch.setattr(httpx, 'AsyncClient', SlowClient)
    assert asyncio.run(agent.llm_extract('В Уфе', agent.SearchFilters(), 'a'*64, lambda *args: True)) is None
