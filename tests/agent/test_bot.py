import asyncio
import json

import httpx


def test_bot_subscription_requires_one_use_confirmation_and_deletes_owned_token(tmp_path):
    from apps.bot.service import BotService, StateStore
    writes = []
    def transport(request):
        if request.method == "POST" and request.url.path.endswith("subscriptions"):
            writes.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "subscription-test", "delete_token": "TEST_DATA-secret"})
        if request.method == "DELETE":
            assert request.headers["Authorization"] == "Bearer TEST_DATA-secret"
            writes.append("delete")
            return httpx.Response(204)
        return httpx.Response(200, json=[])
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport), base_url="http://api") as client:
            service = BotService(client, StateStore(tmp_path / "bot.sqlite"))
            prompt = service.prepare_subscription(10, "Уфа", "Театр")
            assert writes == []
            assert await service.confirm_subscription(11, prompt["nonce"]) is False
            assert await service.confirm_subscription(10, prompt["nonce"]) is True
            assert await service.confirm_subscription(10, prompt["nonce"]) is False
            assert writes == [{"city":"Уфа", "category":"Театр"}]
            assert await service.unsubscribe(10) == 1
            assert writes[-1] == "delete"
            assert await service.unsubscribe(10) == 0
    asyncio.run(scenario())


def test_bot_card_escapes_html_and_refuses_unsafe_source_url():
    from apps.bot.service import event_card, safe_public_url
    text = event_card({"id":"1", "title":"<script>TEST_DATA</script>", "description":"<b>not instructions</b>",
                       "price_min":None, "price_max":None, "source_url":"javascript:alert(1)",
                       "status":"cancelled", "date_start":"2026-11-14T14:00:00+05:00"})
    assert "<script>" not in text and "&lt;script&gt;" in text
    assert "отменено" in text.lower() and "уточня" in text.lower()
    assert safe_public_url("javascript:alert(1)") is None
    assert safe_public_url("https://example.org/event") == "https://example.org/event"


def test_bot_no_token_exits_without_connecting(monkeypatch):
    from apps.bot import main
    monkeypatch.setattr(main, 'load_dotenv', lambda *args, **kwargs: False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    assert asyncio.run(main.run()) == 0


def test_bot_no_token_standby_mode(monkeypatch):
    from apps.bot import main
    monkeypatch.setattr(main, 'load_dotenv', lambda *args, **kwargs: False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setenv("BOT_STANDBY", "true")

    async def run_standby():
        task = asyncio.create_task(main.run())
        await asyncio.sleep(0.05)
        assert not task.done()
        task.cancel()
        assert await task == 0

    asyncio.run(run_standby())


def test_bot_keeps_unsubscribe_capability_when_search_memory_expires(tmp_path):
    import time
    from apps.bot.service import StateStore
    store = StateStore(tmp_path/'bot.sqlite')
    store.put(10, {'search': {'filters': {'city': 'Уфа'}},
                   'subscriptions': [{'id': 'TEST_DATA-sub', 'delete_token': 'TEST_DATA-token'}]})
    with store.connection() as db:
        db.execute('UPDATE bot_state SET updated=?', (time.time()-31*86400,))
    remembered = store.get(10)
    assert remembered['subscriptions'][0]['delete_token'] == 'TEST_DATA-token'
    assert 'search' not in remembered


def test_bot_subscription_retry_reuses_idempotency_key_after_ambiguous_timeout(tmp_path):
    from apps.bot.service import BotService, StateStore
    keys = []
    def transport(request):
        keys.append(request.headers.get("Idempotency-Key"))
        if len(keys) == 1:
            raise httpx.ReadTimeout("TEST_DATA ambiguous response")
        return httpx.Response(201, json={"id":"sub-id", "delete_token":"TEST_DATA-token"})
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport), base_url="http://api") as client:
            service = BotService(client, StateStore(tmp_path/"bot.sqlite"))
            pending = service.prepare_subscription(10, "Уфа")
            import pytest
            with pytest.raises(httpx.ReadTimeout):
                await service.confirm_subscription(10, pending["nonce"])
            assert await service.confirm_subscription(10, pending["nonce"]) is True
            assert keys == [pending["nonce"], pending["nonce"]]
    asyncio.run(scenario())


def test_bot_persists_five_plus_redacted_turns_and_signs_each_user(tmp_path, monkeypatch):
    from apps.bot.service import BotService, StateStore
    monkeypatch.setenv('TRUSTED_PROXY_SECRET', 'TEST_DATA_'+'x'*40)
    sent = []
    def transport(request):
        sent.append(request.headers['X-Client-Identity'])
        payload = json.loads(request.content)
        turns = payload.get('state', {}).get('turns', [])
        turns.extend([{'role':'user', 'content':payload['message']}, {'role':'assistant', 'content':'TEST_DATA answer'}])
        return httpx.Response(200, json={'state':{'filters':{'city':'Уфа'}, 'turns':turns}, 'events':[], 'mode':'fallback'})
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport), base_url='http://api') as client:
            store = StateStore(tmp_path/'bot.sqlite')
            service = BotService(client, store)
            for turn in range(6):
                await service.query(100, f'Уфа {turn} email roman@example.org телефон +7 (999) 123-45-67 sk-TEST_DATA_secret_key')
            restored = StateStore(tmp_path/'bot.sqlite').get(100)
            assert len(restored['search']['turns']) == 12
            literal = json.dumps(restored)
            assert 'roman@example.org' not in literal and 'TEST_DATA_secret_key' not in literal
            assert set(sent) == {'telegram:100'}
    asyncio.run(check())


