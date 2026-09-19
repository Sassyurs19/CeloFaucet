"""
Wallet management handlers for Celo USAT Payment Bot.
Implements the Dual Wallet system:
- Option A: Non-custodial Connect Wallet (Telegram WebApp / Web3)
- Option B: Encrypted Import Wallet (AES-256-GCM private key with instant message deletion)
Enforces multi-wallet isolation per user and provides real-time balances.
"""

from __future__ import annotations

import json
import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from eth_account import Account
from web3 import Web3

from config import config
from database import db
from wallet import wallet_manager
from celo import celo_client
from services.encryption import encryption_service
from services.validation import validate_celo_address
from keyboards.main import (
    get_wallets_menu_keyboard,
    get_add_wallet_choice_keyboard,
    get_connect_wallet_keyboard,
    get_wallet_detail_keyboard,
    get_cancel_keyboard,
    get_back_to_main_keyboard,
)

logger = logging.getLogger(__name__)
router = Router(name="wallet_mgmt")


class AddWalletStates(StatesGroup):
    """FSM states for wallet addition flows."""
    waiting_for_import_name = State()
    waiting_for_import_key = State()
    waiting_for_connect_address = State()
    waiting_for_connect_name = State()
    waiting_for_rename = State()


# --- Wallets List Screen ---

@router.callback_query(F.data == "wallets_list")
async def cb_wallets_list(callback: CallbackQuery, state: FSMContext) -> None:
    """Display user's connected and imported Celo wallets with real-time balances."""
    await state.clear()
    user_id = callback.from_user.id if callback.from_user else 0
    wallets = await db.get_user_wallets(user_id)

    if not wallets:
        empty_text = (
            "👛 <b>MY WALLETS</b>\n\n"
            "<i>You don't have any connected Celo wallets yet.</i>\n\n"
            "Add a wallet to start making USAT payments."
        )
        if callback.message:
            await callback.message.edit_text(
                text=empty_text,
                reply_markup=get_wallets_menu_keyboard([]),
                parse_mode="HTML",
            )
        await callback.answer()
        return

    # Build wallet display text with live USAT and CELO balances
    lines = ["👛 <b>MY WALLETS</b>\n\n<i>Your connected Celo wallets.</i>\n"]
    for idx, w in enumerate(wallets, start=1):
        w_name = w["wallet_name"]
        w_addr = w["address"]
        trunc_addr = wallet_manager.truncate_address(w_addr, prefix_len=6, suffix_len=4)
        w_type_str = "🔗 Connected" if w.get("wallet_type") == "connected" else "📥 Imported"

        # Query live balances
        celo_bal = await celo_client.get_celo_balance(w_addr)
        _, usat_bal_str = await celo_client.get_usat_balance(w_addr)

        lines.append(
            f"👛 <b>{w_name}</b> ({w_type_str})\n"
            f"<code>{trunc_addr}</code>\n"
            f"USAT:\n<code>{usat_bal_str}</code>\n"
            f"CELO:\n<code>{celo_bal:.6f}</code>\n"
        )

    text = "\n".join(lines)
    if callback.message:
        try:
            await callback.message.edit_text(
                text=text,
                reply_markup=get_wallets_menu_keyboard(wallets),
                parse_mode="HTML",
            )
        except Exception:
            await callback.message.answer(
                text=text,
                reply_markup=get_wallets_menu_keyboard(wallets),
                parse_mode="HTML",
            )
    await callback.answer()


# --- Add Wallet Choice Screen ---

