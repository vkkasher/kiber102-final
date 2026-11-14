"""Fact-only regional source parser, with explicit performance tariff provenance.

No artwork or copyrighted synopses are copied. Reuse permission remains a source
configuration decision, separate from robots.txt and parser correctness.
"""
import json
import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from apps.api.app.schemas import normalize


def duration_minutes(text):
    value = text.casefold().replace('ё', 'е')
    if re.search(r'без\s+учета|\bот\s+\d|\d\s*[-–]\s*\d', value):
        return None
    hours = re.search(r'(?<!\d)(\d{1,2})\s*ч(?:\.|ас(?:а|ов)?)?(?=\s|\d|$)', value)
    minutes = re.search(r'(?<!\d)(\d{1,3})\s*мин(?:\.|ут(?:а|ы)?)?(?=[\s.,;:)]|$)', value)
    if not hours and not minutes:
        return None
    result = (int(hours[1]) * 60 if hours else 0) + (int(minutes[1]) if minutes else 0)
    return result if 1 <= result <= 10080 else None


def tariff_ranges(raw):
    """Associate only named-title headings with explicitly printed ruble tariffs."""
    soup = BeautifulSoup(raw, 'html.parser')
    result = {}
    for heading in soup.find_all(['h2', 'h3']):
        names = re.findall(r'[«"]([^»"]+)[»"]', heading.get_text(' ', strip=True))
        if not names:
            continue
        texts = []
        button = heading.find_parent(attrs={'aria-controls': True})
        panel = soup.find(id=button['aria-controls']) if button else None
        if panel is not None:
            texts.append(panel.get_text(' ', strip=True))
        else:
            for sibling in heading.next_siblings:
                if getattr(sibling, 'name', None) in {'h2', 'h3'}:
                    break
                if getattr(sibling, 'get_text', None):
                    texts.append(sibling.get_text(' ', strip=True))
        joined = ' '.join(texts).replace('\xa0', ' ')
        values = [int(re.sub(r'\s', '', item)) for item in re.findall(r'(\d[\d\s]*?)\s*(?:руб\.?|₽)', joined, re.I)]
        if not values:
            continue
        maximum = None if re.search(r'\bот\s+\d', joined, re.I) else max(values)
        for name in names:
            result[normalize(name)] = (min(values), maximum)
    return result


class SterlitamakAdapter:
    def parse(self, raw, source_url):
        documents = {}
        if raw.lstrip().startswith('{'):
            bundle = json.loads(raw)
            if bundle.get('format') != 'catalog-evidence-v1':
                raise ValueError('Неизвестный формат доказательств источника')
            raw, documents = bundle['listing'], bundle.get('documents', {})
        tariffs = tariff_ranges(documents.get('https://rusdramteatr.ru/bilety', ''))
        soup = BeautifulSoup(raw, 'html.parser')
        records = []
        for row in soup.select('.nspArt'):
            title = row.select_one('h2')
            date_line = row.select_one('.nspInfo')
            detail = row.select_one('a.nspImageWrapper[href]')
            text = row.get_text(' ', strip=True)
            if not title or not date_line or not detail or re.search(r'гастрол[ьи].*\bг[.\s]', text, re.I):
                continue
            path = urlsplit(urljoin(source_url, detail['href'])).path
            explicit_date = re.search(r'(\d{2})-(\d{2})-(20\d{2})(?:\D|$)', path)
            clock = re.search(r'(\d{2}):(\d{2})', date_line.get_text(' ', strip=True))
            if explicit_date is None or clock is None:
                continue
            age_tag = row.select_one('h5')
            age = re.fullmatch(r'(0|6|12|16|18)\+', age_tag.get_text(strip=True)) if age_tag else None
            duration = next((value for paragraph in row.find_all('p')
                             if len(paragraph.get_text(' ', strip=True)) < 120
                             and (value := duration_minutes(paragraph.get_text(' ', strip=True))) is not None), None)
            name = title.get_text(' ', strip=True)
            minimum, maximum = tariffs.get(normalize(name), (None, None))
            description = 'Сеанс по официальной афише театра.'
            if minimum is not None:
                description += (' Опубликованный тариф спектакля. Цену и наличие билетов конкретного сеанса '
                                'уточните у организатора. Источник тарифа: https://rusdramteatr.ru/bilety')
            records.append({
                # Booking widget IDs are demonstrably reused on different plays.
                # The official article URL is the only reliable session key here.
                'provider_id': path, 'title': name,
                'date_start': datetime(int(explicit_date[3]), int(explicit_date[2]), int(explicit_date[1]),
                                       int(clock[1]), int(clock[2])).isoformat() + '+05:00',
                'duration_min': duration, 'date_end': None,
                'category': 'концерт' if re.search(r'концерт', text, re.I) else 'спектакль',
                'city': 'Стерлитамак', 'venue': 'Государственный русский драматический театр г. Стерлитамак',
                'address': 'Стерлитамак, ул. Худайбердина, 18', 'description': description,
                'price_min': minimum, 'price_max': maximum, 'is_free': minimum == maximum == 0,
                'age_limit': int(age[1]) if age else None, 'org_link': urljoin(source_url, path),
                'source_url': source_url, 'source_type': 'website', 'status': 'draft',
            })
        return records
