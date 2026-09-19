"""
Automated unit & integration verification tests for Telegram Celo Faucet Bot.
Tests address validation, database operations, Celo RPC connectivity, and transaction service simulation.
"""

import asyncio
import os
import sys
import unittest
import uuid
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config
from services.validation import validate_celo_address
from database import Database
from wallet import WalletManager
from celo import CeloClient
from services.transaction_service import TransactionService


class TestAddressValidation(unittest.TestCase):
    """Test strict EVM / Celo address validation rules."""

    def test_valid_addresses(self):
        # Valid lower-case address
        valid_addr = "0x471ece3750da237f93b8e339c536989b8978a438"
        ok, checksum, err = validate_celo_address(valid_addr)
        self.assertTrue(ok)
        self.assertEqual(checksum, "0x471EcE3750Da237f93B8E339c536989b8978a438")
        self.assertIsNone(err)

        # Valid checksummed address
        ok, checksum, err = validate_celo_address("0x471EcE3750Da237f93B8E339c536989b8978a438")
        self.assertTrue(ok)
        self.assertEqual(checksum, "0x471EcE3750Da237f93B8E339c536989b8978a438")

    def test_invalid_prefix(self):
        ok, _, err = validate_celo_address("471ece3750da237f93b8e339c536989b8978a438")
        self.assertFalse(ok)
        self.assertIn("must start with '0x'", err)

    def test_invalid_length(self):
        ok, _, err = validate_celo_address("0x12345")
        self.assertFalse(ok)
        self.assertIn("length", err)

    def test_non_hex_characters(self):
        ok, _, err = validate_celo_address("0xGG1ece3750da237f93b8e339c536989b8978a438")
        self.assertFalse(ok)
        self.assertIn("non-hexadecimal", err)

    def test_usernames_and_urls(self):
        ok, _, err = validate_celo_address("@thanuj")
        self.assertFalse(ok)
        self.assertIn("usernames", err)

        ok, _, err = validate_celo_address("https://celoscan.io/address/0x123")
        self.assertFalse(ok)
        self.assertIn("URLs", err)

        ok, _, err = validate_celo_address("mywallet.celo")
        self.assertFalse(ok)
        self.assertIn("ENS", err)


class TestDatabaseAndTransactions(unittest.IsolatedAsyncioTestCase):
    """Test async database state machine, anti-race locking, and stats."""

    async def asyncSetUp(self):
        self.test_db_path = f"data/test_{uuid.uuid4().hex}.db"
        self.db = Database(db_path=self.test_db_path)
        await self.db.init_db()

    async def asyncTearDown(self):
        # Allow Windows file handles to release
        await asyncio.sleep(0.1)
        for ext in ("", "-wal", "-shm"):
            p = self.test_db_path + ext
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

    async def test_user_creation_and_activity(self):
        user = await self.db.get_or_create_user(
            telegram_id=999001,
            username="testuser",
            first_name="Alice",
        )
        self.assertEqual(user["telegram_id"], 999001)
        self.assertEqual(user["username"], "testuser")

        count = await self.db.get_user_count()
        self.assertEqual(count, 1)

        # Idempotent call
        user_again = await self.db.get_or_create_user(
            telegram_id=999001,
            username="testuser_updated",
            first_name="Alice B",
        )
        self.assertEqual(user_again["username"], "testuser_updated")
        self.assertEqual(await self.db.get_user_count(), 1)

    async def test_claim_lifecycle(self):
        req_id = "test-uuid-001"
        claim_id = await self.db.create_claim(
            request_id=req_id,
            telegram_id=999001,
            destination_address="0x471EcE3750Da237f93B8E339c536989b8978a438",
            amount=0.1,
        )
        self.assertGreater(claim_id, 0)

        # Check active claim detection (prevents double claims)
        active = await self.db.get_active_claim_for_user(999001)
        self.assertIsNotNone(active)
        self.assertEqual(active["request_id"], req_id)

        # Update claim to SUCCESS
        await self.db.update_claim_status(
            request_id=req_id,
            status="SUCCESS",
            tx_hash="0xabcdef1234567890",
            block_number=123456,
        )

        # Active claim should now be None
        active_after = await self.db.get_active_claim_for_user(999001)
        self.assertIsNone(active_after)

        # Check user history
        claims = await self.db.get_user_claims(999001, limit=5, offset=0)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["status"], "SUCCESS")
        self.assertEqual(claims[0]["tx_hash"], "0xabcdef1234567890")

    async def test_settings_pause_resume(self):
        # Default should not be paused
        self.assertFalse(await self.db.is_faucet_paused())

        # Pause
        await self.db.set_faucet_paused(True)
        self.assertTrue(await self.db.is_faucet_paused())

        # Resume
        await self.db.set_faucet_paused(False)
        self.assertFalse(await self.db.is_faucet_paused())

    async def test_dry_run_transaction_service(self):
        from unittest.mock import patch
        from dataclasses import replace
        from config import config
        mock_cfg = replace(config, dry_run=True)
        with patch("services.transaction_service.config", mock_cfg):
            tx_service = TransactionService(database=self.db)
            result = await tx_service.execute_payout(
                telegram_id=999002,
                destination_address="0x1234567890123456789012345678901234567890",
            )
            self.assertTrue(result.success)
            self.assertTrue(result.is_dry_run)
            self.assertTrue(result.tx_hash.startswith("0xsimulated_"))

            # Verify claim in database
            claims = await self.db.get_user_claims(999002, limit=1)
            self.assertEqual(len(claims), 1)
            self.assertEqual(claims[0]["status"], "SUCCESS")

            # Verify stats updated
            stats = await self.db.get_statistics()
            self.assertGreaterEqual(stats["total_requests"], 1)
            self.assertGreaterEqual(stats["successful"], 1)



class TestCeloNetwork(unittest.IsolatedAsyncioTestCase):
    """Test live RPC query to Celo Mainnet for chain ID verification."""

    async def test_celo_mainnet_connectivity(self):
        client = CeloClient(rpc_url="https://forno.celo.org", expected_chain_id=42220)
        connected = await client.is_connected()
        self.assertTrue(connected, "Failed to ping https://forno.celo.org")

        ok, msg = await client.verify_network()
        self.assertTrue(ok, f"Network verification failed: {msg}")
        self.assertIn("42220", msg)


if __name__ == "__main__":
    unittest.main()
