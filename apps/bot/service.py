"""Telegram-independent API adapter, persistent minimal state, safe card rendering."""
from __future__ import annotations

import asyncio
from datetime import datetime
from functools import lru_cache
from hashlib import sha256
import hmac
from html import escape, unescape
from ipaddress import ip_address
import json
import os
from pathlib import Path
import secrets
import re
import sqlite3
import time
from weakref import WeakValueDictionary
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from apps.dialogue import fresh_search_state, starts_new_search


class StateStore:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS bot_state (user_hash TEXT PRIMARY KEY, payload TEXT NOT NULL, updated REAL NOT NULL)")

    def connection(self):
        return sqlite3.connect(self.path)

    @staticmethod
    def key(user_id: int):
        return sha256(f"telegram:{user_id}".encode()).hexdigest()

    def get(self, user_id: int) -> dict:
        current = time.time()
        with self.connection() as db:
            db.execute("DELETE FROM bot_state WHERE updated < ?", (current - 180 * 86400,))
            row = db.execute("SELECT payload, updated FROM bot_state WHERE user_hash = ?", (self.key(user_id),)).fetchone()
            if not row:
                return {}
            state = json.loads(row[0])
            if 'subscriptions' in state:
                state['subscriptions'] = [subscription for subscription in state['subscriptions']
                                          if subscription.get('expires', row[1] + 180 * 86400) > current]
            if row[1] < current - 30 * 86400:
                state = {'subscriptions': state['subscriptions']} if state.get('subscriptions') else {}
            db.execute('UPDATE bot_state SET payload=? WHERE user_hash=?',
                       (json.dumps(state, ensure_ascii=False), self.key(user_id)))
        return state

    def put(self, user_id: int, state: dict):
        with self.connection() as db:
            db.execute("INSERT INTO bot_state VALUES (?, ?, ?) ON CONFLICT(user_hash) DO UPDATE SET payload=excluded.payload, updated=excluded.updated",
                       (self.key(user_id), json.dumps(state, ensure_ascii=False), time.time()))

    def consume_callback(self, user_id: int, nonce: str) -> bool:
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM bot_state WHERE user_hash=?', (self.key(user_id),)).fetchone()
            state = json.loads(row[0]) if row else {}
            stored = state.get('more_nonce', '')
            if not stored or not secrets.compare_digest(stored, nonce):
                return False
            state.pop('more_nonce', None)
            db.execute('UPDATE bot_state SET payload=? WHERE user_hash=?', (json.dumps(state, ensure_ascii=False), self.key(user_id)))
            return True


def redacted_turn(text):
    value = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[email]', str(text))
    value = re.sub(r'(?<!\d)(?:\+7|8)[\s(.-]*\d{3}[)\s.-]*\d{3}[\s.-]*\d{2}[\s.-]*\d{2}(?!\d)', '[phone]', value)
    value = re.sub(r'(?:sk-|Bearer\s+|token[=: ]+)[A-Za-z0-9_:.\-]{8,}', '[secret]', value, flags=re.I)
    return value[:2000]


def signed_identity(user_id: int) -> dict:
    secret = os.getenv('TRUSTED_PROXY_SECRET', '')
    if not secret:
        return {}
    identity, timestamp = f'telegram:{user_id}', str(int(time.time()))
    signature = hmac.new(secret.encode(), f'{timestamp}\n{identity}'.encode(), sha256).hexdigest()
    return {'X-Client-Identity':identity, 'X-Client-Timestamp':timestamp, 'X-Client-Signature':signature}


def safe_public_url(value: str | None):
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in ["http", "https"] or not parsed.hostname or parsed.username or parsed.password:
        return None
    return value


def event_links(event: dict) -> list[tuple[str, str]]:
    links = [('Источник', event.get('source_url'))]
    # An explicit empty API list suppresses the original purchase URL.
    # Legacy payloads without this field retain their organizer link.
    if 'ticket_links' in event:
        links.extend((link.get('label') or 'Организатор / билеты', link.get('url'))
                     for link in (event['ticket_links'] or []))
    else:
        links.append(('Организатор / билеты', event.get('org_link')))
    return [(label, url) for label, url in links if safe_public_url(url)]


