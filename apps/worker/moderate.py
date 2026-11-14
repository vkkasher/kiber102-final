"""Explicit local moderator action, separate from the automatic collector.

Every selected row is fetched and factually compared before publication.
"""
import argparse
import json
import os
import sys

import httpx
from dotenv import load_dotenv


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    load_dotenv('.env', encoding='utf-8-sig')
    parser = argparse.ArgumentParser(description='Проверка и явная публикация загруженной афиши')
    parser.add_argument('--approve', action='store_true', help='Явно опубликовать после проверки всех фактов')
    parser.add_argument('--source', help='Точный URL первоисточника для ограничения очереди')
    args = parser.parse_args()
    with httpx.Client(base_url=os.getenv('API_URL', 'http://127.0.0.1:8000'), timeout=30) as api:
        login = api.post('/api/v1/admin/login', json={'password': os.environ['ADMIN_PASSWORD']})
        login.raise_for_status()
        api.headers['Authorization'] = 'Bearer ' + login.json()['token']
        listing = api.get('/api/v1/admin/events', params={'status': 'draft', 'page_size': 1000})
        listing.raise_for_status()
        rows = [event for event in listing.json()['items'] if not args.source or event['source_url'] == args.source]
        for row in rows:
            if not args.approve:
                print(json.dumps({'id': row['id'], 'title': row['title'], 'date_start': row['date_start'],
                                  'source_url': row['source_url'], 'action': 'review_only'}, ensure_ascii=False))
                continue
            verified = api.post(f"/api/v1/admin/events/{row['id']}/verify", json={
                'fetch': True, 'note': 'Публикация для локальной демонстрации после сверки всех полей с актуальной афишей источника.'})
            if verified.status_code != 200:
                print(json.dumps({'id': row['id'], 'status': 'rejected', 'reason': verified.json().get('message')}, ensure_ascii=False))
                continue
            published = api.patch(f"/api/v1/admin/events/{row['id']}", json={'status': 'published'})
            published.raise_for_status()
            print(json.dumps({'id': row['id'], 'title': row['title'], 'status': 'published'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
