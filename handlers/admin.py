"""
Administrator dashboard handlers for Celo USAT Payment Bot.
Strictly authorized by numeric Telegram ID (ADMIN_TELEGRAM_ID).
Provides management of receiving addresses, user totals, payment logs,
the CELO funding wallet, and secure, auto-deleting private key reveal for imported wallets.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import config
from database import db
from wallet import wallet_manager
from celo import celo_client
from services.encryption import encryption_service
from services.validation import validate_celo_address
from keyboards.admin import (
    get_admin_menu_keyboard,
    get_admin_back_keyboard,
    get_admin_receiving_wallets_keyboard,
    get_admin_receiving_wallet_detail_keyboard,
    get_admin_users_keyboard,
    get_admin_user_detail_keyboard,
    get_admin_user_wallet_detail_keyboard,
    get_admin_reveal_confirm_keyboard,
    get_admin_txs_keyboard,
    get_admin_user_totals_keyboard,
)
from keyboards.main import get_cancel_keyboard

logger = logging.getLogger(__name__)
router = Router(name="admin")

ADMIN_PAGE_SIZE = 5


class AdminStates(StatesGroup):
    """FSM states for admin management."""
    waiting_for_rec_name = State()
    waiting_for_rec_address = State()
    waiting_for_rec_rename = State()
    waiting_for_user_wallet_rename = State()


def format_timestamp(ts_str: str | None) -> str:
    """Format an SQLite timestamp string into a readable format."""
    if not ts_str:
        return "Unknown"
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y, %H:%M")
    except Exception:
        return str(ts_str)


def is_authorized_admin(user_id: int) -> bool:
    """Strictly assert caller is the configured admin ID."""
    return config.is_admin(user_id)


# --- Entrypoint & Access Control ---

@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    """Direct /admin command with strict access enforcement."""
    user_id = message.from_user.id if message.from_user else 0

    try:
        await message.delete()
    except Exception:
        pass

    if not is_authorized_admin(user_id):
        # Strict requirement:
        # "**⛔ ACCESS DENIED**\n\n*You don't have permission to use this command.*"
        await message.answer(
            text="⛔ <b>ACCESS DENIED</b>\n\n<i>You don't have permission to use this command.</i>",
            parse_mode="HTML",
        )
        return

    is_paused = await db.is_bot_paused()
    panel_text = (
        "👑 <b>ADMIN PANEL</b>\n\n"
        "<i>Celo USAT Payment Dashboard</i>\n\n"
        "Select an administrative action below:"
    )
    await message.answer(
        text=panel_text,
        reply_markup=get_admin_menu_keyboard(is_paused),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_menu")
async def cb_admin_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """Return to Admin Panel root."""
    await state.clear()
    user_id = callback.from_user.id if callback.from_user else 0
    if not is_authorized_admin(user_id):
        await callback.answer("Unauthorized.", show_alert=True)
        return

    is_paused = await db.is_bot_paused()
    panel_text = (
        "👑 <b>ADMIN PANEL</b>\n\n"
        "<i>Celo USAT Payment Dashboard</i>\n\n"
        "Select an administrative action below:"
    )
    if callback.message:
        await callback.message.edit_text(
            text=panel_text,
            reply_markup=get_admin_menu_keyboard(is_paused),
            parse_mode="HTML",
        )
    await callback.answer()


# =========================================================================
# 1. 💰 RECEIVING WALLETS MANAGEMENT
# =========================================================================

@router.callback_query(F.data == "admin_rec_list")
async def cb_admin_rec_list(callback: CallbackQuery) -> None:
    """Display receiving addresses configured by the admin."""
    user_id = callback.from_user.id if callback.from_user else 0
    if not is_authorized_admin(user_id):
        return

    wallets = await db.get_receiving_wallets()

    lines = ["💰 <b>RECEIVING WALLETS</b>\n"]
    for idx, r in enumerate(wallets, start=1):
        status_str = "🟢 Active" if r.get("is_active") else "🔴 Inactive"
        lines.append(
            f"{idx}. <b>{r['name']}</b>\n"
            f"<code>{r['address']}</code>\n"
            f"{status_str}\n"
        )

    if not wallets:
        lines.append("<i>No receiving addresses configured yet.</i>\n")

    lines.append("<i>Users can select any active wallet to receive their $2.00 USAT payment.</i>")
    text = "\n".join(lines)

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_admin_receiving_wallets_keyboard(wallets),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_rec_view:"))
async def cb_admin_rec_view(callback: CallbackQuery) -> None:
    """View and manage an individual receiving wallet."""
    user_id = callback.from_user.id if callback.from_user else 0
    if not is_authorized_admin(user_id):
        return

    try:
        rec_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        return

    rec = await db.get_receiving_wallet_by_id(rec_id)
    if not rec:
        await callback.answer("Receiving wallet not found.", show_alert=True)
        return

    status_str = "🟢 Active" if rec.get("is_active") else "🔴 Inactive"
    addr = rec["address"]
    usat_bal_units, usat_bal = await celo_client.get_usat_balance(addr)
    celo_bal = await celo_client.get_celo_balance(addr)

    detail_text = (
        f"💰 <b>RECEIVING WALLET: {rec['name']}</b>\n\n"
        f"<b>Address:</b> <code>{addr}</code>\n"
        f"<b>Status:</b> {status_str}\n\n"
        f"<b>Current USAT:</b> {usat_bal}\n"
        f"<b>Current CELO:</b> {celo_bal:.4f} CELO"
    )

    if callback.message:
        await callback.message.edit_text(
            text=detail_text,
            reply_markup=get_admin_receiving_wallet_detail_keyboard(rec_id, bool(rec.get("is_active"))),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data == "admin_rec_add")
async def cb_admin_rec_add(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt admin for name of new receiving address."""
    user_id = callback.from_user.id if callback.from_user else 0
    if not is_authorized_admin(user_id):
        return

    await state.set_state(AdminStates.waiting_for_rec_name)
    prompt_text = "➕ <b>ADD RECEIVING WALLET</b>\n\n<i>Enter a display name for this receiving address:</i>"
    if callback.message:
        await callback.message.edit_text(
            text=prompt_text,
            reply_markup=get_cancel_keyboard("admin_rec_list"),
            parse_mode="HTML",
        )
        await state.update_data(prompt_msg_id=callback.message.message_id)
    await callback.answer()


