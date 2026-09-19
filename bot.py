"""
Application entrypoint for Telegram Celo USAT Payment Bot.
Initializes logging, validates network & funding wallet configuration,
verifies the Celo Mainnet USAT token contract (decimals & symbol), initializes database,
launches the non-custodial WebApp server, and runs the aiogram polling event loop.
"""

from __future__ import annotations

import asyncio
import logging
import sys

# Ensure UTF-8 output encoding on Windows consoles to support rich emojis
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import config
from database import db
from wallet import wallet_manager
from celo import celo_client
from webapp.server import start_webapp_server
from handlers import start, profile, wallet_mgmt, payment, history, admin

# Configure clean, structured application logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("celo_usat_bot")


def print_startup_banner() -> None:
    """Print clean startup diagnostics banner."""
    mode_str = (
        "⚠️  DRY RUN MODE ENABLED\n"
        "   Simulated on-chain transactions."
        if config.dry_run
        else "🟢 LIVE CELO MAINNET MODE\n   Real on-chain USAT & CELO transactions broadcasted!"
    )
    banner = f"""
====================================================================
⚡ CELO USAT PAYMENT BOT STARTING
====================================================================
Network:         {config.network_name} (Chain ID: {config.celo_chain_id})
RPC Endpoint:    {config.celo_rpc_url}
USAT Contract:   {config.usat_contract_address}
Payment Amount:  ${config.usat_payment_amount:.2f} USAT (Fixed, Integer Base Units)
Gas Funding:     {config.celo_funding_amount} CELO auto-subsidy
Funding Wallet:  {wallet_manager.truncate_address(wallet_manager.address, 10, 6)}
WebApp Server:   http://{config.webapp_host}:{config.webapp_port}
Admin Telegram:  {config.admin_telegram_id or 'Not Configured'}
Mode:            {mode_str}
====================================================================
"""
    print(banner)


async def main() -> None:
    """Startup initialization and execution loop."""
    if not config.telegram_bot_token or config.telegram_bot_token == "your_bot_token_here":
        logger.error(
            "FATAL: TELEGRAM_BOT_TOKEN is missing or not set in .env! "
            "Please configure your bot token from @BotFather."
        )
        sys.exit(1)

    print_startup_banner()

    # 1. Initialize SQLite Database & Migrations
    logger.info("Initializing database at %s...", config.database_path)
    await db.init_db()
    logger.info("Database initialized successfully.")

    # 2. Check Celo Network Connectivity & Chain ID
    logger.info("Verifying connection to Celo RPC (%s)...", config.celo_rpc_url)
    is_connected, net_msg = await celo_client.verify_network()
    if is_connected:
        logger.info("Celo Network: %s", net_msg)
    else:
        logger.warning(
            "Network Warning: %s. Payouts will fail until RPC connection recovers.",
            net_msg,
        )

    # 3. Verify USAT Contract and Token Decimals from Blockchain
    logger.info("Verifying USAT contract %s on Celo Mainnet...", config.usat_contract_address)
    usat_ok, usat_msg = await celo_client.init_usat_metadata()
    if usat_ok:
        base_units = celo_client.get_payment_amount_base_units()
        logger.info(
            "USAT Verified on-chain: %s (%d base units per $2.00 payment)",
            usat_msg,
            base_units,
        )
    else:
        logger.warning("Could not verify USAT contract metadata: %s", usat_msg)

    # 4. Check CELO Gas Funding Wallet Configuration
    wallet_ok, wallet_msg = wallet_manager.verify_configuration()
    if wallet_ok:
        logger.info("Funding Wallet Status: %s", wallet_msg)
        if not config.dry_run and wallet_manager.is_configured:
            bal = await celo_client.get_celo_balance(wallet_manager.address)
            logger.info("Funding Wallet Balance: %.4f CELO", bal)
            if bal < config.celo_funding_amount + config.min_gas_reserve:
                logger.warning(
                    "CRITICAL: Funding wallet balance (%.4f CELO) is below required gas reserve! "
                    "Please fund %s with CELO.",
                    bal,
                    wallet_manager.address,
                )
    else:
        logger.warning("Funding Wallet notice: %s", wallet_msg)

    # 5. Start Lightweight WebApp Server for Non-Custodial Wallet Connections
    webapp_runner = None
    try:
        webapp_runner = await start_webapp_server(config.webapp_host, config.webapp_port)
    except Exception as e:
        logger.error("Failed to start WebApp server on port %d: %s", config.webapp_port, e)

    # 6. Initialize aiogram Bot and Dispatcher
    bot = Bot(
        token=config.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Register routers (strictly excluding the old faucet router from user interface)
    dp.include_router(start.router)
    dp.include_router(profile.router)
    dp.include_router(wallet_mgmt.router)
    dp.include_router(payment.router)
    dp.include_router(history.router)
    dp.include_router(admin.router)

    # 7. Start Long Polling
    logger.info("Starting Telegram long polling for USAT Payment Bot...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        logger.info("Shutting down bot session...")
        if webapp_runner:
            await webapp_runner.cleanup()
        await bot.session.close()
        logger.info("Bot stopped safely.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot process interrupted. Exiting.")
