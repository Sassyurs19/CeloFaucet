"""
Main user interface keyboards for Celo USAT Payment Bot.
Follows clean, modern UI standards with rich emoji styling.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from config import config


def get_main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Returns the primary user menu keyboard (No Admin button exposed to normal users)."""
    buttons = [
        [InlineKeyboardButton(text="💵 Pay $2 USAT", callback_data="pay_start")],
        [
            InlineKeyboardButton(text="👛 My Wallets", callback_data="wallets_list"),
            InlineKeyboardButton(text="📜 My Payments", callback_data="history_page:0"),
        ],
        [
            InlineKeyboardButton(text="👤 My Profile", callback_data="profile_view"),
            InlineKeyboardButton(text="ℹ️ Help", callback_data="help_view"),
        ],
    ]
    # Admin button is strictly hidden from normal menu per requirements:
    # "Do NOT display an Admin button. Do NOT display /admin in the menu."
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_profile_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for User Profile screen."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✏️ Edit Name", callback_data="profile_edit_name"),
                InlineKeyboardButton(text="📱 Edit Mobile", callback_data="profile_edit_mobile"),
            ],
            [InlineKeyboardButton(text="⬅️ Back", callback_data="main_menu")],
        ]
    )


def get_wallets_menu_keyboard(wallets: list[dict]) -> InlineKeyboardMarkup:
    """Keyboard for My Wallets list screen."""
    buttons = []
    for w in wallets:
        w_id = w["id"]
        w_name = w["wallet_name"]
        w_type_icon = "🔗" if w.get("wallet_type") == "connected" else "📥"
        buttons.append([
            InlineKeyboardButton(
                text=f"{w_type_icon} {w_name}", callback_data=f"wallet_view:{w_id}"
            )
        ])
    buttons.append([InlineKeyboardButton(text="➕ Add Wallet", callback_data="wallet_add_choose")])
    buttons.append([InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_add_wallet_choice_keyboard() -> InlineKeyboardMarkup:
    """Keyboard prompting user to choose between Connect Wallet and Import Wallet."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Connect Wallet", callback_data="wallet_add_connect")],
            [InlineKeyboardButton(text="📥 Import Wallet", callback_data="wallet_add_import")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="wallets_list")],
        ]
    )


def get_connect_wallet_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Button to open WebApp for non-custodial wallet connection."""
    buttons = []
    # If public WebApp URL is configured, use Telegram WebApp button
    webapp_url = config.webapp_public_url or f"http://localhost:{config.webapp_port}"
    full_url = f"{webapp_url}/connect?user_id={user_id}"

    if config.webapp_public_url and config.webapp_public_url.startswith("https://"):
        buttons.append([
            InlineKeyboardButton(text="🔗 Connect Wallet", web_app=WebAppInfo(url=full_url))
        ])
    else:
        # Fallback browser link or manual entry option
        buttons.append([
            InlineKeyboardButton(text="🔗 Open Wallet Connection Page", url=full_url)
        ])
    
    buttons.append([
        InlineKeyboardButton(text="⌨️ Enter Address Manually", callback_data="wallet_connect_manual")
    ])
    buttons.append([InlineKeyboardButton(text="⬅️ Back", callback_data="wallet_add_choose")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_wallet_detail_keyboard(wallet_id: int) -> InlineKeyboardMarkup:
    """Keyboard for individual wallet inspection."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✏️ Rename", callback_data=f"wallet_rename:{wallet_id}"),
                InlineKeyboardButton(text="🗑️ Remove", callback_data=f"wallet_remove:{wallet_id}"),
            ],
            [InlineKeyboardButton(text="⬅️ Back to Wallets", callback_data="wallets_list")],
        ]
    )


def get_pick_source_wallet_keyboard(wallets: list[dict]) -> InlineKeyboardMarkup:
    """Keyboard for selecting source wallet for $2.00 USAT payment."""
    buttons = []
    for w in wallets:
        w_id = w["id"]
        w_name = w["wallet_name"]
        w_type_icon = "🔗" if w.get("wallet_type") == "connected" else "📥"
        buttons.append([
            InlineKeyboardButton(
                text=f"{w_type_icon} {w_name}", callback_data=f"pay_pick_src:{w_id}"
            )
        ])
    buttons.append([InlineKeyboardButton(text="➕ Add Wallet", callback_data="wallet_add_choose")])
    buttons.append([InlineKeyboardButton(text="⬅️ Cancel", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_pick_receiving_wallet_keyboard(
    receivers: list[dict], recent_receiver_id: int | None = None
) -> InlineKeyboardMarkup:
    """Keyboard for selecting destination receiving wallet configured by admin."""
    buttons = []
    for r in receivers:
        r_id = r["id"]
        r_name = r["name"]
        recent_tag = " ⭐ (Recent)" if recent_receiver_id and r_id == recent_receiver_id else ""
        buttons.append([
            InlineKeyboardButton(
                text=f"💰 {r_name}{recent_tag}", callback_data=f"pay_pick_dest:{r_id}"
            )
        ])
    buttons.append([InlineKeyboardButton(text="⬅️ Back", callback_data="pay_start")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_payment_review_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for reviewing payment before confirmation."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Confirm & Pay $2 USAT", callback_data="pay_confirm")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="main_menu")],
        ]
    )


def get_connected_signing_keyboard(payment_id: str, from_addr: str, to_addr: str, rec_name: str) -> InlineKeyboardMarkup:
    """Keyboard for user to approve $2.00 USAT transfer in connected wallet."""
    webapp_url = config.webapp_public_url or f"http://localhost:{config.webapp_port}"
    full_url = (
        f"{webapp_url}/pay?payment_id={payment_id}&from={from_addr}&to={to_addr}&rec_name={rec_name}"
    )

    buttons = []
    if config.webapp_public_url and config.webapp_public_url.startswith("https://"):
        buttons.append([
            InlineKeyboardButton(text="💳 Approve & Pay in Wallet", web_app=WebAppInfo(url=full_url))
        ])
    else:
        buttons.append([
            InlineKeyboardButton(text="💳 Open In-Wallet Signing Page", url=full_url)
        ])
    
    buttons.append([
        InlineKeyboardButton(
            text="🔄 Check Transaction Status", callback_data=f"pay_check:{payment_id}"
        )
    ])
    buttons.append([
        InlineKeyboardButton(text="❌ Cancel", callback_data=f"pay_cancel:{payment_id}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_payment_success_keyboard(tx_hash: str) -> InlineKeyboardMarkup:
    """Keyboard displayed after successful USAT payment confirmation."""
    explorer_url = f"{config.explorer_tx_url}{tx_hash}"
    buttons = []
    if not tx_hash.startswith("0xsimulated_"):
        buttons.append([InlineKeyboardButton(text="🔎 View on Celoscan", url=explorer_url)])
    buttons.append([InlineKeyboardButton(text="💵 Pay Again", callback_data="pay_start")])
    buttons.append([InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_history_keyboard(current_page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Pagination keyboard for personal USAT payment history."""
    nav_buttons = []
    if current_page > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                text="⬅️ Previous", callback_data=f"history_page:{current_page - 1}"
            )
        )
    if current_page < total_pages - 1:
        nav_buttons.append(
            InlineKeyboardButton(
                text="Next ➡️", callback_data=f"history_page:{current_page + 1}"
            )
        )

    rows = []
    if nav_buttons:
        rows.append(nav_buttons)
    rows.append([InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_cancel_keyboard(back_callback: str = "main_menu") -> InlineKeyboardMarkup:
    """Generic cancel/back keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel", callback_data=back_callback)]
        ]
    )


def get_back_to_main_keyboard() -> InlineKeyboardMarkup:
    """Return to home screen button."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu")]
        ]
    )