def event_card(event: dict) -> str:
    lines = [f"<b>{escape(str(event.get('title') or 'Мероприятие'))}</b>"]
    status = event.get("status")
    if status == "cancelled":
        lines.append("Отменено")
    elif status == 'rescheduled':
        lines.append('Перенесено, проверьте новую дату у организатора')
    elif status != "published":
        lines.append("Не опубликовано")
    ticket_status = event.get('ticket_availability')
    ticket_notices = {
        'sold_out': 'Билеты распроданы',
        'off_sale': 'Продажа билетов закрыта',
        'cancelled': 'Отменено',
        'rescheduled': 'Перенесено, проверьте новую дату у организатора',
    }
    if ticket_status in ticket_notices and ticket_status != status:
        lines.append(ticket_notices[ticket_status])
    try:
        start = datetime.fromisoformat(event["date_start"].replace("Z", "+00:00"))
        lines.append(start.astimezone(ZoneInfo("Asia/Yekaterinburg")).strftime("%d.%m.%Y · %H:%M"))
    except (KeyError, TypeError, ValueError):
        lines.append("Время уточняется")
    for key in ["category", "city", "venue", "address"]:
        if event.get(key):
            lines.append(escape(str(event[key])))
    if event.get("description"):
        lines.append(escape(str(event["description"])[:1200]))
    if event.get("is_free"):
        lines.append("Бесплатно")
    elif event.get("price_min") is not None and event.get("price_max") is not None:
        lines.append(f"{event['price_min']:g}–{event['price_max']:g} ₽ за билет. Уточните цену сеанса у организатора.")
    elif event.get("price_min") is not None:
        lines.append(f"От {event['price_min']:g} ₽. Итоговая цена уточняется.")
    else:
        lines.append("Цена уточняется у организатора")
    lines.append(f"{event['age_limit']}+" if event.get("age_limit") is not None else "Возрастное ограничение уточняется")
    sources = event.get('sources') or [{'url':event.get('source_url'), 'name':'Источник'}]
    for source in sources[:4]:
        url = safe_public_url(source.get('url'))
        if url:
            lines.append(f'<a href="{escape(url, quote=True)}">{escape(str(source.get("name") or "Источник"))}</a>')
    if event.get('verified_at'):
        try:
            checked = datetime.fromisoformat(event['verified_at'].replace('Z', '+00:00'))
            lines.append('Проверено ' + checked.astimezone(ZoneInfo('Asia/Yekaterinburg')).strftime('%d.%m.%Y %H:%M'))
        except (TypeError, ValueError):
            pass
    return "\n".join(lines)


def answer_text(result: dict) -> str:
    """A deterministic tool response is not a health probe of the model."""
    text = str(result.get('text') or '')
    if result.get('mode') == 'fallback':
        text += '\nМодель не использовалась. Ответ построен по данным каталога.'
    return escape(text)


def _short_text(value, limit):
    text = ' '.join(str(value or '').split())
    return text if len(text) <= limit else text[:max(0, limit-1)].rstrip() + '…'


def _search_conditions(filters):
    parts = []
    if filters.get('city'):
        parts.append(_short_text(filters['city'], 40))
    start, end = filters.get('date_from'), filters.get('date_to')
    if start or end:
        try:
            if not start:
                period = 'до ' + datetime.fromisoformat(end).strftime('%d.%m.%Y')
            elif not end:
                period = 'с ' + datetime.fromisoformat(start).strftime('%d.%m.%Y')
            else:
                period = datetime.fromisoformat(start).strftime('%d.%m.%Y')
            if start and end and end != start:
                period += '–' + datetime.fromisoformat(end).strftime('%d.%m.%Y')
            parts.append(period)
        except (TypeError, ValueError):
            pass
    if filters.get('category'):
        parts.append(_short_text(filters['category'], 25))
    if filters.get('q'):
        parts.append('название «' + _short_text(filters['q'], 40) + '»')
    if filters.get('age') is not None:
        parts.append(f"возраст {filters['age']} лет")
    if filters.get('price_max') is not None:
        parts.append(f"до {filters['price_max']:g} ₽ на {filters.get('group_size', 1)} чел.")
    return ' · '.join(parts)


