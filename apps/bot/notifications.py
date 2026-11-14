"""Opt-in outbox delivery. Ambiguous Telegram sends are never retried blindly."""
from __future__ import annotations

import asyncio
import logging
import os

import httpx

from .service import event_card

logger = logging.getLogger('bot.notifications')


async def deliver_batch(client, send, token):
    headers = {'Authorization':f'Bearer {token}'}
    response = await client.post('/api/v1/admin/notifications/claim', headers=headers, json={'limit':20})
    response.raise_for_status()
    processed = 0
    for item in response.json()['items']:
        outcome, error = None, None
        try:
            await send(int(item['chat_id']), event_card(item['event']))
            outcome = True
        except Exception as exc:
            # These API replies prove no delivery. A timeout/connection reset
            # may follow a successful send, so leave its lease for unknown.
            if type(exc).__name__ in {'TelegramForbiddenError', 'TelegramBadRequest', 'TelegramRetryAfter'}:
                outcome, error = False, type(exc).__name__
            logger.warning('notification_send_failed type=%s', type(exc).__name__)
        if outcome is not None:
            try:
                ack = await client.post(f"/api/v1/admin/notifications/{item['id']}/ack", headers=headers,
                                        json={'lease_token':item['lease_token'], 'success':outcome, 'error':error})
                ack.raise_for_status()
            except httpx.HTTPError as exc:
                # No re-send. An unacknowledged lease becomes unknown.
                logger.warning('notification_ack_failed type=%s', type(exc).__name__)
        processed += 1
    return processed


async def delivery_loop(client, bot):
    if os.getenv('NOTIFICATIONS_ENABLED', '').lower() not in {'1', 'true', 'yes'}:
        return
    password = os.getenv('NOTIFICATION_ADMIN_PASSWORD') or os.getenv('ADMIN_PASSWORD')
    if not password:
        logger.warning('notification_delivery_disabled missing_service_credentials')
        return
    token = None
    while True:
        try:
            if token is None:
                response = await client.post('/api/v1/admin/login', json={
                    'username':os.getenv('NOTIFICATION_ADMIN_USERNAME', 'admin'), 'password':password})
                response.raise_for_status()
                token = response.json()['token']
            await deliver_batch(client, bot.send_message, token)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                token = None
            logger.warning('notification_transport_failed status=%d', exc.response.status_code)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            logger.warning('notification_transport_failed type=%s', type(exc).__name__)
        await asyncio.sleep(15)
