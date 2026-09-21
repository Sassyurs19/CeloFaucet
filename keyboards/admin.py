"""
Admin dashboard keyboards for Celo USAT Payment Bot.
Strictly authorized by numeric Telegram ID to prevent unauthorized access.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def get_admin_menu_keyboard(is_paused: bool) -> InlineKeyboardMarkup:
    """Returns the main administrator control dashboard keyboard."""
    pause_btn_text = "▶️ Resume Payments" if is_paused else "⏸ Pause Payments"

    buttons = [
        [
            InlineKeyboardButton(text="💰 Receiving Wallets", callback_data="admin_rec_list"),
            InlineKeyboardButton(text="👥 Users", callback_data="admin_users:0"),
        ],
        [
            InlineKeyboardButton(text="📊 User Totals", callback_data="admin_user_totals:0"),
            InlineKeyboardButton(text="📜 Payment History", callback_data="admin_txs:0"),
        ],
        [
            InlineKeyboardButton(text="💵 USAT Statistics", callback_data="admin_stats"),
            InlineKeyboardButton(text="⛽ CELO Funding", callback_data="admin_funding"),
        ],
        [
            InlineKeyboardButton(text="⚙️ Settings", callback_data="admin_settings"),
            InlineKeyboardButton(text=pause_btn_text, callback_data="admin_toggle_pause"),
        ],
        [
            InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_back_keyboard() -> InlineKeyboardMarkup:
    """Back button to return to the Admin dashboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Back to Admin", callback_data="admin_menu")],
            [InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu")],
        ]
    )


def get_admin_receiving_wallets_keyboard(wallets: list[dict]) -> InlineKeyboardMarkup:
    """List of receiving wallets with manage options."""
    buttons = []
    for r in wallets:
        r_id = r["id"]
        status_icon = "🟢" if r.get("is_active") else "🔴"
        buttons.append([
            InlineKeyboardButton(
                text=f"{status_icon} {r['name']}", callback_data=f"admin_rec_view:{r_id}"
            )
        ])
    buttons.append([InlineKeyboardButton(text="➕ Add Receiving Address", callback_data="admin_rec_add")])
    buttons.append([InlineKeyboardButton(text="⬅️ Back to Admin", callback_data="admin_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_receiving_wallet_detail_keyboard(wallet_id: int, is_active: bool) -> InlineKeyboardMarkup:
    """Action buttons for a single receiving wallet."""
    toggle_text = "🔴 Deactivate" if is_active else "🟢 Activate"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✏️ Rename", callback_data=f"admin_rec_rename:{wallet_id}"),
                InlineKeyboardButton(text=toggle_text, callback_data=f"admin_rec_toggle:{wallet_id}"),
            ],
            [InlineKeyboardButton(text="🗑️ Remove", callback_data=f"admin_rec_delete:{wallet_id}")],
            [InlineKeyboardButton(text="⬅️ Back to Receiving Wallets", callback_data="admin_rec_list")],
        ]
    )


def get_admin_users_keyboard(
    users: list[dict], current_page: int, total_pages: int
) -> InlineKeyboardMarkup:
    """Paginated list of registered users for admin inspection."""
    buttons = []
    for u in users:
        tg_id = u["telegram_id"]
        name = u.get("full_name") or u.get("first_name") or f"User {tg_id}"
        wallets_cnt = u.get("wallet_count", 0)
        buttons.append([
            InlineKeyboardButton(
                text=f"👤 {name} ({wallets_cnt} wallets)",
                callback_data=f"admin_user_detail:{tg_id}",
            )
        ])

    nav_buttons = []
    if current_page > 0:
        nav_buttons.append(
            InlineKeyboardButton(text="⬅️ Prev", callback_data=f"admin_users:{current_page - 1}")
        )
    if current_page < total_pages - 1:
        nav_buttons.append(
            InlineKeyboardButton(text="Next ➡️", callback_data=f"admin_users:{current_page + 1}")
        )

    if nav_buttons:
        buttons.append(nav_buttons)
    buttons.append([InlineKeyboardButton(text="⬅️ Back to Admin", callback_data="admin_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_user_detail_keyboard(telegram_id: int, wallets: list[dict]) -> InlineKeyboardMarkup:
    """Detail view of a user with buttons to inspect their wallets."""
    buttons = []
    for w in wallets:
        w_id = w["id"]
        w_type = "🔗" if w.get("wallet_type") == "connected" else "📥"
        buttons.append([
            InlineKeyboardButton(
                text=f"{w_type} {w['wallet_name']}", callback_data=f"admin_user_wallet_view:{w_id}"
            )
        ])
    buttons.append([InlineKeyboardButton(text="⬅️ Back to Users", callback_data="admin_users:0")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_user_wallet_detail_keyboard(
    wallet_id: int, user_tg_id: int, is_imported: bool
) -> InlineKeyboardMarkup:
    """
    Action buttons for inspecting an individual user wallet. Credentials are
    never displayed or exported, including to administrators.
    """
    buttons = []
    buttons.append([
        InlineKeyboardButton(text="✏️ Rename", callback_data=f"admin_wallet_rename:{wallet_id}"),
        InlineKeyboardButton(text="🗑️ Remove", callback_data=f"admin_wallet_remove:{wallet_id}"),
    ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Back to User", callback_data=f"admin_user_detail:{user_tg_id}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_reveal_confirm_keyboard(wallet_id: int) -> InlineKeyboardMarkup:
    """Sensitive confirmation keyboard before displaying private key to admin."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔐 Reveal Private Key", callback_data=f"admin_wallet_reveal_do:{wallet_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Cancel", callback_data=f"admin_user_wallet_view:{wallet_id}"
                )
            ],
        ]
    )


def get_admin_txs_keyboard(current_page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Pagination keyboard for viewing all USAT payments."""
    nav_buttons = []
    if current_page > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                text="⬅️ Previous", callback_data=f"admin_txs:{current_page - 1}"
            )
        )
    if current_page < total_pages - 1:
        nav_buttons.append(
            InlineKeyboardButton(
                text="Next ➡️", callback_data=f"admin_txs:{current_page + 1}"
            )
        )

    rows = []
    if nav_buttons:
        rows.append(nav_buttons)
    rows.append([InlineKeyboardButton(text="⬅️ Back to Admin", callback_data="admin_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_admin_user_totals_keyboard(current_page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Pagination keyboard for User Totals report."""
    nav_buttons = []
    if current_page > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                text="⬅️ Previous", callback_data=f"admin_user_totals:{current_page - 1}"
            )
        )
    if current_page < total_pages - 1:
        nav_buttons.append(
            InlineKeyboardButton(
                text="Next ➡️", callback_data=f"admin_user_totals:{current_page + 1}"
            )
        )

    rows = []
    if nav_buttons:
        rows.append(nav_buttons)
    rows.append([InlineKeyboardButton(text="⬅️ Back to Admin", callback_data="admin_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
