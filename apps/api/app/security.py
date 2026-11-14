"""Bounded outbound HTTPS with hostname allowlist and pinned public DNS."""
import hashlib
import hmac
import http.client
import ipaddress
import os
import json
import re
import base64
import secrets
import socket
import ssl
import time
from functools import lru_cache
from urllib.parse import urlsplit, parse_qsl, urlencode, quote

import httpx
from fastapi import HTTPException, Request


def allowed_hosts():
    value = os.getenv('SOURCE_ALLOWLIST', os.getenv('SOURCE_ALLOWED_HOSTS',
        'www.culture.ru,culture.ru,www.bashopera.ru,bashopera.ru,bashgf.ru,api.timepad.ru,pro.culture.ru,rusdram.ru,rusdramteatr.ru,ufa.kassy.ru,sterlitamak.kassy.ru'))
    return {item.strip().lower() for item in value.split(',') if item.strip()}


def resolve_public_addresses(hostname):
    """System DNS by default; explicitly configured DoH works with fake-IP VPN DNS.

    Both modes validate every returned address and the connection pins one of them.
    The fixed Google endpoint cannot be changed by imported/source-controlled data.
    """
    mode = os.getenv('SOURCE_DNS_MODE', 'system')
    # A positive lookup lives for at most 60s. Each connection still pins the
    # validated IP and validate_source_url enforces the current allowlist first.
    return list(_resolve_public_addresses_cached(hostname, mode, int(time.monotonic() // 60)))


@lru_cache(maxsize=1024)
def _resolve_public_addresses_cached(hostname, mode, time_bucket):
    # Exceptions never enter lru_cache. Immutable tuples cannot be mutated by
    # callers into private addresses or shared between different DNS modes.
    if mode == 'doh':
        response = httpx.get('https://dns.google/resolve',
                             params={'name': hostname, 'type': 'A', 'edns_client_subnet': '0.0.0.0/0'},
                             timeout=8, follow_redirects=False, trust_env=False,
                             headers={'Accept': 'application/dns-json'})
        response.raise_for_status()
        if len(response.content) > 20000:
            raise ValueError('Слишком большой ответ DNS')
        result = response.json()
        if result.get('Status') != 0:
            raise ValueError('DNS не смог разрешить имя источника')
        addresses = {record['data'] for record in result.get('Answer', []) if record.get('type') == 1}
    elif mode == 'system':
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)}
    else:
        raise ValueError('SOURCE_DNS_MODE должен быть system или doh')
    if not addresses or any(not ipaddress.ip_address(value).is_global for value in addresses):
        raise ValueError('Источник разрешился в непубличный IP адрес')
    # The pinned connection uses one address. Prefer IPv4 on dual-stack hosts
    # where a public AAAA answer may exist without a usable IPv6 route.
    return tuple(sorted(addresses, key=lambda value: (ipaddress.ip_address(value).version, value)))


def validate_source_url(url, resolve=False):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in allowed_hosts()
            or parsed.username or parsed.password or parsed.port not in {None, 443}):
        raise ValueError('Источник не входит в список разрешённых HTTPS узлов')
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        raise ValueError('IP адреса запрещены')
    if resolve:
        return parsed, resolve_public_addresses(parsed.hostname)[0]
    return parsed


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname, address):
        super().__init__(hostname, timeout=8, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, 443), timeout=self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _fetch_source_page(parsed, query, headers, byte_limit):
    address = resolve_public_addresses(parsed.hostname)[0]
    connection = PinnedHTTPSConnection(parsed.hostname, address)
    try:
        path = quote(parsed.path or '/', safe='/%:@-._~!$&\'()*+,;=')
        if query:
            path += '?' + urlencode(query)
        connection.request('GET', path, headers=headers)
        response = connection.getresponse()
        # Never follow redirects: new destinations must be independently configured.
        if response.status != 200:
            raise ValueError(f'Источник вернул HTTP {response.status}, переходы не выполняются')
        if not any(kind in response.getheader('Content-Type', '') for kind in ['text/', 'application/json']):
            raise ValueError('Источник вернул неподдерживаемый тип содержимого')
        body = response.read(byte_limit + 1)
        if len(body) > byte_limit:
            raise ValueError('Источник превышает лимит размера ответа')
        return body.decode('utf-8', errors='replace')
    finally:
        connection.close()


