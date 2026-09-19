"""
Transaction execution and lifecycle service for Celo payouts.
Includes nonce synchronization, concurrency locks, balance checks, and DRY_RUN support.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import uuid
from typing import Optional, Tuple
from web3 import Web3

from config import config
from database import db
from wallet import wallet_manager
from celo import celo_client

logger = logging.getLogger(__name__)


class TransactionResult:
    """Represents the outcome of a payout request."""

    def __init__(
        self,
        success: bool,
        request_id: str,
        tx_hash: Optional[str] = None,
        block_number: Optional[int] = None,
        error_message: Optional[str] = None,
        is_dry_run: bool = False,
    ) -> None:
        self.success = success
        self.request_id = request_id
        self.tx_hash = tx_hash
        self.block_number = block_number
        self.error_message = error_message
        self.is_dry_run = is_dry_run


class TransactionService:
    """Coordinates address payouts with concurrency control and security safeguards."""

    def __init__(self, database: Optional[Database] = None) -> None:
        self.db = database or db
        # Mutex lock to serialize nonce retrieval and signing across concurrent coroutines
        self._send_lock = asyncio.Lock()

    async def execute_payout(
        self, telegram_id: int, destination_address: str
    ) -> TransactionResult:
        """
        Executes 0.1 CELO payout to the validated destination address.
        Prevents races, updates database states, and handles DRY_RUN simulations safely.
        """
        # 1. Anti-abuse concurrency check: is there an active PROCESSING claim for this user?
        active_claim = await self.db.get_active_claim_for_user(telegram_id)
        if active_claim:
            logger.warning(
                "User %s attempted concurrent claim while claim %s is still processing.",
                telegram_id,
                active_claim.get("request_id"),
            )
            return TransactionResult(
                success=False,
                request_id=active_claim.get("request_id", ""),
                error_message=(
                    "You already have a transaction in progress. "
                    "Please wait for it to confirm before requesting again."
                ),
            )

        # 2. Check if faucet is paused
        if await self.db.is_faucet_paused():
            return TransactionResult(
                success=False,
                request_id="",
                error_message="Faucet is temporarily paused by the administrator.",
            )

        request_id = str(uuid.uuid4())
        claim_amount = config.claim_amount

        # 3. Create initial claim record with status PROCESSING
        await self.db.create_claim(
            request_id=request_id,
            telegram_id=telegram_id,
            destination_address=destination_address,
            amount=claim_amount,
        )

        logger.info(
            "User %s initiated request %s for %s CELO to %s",
            telegram_id,
            request_id,
            claim_amount,
            wallet_manager.truncate_address(destination_address),
        )

        # 4. Handle DRY_RUN mode simulation
        if config.dry_run:
            logger.info("DRY_RUN active: simulating payout for request %s", request_id)
            # Short simulation delay to mimic network latency
            await asyncio.sleep(0.5)

            # Simulated transaction hash
            simulated_hash = f"0xsimulated_{secrets.token_hex(28)}"
            simulated_block = 12345678

            await self.db.update_claim_status(
                request_id=request_id,
                status="SUCCESS",
                tx_hash=simulated_hash,
                block_number=simulated_block,
            )

            logger.info("DRY_RUN payout succeeded for %s: %s", request_id, simulated_hash)
            return TransactionResult(
                success=True,
                request_id=request_id,
                tx_hash=simulated_hash,
                block_number=simulated_block,
                is_dry_run=True,
            )

        # 5. Live Mode Pre-flight Checks
        # Verify wallet configuration
        if not wallet_manager.is_configured:
            err = "Faucet wallet private key is not configured or invalid."
            logger.error("Request %s aborted: %s", request_id, err)
            await self.db.update_claim_status(
                request_id=request_id, status="FAILED", error_message=err
            )
            return TransactionResult(success=False, request_id=request_id, error_message=err)

        # Verify Celo node connectivity & Chain ID
        is_valid_net, net_msg = await celo_client.verify_network()
        if not is_valid_net:
            logger.error("Request %s aborted: %s", request_id, net_msg)
            await self.db.update_claim_status(
                request_id=request_id, status="FAILED", error_message=net_msg
            )
            return TransactionResult(
                success=False,
                request_id=request_id,
                error_message="Blockchain network temporarily unavailable. Please retry later.",
            )

        faucet_addr = wallet_manager.address

        # Check balance
        current_balance = await celo_client.get_balance(faucet_addr)
        required_minimum = claim_amount + config.min_gas_reserve

        if current_balance < required_minimum:
            err = (
                f"Insufficient faucet balance. Current: {current_balance:.4f} CELO, "
                f"Required: {required_minimum:.4f} CELO."
            )
            logger.warning("Request %s failed: %s", request_id, err)
            await self.db.update_claim_status(
                request_id=request_id, status="FAILED", error_message="Insufficient faucet balance"
            )
            return TransactionResult(
                success=False,
                request_id=request_id,
                error_message="The faucet wallet does not currently have enough CELO. Please try again later.",
            )

        # 6. Build, Sign, and Broadcast with Nonce Lock
        w3 = celo_client.w3
        tx_hash_hex: Optional[str] = None

        try:
            async with self._send_lock:
                # Estimate gas & gas price
                gas_price = await celo_client.get_gas_price()
                gas_limit = 21000

                nonce = await celo_client.get_nonce(faucet_addr)

                tx = {
                    "nonce": nonce,
                    "to": Web3.to_checksum_address(destination_address),
                    "value": w3.to_wei(claim_amount, "ether"),
                    "gas": gas_limit,
                    "gasPrice": gas_price,
                    "chainId": config.celo_chain_id,
                }

                logger.info(
                    "Signing and broadcasting tx for request %s (nonce=%d, gasPrice=%d wei)",
                    request_id,
                    nonce,
                    gas_price,
                )

                # Sign locally
                account = wallet_manager.account
                signed_tx = account.sign_transaction(tx)

                # Broadcast raw transaction
                raw_tx_bytes = signed_tx.raw_transaction
                tx_hash_bytes = await asyncio.to_thread(
                    w3.eth.send_raw_transaction, raw_tx_bytes
                )
                tx_hash_hex = w3.to_hex(tx_hash_bytes)
                logger.info("Transaction submitted for %s: %s", request_id, tx_hash_hex)

            # 7. Wait for receipt outside of the nonce lock so other transactions can queue
            receipt = await asyncio.to_thread(
                w3.eth.wait_for_transaction_receipt, tx_hash_hex, timeout=90
            )

            status = receipt.get("status", 0)
            block_number = receipt.get("blockNumber", 0)

            if status == 1:
                logger.info(
                    "Transaction confirmed for %s at block %d: %s",
                    request_id,
                    block_number,
                    tx_hash_hex,
                )
                await self.db.update_claim_status(
                    request_id=request_id,
                    status="SUCCESS",
                    tx_hash=tx_hash_hex,
                    block_number=block_number,
                )
                return TransactionResult(
                    success=True,
                    request_id=request_id,
                    tx_hash=tx_hash_hex,
                    block_number=block_number,
                )
            else:
                err_msg = "Transaction reverted on blockchain."
                logger.error("Transaction reverted for %s: %s", request_id, tx_hash_hex)
                await self.db.update_claim_status(
                    request_id=request_id,
                    status="FAILED",
                    tx_hash=tx_hash_hex,
                    block_number=block_number,
                    error_message=err_msg,
                )
                return TransactionResult(
                    success=False,
                    request_id=request_id,
                    tx_hash=tx_hash_hex,
                    error_message=err_msg,
                )

        except Exception as e:
            # Safe error logging without exposing secrets
            clean_error = str(e).split("\n")[0]
            logger.error("Transaction processing error for %s: %s", request_id, clean_error)
            await self.db.update_claim_status(
                request_id=request_id,
                status="FAILED",
                tx_hash=tx_hash_hex,
                error_message=clean_error[:200],
            )
            return TransactionResult(
                success=False,
                request_id=request_id,
                tx_hash=tx_hash_hex,
                error_message="Network error while processing your transaction. Please try again later.",
            )


# Global transaction service instance
transaction_service = TransactionService()