def search_presentation(result: dict, website: str) -> dict:
    """One bounded HTML message with factual cards and identity-bound controls."""
    events = result.get('events') or []
    filters = result.get('state', {}).get('filters', {})
    buttons = []
    for index, event in enumerate(events[:5], 1):
        row = []
        card_url = f"{website.rstrip('/')}/events/{event['id']}"
        if website.startswith('https://') and safe_public_url(card_url):
            row.append({'text':f'{index}. Подробнее', 'url':card_url})
        elif re.fullmatch(r'[A-Za-z0-9_-]{1,40}', str(event.get('id', ''))):
            row.append({'text':f'{index}. Подробнее', 'callback_data':f"event:{event['id']}"})
        seen_urls = {button.get('url') for button in row}
        for label, url in event_links(event)[:3]:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            button_text = 'Источник' if label == 'Источник' else 'Билеты' if 'билет' in label.lower() else 'Организатор'
            row.append({'text':button_text, 'url':url})
        if row:
            buttons.append(row[:1])
            if len(row) > 1:
                buttons.append(row[1:3])
            if len(row) > 3:
                buttons.append(row[3:])
    action = result.get('pending_action') or {}
    if isinstance(action.get('nonce'), str) and re.fullmatch(r'[A-Za-z0-9_-]{8,50}', action['nonce']):
        buttons.append([{'text':'Подтвердить', 'callback_data':'agentconfirm:' + action['nonce']}])
    navigation = result.get('page_navigation') or {}
    nonce = navigation.get('nonce')
    if navigation.get('pages',0)>1 and isinstance(nonce,str) and re.fullmatch(r'[A-Za-z0-9_-]{8,40}',nonce):
        index, count = navigation['index'], navigation['pages']
        row = []
        if index:
            row.append({'text':'←','callback_data':f'page:{nonce}:{index-1}'})
        row.append({'text':f'{index+1}/{count}','callback_data':f'page:{nonce}:{index}'})
        if index+1<count:
            row.append({'text':'→','callback_data':f'page:{nonce}:{index+1}'})
        buttons.append(row)
    nonce = result.get('more_nonce')
    if not navigation and events and isinstance(nonce, str) and re.fullmatch(r'[A-Za-z0-9_-]{8,50}', nonce):
        buttons.append([{'text':'Показать ещё', 'callback_data':'more:' + nonce}])
    buttons.append([{'text':'Новый поиск', 'callback_data':'search'}])

    profiles = [(650, 100, 80, 100), (400, 65, 45, 50), (180, 30, 0, 0)]
    for body_limit, title_limit, venue_limit, reason_limit in profiles:
        heading = 'Подборка событий' if events else 'Результат поиска'
        if action:
            heading = 'Подтвердите действие'
        elif result.get('question'):
            heading = 'Уточните, пожалуйста'
        parts = [f'<b>{heading}</b>']
        body = str(result.get('text') or '')
        if result.get('question') and not events and not action:
            body = str(result['question'])
        if body.startswith('Нашёл подтверждённые варианты по вашим условиям.'):
            body = 'Бюджет проверен на всю группу.' if 'Бюджет проверен' in body else ''
        if body:
            parts.append(escape(_short_text(body, body_limit)))
        conditions = _search_conditions(filters)
        if conditions:
            parts.append('<i>' + escape(_short_text(conditions, 240 if venue_limit else 100)) + '</i>')
        question = result.get('question')
        if question and str(question) not in body:
            parts.append(escape(_short_text(question, 220)))
        for index, event in enumerate(events[:5], 1):
            lines = [f"<b>{index}. {escape(_short_text(event.get('title') or 'Мероприятие', title_limit))}</b>"]
            try:
                when = datetime.fromisoformat(event['date_start'].replace('Z', '+00:00')).astimezone(ZoneInfo('Asia/Yekaterinburg')).strftime('%d.%m · %H:%M')
            except (KeyError, TypeError, ValueError):
                when = 'Время уточняется'
            age = f"{event['age_limit']}+" if event.get('age_limit') is not None else 'Возраст уточняется'
            lines.append(escape(when + ' · ' + age))
            if venue_limit and event.get('venue'):
                lines.append(escape(_short_text(event['venue'], venue_limit)))
            low, high = event.get('price_min'), event.get('price_max')
            if low == high == 0:
                price = 'Бесплатно'
            elif low is not None and high is not None:
                price = f'{low:g}–{high:g} ₽ за билет' if low != high else f'{low:g} ₽ за билет'
            elif low is not None:
                price = f'От {low:g} ₽ за билет · итоговая цена уточняется'
            else:
                price = 'Цена уточняется'
            lines.append(escape(price))
            availability = {'sold_out':'Билеты распроданы', 'off_sale':'Продажа закрыта', 'cancelled':'Отменено', 'rescheduled':'Перенесено'}.get(event.get('ticket_availability'))
            if availability:
                lines.append(availability)
            reason = result.get('explanations', {}).get(event['id'])
            if reason and reason_limit:
                lines.append('<i>' + escape(_short_text(reason, reason_limit)) + '</i>')
            parts.append('\n'.join(lines))
        if result.get('mode') == 'fallback':
            parts.append('<i>Поиск без модели</i>')
        text = '\n\n'.join(parts)
        if len(text.encode('utf-16-le'))//2 <= 3900:
            return {'text':text, 'buttons':buttons}
    # Every field above has a bounded compact profile; never truncate HTML tags.
    raise ValueError('Не удалось подготовить компактный ответ')