@router.callback_query(F.data == "wallet_add_choose")
async def cb_add_wallet_choice(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to choose between Option A (Connect) and Option B (Import)."""
    await state.clear()
    choice_text = (
        "➕ <b>ADD WALLET</b>\n\n"
        "<i>Choose how you want to add your Celo wallet.</i>"
    )
    if callback.message:
        await callback.message.edit_text(
            text=choice_text,
            reply_markup=get_add_wallet_choice_keyboard(),
            parse_mode="HTML",
        )
    await callback.answer()


# =========================================================================
# OPTION A: 🔗 CONNECT WALLET (NON-CUSTODIAL)
# =========================================================================

@router.callback_query(F.data == "wallet_add_connect")
async def cb_add_connect_intro(callback: CallbackQuery, state: FSMContext) -> None:
    """Show non-custodial security information and launch WebApp connection."""
    await state.clear()
    user_id = callback.from_user.id if callback.from_user else 0

    intro_text = (
        "🔐 <b>Secure Wallet Connection</b>\n\n"
        "<i>Connect your Celo-compatible wallet securely. "
        "Your recovery phrase and private key will never be requested by this bot.</i>"
    )
    if callback.message:
        await callback.message.edit_text(
            text=intro_text,
            reply_markup=get_connect_wallet_keyboard(user_id),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data == "wallet_connect_manual")
async def cb_connect_manual_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    """Fallback manual input for public address when external WebApp domain is not configured."""
    await state.set_state(AddWalletStates.waiting_for_connect_address)
    prompt_text = (
        "⌨️ <b>CONNECT WALLET (MANUAL ADDRESS)</b>\n\n"
        "<i>Send your Celo wallet address (e.g. from MetaMask, Valora, or OKX).</i>\n\n"
        "⚠️ <i>Only send your public 0x address. Never send a seed phrase or private key!</i>\n\n"
        "<b>Example:</b>\n"
        "<code>0x471EcE3750Da237f93B8E339c536989b8978a438</code>"
    )
    if callback.message:
        await callback.message.edit_text(
            text=prompt_text,
            reply_markup=get_cancel_keyboard("wallet_add_connect"),
            parse_mode="HTML",
        )
        await state.update_data(prompt_msg_id=callback.message.message_id)
    await callback.answer()


@router.message(AddWalletStates.waiting_for_connect_address, F.text)
async def process_connect_address(message: Message, state: FSMContext, bot: Bot) -> None:
    """Validate submitted public address for connected wallet."""
    raw_addr = message.text.strip() if message.text else ""
    try:
        await message.delete()
    except Exception:
        pass

    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")

    is_valid, checksummed_addr, err = validate_celo_address(raw_addr)
    if not is_valid or not checksummed_addr:
        err_text = (
            "❌ <b>Invalid Celo address</b>\n\n"
            f"{err or 'Please enter a valid 0x hex address.'}\n\n"
            "<i>Never send a seed phrase or private key!</i>"
        )
        if prompt_msg_id:
            try:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=prompt_msg_id,
                    text=err_text,
                    reply_markup=get_cancel_keyboard("wallet_add_connect"),
                    parse_mode="HTML",
                )
                return
            except Exception:
                pass
        return

    # Store verified address and ask for wallet name
    await state.update_data(connected_address=checksummed_addr)
    await state.set_state(AddWalletStates.waiting_for_connect_name)

    name_prompt = (
        "✅ <b>WALLET CONNECTED</b>\n\n"
        f"<code>Address: {checksummed_addr}</code>\n\n"
        "🏷️ <b>Wallet Name</b>\n\n"
        "<i>Give this wallet a name so you can identify it later.</i>\n\n"
        "<b>Example:</b>\n"
        "<code>Main Wallet</code>"
    )

    if prompt_msg_id:
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=prompt_msg_id,
                text=name_prompt,
                parse_mode="HTML",
            )
            return
        except Exception:
            pass

    sent = await message.answer(text=name_prompt, parse_mode="HTML")
    await state.update_data(prompt_msg_id=sent.message_id)


@router.message(AddWalletStates.waiting_for_connect_name, F.text)
async def process_connect_name(message: Message, state: FSMContext, bot: Bot) -> None:
    """Save connected wallet with validated name and display balances."""
    user_id = message.from_user.id if message.from_user else 0
    w_name = message.text.strip() if message.text else ""

    try:
        await message.delete()
    except Exception:
        pass

    data = await state.get_data()
    address = data.get("connected_address", "")
    prompt_msg_id = data.get("prompt_msg_id")

    if not w_name or len(w_name) < 2:
        w_name = "Connected Wallet"

    # Save to database as connected (NO private key stored!)
    await db.add_user_wallet(
        telegram_id=user_id,
        wallet_name=w_name,
        address=address,
        wallet_type="connected",
        encrypted_private_key=None,
    )
    await state.clear()

    # Query live balances
    celo_bal = await celo_client.get_celo_balance(address)
    _, usat_bal = await celo_client.get_usat_balance(address)

    success_text = (
        "✅ <b>WALLET CONNECTED</b>\n\n"
        f"Wallet:\n<b>{w_name}</b>\n\n"
        f"Address:\n<code>{wallet_manager.truncate_address(address, 6, 4)}</code>\n\n"
        f"USAT:\n<b>{usat_bal}</b>\n\n"
        f"CELO:\n<b>{celo_bal:.6f}</b>\n\n"
        "<i>Your non-custodial wallet is ready to use.</i>"
    )

    if prompt_msg_id:
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=prompt_msg_id,
                text=success_text,
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML",
            )
            return
        except Exception:
            pass

    await message.answer(text=success_text, reply_markup=get_back_to_main_keyboard(), parse_mode="HTML")


# Handler for WebApp data returned to bot via window.Telegram.WebApp.sendData
@router.message(F.web_app_data)
async def process_webapp_data(message: Message, state: FSMContext) -> None:
    """Receive data sent back from Telegram WebApp."""
    raw_data = message.web_app_data.data if message.web_app_data else ""
    try:
        payload = json.loads(raw_data)
    except Exception:
        return

    action = payload.get("action")
    if action == "wallet_connected":
        address = payload.get("address", "")
        is_valid, chk, _ = validate_celo_address(address)
        if is_valid and chk:
            await state.update_data(connected_address=chk)
            await state.set_state(AddWalletStates.waiting_for_connect_name)
            name_prompt = (
                "✅ <b>WALLET CONNECTED</b>\n\n"
                f"<code>Address: {chk}</code>\n\n"
                "🏷️ <b>Wallet Name</b>\n\n"
                "<i>Give this wallet a name so you can identify it later.</i>\n\n"
                "<b>Example:</b>\n"
                "<code>Main Wallet</code>"
            )
            sent = await message.answer(text=name_prompt, parse_mode="HTML")
            await state.update_data(prompt_msg_id=sent.message_id)


# =========================================================================
# OPTION B: 📥 IMPORT WALLET (ENCRYPTED PRIVATE KEY)
# =========================================================================

@router.callback_query(F.data == "wallet_add_import")
async def cb_add_import_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Step 1 of Import Wallet: Prompt for wallet name."""
    await state.set_state(AddWalletStates.waiting_for_import_name)
    prompt_text = (
        "➕ <b>ADD WALLET</b>\n\n"
        "<i>Enter a name for this wallet.</i>\n\n"
        "Example:\n"
        "<code>Main Wallet</code>"
    )
    if callback.message:
        await callback.message.edit_text(
            text=prompt_text,
            reply_markup=get_cancel_keyboard("wallet_add_choose"),
            parse_mode="HTML",
        )
        await state.update_data(prompt_msg_id=callback.message.message_id)
    await callback.answer()


@router.message(AddWalletStates.waiting_for_import_name, F.text)
async def process_import_name(message: Message, state: FSMContext, bot: Bot) -> None:
    """Step 2 of Import Wallet: Prompt for private key with immediate deletion notice."""
    w_name = message.text.strip() if message.text else ""
    try:
        await message.delete()
    except Exception:
        pass

    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")

    if not w_name or len(w_name) < 2:
        w_name = "My Celo Wallet"

    await state.update_data(import_wallet_name=w_name)
    await state.set_state(AddWalletStates.waiting_for_import_key)

    key_prompt = (
        f"<b>Wallet Name:</b> <code>{w_name}</code>\n\n"
        "🔐 <b>PRIVATE KEY</b>\n\n"
        "<i>Send the private key of the Celo wallet.</i>\n\n"
        "<i>The key will be encrypted immediately and the Telegram message containing it will be deleted.</i>\n\n"
        "⚠️ <i>Only add a wallet that you control. Never share seed phrases.</i>"
    )

    if prompt_msg_id:
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=prompt_msg_id,
                text=key_prompt,
                reply_markup=get_cancel_keyboard("wallet_add_choose"),
                parse_mode="HTML",
            )
            return
        except Exception:
            pass

    sent = await message.answer(
        text=key_prompt,
        reply_markup=get_cancel_keyboard("wallet_add_choose"),
        parse_mode="HTML",
    )
    await state.update_data(prompt_msg_id=sent.message_id)