def fetch_source(url):
    """Verify complete bounded partner listings without persisting URL credentials."""
    parsed = validate_source_url(url)
    if parsed.hostname == 'rusdramteatr.ru' and parsed.path.rstrip('/') == '/afisha':
        from pathlib import Path
        from apps.worker.evidence import fetch_catalog_evidence
        return fetch_catalog_evidence(url, Path(os.getenv('SOURCE_EVIDENCE_DIR', '.local/sources')), allowed_hosts()).body
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    headers = {'User-Agent': 'Kiber102-culture/1.0 (+source-verification)',
               'Accept': 'text/html,application/json,text/plain', 'Accept-Encoding': 'identity'}
    partner = None
    if parsed.hostname == 'pro.culture.ru' and parsed.path.rstrip('/') == '/api/2.5/events':
        partner = ('events', 'offset')
        key = os.getenv('PROCULTURE_API_KEY')
        if not key:
            raise ValueError('Для проверки ПРО.Культура нужен партнёрский ключ на сервере')
        query['apiKey'] = key
    elif parsed.hostname == 'api.timepad.ru' and parsed.path.rstrip('/') == '/v1/events':
        partner = ('values', 'skip')
        token = os.getenv('TIMEPAD_API_TOKEN')
        if not token:
            raise ValueError('Для проверки Timepad нужен партнёрский ключ на сервере')
        headers['Authorization'] = 'Bearer ' + token
    if partner is None:
        return _fetch_source_page(parsed, query, headers, 2_000_000)
    rows_key, offset_key = partner
    try:
        offset = int(query.get(offset_key, '0'))
        if not 0 <= offset <= 10_000_000:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError('Некорректное смещение пагинации') from None
    query['limit'] = '100'
    rows, size = [], 0
    expected_total = None
    for _ in range(20):
        query[offset_key] = str(offset)
        body = _fetch_source_page(parsed, query, headers, 3_000_000 - size)
        size += len(body.encode('utf-8'))
        if size > 3_000_000:
            raise ValueError('Полная выдача источника превышает лимит 3 МБ')
        try:
            page = json.loads(body)
            values = page[rows_key]
            total = int(page['total']) if 'total' in page else None
            if not isinstance(values, list) or len(values) > 100 or (total is not None and not 0 <= total <= 10_000_000):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise ValueError('Некорректный ответ пагинации источника') from None
        if expected_total is not None and total != expected_total:
            raise ValueError('Выдача изменилась во время проверки; нужна повторная полная сверка')
        expected_total = total
        rows.extend(values)
        offset += len(values)
        if (total is not None and offset >= total) or (total is None and len(values) < 100):
            return json.dumps({rows_key: rows, 'total': len(rows)}, ensure_ascii=False)
        if not values or len(values) < 100:
            raise ValueError('Источник вернул неполную страницу пагинации')
    raise ValueError('Пагинация источника не получена полностью за 20 страниц')


def hash_token(value):
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password, iterations=600000):
    salt = secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), iterations).hex()
    return f'pbkdf2_sha256${iterations}${salt}${value}'


def verify_password(password, encoded):
    try:
        kind, rounds, salt, expected = encoded.split('$')
        if kind != 'pbkdf2_sha256' or not 100000 <= int(rounds) <= 2000000:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, AttributeError):
        return False


def admin_accounts():
    raw = os.getenv('ADMIN_ACCOUNTS_JSON', '').strip()
    if not raw:
        return {}
    try:
        accounts = json.loads(raw)
        if not isinstance(accounts, dict) or not accounts:
            raise ValueError
        for name, item in accounts.items():
            if (not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', name) or not isinstance(item, dict)
                    or item.get('role') not in {'admin', 'moderator', 'viewer'}
                    or not isinstance(item.get('password_hash'), str) or not item['password_hash']):
                raise ValueError
        return accounts
    except (ValueError, TypeError):
        raise ValueError('Некорректная конфигурация учётных записей') from None


