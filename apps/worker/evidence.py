"""Two-document fact bundle with immutable raw-page hashes and a strict URL scope."""
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


def prune_catalog_snapshots(cache_dir, cutoff):
    """Expire bodies in both storage formats, preserving metadata and other files."""
    deleted = 0
    for snapshot in Path(cache_dir).iterdir():
        if (re.fullmatch(r'[a-f0-9]{64}\.(?:raw|bundle\.json)', snapshot.name)
                and snapshot.is_file() and snapshot.stat().st_mtime < cutoff):
            snapshot.unlink()
            deleted += 1
    return deleted


def fetch_catalog_evidence(url, cache_dir, allowed_hosts, *, fetcher=None):
    from .sources import FetchResult, fetch_source
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname != 'rusdramteatr.ru' or parsed.path.rstrip('/') != '/afisha' or parsed.query:
        raise ValueError('Неподдерживаемый адрес составного источника')
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    fetcher = fetcher or fetch_source
    bodies, manifest = {}, []
    for address in [url, 'https://rusdramteatr.ru/bilety']:
        fetched = fetcher(address, cache_dir, allowed_hosts)
        body = fetched.body
        if fetched.status == 'not_modified' and fetched.sha256:
            snapshot = cache_dir / (fetched.sha256 + '.raw')
            raw = snapshot.read_bytes() if snapshot.is_file() else b''
            if hashlib.sha256(raw).hexdigest() != fetched.sha256:
                raise ValueError('Нарушен хеш исходного снимка')
            body = raw.decode(fetched.encoding, errors='replace')
        if fetched.status not in {'fetched', 'not_modified'} or not body or not fetched.sha256:
            raise ValueError('Составной источник не подтверждён: ' + fetched.status)
        bodies[address] = body
        if sum(len(item.encode('utf-8')) for item in bodies.values()) > 3_000_000:
            raise ValueError('Составной источник превышает лимит 3 МБ')
        manifest.append({'url': address, 'sha256': fetched.sha256, 'checked_at': fetched.checked_at,
                         'encoding': fetched.encoding})
    bundle = json.dumps({'format': 'catalog-evidence-v1', 'listing': bodies[url],
                         'documents': {'https://rusdramteatr.ru/bilety': bodies['https://rusdramteatr.ru/bilety']},
                         'manifest': manifest}, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(bundle.encode()).hexdigest()
    (cache_dir / (digest + '.bundle.json')).write_text(bundle, encoding='utf-8', newline='\n')
    return FetchResult(url, bundle, 'fetched', manifest[0]['checked_at'], digest)