@router.message(AddWalletStates.waiting_for_import_key, F.text)
async def process_import_key(message: Message, state: FSMContext, bot: Bot) -> None:
    """
    Step 3 of Import Wallet:
    1. Immediately delete the message containing the private key.
    2. Validate private key format.
    3. Derive public address.
    4. Encrypt with AES-256-GCM.
    5. Save encrypted ciphertext to database.
    6. Wipe plaintext key from memory.
    7. Display confirmation with balances.
    """
    raw_key = message.text.strip() if message.text else ""
    user_id = message.from_user.id if message.from_user else 0

    # 1. IMMEDIATELY delete user's message containing private key
    try:
        await message.delete()
    except Exception as e:
        logger.warning("Failed to delete user private key message: %s", e)

    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    w_name = data.get("import_wallet_name", "Imported Wallet")

    # 2. Validate private key format
    clean_key = raw_key
    if clean_key.startswith("0x") or clean_key.startswith("0X"):
        clean_key = clean_key[2:]

    # Hex verification
    if len(clean_key) != 64:
        err_text = (
            "❌ <b>Invalid Private Key</b>\n\n"
            "<i>A standard EVM/Celo private key must have exactly 64 hexadecimal characters (32 bytes). "
            "Seed phrases (12/24 words) are not supported.</i>"
        )
        if prompt_msg_id:
            try:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=prompt_msg_id,
                    text=err_text,
                    reply_markup=get_cancel_keyboard("wallet_add_choose"),
                    parse_mode="HTML",
                )
                return
            except Exception:
                pass
        return

    try:
        # Derive address
        formatted_key = "0x" + clean_key
        account = Account.from_key(formatted_key)
        derived_address = Web3.to_checksum_address(account.address)
    except Exception as e:
        err_text = "❌ <b>Invalid Private Key</b>\n\n<i>Could not derive wallet from the provided key.</i>"
        if prompt_msg_id:
            try:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=prompt_msg_id,
                    text=err_text,
                    reply_markup=get_cancel_keyboard("wallet_add_choose"),
                    parse_mode="HTML",
                )
                return
            except Exception:
                pass
        return

    # 3. Encrypt immediately via AES-256-GCM
    encrypted_key = encryption_service.encrypt(formatted_key)

    # Wipe key variables
    raw_key = None
    clean_key = None
    formatted_key = None
    account = None

    # 4. Save to database
    await db.add_user_wallet(
        telegram_id=user_id,
        wallet_name=w_name,
        address=derived_address,
        wallet_type="imported",
        encrypted_private_key=encrypted_key,
    )
    await state.clear()

    # 5. Query live balances
    celo_bal = await celo_client.get_celo_balance(derived_address)
    _, usat_bal = await celo_client.get_usat_balance(derived_address)

    success_text = (
        "✅ <b>WALLET ADDED</b>\n\n"
        "Wallet:\n"
        f"<b>{w_name}</b>\n\n"
        "Address:\n"
        f"<code>{derived_address}</code>\n\n"
        "USAT:\n"
        f"<b>{usat_bal}</b>\n\n"
        "CELO:\n"
        f"<b>{celo_bal:.6f}</b>\n\n"
        "<i>Your wallet is ready to use.</i>"
    )

    if prompt_msg_id:
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=prompt_msg_id,
                text=success_text,
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML",
            )
            return
        except Exception:
            pass

    await message.answer(text=success_text, reply_markup=get_back_to_main_keyboard(), parse_mode="HTML")


