"""Fetch configured public sources into the moderation queue. Never invent events."""
from __future__ import annotations

import argparse
import hashlib
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import threading
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv

from .sources import SourceRegistry, fetch_source
from .pagination import collect_pages

MONTH_SLUGS = ['yanvar', 'fevral', 'mart', 'aprel', 'may', 'iyun', 'iyul', 'avgust',
               'sentyabr', 'oktyabr', 'noyabr', 'dekabr']


def scheduled_sources(configuration: dict, local_now: datetime | None = None) -> list[dict]:
    current = local_now or datetime.now(ZoneInfo('Asia/Yekaterinburg'))
    result = []
    for item in configuration['sources']:
        if not item.get('url_template'):
            result.append(item)
            continue
        for offset in item.get('month_offsets', [0]):
            if not isinstance(offset, int) or not 0 <= offset <= 12:
                raise ValueError('Смещение месяца источника должно быть от 0 до 12')
            year, month = divmod(current.year * 12 + current.month - 1 + offset, 12)
            result.append(item | {'url': item['url_template'].format(month=MONTH_SLUGS[month], year=year)})
    return result


def source_enabled(config: dict, states: list[dict]) -> bool:
    """A moderator's blocked/disabled decision overrides a local collection schedule."""
    def host(value):
        return (urlsplit(value).hostname or '').lower().removeprefix('www.')
    demo_enabled = (config.get('id') == 'sterlitamak' and config.get('demo_only')
                    and os.getenv('SOURCE_ENABLE_STERLITAMAK_DEMO') == '1')
    return bool(config.get('enabled') or demo_enabled) and not any(
        host(row['url']) == host(config['url']) and row['status'] in {'blocked', 'disabled'} for row in states)


def reconcile_published(api: httpx.Client, source_url: str, records: list[dict]) -> dict:
    """Only a successful source fetch may refresh or quarantine existing cards."""
    from apps.api.app.schemas import normalize

    def identity(event):
        return normalize(event['title']), datetime.fromisoformat(event['date_start'].replace('Z', '+00:00')).astimezone(timezone.utc)

    current = {identity(event): event for event in records}
    result = {'refreshed': 0, 'quarantined': 0, 'cancelled': 0}
    page = 1
    snapshot = []
    while True:
        response = api.get('/api/v1/admin/events', params={'page_size': 1000, 'page': page})
        response.raise_for_status()
        rows = response.json()['items']
        snapshot.extend(rows)
        if len(rows) < 1000:
            break
        page += 1
    # Mutations change updated_at ordering. Snapshot all pages before editing anything.
    for row in snapshot:
        if row['status'] != 'published' or row['source_url'] != source_url:
            continue
        candidate = current.get(identity(row))
        if candidate is None:
            update = api.patch(f"/api/v1/admin/events/{row['id']}", json={'status': 'draft'})
            update.raise_for_status()
            result['quarantined'] += 1
        elif candidate.get('status') in {'cancelled', 'отменено'}:
            update = api.patch(f"/api/v1/admin/events/{row['id']}", json={'status': 'cancelled'})
            update.raise_for_status()
            result['cancelled'] += 1
        else:
            verified = api.post(f"/api/v1/admin/events/{row['id']}/verify", json={
                'fetch': True, 'note': 'Плановая повторная сверка всех полей с успешно загруженным первоисточником.'})
            if verified.status_code == 200:
                result['refreshed'] += 1
            else:
                update = api.patch(f"/api/v1/admin/events/{row['id']}", json={'status': 'draft'})
                update.raise_for_status()
                result['quarantined'] += 1
    return result


