import io
import json
import logging

from fastapi.testclient import TestClient


def test_normal_requests_emit_json_without_level_override_or_duplicate_handlers(tmp_path):
    from apps.api.app.main import create_app
    app = create_app(f'sqlite:///{tmp_path / "logs.db"}')
    logger = logging.getLogger('kiber102.api')
    handlers = [handler for handler in logger.handlers if getattr(handler, '_kiber102_api_handler', False)]
    assert len(handlers) == 1
    assert logger.getEffectiveLevel() == logging.INFO
    assert logger.propagate is False
    another = create_app('sqlite://')
    another.state.engine.dispose()
    assert len([handler for handler in logger.handlers if getattr(handler, '_kiber102_api_handler', False)]) == 1
    stream = io.StringIO()
    handler = handlers[0]
    original = handler.setStream(stream)
    try:
        with TestClient(app) as client:
            response = client.get('/api/v1/events', params={'q': 'PRIVATE-QUERY-TEXT'},
                                  headers={'X-Request-ID': 'request-log-123456', 'Authorization': 'Bearer PRIVATE-CREDENTIAL'})
        assert response.status_code == 200
    finally:
        handler.setStream(original)
    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry['event'] == 'http_request' and entry['request_id'] == 'request-log-123456'
    assert entry['route'] == '/api/v1/events' and entry['status'] == 200
    assert set(entry) == {'event', 'request_id', 'method', 'route', 'status', 'duration_ms'}
    assert 'PRIVATE' not in lines[0] and '127.0.0.1' not in lines[0]
    assert logging.getLogger('uvicorn.access').disabled is True
