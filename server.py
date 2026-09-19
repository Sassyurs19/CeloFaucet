"""
Production Web Service Entry Point for Render.
Listens on 0.0.0.0, reads PORT dynamically from process environment,
initializes the database and Celo contract metadata on startup,
and serves the Celo USDT API & SPA endpoints.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from aiohttp import web

# Ensure workspace root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import config
from database import db
from celo import celo_client
from wallet import wallet_manager
from webapp.server import create_webapp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("celo_usdt_server")


async def on_startup(app: web.Application) -> None:
    """Initialize persistent database and verify Celo contract metadata on application boot."""
    logger.info("Initializing database at %s...", config.database_path)
    await db.init_db()
    logger.info("Database initialized successfully.")

    # Fresh Start: Ensure all previous test accounts, wallets, and payments are cleared
    db_dir = os.path.dirname(config.database_path) or "data"
    os.makedirs(db_dir, exist_ok=True)
    reset_marker = os.path.join(db_dir, ".fresh_start_v2_done")
    if not os.path.exists(reset_marker):
        logger.info("Fresh start requested: wiping previous accounts, wallets, and payments...")
        await db.reset_all_users_and_wallets()
        try:
            with open(reset_marker, "w") as f:
                f.write("done\n")
            logger.info("Fresh start complete: database is 100% clean and ready.")
        except Exception as e:
            logger.warning("Could not create reset marker: %s", e)

    logger.info("Verifying Celo Mainnet RPC connection (%s)...", config.celo_rpc_url)
    connected, net_msg = await celo_client.verify_network()
    if connected:
        logger.info("Celo Network: %s", net_msg)
    else:
        logger.warning("Celo Network Warning: %s", net_msg)

    logger.info("Verifying USAT contract %s...", config.usat_contract_address)
    usat_ok, usat_msg = await celo_client.init_usat_metadata()
    if usat_ok:
        logger.info("USAT Verified on-chain: %s", usat_msg)
    else:
        logger.warning("USAT metadata notice: %s", usat_msg)

    wallet_ok, wallet_msg = wallet_manager.verify_configuration()
    if wallet_ok:
        logger.info("Dedicated Funding Wallet: %s", wallet_msg)
        bal = await celo_client.get_celo_balance(wallet_manager.address)
        logger.info("Funding Wallet Balance: %.4f CELO", bal)
    else:
        logger.warning("Funding Wallet notice: %s", wallet_msg)


def main() -> None:
    """Launch the production WebApp server on Render."""
    port_env = os.getenv("PORT") or os.getenv("WEBAPP_PORT", "8080")
    try:
        port = int(str(port_env).strip())
    except ValueError:
        port = 8080

    host = "0.0.0.0"
    logger.info("Starting CELO USDT Web Service on http://%s:%d (PORT=%d)", host, port, port)

    app = create_webapp()
    app.on_startup.append(on_startup)

    web.run_app(app, host=host, port=port, access_log=logger)


if __name__ == "__main__":
    main()
