"""Session-level public ticket evidence, never checkout or booking requests."""
from datetime import datetime, timezone
import json
import math
import re
import hashlib
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from apps.api.app.schemas import clean_text, normalize


VENUES = {
    'https://ufa.kassy.ru/venue/gbukii-russkijj-dramaticheskijj-teatr-rb-18/':
        ('Уфа', 'rusdram', ['Русский драматический театр РБ', 'Русский драматический театр Башкортостана']),
    'https://ufa.kassy.ru/venue/bashkirskijj-gosudarstvennyjj-teatr-opery-i-baleta-5/':
        ('Уфа', 'bashopera', ['Башкирский театр оперы и балета']),
    'https://sterlitamak.kassy.ru/venue/sterlitamakskijj-russkijj-dramaticheskijj-teatr-490/':
        ('Стерлитамак', 'sterlitamak', ['Государственный русский драматический театр г. Стерлитамак']),
}
HOSTS = {'ufa.kassy.ru', 'sterlitamak.kassy.ru'}
UNAVAILABLE = {'sold_out', 'off_sale', 'cancelled', 'rescheduled'}


def session_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme == 'https' and parsed.hostname in HOSTS and not parsed.username
            and not parsed.password and parsed.port in {None,443} and not parsed.query
            and re.fullmatch(r'/events/(?:teatr|detskie)/\d+-\d+/', parsed.path)):
        return value
    return None


def public_session_url(value):
    """Use a known purchase link only to identify its public session page."""
    return session_url(value[:-4] if value.endswith('/buy/') else value)


def sync_ticket_catalog(api, evidence_dir: Path, allowed_hosts: set[str], max_sessions=120):
    """One bounded hourly pass. A failed page never changes a previous observation."""
    from .sources import fetch_source
    events = []
    for page in range(1,101):
        response = api.get('/api/v1/admin/events',params={'page_size':1000,'page':page})
        response.raise_for_status()
        value = response.json()
        events.extend(value['items'])
        if len(events) >= value.get('total',len(events)) or len(value['items']) < 1000:
            break
    titles = {normalize(event['title']) for event in events}
    queue = list(dict.fromkeys(url for event in events
                               if (url := public_session_url(event.get('org_link') or ''))))
    report = {'checked':0,'matched':0,'unavailable':0,'errors':0,'venues':[]}
    cache = {}

    def read(url):
        if url in cache:
            return cache[url]
        fetched = fetch_source(url,evidence_dir,allowed_hosts)
        body = fetched.body
        if fetched.status == 'not_modified' and fetched.sha256:
            body = (evidence_dir/(fetched.sha256+'.raw')).read_bytes().decode(fetched.encoding,errors='replace')
        if fetched.status not in {'fetched','not_modified'} or not body:
            raise ValueError('Источник требует проверки: '+fetched.status)
        cache[url] = body
        return body

    for venue in VENUES:
        try:
            body = read(venue)
            soup = BeautifulSoup(body,'html.parser')
            anchors = {urljoin(venue,a['href']) for a in soup.select('a[href]')
                       if normalize(a.get_text(' ',strip=True)) in titles}
            queue.extend(url for url in discover_sessions(body,venue) if url in anchors)
            report['venues'].append({'url':venue,'status':'fetched'})
        except Exception as error:
            report['venues'].append({'url':venue,'status':'error','error_type':type(error).__name__})
            report['errors'] += 1
    for url in list(dict.fromkeys(queue))[:max(1,min(240,max_sessions))]:
        try:
            body = read(url)
            result = api.post('/api/v1/admin/ticket-evidence',json={
                'source_url':url,'content':body,'sha256':hashlib.sha256(body.encode()).hexdigest()})
            result.raise_for_status()
            value = result.json()
            report['checked'] += 1
            report['matched'] += value.get('matched',0)
            report['unavailable'] += int(value.get('matched',0) and value.get('availability') in UNAVAILABLE)
        except Exception:
            report['errors'] += 1
        time.sleep(.15)
    return report


