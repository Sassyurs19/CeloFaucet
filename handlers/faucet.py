"""
Claim flow and FSM handler for user wallet address submissions.
Implements in-place message morphing, automatic cleanup of user inputs,
validation, balance verification, transaction tracking, and result delivery.
"""

from __future__ import annotations

import logging
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import config
from database import db
from wallet import wallet_manager
from celo import celo_client
from services.validation import validate_celo_address
from services.transaction_service import transaction_service
from keyboards.main import (
    get_cancel_keyboard,
    get_success_keyboard,
    get_back_to_main_keyboard,
)

logger = logging.getLogger(__name__)
router = Router(name="faucet")


class ClaimStates(StatesGroup):
    """FSM states for address collection."""
    waiting_for_address = State()


@router.callback_query(F.data == "claim_start")
@router.message(Command("claim"))
async def start_claim_flow(event: Message | CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Initiate the CELO claim process."""
    user_id = event.from_user.id if event.from_user else 0

    # Auto-cleanup command message if sent as text
    if isinstance(event, Message):
        try:
            await event.delete()
        except Exception:
            pass

    # 1. Check if faucet is paused
    if await db.is_faucet_paused():
        paused_text = (
            "⏸ <b>FAUCET PAUSED</b>\n\n"
            "The faucet is temporarily unavailable.\n\n"
            "Please try again later."
        )
        if isinstance(event, CallbackQuery) and event.message:
            await event.message.edit_text(
                text=paused_text,
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML",
            )
            await event.answer()
        elif isinstance(event, Message):
            await event.answer(
                text=paused_text,
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML",
            )
        return

    # 2. Check for active transaction in progress
    active_claim = await db.get_active_claim_for_user(user_id)
    if active_claim:
        wait_text = (
            "⏳ <b>TRANSACTION IN PROGRESS</b>\n\n"
            "You already have a claim currently being processed on the Celo network.\n\n"
            f"<b>Request ID:</b> <code>{active_claim.get('request_id')}</code>\n\n"
            "Please wait a moment for it to complete."
        )
        if isinstance(event, CallbackQuery) and event.message:
            await event.message.edit_text(
                text=wait_text,
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML",
            )
            await event.answer()
        elif isinstance(event, Message):
            await event.answer(
                text=wait_text,
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML",
            )
        return

    # 3. Set FSM state and prompt for address
    await state.set_state(ClaimStates.waiting_for_address)
    prompt_text = (
        "💸 <b>REQUEST CELO</b>\n\n"
        "Send your Celo wallet address.\n\n"
        "<b>Example:</b>\n"
        "<code>0x1234567890abcdef1234567890abcdef12345678</code>\n\n"
        "Only send a valid Celo/EVM wallet address."
    )

    if isinstance(event, CallbackQuery) and event.message:
        await event.message.edit_text(
            text=prompt_text,
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML",
        )
        await state.update_data(prompt_msg_id=event.message.message_id)
        await event.answer()
    elif isinstance(event, Message):
        sent_msg = await event.answer(
            text=prompt_text,
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML",
        )
        await state.update_data(prompt_msg_id=sent_msg.message_id)


@router.message(ClaimStates.waiting_for_address, F.text)
async def process_address_submission(message: Message, state: FSMContext, bot: Bot) -> None:
    """Validate submitted address, verify balance, and dispatch payout with clean in-place editing."""
    raw_address = message.text.strip() if message.text else ""
    user_id = message.from_user.id if message.from_user else 0
    chat_id = message.chat.id

    # 1. Clean up user's typed message immediately
    try:
        await message.delete()
    except Exception:
        pass

    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")

    # Allow canceling via /cancel or /start
    if raw_address in ("/start", "/cancel"):
        await state.clear()
        if prompt_msg_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=prompt_msg_id)
            except Exception:
                pass
        return

    await db.update_user_activity(user_id)

    # 2. Address Format & EVM Checksum Validation
    is_valid, checksum_addr, err_msg = validate_celo_address(raw_address)
    if not is_valid or not checksum_addr:
        error_text = (
            "❌ <b>Invalid Celo address</b>\n\n"
            f"{err_msg or 'Please send a valid Celo wallet address.'}\n\n"
            "<b>Example:</b>\n"
            "<code>0x1234567890abcdef1234567890abcdef12345678</code>"
        )
        # Edit existing prompt message instead of polluting chat with new error messages
        if prompt_msg_id:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=prompt_msg_id,
                    text=error_text,
                    reply_markup=get_cancel_keyboard(),
                    parse_mode="HTML",
                )
                return
            except Exception:
                pass

        # Fallback if editing failed
        sent_err = await message.answer(
            text=error_text,
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML",
        )
        await state.update_data(prompt_msg_id=sent_err.message_id)
        return

    # Clear state as valid address is received
    await state.clear()

    # 3. Show Address Verified message (morphing prompt message)
    truncated_dest = wallet_manager.truncate_address(checksum_addr, prefix_len=8, suffix_len=6)
    verified_text = (
        "🔍 <b>ADDRESS VERIFIED</b>\n\n"
        "<b>Network:</b>\n"
        f"{config.network_name}\n\n"
        "<b>Amount:</b>\n"
        f"{config.claim_amount} CELO\n\n"
        "<b>Destination:</b>\n"
        f"<code>{truncated_dest}</code>\n\n"
        "Checking faucet balance..."
    )

    status_msg_id = prompt_msg_id
    if status_msg_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text=verified_text,
                parse_mode="HTML",
            )
        except Exception:
            sent_msg = await message.answer(text=verified_text, parse_mode="HTML")
            status_msg_id = sent_msg.message_id
    else:
        sent_msg = await message.answer(text=verified_text, parse_mode="HTML")
        status_msg_id = sent_msg.message_id

    # 4. Fast pre-check for faucet balance
    if not config.dry_run and wallet_manager.is_configured:
        balance = await celo_client.get_balance(wallet_manager.address)
        needed = config.claim_amount + config.min_gas_reserve
        if balance < needed:
            unavailable_text = (
                "⚠️ <b>Faucet temporarily unavailable</b>\n\n"
                "The faucet wallet does not currently have enough CELO.\n\n"
                "Please try again later."
            )
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text=unavailable_text,
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML",
            )
            return

    # 5. Update status to Processing
    processing_text = (
        "⏳ <b>SENDING TRANSACTION</b>\n\n"
        "<b>Network:</b>\n"
        f"{config.network_name}\n\n"
        "<b>Amount:</b>\n"
        f"{config.claim_amount} CELO\n\n"
        "<b>Destination:</b>\n"
        f"<code>{truncated_dest}</code>\n\n"
        "Submitting transaction to the Celo network..."
    )
    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=status_msg_id,
        text=processing_text,
        parse_mode="HTML",
    )

    # 6. Execute Payout via TransactionService
    result = await transaction_service.execute_payout(
        telegram_id=user_id,
        destination_address=checksum_addr,
    )

    if result.success and result.tx_hash:
        dry_tag = " <i>(Simulated DRY_RUN)</i>" if result.is_dry_run else ""
        success_text = (
            "✅ <b>CELO SENT</b>\n\n"
            "<b>Amount:</b>\n"
            f"{config.claim_amount} CELO\n\n"
            "<b>Network:</b>\n"
            f"{config.network_name}\n\n"
            "<b>To:</b>\n"
            f"<code>{checksum_addr}</code>\n\n"
            "<b>Status:</b>\n"
            f"🟢 Confirmed{dry_tag}\n\n"
            "<b>Transaction:</b>\n"
            f"<code>{result.tx_hash}</code>\n\n"
            "Thank you for using the faucet."
        )
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg_id,
            text=success_text,
            reply_markup=get_success_keyboard(result.tx_hash),
            parse_mode="HTML",
        )
    else:
        fail_text = (
            "❌ <b>TRANSACTION FAILED</b>\n\n"
            f"{result.error_message or 'An unexpected error occurred.'}\n\n"
            "Your claim could not be completed. Please try again later."
        )
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg_id,
            text=fail_text,
            reply_markup=get_back_to_main_keyboard(),
            parse_mode="HTML",
        )
