"""
Вебхук-сервер для IPN-сповіщень від NOWPayments.
Запускається окремо від бота: python webhook.py
"""

import asyncio
import hashlib
import hmac
import json
import logging

from aiohttp import web
from datetime import datetime, timedelta

import config
from database import Database
from aiogram import Bot

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

PLANS = {
    "1m": {"days": 30},
    "3m": {"days": 90},
    "1y": {"days": 365},
}

db  = Database("subscriptions.db")
bot = Bot(token=config.BOT_TOKEN)


async def nowpayments_webhook(request: web.Request) -> web.Response:
    body = await request.read()

    try:
        data = json.loads(body)
    except Exception:
        return web.Response(status=400)

    payment_id = str(data.get("payment_id", ""))
    status     = data.get("payment_status", "")

    log.info(f"IPN: payment_id={payment_id}, status={status}")

    if status in ("finished", "confirmed"):
        pending = db.get_pending_payment(payment_id)
        if pending:
            user_id  = pending["user_id"]
            plan_key = pending.get("plan_key", "1m")
            days     = PLANS.get(plan_key, PLANS["1m"])["days"]
            expires  = datetime.utcnow() + timedelta(days=days)

            db.save_subscription(user_id, payment_id, expires, plan_key)
            db.delete_pending_payment(payment_id)

            try:
                invite = await bot.create_chat_invite_link(
                    chat_id=config.CHANNEL_ID,
                    member_limit=1,
                    expire_date=int((datetime.utcnow() + timedelta(hours=24)).timestamp())
                )
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="📢 Перейти до каналу", url=invite.invite_link)
                ]])
                await bot.send_message(
                    user_id,
                    "🎉 <b>Оплата підтверджена автоматично!</b>\n\nТвоє посилання на канал 👇",
                    parse_mode="HTML",
                    reply_markup=kb
                )
                log.info(f"Доступ надано: user={user_id}, plan={plan_key}")
            except Exception as e:
                log.error(f"Помилка відправки запрошення: {e}")

    return web.Response(status=200, text="OK")


async def main():
    db.init()
    app = web.Application()
    app.router.add_post("/nowpayments/webhook", nowpayments_webhook)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    log.info("Webhook-сервер запущено на порту 8080 ✅")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
