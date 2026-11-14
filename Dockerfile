FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --create-home app && mkdir -p /app/.local && chown -R app:app /app
COPY requirements.txt requirements.lock.txt ./
RUN pip install --no-cache-dir --default-timeout=100 --retries=5 --prefer-binary -r requirements.lock.txt
COPY alembic.ini ./
COPY apps/api ./apps/api
COPY apps/worker ./apps/worker
COPY apps/bot ./apps/bot
COPY apps/dialogue.py ./apps/dialogue.py
COPY apps/web/src/lib/editorial-images.json ./apps/web/src/lib/editorial-images.json
COPY apps/web/public/editorial ./apps/web/public/editorial
RUN chown -R app:app /app
USER app
CMD ["sh", "-c", "python -c \"import os, socket, time, urllib.parse as u; r = os.getenv('DATABASE_URL', ''); h = u.urlsplit(r.replace('+psycopg', '')).hostname if 'postgres' in r else None; p = u.urlsplit(r.replace('+psycopg', '')).port or 5432 if h else 5432; any(True for _ in range(30) if not h or socket.socket().connect_ex((h, p)) == 0 or time.sleep(1))\" && alembic upgrade head && exec uvicorn apps.api.app.main:app --host 0.0.0.0 --port 8000 --no-access-log"]
