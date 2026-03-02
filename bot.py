"""
Telegram Bot з платною підпискою через NOWPayments (криптовалюта)
Тарифи: 1 місяць / 3 місяці / 1 рік

Функції:
- 3 тарифи підписки
- Кабінет користувача
- Реферальна програма
- Нагадування за 3 дні до закінчення
- Промокоди зі знижкою
- Адмін-панель /admin
"""

import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import config
from database import Database
from payments import NOWPaymentsClient

# ─── Логування ───────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

# ─── Ініціалізація ────────────────────────────────────────────────────────────
bot = Bot(token=config.BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())
db  = Database("subscriptions.db")
nwp = NOWPaymentsClient(config.NOWPAYMENTS_API_KEY)

# ─── Тарифи ──────────────────────────────────────────────────────────────────
PLANS = {
    "1m": {"name": "1 місяць",  "price": 10.0,  "days": 30,  "emoji": "🥉"},
    "3m": {"name": "3 місяці",  "price": 25.0,  "days": 90,  "emoji": "🥈"},
    "1y": {"name": "1 рік",     "price": 90.0,  "days": 365, "emoji": "🥇"},
}

# ─── FSM стани ───────────────────────────────────────────────────────────────
class PromoState(StatesGroup):
    waiting_promo = State()


# ══════════════════════════════════════════════════════════════════════════════
# ГОЛОВНЕ МЕНЮ
# ══════════════════════════════════════════════════════════════════════════════

def main_menu_kb(has_sub: bool = False) -> InlineKeyboardMarkup:
    buttons = []
    if not has_sub:
        buttons.append([InlineKeyboardButton(text="💳 Купити підписку", callback_data="show_plans")])
    else:
        buttons.append([InlineKeyboardButton(text="🔄 Продовжити підписку", callback_data="show_plans")])
    buttons.append([InlineKeyboardButton(text="👤 Кабінет", callback_data="cabinet")])
    buttons.append([InlineKeyboardButton(text="👥 Реферальна програма", callback_data="referral")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or str(user_id)

    # Реєстрація користувача
    db.register_user(user_id, username)

    # Перевірка реферала
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("ref_"):
        referrer_id = int(args[1].replace("ref_", ""))
        if referrer_id != user_id:
            db.set_referrer(user_id, referrer_id)

    sub = db.get_subscription(user_id)
    has_sub = sub and sub["expires_at"] > datetime.utcnow()

    if has_sub:
        days_left = (sub["expires_at"] - datetime.utcnow()).days
        await message.answer(
            f"👋 Вітаю, <b>{message.from_user.first_name}</b>!\n\n"
            f"✅ У тебе активна підписка!\n"
            f"📅 Діє ще: <b>{days_left} днів</b>",
            parse_mode="HTML",
            reply_markup=main_menu_kb(has_sub=True)
        )
    else:
        await message.answer(
            f"👋 Вітаю, <b>{message.from_user.first_name}</b>!\n\n"
            f"Цей бот дає доступ до приватного каналу з ексклюзивним контентом.\n\n"
            f"💰 <b>Тарифи:</b>\n"
            f"🥉 1 місяць — <b>$10</b>\n"
            f"🥈 3 місяці — <b>$25</b> <i>(економія $5)</i>\n"
            f"🥇 1 рік — <b>$90</b> <i>(економія $30)</i>\n\n"
            f"Оплата через криптовалюту (BTC, ETH, USDT та інші).",
            parse_mode="HTML",
            reply_markup=main_menu_kb(has_sub=False)
        )


# ══════════════════════════════════════════════════════════════════════════════
# ТАРИФИ
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data == "show_plans")
async def show_plans(callback: CallbackQuery):
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{p['emoji']} {p['name']} — ${p['price']}",
            callback_data=f"buy_{key}"
        )] for key, p in PLANS.items()
    ] + [
        [InlineKeyboardButton(text="🎟 Маю промокод", callback_data="enter_promo")],
        [InlineKeyboardButton(text="« Назад", callback_data="back_main")]
    ])
    await callback.message.edit_text(
        "💳 <b>Оберіть тариф підписки:</b>\n\n"
        "🥉 <b>1 місяць</b> — $10\n"
        "🥈 <b>3 місяці</b> — $25 <i>(економія $5)</i>\n"
        "🥇 <b>1 рік</b> — $90 <i>(економія $30)</i>",
        parse_mode="HTML",
        reply_markup=kb
    )


