"""Recheck separately transcribed, agent-reviewed source labels against adapters.

This is an auditable extraction check, not the PDF's independent human evaluation.
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def review(annotations, evidence_dir):
    from apps.api.app.schemas import EventInput, normalize
    from apps.worker.sources import SourceRegistry
    registry = SourceRegistry()
    parsed, hashes = {}, {}
    for name, source in annotations['sources'].items():
        def read(digest):
            raw = (Path(evidence_dir) / (digest + '.raw')).read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError('SHA256 не совпадает для исходного снимка: ' + digest)
            hashes[digest] = True
            return raw.decode(source.get('encoding', 'utf-8'), errors='replace')
        raw = read(source['raw_sha256'])
        if source.get('documents'):
            raw = json.dumps({'format': 'catalog-evidence-v1', 'listing': raw,
                              'documents': {url: read(digest) for url, digest in source['documents'].items()}}, ensure_ascii=False)
        records = registry.adapters[source['adapter']].parse(raw, source['url'])
        parsed[name] = [EventInput.model_validate(row).model_dump(mode='json') for row in records]

    def comparable(field, value):
        if field in {'date_start', 'date_end'} and value is not None:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc).isoformat()
        return value

    checked, exact, fields, matching_fields = [], 0, 0, 0
    seen, duplicates = set(), 0
    for index, card in enumerate(annotations['cards']):
        expected = card['expected']
        candidates = [row for row in parsed[card['source']] if row['title'] == expected['title']
                      and comparable('date_start', row['date_start']) == comparable('date_start', expected['date_start'])]
        mismatches = []
        for field, value in expected.items():
            fields += 1
            if len(candidates) != 1 or comparable(field, candidates[0].get(field)) != comparable(field, value):
                mismatches.append(field)
            else:
                matching_fields += 1
        if not mismatches:
            exact += 1
        key = (normalize(expected['title']), comparable('date_start', expected['date_start']),
               normalize(expected.get('venue', '')), expected.get('city'))
        if key in seen:
            duplicates += 1
        seen.add(key)
        checked.append({'index': index, 'source': card['source'], 'title': expected['title'],
                        'locator': card['locator'], 'matched': not mismatches, 'mismatched_fields': mismatches})
    total = len(checked)
    return {'checked_at': datetime.now(timezone.utc).isoformat(), 'review_type': 'agent_reviewed',
            'human_verified': False, 'official_human_verified_precision': None,
            'official_human_verified_sample_size': 0,
            'method': 'Separately transcribed source facts, source SHA256 verification, deterministic adapter comparison. Agent review is not independent human acceptance.',
            'cards_checked': total, 'cards_exact_match': exact,
            'agent_reviewed_card_precision': exact / total if total else None,
            'fields_checked': fields, 'fields_match': matching_fields,
            'agent_reviewed_field_precision': matching_fields / fields if fields else None,
            'sample_duplicates': duplicates, 'sample_duplicate_rate': duplicates / total if total else None,
            'source_hashes_verified': sorted(hashes), 'cards': checked}


def main():
    default_evidence = (
        ROOT / 'docs' / 'evidence' / 'sources'
        if (ROOT / 'docs' / 'evidence' / 'sources').is_dir()
        else ROOT / '.local' / 'source-quality'
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--labels', type=Path, default=ROOT / 'docs' / 'data' / 'source-labels.json')
    parser.add_argument('--evidence-dir', type=Path, default=default_evidence)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs' / 'evidence' / 'source-quality.json')
    args = parser.parse_args()
    result = review(json.loads(args.labels.read_text(encoding='utf-8')), args.evidence_dir)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: result[key] for key in ['cards_checked', 'cards_exact_match', 'fields_checked',
                                                'fields_match', 'human_verified', 'official_human_verified_precision']}))
    return 0 if result['cards_checked'] >= 50 and result['cards_exact_match'] == result['cards_checked'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