def test_more_callback_is_bound_to_user_and_consumed_atomically(tmp_path):
    from apps.bot.service import StateStore
    store = StateStore(tmp_path/'bot.sqlite')
    store.put(100, {'more_nonce':'TEST_DATA_nonce'})
    assert not store.consume_callback(200, 'TEST_DATA_nonce')
    assert store.consume_callback(100, 'TEST_DATA_nonce')
    assert not store.consume_callback(100, 'TEST_DATA_nonce')


def test_notifications_ack_only_proven_outcomes_and_leave_timeouts_unknown():
    from apps.bot.notifications import deliver_batch
    acknowledgements = []
    def transport(request):
        if request.url.path.endswith('/claim'):
            return httpx.Response(200, json={'items':[
                {'id':'sent', 'chat_id':'100', 'event':{'title':'TEST_DATA'}, 'lease_token':'lease-1'},
                {'id':'unknown', 'chat_id':'200', 'event':{'title':'TEST_DATA'}, 'lease_token':'lease-2'}]})
        acknowledgements.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={'status':'sent'})
    async def send(chat_id, text):
        if chat_id == 200:
            raise TimeoutError('TEST_DATA may have been delivered')
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport), base_url='http://api') as client:
            assert await deliver_batch(client, send, 'TEST_DATA_admin_token') == 2
    asyncio.run(check())
    assert acknowledgements == [('/api/v1/admin/notifications/sent/ack', {'lease_token':'lease-1', 'success':True, 'error':None})]


def test_typed_confirmation_uses_only_one_fresh_identity_bound_agent_nonce(tmp_path):
    from datetime import datetime, timedelta, timezone
    from apps.bot.service import BotService, StateStore
    calls = []
    def transport(request):
        body = json.loads(request.content)
        calls.append(body)
        result = {'state':{'filters':{'city':'Уфа'}}, 'events':[], 'mode':'fallback'}
        if body.get('confirmation_nonce'):
            result['action_result'] = {'id':'TEST_DATA-feedback', 'status':'received'}
        return httpx.Response(200, json=result)
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport), base_url='http://api') as client:
            store = StateStore(tmp_path/'bot.sqlite')
            service = BotService(client, store)
            pending = {'nonce':'TEST_DATA_nonce_capability', 'expires_at':(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()}
            store.put(100, {'agent_pending':pending})
            await service.query(100, 'Да, покажи другие события')
            assert calls[-1]['confirmation_nonce'] is None
            await service.query(200, 'Подтверждаю')
            assert len(calls) == 1
            await service.query(100, '/confirm')
            assert calls[-1]['confirmation_nonce'] == pending['nonce'] and calls[-1]['message'] == 'Подтверждаю'
            await service.query(100, 'Подтверждаю')
            assert len(calls) == 2
            pending['expires_at'] = (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
            store.put(100, {'agent_pending':pending})
            await service.query(100, 'Подтверждаю')
            assert len(calls) == 2
    asyncio.run(check())


def test_typed_legacy_subscription_confirmation_and_conflicting_pending_actions(tmp_path):
    from datetime import datetime, timedelta, timezone
    from apps.bot.service import BotService, StateStore
    calls = []
    def transport(request):
        calls.append(request.url.path)
        return httpx.Response(201, json={'id':'TEST_DATA-subscription', 'delete_token':'TEST_DATA-token', 'notifications':'disabled'})
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport), base_url='http://api') as client:
            store = StateStore(tmp_path/'bot.sqlite')
            service = BotService(client, store)
            service.prepare_subscription(100, 'Уфа')
            state = store.get(100)
            state['agent_pending'] = {'nonce':'TEST_DATA-other_action', 'expires_at':(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()}
            store.put(100, state)
            result = await service.query(100, 'Подтверждаю')
            assert 'несколько' in result['text'] and not calls
            state.pop('agent_pending')
            store.put(100, state)
            result = await service.query(100, 'Подтверждаю')
            assert 'Подписка сохранена' in result['text'] and calls == ['/api/v1/subscriptions']
            await service.query(100, '/confirm')
            assert len(calls) == 1
    asyncio.run(check())


def test_bot_deterministic_answer_does_not_claim_model_outage():
    from apps.bot.service import answer_text
    result = answer_text({'mode':'fallback', 'text':'Подобрал план: <2 события> по данным каталога.'})
    assert 'Модель не использовалась. Ответ построен по данным каталога.' in result
    assert '&lt;2 события&gt;' in result
    assert 'отключена' not in result and 'недоступна' not in result
    assert answer_text({'mode':'llm', 'text':'Нашёл события.'}) == 'Нашёл события.'
