"""Warm up the local GPU before user requests; no user data or remote provider."""
import json
from pathlib import Path
from time import perf_counter

import httpx


def main():
    started = perf_counter()
    response = httpx.post('http://127.0.0.1:18080/v1/chat/completions', timeout=45, trust_env=False,
                          json={'model': 'kiber102-local', 'temperature': 0, 'max_tokens': 16,
                                'response_format': {'type': 'json_object'},
                                'chat_template_kwargs': {'enable_thinking': False},
                                'messages': [{'role': 'user', 'content': 'Return only JSON: {"ready":true}'}]})
    response.raise_for_status()
    result = response.json()
    if json.loads(result['choices'][0]['message']['content']) != {'ready': True}:
        raise ValueError('Unexpected local model readiness response')
    output = Path(__file__).resolve().parents[1] / '.local' / 'llm-runtime' / 'warmup.json'
    output.write_text(json.dumps({'elapsed_s': perf_counter() - started, 'model': result.get('model'),
                                  'tokens': result.get('usage'), 'api_cost_rub': 0}), encoding='utf8')


if __name__ == '__main__':
    main()
