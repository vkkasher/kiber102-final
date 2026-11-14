import hashlib
import json

import pytest


def test_bundle_keeps_each_source_hash_and_replays_same_fact_parser(tmp_path):
    from apps.worker.evidence import fetch_catalog_evidence
    from apps.worker.sources import FetchResult
    pages = {'https://rusdramteatr.ru/afisha': '<html>TEST_DATA афиша</html>',
             'https://rusdramteatr.ru/bilety': '<html>TEST_DATA тарифы</html>'}
    called = []

    def fetch(url, directory, hosts):
        called.append(url)
        body = pages[url]
        digest = hashlib.sha256(body.encode()).hexdigest()
        (directory / f'{digest}.raw').write_bytes(body.encode())
        return FetchResult(url, body, 'fetched', '2026-11-13T12:00:00+00:00', digest)
    result = fetch_catalog_evidence('https://rusdramteatr.ru/afisha', tmp_path, {'rusdramteatr.ru'}, fetcher=fetch)
    bundle = json.loads(result.body)
    assert called == list(pages)
    assert bundle['listing'] == pages[called[0]]
    assert bundle['documents'] == {called[1]: pages[called[1]]}
    assert all(hashlib.sha256((tmp_path / (page['sha256'] + '.raw')).read_bytes()).hexdigest() == page['sha256'] for page in bundle['manifest'])
    saved = tmp_path / (result.sha256 + '.bundle.json')
    assert saved.read_text(encoding='utf-8') == result.body


def test_evidence_refuses_scope_expansion_and_unavailable_required_tariffs(tmp_path):
    from apps.worker.evidence import fetch_catalog_evidence
    from apps.worker.sources import FetchResult
    with pytest.raises(ValueError, match='адрес'):
        fetch_catalog_evidence('https://evil.example/afisha', tmp_path, {'evil.example'})

    def failed(url, directory, hosts):
        return FetchResult(url, None, 'robots_disallowed', '2026-11-13T12:00:00+00:00')
    with pytest.raises(ValueError, match='источник'):
        fetch_catalog_evidence('https://rusdramteatr.ru/afisha', tmp_path, {'rusdramteatr.ru'}, fetcher=failed)


def test_snapshot_retention_expires_raw_and_composite_bodies_only(tmp_path):
    import os
    from apps.worker.evidence import prune_catalog_snapshots
    expired = [tmp_path / ('a' * 64 + '.raw'), tmp_path / ('b' * 64 + '.bundle.json')]
    retained = [tmp_path / ('c' * 64 + '.raw'), tmp_path / 'notes.bundle.json', tmp_path / 'metadata.json']
    for path in expired + retained:
        path.write_text('TEST_DATA immutable source', encoding='utf-8')
        os.utime(path, (10, 10))
    os.utime(retained[0], (100, 100))
    assert prune_catalog_snapshots(tmp_path, 50) == 2
    assert all(not path.exists() for path in expired)
    assert all(path.exists() for path in retained)
