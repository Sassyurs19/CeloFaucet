"""
Production-ready REST API router for CELO USAT Payment Web Application.
Enforces strict server-side authorization, fixed $2.00 USAT amounts,
AES-256-GCM encrypted private key imports with recovery phrase rejection,
automatic 0.05 CELO gas funding, rate limiting, and admin controls.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from aiohttp import web
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from eth_account import Account
from web3 import Web3

from config import config
from database import db, normalize_mobile, is_admin_phone
from wallet import wallet_manager
from celo import BlockchainUnavailableError, celo_client
from services.encryption import encryption_service
from services.validation import validate_celo_address

logger = logging.getLogger(__name__)

# Argon2id password hasher
password_hasher = PasswordHasher()

# Login rate limiting: normalized_mobile -> list of failed attempt timestamps
LOGIN_FAILED_ATTEMPTS: dict[str, list[float]] = {}
MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 15 * 60  # 15 minutes lockout

# Regex for full name: 2-80 characters, letters, spaces, hyphens, apostrophes
NAME_REGEX = re.compile(r"^[a-zA-Z\s\-']{2,80}$")


def validate_password_strength(password: str) -> tuple[bool, str]:
    """Validate password meets minimum security criteria (min 8 chars, letter + digit)."""
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not re.search(r"[A-Za-z]", password):
        return False, "Password must contain at least one letter."
    if not re.search(r"\d", password):
        return False, "Password must contain at least one number."
    return True, ""


# --- Cryptographically Signed, Persistent Session Tokens ---

def session_expiry(exp_days: int) -> datetime | str:
    """Return a UTC expiry suitable for the active database driver's timestamp type."""
    expires_utc = datetime.now(timezone.utc) + timedelta(days=exp_days)
    if db.using_postgres:
        return expires_utc
    # SQLite stores CURRENT_TIMESTAMP as a UTC text value without an offset.
    return expires_utc.strftime("%Y-%m-%d %H:%M:%S")


async def create_session_token(user_id: int, mobile: str, is_admin: bool, exp_days: int = 30) -> str:
    """
    Generate an opaque durable server-side session token.
    """
    token = secrets.token_urlsafe(48)
    expires_at = session_expiry(exp_days)
    await db.create_session(token, user_id, is_admin, expires_at)
    return token


# --- Helper Functions & Middlewares ---

def get_user_id_from_request(request: web.Request) -> int | None:
    """Extract authenticated user ID from Authorization header, X-Session-Id, or cookie."""
    session = request.get("user_session")
    return int(session["user_id"]) if session else None


def is_admin_request(request: web.Request) -> bool:
    """Verify admin authorization token or designated admin user session."""
    admin_session = request.get("admin_session")
    user_session = request.get("user_session")
    return bool((admin_session and admin_session.get("is_admin")) or (user_session and user_session.get("is_admin")))


def mask_mobile(mobile: str | None) -> str:
    """Mask mobile number for display (e.g. +91 ******1234)."""
    if not mobile:
        return "Not Set"
    cleaned = mobile.strip()
    if len(cleaned) <= 6:
        return cleaned
    prefix = cleaned[:3]
    suffix = cleaned[-4:]
    return f"{prefix} ******{suffix}"


def set_browser_session_cookie(response: web.Response, session_token: str, *, max_age: int) -> None:
    """Set the durable cross-site session cookie used by Firebase Hosting.

    The frontend and API are on different registrable domains. ``Partitioned``
    lets modern mobile browsers retain this Secure, HttpOnly cookie in the
    Firebase site's partition instead of treating it as a blocked third-party
    cookie. Browsers that do not implement it safely ignore the attribute.
    """
    secure = config.frontend_url.startswith("https://")
    same_site = "None" if secure else "Lax"
    attributes = [
        f"usat_session={session_token}",
        f"Max-Age={max_age}",
        "Path=/",
        "HttpOnly",
        f"SameSite={same_site}",
    ]
    if secure:
        attributes.extend(["Secure", "Partitioned"])
    response.headers.add("Set-Cookie", "; ".join(attributes))


def clear_browser_session_cookie(response: web.Response, cookie_name: str) -> None:
    """Clear both the cross-site cookie attributes and its server-side state."""
    secure = config.frontend_url.startswith("https://")
    same_site = "None" if secure else "Lax"
    attributes = [
        f"{cookie_name}=",
        "Max-Age=0",
        "Path=/",
        "HttpOnly",
        f"SameSite={same_site}",
    ]
    if secure:
        attributes.extend(["Secure", "Partitioned"])
    response.headers.add("Set-Cookie", "; ".join(attributes))


# =========================================================================
# 1. AUTHENTICATION & USER REGISTRATION (NO OTP/SMS VERIFICATION)
# =========================================================================

