"""Bounded collection pages. Incomplete exports cannot justify withdrawals."""
import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def collect_pages(url, first, fetch_next, read_body):
    combined = None
    hashes = []
    current = first
    for _ in range(20):
        body = read_body(current)
        if body is None:
            return combined, hashes, False
        data = json.loads(body)
        key = 'events' if 'events' in data else 'values'
        values = data.get(key)
        try:
            total = int(data['total'])
        except (KeyError, TypeError, ValueError):
            raise ValueError('Неверный счётчик постраничного API') from None
        if not isinstance(values, list) or total < 0:
            raise ValueError('Неверный контракт постраничного API')
        if combined is None:
            combined = {**data, key: []}
        combined[key].extend(values)
        hashes.append({'url': current.url, 'sha256': current.sha256})
        if len(combined[key]) >= total:
            return combined, hashes, True
        if not values:
            return combined, hashes, False
        parsed = urlsplit(url)
        query = dict(parse_qsl(parsed.query))
        # A configured nonzero offset is explicitly a partial export.
        offset_key = 'skip' if parsed.hostname == 'api.timepad.ru' else 'offset'
        initial = int(query.get(offset_key, '0'))
        query[offset_key] = str(initial + len(combined[key]))
        current = fetch_next(urlunsplit(parsed._replace(query=urlencode(query))))
    return combined, hashes, False