@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(callback: CallbackQuery):
    plan_key = callback.data.split("_", 1)[1]
    plan = PLANS.get(plan_key)
    if not plan:
        await callback.answer("Невідомий тариф")
        return

    user_id = callback.from_user.id
    await callback.answer()
    await callback.message.answer("⏳ Створюємо платіж, зачекай...")

    # Перевірка промокоду
    promo = db.get_user_promo(user_id)
    final_price = plan["price"]
    promo_text = ""
    if promo:
        discount = promo["discount"]
        final_price = round(plan["price"] * (1 - discount / 100), 2)
        promo_text = f"\n🎟 Промокод: -{discount}% (${plan['price']} → ${final_price})"
        db.use_promo(user_id)

    try:
        invoice = await nwp.create_invoice(
            price_amount=final_price,
            price_currency="usd",
            pay_currency="usdttrc20",
            order_id=f"sub_{user_id}_{plan_key}_{int(datetime.utcnow().timestamp())}",
            order_description=f"Підписка {plan['name']} для @{callback.from_user.username or user_id}",
            ipn_callback_url=config.IPN_CALLBACK_URL,
        )

        pay_url = invoice.get("invoice_url") or invoice.get("pay_url", "")
        payment_id = str(invoice["id"])

        db.save_pending_payment(user_id, payment_id, plan_key)

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💸 Оплатити", url=pay_url)],
            [InlineKeyboardButton(text="✅ Я оплатив(ла)", callback_data=f"check_{payment_id}")]
        ])
        await callback.message.answer(
            f"💳 <b>Рахунок створено!</b>\n\n"
            f"📦 Тариф: <b>{plan['emoji']} {plan['name']}</b>\n"
            f"💵 Сума: <b>${final_price} USDT</b>{promo_text}\n"
            f"🆔 ID платежу: <code>{payment_id}</code>\n\n"
            "1️⃣ Натисни <b>«Оплатити»</b>\n"
            "2️⃣ Після оплати натисни <b>«Я оплатив(ла)»</b>",
            parse_mode="HTML",
            reply_markup=kb
        )

    except Exception as e:
        log.error(f"Помилка створення інвойсу: {e}")
        await callback.message.answer("❌ Помилка при створенні платежу. Спробуй пізніше.")


# ══════════════════════════════════════════════════════════════════════════════
# ПЕРЕВІРКА ПЛАТЕЖУ
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data.startswith("check_"))
async def check_payment(callback: CallbackQuery):
    payment_id = callback.data.split("_", 1)[1]
    user_id = callback.from_user.id
    await callback.answer("🔍 Перевіряємо оплату...")

    try:
        status = await nwp.get_payment_status(payment_id)
        pstatus = status.get("payment_status", "")

        if pstatus in ("finished", "confirmed", "sending", "partially_paid"):
            pending = db.get_pending_payment(payment_id)
            plan_key = pending["plan_key"] if pending else "1m"
            await _grant_access(user_id, payment_id, plan_key)
            await callback.message.answer(
                "🎉 <b>Оплата підтверджена!</b>\n\nТебе додано до каналу 👇",
                parse_mode="HTML"
            )
            await _send_channel_invite(user_id)
            await _notify_referrer(user_id)

        elif pstatus == "waiting":
            await callback.message.answer("⏳ Платіж ще не надійшов. Зачекай кілька хвилин і перевір знову.")
        else:
            await callback.message.answer(
                f"ℹ️ Статус платежу: <b>{pstatus}</b>\n"
                "Якщо оплатив — зачекай 5-10 хвилин і перевір знову.",
                parse_mode="HTML"
            )

    except Exception as e:
        log.error(f"Помилка перевірки платежу: {e}")
        await callback.message.answer("❌ Не вдалося перевірити платіж. Спробуй пізніше.")


# ══════════════════════════════════════════════════════════════════════════════
# КАБІНЕТ
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data == "cabinet")
async def cabinet(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    sub = db.get_subscription(user_id)
    user = db.get_user(user_id)
    referral_count = db.get_referral_count(user_id)

    if sub and sub["expires_at"] > datetime.utcnow():
        days_left = (sub["expires_at"] - datetime.utcnow()).days
        expires_str = sub["expires_at"].strftime("%d.%m.%Y")
        status_text = (
            f"✅ <b>Підписка активна</b>\n"
            f"📅 Діє до: <b>{expires_str}</b>\n"
            f"⏳ Залишилось: <b>{days_left} днів</b>"
        )
    else:
        status_text = "❌ <b>Підписка неактивна</b>"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Продовжити підписку", callback_data="show_plans")],
        [InlineKeyboardButton(text="« Назад", callback_data="back_main")]
    ])

    await callback.message.edit_text(
        f"👤 <b>Кабінет</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"👤 Username: @{callback.from_user.username or 'немає'}\n\n"
        f"{status_text}\n\n"
        f"👥 Запрошено рефералів: <b>{referral_count}</b>",
        parse_mode="HTML",
        reply_markup=kb
    )