def discover_sessions(content, source_url):
    soup = BeautifulSoup(content, 'html.parser')
    host = urlsplit(source_url).hostname
    return list(dict.fromkeys(url for a in soup.select('a[href]')
                             if (url := session_url(urljoin(source_url, a['href'])))
                             and urlsplit(url).hostname == host))


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and 0 <= result <= 10_000_000 else None
    except (ValueError,TypeError):
        return None


def parse_session(content, source_url):
    if not session_url(source_url):
        return None
    soup = BeautifulSoup(content, 'html.parser')
    heading = soup.find('h1')
    if heading is None:
        return None
    candidates = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
            candidates.extend(data if isinstance(data,list) else data.get('@graph', [data]))
        except (ValueError,AttributeError):
            continue
    for data in candidates:
        if not isinstance(data,dict) or not isinstance(data.get('@type'), str) or data.get('@type') not in {'TheaterEvent','Event','MusicEvent'}:
            continue
        if data.get('url') != source_url or not isinstance(data.get('name'),str) or normalize(data.get('name','')) != normalize(heading.get_text(' ',strip=True)):
            continue
        location = data.get('location') or {}
        if not isinstance(location,dict):
            continue
        venue_url = location.get('url')
        if venue_url not in VENUES:
            continue
        city, kind, aliases = VENUES[venue_url]
        try:
            start = datetime.fromisoformat(data['startDate'])
            if start.tzinfo is None:
                continue
            end = datetime.fromisoformat(data['endDate']) if data.get('endDate') else None
            if end and (end.tzinfo is None or end <= start):
                end = None
        except (ValueError,TypeError,KeyError):
            continue
        offers = data.get('offers') or {}
        if not isinstance(offers,dict):
            continue
        stock = str(offers.get('availability') or '').rsplit('/',1)[-1]
        availability = {'InStock':'available','LimitedAvailability':'available','SoldOut':'sold_out',
                        'OutOfStock':'sold_out','Discontinued':'off_sale'}.get(stock)
        status = str(data.get('eventStatus') or '').rsplit('/',1)[-1]
        if status in {'EventCancelled','EventPostponed','EventRescheduled'}:
            availability = 'cancelled' if status == 'EventCancelled' else 'rescheduled'
        # Only the session's price block counts, never advertisements or other sessions.
        price_block = soup.select_one('.price_line p')
        price_text = price_block.get_text(' ',strip=True).replace('\xa0',' ') if price_block else ''
        if re.search(r'\b(?:билетов нет|нет билетов|билеты закончились|все билеты проданы|свободных мест нет|нет свободных мест)\b', price_text, re.I):
            availability = 'sold_out'
        low = _number(offers.get('lowPrice', offers.get('price'))) if offers.get('priceCurrency') == 'RUB' else None
        high = _number(offers.get('highPrice')) if offers.get('priceCurrency') == 'RUB' else None
        span = re.search(r'(\d[\d ]*)\s*[—–-]\s*(\d[\d ]*)', price_text)
        if span and offers.get('priceCurrency') == 'RUB':
            low, high = (_number(part.replace(' ','')) for part in span.groups())
        if low is not None and high is not None and high < low:
            high = None
        image = data.get('image')
        if isinstance(image,list):
            image = image[0] if image else None
        image_parts = urlsplit(image) if isinstance(image,str) else None
        if not image_parts or (image_parts.scheme != 'https' or image_parts.hostname != urlsplit(source_url).hostname
                               or not image_parts.path.startswith('/media/') or image_parts.username
                               or image_parts.password or image_parts.port not in {None,443}):
            image = None
        return {'title':clean_text(data['name']), 'city':city, 'venue_kind':kind, 'venue_aliases':aliases,
                'date_start':start.isoformat(), 'date_end':end.isoformat() if end else None,
                'date_end_utc':end.astimezone(timezone.utc).isoformat(timespec='microseconds') if end else None,
                'source_url':source_url, 'availability':availability, 'image_url':image,
                'price_min':low, 'price_max':high}
    return None
