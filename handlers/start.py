"""
Start and onboarding handlers for Telegram Celo USAT Payment Bot.
Handles user registration (Full Name & Mobile Number), user activity,
the standardized user menu, and /help information.
"""

from __future__ import annotations

import logging
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import config
from database import db
from keyboards.main import get_main_menu_keyboard, get_back_to_main_keyboard

logger = logging.getLogger(__name__)
router = Router(name="start")


class RegistrationStates(StatesGroup):
    """FSM states for initial onboarding."""
    waiting_for_name = State()
    waiting_for_mobile = State()


def get_main_menu_text() -> str:
    """Generate the standardized rich text main menu message."""
    dry_tag = "\n\n<i>⚠️ Test Mode (DRY_RUN): Transactions are simulated.</i>" if config.dry_run else ""
    return (
        "⚡ <b>CELO USAT</b>\n\n"
        "<i>Manage your wallets and make USAT payments.</i>"
        f"{dry_tag}"
    )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot: Bot) -> None:
    """Handle /start command, check registration, or prompt onboarding."""
    try:
        await message.delete()
    except Exception:
        pass

    user = message.from_user
    if not user:
        return

    # Track in database
    await db.get_or_create_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    # Check if user has already completed registration
    is_registered = await db.is_user_registered(user.id)
    if not is_registered:
        # Start onboarding flow
        await state.set_state(RegistrationStates.waiting_for_name)
        welcome_text = (
            "👋 <b>Welcome</b>\n\n"
            "<i>Welcome to the Celo USAT payment bot.</i>\n\n"
            "<i>Before making a payment, please provide your details.</i>\n\n"
            "👤 <b>YOUR NAME</b>\n"
            "<i>Enter your full name.</i>"
        )
        sent = await message.answer(text=welcome_text, parse_mode="HTML")
        await state.update_data(prompt_msg_id=sent.message_id)
        return

    # User is registered, show main menu
    await state.clear()
    sent_menu = await message.answer(
        text=get_main_menu_text(),
        reply_markup=get_main_menu_keyboard(is_admin=config.is_admin(user.id)),
        parse_mode="HTML",
    )
    await state.update_data(last_menu_msg_id=sent_menu.message_id)


@router.message(RegistrationStates.waiting_for_name, F.text)
async def process_registration_name(message: Message, state: FSMContext, bot: Bot) -> None:
    """Capture full name and prompt for mobile number."""
    user_id = message.from_user.id if message.from_user else 0
    full_name = message.text.strip() if message.text else ""

    try:
        await message.delete()
    except Exception:
        pass

    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")

    if not full_name or len(full_name) < 2:
        err_text = (
            "👤 <b>YOUR NAME</b>\n\n"
            "❌ <i>Please enter a valid full name (at least 2 characters):</i>"
        )
        if prompt_msg_id:
            try:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=prompt_msg_id,
                    text=err_text,
                    parse_mode="HTML",
                )
                return
            except Exception:
                pass
        sent = await message.answer(text=err_text, parse_mode="HTML")
        await state.update_data(prompt_msg_id=sent.message_id)
        return

    await db.update_user_name(user_id, full_name)
    await state.update_data(saved_full_name=full_name)
    await state.set_state(RegistrationStates.waiting_for_mobile)

    mobile_prompt = (
        f"<b>👤 Name:</b> <code>{full_name}</code>\n\n"
        "📱 <b>MOBILE NUMBER</b>\n\n"
        "<i>Enter your mobile number (e.g. +919876543210).</i>"
    )

    if prompt_msg_id:
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=prompt_msg_id,
                text=mobile_prompt,
                parse_mode="HTML",
            )
            return
        except Exception:
            pass

    sent = await message.answer(text=mobile_prompt, parse_mode="HTML")
    await state.update_data(prompt_msg_id=sent.message_id)


