"""Async agent requests must not synchronously wait for the SQLAlchemy pool."""
import asyncio
from datetime import datetime, timedelta, timezone
import time

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import create_engine


@pytest.mark.parametrize('endpoint', ['query', 'plan'])
def test_twenty_same_identity_agent_requests_complete_with_a_small_sqlite_pool(tmp_path, monkeypatch, endpoint):
    from apps.api.app import main

    monkeypatch.setenv('ADMIN_SESSION_SECRET', 'isolated-concurrency-test-session-secret')
    # Permit normal worker scheduling under test-machine load, while keeping
    # the total batch deadline far below the production pool's 30-second wait.
    monkeypatch.setattr(main, 'create_engine', lambda url, **options:
                        create_engine(url, pool_size=2, max_overflow=0, pool_timeout=1, **options))
    app = main.create_app('sqlite:///'+str(tmp_path/'concurrent.db'))
    with TestClient(app):
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                payload = {'message': 'Покажи спектакли в Уфе', 'offline': True, 'state': {'city': 'Уфа'}}
                if endpoint == 'plan':
                    payload = {'city': 'Уфа', 'date': (datetime.now(timezone.utc)+timedelta(days=5)).date().isoformat(),
                               'window_start': '11:00', 'window_end': '20:00', 'budget': 2000}
                started = time.perf_counter()
                responses = await asyncio.wait_for(asyncio.gather(*[
                    client.post('/api/v1/agent/'+endpoint, json=payload) for _ in range(20)]), timeout=6)
                assert [response.status_code for response in responses] == [200]*20
                assert time.perf_counter()-started < 6
                if endpoint == 'query':
                    assert len({response.json()['dialog_id'] for response in responses}) == 1
        asyncio.run(scenario())
