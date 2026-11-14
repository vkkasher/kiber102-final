"""ASGI entry point and isolated app factory used by integration tests."""
import logging
import json
import re
import os
import sys
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from uuid import uuid4
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.exceptions import HTTPException

from .models import AgentTrace, Base
from .routes import router
from .security import client_identity, sanitize_trace

logger = logging.getLogger('kiber102.api')


def configure_api_logging():
    """Emit JSON at runtime independently of the server's root log level."""
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(getattr(handler, '_kiber102_api_handler', False) for handler in logger.handlers):
        handler = logging.StreamHandler(sys.__stdout__ or sys.stdout)
        handler._kiber102_api_handler = True
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(handler)
    # Uvicorn's access formatter includes the raw query string. The structured
    # route-template record below is the only HTTP access log.
    logging.getLogger('uvicorn.access').disabled = True
    logging.getLogger('uvicorn.access').propagate = False


def create_app(database_url=None):
    configure_api_logging()
    url = database_url or os.getenv('DATABASE_URL', 'sqlite:///./kiber102.db')
    options = {'connect_args': {'check_same_thread': False, 'timeout': 30}} if url.startswith('sqlite') else {'pool_pre_ping': True}
    engine = create_engine(url, **options)
    factory = sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(application):
        # Also cover uvicorn.run(prebuilt_app), whose log config comes later.
        configure_api_logging()
        # PostgreSQL deployments migrate with Alembic before serving. SQLite is the local fallback.
        if url.startswith('sqlite') or os.getenv('AUTO_CREATE_SCHEMA') == '1':
            Base.metadata.create_all(engine)
            from .services import backfill_ticket_end_times
            with engine.begin() as connection:
                backfill_ticket_end_times(connection)
        yield
        engine.dispose()

    app = FastAPI(title='Афиша культуры Башкортостана', version='0.1.0', lifespan=lifespan, docs_url=None,
                  description='Независимый культурный каталог. Импорт требует проверки и модерации.')
    # Pinned local assets keep the public contract usable without a third-party CDN.
    app.mount('/api/static/docs', StaticFiles(directory=Path(__file__).resolve().parents[1] / 'static' / 'docs'), name='docs-assets')

    @app.get('/docs', include_in_schema=False)
    async def local_docs():
        return get_swagger_ui_html(openapi_url='/openapi.json', title=app.title + ' - Swagger UI',
                                   swagger_js_url='/api/static/docs/swagger-ui-bundle.js',
                                   swagger_css_url='/api/static/docs/swagger-ui.css',
                                   swagger_favicon_url='/api/static/docs/favicon-32x32.png')
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.admin_password = os.getenv('ADMIN_PASSWORD', '')
    app.state.session_secret = os.getenv('ADMIN_SESSION_SECRET', '')
    from .routing import configured_provider
    app.state.travel_provider = configured_provider()
    app.state.metrics = {'requests': 0, 'errors_5xx': 0, 'search_ms': deque(maxlen=2000)}
    app.state.rate_windows = defaultdict(deque)

    def persist_trace(trace):
        with factory() as session:
            row = AgentTrace(id=trace.get('trace_id') or str(uuid4()), payload=sanitize_trace(trace))
            session.add(row)
            session.commit()
    app.state.persist_agent_trace = persist_trace
    from .services import configure_agent_state, reserve_llm_usage
    configure_agent_state(app, factory)
    app.state.reserve_llm_usage = lambda identity_hash, reservation_cost, token_limit, currency: reserve_llm_usage(
        factory, identity_hash, reservation_cost, token_limit, currency)
    origins = [value.strip() for value in os.getenv('CORS_ORIGINS', 'http://localhost:3000,http://127.0.0.1:3000').split(',') if value.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
                       allow_methods=['GET', 'POST', 'PATCH', 'DELETE', 'OPTIONS'],
                       allow_headers=['Authorization', 'Content-Type', 'X-Request-ID', 'Idempotency-Key'])

    def error_body(request, status, message, details=None):
        return {'code': {401: 'unauthorized', 403: 'forbidden', 404: 'not_found', 409: 'conflict',
                         422: 'validation_error', 429: 'rate_limited', 503: 'unavailable'}.get(status, 'request_error'),
                'message': message, 'details': details, 'request_id': getattr(request.state, 'request_id', str(uuid4()))}

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        detail = 'Страница не найдена' if error.status_code == 404 and error.detail == 'Not Found' else error.detail
        return JSONResponse(error_body(request, error.status_code,
                            detail if isinstance(detail, str) else 'Некорректный запрос',
                            detail if not isinstance(detail, str) else None), status_code=error.status_code,
                            headers=error.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        details = [{'field': '.'.join(map(str, item['loc'])), 'message': item['msg']} for item in error.errors()]
        return JSONResponse(error_body(request, 422, 'Проверьте поля запроса', details), status_code=422)

    @app.middleware('http')
    async def request_context(request: Request, call_next):
        supplied_id = request.headers.get('x-request-id', '')
        request.state.request_id = supplied_id if re.fullmatch(r'[A-Za-z0-9_-]{8,80}', supplied_id) else str(uuid4())
        started = time.monotonic()
        app.state.metrics['requests'] += 1
        path = request.url.path

        async def handle():
            try:
                request.state.identity_hash = client_identity(request)
            except HTTPException as error:
                return JSONResponse(error_body(request, error.status_code, error.detail), status_code=error.status_code)
            try:
                content_length = int(request.headers.get('content-length', '0') or 0)
                if content_length < 0:
                    raise ValueError('negative length')
            except ValueError:
                return JSONResponse(error_body(request, 400, 'Некорректная длина запроса'), status_code=400)
            if content_length > 5_500_000:
                return JSONResponse(error_body(request, 413, 'Запрос превышает лимит'), status_code=413)
            bucket = ('login' if path.endswith('/admin/login') else
                      'write' if request.method in {'POST', 'PATCH', 'DELETE'} and '/admin/' not in path else 'read')
            key = (request.state.identity_hash, bucket)
            # Evict only inactive identities. Full active capacity fails closed.
            if key not in app.state.rate_windows and len(app.state.rate_windows) >= 10000:
                stale = [item for item, values in app.state.rate_windows.items() if not values or values[-1] < started - 60]
                for item in stale:
                    del app.state.rate_windows[item]
                if len(app.state.rate_windows) >= 10000:
                    return JSONResponse(error_body(request, 429, 'Сервис занят. Повторите через минуту'), status_code=429)
            window = app.state.rate_windows[key]
            while window and window[0] < started - 60:
                window.popleft()
            limit = {'login': 10, 'write': 60, 'read': 600}[bucket]
            if len(window) >= limit:
                return JSONResponse(error_body(request, 429, 'Слишком много запросов. Повторите через минуту'),
                                    status_code=429, headers={'Retry-After': '60'})
            window.append(started)
            # Content-Length is advisory. Bound actual streamed bytes before JSON parsing.
            if request.method in {'POST', 'PATCH', 'PUT', 'DELETE'}:
                body = bytearray()
                async for chunk in request.stream():
                    if len(body) + len(chunk) > 5_500_000:
                        return JSONResponse(error_body(request, 413, 'Запрос превышает лимит'), status_code=413)
                    body.extend(chunk)
                request._body = bytes(body)
            return await call_next(request)

        try:
            response = await handle()
        except Exception as error:
            # Exception text can contain imported data or credentials. Log only its type.
            logger.error(json.dumps({'event': 'request_failed', 'request_id': request.state.request_id,
                                     'error_type': type(error).__name__}))
            response = JSONResponse(error_body(request, 500, 'Внутренняя ошибка сервиса'), status_code=500)
        elapsed = round((time.monotonic() - started) * 1000, 3)
        if response.status_code >= 500:
            app.state.metrics['errors_5xx'] += 1
        if path == '/api/v1/events':
            app.state.metrics['search_ms'].append(elapsed)
        # Log route templates, never raw URL segments, queries, bodies or identifiers.
        route = request.scope.get('route')
        route_path = getattr(route, 'path', '/unmatched')
        logger.info(json.dumps({'event': 'http_request', 'request_id': request.state.request_id,
                                'method': request.method, 'route': route_path,
                                'status': response.status_code, 'duration_ms': elapsed}))
        response.headers['X-Request-ID'] = request.state.request_id
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        return response

    app.include_router(router)
    from .agent import router as agent_router
    app.include_router(agent_router)
    return app


app = create_app()