def issue_token(secret, actor='admin', role='admin'):
    nonce = secrets.token_urlsafe(32)
    expires = str(int(time.time()) + 8 * 3600)
    subject = base64.urlsafe_b64encode(json.dumps({'actor': actor, 'role': role}, separators=(',', ':')).encode()).decode().rstrip('=')
    payload = f'{nonce}.{expires}.{subject}'
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + '.' + signature


def require_admin(request: Request):
    header = request.headers.get('authorization', '')
    secret = request.app.state.session_secret
    if not secret or not header.startswith('Bearer '):
        raise HTTPException(401, 'Требуется вход администратора')
    try:
        nonce, expires, subject, signature = header[7:].split('.')
        expected = hmac.new(secret.encode(), f'{nonce}.{expires}.{subject}'.encode(), hashlib.sha256).hexdigest()
        valid = int(expires) > time.time() and hmac.compare_digest(expected, signature)
        account = json.loads(base64.urlsafe_b64decode(subject + '=' * (-len(subject) % 4)))
        actor, role = account['actor'], account['role']
        valid = valid and isinstance(actor, str) and role in {'admin', 'moderator', 'viewer'}
    except (ValueError, TypeError, KeyError):
        valid = False
    if not valid:
        raise HTTPException(401, 'Сессия недействительна или истекла')
    if role == 'viewer' and request.method not in {'GET', 'HEAD', 'OPTIONS'}:
        raise HTTPException(403, 'Для изменения данных нужна роль модератора')
    if '/notifications' in request.url.path or '/sources/' in request.url.path or request.url.path.endswith('/maintenance'):
        if request.method not in {'GET', 'HEAD', 'OPTIONS'} and role != 'admin':
            raise HTTPException(403, 'Для управления источниками нужна роль администратора')
    request.state.actor = actor
    request.state.role = role
    return actor


def client_identity(request):
    """Only a server-side HMAC can establish a forwarded user identity."""
    identity = request.headers.get('x-client-identity')
    stamp = request.headers.get('x-client-timestamp', '')
    signature = request.headers.get('x-client-signature', '')
    if identity or stamp or signature:
        secret = os.getenv('TRUSTED_PROXY_SECRET', '')
        valid = len(secret) >= 32 and identity and re.fullmatch(r'[A-Za-z0-9:_-]{8,160}', identity)
        try:
            valid = valid and abs(int(stamp) - int(time.time())) <= 60
            expected = hmac.new(secret.encode(), f'{stamp}\n{identity}'.encode(), hashlib.sha256).hexdigest()
            valid = valid and hmac.compare_digest(signature, expected)
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise HTTPException(401, 'Недействительная подпись внутреннего клиента')
    else:
        identity = 'ip:' + (request.client.host if request.client else 'unknown')
    request.state.client_identity = identity
    return hashlib.sha256(identity.encode()).hexdigest()


def sanitize_trace(trace):
    """Keep structured evidence, redact incidental identifiers and credentials."""
    import re
    if isinstance(trace, dict):
        return {str(key): sanitize_trace(value) for key, value in trace.items()}
    if isinstance(trace, (list, tuple)):
        return [sanitize_trace(value) for value in trace]
    if not isinstance(trace, str):
        return trace
    value = re.sub(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}', '[email]', trace)
    value = re.sub(r'(?i)(bearer\s+|sk-)[a-zA-Z0-9_.-]+', '[secret]', value)
    # Preserve structured evidence while redacting human phone numbers. Phone
    # matching must never eat the hyphens in ISO dates or the digits in UUIDs.
    evidence = (r'([a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-'
                r'[a-fA-F0-9]{4}-[a-fA-F0-9]{12}|\b\d{4}-\d{2}-\d{2}'
                r'(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?'
                r'|https?://[^?\s]+)')
    parts = re.split(evidence, value)
    for index in range(0, len(parts), 2):
        parts[index] = re.sub(r'(?<!\w)\+?\d[\d ()-]{8,}\d(?!\w)',
                             lambda match: '[phone]' if len(re.sub(r'\D', '', match[0])) >= 10 else match[0],
                             parts[index])
    return ''.join(parts)