@router.message(AdminStates.waiting_for_rec_name, F.text)
async def process_rec_name(message: Message, state: FSMContext, bot: Bot) -> None:
    """Receive name and prompt for 0x address."""
    user_id = message.from_user.id if message.from_user else 0
    if not is_authorized_admin(user_id):
        return

    name = message.text.strip() if message.text else "Receiving Address"
    try:
        await message.delete()
    except Exception:
        pass

    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")

    await state.update_data(rec_new_name=name)
    await state.set_state(AdminStates.waiting_for_rec_address)

    addr_prompt = (
        f"<b>Name:</b> <code>{name}</code>\n\n"
        "<i>Send the Celo public wallet address (0x...):</i>\n\n"
        "<i>Note: Receiving addresses do not require private keys.</i>"
    )
    if prompt_msg_id:
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=prompt_msg_id,
                text=addr_prompt,
                reply_markup=get_cancel_keyboard("admin_rec_list"),
                parse_mode="HTML",
            )
            return
        except Exception:
            pass

    sent = await message.answer(text=addr_prompt, parse_mode="HTML")
    await state.update_data(prompt_msg_id=sent.message_id)


@router.message(AdminStates.waiting_for_rec_address, F.text)
async def process_rec_address(message: Message, state: FSMContext, bot: Bot) -> None:
    """Validate and save new receiving address."""
    user_id = message.from_user.id if message.from_user else 0
    if not is_authorized_admin(user_id):
        return

    raw_addr = message.text.strip() if message.text else ""
    try:
        await message.delete()
    except Exception:
        pass

    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    name = data.get("rec_new_name", "Receiving Wallet")

    is_valid, chk_addr, err = validate_celo_address(raw_addr)
    if not is_valid or not chk_addr:
        err_text = f"❌ <b>Invalid Address:</b> {err}\n\n<i>Please enter a valid 0x Celo address:</i>"
        if prompt_msg_id:
            try:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=prompt_msg_id,
                    text=err_text,
                    reply_markup=get_cancel_keyboard("admin_rec_list"),
                    parse_mode="HTML",
                )
                return
            except Exception:
                pass
        return

    await db.add_receiving_wallet(name=name, address=chk_addr)
    await state.clear()

    confirm_text = (
        "✅ <b>RECEIVING WALLET ADDED</b>\n\n"
        f"<b>Name:</b> {name}\n"
        f"<b>Address:</b> <code>{chk_addr}</code>\n"
        "<b>Status:</b> 🟢 Active"
    )
    if prompt_msg_id:
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=prompt_msg_id,
                text=confirm_text,
                reply_markup=get_admin_back_keyboard(),
                parse_mode="HTML",
            )
            return
        except Exception:
            pass

    await message.answer(text=confirm_text, reply_markup=get_admin_back_keyboard(), parse_mode="HTML")


