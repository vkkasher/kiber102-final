import hashlib

import pytest


def test_review_is_agent_evidence_and_never_claims_human_acceptance(tmp_path):
    from scripts.review_source_quality import review
    raw = '<h1>Декабрь 2026</h1><div class="item content"><div class="date">1 декабря</div><div class="name"><a href="/repertoire/opera/1/">TEST_DATA</a></div><div class="hall">Большой зал, 19:00</div></div>'
    digest = hashlib.sha256(raw.encode()).hexdigest()
    (tmp_path / (digest + '.raw')).write_bytes(raw.encode())
    annotations = {'review_type': 'agent_reviewed', 'human_verified': False, 'sources': {
        'test': {'adapter': 'bashopera', 'url': 'https://www.bashopera.ru/affiche/dekabr-2026/', 'raw_sha256': digest}},
        'cards': [{'source': 'test', 'locator': {'selector': '.item.content', 'index': 0},
                   'expected': {'title': 'TEST_DATA', 'date_start': '2026-12-01T19:00:00+05:00', 'price_min': None}}]}
    result = review(annotations, tmp_path)
    assert result['cards_checked'] == result['cards_exact_match'] == 1
    assert result['agent_reviewed_card_precision'] == 1
    assert result['official_human_verified_precision'] is None
    assert result['human_verified'] is False
    (tmp_path / (digest + '.raw')).write_bytes(b'tampered')
    with pytest.raises(ValueError, match='SHA256'):
        review(annotations, tmp_path)
