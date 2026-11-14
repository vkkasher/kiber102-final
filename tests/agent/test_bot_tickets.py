import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(('availability', 'notice'), [
    ('sold_out', 'Билеты распроданы'),
    ('off_sale', 'Продажа билетов закрыта'),
    ('cancelled', 'Отменено'),
    ('rescheduled', 'Перенесено'),
])
def test_permanent_bot_card_explains_ticket_unavailability(availability, notice):
    from apps.bot.service import event_card

    text = event_card({'title': 'TEST_DATA спектакль', 'status': 'published',
                       'ticket_availability': availability})
    assert notice in text


@pytest.mark.parametrize(('ticket_fields', 'purchase_allowed'), [
    ({'ticket_availability': 'sold_out', 'ticket_links': []}, False),
    ({'ticket_availability': 'off_sale', 'ticket_links': []}, False),
    ({'ticket_availability': 'cancelled', 'ticket_links': []}, False),
    ({'ticket_availability': 'rescheduled', 'ticket_links': []}, False),
    ({'ticket_availability': 'unknown', 'ticket_links': []}, False),
    ({'ticket_availability': 'available', 'ticket_links': [
        {'url': 'https://example.org/buy', 'label': 'Страница продажи билетов'}]}, True),
    ({'ticket_availability': 'unknown', 'ticket_links': [
        {'url': 'https://example.org/buy', 'label': 'Страница организатора'}]}, True),
    ({'ticket_availability': 'available', 'ticket_links': [
        {'url': 'https://example.org/current-buy', 'label': 'Страница продажи билетов'}]}, False),
    ({'ticket_availability': 'available', 'ticket_links': [
        {'url': 'javascript:alert(1)', 'label': 'TEST_DATA unsafe'}]}, False),
    ({}, True),
])
@pytest.mark.parametrize('article_delivery', ['sent','rejected','unknown'])
def test_event_search_and_more_handlers_respect_api_ticket_links(tmp_path, monkeypatch, ticket_fields, purchase_allowed, article_delivery):
    import aiogram
    from aiogram.types import CallbackQuery, Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message, User
    from apps.bot import main, notifications, service as bot_service
    from apps.bot.service import BotService, StateStore

    event = {'id': 'TEST_DATA-event', 'title': 'TEST_DATA спектакль', 'status': 'published',
             'source_url': 'https://example.org/source', 'org_link': 'https://example.org/buy', 'image_url':'https://example.org/photo.jpg',
             **ticket_fields}
    rendered = []
    user = User(id=100, is_bot=False, first_name='TEST_DATA')
    message = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=100, type='private'),
                      from_user=user, text='/event TEST_DATA-event')
    store_path = tmp_path / 'bot.sqlite'

    async def answer(self, text, **kwargs):
        rendered.append((text, kwargs.get('reply_markup')))

    async def acknowledge(self, **kwargs):
        return True

    async def close():
        pass

    rich_calls=[]
    async def send_rich_message(**kwargs):
        from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
        from aiogram.methods import SendRichMessage
        rich_calls.append(kwargs)
        if article_delivery=='unknown':
            raise TelegramNetworkError(method=SendRichMessage(**kwargs),message='TEST_DATA uncertain outcome')
        if article_delivery=='rejected':
            raise TelegramBadRequest(method=SendRichMessage(**kwargs),message='TEST_DATA photo unavailable')
        blocks=kwargs['rich_message'].model_dump(exclude_none=True)['blocks']
        from aiogram.types import BufferedInputFile
        assert any(b['type']=='photo' and isinstance(b['photo']['media'],BufferedInputFile) for b in blocks)
        text='\n'.join(b.get('text','') for b in blocks)
        rows=[[InlineKeyboardButton(**button) for button in b['buttons']] for b in blocks if b['type']=='buttons']
        rendered.append((text,InlineKeyboardMarkup(inline_keyboard=rows)))

    async def get_event(self, event_id):
        assert event_id == event['id']
        return event

    async def query(self, user_id, text, confirmation_nonce=None):
        assert user_id == user.id
        return {'text': 'TEST_DATA result', 'events': [event], 'mode': 'fallback'}

    async def delivery(*args):
        await asyncio.sleep(3600)

    async def polling(self, bot, **kwargs):
        handlers = {handler.callback.__name__: handler.callback for handler in self.message.handlers}
        callbacks = {handler.callback.__name__: handler.callback for handler in self.callback_query.handlers}
        await handlers['event'](message)
        await handlers['free_text'](message)
        await handlers['explicit_search'](message.model_copy(update={'text': '/search TEST_DATA спектакль'}))
        StateStore(store_path).put(user.id, {'more_nonce': 'TEST_DATA_nonce'})
        callback = CallbackQuery(id='TEST_DATA', from_user=user, chat_instance='TEST_DATA',
                                 data='more:TEST_DATA_nonce', message=message)
        await callbacks['more'](callback)
        StateStore(store_path).put(user.id, {'agent_pending': {'nonce': 'TEST_DATA_confirm'}})
        await callbacks['agent_confirm'](callback.model_copy(update={'data': 'agentconfirm:TEST_DATA_confirm'}))
        assert len(rendered) == (1 if article_delivery=='unknown' else 5)
        subscription = {'id':'TEST_DATA-sub', 'delete_token':'TEST_DATA-capability'}
        for reset in ['start', 'search']:
            StateStore(store_path).put(user.id, {'search':{'filters':{'q':'Мне'}}, 'dialog_id':'TEST_DATA-old-dialog', 'subscriptions':[subscription]})
            if reset == 'start':
                await handlers['start'](message.model_copy(update={'text':'/start'}))
            else:
                await callbacks['action'](callback.model_copy(update={'data':'search'}))
            assert StateStore(store_path).get(user.id) == {'subscriptions':[subscription]}

    monkeypatch.setattr(main, 'load_dotenv', lambda *args, **kwargs: False)
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'TEST_DATA-no-network')
    monkeypatch.setenv('BOT_STATE_PATH', str(store_path))
    monkeypatch.setenv('WEB_PUBLIC_URL', 'https://example.org')
    monkeypatch.setattr(aiogram, 'Bot', lambda *args, **kwargs: SimpleNamespace(session=SimpleNamespace(close=close),send_rich_message=send_rich_message))
    monkeypatch.setattr(aiogram.Dispatcher, 'start_polling', polling)
    monkeypatch.setattr(Message, 'answer', answer)
    monkeypatch.setattr(CallbackQuery, 'answer', acknowledge)
    monkeypatch.setattr(BotService, 'event', get_event)
    monkeypatch.setattr(BotService, 'query', query)
    monkeypatch.setattr(notifications, 'delivery_loop', delivery)
    monkeypatch.setattr(bot_service, '_photo_bytes', lambda *args:b'\xff\xd8\xffTEST_DATA')
    assert asyncio.run(main.run()) == 0
    assert len(rich_calls)==4

    cards = [(text, markup) for text, markup in rendered if 'TEST_DATA спектакль' in text]
    assert len(cards) == (1 if article_delivery=='unknown' else 5)
    for text, markup in cards:
        urls = [button.url for row in markup.inline_keyboard for button in row if button.url]
        assert ('https://example.org/buy' in urls) is purchase_allowed
        assert 'https://example.org/source' in urls
        assert 'https://example.org/events/TEST_DATA-event' in urls
        assert all(url.startswith('https://') for url in urls)
        if any(link['url'] == 'https://example.org/current-buy' for link in ticket_fields.get('ticket_links', [])):
            assert 'https://example.org/current-buy' in urls