def sync_once(config_path: Path | None = None) -> dict:
    config_path = config_path or Path(os.getenv('SOURCES_CONFIG', str(Path(__file__).with_name('sources.json'))))
    registry = SourceRegistry()
    configuration = json.loads(config_path.read_text(encoding='utf8'))
    evidence_dir = Path(os.getenv('SOURCE_EVIDENCE_DIR', '.local/sources'))
    evidence_dir.mkdir(parents=True, exist_ok=True)
    allowed = {host.strip() for host in os.getenv('SOURCE_ALLOWED_HOSTS',
               'www.bashopera.ru,bashopera.ru,rusdram.ru,api.timepad.ru,pro.culture.ru,rusdramteatr.ru,ufa.kassy.ru,sterlitamak.kassy.ru').split(',')}
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'sources': [], 'registry': registry.status()}
    password = os.getenv('WORKER_ADMIN_PASSWORD') or os.getenv('ADMIN_PASSWORD')
    if not password:
        report['error'] = 'ADMIN_PASSWORD не задан. Импорт отключён.'
        return report
    with httpx.Client(base_url=os.getenv('API_URL', 'http://127.0.0.1:8000'), timeout=20) as api:
        login = api.post('/api/v1/admin/login', json={'username': os.getenv('WORKER_ADMIN_USERNAME', 'admin'), 'password': password})
        login.raise_for_status()
        api.headers['Authorization'] = 'Bearer ' + login.json()['token']
        maintenance = api.post('/api/v1/admin/maintenance')
        maintenance.raise_for_status()
        report['retention'] = maintenance.json()
        source_states = api.get('/api/v1/admin/sources')
        source_states.raise_for_status()
        cutoff = datetime.now(timezone.utc).timestamp() - 90 * 86400
        from .evidence import prune_catalog_snapshots
        prune_catalog_snapshots(evidence_dir, cutoff)
        normalized_aliases = set()
        for config in scheduled_sources(configuration):
            source_id = config['id']
            if not source_enabled(config, source_states.json()) or source_id not in registry.adapters:
                status = registry.status().get(source_id, 'disabled')
                if status == 'configured':
                    status = 'disabled'
                report['sources'].append({'id': source_id, 'status': status})
                continue
            try:
                def fetch(url):
                    if source_id == 'sterlitamak':
                        from .evidence import fetch_catalog_evidence
                        return fetch_catalog_evidence(url, evidence_dir, allowed)
                    return fetch_source(url, evidence_dir, allowed,
                                        token=os.getenv('TIMEPAD_API_TOKEN') if source_id == 'timepad' else None,
                                        api_key=os.getenv('PROCULTURE_API_KEY') if source_id == 'proculture' else None)

                def read_body(result):
                    if result.body is not None:
                        return result.body
                    snapshot = evidence_dir / f'{result.sha256}.raw'
                    if result.status == 'not_modified' and snapshot.exists():
                        return snapshot.read_bytes().decode(result.encoding, errors='replace')
                    return None

                fetched = fetch(config['url'])
                item = {'id': source_id, **asdict(fetched)}
                item.pop('body')
                body = read_body(fetched)
                complete = True
                if body and source_id in {'timepad', 'proculture'}:
                    combined, hashes, complete = collect_pages(config['url'], fetched, fetch, read_body)
                    body = json.dumps(combined, ensure_ascii=False) if combined else None
                    item['pages'] = hashes
                if body:
                    records = registry.adapters[source_id].parse(body, config['url'])
                    # Keep normalization separately from immutable raw source snapshots.
                    normalized = json.dumps({'source': config, 'checked_at': fetched.checked_at,
                                             'raw_sha256': fetched.sha256, 'events': records}, ensure_ascii=False, indent=2)
                    url_key = hashlib.sha256(config['url'].encode()).hexdigest()[:12]
                    (evidence_dir / f'{source_id}-{url_key}-normalized.json').write_text(normalized, encoding='utf8')
                    if source_id not in normalized_aliases:
                        (evidence_dir / f'{source_id}-normalized.json').write_text(normalized, encoding='utf8')
                        normalized_aliases.add(source_id)
                    future = [event for event in records if datetime.fromisoformat(event['date_start']) > datetime.now(timezone.utc)]
                    item['extracted'] = len(records)
                    item['future'] = len(future)
                    if future:
                        result = api.post('/api/v1/admin/import', json={'format': 'json', 'content': json.dumps(future, ensure_ascii=False)})
                        result.raise_for_status()
                        item['import'] = result.json()
                    # A changed/empty parser must never silently erase the catalogue.
                    # Quarantine is conservative; no cancellation is inferred from absence.
                    # Partial API pages cannot prove that unseen sessions disappeared.
                    api_partial = not complete
                    if records and not api_partial:
                        item['reconciliation'] = reconcile_published(api, config['url'], records)
                    elif api_partial:
                        item['status'] = 'partial_page_requires_pagination'
                    else:
                        item['status'] = 'parser_empty_requires_review'
                report['sources'].append(item)
            except (httpx.HTTPError, ValueError, OSError, KeyError) as exc:
                report['sources'].append({'id': source_id, 'status': 'error', 'error_type': type(exc).__name__})
        if configuration.get('ticket_enrichment',{}).get('enabled'):
            from .kassy import sync_ticket_catalog
            report['ticket_evidence'] = sync_ticket_catalog(api,evidence_dir,allowed)
        queued = api.post('/api/v1/admin/notifications/enqueue')
        queued.raise_for_status()
        report['notifications'] = queued.json()
    (evidence_dir / 'last-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    return report


def main():
    load_dotenv(encoding='utf-8-sig')
    parser = argparse.ArgumentParser(description='Сбор источников в очередь модерации')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    shutdown = threading.Event()
    for signum in [signal.SIGINT, signal.SIGTERM]:
        signal.signal(signum, lambda *_: shutdown.set())
    while not shutdown.is_set():
        interval = max(300, int(os.getenv('WORKER_INTERVAL_SECONDS', '3600')))
        delay = interval
        try:
            report = sync_once()
            print(json.dumps({k: v for k, v in report.items() if k != 'sources'}, ensure_ascii=False), flush=True)
            for item in report.get('sources', []):
                print(json.dumps({'source': item['id'], 'status': item['status'],
                                  'future': item.get('future'), 'import': {k: v for k, v in item.get('import', {}).items() if k != 'details'}}, ensure_ascii=False), flush=True)
        except (httpx.HTTPError, ValueError, OSError) as exc:
            print(json.dumps({'status': 'sync_failed', 'error_type': type(exc).__name__}), flush=True)
            # API startup or a temporary outage must not postpone the first
            # successful collection for an entire hourly interval.
            delay = min(60, interval)
        if args.once:
            break
        shutdown.wait(delay)


if __name__ == '__main__':
    main()
