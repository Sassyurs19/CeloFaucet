"""
Payment flow handler for $2.00 USAT payments.
Enforces fixed $2.00 amount (exact 2,000,000 base units for 6 decimals),
manages source wallet selection, active admin receiving address selection,
automated 0.05 CELO gas subsidy from the dedicated funding wallet,
and dual-mode execution (imported key signing or non-custodial WebApp signing).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import config
from database import db
from wallet import wallet_manager
from celo import celo_client
from services.encryption import encryption_service
from webapp.server import PENDING_PAYMENT_FUTURES
from keyboards.main import (
    get_pick_source_wallet_keyboard,
    get_pick_receiving_wallet_keyboard,
    get_payment_review_keyboard,
    get_connected_signing_keyboard,
    get_payment_success_keyboard,
    get_back_to_main_keyboard,
)

logger = logging.getLogger(__name__)
router = Router(name="payment")


class PaymentStates(StatesGroup):
    """FSM states for payment configuration."""
    picking_source = State()
    picking_destination = State()
    reviewing = State()


# --- Step 1: Select Source Wallet ---

@router.callback_query(F.data == "pay_start")
async def cb_pay_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Initiate $2.00 USAT payment and prompt for source wallet."""
    await state.clear()
    user_id = callback.from_user.id if callback.from_user else 0

    # 1. Check if bot/payments is paused by admin
    if await db.is_bot_paused():
        paused_text = (
            "⏸ <b>PAYMENTS TEMPORARILY PAUSED</b>\n\n"
            "<i>The payment service is currently undergoing routine maintenance. "
            "Please try again shortly.</i>"
        )
        if callback.message:
            await callback.message.edit_text(
                text=paused_text,
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML",
            )
        await callback.answer()
        return

    # 2. Check for active processing payment
    active = await db.get_active_usat_payment(user_id)
    if active:
        wait_text = (
            "⏳ <b>PAYMENT IN PROGRESS</b>\n\n"
            "<i>You already have a payment being processed on the Celo network.</i>\n\n"
            f"<b>Payment ID:</b> <code>{active.get('payment_id')}</code>\n\n"
            "<i>Please wait a moment for on-chain confirmation before starting a new payment.</i>"
        )
        if callback.message:
            await callback.message.edit_text(
                text=wait_text,
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML",
            )
        await callback.answer()
        return

    # 3. Retrieve user's connected wallets
    wallets = await db.get_user_wallets(user_id)
    if not wallets:
        no_wallet_text = (
            "💵 <b>USAT PAYMENT</b>\n\n"
            "Amount:\n<b>$2.00 USAT</b>\n\n"
            "⚠️ <i>You don't have any wallets connected yet. "
            "Please connect or import a Celo wallet first.</i>"
        )
        if callback.message:
            await callback.message.edit_text(
                text=no_wallet_text,
                reply_markup=get_pick_source_wallet_keyboard([]),
                parse_mode="HTML",
            )
        await callback.answer()
        return

    await state.set_state(PaymentStates.picking_source)

    # Build wallet selector text with live balances
    lines = [
        "💵 <b>USAT PAYMENT</b>\n",
        "Amount:\n<b>$2.00 USAT</b>\n",
        "<i>Select the wallet you want to pay from:</i>\n",
    ]
    for w in wallets:
        w_name = w["wallet_name"]
        w_addr = w["address"]
        trunc = wallet_manager.truncate_address(w_addr, prefix_len=6, suffix_len=4)
        celo_bal = await celo_client.get_celo_balance(w_addr)
        _, usat_bal = await celo_client.get_usat_balance(w_addr)
        lines.append(
            f"👛 <b>{w_name}</b>\n<code>{trunc}</code>\nUSAT: {usat_bal}\nCELO: {celo_bal:.6f}\n"
        )

    text = "\n".join(lines)
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_pick_source_wallet_keyboard(wallets),
            parse_mode="HTML",
        )
    await callback.answer()


# --- Step 2: Select Receiving Address ---

