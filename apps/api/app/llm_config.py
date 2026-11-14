"""A local model may have zero API charges only on an explicit loopback endpoint."""
import os
from urllib.parse import urlsplit


def is_local_llm_config():
    if os.getenv('LLM_PROVIDER', '').lower() != 'local':
        return False
    try:
        url = urlsplit(os.getenv('LLM_BASE_URL', ''))
        return (url.scheme in {'http', 'https'}
                and url.hostname in {'127.0.0.1', 'localhost', '::1'}
                and url.username is None and url.password is None
                and not url.query and not url.fragment
                and url.path in {'/v1', '/v1/'}
                and (url.port is None or 1 <= url.port <= 65535))
    except ValueError:
        return False