# --- Single Wallet Actions (Rename / Remove) ---

@router.callback_query(F.data.startswith("wallet_view:"))
async def cb_wallet_view(callback: CallbackQuery) -> None:
    """View details of a single user wallet with Rename and Remove options."""
    user_id = callback.from_user.id if callback.from_user else 0
    try:
        w_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        return

    wallet = await db.get_user_wallet_by_id(w_id, user_id)
    if not wallet:
        await callback.answer("Wallet not found.", show_alert=True)
        return

    w_addr = wallet["address"]
    w_name = wallet["wallet_name"]
    w_type = wallet.get("wallet_type", "imported")
    w_type_str = "🔗 Non-Custodial (Connected)" if w_type == "connected" else "📥 Encrypted (Imported)"

    celo_bal = await celo_client.get_celo_balance(w_addr)
    _, usat_bal = await celo_client.get_usat_balance(w_addr)
    total_sent = await db.get_wallet_total_sent(w_addr)

    detail_text = (
        f"👛 <b>{w_name}</b>\n\n"
        f"<b>Type:</b> {w_type_str}\n"
        f"<b>Address:</b> <code>{w_addr}</code>\n\n"
        f"<b>USAT Balance:</b> {usat_bal}\n"
        f"<b>CELO Balance:</b> {celo_bal:.6f} CELO\n"
        f"<b>Total Paid:</b> ${total_sent:.2f} USAT"
    )

    if callback.message:
        await callback.message.edit_text(
            text=detail_text,
            reply_markup=get_wallet_detail_keyboard(w_id),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data.startswith("wallet_rename:"))
