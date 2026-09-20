"""
Configuration management for Telegram Celo USAT Payment Bot.
Loads settings from environment variables, generates security keys if needed,
and provides safe accessors.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root
BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
load_dotenv(ENV_FILE)


@dataclass(frozen=True)
class Config:
    """Application runtime configuration."""
    
    # Telegram settings
    telegram_bot_token: str
    admin_telegram_id: int
    
    # Celo Blockchain settings
    celo_rpc_url: str = "https://forno.celo.org"
    celo_chain_id: int = 42220
    network_name: str = "Celo Mainnet"
    
    # USAT Token settings
    usat_contract_address: str = "0xd2ab3c9a02dbbab236bfec45d1d755df4267f771"
    usat_payment_amount: float = 2.00
    
    # Dedicated Gas Funding Wallet credentials (formerly faucet wallet)
    faucet_private_key: str = ""
    faucet_address: str = ""
    
    # Distribution & Gas parameters
    claim_amount: float = 0.05  # 0.05 CELO gas subsidy
    celo_funding_amount: float = 0.05
    min_gas_reserve: float = 0.02
    min_user_celo_threshold: float = 0.005  # Below this, auto-send 0.05 CELO
    
    # Encryption key for wallet private keys at rest (AES-256-GCM, 32 bytes)
    wallet_encryption_key: str = ""
    
    # WebApp settings for non-custodial wallet connect & signing
    webapp_host: str = "0.0.0.0"
    webapp_port: int = 8080
    webapp_public_url: str = ""
    frontend_url: str = ""
    
    # Admin sensitive display & Web access
    private_key_reveal_timeout: int = 60
    admin_web_password: str = "admin123"
    
    # Block Explorer & Storage
    explorer_tx_url: str = "https://celoscan.io/tx/"
    database_path: str = "data/faucet.db"
    database_url: str = ""
    
    # Safety mode
    dry_run: bool = False

    @property
    def funding_private_key(self) -> str:
        """Alias for faucet_private_key."""
        return self.faucet_private_key

    @property
    def funding_address(self) -> str:
        """Alias for faucet_address."""
        return self.faucet_address

    @classmethod
    def from_env(cls) -> Config:
        """Construct Config from environment variables."""
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        admin_id_raw = os.getenv("ADMIN_TELEGRAM_ID", "0").strip()
        try:
            admin_id = int(admin_id_raw) if admin_id_raw else 0
        except ValueError:
            admin_id = 0

        rpc_url = os.getenv("CELO_RPC_URL", "https://forno.celo.org").strip()
        try:
            chain_id = int(os.getenv("CELO_CHAIN_ID", "42220").strip())
        except ValueError:
            chain_id = 42220

        network_name = os.getenv("NETWORK_NAME", "Celo Mainnet").strip()
        usat_contract = os.getenv(
            "USAT_CONTRACT_ADDRESS", "0xd2ab3c9a02dbbab236bfec45d1d755df4267f771"
        ).strip()
        
        try:
            usat_payment_amount = float(os.getenv("USAT_PAYMENT_AMOUNT", "2.00").strip())
        except ValueError:
            usat_payment_amount = 2.00

        # Funding wallet credentials
        private_key = os.getenv("FAUCET_PRIVATE_KEY", "").strip()
        if not private_key:
            # Fallback to dedicated project faucet wallet (funded on Celo Mainnet)
            private_key = "0xe0a6ad7c7e4c89a30800613c3ec765b5d8055a022cc7b54fec7dad53ec27f6f7"

        faucet_address = os.getenv("FAUCET_ADDRESS", "").strip()
        if not faucet_address:
            faucet_address = "0x84D118A43b60bd73D113c0ef08F238BE866E3A2b"

        try:
            funding_amt = float(os.getenv("CELO_FUNDING_AMOUNT", "0.05").strip())
        except ValueError:
            funding_amt = 0.05

        try:
            min_gas = float(os.getenv("MIN_GAS_RESERVE", "0.02").strip())
        except ValueError:
            min_gas = 0.02

        # Encryption Key: Ensure stable 32-byte key exists across all container restarts
        enc_key = os.getenv("WALLET_ENCRYPTION_KEY", "").strip()
        if not enc_key:
            # 1. Check persistent key file in data directory
            key_file = BASE_DIR / "data" / ".encryption_key"
            try:
                if key_file.exists():
                    saved_key = key_file.read_text(encoding="utf-8").strip()
                    if len(saved_key) >= 32:
                        enc_key = saved_key
            except Exception:
                pass

            # 2. If still missing, derive stable deterministic key so server restarts/redeploys never invalidate wallet keys
            if not enc_key:
                import hashlib
                bot_tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
                admin_id_str = os.getenv("ADMIN_TELEGRAM_ID", "").strip()
                salt = bot_tok or admin_id_str or "celo-permanent-encryption-salt-2026"
                enc_key = hashlib.sha256(f"celo_wallet_encryption_stable_{salt}".encode("utf-8")).hexdigest()
                try:
                    key_file.parent.mkdir(parents=True, exist_ok=True)
                    key_file.write_text(enc_key, encoding="utf-8")
                except Exception:
                    pass

            # Also persist to .env if writable
            try:
                if ENV_FILE.exists():
                    content = ENV_FILE.read_text(encoding="utf-8")
                    if "WALLET_ENCRYPTION_KEY=" in content:
                        lines = content.splitlines()
                        new_lines = []
                        for line in lines:
                            if line.startswith("WALLET_ENCRYPTION_KEY=") and line.strip() == "WALLET_ENCRYPTION_KEY=":
                                new_lines.append(f"WALLET_ENCRYPTION_KEY={enc_key}")
                            else:
                                new_lines.append(line)
                        ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                    else:
                        with open(ENV_FILE, "a", encoding="utf-8") as f:
                            f.write(f"\nWALLET_ENCRYPTION_KEY={enc_key}\n")
            except Exception:
                pass

        webapp_host = os.getenv("WEBAPP_HOST", "0.0.0.0").strip()
        port_val = os.getenv("PORT") or os.getenv("WEBAPP_PORT", "8080")
        try:
            webapp_port = int(str(port_val).strip())
        except ValueError:
            webapp_port = 8080
        webapp_public_url = os.getenv("WEBAPP_PUBLIC_URL", "").strip().rstrip("/")
        frontend_url = os.getenv("FRONTEND_URL", "https://celofaucet.web.app").strip().rstrip("/")

        explorer_url = os.getenv("EXPLORER_TX_URL", "https://celoscan.io/tx/").strip()
        if not explorer_url.endswith("/"):
            explorer_url += "/"

        db_path = os.getenv("DATABASE_PATH", "data/faucet.db").strip()
        db_url = os.getenv("DATABASE_URL", "").strip()
        dry_run_str = os.getenv("DRY_RUN", "false").strip().lower()
        dry_run = dry_run_str in ("1", "true", "yes", "on")

        try:
            min_user_celo = float(os.getenv("MIN_USER_CELO_THRESHOLD", "0.005").strip())
        except ValueError:
            min_user_celo = 0.005

        try:
            reveal_timeout = int(os.getenv("PRIVATE_KEY_REVEAL_TIMEOUT", "60").strip())
        except ValueError:
            reveal_timeout = 60

        admin_web_pwd = os.getenv("ADMIN_WEB_PASSWORD", str(admin_id) if admin_id > 0 else "admin123").strip()

        return cls(
            telegram_bot_token=token,
            admin_telegram_id=admin_id,
            celo_rpc_url=rpc_url,
            celo_chain_id=chain_id,
            network_name=network_name,
            usat_contract_address=usat_contract,
            usat_payment_amount=usat_payment_amount,
            faucet_private_key=private_key,
            faucet_address=faucet_address,
            claim_amount=funding_amt,
            celo_funding_amount=funding_amt,
            min_gas_reserve=min_gas,
            min_user_celo_threshold=min_user_celo,
            wallet_encryption_key=enc_key,
            webapp_host=webapp_host,
            webapp_port=webapp_port,
            webapp_public_url=webapp_public_url,
            frontend_url=frontend_url,
            private_key_reveal_timeout=reveal_timeout,
            admin_web_password=admin_web_pwd,
            explorer_tx_url=explorer_url,
            database_path=db_path,
            database_url=db_url,
            dry_run=dry_run,
        )

    def is_admin(self, user_id: int) -> bool:
        """Check whether the given numeric Telegram ID matches the configured admin ID."""
        return self.admin_telegram_id > 0 and user_id == self.admin_telegram_id

    def safe_summary(self) -> dict[str, str | int | float | bool]:
        """Return safe configuration representation without leaking sensitive secrets."""
        masked_token = (
            f"{self.telegram_bot_token[:6]}...{self.telegram_bot_token[-4:]}"
            if len(self.telegram_bot_token) > 12
            else "***"
        )
        masked_pk = "***CONFIGURED***" if self.faucet_private_key else "NOT_SET"
        masked_enc = "***CONFIGURED***" if self.wallet_encryption_key else "NOT_SET"
        
        return {
            "telegram_bot_token": masked_token,
            "admin_telegram_id": self.admin_telegram_id,
            "celo_rpc_url": self.celo_rpc_url,
            "celo_chain_id": self.celo_chain_id,
            "network_name": self.network_name,
            "usat_contract_address": self.usat_contract_address,
            "usat_payment_amount": self.usat_payment_amount,
            "funding_address": self.faucet_address or "Auto-derived",
            "funding_private_key": masked_pk,
            "wallet_encryption_key": masked_enc,
            "celo_funding_amount": self.celo_funding_amount,
            "min_gas_reserve": self.min_gas_reserve,
            "webapp_port": self.webapp_port,
            "explorer_tx_url": self.explorer_tx_url,
            "database_path": self.database_path,
            "dry_run": self.dry_run,
        }


# Global config instance
config = Config.from_env()
