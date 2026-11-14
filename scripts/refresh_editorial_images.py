"""Refresh source-linked editorial photographs without changing the event database."""
import concurrent.futures
import datetime
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]


def extract(event):
    url = event.get('org_link')
    if not url or urlparse(url).hostname not in {'rusdramteatr.ru', 'www.bashopera.ru'}:
        return None
    try:
        source = re.sub(r'(/repertoire/[^/]+/\d+)/\d+/$', r'\1/', url)
        response = httpx.get(source, timeout=12, follow_redirects=True)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        def normalize(value):
            return re.sub(r'[^\w]', '', value.casefold().replace('ё', 'е'))
        expected_title = normalize(event['title'])
        source_title = normalize(soup.title.get_text(' ', strip=True) if soup.title else '')
        if not expected_title or expected_title not in source_title:
            return None
        image = soup.select_one('img[src^="/images/repertuar/"]')
        gallery = soup.select_one('a.image.m-lightbox[data-caption]')
        image_path = gallery.get('href') if gallery else image.get('src') if image else None
        if not image_path:
            return None
        image_url = urljoin(source, image_path)
        if urlparse(image_url).hostname != urlparse(url).hostname:
            return None
        return url, {'url': image_url, 'source': source,
                     'caption': 'Изображение · сайт театра',
                     'fetched_at': datetime.datetime.now(datetime.UTC).isoformat()}
    except (httpx.HTTPError, ValueError):
        return None


def main():
    response = httpx.get('http://127.0.0.1:8000/api/v1/events?page_size=100', timeout=10)
    response.raise_for_status()
    events = {e['org_link']: e for e in response.json()['items'] if e.get('org_link')}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        images = dict(result for result in pool.map(extract, events.values()) if result)
    destination = ROOT / 'apps/web/src/lib/editorial-images.json'
    destination.write_text(json.dumps(images, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Collected {len(images)} source-linked photographs; database unchanged.')


if __name__ == '__main__':
    main()
