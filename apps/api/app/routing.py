"""Optional OSRM driving estimates. Missing coordinates/network never become invented travel."""
from datetime import datetime, timezone
import math
import os
import threading
import time
from urllib.parse import urlsplit

import httpx


class OSRMTravelProvider:
    def __init__(self, base_url: str, *, transport=None):
        parsed = urlsplit(base_url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query:
            raise ValueError('OSRM_BASE_URL должен быть HTTPS-адресом без секретов')
        self.base_url = base_url.rstrip('/')
        self.transport = transport
        self.cache = {}
        self.lock = threading.Lock()

    def __call__(self, origin: dict, destination: dict) -> dict | None:
        points = []
        for event in (origin, destination):
            lat, lon = event.get('lat'), event.get('lon')
            if (not isinstance(lat, (int, float)) or isinstance(lat, bool)
                    or not isinstance(lon, (int, float)) or isinstance(lon, bool)
                    or not math.isfinite(lat) or not math.isfinite(lon)
                    or not -90 <= lat <= 90 or not -180 <= lon <= 180):
                return None
            points.append(f'{lon:.6f},{lat:.6f}')
        key = ';'.join(points)
        with self.lock:
            cached = self.cache.get(key)
            if cached and cached[0] > time.monotonic() - 3600:
                return cached[1]
        url = f'{self.base_url}/route/v1/driving/{key}'
        try:
            with httpx.Client(transport=self.transport, timeout=2, follow_redirects=False) as client:
                response = client.get(url, params={'overview': 'false', 'steps': 'false'},
                                      headers={'User-Agent': 'Kiber102Catalog/0.1'})
                response.raise_for_status()
                data = response.json()
            seconds = data['routes'][0]['duration'] if data.get('code') == 'Ok' else None
            if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
                return None
            result = {'minutes': math.ceil(seconds / 60), 'source': str(response.url),
                      'checked_at': datetime.now(timezone.utc).isoformat(), 'mode': 'driving',
                      'note': 'Оценка поездки на автомобиле по OpenStreetMap, без учёта пробок и парковки.'}
            with self.lock:
                if len(self.cache) >= 2000:
                    self.cache.clear()
                self.cache[key] = (time.monotonic(), result)
            return result
        except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError):
            return None


def configured_provider():
    endpoint = os.getenv('OSRM_BASE_URL', '').strip()
    return OSRMTravelProvider(endpoint) if endpoint else None
