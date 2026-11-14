"""Source adapters extract facts only. Publication remains a moderator action."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin, urlsplit, parse_qsl, urlencode, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

MONTHS = {'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4, 'мая': 5, 'июня': 6,
          'июля': 7, 'августа': 8, 'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12}
USER_AGENT = 'Kiber102Catalog/0.1 (+local hackathon demonstration; low frequency)'


def parse_price(text: str) -> tuple[int | None, int | None, bool]:
    text = text.lower().replace('\xa0', ' ')
    if 'бесплатно' in text or 'вход свободный' in text:
        return 0, 0, True
    numbers = [int(n.replace(' ', '')) for n in re.findall(r'\d[\d ]*', text)]
    if not numbers:
        return None, None, False
    if len(numbers) > 1:
        return numbers[0], numbers[1], False
    return numbers[0], None if 'от' in text or '-' in text else numbers[0], False


class Adapter(Protocol):
    def parse(self, raw: str, source_url: str) -> list[dict]: ...


class BashOperaAdapter:
    def parse(self, raw: str, source_url: str) -> list[dict]:
        soup = BeautifulSoup(raw, 'html.parser')
        heading = soup.select_one('h1')
        year = re.search(r'\b(20\d{2})\b', heading.get_text(' ', strip=True) if heading else '')
        if not year:
            return []  # Never infer the year of a potentially archived performance.
        records = []
        heading_text = heading.get_text(' ', strip=True).casefold() if heading else ''
        for item in soup.select('.item.content'):
            title = item.select_one('.name a')
            day = item.select_one('.date')
            hall = item.select_one('.hall')
            if not all((title, day, hall)):
                continue
            date_match = re.search(r'(\d{1,2})\s+([а-я]+)', day.get_text(' ', strip=True).lower())
            clock = re.search(r'(\d{2}:\d{2})', hall.get_text(' ', strip=True))
            if not date_match or not clock or date_match[2] not in MONTHS:
                continue
            # The official December programme explicitly includes January
            # sessions of the same season. Keep that chronological year rollover.
            session_year = int(year[1]) + (1 if 'декабрь' in heading_text and date_match[2] == 'января' else 0)
            start = f'{session_year}-{MONTHS[date_match[2]]:02}-{int(date_match[1]):02}T{clock[1]}:00+05:00'
            age = re.search(r'\b(0|6|12|16|18)\+', item.get_text(' ', strip=True))
            price = item.select_one('.price')
            minimum, maximum, free = parse_price(price.get_text(' ', strip=True) if price else '')
            ticket = item.select_one('.buy a[href]')
            genre_path = title.get('href', '')
            category = 'концерт' if '/concert' in genre_path else 'спектакль'
            composer = item.select_one('.composer')
            records.append({
                'provider_id': (urlsplit(ticket['href']).path.rstrip('/').split('/')[-1]
                                if ticket else item.get('id')),
                'title': title.get_text(' ', strip=True), 'date_start': start,
                'date_end': None, 'duration_min': None, 'category': category,
                'tags': ['балет'] if '/ballet/' in genre_path else ['опера'] if '/opera/' in genre_path else [],
                'city': 'Уфа', 'venue': 'Башкирский театр оперы и балета, ' + hall.get_text(' ', strip=True).split(',')[0],
                'address': 'Уфа, ул. Ленина, 5/1',
                'description': composer.get_text(' ', strip=True) if composer else title.get_text(' ', strip=True),
                'age_limit': int(age[1]) if age else None,
                'price_min': minimum, 'price_max': maximum, 'is_free': free,
                'org_link': urljoin(source_url, ticket['href']) if ticket else urljoin(source_url, genre_path),
                'source_url': source_url, 'source_type': 'страница', 'status': 'черновик',
            })
        return records


class RusdramAdapter:
    """JSON-LD is cross-checked with rendered rows to reject off-site performances."""
    def parse(self, raw: str, source_url: str) -> list[dict]:
        soup = BeautifulSoup(raw, 'html.parser')
        rows = {}
        for row in soup.select('article.show[data-date]'):
            title = row.select_one('.show__title')
            clock = row.select_one('.show__time')
            if title and clock:
                rows[(title.get_text(' ', strip=True), row['data-date'], clock.get_text(strip=True))] = row
        records = []
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.string or script.get_text())
                if data.get('@type') != 'TheaterEvent':
                    continue
                start = datetime.fromisoformat(data['startDate'])
                if start.tzinfo is None:
                    continue
                title = data['name']
                row = rows.get((title, start.date().isoformat(), start.strftime('%H:%M')))
                if row is None:
                    continue
                meta = row.select_one('.show__meta')
                meta_text = meta.get_text(' ', strip=True) if meta else ''
                if not any(x in meta_text for x in ['Большая сцена', 'Камерная сцена']):
                    continue  # Touring venue/address require separate proof.
                age = re.fullmatch(r'(0|6|12|16|18)\+', data.get('typicalAgeRange', ''))
                status = data.get('eventStatus', '')
                cancelled = status.endswith('EventCancelled')
                records.append({
                    'provider_id': str(data.get('@id') or row.get('id') or data.get('offers', {}).get('url') or '') or None,
                    'title': title, 'date_start': start.isoformat(), 'category': 'спектакль',
                    'city': 'Уфа', 'venue': 'Русский драматический театр РБ',
                    'address': 'Уфа, проспект Октября, 79', 'description': meta_text or title,
                    'age_limit': int(age[1]) if age else None,
                    'price_min': None, 'price_max': None, 'is_free': False,
                    'org_link': data.get('offers', {}).get('url') or data.get('url'),
                    'source_url': source_url, 'source_type': 'страница',
                    'status': 'отменено' if cancelled else 'rescheduled' if status.endswith('EventRescheduled') else 'черновик',
                })
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
        return records


class TimepadAdapter:
    def parse(self, raw: str, source_url: str) -> list[dict]:
        records = []
        for event in json.loads(raw).get('values', []):
            if event.get('access_status', 'public') != 'public':
                continue
            location = event.get('location') or {}
            prices = event.get('registration_data') or {}
            minimum, maximum = prices.get('price_min'), prices.get('price_max')
            # Remote events and unverified regions never enter the regional queue.
            if location.get('city') not in ['Уфа', 'Стерлитамак', 'Салават', 'Нефтекамск', 'Октябрьский', 'Белорецк', 'Сибай']:
                continue
            records.append({
                'provider_id': str(event['id']) if event.get('id') is not None else None,
                'title': event['name'], 'date_start': event['starts_at'],
                'date_end': event.get('ends_at'), 'city': location['city'],
                'venue': location.get('name') or location.get('address') or '',
                'address': location.get('address') or '', 'category': 'другое',
                'description': BeautifulSoup(event.get('description_short') or event['name'], 'html.parser').get_text(' ', strip=True),
                'price_min': minimum, 'price_max': maximum,
                'is_free': minimum == 0 and maximum == 0,
                'age_limit': event.get('age_limit'), 'org_link': event.get('url'),
                'source_url': source_url, 'source_type': 'API', 'status': 'черновик',
            })
        return records


@dataclass
class FetchResult:
    url: str
    body: str | None
    status: str
    checked_at: str
    sha256: str | None = None
    encoding: str = 'utf-8'


class SourceRegistry:
    def __init__(self):
        from .proculture import PROCultureAdapter
        from .sterlitamak import SterlitamakAdapter
        self.adapters: dict[str, Adapter] = {
            'bashopera': BashOperaAdapter(), 'rusdram': RusdramAdapter(), 'timepad': TimepadAdapter(),
            'sterlitamak': SterlitamakAdapter(),
            'proculture': PROCultureAdapter(),
        }

    def status(self) -> dict[str, str]:
        return {**dict.fromkeys(self.adapters, 'configured'),
                'proculture': 'configured' if os.getenv('PROCULTURE_API_KEY') else 'waiting_for_partner_key'}


def assert_public_url(url: str, allowed_hosts: set[str]) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in allowed_hosts or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('Источник вне списка разрешённых HTTPS-адресов')
    from apps.api.app.security import validate_source_url
    validate_source_url(url, resolve=True)


class PinnedSourceTransport(httpx.BaseTransport):
    """The validated public IP is also the connected IP, preserving TLS SNI."""
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        from apps.api.app.security import PinnedHTTPSConnection, validate_source_url
        parsed, address = validate_source_url(str(request.url), resolve=True)
        connection = PinnedHTTPSConnection(parsed.hostname, address)
        try:
            target = parsed.path or '/'
            if parsed.query:
                target += '?' + parsed.query
            connection.request('GET', target, headers={**dict(request.headers), 'Accept-Encoding': 'identity'})
            response = connection.getresponse()
            body = response.read(3_000_001)
            if len(body) > 3_000_000:
                raise ValueError('Источник превышает лимит 3 МБ')
            return httpx.Response(response.status, headers=dict(response.getheaders()), content=body, request=request)
        except TimeoutError as error:
            raise httpx.ReadTimeout('Источник не ответил в отведённое время', request=request) from error
        except OSError as error:
            raise httpx.ConnectError('Соединение с источником недоступно', request=request) from error
        except http.client.HTTPException as error:
            raise httpx.RemoteProtocolError('Некорректный ответ источника', request=request) from error
        finally:
            connection.close()


def fetch_source(url: str, cache_dir: Path, allowed_hosts: set[str], token: str | None = None,
                 api_key: str | None = None) -> FetchResult:
    """Bounded requests, robots, redirect refusal, conditional fetch and raw evidence."""
    assert_public_url(url, allowed_hosts)
    request_url = url
    if api_key:
        parsed = urlsplit(url)
        if parsed.hostname != 'pro.culture.ru':
            raise ValueError('Партнёрский ключ разрешён только для pro.culture.ru')
        request_url = urlunsplit(parsed._replace(query=urlencode(parse_qsl(parsed.query) + [('apiKey', api_key)])))
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()
    meta_path = cache_dir / f'{key}.json'
    metadata = json.loads(meta_path.read_text(encoding='utf8')) if meta_path.exists() else {}
    cached_path = cache_dir / f"{metadata.get('sha256', '')}.raw"
    valid_cache = cached_path.is_file() and hashlib.sha256(cached_path.read_bytes()).hexdigest() == metadata.get('sha256')
    now = datetime.now(timezone.utc).isoformat()
    headers = {'User-Agent': USER_AGENT, 'Accept': 'application/json,text/html;q=0.9'}
    if token:
        if urlsplit(url).hostname != 'api.timepad.ru':
            raise ValueError('Токен Timepad разрешён только для api.timepad.ru')
        # Credentials apply to the exact allowlisted API request, never robots/redirects.
        headers['Authorization'] = 'Bearer ' + token
    if valid_cache and metadata.get('etag'):
        headers['If-None-Match'] = metadata['etag']
    if valid_cache and metadata.get('modified'):
        headers['If-Modified-Since'] = metadata['modified']
    with httpx.Client(transport=PinnedSourceTransport(), timeout=12, follow_redirects=False,
                      headers={'User-Agent': USER_AGENT}) as client:
        for attempt in range(2):
            try:
                robots = client.get(urljoin(url, '/robots.txt'))
                break
            except httpx.TransportError:
                if attempt == 1:
                    raise
        # Some institutions redirect a missing robots.txt to their public home page.
        # Follow only individually validated, same-authority HTTPS locations.
        for _ in range(3):
            if not robots.is_redirect:
                break
            destination = urljoin(str(robots.url), robots.headers.get('location', ''))
            if urlsplit(destination).netloc != urlsplit(url).netloc:
                return FetchResult(url, None, 'robots_redirect_requires_review', now)
            assert_public_url(destination, allowed_hosts)
            robots = client.get(destination)
        if robots.status_code == 200 and 'user-agent:' in robots.text.lower():
            parser = RobotFileParser()
            parser.parse(robots.text.splitlines())
            if not parser.can_fetch(USER_AGENT, url):
                return FetchResult(url, None, 'robots_disallowed', now)
        elif robots.status_code not in (200, 404):
            return FetchResult(url, None, 'robots_unavailable', now)
        last_error = None
        for attempt in range(3):
            try:
                with client.stream('GET', request_url, headers=headers) as response:
                    if response.status_code == 304:
                        if not valid_cache:
                            return FetchResult(url, None, 'invalid_cache_requires_review', now)
                        return FetchResult(url, None, 'not_modified', now, metadata.get('sha256'), metadata.get('encoding', 'utf-8'))
                    if response.status_code in (401, 403):
                        return FetchResult(url, None, 'access_requires_token_or_permission', now)
                    if response.status_code == 429 or response.status_code >= 500:
                        response.raise_for_status()
                    if response.is_redirect:
                        return FetchResult(url, None, 'redirect_requires_review', now)
                    response.raise_for_status()
                    parts = []
                    length = 0
                    for part in response.iter_bytes():
                        length += len(part)
                        if length > 3_000_000:
                            return FetchResult(url, None, 'body_too_large', now)
                        parts.append(part)
                    raw = b''.join(parts)
                    if not raw:
                        return FetchResult(url, None, 'empty_response_requires_review', now)
                    encoding = response.encoding or 'utf-8'
                    body = raw.decode(encoding, errors='replace')
                    digest = hashlib.sha256(raw).hexdigest()
                    (cache_dir / f'{digest}.raw').write_bytes(raw)
                    meta_path.write_text(json.dumps({'url': url, 'checked_at': now, 'sha256': digest,
                                                    'encoding': encoding,
                                                    'etag': response.headers.get('etag'),
                                                    'modified': response.headers.get('last-modified')}, ensure_ascii=False), encoding='utf8')
                    return FetchResult(url, body, 'fetched', now, digest, encoding)
            except httpx.HTTPError as exc:
                last_error = type(exc).__name__
                if attempt < 2:
                    time.sleep(2 ** attempt)
        return FetchResult(url, None, f'unavailable:{last_error}', now)