@lru_cache(maxsize=1)
def _editorial_photos():
    path = Path(__file__).resolve().parents[1]/'web/src/lib/editorial-images.json'
    try:
        return json.loads(path.read_text(encoding='utf8'))
    except (OSError, ValueError):
        return {}


def _event_photo(event, website=''):
    image = event.get('poster_url') or event.get('image_url')
    if not image:
        editorial = _editorial_photos().get(event.get('org_link') or '', {})
        local = editorial.get('local_url', '')
        image = website.rstrip('/')+local if website.startswith('https://') and re.fullmatch(r'/editorial/[a-f0-9]{16}\.jpg', local) else editorial.get('url')
    try:
        url = safe_public_url(image)
        parsed = urlsplit(url or '')
        port = parsed.port
    except ValueError:
        return None
    if not url:
        return None
    if parsed.scheme != 'https' or port not in {None,443} or parsed.hostname in {'localhost','localhost.localdomain'}:
        return None
    try:
        if not ip_address(parsed.hostname).is_global:
            return None
    except ValueError:
        if '.' not in parsed.hostname or parsed.hostname.endswith(('.local','.internal','.localhost')):
            return None
    return url


def rich_search_presentation(result: dict, website: str) -> dict:
    """Native Telegram article: each photo and link belongs to its event."""
    view = search_presentation(result, website)
    blocks = []
    events = result.get('events') or []
    for position, chunk in enumerate(view['text'].split('\n\n')):
        plain = unescape(re.sub(r'<[^>]*>', '', chunk))
        card = re.match(r'^<b>([1-5])\. ', chunk)
        if position == 0:
            blocks.append({'type':'heading','size':2,'text':plain})
        elif card:
            index = int(card[1])-1
            event = events[index]
            title, _, details = plain.partition('\n')
            if index:
                blocks.append({'type':'divider'})
            blocks.append({'type':'heading','size':3,'text':title})
            photo = _event_photo(event, website)
            if photo:
                blocks.append({'type':'photo','photo':{'type':'photo','media':photo}})
            if details:
                blocks.append({'type':'paragraph','text':details})
            card_url = f"{website.rstrip('/')}/events/{event['id']}"
            if website.startswith('https://') and safe_public_url(card_url):
                blocks.append({'type':'buttons','buttons':[{'text':'Подробнее о событии','url':card_url}]})
            links = [{'text':label,'url':url} for label,url in event_links(event)]
            for start in range(0,len(links),2):
                pair = links[start:start+2]
                for link in pair:
                    link['text'] = 'Источник' if link['text']=='Источник' else 'Билеты' if 'билет' in link['text'].lower() else 'Организатор'
                blocks.append({'type':'buttons','buttons':pair})
        else:
            blocks.append({'type':'footer' if plain=='Поиск без модели' else 'paragraph','text':plain})
    controls = [[button for button in row if button.get('callback_data') and not button['callback_data'].startswith('event:')]
                for row in view['buttons']]
    return {'rich_message':{'blocks':blocks,'skip_entity_detection':True}, 'buttons':[row for row in controls if row]}


