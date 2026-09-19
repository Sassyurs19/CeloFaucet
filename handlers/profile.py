"""
Profile handler for Telegram Celo USAT Payment Bot.
Allows users to view their registration details, masked mobile number, wallet count,
total USAT payments, and edit their name and mobile number.
"""

from __future__ import annotations

import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database import db
from keyboards.main import get_profile_keyboard, get_cancel_keyboard, get_back_to_main_keyboard

logger = logging.getLogger(__name__)
router = Router(name="profile")


class EditProfileStates(StatesGroup):
    """FSM states for editing profile fields."""
    editing_name = State()
    editing_mobile = State()


def mask_mobile(mobile: str | None) -> str:
    """Mask mobile number for privacy (e.g. +91******1234)."""
    if not mobile:
        return "Not Set"
    cleaned = mobile.strip()
    if len(cleaned) <= 6:
        return cleaned
    prefix = cleaned[:3]
    suffix = cleaned[-4:]
    masked_middle = "*" * (len(cleaned) - 7) if len(cleaned) > 7 else "***"
    return f"{prefix}{masked_middle}{suffix}"


@router.callback_query(F.data == "profile_view")
async def cb_view_profile(callback: CallbackQuery, state: FSMContext) -> None:
    """Display user profile with wallet count, payments count, and total USAT paid."""
    await state.clear()
    user_id = callback.from_user.id if callback.from_user else 0
    profile = await db.get_user_profile(user_id) or {}
    
    full_name = profile.get("full_name") or profile.get("first_name") or "Anonymous"
    mobile = profile.get("mobile_number") or ""
    masked_mob = mask_mobile(mobile)
    
    wallets_count = await db.get_user_wallet_count(user_id)
    payments_count = await db.get_user_payments_count(user_id)
    total_paid = await db.get_user_total_paid(user_id)

    profile_text = (
        "👤 <b>MY PROFILE</b>\n\n"
        "Name:\n"
        f"<b>{full_name}</b>\n\n"
        "Mobile:\n"
        f"<code>{masked_mob}</code>\n\n"
        "Wallets:\n"
        f"<b>{wallets_count}</b>\n\n"
        "Payments:\n"
        f"<b>{payments_count}</b>\n\n"
        "Total Paid:\n"
        f"<b>${total_paid:.2f} USAT</b>"
    )

    if callback.message:
        try:
            await callback.message.edit_text(
                text=profile_text,
                reply_markup=get_profile_keyboard(),
                parse_mode="HTML",
            )
        except Exception:
            await callback.message.answer(
                text=profile_text,
                reply_markup=get_profile_keyboard(),
                parse_mode="HTML",
            )
    await callback.answer()


@router.callback_query(F.data == "profile_edit_name")
async def cb_edit_name_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user for new full name."""
    await state.set_state(EditProfileStates.editing_name)
    prompt_text = (
        "✏️ <b>EDIT NAME</b>\n\n"
        "<i>Please enter your updated full name:</i>"
    )
    if callback.message:
        await callback.message.edit_text(
            text=prompt_text,
            reply_markup=get_cancel_keyboard(back_callback="profile_view"),
            parse_mode="HTML",
        )
        await state.update_data(prompt_msg_id=callback.message.message_id)
    await callback.answer()


@router.message(EditProfileStates.editing_name, F.text)
async def process_new_name(message: Message, state: FSMContext, bot: Bot) -> None:
    """Update full name in database."""
    user_id = message.from_user.id if message.from_user else 0
    new_name = message.text.strip() if message.text else ""

    try:
        await message.delete()
    except Exception:
        pass

    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")

    if not new_name or len(new_name) < 2:
        err_text = "❌ <i>Name must be at least 2 characters. Please try again:</i>"
        if prompt_msg_id:
            try:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=prompt_msg_id,
                    text=err_text,
                    reply_markup=get_cancel_keyboard("profile_view"),
                    parse_mode="HTML",
                )
                return
            except Exception:
                pass
        return

    await db.update_user_name(user_id, new_name)
    await state.clear()

    confirm_text = (
        "✅ <b>NAME UPDATED</b>\n\n"
        f"Your name has been updated to: <b>{new_name}</b>"
    )
    if prompt_msg_id:
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=prompt_msg_id,
                text=confirm_text,
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML",
            )
            return
        except Exception:
            pass

    await message.answer(text=confirm_text, reply_markup=get_back_to_main_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "profile_edit_mobile")
async def cb_edit_mobile_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user for new mobile number."""
    await state.set_state(EditProfileStates.editing_mobile)
    prompt_text = (
        "📱 <b>EDIT MOBILE</b>\n\n"
        "<i>Please enter your updated mobile number (e.g. +919876543210):</i>"
    )
    if callback.message:
        await callback.message.edit_text(
            text=prompt_text,
            reply_markup=get_cancel_keyboard(back_callback="profile_view"),
            parse_mode="HTML",
        )
        await state.update_data(prompt_msg_id=callback.message.message_id)
    await callback.answer()


@router.message(EditProfileStates.editing_mobile, F.text)
async def process_new_mobile(message: Message, state: FSMContext, bot: Bot) -> None:
    """Update mobile number in database."""
    user_id = message.from_user.id if message.from_user else 0
    raw_mobile = message.text.strip() if message.text else ""

    try:
        await message.delete()
    except Exception:
        pass

    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")

    cleaned_mobile = "".join(ch for ch in raw_mobile if ch.isdigit() or ch == "+")
    if len(cleaned_mobile) < 7:
        err_text = "❌ <i>Please enter a valid mobile number (at least 7 digits):</i>"
        if prompt_msg_id:
            try:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=prompt_msg_id,
                    text=err_text,
                    reply_markup=get_cancel_keyboard("profile_view"),
                    parse_mode="HTML",
                )
                return
            except Exception:
                pass
        return

    await db.update_user_mobile(user_id, cleaned_mobile)
    await state.clear()

    confirm_text = (
        "✅ <b>MOBILE UPDATED</b>\n\n"
        f"Your mobile number has been updated to: <code>{mask_mobile(cleaned_mobile)}</code>"
    )
    if prompt_msg_id:
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=prompt_msg_id,
                text=confirm_text,
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML",
            )
            return
        except Exception:
            pass

    await message.answer(text=confirm_text, reply_markup=get_back_to_main_keyboard(), parse_mode="HTML")