@router.callback_query(PaymentStates.picking_source, F.data.startswith("pay_pick_src:"))
async def cb_pick_source_wallet(callback: CallbackQuery, state: FSMContext) -> None:
    """User selected source wallet, prompt for active admin receiving wallet."""
    user_id = callback.from_user.id if callback.from_user else 0
    try:
        wallet_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        return

    wallet = await db.get_user_wallet_by_id(wallet_id, user_id)
    if not wallet:
        await callback.answer("Wallet not found.", show_alert=True)
        return

    await state.update_data(source_wallet_id=wallet_id)
    await state.set_state(PaymentStates.picking_destination)

    # Retrieve active admin receiving wallets
    receivers = await db.get_receiving_wallets(only_active=True)
    if not receivers:
        err_text = (
            "⚠️ <b>NO RECEIVING WALLETS AVAILABLE</b>\n\n"
            "<i>There are currently no active receiving addresses configured by the administrator. "
            "Please try again later.</i>"
        )
        if callback.message:
            await callback.message.edit_text(
                text=err_text,
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML",
            )
        await callback.answer()
        return

    recent_receiver = await db.get_recently_used_receiving_wallet(user_id)
    recent_id = recent_receiver["id"] if recent_receiver else None

    dest_text = (
        "📥 <b>SELECT RECEIVING WALLET</b>\n\n"
        "<i>Choose where your $2.00 USAT payment should be sent:</i>"
    )
    if callback.message:
        await callback.message.edit_text(
            text=dest_text,
            reply_markup=get_pick_receiving_wallet_keyboard(receivers, recent_receiver_id=recent_id),
            parse_mode="HTML",
        )
    await callback.answer()


# --- Step 3: Review Payment Screen ---

@router.callback_query(PaymentStates.picking_destination, F.data.startswith("pay_pick_dest:"))
async def cb_pick_dest_wallet(callback: CallbackQuery, state: FSMContext) -> None:
    """User selected receiving wallet, show confirmation/review screen."""
    try:
        rec_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        return

    data = await state.get_data()
    source_wallet_id = data.get("source_wallet_id")
    user_id = callback.from_user.id if callback.from_user else 0

    source_wallet = await db.get_user_wallet_by_id(source_wallet_id, user_id)
    receiving_wallet = await db.get_receiving_wallet_by_id(rec_id)

    if not source_wallet or not receiving_wallet:
        await callback.answer("Selection invalid, please restart.", show_alert=True)
        return

    await state.update_data(
        dest_wallet_id=rec_id,
        source_address=source_wallet["address"],
        dest_address=receiving_wallet["address"],
        rec_name=receiving_wallet["name"],
        wallet_type=source_wallet.get("wallet_type", "imported"),
    )
    await state.set_state(PaymentStates.reviewing)

    from_addr = source_wallet["address"]
    to_addr = receiving_wallet["address"]
    rec_name = receiving_wallet["name"]

    # Query current CELO balance for review screen
    current_celo = await celo_client.get_celo_balance(from_addr)

    review_text = (
        "🔍 <b>REVIEW PAYMENT</b>\n\n"
        "Amount:\n<b>$2.00 USAT</b>\n\n"
        f"From:\n<code>{from_addr}</code> ({source_wallet['wallet_name']})\n\n"
        f"To:\n<code>{to_addr}</code>\n\n"
        f"Receiving Wallet:\n<b>{rec_name}</b>\n\n"
        f"Current CELO:\n<code>{current_celo:.6f}</code>\n\n"
        "<i>Please review the details before confirming.</i>"
    )

    if callback.message:
        await callback.message.edit_text(
            text=review_text,
            reply_markup=get_payment_review_keyboard(),
            parse_mode="HTML",
        )
    await callback.answer()


# --- Step 4: Confirm & Execute Payment ---