async def cb_wallet_rename_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user for new name for their wallet."""
    try:
        w_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        return

    await state.set_state(AddWalletStates.waiting_for_rename)
    await state.update_data(rename_wallet_id=w_id)

    prompt_text = "✏️ <b>RENAME WALLET</b>\n\n<i>Enter the new name for this wallet:</i>"
    if callback.message:
        await callback.message.edit_text(
            text=prompt_text,
            reply_markup=get_cancel_keyboard(f"wallet_view:{w_id}"),
            parse_mode="HTML",
        )
    await callback.answer()


@router.message(AddWalletStates.waiting_for_rename, F.text)
async def process_wallet_rename(message: Message, state: FSMContext, bot: Bot) -> None:
    """Rename user wallet."""
    user_id = message.from_user.id if message.from_user else 0
    new_name = message.text.strip() if message.text else ""

    try:
        await message.delete()
    except Exception:
        pass

    data = await state.get_data()
    w_id = data.get("rename_wallet_id")
    await state.clear()

    if w_id and new_name:
        await db.rename_user_wallet(w_id, user_id, new_name)

    await message.answer(
        text=f"✅ <i>Wallet renamed to:</i> <b>{new_name}</b>",
        reply_markup=get_back_to_main_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("wallet_remove:"))
async def cb_wallet_remove(callback: CallbackQuery) -> None:
    """Remove wallet belonging to user."""
    user_id = callback.from_user.id if callback.from_user else 0
    try:
        w_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        return

    await db.delete_user_wallet(w_id, user_id)
    await callback.answer("Wallet removed.", show_alert=True)
    
    # Return to updated wallets list
    wallets = await db.get_user_wallets(user_id)
    lines = ["👛 <b>MY WALLETS</b>\n\n<i>Your connected Celo wallets.</i>\n"]
    for w in wallets:
        lines.append(f"👛 <b>{w['wallet_name']}</b>\n<code>{w['address'][:8]}...</code>\n")
    
    if not wallets:
        lines.append("<i>No connected wallets remaining.</i>")

    if callback.message:
        await callback.message.edit_text(
            text="\n".join(lines),
            reply_markup=get_wallets_menu_keyboard(wallets),
            parse_mode="HTML",
        )
