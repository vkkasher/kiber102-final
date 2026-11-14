"""Seed only the explicitly isolated loopback browser acceptance API."""
import json
import os

import httpx

if os.environ.get('ISOLATED_WEB_E2E') != '1' or os.environ.get('API_TEST_URL') != 'http://127.0.0.1:8100':
    raise SystemExit('Requires ISOLATED_WEB_E2E=1 and API_TEST_URL=http://127.0.0.1:8100. Never run against the public catalog.')
password = os.environ.get('ADMIN_PASSWORD')
if not password:
    raise SystemExit('Set the isolated ADMIN_PASSWORD first')
with httpx.Client(base_url=os.environ['API_TEST_URL'], timeout=30) as client:
    login = client.post('/api/v1/admin/login', json={'password': password})
    login.raise_for_status()
    headers = {'Authorization': 'Bearer ' + login.json()['token']}
    rows = [{'title': f'TEST_DATA Изолированный сеанс {i:03}', 'category': 'спектакль',
             'city': 'Уфа', 'venue': 'TEST_DATA Тестовая площадка', 'address': 'TEST_DATA Только изолированная база',
             'date_start': f'2027-01-{1+i//8:02}T{10+i%8:02}:00:00+05:00',
             'source_url': f'https://example.org/test-only/{i}', 'status': 'draft',
             'price_min': 100, 'price_max': 100, 'age_limit': 6, 'duration_min': 60,
             'description': 'TEST_DATA Синтетические данные только для изолированной приёмки. Не публиковать в рабочем каталоге.'}
            for i in range(105)]
    result = client.post('/api/v1/admin/import', headers=headers,
                         json={'format': 'json', 'content': json.dumps(rows, ensure_ascii=False)})
    result.raise_for_status()
    for i in (102, 103, 104):
        listing = client.get('/api/v1/admin/events', headers=headers,
                             params={'q': f'TEST_DATA Изолированный сеанс {i:03}'})
        listing.raise_for_status()
        event = listing.json()['items'][0]
        verified = client.post(f"/api/v1/admin/events/{event['id']}/verify", headers=headers,
                               json={'fetch': False, 'note': 'TEST_DATA fixture attestation for isolated browser QA only'})
        verified.raise_for_status()
        published = client.patch(f"/api/v1/admin/events/{event['id']}", headers=headers, json={'status': 'published'})
        published.raise_for_status()
    print('Isolated TEST_DATA fixture ready: 105 base sessions; 3 published only on the dedicated test server.')