@lru_cache(maxsize=12)
def _photo_bytes(url, website, hour):
    """Bounded JPEG/PNG upload, using the project's pinned public transport."""
    parsed = urlsplit(url)
    local = re.fullmatch(r'/editorial/[a-f0-9]{16}\.jpg', parsed.path)
    if local and parsed.netloc==urlsplit(website).netloc:
        path = Path(__file__).resolve().parents[1]/'web/public'/parsed.path.lstrip('/')
        if path.is_file() and path.stat().st_size <= 3_000_000:
            raw = path.read_bytes()
        else:
            return None
    else:
        from apps.worker.sources import PinnedSourceTransport
        with httpx.Client(transport=PinnedSourceTransport(),timeout=12,follow_redirects=False,trust_env=False) as client:
            response = client.get(url,headers={'User-Agent':'Kiber102-culture/1.0','Accept':'image/jpeg,image/png'})
            response.raise_for_status()
            if response.headers.get('content-type','').split(';')[0].lower() not in {'image/jpeg','image/png'}:
                return None
            raw = response.content
    if not 0 < len(raw) <= 3_000_000 or not (raw.startswith(b'\xff\xd8\xff') or raw.startswith(b'\x89PNG\r\n\x1a\n')):
        return None
    return raw


async def prepare_rich_media(article, website):
    """Upload photos as files so Telegram need not fetch partner websites."""
    from aiogram.types import BufferedInputFile, InputRichMessage
    blocks = article['blocks']
    urls = list(dict.fromkeys(b['photo']['media'] for b in blocks if b['type']=='photo'))
    async def read(url):
        try:
            return await asyncio.to_thread(_photo_bytes,url,website,int(time.time()//3600))
        except (OSError, ValueError, httpx.HTTPError):
            return None
    data = dict(zip(urls, await asyncio.gather(*(read(url) for url in urls)), strict=True))
    prepared = []
    for block in blocks:
        if block['type']!='photo':
            prepared.append(block)
            continue
        raw = data.get(block['photo']['media'])
        if raw:
            suffix = 'png' if raw.startswith(b'\x89PNG') else 'jpg'
            prepared.append({'type':'photo','photo':{'type':'photo','media':BufferedInputFile(raw,filename=f'event.{suffix}')}})
    return InputRichMessage.model_validate({**article,'blocks':prepared})


class BotService:
    def __init__(self, client: httpx.AsyncClient, store: StateStore):
        self.client = client
        self.store = store
        self._user_locks = WeakValueDictionary()

    def user_lock(self, user_id):
        lock = self._user_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._user_locks[user_id] = lock
        return lock

    async def query(self, user_id: int, message: str, confirmation_nonce: str | None = None):
        if message.strip().lower().replace('ё','е') in {'а еще','еще','покажи еще','дальше'}:
            current = self.store.get(user_id)
            view = current.get('page_view') or {}
            if view and view.get('expires',0)>time.time():
                if view['index']+1<view['total']:
                    return await self.page(user_id,view['nonce'],view['index']+1)
                return {'text':'Это последняя страница подборки. Можно изменить условия или написать новый запрос.','events':[],
                        'state':current.get('search',{}),'mode':'fallback','tool_calls':0}
        async with self.user_lock(user_id):
            return await self._query(user_id, message, confirmation_nonce)

    async def _query(self, user_id: int, message: str, confirmation_nonce: str | None = None, page_request=False):
        current = self.store.get(user_id)
        if starts_new_search(message, current.get('search', {})):
            fresh = fresh_search_state(message, current.get('search', {}))
            self._cancel(user_id)
            if fresh:
                current = self.store.get(user_id)
                current['search'] = fresh
                self.store.put(user_id, current)
        state = self.store.get(user_id)
        affirmation = bool(re.fullmatch(r'(?:подтверждаю|/confirm(?:@[A-Za-z0-9_]{5,32})?)', message.strip(), re.I))
        if affirmation and confirmation_nonce is None:
            agent_pending = state.get('agent_pending', {})
            legacy_pending = state.get('pending', {})
            try:
                agent_valid = bool(agent_pending.get('nonce') and datetime.fromisoformat(agent_pending.get('expires_at', '').replace('Z', '+00:00')).timestamp() > time.time())
            except (TypeError, ValueError, AttributeError):
                agent_valid = False
            legacy_valid = bool(legacy_pending.get('nonce') and isinstance(legacy_pending.get('expires'), (int, float)) and legacy_pending['expires'] > time.time())
            count = int(agent_valid) + int(legacy_valid)
            if count != 1:
                text = ('Есть несколько ожидающих действий. Используйте кнопку нужного подтверждения или /cancel и повторите запрос.'
                        if count else 'Сейчас нет действующего запроса на подтверждение. Повторите нужное действие.')
                return {'text':text, 'events':[], 'question':None, 'state':state.get('search', {}), 'mode':'fallback', 'tool_calls':0}
            if legacy_valid:
                done = await self._confirm_subscription(user_id, legacy_pending['nonce'])
                configured = self.store.get(user_id).get('notifications') in {'enabled', 'telegram'}
                text = ('Подписка сохранена. Доставка уведомлений включена.' if configured else 'Подписка сохранена. Доставка уведомлений пока не настроена.') if done else 'Подтверждение истекло или уже использовано.'
                return {'text':text, 'events':[], 'question':None, 'state':state.get('search', {}), 'mode':'fallback', 'tool_calls':int(done)}
            confirmation_nonce, message = agent_pending['nonce'], 'Подтверждаю'
        response = await self.client.post("/api/v1/agent/query", headers=signed_identity(user_id), json={"message": message, "result_limit":3, "state": state.get("search", {}), "dialog_id":state.get('dialog_id'), "confirmation_nonce":confirmation_nonce})
        response.raise_for_status()
        body = response.json()
        # A synchronous menu action may have saved a pending subscription while
        # the HTTP request was in flight. Preserve its fresh state.
        state = self.store.get(user_id)
        state['dialog_id'] = body.get('dialog_id')
        state["search"] = body['state']
        state['search']['turns'] = [{'role':turn['role'], 'content':redacted_turn(turn['content'])} for turn in body['state'].get('turns', [])[-20:]]
        if body.get('pending_action'):
            state['agent_pending'] = body['pending_action']
        if body.get('action_result'):
            result = body['action_result']
            state.pop('agent_pending', None)
            if result.get('delete_token') and not any(item['id'] == result['id'] for item in state.get('subscriptions', [])):
                state.setdefault('subscriptions', []).append({**result, 'expires':time.time()+180*86400})
        if body.get('events'):
            state['more_nonce'] = secrets.token_urlsafe(12)
            body['more_nonce'] = state['more_nonce']
        else:
            state.pop('more_nonce', None)
        if not page_request:
            state.pop('page_view',None)
            if body.get('events'):
                provided = body['events']
                count = max(len(provided),int(body['state'].get('result_count',len(provided))))
                pages = (count+2)//3
                nonce = secrets.token_urlsafe(12)
                snapshots = {str(start//3):{**body,'events':provided[start:start+3]} for start in range(0,len(provided),3)}
                body['events'] = provided[:3]
                state['page_view'] = {'nonce':nonce,'index':0,'total':pages,'expires':time.time()+900,'pages':snapshots}
                body['page_navigation'] = {'nonce':nonce,'index':0,'pages':pages}
        self.store.put(user_id, state)
        return body

    async def page(self, user_id, nonce, target):
        async with self.user_lock(user_id):
            state = self.store.get(user_id)
            view = state.get('page_view') or {}
            if (not view or not secrets.compare_digest(view.get('nonce',''),nonce)
                    or view.get('expires',0)<time.time() or not 0<=target<view['total']
                    or abs(target-view['index'])>1):
                raise ValueError('Эта подборка устарела. Напишите новый запрос.')
            unchanged = target==view['index']
            if str(target) not in view['pages']:
                if target!=view['index']+1:
                    raise ValueError('Страница недоступна. Напишите новый запрос.')
                body = await self._query(user_id,'а ещё',page_request=True)
                if not body.get('events'):
                    raise ValueError('Каталог обновился. Напишите запрос заново.')
                view['pages'][str(target)] = dict(body)
            else:
                body = dict(view['pages'][str(target)])
            view['index'] = target
            state = self.store.get(user_id)
            state['search'] = body['state']
            state['page_view'] = view
            self.store.put(user_id,state)
            return {**body,'page_unchanged':unchanged,'page_navigation':{'nonce':nonce,'index':target,'pages':view['total']}}

    async def event(self, event_id: str):
        if not event_id or len(event_id) > 100 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in event_id):
            raise ValueError("Некорректный идентификатор")
        response = await self.client.get(f"/api/v1/events/{event_id}")
        response.raise_for_status()
        return response.json()

    def prepare_subscription(self, user_id: int, city: str, category: str | None = None):
        state = self.store.get(user_id)
        nonce = secrets.token_urlsafe(12)
        state["pending"] = {"nonce": nonce, "city": city, "category": category, "expires": time.time() + 300}
        self.store.put(user_id, state)
        return state["pending"]

    async def confirm_subscription(self, user_id: int, nonce: str) -> bool:
        async with self.user_lock(user_id):
            return await self._confirm_subscription(user_id, nonce)

    async def _confirm_subscription(self, user_id: int, nonce: str) -> bool:
        state = self.store.get(user_id)
        pending = state.get("pending")
        if not pending or not secrets.compare_digest(pending["nonce"], nonce) or pending["expires"] < time.time():
            return False
        # The same per-user nonce is an idempotency capability. An ambiguous
        # timeout leaves confirmation retryable without creating another record.
        response = await self.client.post("/api/v1/subscriptions", headers={**signed_identity(user_id), "Idempotency-Key":pending["nonce"]}, json={k: v for k, v in pending.items() if k in ["city", "category"] and v is not None})
        response.raise_for_status()
        body = response.json()
        state = self.store.get(user_id)
        state['notifications'] = body.get('notifications', 'disabled')
        if state.get("pending", {}).get("nonce") == nonce:
            state.pop("pending", None)
        if not any(item["id"] == body["id"] for item in state.get("subscriptions", [])):
            state.setdefault("subscriptions", []).append({"id": body["id"], "delete_token": body["delete_token"],
                                                           "expires": time.time() + 180 * 86400})
        self.store.put(user_id, state)
        return True

    async def unsubscribe(self, user_id: int) -> int:
        async with self.user_lock(user_id):
            return await self._unsubscribe(user_id)

    async def _unsubscribe(self, user_id: int) -> int:
        state = self.store.get(user_id)
        removed = 0
        remaining = []
        for subscription in state.get("subscriptions", []):
            try:
                response = await self.client.delete(f"/api/v1/subscriptions/{subscription['id']}", headers={"Authorization": f"Bearer {subscription['delete_token']}"})
                if response.status_code in [200, 204, 404]:
                    removed += 1
                else:
                    remaining.append(subscription)
            except httpx.HTTPError:
                remaining.append(subscription)
        state["subscriptions"] = remaining
        state.pop("pending", None)
        state.pop('agent_pending', None)
        self.store.put(user_id, state)
        return removed

    async def cancel(self, user_id: int):
        async with self.user_lock(user_id):
            self._cancel(user_id)

    def _cancel(self, user_id: int):
        state = self.store.get(user_id)
        state.pop("search", None)
        state.pop("pending", None)
        state.pop('agent_pending', None)
        state.pop('dialog_id', None)
        state.pop('more_nonce', None)
        state.pop('page_view',None)
        self.store.put(user_id, state)