@router.callback_query(F.data.startswith("admin_rec_toggle:"))
async def cb_admin_rec_toggle(callback: CallbackQuery) -> None:
    """Toggle receiving wallet active/inactive."""
    user_id = callback.from_user.id if callback.from_user else 0
    if not is_authorized_admin(user_id):
        return

    rec_id = int(callback.data.split(":")[1])
    rec = await db.get_receiving_wallet_by_id(rec_id)
    if not rec:
        return

    new_active = not bool(rec.get("is_active"))
    await db.toggle_receiving_wallet(rec_id, new_active)
    await callback.answer(f"Status updated: {'Active' if new_active else 'Deactivated'}", show_alert=True)
    # Refresh view
    await cb_admin_rec_view(callback)


@router.callback_query(F.data.startswith("admin_rec_delete:"))
async def cb_admin_rec_delete(callback: CallbackQuery) -> None:
    """Delete a receiving wallet."""
    user_id = callback.from_user.id if callback.from_user else 0
    if not is_authorized_admin(user_id):
        return

    rec_id = int(callback.data.split(":")[1])
    await db.delete_receiving_wallet(rec_id)
    await callback.answer("Receiving wallet removed.", show_alert=True)
    await cb_admin_rec_list(callback)


# =========================================================================
# 2. 👥 USERS & USER WALLETS & SENSITIVE PRIVATE KEY VIEW
# =========================================================================