@router.callback_query(PaymentStates.reviewing, F.data == "pay_confirm")
async def cb_pay_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """
    Execute payment workflow:
    1. Pre-flight USAT balance check (>= 2,000,000 base units).
    2. Pre-flight CELO balance check. If CELO < 0.005, automatically fund 0.05 CELO from funding wallet.
    3. Branch:
       - Imported Wallet: Decrypt key in memory, sign transfer, broadcast, wait for receipt.
       - Connected Wallet: Launch in-wallet signing flow via Telegram WebApp.
    """
    user_id = callback.from_user.id if callback.from_user else 0
    chat_id = callback.message.chat.id if callback.message else user_id
    message_id = callback.message.message_id if callback.message else 0

    data = await state.get_data()
    source_wallet_id = data.get("source_wallet_id")
    source_addr = data.get("source_address")
    dest_addr = data.get("dest_address")
    rec_name = data.get("rec_name", "Receiving Wallet")
    wallet_type = data.get("wallet_type", "imported")

    await state.clear()
    await callback.answer()

    # 1. Verify source wallet
    source_wallet = await db.get_user_wallet_by_id(source_wallet_id, user_id)
    if not source_wallet:
        await callback.message.edit_text(
            text="❌ <i>Selected wallet could not be found.</i>",
            reply_markup=get_back_to_main_keyboard(),
            parse_mode="HTML",
        )
        return

    # 2. Check USAT Balance
    required_base_units = celo_client.get_payment_amount_base_units()  # 2,000,000 base units for 6 decimals
    usat_balance_units, usat_bal_str = await celo_client.get_usat_balance(source_addr)

    if usat_balance_units < required_base_units:
        insufficient_text = (
            "❌ <b>INSUFFICIENT USAT BALANCE</b>\n\n"
            f"<b>Wallet:</b> {source_wallet['wallet_name']}\n"
            f"<b>Address:</b> <code>{wallet_manager.truncate_address(source_addr, 6, 4)}</code>\n\n"
            f"<b>Required:</b> $2.00 USAT ({required_base_units:,} units)\n"
            f"<b>Your Balance:</b> {usat_bal_str} USAT ({usat_balance_units:,} units)\n\n"
            "<i>Please fund your wallet with at least $2.00 USAT before paying.</i>"
        )
        await callback.message.edit_text(
            text=insufficient_text,
            reply_markup=get_back_to_main_keyboard(),
            parse_mode="HTML",
        )
        return

    payment_id = str(uuid.uuid4())
    await db.create_usat_payment(
        payment_id=payment_id,
        telegram_id=user_id,
        wallet_type=wallet_type,
        from_address=source_addr,
        to_address=dest_addr,
        receiving_wallet_name=rec_name,
        amount_usat=f"{config.usat_payment_amount:.2f}",
        amount_base_units=required_base_units,
    )

    # 3. Check CELO gas balance & auto-fund 0.05 CELO if insufficient
    current_celo = await celo_client.get_celo_balance(source_addr)
    gas_threshold = config.min_user_celo_threshold  # 0.005 CELO

    celo_funded = 0
    celo_fund_tx = None

    if current_celo < gas_threshold:
        funding_msg = (
            "⛽ <b>FUNDING CELO GAS SUBSIDY</b>\n\n"
            f"<i>Your wallet balance ({current_celo:.6f} CELO) is low for network gas.</i>\n\n"
            "<i>Automatically sending <b>0.05 CELO</b> from dedicated funding wallet...</i>\n\n"
            "⏳ <i>Waiting for on-chain confirmation...</i>"
        )
        await callback.message.edit_text(text=funding_msg, parse_mode="HTML")

        fund_ok, fund_tx_hash, fund_err = await celo_client.send_celo_funding(
            to_address=source_addr, amount_celo=config.celo_funding_amount
        )
        if fund_ok:
            celo_funded = 1
            celo_fund_tx = fund_tx_hash
            await db.update_usat_payment_status(
                payment_id=payment_id,
                status="PROCESSING",
                celo_funded=1,
                celo_fund_tx_hash=fund_tx_hash,
            )
            logger.info("0.05 CELO subsidy funded to %s for payment %s", source_addr, payment_id)
        else:
            logger.warning("Could not auto-fund 0.05 CELO: %s", fund_err)

    # 4. Handle Execution according to wallet type
    if wallet_type == "imported":
        # --- IMPORTED WALLET (Bot signs transaction) ---
        processing_text = (
            "⏳ <b>PROCESSING USAT PAYMENT</b>\n\n"
            "<b>Amount:</b> $2.00 USAT\n"
            f"<b>From:</b> <code>{wallet_manager.truncate_address(source_addr, 6, 4)}</code>\n"
            f"<b>To:</b> <code>{wallet_manager.truncate_address(dest_addr, 6, 4)}</code>\n\n"
            "<i>Broadcasting transaction to Celo Mainnet...</i>"
        )
        await callback.message.edit_text(text=processing_text, parse_mode="HTML")

        # Decrypt private key
        encrypted_pk = source_wallet.get("encrypted_private_key", "")
        try:
            decrypted_pk = encryption_service.decrypt(encrypted_pk)
        except Exception as e:
            err_msg = "Decryption error: could not decrypt wallet key."
            logger.error("Payment %s abort: %s", payment_id, e)
            await db.update_usat_payment_status(
                payment_id=payment_id, status="FAILED", error_message=err_msg
            )
            await callback.message.edit_text(
                text=f"❌ <b>Error:</b> {err_msg}",
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML",
            )
            return

        # Execute ERC-20 transfer
        success, tx_hash, block_num, err_desc = await celo_client.transfer_usat_imported(
            private_key=decrypted_pk,
            to_address=dest_addr,
            base_units=required_base_units,
        )
        # Clear private key from memory
        decrypted_pk = None

        if success:
            await db.update_usat_payment_status(
                payment_id=payment_id,
                status="SUCCESS",
                tx_hash=tx_hash,
                block_number=block_num,
            )
            await db.update_wallet_last_used(source_wallet_id)

            success_text = (
                "✅ <b>PAYMENT SUCCESSFUL</b>\n\n"
                "Amount:\n<b>$2.00 USAT</b>\n\n"
                f"From:\n<code>{wallet_manager.truncate_address(source_addr, 8, 6)}</code>\n\n"
                f"To:\n<code>{wallet_manager.truncate_address(dest_addr, 8, 6)}</code>\n"
                f"<b>Receiving Wallet:</b> {rec_name}\n\n"
                "Status:\n🟢 <b>Confirmed</b>\n\n"
                f"Transaction:\n<code>{tx_hash}</code>\n\n"
                "<i>Thank you for your payment.</i>"
            )
            await callback.message.edit_text(
                text=success_text,
                reply_markup=get_payment_success_keyboard(tx_hash),
                parse_mode="HTML",
            )
        else:
            await db.update_usat_payment_status(
                payment_id=payment_id,
                status="FAILED",
                error_message=err_desc,
            )
            fail_text = (
                "❌ <b>TRANSACTION FAILED</b>\n\n"
                f"<i>{err_desc or 'Network error while broadcasting transaction.'}</i>\n\n"
                "Please check your wallet and try again."
            )
            await callback.message.edit_text(
                text=fail_text,
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML",
            )

    else:
        # --- CONNECTED WALLET (Non-custodial, user signs in wallet) ---
        sign_prompt_text = (
            "🔐 <b>WALLET SIGNING REQUIRED</b>\n\n"
            "Amount:\n<b>$2.00 USAT</b>\n\n"
            f"From:\n<code>{source_addr}</code>\n\n"
            f"To ({rec_name}):\n<code>{dest_addr}</code>\n\n"
            "<i>Click below to approve and sign the transaction in your connected wallet.</i>"
        )

        # Set up an async future to wait for callback submission
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        PENDING_PAYMENT_FUTURES[payment_id] = future

        await callback.message.edit_text(
            text=sign_prompt_text,
            reply_markup=get_connected_signing_keyboard(
                payment_id, source_addr, dest_addr, rec_name
            ),
            parse_mode="HTML",
        )

        # Background task to monitor for future completion or timeout
        asyncio.create_task(
            monitor_connected_payment(
                bot=bot,
                chat_id=chat_id,
                message_id=message_id,
                payment_id=payment_id,
                future=future,
                source_addr=source_addr,
                dest_addr=dest_addr,
                rec_name=rec_name,
                source_wallet_id=source_wallet_id,
            )
        )


