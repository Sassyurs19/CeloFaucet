"""
Celo blockchain interface module for Celo Mainnet and USAT ERC-20 token interactions.
Wraps Web3.py RPC calls with async execution to prevent blocking the aiogram event loop.
Verifies chain ID (42220), enforces integer base unit token calculations, and handles
automatic 0.05 CELO gas funding from the dedicated funding wallet.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Optional, Tuple
from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3
from web3.exceptions import Web3Exception

from config import config
from wallet import wallet_manager

logger = logging.getLogger(__name__)

# Standard ERC-20 ABI for USAT
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "name",
        "outputs": [{"name": "", "type": "string"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_value", "type": "uint256"},
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "payable": False,
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


class CeloClient:
    """Async wrapper for Web3 Celo Mainnet and USAT ERC-20 interactions."""

    def __init__(
        self,
        rpc_url: str | None = None,
        expected_chain_id: int | None = None,
        usat_contract_address: str | None = None,
    ) -> None:
        self.rpc_url = rpc_url or config.celo_rpc_url
        self.expected_chain_id = expected_chain_id or config.celo_chain_id
        self.usat_address_str = usat_contract_address or config.usat_contract_address
        self._w3 = Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": 15}))
        
        # Lock to serialize nonces when sending funding transactions
        self._funding_lock = asyncio.Lock()

        # USAT Token metadata (dynamically queried on startup)
        self.usat_decimals: int = 6
        self.usat_symbol: str = "USAT"
        self.usat_name: str = "Tether America USD"
        self._usat_contract = None
        self._init_contract_instance()

    def _init_contract_instance(self) -> None:
        """Instantiate the USAT contract wrapper."""
        try:
            checksum_addr = Web3.to_checksum_address(self.usat_address_str)
            self._usat_contract = self._w3.eth.contract(
                address=checksum_addr, abi=ERC20_ABI
            )
        except Exception as e:
            logger.error("Failed to initialize USAT contract instance: %s", e)

    @property
    def w3(self) -> Web3:
        """Underlying synchronous Web3 instance."""
        return self._w3

    async def is_connected(self) -> bool:
        """Check if node responds to RPC ping."""
        try:
            return await asyncio.to_thread(self._w3.is_connected)
        except Exception as e:
            logger.error("RPC connection check failed: %s", e)
            return False

    async def get_chain_id(self) -> int:
        """Retrieve current chain ID from connected RPC."""
        return await asyncio.to_thread(lambda: self._w3.eth.chain_id)

    async def verify_network(self) -> tuple[bool, str]:
        """
        Verify RPC connectivity and assert connected chain ID matches Celo Mainnet (42220).
        """
        connected = await self.is_connected()
        if not connected:
            return False, f"Cannot connect to Celo RPC at {self.rpc_url}"

        try:
            actual_chain_id = await self.get_chain_id()
            if actual_chain_id != self.expected_chain_id:
                return (
                    False,
                    f"Chain ID mismatch! Expected {self.expected_chain_id} (Celo Mainnet), "
                    f"but connected node reported {actual_chain_id}.",
                )
            return True, f"Connected to Celo Mainnet (Chain ID: {actual_chain_id})"
        except Exception as e:
            return False, f"Failed to query chain ID: {e}"

    async def init_usat_metadata(self) -> tuple[bool, str]:
        """
        Query the USAT contract directly from Celo Mainnet to verify decimals, symbol, and name.
        Does not assume metadata.
        """
        if not self._usat_contract:
            self._init_contract_instance()
        if not self._usat_contract:
            return False, "USAT contract not initialized."

        try:
            decimals = await asyncio.to_thread(self._usat_contract.functions.decimals().call)
            symbol = await asyncio.to_thread(self._usat_contract.functions.symbol().call)
            name = await asyncio.to_thread(self._usat_contract.functions.name().call)

            self.usat_decimals = int(decimals)
            self.usat_symbol = str(symbol)
            self.usat_name = str(name)
            
            logger.info(
                "USAT Token Verified: %s (%s), Decimals: %d, Contract: %s",
                self.usat_name,
                self.usat_symbol,
                self.usat_decimals,
                self.usat_address_str,
            )
            return True, f"{self.usat_symbol} ({self.usat_decimals} decimals)"
        except Exception as e:
            logger.warning("Could not query USAT contract metadata (using defaults): %s", e)
            return False, str(e)

    def get_payment_amount_base_units(self) -> int:
        """
        Compute exact integer base units for the $2.00 USAT payment.
        Never uses floating-point arithmetic on token quantities.
        Example: 2 * (10 ** 6) = 2,000,000
        """
        # Exactly 2 USAT
        return 2 * (10 ** self.usat_decimals)

    def format_usat(self, base_units: int) -> str:
        """Format base units into standardized decimal string (e.g. 15.000000)."""
        factor = 10 ** self.usat_decimals
        integer_part = base_units // factor
        fractional_part = base_units % factor
        return f"{integer_part}.{fractional_part:0{self.usat_decimals}d}"

    # --- Balance Queries ---

    async def get_celo_balance(self, address: str) -> float:
        """Retrieve CELO native gas token balance for an address in standard unit (CELO)."""
        try:
            checksum_addr = Web3.to_checksum_address(address)
            balance_wei = await asyncio.to_thread(self._w3.eth.get_balance, checksum_addr)
            return float(self._w3.from_wei(balance_wei, "ether"))
        except Exception as e:
            logger.error("Error querying CELO balance for %s: %s", address, e)
            return 0.0

    async def get_usat_balance(self, address: str) -> tuple[int, str]:
        """
        Retrieve USAT ERC-20 balance for an address.
        Returns:
            (base_units: int, formatted_str: str)
        """
        try:
            checksum_addr = Web3.to_checksum_address(address)
            if not self._usat_contract:
                self._init_contract_instance()
            balance_raw = await asyncio.to_thread(
                self._usat_contract.functions.balanceOf(checksum_addr).call
            )
            base_units = int(balance_raw)
            return base_units, self.format_usat(base_units)
        except Exception as e:
            logger.error("Error querying USAT balance for %s: %s", address, e)
            return 0, f"0.{'0' * self.usat_decimals}"

    async def get_gas_price(self) -> int:
        """Fetch current gas price in Wei."""
        try:
            return await asyncio.to_thread(lambda: self._w3.eth.gas_price)
        except Exception as e:
            logger.warning("Could not get dynamic gas price, fallback to 5 Gwei: %s", e)
            return self._w3.to_wei(5, "gwei")

    # --- Automatic 0.05 CELO Gas Funding ---

    async def send_celo_funding(
        self, to_address: str, amount_celo: float = 0.05
    ) -> tuple[bool, str, str]:
        """
        Transfer 0.05 CELO from the dedicated funding wallet to user's wallet address.
        Waits for on-chain receipt before returning.
        
        Returns:
            (success: bool, tx_hash: str, error_message: str)
        """
        to_chk = Web3.to_checksum_address(to_address)
        
        if config.dry_run:
            await asyncio.sleep(0.5)
            simulated_hash = f"0xsimulated_gas_{secrets.token_hex(28)}"
            logger.info("DRY_RUN: Simulated 0.05 CELO gas funding to %s: %s", to_chk, simulated_hash)
            return True, simulated_hash, ""

        if not wallet_manager.is_configured:
            return False, "", "Funding wallet private key is not configured."

        funding_addr = wallet_manager.address
        funding_balance = await self.get_celo_balance(funding_addr)
        if funding_balance < (amount_celo + config.min_gas_reserve):
            err = (
                f"Funding wallet balance ({funding_balance:.4f} CELO) is insufficient "
                f"to send {amount_celo} CELO + gas reserve."
            )
            logger.error(err)
            return False, "", err

        try:
            async with self._funding_lock:
                gas_price = await self.get_gas_price()
                gas_limit = 25000  # Standard transfer
                nonce = await asyncio.to_thread(
                    self._w3.eth.get_transaction_count, funding_addr, "pending"
                )

                tx = {
                    "nonce": nonce,
                    "to": to_chk,
                    "value": self._w3.to_wei(amount_celo, "ether"),
                    "gas": gas_limit,
                    "gasPrice": gas_price,
                    "chainId": config.celo_chain_id,
                }

                signed = wallet_manager.account.sign_transaction(tx)
                tx_hash_bytes = await asyncio.to_thread(
                    self._w3.eth.send_raw_transaction, signed.raw_transaction
                )
                tx_hash = self._w3.to_hex(tx_hash_bytes)
                logger.info("Broadcast 0.05 CELO funding to %s: %s (nonce=%d)", to_chk, tx_hash, nonce)

            # Wait for receipt
            receipt = await asyncio.to_thread(
                self._w3.eth.wait_for_transaction_receipt, tx_hash, timeout=60
            )
            if receipt.get("status") == 1:
                logger.info("0.05 CELO funding confirmed for %s: %s", to_chk, tx_hash)
                return True, tx_hash, ""
            else:
                return False, tx_hash, "Funding transaction reverted on blockchain."
        except Exception as e:
            clean_err = str(e).split("\n")[0]
            logger.error("Error sending 0.05 CELO funding to %s: %s", to_chk, clean_err)
            return False, "", clean_err

    # --- USAT Token Transfers (Imported Wallets) ---

    async def transfer_usat_imported(
        self,
        private_key: str,
        to_address: str,
        base_units: int,
    ) -> tuple[bool, str, int, str]:
        """
        Execute USAT transfer from an imported wallet.
        Decrypted private key is held in memory only during signing.
        
        Returns:
            (success: bool, tx_hash: str, block_number: int, error_message: str)
        """
        to_chk = Web3.to_checksum_address(to_address)
        
        if config.dry_run:
            await asyncio.sleep(0.5)
            sim_hash = f"0xsimulated_usat_{secrets.token_hex(28)}"
            logger.info("DRY_RUN: Simulated USAT transfer of %d units to %s: %s", base_units, to_chk, sim_hash)
            return True, sim_hash, 12345678, ""

        account: Optional[LocalAccount] = None
        try:
            clean_key = private_key.strip()
            if not clean_key.startswith("0x"):
                clean_key = "0x" + clean_key
            account = Account.from_key(clean_key)
            from_addr = account.address

            if not self._usat_contract:
                self._init_contract_instance()

            # Build contract function transaction
            gas_price = await self.get_gas_price()
            nonce = await asyncio.to_thread(
                self._w3.eth.get_transaction_count, from_addr, "pending"
            )

            # Build transaction dictionary
            transfer_fn = self._usat_contract.functions.transfer(to_chk, base_units)
            tx_data = transfer_fn.build_transaction({
                "from": from_addr,
                "nonce": nonce,
                "gasPrice": gas_price,
                "chainId": config.celo_chain_id,
            })
            
            # Estimate gas or fallback to 75,000 for standard ERC-20 transfer
            try:
                est_gas = await asyncio.to_thread(self._w3.eth.estimate_gas, tx_data)
                tx_data["gas"] = int(est_gas * 1.2)  # 20% safety margin
            except Exception:
                tx_data["gas"] = 80000

            # Sign with user's private key
            signed_tx = account.sign_transaction(tx_data)
            
            # Wipe local account variable
            account = None

            # Broadcast with retry for load-balanced RPC node propagation
            tx_hash_bytes = None
            last_err = None
            for attempt in range(3):
                try:
                    tx_hash_bytes = await asyncio.to_thread(
                        self._w3.eth.send_raw_transaction, signed_tx.raw_transaction
                    )
                    break
                except Exception as b_err:
                    last_err = b_err
                    err_str = str(b_err).lower()
                    if "insufficient funds" in err_str and attempt < 2:
                        logger.warning(
                            "Attempt %d: RPC cluster reported insufficient funds for %s. Waiting 2.5s for state propagation...",
                            attempt + 1,
                            from_addr,
                        )
                        await asyncio.sleep(2.5)
                        continue
                    raise b_err

            if not tx_hash_bytes:
                raise last_err or Exception("Failed to broadcast transaction")

            tx_hash = self._w3.to_hex(tx_hash_bytes)
            logger.info("USAT transfer broadcast from %s to %s: %s", from_addr, to_chk, tx_hash)

            # Wait for receipt
            receipt = await asyncio.to_thread(
                self._w3.eth.wait_for_transaction_receipt, tx_hash, timeout=90
            )
            status = receipt.get("status", 0)
            block_num = receipt.get("blockNumber", 0)

            if status == 1:
                logger.info("USAT transfer confirmed at block %d: %s", block_num, tx_hash)
                return True, tx_hash, block_num, ""
            else:
                return False, tx_hash, block_num, "USAT transfer reverted on blockchain."

        except Exception as e:
            clean_err = str(e).split("\n")[0]
            logger.error("USAT transfer error: %s", clean_err)
            return False, "", 0, clean_err
        finally:
            account = None

    # --- Helper for Connected Wallets (WebApp Signing) ---

    def build_usat_transfer_tx_params(
        self, from_address: str, to_address: str, base_units: int
    ) -> dict[str, Any]:
        """
        Construct transaction parameters for non-custodial wallet signing via WebApp/WalletConnect.
        """
        to_chk = Web3.to_checksum_address(to_address)
        contract_chk = Web3.to_checksum_address(self.usat_address_str)
        
        if not self._usat_contract:
            self._init_contract_instance()

        transfer_fn = self._usat_contract.functions.transfer(to_chk, base_units)
        calldata = transfer_fn._encode_transaction_data()

        return {
            "from": Web3.to_checksum_address(from_address),
            "to": contract_chk,
            "data": calldata,
            "value": "0x0",
            "chainId": hex(config.celo_chain_id),
        }

    async def wait_for_tx_receipt(self, tx_hash: str, timeout: int = 90) -> tuple[bool, int, str]:
        """Verify and poll confirmation of a user-submitted transaction hash."""
        if config.dry_run or tx_hash.startswith("0xsimulated_"):
            return True, 12345678, ""

        try:
            receipt = await asyncio.to_thread(
                self._w3.eth.wait_for_transaction_receipt, tx_hash, timeout=timeout
            )
            status = receipt.get("status", 0)
            block_num = receipt.get("blockNumber", 0)
            if status == 1:
                return True, block_num, ""
            return False, block_num, "Transaction reverted on blockchain."
        except Exception as e:
            return False, 0, f"Confirmation timeout or error: {e}"


# Global Celo client instance
celo_client = CeloClient()