@router.message(RegistrationStates.waiting_for_mobile, F.text)
async def process_registration_mobile(message: Message, state: FSMContext, bot: Bot) -> None:
    """Capture mobile number, finalize registration, and present main menu."""
    user_id = message.from_user.id if message.from_user else 0
    raw_mobile = message.text.strip() if message.text else ""

    try:
        await message.delete()
    except Exception:
        pass

    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")

    # Basic mobile sanitization: digits and optional leading +
    cleaned_mobile = "".join(ch for ch in raw_mobile if ch.isdigit() or ch == "+")
    if len(cleaned_mobile) < 7:
        err_text = (
            "📱 <b>MOBILE NUMBER</b>\n\n"
            "❌ <i>Please enter a valid mobile number:</i>"
        )
        if prompt_msg_id:
            try:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=prompt_msg_id,
                    text=err_text,
                    parse_mode="HTML",
                )
                return
            except Exception:
                pass
        sent = await message.answer(text=err_text, parse_mode="HTML")
        await state.update_data(prompt_msg_id=sent.message_id)
        return

    await db.update_user_mobile(user_id, cleaned_mobile)
    await state.clear()

    # Show registered confirmation then main menu
    complete_text = (
        "✅ <b>REGISTRATION COMPLETE</b>\n\n"
        "<i>Your profile has been saved.</i>\n\n"
        f"{get_main_menu_text()}"
    )

    if prompt_msg_id:
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=prompt_msg_id,
                text=complete_text,
                reply_markup=get_main_menu_keyboard(is_admin=config.is_admin(user_id)),
                parse_mode="HTML",
            )
            return
        except Exception:
            pass

    await message.answer(
        text=complete_text,
        reply_markup=get_main_menu_keyboard(is_admin=config.is_admin(user_id)),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """Return to main menu from any sub-screen."""
    await state.clear()
    user = callback.from_user
    if user:
        await db.update_user_activity(user.id)

    is_admin = config.is_admin(user.id if user else 0)
    text = get_main_menu_text()

    if callback.message:
        try:
            await callback.message.edit_text(
                text=text,
                reply_markup=get_main_menu_keyboard(is_admin=is_admin),
                parse_mode="HTML",
            )
        except Exception:
            await callback.message.answer(
                text=text,
                reply_markup=get_main_menu_keyboard(is_admin=is_admin),
                parse_mode="HTML",
            )
    await callback.answer()


@router.callback_query(F.data == "help_view")
@router.message(Command("help"))
async def show_help(event: Message | CallbackQuery) -> None:
    """Display clean, informative guide on USAT payments and wallet options."""
    if isinstance(event, Message):
        try:
            await event.delete()
        except Exception:
            pass

    help_text = (
        "ℹ️ <b>CELO USAT PAYMENT BOT — HELP</b>\n\n"
        "⚡ <b>What does this bot do?</b>\n"
        "This bot allows you to connect or import your Celo wallets and make secure "
        "<b>$2.00 USAT</b> payments to verified receiving addresses.\n\n"
        "👛 <b>Dual Wallet Options:</b>\n"
        "• <b>🔗 Connect Wallet (Recommended):</b> Non-custodial connection via Telegram WebApp. "
        "Never requests your seed phrase or private key. You sign transactions in your wallet.\n"
        "• <b>📥 Import Wallet:</b> Single-key import encrypted at rest via AES-256-GCM. "
        "Allows automated signing without manual confirmation.\n\n"
        "⛽ <b>Automatic 0.05 CELO Gas Subsidy:</b>\n"
        "If your selected wallet has insufficient CELO to cover network gas fees, "
        "the bot automatically sends <b>0.05 CELO</b> from the dedicated funding wallet "
        "and completes your USAT payment once confirmed!\n\n"
        "🔒 <b>Security & Privacy:</b>\n"
        "• Private keys are encrypted at rest with authenticated AES-256-GCM.\n"
        "• Messages containing private keys are wiped immediately from chat.\n"
        "• All payments are verified on-chain via Celoscan."
    )

    if isinstance(event, CallbackQuery) and event.message:
        await event.message.edit_text(
            text=help_text,
            reply_markup=get_back_to_main_keyboard(),
            parse_mode="HTML",
        )
        await event.answer()
    elif isinstance(event, Message):
        await event.answer(
            text=help_text,
            reply_markup=get_back_to_main_keyboard(),
            parse_mode="HTML",
        )