async def monitor_connected_payment(
    bot: Bot,
    chat_id: int,
    message_id: int,
    payment_id: str,
    future: asyncio.Future,
    source_addr: str,
    dest_addr: str,
    rec_name: str,
    source_wallet_id: int,
) -> None:
    """Wait for in-wallet submission and poll confirmation on Celo Mainnet."""
    try:
        # Wait up to 180 seconds for user to sign in their wallet
        tx_hash = await asyncio.wait_for(future, timeout=180.0)
    except asyncio.TimeoutError:
        PENDING_PAYMENT_FUTURES.pop(payment_id, None)
        return
    except Exception as e:
        logger.error("Error waiting for payment signing future %s: %s", payment_id, e)
        return
    finally:
        PENDING_PAYMENT_FUTURES.pop(payment_id, None)

    # Inform user transaction was broadcasted, now verifying
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=(
                "⏳ <b>VERIFYING TRANSACTION</b>\n\n"
                f"<b>Transaction Hash:</b>\n<code>{tx_hash}</code>\n\n"
                "<i>Waiting for Celo Mainnet confirmation...</i>"
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass

    # Wait for receipt on-chain
    is_confirmed, block_num, err = await celo_client.wait_for_tx_receipt(tx_hash, timeout=90)

    if is_confirmed:
        await db.update_usat_payment_status(
            payment_id=payment_id,
            status="SUCCESS",
            tx_hash=tx_hash,
            block_number=block_num,
        )
        await db.update_wallet_last_used(source_wallet_id)

        success_text = (
            "✅ <b>PAYMENT SUCCESSFUL</b>\n\n"
            "Amount:\n<b>$2.00 USAT</b>\n\n"
            f"From:\n<code>{wallet_manager.truncate_address(source_addr, 8, 6)}</code>\n\n"
            f"To:\n<code>{wallet_manager.truncate_address(dest_addr, 8, 6)}</code>\n"
            f"<b>Receiving Wallet:</b> {rec_name}\n\n"
            "Status:\n🟢 <b>Confirmed</b>\n\n"
            f"Transaction:\n<code>{tx_hash}</code>\n\n"
            "<i>Thank you for your payment.</i>"
        )
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=success_text,
                reply_markup=get_payment_success_keyboard(tx_hash),
                parse_mode="HTML",
            )
        except Exception:
            await bot.send_message(
                chat_id=chat_id,
                text=success_text,
                reply_markup=get_payment_success_keyboard(tx_hash),
                parse_mode="HTML",
            )
    else:
        await db.update_usat_payment_status(
            payment_id=payment_id,
            status="FAILED",
            tx_hash=tx_hash,
            error_message=err,
        )
        fail_text = (
            "❌ <b>TRANSACTION FAILED OR REVERTED</b>\n\n"
            f"<i>{err}</i>\n\n"
            f"Transaction: <code>{tx_hash}</code>"
        )
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=fail_text,
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML",
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("pay_check:"))
async def cb_pay_check(callback: CallbackQuery) -> None:
    """Allow user to manually check payment status."""
    payment_id = callback.data.split(":")[1]
    # Check if future has result or payment status in DB
    fut = PENDING_PAYMENT_FUTURES.get(payment_id)
    if fut and fut.done():
        await callback.answer("Transaction detected, verifying on-chain...", show_alert=True)
    else:
        await callback.answer("Awaiting signature in connected wallet...", show_alert=True)


@router.callback_query(F.data.startswith("pay_cancel:"))
async def cb_pay_cancel(callback: CallbackQuery) -> None:
    """Cancel pending connected wallet payment."""
    payment_id = callback.data.split(":")[1]
    fut = PENDING_PAYMENT_FUTURES.pop(payment_id, None)
    if fut and not fut.done():
        fut.cancel()

    await db.update_usat_payment_status(
        payment_id=payment_id, status="FAILED", error_message="Cancelled by user"
    )
    await callback.answer("Payment cancelled.", show_alert=True)
    if callback.message:
        await callback.message.edit_text(
            text="❌ <i>Payment was cancelled.</i>",
            reply_markup=get_back_to_main_keyboard(),
            parse_mode="HTML",
        )
