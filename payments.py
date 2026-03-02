"""
Клієнт для NOWPayments API.
Документація: https://documenter.getpostman.com/view/7907941/2s93JusNJt
"""

import aiohttp
import logging

log = logging.getLogger(__name__)

NOWPAYMENTS_BASE = "https://api.nowpayments.io/v1"


class NOWPaymentsClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json",
        }

    async def create_invoice(
        self,
        price_amount: float,
        price_currency: str,
        pay_currency: str,
        order_id: str,
        order_description: str,
        ipn_callback_url: str,
    ) -> dict:
        """Створити інвойс для оплати."""
        payload = {
            "price_amount": price_amount,
            "price_currency": price_currency,
            "pay_currency": pay_currency,
            "order_id": order_id,
            "order_description": order_description,
            "ipn_callback_url": ipn_callback_url,
            "is_fixed_rate": True,
            "is_fee_paid_by_user": False,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{NOWPAYMENTS_BASE}/invoice",
                json=payload,
                headers=self.headers
            ) as resp:
                data = await resp.json()
                if resp.status not in (200, 201):
                    raise Exception(f"NOWPayments invoice error {resp.status}: {data}")
                log.info(f"Інвойс створено: {data.get('id')}")
                return data

    async def get_payment_status(self, payment_id: str) -> dict:
        """Отримати статус платежу за ID."""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{NOWPAYMENTS_BASE}/payment/{payment_id}",
                headers=self.headers
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    raise Exception(f"NOWPayments status error {resp.status}: {data}")
                return data

    async def get_available_currencies(self) -> list[str]:
        """Список доступних криптовалют."""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{NOWPAYMENTS_BASE}/currencies",
                headers=self.headers
            ) as resp:
                data = await resp.json()
                return data.get("currencies", [])