# ══════════════════════════════════════════════════════════════════════════════
# РЕФЕРАЛЬНА ПРОГРАМА
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data == "referral")
async def referral(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    ref_link = f"https://t.me/{(await bot.get_me()).username}?start=ref_{user_id}"
    ref_count = db.get_referral_count(user_id)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Назад", callback_data="back_main")]
    ])

    await callback.message.edit_text(
        f"👥 <b>Реферальна програма</b>\n\n"
        f"Запрошуй друзів і отримуй бонуси!\n\n"
        f"🎁 <b>За кожного друга який купить підписку:</b>\n"
        f"• +7 днів до твоєї підписки\n\n"
        f"🔗 Твоє посилання:\n<code>{ref_link}</code>\n\n"
        f"👤 Запрошено: <b>{ref_count} осіб</b>",
        parse_mode="HTML",
        reply_markup=kb
    )


# ══════════════════════════════════════════════════════════════════════════════
# ПРОМОКОДИ
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data == "enter_promo")
async def enter_promo(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(PromoState.waiting_promo)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Скасувати", callback_data="cancel_promo")]
    ])
    await callback.message.answer("🎟 Введи промокод:", reply_markup=kb)


@dp.message(PromoState.waiting_promo)
async def process_promo(message: Message, state: FSMContext):
    code = message.text.strip().upper()
    user_id = message.from_user.id
    promo = db.check_promo(code)

    if promo:
        db.save_user_promo(user_id, code, promo["discount"])
        await state.clear()
        await message.answer(
            f"✅ Промокод <b>{code}</b> активовано!\n"
            f"🎁 Знижка: <b>{promo['discount']}%</b>\n\n"
            f"Знижка буде застосована при наступній оплаті.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Обрати тариф", callback_data="show_plans")]
            ])
        )
    else:
        await message.answer("❌ Невірний або вже використаний промокод. Спробуй ще раз або натисни «Скасувати».")


@dp.callback_query(F.data == "cancel_promo")
async def cancel_promo(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await callback.message.delete()


# ══════════════════════════════════════════════════════════════════════════════
# АДМІН-ПАНЕЛЬ
# ══════════════════════════════════════════════════════════════════════════════

@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    stats = db.get_stats()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Список підписників", callback_data="admin_subs")],
        [InlineKeyboardButton(text="➕ Створити промокод", callback_data="admin_create_promo")],
        [InlineKeyboardButton(text="📢 Розсилка", callback_data="admin_broadcast")],
    ])

    await message.answer(
        f"🔧 <b>Адмін-панель</b>\n\n"
        f"👥 Всього користувачів: <b>{stats['total_users']}</b>\n"
        f"✅ Активних підписок: <b>{stats['active_subs']}</b>\n"
        f"💰 Загальний дохід: <b>${stats['total_revenue']}</b>\n\n"
        f"📊 <b>По тарифах:</b>\n"
        f"🥉 1 місяць: {stats['plan_1m']} шт.\n"
        f"🥈 3 місяці: {stats['plan_3m']} шт.\n"
        f"🥇 1 рік: {stats['plan_1y']} шт.",
        parse_mode="HTML",
        reply_markup=kb
    )


