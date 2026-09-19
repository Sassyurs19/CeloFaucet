"""
Payment history handler for Telegram Celo USAT Payment Bot.
Provides paginated user USAT payment logs with on-chain Celoscan links and confirmation status.
"""

from __future__ import annotations

import math
from datetime import datetime
from aiogram import Router, F
from aiogram.types import CallbackQuery

from config import config
from database import db
from wallet import wallet_manager
from keyboards.main import get_history_keyboard, get_back_to_main_keyboard

router = Router(name="history")
PAGE_SIZE = 5


def format_timestamp(ts_str: str | None) -> str:
    """Format an SQLite timestamp string into a readable format."""
    if not ts_str:
        return "Unknown"
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y, %I:%M %p")
    except Exception:
        return str(ts_str)


@router.callback_query(F.data.startswith("history_page:"))
async def cb_user_history(callback: CallbackQuery) -> None:
    """Display paginated list of user's personal USAT payments."""
    user_id = callback.from_user.id if callback.from_user else 0
    try:
        page = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        page = 0

    total_count = await db.get_user_payments_count(user_id)
    total_pages = max(1, math.ceil(total_count / PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))

    payments = await db.get_user_payments(user_id, limit=PAGE_SIZE, offset=page * PAGE_SIZE)

    if not payments:
        empty_text = (
            "📜 <b>MY PAYMENTS</b>\n\n"
            "<i>You haven't made any USAT payments yet.</i>\n\n"
            "Click <b>💵 Pay $2 USAT</b> in the main menu to make your first payment!"
        )
        if callback.message:
            await callback.message.edit_text(
                text=empty_text,
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML",
            )
        await callback.answer()
        return

    lines = [f"📜 <b>MY PAYMENTS</b> (Page {page + 1}/{total_pages})\n"]

    for idx, p in enumerate(payments, start=1 + (page * PAGE_SIZE)):
        status = p.get("status", "UNKNOWN")
        if status == "SUCCESS":
            status_icon = "🟢 Confirmed"
        elif status == "PROCESSING":
            status_icon = "⏳ Processing"
        else:
            status_icon = "❌ Failed"

        dest_addr = wallet_manager.truncate_address(p.get("to_address", ""), prefix_len=6, suffix_len=4)
        rec_name = p.get("receiving_wallet_name", "Receiving Wallet")
        amt = p.get("amount_usat", "2.00")
        created = format_timestamp(p.get("created_at"))
        tx_hash = p.get("tx_hash")

        tx_line = ""
        if tx_hash and not tx_hash.startswith("0xsimulated_"):
            tx_url = f"{config.explorer_tx_url}{tx_hash}"
            short_hash = wallet_manager.truncate_address(tx_hash, prefix_len=6, suffix_len=4)
            tx_line = f"\n🔗 <a href=\"{tx_url}\">{short_hash}</a>"

        gas_sub = " <i>(0.05 CELO Gas Subsidized)</i>" if p.get("celo_funded") == 1 else ""

        lines.append(
            f"<b>{idx}.</b>\n"
            f"💵 <b>${amt} USAT</b>\n"
            f"📍 <b>To:</b> {rec_name} (<code>{dest_addr}</code>)\n"
            f"<b>Status:</b> {status_icon}{gas_sub}\n"
            f"🕐 {created}"
            f"{tx_line}\n"
        )

    text = "\n".join(lines)

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_history_keyboard(page, total_pages),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    await callback.answer()
