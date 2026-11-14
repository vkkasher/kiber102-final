"""PRO.Культура export 2.5 contract. Requires a partner key; no access bypass.

Schema inspected at https://pro.culture.ru/new/api/documentation/export.
Images are deliberately omitted until the partner grants publication rights.
"""
import json
from datetime import datetime, timezone

from apps.api.app.schemas import CITIES, clean_text


def timestamp(value):
    # Export schema uses UTC milliseconds, never host-local or guessed dates.
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError('Нет даты UTC в миллисекундах')
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()


class PROCultureAdapter:
    def parse(self, raw: str, source_url: str) -> list[dict]:
        data = json.loads(raw)
        records = []
        for event in data.get('events', []):
            if event.get('status') != 'accepted' or event.get('isPublished') is False:
                continue
            places = event.get('places') or []
            # Explicit sessions carry their own placeIndex. No imagined visit slots.
            seances = event.get('seances') or [{'start': event.get('start'), 'end': event.get('end'), 'placeIndex': 0}]
            for seance in seances:
                try:
                    place = places[seance.get('placeIndex', 0)]
                    address = place.get('address') or {}
                    region = (address.get('region') or {}).get('name', '')
                    city = (address.get('city') or address.get('settlement') or {}).get('name', '')
                    if 'башкортостан' not in region.casefold() or city not in CITIES:
                        continue
                    start = timestamp(seance['start'])
                    end = timestamp(seance['end']) if seance.get('end') is not None else None
                    if end and datetime.fromisoformat(end) <= datetime.fromisoformat(start):
                        continue
                    category = (event.get('category') or {}).get('sysName')
                    names = {'spektakli': 'спектакль', 'koncerty': 'концерт', 'vystavki': 'выставка',
                             'prazdniki': 'фестиваль', 'ekskursii': 'экскурсия', 'kino': 'кино'}
                    parts = [city]
                    for key in ['street', 'house']:
                        part = address.get(key) or {}
                        if part.get('name'):
                            parts.append(' '.join(str(part.get(k, '')) for k in ['type', 'name']).strip())
                    free = event.get('isFree') is True
                    # An event ID alone is not a session ID. Use only an explicit
                    # provider session identifier; otherwise conservative dedup applies.
                    session_id = seance.get('_id')
                    records.append({
                        'provider_id': f"{event['_id']}:{session_id}" if session_id is not None else None,
                        'title': event['name'], 'description': clean_text(event.get('description') or event['name']),
                        'date_start': start, 'date_end': end, 'city': city,
                        'venue': place.get('name') or ', '.join(parts), 'address': ', '.join(parts),
                        'category': names.get(category, 'другое'),
                        'age_limit': event.get('ageRestriction'),
                        'is_free': free, 'price_min': 0 if free else event.get('price'),
                        'price_max': 0 if free else event.get('maxPrice'),
                        'org_link': event.get('saleLink') or None,
                        'source_url': source_url, 'source_type': 'api', 'status': 'draft',
                    })
                except (ValueError, TypeError, KeyError, IndexError, OSError, OverflowError):
                    continue
        return records