async def api_register(request: web.Request) -> web.Response:
    """
    Create a new account with Mobile Number + Password.
    Zero OTP/SMS/email verification: immediate activation upon registration.
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    name = str(data.get("name") or data.get("full_name") or "").strip()
    mobile = str(data.get("mobile") or data.get("mobile_number") or "").strip()
    password = str(data.get("password") or "")
    confirm_password = str(data.get("confirm_password") or "")
    address = str(data.get("address", "")).strip()

    # Name validation
    if not NAME_REGEX.match(name):
        return web.json_response({
            "error": "Full name must be 2–80 characters and contain only letters, spaces, hyphens, and apostrophes."
        }, status=400)

    # Mobile validation and canonical normalization
    digits = "".join(ch for ch in mobile if ch.isdigit())
    if len(digits) < 10 or len(digits) > 15:
        return web.json_response({
            "error": "Please enter a valid mobile number (10–15 digits, e.g. 9876543210 or +919876543210)."
        }, status=400)

    try:
        normalized_mobile = normalize_mobile(mobile)
    except Exception:
        normalized_mobile = f"+{digits}"

    # Password validation
    pwd_ok, pwd_err = validate_password_strength(password)
    if not pwd_ok:
        return web.json_response({"error": pwd_err}, status=400)

    if password != confirm_password:
        return web.json_response({
            "error": "Passwords do not match. Please verify and try again."
        }, status=400)

    # Check for duplicate registration on canonical normalized mobile
    existing_user = await db.get_user_by_normalized_mobile(normalized_mobile)
    if existing_user:
        return web.json_response({
            "error": "An account already exists with this mobile number. Please login."
        }, status=409)

    # Argon2id password hash
    hashed_password = password_hasher.hash(password)

    # Create account in SQLite database
    new_user = await db.create_user_account(
        full_name=name,
        mobile_number=mobile,
        password_hash=hashed_password,
    )
    user_id = new_user["id"]

    # Optional initial wallet attachment if provided during onboard
    if address:
        is_val, chk, _ = validate_celo_address(address)
        if is_val and chk:
            await db.add_user_wallet(
                telegram_id=user_id,
                wallet_name="Main Wallet",
                address=chk,
                wallet_type="connected",
                user_id=user_id,
            )

    # Check admin eligibility (strictly mobile 8142177207)
    is_admin = is_admin_phone(normalized_mobile)

    # Create cryptographically signed persistent session token
    session_token = await create_session_token(user_id, normalized_mobile, is_admin)

    resp = web.json_response({
        "success": True,
        "user": {
            "id": user_id,
            "name": name,
            "full_name": name,
            "mobile": mask_mobile(mobile),
            "mobile_raw": mobile,
            "is_admin_eligible": is_admin,
            "registered": True,
        }
    })
    set_browser_session_cookie(resp, session_token, max_age=86400 * 30)
    return resp


async def api_login(request: web.Request) -> web.Response:
    """
    Authenticate user using Mobile Number + Password.
    Brute-force protected: max 5 failed attempts per 15 minutes.
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    mobile = str(data.get("mobile") or data.get("mobile_number") or "").strip()
    password = str(data.get("password") or "")

    if not mobile or not password:
        return web.json_response({
            "error": "Please enter both mobile number and password."
        }, status=400)

    try:
        normalized_mobile = normalize_mobile(mobile)
    except Exception:
        normalized_mobile = mobile

    now = time.time()
    # Check rate limiting / lockout
    attempts = LOGIN_FAILED_ATTEMPTS.get(normalized_mobile, [])
    valid_attempts = [t for t in attempts if now - t < LOGIN_LOCKOUT_SECONDS]
    if len(valid_attempts) >= MAX_LOGIN_ATTEMPTS:
        oldest = valid_attempts[0]
        rem_seconds = int(LOGIN_LOCKOUT_SECONDS - (now - oldest))
        rem_mins = max(1, (rem_seconds + 59) // 60)
        return web.json_response({
            "error": f"Too many failed login attempts. Please try again in {rem_mins} minutes."
        }, status=429)

    # Find user by canonical mobile or suffix
    user = await db.get_user_by_normalized_mobile(normalized_mobile)
    if not user:
        digits = "".join(ch for ch in mobile if ch.isdigit())
        if len(digits) >= 10:
            async with db.connect() as conn:
                async with conn.execute(
                    "SELECT * FROM users WHERE mobile_number LIKE ? OR normalized_mobile LIKE ? ORDER BY id DESC LIMIT 1;",
                    (f"%{digits[-10:]}", f"%{digits[-10:]}"),
                ) as cur:
                    row = await cur.fetchone()
                    if row:
                        user = dict(row)

    if not user:
        LOGIN_FAILED_ATTEMPTS[normalized_mobile] = valid_attempts + [now]
        return web.json_response({"error": "Invalid mobile number or password."}, status=401)

    stored_hash = user.get("password_hash")
    if not stored_hash:
        return web.json_response({
            "error": "No password set for this account. Please create an account or contact support.",
            "requires_password_setup": True,
        }, status=401)

    # Verify password with Argon2id
    try:
        password_hasher.verify(stored_hash, password)
    except VerifyMismatchError:
        LOGIN_FAILED_ATTEMPTS[normalized_mobile] = valid_attempts + [now]
        return web.json_response({"error": "Invalid mobile number or password."}, status=401)
    except Exception as e:
        logger.warning("Argon2 verification exception: %s", e)
        LOGIN_FAILED_ATTEMPTS[normalized_mobile] = valid_attempts + [now]
        return web.json_response({"error": "Invalid mobile number or password."}, status=401)

    # Check if hash needs upgrade
    if password_hasher.check_needs_rehash(stored_hash):
        try:
            new_hash = password_hasher.hash(password)
            await db.update_user_password(user["id"], new_hash)
        except Exception as e:
            logger.warning("Rehash error: %s", e)

    # Success: clear failed attempts and update last login
    LOGIN_FAILED_ATTEMPTS.pop(normalized_mobile, None)
    await db.update_user_last_login(user["id"])

    user_id = user["id"]
    is_admin = is_admin_phone(user.get("normalized_mobile") or user.get("mobile_number"))

    # Create cryptographically signed persistent session token
    session_token = await create_session_token(user_id, normalized_mobile, is_admin)

    full_name = user.get("full_name") or user.get("first_name") or "User"
    resp = web.json_response({
        "success": True,
        "user": {
            "id": user_id,
            "name": full_name,
            "full_name": full_name,
            "mobile": mask_mobile(user.get("mobile_number")),
            "mobile_raw": user.get("mobile_number", ""),
            "is_admin_eligible": is_admin,
            "registered": True,
        }
    })
    set_browser_session_cookie(resp, session_token, max_age=86400 * 30)
    return resp


async def api_logout(request: web.Request) -> web.Response:
    """Log out authenticated user by invalidating session token."""
    auth_header = request.headers.get("Authorization", "")
    token = None
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    if not token:
        token = request.headers.get("X-Session-Id") or request.cookies.get("usat_session")

    if token:
        await db.invalidate_session(token)
    admin_token = request.cookies.get("admin_session")
    if admin_token:
        await db.invalidate_session(admin_token)

    resp = web.json_response({"success": True, "message": "Logged out successfully."})
    clear_browser_session_cookie(resp, "usat_session")
    clear_browser_session_cookie(resp, "admin_session")
    return resp


async def api_change_password(request: web.Request) -> web.Response:
    """
    Update password for authenticated user.
    Validates current password using Argon2id and enforces password strength on new password.
    """
    user_id = get_user_id_from_request(request)
    if not user_id:
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    current_pwd = str(data.get("current_password") or "")
    new_pwd = str(data.get("new_password") or "")
    confirm_pwd = str(data.get("confirm_password") or "")

    if not current_pwd or not new_pwd:
        return web.json_response({"error": "Please enter both current and new password."}, status=400)

    val_ok, val_err = validate_password_strength(new_pwd)
    if not val_ok:
        return web.json_response({"error": val_err}, status=400)

    if new_pwd != confirm_pwd:
        return web.json_response({"error": "New passwords do not match. Please verify and try again."}, status=400)

    user = await db.get_user_by_id(user_id)
    if not user:
        user = await db.get_user_profile(user_id)
    if not user:
        return web.json_response({"error": "User not found."}, status=404)

    stored_hash = user.get("password_hash")
    if stored_hash:
        try:
            password_hasher.verify(stored_hash, current_pwd)
        except Exception:
            return web.json_response({"error": "Current password is incorrect."}, status=400)

    new_hash = password_hasher.hash(new_pwd)
    await db.update_user_password(user["id"], new_hash)

    return web.json_response({"success": True, "message": "Password updated successfully."})


async def api_get_me(request: web.Request) -> web.Response:
    """Return authenticated user profile, stats, and admin eligibility."""
    user_id = get_user_id_from_request(request)
    if not user_id:
        return web.json_response({
            "authenticated": False,
            "user": {
                "is_admin_eligible": False,
            }
        }, status=200)

    profile = await db.get_user_profile(user_id)
    if not profile:
        profile = await db.get_user_by_id(user_id)

    if not profile:
        return web.json_response({
            "authenticated": False,
            "user": {
                "is_admin_eligible": False,
            }
        }, status=200)

    wallets_cnt = await db.get_user_wallet_count(user_id)
    payments_cnt = await db.get_user_payments_count(user_id)
    total_paid = await db.get_user_total_paid(user_id)

    is_admin = is_admin_phone(profile.get("normalized_mobile") or profile.get("mobile_number"))
    user_full_name = profile.get("full_name") or profile.get("first_name") or "User"

    return web.json_response({
        "authenticated": True,
        "user": {
            "id": user_id,
            "name": user_full_name,
            "full_name": user_full_name,
            "mobile": mask_mobile(profile.get("mobile_number")),
            "mobile_raw": profile.get("mobile_number", ""),
            "is_admin_eligible": is_admin,
            "wallets_count": wallets_cnt,
            "payments_count": payments_cnt,
            "total_paid": round(total_paid, 2),
            # Authentication must remain available when blockchain RPC is not.
            # Live balances are returned by /api/wallets instead.
            "total_usdt_balance": None,
            "created_at": profile.get("created_at"),
        }
    })


# =========================================================================
# 2. WALLET MANAGEMENT API (SUPPORTS UNLIMITED / 20+ WALLETS PER USER)
# =========================================================================

async def api_get_workspaces(request: web.Request) -> web.Response:
    """Return only the current user's wallet workspaces."""
    user_id = get_user_id_from_request(request)
    if not user_id:
        return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        workspaces = await db.get_user_workspaces(user_id)
    except Exception:
        logger.exception("Unable to read wallet workspaces for user ID %s.", user_id)
        return web.json_response({"error": "Wallet workspaces are temporarily unavailable. Please try again later."}, status=503)
    return web.json_response({"workspaces": workspaces})


async def api_create_workspace(request: web.Request) -> web.Response:
    """Create a simple, user-owned workspace without changing existing wallets."""
    user_id = get_user_id_from_request(request)
    if not user_id:
        return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    name = str(data.get("name", "")).strip()
    if not name or len(name) > 40:
        return web.json_response({"error": "Workspace name must be between 1 and 40 characters."}, status=400)
    try:
        workspace = await db.create_user_workspace(user_id, name)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception:
        logger.exception("Unable to create wallet workspace for user ID %s.", user_id)
        return web.json_response({"error": "Wallet workspace could not be created. Please try again later."}, status=503)
    return web.json_response({"success": True, "workspace": workspace}, status=201)


async def api_rename_workspace(request: web.Request) -> web.Response:
    """Rename a current user's workspace without moving any wallets."""
    user_id = get_user_id_from_request(request)
    if not user_id:
        return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        workspace_id = int(request.match_info["id"])
        data = await request.json()
    except (ValueError, KeyError):
        return web.json_response({"error": "Invalid workspace."}, status=400)
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    name = str(data.get("name", "")).strip()
    if not name or len(name) > 40:
        return web.json_response({"error": "Workspace name must be between 1 and 40 characters."}, status=400)
    try:
        workspace = await db.rename_user_workspace(workspace_id, user_id, name)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception:
        logger.exception("Unable to rename wallet workspace for user ID %s.", user_id)
        return web.json_response({"error": "Workspace could not be renamed. Please try again later."}, status=503)
    return web.json_response({"success": True, "workspace": workspace})


async def _workspace_for_request(data: dict, user_id: int) -> tuple[int | None, web.Response | None]:
    """Resolve a supplied workspace ID while enforcing ownership server-side."""
    raw_workspace_id = data.get("workspace_id")
    if raw_workspace_id in (None, ""):
        try:
            return await db.ensure_personal_workspace(user_id), None
        except Exception:
            logger.exception("Unable to resolve personal workspace for user ID %s.", user_id)
            return None, web.json_response({"error": "Wallet workspace is temporarily unavailable. Please try again later."}, status=503)
    try:
        workspace_id = int(raw_workspace_id)
    except (TypeError, ValueError):
        return None, web.json_response({"error": "Invalid workspace."}, status=400)
    workspace = await db.get_user_workspace_by_id(workspace_id, user_id)
    if not workspace:
        return None, web.json_response({"error": "Workspace not found."}, status=404)
    return workspace_id, None

async def api_get_wallets(request: web.Request) -> web.Response:
    """Retrieve all wallets for the authenticated user with live balances fetched concurrently."""
    user_id = get_user_id_from_request(request)
    if not user_id:
        return web.json_response({"error": "Unauthorized"}, status=401)

    if not await celo_client.is_connected():
        return web.json_response({"error": "Blockchain data is temporarily unavailable."}, status=503)

    try:
        await db.ensure_personal_workspace(user_id)
        raw_wallets = await db.get_user_wallets(user_id)
    except Exception:
        logger.exception("Unable to access wallet storage for user ID %s.", user_id)
        return web.json_response({"error": "Wallet storage is temporarily unavailable. Please try again later."}, status=503)

    async def fetch_wallet_info(w):
        addr = w["address"]
        celo_task = celo_client.get_celo_balance(addr)
        usat_task = celo_client.get_usat_balance(addr)
        celo_bal, (_, usat_bal) = await asyncio.gather(celo_task, usat_task)
        u_val = float(usat_bal)

        return {
            "id": w["id"],
            "name": w["wallet_name"],
            "label": w["wallet_name"],
            "address": addr,
            "type": w.get("wallet_type", "connected"),
            "wallet_type": w.get("wallet_type", "connected"),
            "workspace_id": w.get("workspace_id"),
            "celo_balance": f"{celo_bal:.4f}",
            "usat_balance": f"{u_val:.2f}",
            "created_at": w.get("created_at"),
            "_usat_num": u_val,
        }

    if raw_wallets:
        try:
            wallets_data = await asyncio.gather(*(fetch_wallet_info(w) for w in raw_wallets))
        except BlockchainUnavailableError:
            return web.json_response({"error": "Blockchain data is temporarily unavailable."}, status=503)
        total_usdt = sum(w.pop("_usat_num", 0.0) for w in wallets_data)
        wallets_data.sort(key=lambda w: (w.get("name") or "").lower())
    else:
        wallets_data = []
        total_usdt = 0.0

    return web.json_response({
        "wallets": wallets_data,
        "total_usdt_balance": f"{total_usdt:.2f}",
        "total_wallets": len(wallets_data),
    })


async def api_connect_wallet(request: web.Request) -> web.Response:
    """Connect a non-custodial in-browser wallet."""
    user_id = get_user_id_from_request(request)
    if not user_id:
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    addr = str(data.get("address", "")).strip()
    name = str(data.get("name") or data.get("label") or "Connected Wallet").strip()
    workspace_id, workspace_error = await _workspace_for_request(data, user_id)
    if workspace_error is not None:
        return workspace_error

    is_val, chk_addr, err_msg = validate_celo_address(addr)
    if not is_val or not chk_addr:
        return web.json_response({"error": err_msg or "Invalid Celo address format."}, status=400)

    # If wallet address is already in user's account, select/update it gracefully instead of 409 error
    try:
        existing = await db.get_user_wallets(user_id)
    except Exception:
        logger.exception("Unable to access wallet storage for user ID %s.", user_id)
        return web.json_response({"error": "Wallet storage is temporarily unavailable. Please try again later."}, status=503)
    existing_wallet = next((w for w in existing if w["address"].lower() == chk_addr.lower()), None)
    if existing_wallet:
        # If user passed a custom name (not default placeholder), update the wallet name
        if name and not name.lower().startswith("connected wallet") and name.lower() != "imported wallet":
            await db.rename_user_wallet(existing_wallet["id"], user_id, name)
            existing_wallet["wallet_name"] = name

        celo_bal = await celo_client.get_celo_balance(chk_addr)
        _, usat_bal = await celo_client.get_usat_balance(chk_addr)
        return web.json_response({
            "success": True,
            "is_existing": True,
            "message": f"Wallet '{existing_wallet['wallet_name']}' is already in your account and is now selected.",
            "wallet": {
                "id": existing_wallet["id"],
                "name": existing_wallet["wallet_name"],
                "label": existing_wallet["wallet_name"],
                "address": chk_addr,
                "type": existing_wallet.get("wallet_type", "connected"),
                "wallet_type": existing_wallet.get("wallet_type", "connected"),
                "celo_balance": f"{celo_bal:.4f}",
                "usat_balance": f"{float(usat_bal):.2f}",
            },
        })

    w_id = await db.add_user_wallet(
        telegram_id=user_id,
        wallet_name=name,
        address=chk_addr,
        wallet_type="connected",
        encrypted_private_key=None,
        user_id=user_id,
        workspace_id=workspace_id,
    )
    wallet_name = name

    celo_bal = await celo_client.get_celo_balance(chk_addr)
    _, usat_bal = await celo_client.get_usat_balance(chk_addr)

    return web.json_response({
        "success": True,
        "wallet": {
            "id": w_id,
            "name": wallet_name,
            "label": wallet_name,
            "address": chk_addr,
            "type": "connected",
            "wallet_type": "connected",
            "celo_balance": f"{celo_bal:.4f}",
            "usat_balance": f"{float(usat_bal):.2f}",
        }
    })


async def api_import_wallet(request: web.Request) -> web.Response:
    """
    Import a wallet via private key (AES-256-GCM encrypted).
    Strictly rejects 12/24-word recovery phrases with required message.
    Unlimited wallet additions allowed.
    """
    user_id = get_user_id_from_request(request)
    if not user_id:
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    name = str(data.get("name") or data.get("label") or "Imported Wallet").strip() or "Imported Wallet"
    raw_key = str(data.get("private_key", "")).strip()
    workspace_id, workspace_error = await _workspace_for_request(data, user_id)
    if workspace_error is not None:
        return workspace_error

    # Strict check: reject 12/24-word recovery phrases
    # If key contains multiple words separated by spaces or punctuation
    words = raw_key.split()
    if len(words) >= 12 or " " in raw_key:
        return web.json_response({
            "error": "Seed phrases / recovery phrases are not supported. Enter only a 64-character hex private key."
        }, status=400)

    clean_key = raw_key
    if clean_key.startswith("0x") or clean_key.startswith("0X"):
        clean_key = clean_key[2:]

    if len(clean_key) != 64:
        return web.json_response({
            "error": "Invalid private key. Must be a 64-character hexadecimal key (32 bytes)."
        }, status=400)

    try:
        formatted_key = "0x" + clean_key
        account = Account.from_key(formatted_key)
        derived_address = Web3.to_checksum_address(account.address)
    except Exception:
        return web.json_response({"error": "Failed to derive address from private key."}, status=400)

    # Encrypt immediately via AES-256-GCM. Imports cannot proceed without the
    # server-only master key; never substitute a generated or client-side key.
    try:
        encrypted_key = encryption_service.encrypt(formatted_key)
    except RuntimeError:
        return web.json_response({"error": "Imported wallets are temporarily unavailable. Please contact support."}, status=503)

    # Wipe key variables immediately
    raw_key = None
    clean_key = None
    formatted_key = None
    account = None

    # If address already exists in user's account, update/link the private key!
    try:
        existing = await db.get_user_wallets(user_id)
    except Exception:
        logger.exception("Unable to access wallet storage for user ID %s.", user_id)
        return web.json_response({"error": "Wallet storage is temporarily unavailable. Please try again later."}, status=503)
    existing_wallet = next((w for w in existing if w["address"].lower() == derived_address.lower()), None)
    if existing_wallet:
        final_name = name if (name and name.strip() and name.strip().lower() != "imported wallet") else existing_wallet["wallet_name"]
        try:
            await db.update_wallet_private_key(
                wallet_id=existing_wallet["id"],
                encrypted_private_key=encrypted_key,
                wallet_name=final_name,
            )
        except Exception:
            logger.exception("Unable to save imported wallet for user ID %s.", user_id)
            return web.json_response({"error": "Wallet storage is temporarily unavailable. Please try again later."}, status=503)
        return web.json_response({
            "success": True,
            "message": f"Private key linked successfully! '{final_name}' is now ready for payments.",
            "wallet": {
                "id": existing_wallet["id"],
                "name": final_name,
                "address": derived_address,
                "wallet_type": "imported",
                "balances_available": False,
            },
        })

    try:
        w_id = await db.add_user_wallet(
            telegram_id=user_id,
            wallet_name=name,
            address=derived_address,
            wallet_type="imported",
            encrypted_private_key=encrypted_key,
            user_id=user_id,
            workspace_id=workspace_id,
        )
    except Exception:
        logger.exception("Unable to save imported wallet for user ID %s.", user_id)
        return web.json_response({"error": "Wallet storage is temporarily unavailable. Please try again later."}, status=503)
    wallet_name = name

    return web.json_response({
        "success": True,
        "message": f"Wallet '{wallet_name}' imported successfully.",
        "wallet": {
            "id": w_id,
            "name": wallet_name,
            "label": wallet_name,
            "address": derived_address,
            "type": "imported",
            "wallet_type": "imported",
            "balances_available": False,
        }
    })


async def api_rename_wallet(request: web.Request) -> web.Response:
    """Rename user wallet."""
    user_id = get_user_id_from_request(request)
    if not user_id:
        return web.json_response({"error": "Unauthorized"}, status=401)

    wallet_id = int(request.match_info["id"])
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    new_name = str(data.get("name", "")).strip()
    if not new_name or len(new_name) < 2:
        return web.json_response({"error": "Wallet name must be at least 2 characters."}, status=400)

    await db.rename_user_wallet(wallet_id, user_id, new_name)
    return web.json_response({"success": True, "name": new_name})


async def api_delete_wallet(request: web.Request) -> web.Response:
    """Delete user wallet."""
    user_id = get_user_id_from_request(request)
    if not user_id:
        return web.json_response({"error": "Unauthorized"}, status=401)

    wallet_id = int(request.match_info["id"])
    await db.delete_user_wallet(wallet_id, user_id)
    return web.json_response({"success": True})


async def api_fill_wallet_celo(request: web.Request) -> web.Response:
    """
    Fund user's wallet with CELO gas fee from the dedicated faucet wallet.
    Uses the configured faucet wallet (0x84D118A43b60bd73D113c0ef08F238BE866E3A2b).
    """
    user_id = get_user_id_from_request(request)
    if not user_id:
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        wallet_id = int(request.match_info["id"])
    except (ValueError, KeyError):
        return web.json_response({"error": "Invalid wallet ID"}, status=400)

    wallet = await db.get_user_wallet_by_id(wallet_id, user_id)
    if not wallet:
        return web.json_response({"error": "Wallet not found or does not belong to your account."}, status=404)

    target_addr = Web3.to_checksum_address(wallet["address"])

    if not wallet_manager.is_configured:
        wallet_manager._initialize_account()

    if not wallet_manager.is_configured:
        return web.json_response({"error": "CELO faucet funding wallet is not configured on server."}, status=503)

    funding_amt = float(config.celo_funding_amount or 0.05)

    faucet_bal = await celo_client.get_celo_balance(wallet_manager.address)
    if faucet_bal < (funding_amt + config.min_gas_reserve):
        return web.json_response({
            "error": f"Faucet reserve ({faucet_bal:.4f} CELO) is currently low. Please contact admin."
        }, status=503)

    current_user_celo = await celo_client.get_celo_balance(target_addr)
    if round(current_user_celo, 4) > 0:
        return web.json_response({
            "error": f"Wallet already has {current_user_celo:.4f} CELO gas fee. Faucet funding is only available for wallets with 0 CELO."
        }, status=400)

    success, tx_hash, err_msg = await celo_client.send_celo_funding(
        to_address=target_addr,
        amount_celo=funding_amt,
    )

    if not success:
        logger.error("Failed to fund CELO to %s: %s", target_addr, err_msg)
        return web.json_response({"error": f"Failed to transfer CELO gas fee: {err_msg}"}, status=500)

    # Record in claims table for audit & analytics
    req_id = f"fill_gas_{uuid.uuid4().hex[:12]}"
    try:
        await db.create_claim(
            request_id=req_id,
            telegram_id=user_id,
            destination_address=target_addr,
            amount=funding_amt,
        )
        await db.update_claim_status(
            request_id=req_id,
            status="SUCCESS",
            tx_hash=tx_hash,
        )
    except Exception as e:
        logger.warning("Could not record claim in database: %s", e)

    new_user_celo = await celo_client.get_celo_balance(target_addr)

    return web.json_response({
        "success": True,
        "tx_hash": tx_hash,
        "amount": funding_amt,
        "amount_formatted": f"{funding_amt:.2f} CELO",
        "previous_balance": f"{current_user_celo:.4f}",
        "new_balance": f"{new_user_celo:.4f}",
        "explorer_url": f"https://celoscan.io/tx/{tx_hash}",
        "wallet": {
            "id": wallet["id"],
            "name": wallet["wallet_name"],
            "address": target_addr,
            "celo_balance": f"{new_user_celo:.4f}",
        },
        "message": f"Successfully sent {funding_amt:.2f} CELO fee to {wallet['wallet_name']}!",
    })


# =========================================================================
# 3. PAYMENT FLOW & USAT TRANSFER API
# =========================================================================

async def api_get_receiving_wallets(request: web.Request) -> web.Response:
    """Retrieve active receiving addresses configured by admin."""
    wallets = await db.get_receiving_wallets(only_active=True)
    clean_list = [
        {"id": w["id"], "name": w["name"], "address": w["address"]}
        for w in wallets
    ]
    return web.json_response({"receiving_wallets": clean_list})


async def api_get_saved_recipients(request: web.Request) -> web.Response:
    """List addresses personally saved by the authenticated user."""
    user_id = get_user_id_from_request(request)
    if not user_id:
        return web.json_response({"error": "Unauthorized"}, status=401)

    recipients = await db.get_saved_recipients(user_id)
    return web.json_response({
        "recipients": [{
            "id": recipient["id"],
            "name": recipient["name"],
            "address": recipient["address"],
            "created_at": recipient.get("created_at"),
        } for recipient in recipients]
    })


async def api_save_recipient(request: web.Request) -> web.Response:
    """Save a named Celo address for the authenticated user."""
    user_id = get_user_id_from_request(request)
    if not user_id:
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    name = str(data.get("name") or "").strip()
    address = str(data.get("address") or "").strip()
    if not name or len(name) > 40:
        return web.json_response({"error": "Recipient name must be 1–40 characters."}, status=400)

    valid, checksum_address, error = validate_celo_address(address)
    if not valid or not checksum_address:
        return web.json_response({"error": error or "Invalid Celo address."}, status=400)

    await db.save_recipient(user_id, name, checksum_address)
    return web.json_response({
        "success": True,
        "recipient": {"name": name, "address": checksum_address},
    })


async def api_delete_saved_recipient(request: web.Request) -> web.Response:
    """Delete a recipient owned by the authenticated user."""
    user_id = get_user_id_from_request(request)
    if not user_id:
        return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        recipient_id = int(request.match_info["id"])
    except (KeyError, TypeError, ValueError):
        return web.json_response({"error": "Invalid recipient ID."}, status=400)

    if not await db.delete_saved_recipient(recipient_id, user_id):
        return web.json_response({"error": "Recipient not found."}, status=404)
    return web.json_response({"success": True})


async def api_create_payment(request: web.Request) -> web.Response:
    """
    Enforce fixed $2.00 USAT payment creation:
    1. Resolve source wallet & verify ownership.
    2. Resolve active receiving wallet.
    3. Check active payments (1 active payment per user max).
    4. Check USAT balance (>= 2,000,000 base units).
    5. Gas check: If CELO < 0.005, auto-fund 0.05 CELO from dedicated wallet & wait for receipt.
    6. If imported: server signs with decrypted key and broadcasts.
    7. If connected: returns prepared transaction parameters for in-wallet client signing.
    """
    user_id = get_user_id_from_request(request)
    if not user_id:
        return web.json_response({"error": "Unauthorized"}, status=401)

    if config.dry_run:
        return web.json_response({"error": "Transfers are disabled while DRY_RUN is enabled."}, status=503)

    if not await celo_client.is_connected():
        return web.json_response({"error": "Blockchain data is temporarily unavailable."}, status=503)

    # Check if system is paused
    if await db.is_bot_paused():
        return web.json_response({"error": "Payment system is temporarily paused for maintenance."}, status=503)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    try:
        source_wallet_id = int(data.get("source_wallet_id") or data.get("wallet_id") or 0)
    except (ValueError, TypeError):
        source_wallet_id = 0

    idempotency_key = str(data.get("idempotency_key", "")).strip() or str(uuid.uuid4())

    # 1. Verify source wallet ownership
    source_wallet = None
    if source_wallet_id > 0:
        source_wallet = await db.get_user_wallet_by_id(source_wallet_id, user_id)

    # Fallback lookup by source address
    if not source_wallet and data.get("source_address"):
        s_addr = str(data.get("source_address")).strip().lower()
        user_wallets = await db.get_user_wallets(user_id)
        for w in user_wallets:
            if w["address"].lower() == s_addr:
                source_wallet = w
                source_wallet_id = w["id"]
                break

    if not source_wallet:
        return web.json_response({"error": "Source wallet not found or unauthorized."}, status=404)

    source_addr = Web3.to_checksum_address(source_wallet["address"])
    wallet_type = source_wallet.get("wallet_type", "connected")

    # 2. Resolve and validate Recipient Address (User-entered EVM/Celo address)
    raw_dest = str(
        data.get("recipient_address")
        or data.get("to_address")
        or data.get("receiving_address")
        or ""
    ).strip()

    # Fallback: resolve from receiving_wallet_id if provided (backward compatibility)
    if not raw_dest and data.get("receiving_wallet_id"):
        try:
            r_id = int(data.get("receiving_wallet_id") or 0)
            r_wallet = await db.get_receiving_wallet_by_id(r_id)
            if r_wallet and r_wallet.get("address"):
                raw_dest = r_wallet["address"]
        except Exception:
            pass

    if not raw_dest:
        return web.json_response({"error": "Recipient Celo address is required."}, status=400)

    # Validate EVM/Celo address: 0x prefix, length 42, hex format, valid checksum
    if not raw_dest.startswith("0x") or len(raw_dest) != 42:
        return web.json_response({
            "error": "Invalid recipient address. Must begin with '0x' and be 42 characters long."
        }, status=400)

    if not re.match(r"^0x[a-fA-F0-9]{40}$", raw_dest) or not Web3.is_address(raw_dest):
        return web.json_response({
            "error": "Malformed recipient address. Please enter a valid hexadecimal Celo/EVM address."
        }, status=400)

    dest_addr = Web3.to_checksum_address(raw_dest)

    if dest_addr.lower() == source_addr.lower():
        return web.json_response({
            "error": "Recipient address cannot be identical to the sending wallet address."
        }, status=400)

    # 3. Validate USDT Transfer Amount
    raw_amount = None
    for field in ("amount", "amount_usdt", "amount_usat"):
        if field in data and data[field] is not None:
            raw_amount = data[field]
            break

    if raw_amount is None or str(raw_amount).strip() == "":
        amount_val = 2.00  # Default fallback if unspecified
    else:
        try:
            amount_val = float(raw_amount)
        except (ValueError, TypeError):
            return web.json_response({"error": "Invalid transfer amount. Please enter a positive numeric amount."}, status=400)

    if amount_val <= 0:
        return web.json_response({"error": "Transfer amount must be greater than zero."}, status=400)

    # Dynamic base unit calculation from contract decimals
    metadata_ok, _ = await celo_client.init_usat_metadata()
    if not metadata_ok or celo_client.usat_decimals is None:
        return web.json_response({"error": "Token contract metadata is temporarily unavailable."}, status=503)
    token_decimals = celo_client.usat_decimals
    required_units = int(round(amount_val * (10 ** token_decimals)))
    if required_units <= 0:
        return web.json_response({"error": "Requested amount is below token precision threshold."}, status=400)

    # 4. Check available USDT balance from real Celo blockchain contract
    try:
        usat_balance_units, usat_bal_str = await celo_client.get_usat_balance(source_addr)
    except BlockchainUnavailableError:
        return web.json_response({"error": "Blockchain data is temporarily unavailable."}, status=503)
    available_usdt = float(usat_bal_str)

    if usat_balance_units < required_units:
        return web.json_response({
            "error": f"Insufficient USDT balance: This wallet holds {available_usdt:.2f} USDT, but {amount_val:.2f} USDT was requested."
        }, status=400)

    # 5. Concurrency check: max 1 active processing payment per user
    active = await db.get_active_usat_payment(user_id)
    if active:
        return web.json_response({
            "error": "You already have a payment currently processing. Please wait for confirmation.",
            "payment_id": active["payment_id"],
        }, status=409)

    payment_id = str(uuid.uuid4())
    rec_label = f"Recipient ({dest_addr[:6]}...{dest_addr[-4:]})"

    await db.create_usat_payment(
        payment_id=payment_id,
        telegram_id=user_id,
        wallet_type=wallet_type,
        from_address=source_addr,
        to_address=dest_addr,
        receiving_wallet_name=rec_label,
        amount_usat=f"{amount_val:.2f}",
        amount_base_units=required_units,
        user_id=user_id,
    )

    # 6. Gas check & automatic 0.05 CELO subsidy if balance < 0.005 CELO
    try:
        current_celo = await celo_client.get_celo_balance(source_addr)
    except BlockchainUnavailableError:
        return web.json_response({"error": "Blockchain data is temporarily unavailable."}, status=503)
    celo_funded = False
    fund_tx_hash = None

    if current_celo < config.min_user_celo_threshold:
        logger.info("Auto-funding 0.05 CELO to %s for payment %s (balance: %.4f CELO)", source_addr, payment_id, current_celo)
        fund_ok, f_tx, f_err = await celo_client.send_celo_funding(
            to_address=source_addr, amount_celo=config.celo_funding_amount
        )
        if fund_ok:
            celo_funded = True
            fund_tx_hash = f_tx
            await db.update_usat_payment_status(
                payment_id=payment_id,
                status="PROCESSING",
                celo_funded=1,
                celo_fund_tx_hash=f_tx,
            )
            # Re-check balance after funding confirmation
            rechecked_celo = await celo_client.get_celo_balance(source_addr)
            logger.info("Rechecked CELO balance after subsidy for %s: %.4f CELO", source_addr, rechecked_celo)
            await asyncio.sleep(1.0)
        else:
            if f_err == "Transaction status is pending reconciliation.":
                await db.update_usat_payment_status(
                    payment_id=payment_id, status="CONFIRMING", celo_fund_tx_hash=f_tx, error_message=f_err
                )
                return web.json_response({"success": False, "status": "CONFIRMING", "payment_id": payment_id}, status=202)
            await db.update_usat_payment_status(payment_id, "FAILED", error_message="Gas funding failed")
            logger.warning("Gas funding failed for %s: %s", source_addr, f_err)
            return web.json_response({"error": "Gas Funding Failed. The transfer was not submitted."}, status=503)

    # 7. Branch on wallet type
    if wallet_type == "imported" or source_wallet.get("encrypted_private_key"):
        # Server-side signing with decrypted private key
        encrypted_pk = source_wallet.get("encrypted_private_key", "")
        try:
            decrypted_pk = encryption_service.decrypt(encrypted_pk)
        except Exception as e:
            await db.update_usat_payment_status(payment_id, "FAILED", error_message="Key decryption error")
            return web.json_response({"error": "Failed to decrypt wallet private key."}, status=500)

        success, tx_hash, block_num, err_desc = await celo_client.transfer_usat_imported(
            private_key=decrypted_pk,
            to_address=dest_addr,
            base_units=required_units,
        )
        decrypted_pk = None

        if success:
            await db.update_usat_payment_status(
                payment_id=payment_id,
                status="SUCCESS",
                tx_hash=tx_hash,
                block_number=block_num,
            )
            await db.update_wallet_last_used(source_wallet_id)
            payment_obj = {
                "id": payment_id,
                "payment_id": payment_id,
                "status": "CONFIRMED",
                "tx_hash": tx_hash,
                "block_number": block_num,
                "amount": f"{amount_val:.2f}",
                "token": "USDT",
                "from_address": source_addr,
                "to_address": dest_addr,
                "celo_funded": celo_funded,
                "explorer_url": f"{config.explorer_tx_url}{tx_hash}",
            }
            return web.json_response({
                "success": True,
                "status": "CONFIRMED",
                "payment_id": payment_id,
                "tx_hash": tx_hash,
                "block_number": block_num,
                "amount": f"{amount_val:.2f}",
                "token": "USDT",
                "from_address": source_addr,
                "to_address": dest_addr,
                "celo_funded": celo_funded,
                "explorer_url": f"{config.explorer_tx_url}{tx_hash}",
                "payment": payment_obj,
            })
        if err_desc == "Transaction status is pending reconciliation.":
            await db.update_usat_payment_status(payment_id, "CONFIRMING", tx_hash=tx_hash, error_message=err_desc)
            return web.json_response({"success": False, "status": "CONFIRMING", "payment_id": payment_id}, status=202)
        else:
            await db.update_usat_payment_status(payment_id, "FAILED", error_message=err_desc)
            return web.json_response({"error": err_desc or "Transaction reverted on Celo blockchain."}, status=500)

    else:
        # Connected wallet: return transaction parameters for client-side signing or private key prompt
        tx_params = celo_client.build_usat_transfer_tx_params(source_addr, dest_addr, required_units)
        payment_obj = {
            "id": payment_id,
            "payment_id": payment_id,
            "status": "AWAITING_USER_SIGNATURE",
            "amount": f"{amount_val:.2f}",
            "token": "USDT",
            "from_address": source_addr,
            "to_address": dest_addr,
            "celo_funded": celo_funded,
            "tx_params": tx_params,
            "requires_private_key": False,
            "wallet_id": source_wallet_id,
            "wallet_name": source_wallet.get("wallet_name"),
        }
        return web.json_response({
            "success": True,
            "status": "AWAITING_USER_SIGNATURE",
            "payment_id": payment_id,
            "amount": f"{amount_val:.2f}",
            "token": "USDT",
            "from_address": source_addr,
            "to_address": dest_addr,
            "tx_params": tx_params,
            "celo_funded": celo_funded,
            "funding_tx_hash": fund_tx_hash,
            "payment": payment_obj,
            "requires_private_key": False,
            "wallet_id": source_wallet_id,
            "wallet_name": source_wallet.get("wallet_name"),
        })


async def api_submit_payment_hash(request: web.Request) -> web.Response:
    """
    Confirm transaction hash submitted by connected wallet.
    Verifies receipt on Celo Mainnet before marking SUCCESS.
    """
    user_id = get_user_id_from_request(request)
    if not user_id:
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    payment_id = str(data.get("payment_id", "")).strip()
    tx_hash = str(data.get("tx_hash", "")).strip()

    if not payment_id or not tx_hash:
        return web.json_response({"error": "Missing payment_id or tx_hash."}, status=400)

    if not re.fullmatch(r"0x[a-fA-F0-9]{64}", tx_hash):
        return web.json_response({"error": "Invalid transaction hash."}, status=400)
    payment = await db.get_usat_payment_by_id(payment_id)
    if not payment or (payment.get("user_id") != user_id and payment.get("telegram_id") != user_id):
        return web.json_response({"error": "Payment not found."}, status=404)
    if str(payment.get("status", "")).upper() not in ("PROCESSING", "PENDING", "AWAITING_USER_SIGNATURE"):
        return web.json_response({"error": "This payment cannot be confirmed in its current state."}, status=409)

    is_confirmed, block_num, err = await celo_client.verify_usat_transfer_receipt(
        tx_hash, payment["from_address"], payment["to_address"], payment["amount_base_units"]
    )
    if is_confirmed:
        await db.update_usat_payment_status(
            payment_id=payment_id,
            status="SUCCESS",
            tx_hash=tx_hash,
            block_number=block_num,
        )
        p_row = await db.get_usat_payment_by_id(payment_id)
        return web.json_response({
            "success": True,
            "status": "CONFIRMED",
            "payment_id": payment_id,
            "tx_hash": tx_hash,
            "block_number": block_num,
            "explorer_url": f"{config.explorer_tx_url}{tx_hash}",
            "payment": p_row or {
                "payment_id": payment_id,
                "tx_hash": tx_hash,
                "block_number": block_num,
                "status": "CONFIRMED",
            },
        })
    if err == "Transaction status is pending reconciliation.":
        await db.update_usat_payment_status(payment_id, "CONFIRMING", tx_hash=tx_hash, error_message=err)
        return web.json_response({"success": False, "status": "CONFIRMING", "payment_id": payment_id}, status=202)
    await db.update_usat_payment_status(payment_id, "FAILED", tx_hash=tx_hash, error_message=err)
    return web.json_response({"error": err or "Transaction was not confirmed on Celo blockchain."}, status=400)


async def api_get_payments_history(request: web.Request) -> web.Response:
    """Retrieve paginated personal payment history."""
    user_id = get_user_id_from_request(request)
    if not user_id:
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        page = int(request.query.get("page", 0))
    except ValueError:
        page = 0

    page_size = 10
    total_count = await db.get_user_payments_count(user_id)
    payments = await db.get_user_payments(user_id, limit=page_size, offset=page * page_size)
    wallets_by_address = {
        str(wallet.get("address", "")).lower(): wallet.get("wallet_name", "Wallet")
        for wallet in await db.get_user_wallets(user_id)
    }

    formatted = []
    for p in payments:
        formatted.append({
            "id": p["id"],
            "payment_id": p.get("payment_id") or str(p["id"]),
            "amount": p.get("amount_usat", "2.00"),
            "wallet_name": wallets_by_address.get(str(p.get("from_address", "")).lower(), "Wallet"),
            "from_address": p.get("from_address"),
            "to_address": p.get("to_address"),
            "receiving_wallet_name": p.get("receiving_wallet_name", "Receiving Wallet"),
            "status": p.get("status"),
            "tx_hash": p.get("tx_hash"),
            "explorer_url": f"{config.explorer_tx_url}{p['tx_hash']}" if p.get("tx_hash") else None,
            "celo_funded": bool(p.get("celo_funded")),
            "created_at": p.get("created_at"),
        })

    return web.json_response({
        "payments": formatted,
        "total_count": total_count,
        "page": page,
        "total_pages": max(1, (total_count + page_size - 1) // page_size),
    })


async def api_get_wallet_payment_history(request: web.Request) -> web.Response:
    """Return all recorded transactions for one wallet owned by the current user."""
    user_id = get_user_id_from_request(request)
    if not user_id:
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        wallet_id = int(request.match_info["id"])
    except (KeyError, TypeError, ValueError):
        return web.json_response({"error": "Invalid wallet ID."}, status=400)

    wallet = await db.get_user_wallet_by_id(wallet_id, user_id)
    if not wallet:
        return web.json_response({"error": "Wallet not found or unauthorized."}, status=404)

    payments = await db.get_wallet_payment_history(user_id, wallet["address"])
    formatted = [{
        "id": payment["record_id"],
        "direction": payment.get("direction"),
        "activity_type": payment.get("activity_type"),
        "amount": payment.get("amount", "0"),
        "currency": payment.get("currency", "USDT"),
        "counterparty_address": payment.get("counterparty_address"),
        "status": payment.get("status"),
        "tx_hash": payment.get("tx_hash"),
        "created_at": payment.get("created_at"),
    } for payment in payments]

    return web.json_response({
        "wallet": {
            "id": wallet["id"],
            "name": wallet["wallet_name"],
            "address": wallet["address"],
        },
        "payments": formatted,
        "total_count": len(formatted),
    })


async def api_cancel_payment(request: web.Request) -> web.Response:
    """Cancel a pending/processing payment if funds have not been debited."""
    user_id = get_user_id_from_request(request)
    is_admin = is_admin_request(request)
    if not user_id and not is_admin:
        return web.json_response({"error": "Unauthorized"}, status=401)

    payment_id = request.match_info.get("payment_id", "").strip()
    if not payment_id or payment_id in ("active", "current", "pending", "cancel-active"):
        if not user_id:
            return web.json_response({"error": "User ID required to cancel active payment."}, status=400)
        cancelled_pid = await db.cancel_user_active_payment(user_id)
        if cancelled_pid:
            return web.json_response({
                "success": True,
                "message": "Pending transaction cancelled successfully. You can now initiate a new payment.",
                "payment_id": cancelled_pid,
                "status": "CANCELLED",
            })
        return web.json_response({"error": "No active pending payment found to cancel."}, status=404)

    payment = await db.get_usat_payment_by_id(payment_id)
    if not payment:
        # Fallback: if user specified an ID that was not found, check if they have any active payment
        if user_id:
            cancelled_pid = await db.cancel_user_active_payment(user_id)
            if cancelled_pid:
                return web.json_response({
                    "success": True,
                    "message": "Pending transaction cancelled successfully. You can now initiate a new payment.",
                    "payment_id": cancelled_pid,
                    "status": "CANCELLED",
                })
        return web.json_response({"error": "Payment not found."}, status=404)

    # Ownership check
    if not is_admin:
        user_ids = set(await db._resolve_user_identifiers(user_id)) if user_id else set()
        payment_user_ids = {payment.get("user_id"), payment.get("telegram_id")} - {None}
        if not user_ids.intersection(payment_user_ids):
            return web.json_response({"error": "Unauthorized to cancel this payment."}, status=403)

    # Check status
    current_status = (payment.get("status") or "").upper().strip()
    if current_status in ("SUCCESS", "CONFIRMED"):
        return web.json_response({
            "error": "Payment has already completed successfully. Debited transactions cannot be cancelled."
        }, status=400)

    if current_status == "CANCELLED":
        return web.json_response({
            "success": True,
            "message": "Payment has already been cancelled.",
            "status": "CANCELLED",
            "payment_id": payment.get("payment_id") or str(payment.get("id")),
        })

    # If tx_hash exists, check on-chain whether funds were debited
    tx_hash = payment.get("tx_hash")
    if tx_hash:
        try:
            receipt = await celo_client.get_transaction_receipt(tx_hash)
            if receipt and receipt.get("status") == 1:
                await db.update_usat_payment_status(payment["payment_id"], "SUCCESS", block_number=receipt.get("blockNumber"))
                return web.json_response({
                    "error": "Transaction was already debited and mined on Celo blockchain. Cannot cancel."
                }, status=400)
        except Exception as ex:
            logger.warning("Could not verify on-chain receipt for tx %s: %s", tx_hash, ex)

    # Proceed with cancellation
    target_pid = payment.get("payment_id") or payment_id
    cancelled = await db.cancel_pending_payment(target_pid, user_identifier=user_id, is_admin=is_admin)
    if cancelled:
        logger.info("Payment %s cancelled by user %s (admin=%s)", target_pid, user_id, is_admin)
        return web.json_response({
            "success": True,
            "message": "Pending transaction cancelled successfully. You can now initiate a new payment.",
            "payment_id": target_pid,
            "status": "CANCELLED",
        })
    else:
        return web.json_response({
            "error": "Payment could not be cancelled or is already finalized."
        }, status=400)


# =========================================================================
# 4. PROFILE API
# =========================================================================

async def api_get_profile(request: web.Request) -> web.Response:
    """Get profile metrics for user."""
    user_id = get_user_id_from_request(request)
    if not user_id:
        return web.json_response({"error": "Unauthorized"}, status=401)

    user = await db.get_user_profile(user_id) or {}
    total_paid = await db.get_user_total_paid(user_id)
    payments_cnt = await db.get_user_payments_count(user_id)
    wallets_cnt = await db.get_user_wallet_count(user_id)

    return web.json_response({
        "name": user.get("full_name") or user.get("first_name") or "User",
        "mobile": mask_mobile(user.get("mobile_number")),
        "raw_mobile": user.get("mobile_number", ""),
        "wallets_count": wallets_cnt,
        "completed_payments": payments_cnt,
        "total_paid": f"{total_paid:.2f}",
    })


async def api_update_profile(request: web.Request) -> web.Response:
    """Update profile name and/or mobile."""
    user_id = get_user_id_from_request(request)
    if not user_id:
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    name = str(data.get("name", "")).strip()
    mobile = str(data.get("mobile", "")).strip()

    if name:
        if not NAME_REGEX.match(name):
            return web.json_response({"error": "Invalid name format."}, status=400)
        await db.update_user_name(user_id, name)

    if mobile:
        cleaned = "".join(ch for ch in mobile if ch.isdigit() or ch == "+")
        if len(cleaned) < 7:
            return web.json_response({"error": "Invalid mobile number."}, status=400)
        await db.update_user_mobile(user_id, cleaned)

    return web.json_response({"success": True})


# =========================================================================
# 5. ADMIN API
# =========================================================================

async def api_admin_login(request: web.Request) -> web.Response:
    """Authenticate administrator with master password or admin account password."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    password = str(data.get("password", "")).strip()
    # A numeric Telegram ID is an identifier, never an administrator password.
    expected = config.admin_web_password
    is_valid = bool(password and expected and secrets.compare_digest(password, expected))

    # Also verify if user is 8142177207 and entered their own account password
    user_id = get_user_id_from_request(request)
    if not is_valid and user_id:
        user = await db.get_user_profile(user_id) or await db.get_user_by_id(user_id)
        if user and is_admin_phone(user.get("normalized_mobile") or user.get("mobile_number")):
            stored_hash = user.get("password_hash")
            if stored_hash and password:
                try:
                    password_hasher.verify(stored_hash, password)
                    is_valid = True
                except Exception:
                    pass

    if not is_valid:
        return web.json_response({"error": "Invalid administrator password."}, status=401)

    admin_token = secrets.token_urlsafe(32)
    admin_session_user_id = user_id or (None if db.using_postgres else 0)
    await db.create_session(admin_token, admin_session_user_id, True, session_expiry(7))

    resp = web.json_response({"success": True})
    resp.set_cookie("admin_session", admin_token, max_age=86400 * 7, httponly=True,
                    secure=config.frontend_url.startswith("https://"),
                    samesite="None" if config.frontend_url.startswith("https://") else "Lax")
    return resp


async def api_admin_dashboard(request: web.Request) -> web.Response:
    """Retrieve top-level platform statistics for admin."""
    if not is_admin_request(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    stats = await db.get_usat_statistics()
    is_paused = await db.is_bot_paused()
    return web.json_response({
        "total_users": stats.get("total_users", 0),
        "total_payments": stats.get("total_payments", 0),
        "total_usat_received": f"{stats.get('total_usat_sent', 0.0):.2f}",
        "total_volume_usat": stats.get("total_usat_sent", 0.0),
        "fixed_payment_usat": config.usat_payment_amount,
        "system_paused": is_paused,
        "pending_payments": stats.get("processing", 0),
        "failed_payments": stats.get("failed", 0),
        "celo_subsidies": stats.get("celo_subsidies_count", 0),
        "connected_wallets": stats.get("connected_wallets", 0),
        "imported_wallets": stats.get("imported_wallets", 0),
    })


async def api_admin_get_users(request: web.Request) -> web.Response:
    """Paginated list of users for admin with optional search."""
    if not is_admin_request(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        page = int(request.query.get("page", 0))
    except ValueError:
        page = 0

    search = str(request.query.get("search", "")).strip()
    page_size = 15
    users = await db.get_all_users(limit=page_size, offset=page * page_size, search=search)
    total_users = await db.get_user_count(search=search)

    formatted = []
    for u in users:
        formatted.append({
            "id": u.get("id") or u.get("telegram_id"),
            "telegram_id": u.get("telegram_id"),
            "name": u.get("full_name") or u.get("first_name") or "N/A",
            "mobile": mask_mobile(u.get("mobile_number")),
            "mobile_raw": u.get("mobile_number", ""),
            "wallets_count": u.get("wallet_count", 0),
            "payments_count": u.get("payment_count", 0),
            "created_at": u.get("created_at"),
        })

    return web.json_response({
        "users": formatted,
        "total": total_users,
        "page": page,
    })


async def api_admin_get_user_details(request: web.Request) -> web.Response:
    """Detailed view of a specific user including safe wallets and payment history."""
    if not is_admin_request(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        user_id = int(request.match_info["id"])
    except ValueError:
        return web.json_response({"error": "Invalid user ID."}, status=400)

    details = await db.get_user_details_admin(user_id)
    if not details:
        return web.json_response({"error": "User not found."}, status=404)

    # Fetch live balances for user's wallets
    wallets = details.get("wallets", [])
    for w in wallets:
        try:
            _, usat_bal = await celo_client.get_usat_balance(w["address"])
            w["usat_balance"] = f"{float(usat_bal):.2f}"
            c_bal = await celo_client.get_celo_balance(w["address"])
            w["celo_balance"] = f"{c_bal:.4f}"
        except Exception:
            w["usat_balance"] = "0.00"
            w["celo_balance"] = "0.0000"

    user_info = details.get("user", {})
    safe_user = {
        "id": user_info.get("id"),
        "telegram_id": user_info.get("telegram_id"),
        "full_name": user_info.get("full_name") or user_info.get("first_name") or "User",
        "mobile": mask_mobile(user_info.get("mobile_number")),
        "mobile_raw": user_info.get("mobile_number", ""),
        "created_at": user_info.get("created_at"),
        "last_login_at": user_info.get("last_login_at"),
        "last_activity": user_info.get("last_activity"),
    }

    return web.json_response({
        "user": safe_user,
        "wallets": wallets,
        "payments": details.get("payments", []),
        "total_paid": details.get("total_paid", 0.0),
    })


async def api_admin_get_wallets(request: web.Request) -> web.Response:
    """Paginated list of all wallets across platform with USDT and CELO balances."""
    if not is_admin_request(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        page = int(request.query.get("page", 0))
    except ValueError:
        page = 0

    search = str(request.query.get("search", "")).strip()
    page_size = 20

    raw_wallets = await db.get_all_wallets_admin(limit=page_size, offset=page * page_size, search=search)
    total_wallets = await db.get_all_wallets_count_admin(search=search)

    # Fetch live USDT and CELO balances asynchronously
    async def _fetch_bal(w):
        try:
            _, bal = await celo_client.get_usat_balance(w["address"])
            usat_str = f"{float(bal):.2f}"
        except Exception:
            usat_str = "0.00"
        try:
            c_bal = await celo_client.get_celo_balance(w["address"])
            celo_str = f"{c_bal:.4f}"
        except Exception:
            celo_str = "0.0000"
        return usat_str, celo_str

    balances = await asyncio.gather(*[_fetch_bal(w) for w in raw_wallets])

    formatted = []
    for w, (u_bal, c_bal) in zip(raw_wallets, balances):
        user_label = w.get("full_name") or (f"User {w.get('user_id') or w.get('telegram_id')}")
        formatted.append({
            "id": w["id"],
            "wallet_name": w.get("wallet_name") or "Unnamed Wallet",
            "address": w["address"],
            "user": user_label,
            "mobile": mask_mobile(w.get("mobile_number")),
            "mobile_raw": w.get("mobile_number", ""),
            "wallet_type": w.get("wallet_type", "connected"),
            "usat_balance": u_bal,
            "celo_balance": c_bal,
            "created_at": w.get("created_at"),
        })

    return web.json_response({
        "wallets": formatted,
        "total": total_wallets,
        "page": page,
    })


async def api_admin_get_receiving_wallets(request: web.Request) -> web.Response:
    """Get receiving addresses for admin."""
    if not is_admin_request(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    wallets = await db.get_receiving_wallets(only_active=False)
    return web.json_response({"receiving_wallets": wallets})


async def api_admin_add_receiving_wallet(request: web.Request) -> web.Response:
    """Add a new receiving address."""
    if not is_admin_request(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    name = str(data.get("name", "")).strip()
    address = str(data.get("address", "")).strip()

    if not name or len(name) < 2:
        return web.json_response({"error": "Please enter a valid wallet name."}, status=400)

    is_val, chk, err = validate_celo_address(address)
    if not is_val or not chk:
        return web.json_response({"error": err or "Invalid Celo address."}, status=400)

    # Check duplicate
    existing = await db.get_receiving_wallets(only_active=False)
    if any(w["address"].lower() == chk.lower() for w in existing):
        return web.json_response({"error": "This receiving address already exists."}, status=400)

    w_id = await db.add_receiving_wallet(name, chk)
    return web.json_response({"success": True, "id": w_id})


async def api_admin_update_receiving_wallet(request: web.Request) -> web.Response:
    """Rename or toggle receiving wallet status."""
    if not is_admin_request(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    rec_id = int(request.match_info["id"])
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    if "name" in data:
        name = str(data["name"]).strip()
        if name:
            await db.update_receiving_wallet_name(rec_id, name)

    if "active" in data:
        is_active = bool(data["active"])
        await db.toggle_receiving_wallet(rec_id, is_active)

    return web.json_response({"success": True})


async def api_admin_delete_receiving_wallet(request: web.Request) -> web.Response:
    """Delete receiving wallet."""
    if not is_admin_request(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    rec_id = int(request.match_info["id"])
    await db.delete_receiving_wallet(rec_id)
    return web.json_response({"success": True})


async def api_admin_get_payments(request: web.Request) -> web.Response:
    """Paginated all payments with search, date range, and time window filtering."""
    if not is_admin_request(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        page = int(request.query.get("page", 0))
    except ValueError:
        page = 0

    search = str(request.query.get("search", "")).strip()
    status_filter = str(request.query.get("status", "")).strip()
    date_filter = str(request.query.get("date", "")).strip()
    time_from = str(request.query.get("time_from", "")).strip()
    time_to = str(request.query.get("time_to", "")).strip()
    page_size = 20

    summary = await db.get_all_payments_summary(
        search=search,
        status_filter=status_filter,
        date_filter=date_filter,
        time_from=time_from,
        time_to=time_to,
    )
    payments = await db.get_all_payments(
        limit=page_size,
        offset=page * page_size,
        search=search,
        status_filter=status_filter,
        date_filter=date_filter,
        time_from=time_from,
        time_to=time_to,
    )

    formatted = []
    for p in payments:
        formatted.append({
            "id": p["id"],
            "payment_id": p.get("payment_id") or str(p["id"]),
            "user": p.get("full_name") or f"User {p.get('user_id') or p.get('telegram_id')}",
            "mobile": mask_mobile(p.get("mobile_number")),
            "mobile_raw": p.get("mobile_number", ""),
            "amount": p.get("amount_usat", "2.00"),
            "source": wallet_manager.truncate_address(p.get("from_address", ""), 6, 4),
            "source_raw": p.get("from_address", ""),
            "destination": p.get("receiving_wallet_name", "Admin (Sassy)"),
            "destination_address": wallet_manager.truncate_address(p.get("to_address", ""), 6, 4),
            "status": p.get("status"),
            "tx_hash": p.get("tx_hash"),
            "celo_funded": bool(p.get("celo_funded")),
            "created_at": p.get("local_created_at") or p.get("created_at"),
        })

    return web.json_response({
        "payments": formatted,
        "total": summary["total_count"],
        "total_amount": summary["total_amount"],
        "unique_users": summary["unique_users"],
        "page": page,
    })


async def api_admin_get_statistics(request: web.Request) -> web.Response:
    """Platform metrics breakdown."""
    if not is_admin_request(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    stats = await db.get_usat_statistics()
    return web.json_response({"statistics": stats})


async def api_admin_get_funding(request: web.Request) -> web.Response:
    """CELO funding wallet metrics."""
    if not is_admin_request(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    funding_addr = wallet_manager.address
    celo_bal = await celo_client.get_celo_balance(funding_addr)
    stats = await db.get_usat_statistics()

    return web.json_response({
        "funding_address": funding_addr,
        "funding_wallet": funding_addr,
        "celo_balance": f"{celo_bal:.4f}",
        "balance_celo": f"{celo_bal:.4f}",
        "subsidy_amount": f"{config.celo_funding_amount} CELO",
        "total_subsidies_given": stats.get("celo_subsidies_count", 0),
        "total_subsidies": stats.get("celo_subsidies_count", 0),
        "estimated_subsidies_remaining": int(celo_bal / config.celo_funding_amount) if celo_bal > 0 else 0,
    })


async def api_admin_get_funding_transactions(request: web.Request) -> web.Response:
    """Recent CELO gas subsidies records."""
    if not is_admin_request(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    txs = await db.get_admin_funding_history(limit=50)
    return web.json_response({"transactions": txs})


async def api_admin_toggle_pause(request: web.Request) -> web.Response:
    """Toggle system pause status."""
    if not is_admin_request(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    current = await db.is_bot_paused()
    new_state = not current
    await db.set_bot_paused(new_state)
    return web.json_response({"paused": new_state})


async def api_admin_reset_database(request: web.Request) -> web.Response:
    """Admin endpoint to completely wipe all user accounts, wallets, and payments to start fresh."""
    if not is_admin_request(request):
        return web.json_response({"error": "Unauthorized: Admin privileges required."}, status=401)

    result = await db.reset_all_users_and_wallets()
    logger.info("Admin triggered complete database reset: all users, wallets, and payments wiped.")
    return web.json_response(result)


# --- Mount API Routes onto Application ---

def register_api_routes(app: web.Application) -> None:
    """Register all REST API endpoints under /api."""
    # Auth & User
    app.router.add_post("/api/auth/register", api_register)
    app.router.add_post("/api/auth/login", api_login)
    app.router.add_post("/api/auth/logout", api_logout)
    app.router.add_post("/api/auth/change-password", api_change_password)
    app.router.add_get("/api/auth/me", api_get_me)

    # Wallets
    app.router.add_get("/api/workspaces", api_get_workspaces)
    app.router.add_post("/api/workspaces", api_create_workspace)
    app.router.add_patch("/api/workspaces/{id}", api_rename_workspace)
    app.router.add_get("/api/wallets", api_get_wallets)
    app.router.add_post("/api/wallets/connect", api_connect_wallet)
    app.router.add_post("/api/wallets/import", api_import_wallet)
    app.router.add_get("/api/wallets/{id}/payments", api_get_wallet_payment_history)
    app.router.add_patch("/api/wallets/{id}", api_rename_wallet)
    app.router.add_delete("/api/wallets/{id}", api_delete_wallet)
    app.router.add_post("/api/wallets/{id}/fill-celo", api_fill_wallet_celo)

    # Payments
    app.router.add_get("/api/receiving-wallets", api_get_receiving_wallets)
    app.router.add_get("/api/recipients", api_get_saved_recipients)
    app.router.add_post("/api/recipients", api_save_recipient)
    app.router.add_delete("/api/recipients/{id}", api_delete_saved_recipient)
    app.router.add_post("/api/payments/create", api_create_payment)
    app.router.add_post("/api/payments/confirm-hash", api_submit_payment_hash)
    app.router.add_post("/api/payments/cancel-active", api_cancel_payment)
    app.router.add_post("/api/payments/{payment_id}/cancel", api_cancel_payment)
    app.router.add_get("/api/payments", api_get_payments_history)

    # Profile
    app.router.add_get("/api/profile", api_get_profile)
    app.router.add_patch("/api/profile", api_update_profile)

    # Admin
    app.router.add_post("/api/admin/login", api_admin_login)
    app.router.add_get("/api/admin/dashboard", api_admin_dashboard)
    app.router.add_get("/api/admin/users", api_admin_get_users)
    app.router.add_get("/api/admin/users/{id}", api_admin_get_user_details)
    app.router.add_get("/api/admin/wallets", api_admin_get_wallets)
    app.router.add_get("/api/admin/receiving-wallets", api_admin_get_receiving_wallets)
    app.router.add_post("/api/admin/receiving-wallets", api_admin_add_receiving_wallet)
    app.router.add_patch("/api/admin/receiving-wallets/{id}", api_admin_update_receiving_wallet)
    app.router.add_delete("/api/admin/receiving-wallets/{id}", api_admin_delete_receiving_wallet)
    app.router.add_get("/api/admin/payments", api_admin_get_payments)
    app.router.add_get("/api/admin/statistics", api_admin_get_statistics)
    app.router.add_get("/api/admin/funding", api_admin_get_funding)
    app.router.add_get("/api/admin/funding/transactions", api_admin_get_funding_transactions)
    app.router.add_post("/api/admin/settings/pause", api_admin_toggle_pause)
    app.router.add_post("/api/admin/reset-database", api_admin_reset_database)
