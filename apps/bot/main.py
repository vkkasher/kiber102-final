"""Run with python -m apps.bot.main. No token means no Telegram network calls."""
import asyncio
from contextlib import suppress
from html import escape
import logging
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

from .service import BotService, StateStore, event_card, event_links, prepare_rich_media, redacted_turn, rich_search_presentation, search_presentation


async def run() -> int:
    load_dotenv('.env', encoding='utf-8-sig')
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if not token:
        print("Telegram отключён: TELEGRAM_BOT_TOKEN не задан. Каталог и API доступны независимо от бота.")
        if os.getenv("BOT_STANDBY", "").lower() in ("1", "true", "yes"):
            print("Бот переведён в режим ожидания (BOT_STANDBY=true). Контейнер остаётся активным.")
            try:
                while True:
                    await asyncio.sleep(3600)
            except (asyncio.CancelledError, KeyboardInterrupt):
                return 0
        return 0
    from aiogram import Bot, Dispatcher, F
    from aiogram.client.default import DefaultBotProperties
    from aiogram.filters import Command
    from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
    from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

    api_url = os.getenv("API_URL") or os.getenv("API_INTERNAL_URL", "http://127.0.0.1:8000")
    api_url = api_url.rstrip('/')
    website = (os.getenv("WEB_PUBLIC_URL") or os.getenv("WEB_URL", "http://localhost:3000")).rstrip("/")
    store_path = os.getenv("BOT_STATE_PATH", str(Path(".local") / "bot.sqlite"))
    async with httpx.AsyncClient(base_url=api_url, timeout=10) as client:
        service = BotService(client, StateStore(store_path))
        bot = Bot(token, default=DefaultBotProperties(parse_mode="HTML"))
        dispatcher = Dispatcher()

        async def safe_answer(message, text, **kwargs):
            try:
                return await message.answer(text, **kwargs)
            except TelegramRetryAfter as exc:
                if exc.retry_after <= 5:
                    await asyncio.sleep(exc.retry_after)
                    try:
                        return await message.answer(text, **kwargs)
                    except TelegramAPIError:
                        return None
            except TelegramAPIError:
                return None

        def menu():
            return InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Новый поиск", callback_data="search")],
                [InlineKeyboardButton(text="Подписка", callback_data="subscribe"), InlineKeyboardButton(text="Помощь", callback_data="help")],
            ])

        async def card(message: Message, event: dict):
            buttons = [InlineKeyboardButton(text=label, url=url) for label, url in event_links(event)]
            # Telegram rejects localhost URLs. The permanent web link is enabled
            # only after configuring a public HTTPS deployment.
            if website.startswith("https://"):
                buttons.append(InlineKeyboardButton(text="Карточка на сайте", url=f"{website}/events/{event['id']}"))
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[button] for button in buttons]) if buttons else None
            await safe_answer(message, event_card(event), reply_markup=keyboard)

        async def show_result(message: Message, result: dict, editing=False):
            if result.get('events'):
                article = rich_search_presentation(result, website)
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(**button) for button in row] for row in article['buttons']])
                try:
                    prepared = await prepare_rich_media(article['rich_message'],website)
                    if editing:
                        await bot.edit_message_text(chat_id=message.chat.id,message_id=message.message_id,rich_message=prepared,reply_markup=keyboard)
                    else:
                        await bot.send_rich_message(chat_id=message.chat.id,
                            message_thread_id=message.message_thread_id,rich_message=prepared, reply_markup=keyboard)
                    return
                except TelegramBadRequest as exc:
                    logging.getLogger(__name__).warning('Telegram rejected rich message: %s',redacted_turn(exc.message)[:220])
                    # A rejected article has not been sent. Preserve the full
                    # search and working links if a source photo is unavailable.
                    pass
                except TelegramAPIError:
                    # Do not duplicate a possibly delivered article on timeout.
                    return
            view = search_presentation(result, website)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(**button) for button in row] for row in view['buttons']])
            if editing:
                await bot.edit_message_text(chat_id=message.chat.id,message_id=message.message_id,text=view['text'],reply_markup=keyboard,link_preview_options={'is_disabled':True})
            else:
                await safe_answer(message, view['text'], reply_markup=keyboard, link_preview_options={'is_disabled':True})

        async def search(message: Message, text: str):
            if not message.from_user:
                return
            try:
                result = await service.query(message.from_user.id, text)
                await show_result(message, result)
            except (httpx.HTTPError, ValueError):
                await message.answer("Сервис временно недоступен. Попробуйте ещё раз чуть позже.")

        async def subscription_prompt(message: Message, user_id: int, city: str | None = None):
            state = service.store.get(user_id).get("search", {}).get("filters", {})
            city = city or state.get("city")
            if not city:
                await message.answer("Сначала найдите события в своём городе или напишите /subscribe Уфа.")
                return
            pending = service.prepare_subscription(user_id, city, state.get("category"))
            label = escape(city + (", " + state["category"] if state.get("category") else ""))
            await message.answer(f"Сохранить подписку на {label}? Нажмите кнопку или напишите «Подтверждаю». После сохранения покажу статус доставки уведомлений.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Подтверждаю подписку", callback_data=f"confirm:{pending['nonce']}")]]))

        @dispatcher.message(Command("start"))
        async def start(message: Message):
            if message.from_user:
                await service.cancel(message.from_user.id)
            await safe_answer(message, "<b>Культурная афиша Башкортостана</b>\n\nНайдём событие по городу, дате и вашим планам. Напишите, например:\n<i>В Уфе с ребёнком 8 лет в субботу, до 1000 рублей на двоих.</i>\n\nПросто напишите новый запрос. Короткие уточнения «подешевле» или «на субботу» изменят текущую подборку.\n\n<i>Независимый проект, не официальный сервис министерства.</i>", reply_markup=menu())

        @dispatcher.message(Command("help"))
        async def help_message(message: Message):
            await message.answer("/search запрос или обычный текст для поиска\n/event ID для карточки\n/subscribe город для подписки с подтверждением\n/confirm или «Подтверждаю» для одного ожидающего действия\n/unsubscribe для удаления подписок\n/cancel для нового поиска\n\nУточнения «подешевле», «только бесплатные», «а ещё» сохраняют остальные условия. Время указано по Башкортостану. Стоимость сеанса проверяйте у организатора.")

        @dispatcher.message(Command('confirm'))
        async def confirm_message(message: Message):
            await search(message, '/confirm')

        @dispatcher.message(Command("cancel"))
        async def cancel(message: Message):
            if message.from_user:
                await service.cancel(message.from_user.id)
            await message.answer("Поиск и неподтверждённые действия сброшены. Подписки сохранены.", reply_markup=menu())

        @dispatcher.message(Command("subscribe"))
        async def subscribe(message: Message):
            if message.from_user:
                city = (message.text or "").partition(" ")[2].strip() or None
                await subscription_prompt(message, message.from_user.id, city)

        @dispatcher.message(Command("unsubscribe"))
        async def unsubscribe(message: Message):
            if message.from_user:
                count = await service.unsubscribe(message.from_user.id)
                await message.answer(f"Удалено подписок: {count}.")

        @dispatcher.message(Command("event"))
        async def event(message: Message):
            try:
                await card(message, await service.event((message.text or "").partition(" ")[2].strip()))
            except (ValueError, httpx.HTTPError):
                await message.answer("Карточка не найдена. Проверьте идентификатор или выполните новый поиск.")

        @dispatcher.message(Command("search"))
        async def explicit_search(message: Message):
            text = (message.text or "").partition(" ")[2].strip()
            if text:
                await search(message, text)
            else:
                await message.answer("Напишите запрос после /search или обычным сообщением.")

        @dispatcher.callback_query(F.data.startswith("confirm:"))
        async def confirm(callback: CallbackQuery):
            await callback.answer()
            if not isinstance(callback.message, Message):
                return
            try:
                done = await service.confirm_subscription(callback.from_user.id, (callback.data or "").partition(":")[2])
                status = service.store.get(callback.from_user.id).get('notifications')
                text = 'Подписка сохранена. Доставка уведомлений включена.' if status in {'enabled', 'telegram'} else 'Подписка сохранена. Доставка уведомлений пока не настроена.'
                await callback.message.answer(text + ' /unsubscribe удалит её.' if done else "Подтверждение истекло или уже использовано. Откройте подписку заново.")
            except httpx.HTTPError:
                await callback.message.answer("Не удалось подтвердить сохранение. Проверьте подписки перед повтором.")

        @dispatcher.callback_query(F.data.startswith('agentconfirm:'))
        async def agent_confirm(callback: CallbackQuery):
            await callback.answer()
            if not isinstance(callback.message, Message):
                return
            nonce = (callback.data or '').partition(':')[2]
            pending = service.store.get(callback.from_user.id).get('agent_pending', {})
            if pending.get('nonce') != nonce:
                await safe_answer(callback.message, 'Подтверждение истекло или уже обработано.')
                return
            try:
                await show_result(callback.message, await service.query(callback.from_user.id, 'Подтверждаю', nonce))
            except (httpx.HTTPError, ValueError):
                await safe_answer(callback.message, 'Не удалось подтвердить действие. Повторите это же подтверждение.')

        @dispatcher.callback_query(F.data.startswith('more:'))
        async def more(callback: CallbackQuery):
            await callback.answer()
            if not isinstance(callback.message, Message):
                return
            nonce = (callback.data or '').partition(':')[2]
            if not service.store.consume_callback(callback.from_user.id, nonce):
                await safe_answer(callback.message, 'Эта кнопка уже использована. Уточните поиск или используйте последнюю кнопку.')
                return
            try:
                await show_result(callback.message, await service.query(callback.from_user.id, 'а ещё'))
            except (httpx.HTTPError, ValueError):
                await safe_answer(callback.message, 'Каталог временно недоступен. Напишите «а ещё», чтобы повторить.')

        @dispatcher.callback_query(F.data.startswith('page:'))
        async def page(callback: CallbackQuery):
            if not isinstance(callback.message,Message):
                await callback.answer()
                return
            parts = (callback.data or '').split(':')
            try:
                if len(parts)!=3 or not parts[2].isdigit():
                    raise ValueError('Некорректная страница')
                result = await service.page(callback.from_user.id,parts[1],int(parts[2]))
                nav = result['page_navigation']
                await callback.answer(f"Страница {nav['index']+1} из {nav['pages']}")
                if not result.get('page_unchanged'):
                    await show_result(callback.message,result,editing=True)
            except (ValueError,httpx.HTTPError):
                await callback.answer('Подборка устарела. Напишите новый запрос.',show_alert=True)
            except TelegramAPIError:
                return

        @dispatcher.callback_query(F.data.startswith('event:'))
        async def event_details(callback: CallbackQuery):
            await callback.answer()
            if not isinstance(callback.message, Message):
                return
            try:
                await card(callback.message, await service.event((callback.data or '').partition(':')[2]))
            except (httpx.HTTPError, ValueError):
                await safe_answer(callback.message, 'Карточка недоступна. Начните новый поиск.')

        @dispatcher.callback_query(F.data.in_({"search", "subscribe", "help"}))
        async def action(callback: CallbackQuery):
            await callback.answer()
            if not isinstance(callback.message, Message):
                return
            if callback.data == "search":
                await service.cancel(callback.from_user.id)
                await safe_answer(callback.message, "<b>Новый поиск</b>\n\nНапишите город, дату и пожелания одним сообщением. Прежние условия сброшены, подписки сохранены.")
            elif callback.data == "subscribe":
                await subscription_prompt(callback.message, callback.from_user.id)
            elif callback.data == "help":
                await help_message(callback.message)

        @dispatcher.message(F.text)
        async def free_text(message: Message):
            await search(message, message.text or "")

        try:
            from .notifications import delivery_loop
            delivery = asyncio.create_task(delivery_loop(client, bot))
            await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
        finally:
            if 'delivery' in locals():
                delivery.cancel()
                with suppress(asyncio.CancelledError):
                    await delivery
            await bot.session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
