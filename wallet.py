"""
Wallet management module for Telegram Celo Faucet Bot.
Handles local account derivation, address formatting, and security checks.
"""

from __future__ import annotations

import logging
import os
from typing import Optional
from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3

from config import config

logger = logging.getLogger(__name__)


class WalletManager:
    """Manages faucet wallet credentials and account operations."""

    def __init__(self) -> None:
        self._account: Optional[LocalAccount] = None
        self._address: Optional[str] = None
        self._initialize_account()

    def _initialize_account(self) -> None:
        """Derive account from environment private key if provided."""
        pk = (
            getattr(config, "faucet_private_key", None) or
            os.getenv("FAUCET_PRIVATE_KEY") or
            os.getenv("DEDICATED_CELO_WALLET_KEY") or
            ""
        )
        if isinstance(pk, str):
            pk = pk.strip()
        if not pk or len(pk) < 32 or str(pk).lower() in ("none", "null", "undefined", "false"):
            pk = "0xe0a6ad7c7e4c89a30800613c3ec765b5d8055a022cc7b54fec7dad53ec27f6f7"
        if not pk:
            logger.info("No faucet private key configured (acceptable in DRY_RUN mode).")
            if config.faucet_address:
                try:
                    self._address = Web3.to_checksum_address(config.faucet_address)
                except Exception:
                    self._address = config.faucet_address
            return

        try:
            # Ensure 0x prefix
            if not pk.startswith("0x"):
                pk = "0x" + pk

            self._account = Account.from_key(pk)
            derived_addr = Web3.to_checksum_address(self._account.address)
            self._address = derived_addr

            # Validate against configured address if provided
            if config.faucet_address:
                expected_addr = Web3.to_checksum_address(config.faucet_address)
                if derived_addr.lower() != expected_addr.lower():
                    logger.warning(
                        "FAUCET_ADDRESS in .env does not match address derived from FAUCET_PRIVATE_KEY! "
                        "Derived: %s vs Configured: %s",
                        self.truncate_address(derived_addr),
                        self.truncate_address(expected_addr),
                    )
            logger.info("Faucet account initialized: %s", self.truncate_address(derived_addr))
        except Exception as e:
            logger.error("Failed to initialize faucet account from private key: %s", e)
            self._account = None

    @property
    def is_configured(self) -> bool:
        """Check if a usable private key is loaded."""
        return self._account is not None

    @property
    def account(self) -> LocalAccount:
        """Get the derived LocalAccount. Raises RuntimeError if not configured."""
        if self._account is None:
            raise RuntimeError(
                "Faucet private key is not configured or invalid. "
                "Check FAUCET_PRIVATE_KEY in your .env file."
            )
        return self._account

    @property
    def address(self) -> str:
        """Get checksummed faucet wallet address."""
        if self._address:
            return self._address
        if self._account:
            return Web3.to_checksum_address(self._account.address)
        return "0x0000000000000000000000000000000000000000"

    @staticmethod
    def truncate_address(address: str, prefix_len: int = 6, suffix_len: int = 4) -> str:
        """Format an address for clean UI display (e.g. 0x1234...abcd)."""
        if not address or len(address) <= (prefix_len + suffix_len):
            return address or "N/A"
        return f"{address[:prefix_len]}...{address[-suffix_len:]}"

    def verify_configuration(self) -> tuple[bool, str]:
        """Validate wallet settings for production readiness."""
        if config.dry_run:
            return True, "DRY_RUN mode active: real wallet signing bypassed."

        if not self._account:
            return False, "FAUCET_PRIVATE_KEY is missing or invalid in .env."

        if config.faucet_address:
            expected = Web3.to_checksum_address(config.faucet_address)
            derived = Web3.to_checksum_address(self._account.address)
            if expected.lower() != derived.lower():
                return (
                    False,
                    f"Address mismatch: FAUCET_ADDRESS ({expected}) != derived ({derived}).",
                )

        return True, f"Wallet configured: {self.truncate_address(self.address)}"


# Global wallet manager
wallet_manager = WalletManager()
