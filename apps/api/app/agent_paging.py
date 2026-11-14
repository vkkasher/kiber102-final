"""Stable keyset cursors for a filtered search, independent of seen-ID history."""
import hashlib
import json
import math
import re


def search_filter_hash(filters):
    values = filters.model_dump(exclude={'exclude_ids', 'after_key'})
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def event_order_key(event, sort):
    from .agent import confirmed_price, event_datetime
    key = (event_datetime(event['date_start']).isoformat(), str(event['id']))
    price = confirmed_price(event)
    return ((price[1] if price else math.inf), *key) if sort == 'price' else key


def decode_cursor_key(value, sort):
    from .agent import event_datetime
    body = json.loads(value)
    if (not isinstance(body, dict) or set(body) != {'sort', 'date', 'id', 'price'}
            or body['sort'] != sort or not isinstance(body['date'], str)
            or not isinstance(body['id'], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', body['id'])):
        raise ValueError('Некорректный курсор поиска')
    start = event_datetime(body['date'])
    if start is None:
        raise ValueError('Некорректная дата курсора')
    key = (start.isoformat(), body['id'])
    if sort == 'date':
        return key
    price = body['price']
    if price is not None and (not isinstance(price, (int, float)) or isinstance(price, bool) or not math.isfinite(price) or price < 0):
        raise ValueError('Некорректная цена курсора')
    return (price if price is not None else math.inf, *key)


def make_search_cursor(event, filters):
    from .agent import confirmed_price, event_datetime
    price = confirmed_price(event)
    key = json.dumps({'sort':filters.sort, 'date':event_datetime(event['date_start']).isoformat(),
                      'id':event['id'], 'price':price[1] if price else None}, ensure_ascii=False, separators=(',', ':'))
    return {'key':key, 'filters_hash':search_filter_hash(filters)}