@dp.callback_query(F.data == "admin_subs")
async def admin_subs(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        return
    await callback.answer()
    subs = db.get_active_subscriptions()
    if not subs:
        await callback.message.answer("Активних підписок немає.")
        return

    text = "📋 <b>Активні підписки:</b>\n\n"
    for s in subs[:20]:  # показуємо перші 20
        expires = datetime.fromisoformat(s["expires_at"]).strftime("%d.%m.%Y")
        text += f"• ID <code>{s['user_id']}</code> — до {expires} ({s['plan_key']})\n"

    if len(subs) > 20:
        text += f"\n<i>...і ще {len(subs) - 20}</i>"

    await callback.message.answer(text, parse_mode="HTML")


@dp.callback_query(F.data == "admin_create_promo")
async def admin_create_promo(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        return
    await callback.answer()
    # Автоматично створюємо промокод
    import random, string
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    discount = 20  # 20% знижка за замовчуванням
    db.create_promo(code, discount)
    await callback.message.answer(
        f"✅ Промокод створено!\n\n"
        f"🎟 Код: <code>{code}</code>\n"
        f"💸 Знижка: <b>{discount}%</b>",
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_prompt(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        return
    await callback.answer()
    await state.set_state(PromoState.waiting_promo)  # reuse state for simplicity
    await callback.message.answer("📢 Введи текст розсилки (відправляється всім користувачам):")


# ══════════════════════════════════════════════════════════════════════════════
# НАВІГАЦІЯ
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data == "back_main")
async def back_main(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    sub = db.get_subscription(user_id)
    has_sub = sub and sub["expires_at"] > datetime.utcnow()
    await callback.message.edit_text(
        f"👋 Головне меню",
        reply_markup=main_menu_kb(has_sub=bool(has_sub))
    )


@dp.message(Command("mystatus"))
async def my_status(message: Message):
    sub = db.get_subscription(message.from_user.id)
    if sub and sub["expires_at"] > datetime.utcnow():
        days = (sub["expires_at"] - datetime.utcnow()).days
        expires_str = sub["expires_at"].strftime("%d.%m.%Y")
        await message.answer(
            f"✅ Підписка активна.\n"
            f"📅 Діє до: <b>{expires_str}</b>\n"
            f"⏳ Залишилось: <b>{days} днів</b>",
            parse_mode="HTML"
        )
    else:
        await message.answer("❌ Підписка неактивна. /start — купити.")


# ══════════════════════════════════════════════════════════════════════════════
# ДОПОМІЖНІ ФУНКЦІЇ
# ══════════════════════════════════════════════════════════════════════════════

async def _grant_access(user_id: int, payment_id: str, plan_key: str = "1m"):
    plan = PLANS.get(plan_key, PLANS["1m"])
    expires = datetime.utcnow() + timedelta(days=plan["days"])
    db.save_subscription(user_id, payment_id, expires, plan_key)
    log.info(f"Доступ надано: user={user_id}, plan={plan_key}, expires={expires}")


async def _send_channel_invite(user_id: int):
    try:
        invite = await bot.create_chat_invite_link(
            chat_id=config.CHANNEL_ID,
            member_limit=1,
            expire_date=int((datetime.utcnow() + timedelta(hours=24)).timestamp())
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📢 Перейти до каналу", url=invite.invite_link)
        ]])
        await bot.send_message(user_id, "🔗 Твоє персональне посилання:", reply_markup=kb)
    except Exception as e:
        log.error(f"Помилка створення запрошення: {e}")


async def _notify_referrer(user_id: int):
    """Нараховує реферальний бонус — +7 днів до підписки реферера."""
    referrer_id = db.get_referrer(user_id)
    if not referrer_id:
        return
    try:
        sub = db.get_subscription(referrer_id)
        if sub:
            new_expires = sub["expires_at"] + timedelta(days=7)
            db.update_subscription_expiry(referrer_id, new_expires)
            await bot.send_message(
                referrer_id,
                "🎁 <b>Реферальний бонус!</b>\n\n"
                "Твій друг купив підписку.\n"
                "➕ <b>+7 днів</b> додано до твоєї підписки!",
                parse_mode="HTML"
            )
    except Exception as e:
        log.warning(f"Помилка нарахування реферального бонусу: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# ФОНОВІ ЗАДАЧІ
# ══════════════════════════════════════════════════════════════════════════════

async def expire_checker():
    """Кожні 12 годин кікає тих, у кого скінчилася підписка."""
    while True:
        await asyncio.sleep(12 * 3600)
        expired = db.get_expired_subscriptions()
        for row in expired:
            user_id = row["user_id"]
            try:
                await bot.ban_chat_member(config.CHANNEL_ID, user_id)
                await bot.unban_chat_member(config.CHANNEL_ID, user_id)
                await bot.send_message(
                    user_id,
                    "⚠️ Твоя підписка завершилась.\n\n"
                    "Натисни /start, щоб продовжити.",
                )
                db.remove_subscription(user_id)
                log.info(f"Підписку видалено: user={user_id}")
            except Exception as e:
                log.warning(f"Не вдалося видалити user={user_id}: {e}")


async def reminder_checker():
    """Щодня надсилає нагадування тим, у кого залишилось ≤ 3 дні підписки."""
    while True:
        await asyncio.sleep(24 * 3600)
        soon = db.get_expiring_soon(days=3)
        for row in soon:
            user_id = row["user_id"]
            expires_at = datetime.fromisoformat(row["expires_at"])
            days_left = (expires_at - datetime.utcnow()).days
            try:
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🔄 Продовжити підписку", callback_data="show_plans")
                ]])
                await bot.send_message(
                    user_id,
                    f"⏰ <b>Нагадування!</b>\n\n"
                    f"Твоя підписка закінчується через <b>{days_left} дні(в)</b>.\n"
                    f"Продовж зараз, щоб не втратити доступ до каналу!",
                    parse_mode="HTML",
                    reply_markup=kb
                )
            except Exception as e:
                log.warning(f"Не вдалося надіслати нагадування user={user_id}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# ЗАПУСК
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    db.init()
    asyncio.create_task(expire_checker())
    asyncio.create_task(reminder_checker())
    log.info("Бот запущено ✅")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