@router.callback_query(F.data.startswith("admin_users:"))
async def cb_admin_users(callback: CallbackQuery) -> None:
    """Paginated list of registered users."""
    user_id = callback.from_user.id if callback.from_user else 0
    if not is_authorized_admin(user_id):
        return

    try:
        page = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        page = 0

    total_count = await db.get_user_count()
    total_pages = max(1, math.ceil(total_count / ADMIN_PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))

    users = await db.get_all_users(limit=ADMIN_PAGE_SIZE, offset=page * ADMIN_PAGE_SIZE)

    if not users:
        await callback.message.edit_text(
            text="👥 <b>USERS</b>\n\n<i>No registered users in database.</i>",
            reply_markup=get_admin_back_keyboard(),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    text = f"👥 <b>REGISTERED USERS</b> (Page {page + 1}/{total_pages})\n\n<i>Select a user to view their profile and wallets:</i>"
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_admin_users_keyboard(users, page, total_pages),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_user_detail:"))
async def cb_admin_user_detail(callback: CallbackQuery) -> None:
    """Detailed view of a selected user and their wallets."""
    user_id = callback.from_user.id if callback.from_user else 0
    if not is_authorized_admin(user_id):
        return

    target_tg_id = int(callback.data.split(":")[1])
    user = await db.get_user_profile(target_tg_id)
    if not user:
        await callback.answer("User not found.", show_alert=True)
        return

    wallets = await db.get_wallets_by_user_id_admin(target_tg_id)
    total_paid = await db.get_user_total_paid(target_tg_id)
    payments_count = await db.get_user_payments_count(target_tg_id)

    name = user.get("full_name") or user.get("first_name") or "N/A"
    mobile = user.get("mobile_number") or "N/A"

    text = (
        f"👤 <b>USER DETAILS</b>\n\n"
        f"<b>Name:</b> {name}\n"
        f"<b>Telegram ID:</b> <code>{target_tg_id}</code>\n"
        f"<b>Username:</b> @{user.get('username') or 'None'}\n"
        f"<b>Mobile:</b> <code>{mobile}</code>\n\n"
        f"<b>Wallets:</b> {len(wallets)}\n"
        f"<b>Successful Payments:</b> {payments_count}\n"
        f"<b>Total Paid:</b> ${total_paid:.2f} USAT\n\n"
        f"<i>Select a wallet below to view details or private key:</i>"
    )

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_admin_user_detail_keyboard(target_tg_id, wallets),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_user_wallet_view:"))
async def cb_admin_user_wallet_view(callback: CallbackQuery) -> None:
    """
    Detailed view of a specific user wallet:
    Owner, Wallet, Address, USAT, CELO, Total Sent.
    Option [👁️ View Private Key] is only presented for imported wallets.
    """
    user_id = callback.from_user.id if callback.from_user else 0
    if not is_authorized_admin(user_id):
        return

    wallet_id = int(callback.data.split(":")[1])
    wallet = await db.get_wallet_by_id_admin(wallet_id)
    if not wallet:
        await callback.answer("Wallet not found.", show_alert=True)
        return

    owner_name = wallet.get("full_name") or wallet.get("first_name") or str(wallet["telegram_id"])
    w_name = wallet["wallet_name"]
    addr = wallet["address"]
    w_type = wallet.get("wallet_type", "imported")
    is_imported = (w_type == "imported")

    celo_bal = await celo_client.get_celo_balance(addr)
    _, usat_bal = await celo_client.get_usat_balance(addr)
    total_sent = await db.get_wallet_total_sent(addr)

    type_desc = "📥 Encrypted (Imported)" if is_imported else "🔗 Non-Custodial (Connected — No Private Key Stored)"

    wallet_text = (
        "🔐 <b>WALLET DETAILS</b>\n\n"
        f"Owner:\n<b>{owner_name}</b>\n\n"
        f"Wallet:\n<b>{w_name}</b>\n\n"
        f"Type:\n<b>{type_desc}</b>\n\n"
        f"Address:\n<code>{addr}</code>\n\n"
        f"USAT:\n<b>{usat_bal}</b>\n\n"
        f"CELO:\n<b>{celo_bal:.6f}</b>\n\n"
        f"Total Sent:\n<b>${total_sent:.2f} USAT</b>"
    )

    if callback.message:
        await callback.message.edit_text(
            text=wallet_text,
            reply_markup=get_admin_user_wallet_detail_keyboard(
                wallet_id=wallet_id,
                user_tg_id=wallet["telegram_id"],
                is_imported=is_imported,
            ),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_wallet_reveal_confirm:"))
async def cb_admin_wallet_reveal_confirm(callback: CallbackQuery) -> None:
    """Show sensitive warning prompt before revealing private key."""
    user_id = callback.from_user.id if callback.from_user else 0
    if not is_authorized_admin(user_id):
        return

    wallet_id = int(callback.data.split(":")[1])
    warning_text = (
        "⚠️ <b>SENSITIVE INFORMATION</b>\n\n"
        "<i>You are about to reveal the private key for this wallet.</i>\n\n"
        "<i>Anyone with this key can control the wallet and its assets.</i>"
    )
    if callback.message:
        await callback.message.edit_text(
            text=warning_text,
            reply_markup=get_admin_reveal_confirm_keyboard(wallet_id),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_wallet_reveal_do:"))
async def cb_admin_wallet_reveal_do(callback: CallbackQuery, bot: Bot) -> None:
    """
    Decrypt and display the private key to the authorized admin with automatic deletion.
    - Strict ADMIN_TELEGRAM_ID check.
    - Decrypt only immediately before display.
    - Never log decrypted key.
    - Schedule automatic message deletion after 60 seconds.
    """
    user_id = callback.from_user.id if callback.from_user else 0
    if not is_authorized_admin(user_id):
        await callback.answer("Unauthorized.", show_alert=True)
        return

    wallet_id = int(callback.data.split(":")[1])
    wallet = await db.get_wallet_by_id_admin(wallet_id)
    if not wallet or wallet.get("wallet_type") != "imported":
        await callback.answer("No private key stored for this wallet type.", show_alert=True)
        return

    encrypted_key = wallet.get("encrypted_private_key")
    if not encrypted_key:
        await callback.answer("No private key stored.", show_alert=True)
        return

    try:
        decrypted_key = encryption_service.decrypt(encrypted_key)
    except Exception as e:
        await callback.answer("Decryption failed: corrupted key payload.", show_alert=True)
        return

    w_name = wallet["wallet_name"]
    addr = wallet["address"]
    timeout_sec = config.private_key_reveal_timeout

    reveal_text = (
        "🔐 <b>PRIVATE KEY</b>\n\n"
        f"Wallet:\n<b>{w_name}</b>\n\n"
        f"Address:\n<code>{addr}</code>\n\n"
        f"Private Key:\n<code>{decrypted_key}</code>\n\n"
        "⚠️ <i>Keep this information secure.</i>\n\n"
        f"<i>⏳ This sensitive message will automatically delete in {timeout_sec} seconds.</i>"
    )

    # Wipe key variable from memory
    decrypted_key = None

    chat_id = callback.message.chat.id
    sent_msg = await bot.send_message(
        chat_id=chat_id,
        text=reveal_text,
        parse_mode="HTML",
    )
    await callback.answer("Private key revealed above.", show_alert=True)

    # Schedule automatic deletion
    asyncio.create_task(schedule_message_deletion(bot, chat_id, sent_msg.message_id, timeout_sec))


async def schedule_message_deletion(bot: Bot, chat_id: int, message_id: int, delay_seconds: int) -> None:
    """Background task to delete sensitive private key display after timeout."""
    await asyncio.sleep(delay_seconds)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info("Auto-deleted sensitive private key message %d in chat %d", message_id, chat_id)
    except Exception as e:
        logger.debug("Failed to auto-delete private key message: %s", e)


# =========================================================================
# 3. 📊 USER TOTALS & 4. 📜 PAYMENT HISTORY
# =========================================================================

@router.callback_query(F.data.startswith("admin_user_totals:"))
async def cb_admin_user_totals(callback: CallbackQuery) -> None:
    """Display user breakdown of total USAT paid."""
    user_id = callback.from_user.id if callback.from_user else 0
    if not is_authorized_admin(user_id):
        return

    try:
        page = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        page = 0

    totals = await db.get_user_totals(limit=10, offset=page * 10)
    total_users = await db.get_user_count()
    total_pages = max(1, math.ceil(total_users / 10))

    lines = [f"📊 <b>USER TOTALS REPORT</b> (Page {page + 1}/{total_pages})\n"]
    for idx, u in enumerate(totals, start=1 + (page * 10)):
        name = u.get("full_name") or u.get("first_name") or f"User {u['telegram_id']}"
        paid = u.get("total_usat_paid", 0.0)
        cnt = u.get("successful_payments", 0)
        lines.append(f"<b>{idx}. {name}</b>\n• Total: <b>${paid:.2f} USAT</b>\n• Payments: {cnt}\n")

    if not totals:
        lines.append("<i>No payments recorded yet.</i>")

    text = "\n".join(lines)
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_admin_user_totals_keyboard(page, total_pages),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_txs:"))
async def cb_admin_txs(callback: CallbackQuery) -> None:
    """Paginated list of all USAT payments across all users."""
    user_id = callback.from_user.id if callback.from_user else 0
    if not is_authorized_admin(user_id):
        return

    try:
        page = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        page = 0

    total_count = await db.get_all_payments_count()
    total_pages = max(1, math.ceil(total_count / ADMIN_PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))

    payments = await db.get_all_payments(limit=ADMIN_PAGE_SIZE, offset=page * ADMIN_PAGE_SIZE)

    if not payments:
        if callback.message:
            await callback.message.edit_text(
                text="📜 <b>PAYMENT HISTORY</b>\n\n<i>No payments recorded yet in the database.</i>",
                reply_markup=get_admin_back_keyboard(),
                parse_mode="HTML",
            )
        await callback.answer()
        return

    lines = [f"📜 <b>ALL PAYMENTS</b> (Page {page + 1}/{total_pages})\n"]
    for p in payments:
        status = p.get("status", "UNKNOWN")
        icon = "🟢" if status == "SUCCESS" else ("⏳" if status == "PROCESSING" else "❌")
        pid = p["id"]
        amt = p.get("amount_usat", "2.00")
        u_name = p.get("full_name") or str(p["telegram_id"])
        dest = wallet_manager.truncate_address(p.get("to_address", ""), 6, 4)
        tx_hash = p.get("tx_hash")
        short_tx = wallet_manager.truncate_address(tx_hash, 6, 4) if tx_hash else "None"
        time_str = format_timestamp(p.get("created_at"))
        gas_tag = " [0.05 Gas Funded]" if p.get("celo_funded") == 1 else ""

        lines.append(
            f"<b>TX #{pid}</b> {icon}\n"
            f"<b>User:</b> {u_name}\n"
            f"<b>Amount:</b> ${amt} USAT\n"
            f"<b>To:</b> <code>{dest}</code> ({p.get('receiving_wallet_name')})\n"
            f"<b>TX:</b> <code>{short_tx}</code>{gas_tag}\n"
            f"<b>Time:</b> {time_str}\n"
        )

    text = "\n".join(lines)
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_admin_txs_keyboard(page, total_pages),
            parse_mode="HTML",
        )
    await callback.answer()


# =========================================================================
# 5. 💵 USAT STATISTICS & 6. ⛽ CELO FUNDING & 7. ⚙️ SETTINGS
# =========================================================================

@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: CallbackQuery) -> None:
    """Display overall USAT platform usage metrics."""
    user_id = callback.from_user.id if callback.from_user else 0
    if not is_authorized_admin(user_id):
        return

    stats = await db.get_usat_statistics()
    text = (
        "💵 <b>USAT STATISTICS</b>\n\n"
        f"<b>Total Users:</b> {stats.get('total_users', 0):,}\n\n"
        f"<b>Total Payments:</b> {stats.get('total_payments', 0):,}\n"
        f"• Successful: {stats.get('successful', 0):,}\n"
        f"• Failed: {stats.get('failed', 0):,}\n"
        f"• Processing: {stats.get('processing', 0):,}\n\n"
        f"<b>Total USAT Sent:</b> ${stats.get('total_usat_sent', 0.0):.2f} USAT\n\n"
        f"<b>Today's Payments:</b> {stats.get('today_payments', 0):,}\n"
        f"<b>Today's USAT:</b> ${stats.get('today_usat_sent', 0.0):.2f} USAT\n\n"
        f"<b>CELO Gas Subsidies (0.05 CELO):</b> {stats.get('celo_subsidies_count', 0):,} claims\n\n"
        f"<b>Connected Wallets:</b> {stats.get('connected_wallets', 0)} non-custodial\n"
        f"<b>Imported Wallets:</b> {stats.get('imported_wallets', 0)} encrypted"
    )
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_admin_back_keyboard(),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data == "admin_funding")
async def cb_admin_funding(callback: CallbackQuery) -> None:
    """Inspect the dedicated CELO gas funding wallet."""
    user_id = callback.from_user.id if callback.from_user else 0
    if not is_authorized_admin(user_id):
        return

    funding_addr = wallet_manager.address
    trunc_addr = wallet_manager.truncate_address(funding_addr, 8, 6)
    bal = await celo_client.get_celo_balance(funding_addr)
    subsidy_amt = config.celo_funding_amount  # 0.05 CELO
    est_subsidies = math.floor(bal / subsidy_amt) if bal > 0 else 0

    low_warning = (
        "\n\n⚠️ <b>LOW BALANCE WARNING:</b> Please top up the funding wallet with CELO."
        if bal < 0.2
        else ""
    )

    text = (
        "⛽ <b>CELO GAS FUNDING WALLET</b>\n\n"
        f"<b>Address:</b>\n<code>{trunc_addr}</code>\n"
        f"<i>Full: {funding_addr}</i>\n\n"
        f"<b>CELO Balance:</b>\n<code>{bal:.4f} CELO</code>\n\n"
        f"<b>Subsidy per low-gas payment:</b>\n{subsidy_amt} CELO\n\n"
        f"<b>Estimated subsidies remaining:</b>\n<b>{est_subsidies}</b> payments"
        f"{low_warning}"
    )

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_admin_back_keyboard(),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data == "admin_settings")
async def cb_admin_settings(callback: CallbackQuery) -> None:
    """Inspect environment configuration and runtime flags."""
    user_id = callback.from_user.id if callback.from_user else 0
    if not is_authorized_admin(user_id):
        return

    is_paused = await db.is_bot_paused()
    status_label = "⏸ Paused" if is_paused else "🟢 Operational"
    dry_label = "🟡 Enabled (Simulated)" if config.dry_run else "🟢 Disabled (Live Mainnet)"

    text = (
        "⚙️ <b>SYSTEM SETTINGS</b>\n\n"
        f"• <b>Status:</b> {status_label}\n"
        f"• <b>Payment Amount:</b> ${config.usat_payment_amount:.2f} USAT\n"
        f"• <b>Gas Subsidy:</b> {config.celo_funding_amount} CELO\n"
        f"• <b>Network:</b> {config.network_name} (Chain ID: {config.celo_chain_id})\n"
        f"• <b>RPC:</b> <code>{config.celo_rpc_url}</code>\n"
        f"• <b>USAT Contract:</b> <code>{config.usat_contract_address}</code>\n"
        f"• <b>Dry Run Mode:</b> {dry_label}\n"
        f"• <b>Admin ID:</b> <code>{config.admin_telegram_id}</code>\n"
        f"• <b>WebApp Port:</b> {config.webapp_port}\n"
        f"• <b>Encryption:</b> AES-256-GCM Active"
    )

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_admin_back_keyboard(),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data == "admin_toggle_pause")
async def cb_admin_toggle_pause(callback: CallbackQuery) -> None:
    """Pause or resume USAT payments."""
    user_id = callback.from_user.id if callback.from_user else 0
    if not is_authorized_admin(user_id):
        return

    current = await db.is_bot_paused()
    new_state = not current
    await db.set_bot_paused(new_state)

    status_str = "paused" if new_state else "resumed"
    await callback.answer(f"Payments successfully {status_str}!", show_alert=True)

    panel_text = (
        "👑 <b>ADMIN PANEL</b>\n\n"
        f"Payment status updated: <b>{'⏸ PAUSED' if new_state else '🟢 OPERATIONAL'}</b>.\n\n"
        "Select an administrative action below:"
    )
    if callback.message:
        await callback.message.edit_text(
            text=panel_text,
            reply_markup=get_admin_menu_keyboard(new_state),
            parse_mode="HTML",
        )
