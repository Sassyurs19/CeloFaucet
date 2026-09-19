"""
Automated unit & integration tests for Telegram Celo USAT Payment Bot.
Covers AES-256-GCM encryption, database dual-wallet isolation, integer base unit calculations,
admin controls, and payment lifecycle tracking.
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
from services.encryption import EncryptionService
from database import Database
from celo import CeloClient


class TestEncryptionService(unittest.TestCase):
    """Test authenticated AES-256-GCM encryption."""

    def setUp(self):
        # Deterministic 32-byte hex key for test
        self.test_key = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        self.enc_service = EncryptionService(master_key_hex=self.test_key)

    def test_encrypt_decrypt_roundtrip(self):
        sample_pk = "0x4f3edf983ac636a65a842ce7c78d9aa706d3b113bce9c46f30d7d21715b23b1d"
        encrypted = self.enc_service.encrypt(sample_pk)
        
        self.assertIn(":", encrypted)
        self.assertNotIn(sample_pk, encrypted)

        decrypted = self.enc_service.decrypt(encrypted)
        self.assertEqual(decrypted, sample_pk)

    def test_unique_nonces(self):
        sample_pk = "0x4f3edf983ac636a65a842ce7c78d9aa706d3b113bce9c46f30d7d21715b23b1d"
        enc1 = self.enc_service.encrypt(sample_pk)
        enc2 = self.enc_service.encrypt(sample_pk)
        self.assertNotEqual(enc1, enc2, "Nonces must be unique per encryption")

    def test_tampered_payload_fails(self):
        sample_pk = "0x4f3edf983ac636a65a842ce7c78d9aa706d3b113bce9c46f30d7d21715b23b1d"
        encrypted = self.enc_service.encrypt(sample_pk)
        parts = encrypted.split(":")
        
        # Tamper ciphertext by flipping a character
        tampered_cipher = parts[1][:-2] + ("aa" if parts[1][-2:] != "aa" else "bb")
        tampered = f"{parts[0]}:{tampered_cipher}"

        with self.assertRaises(ValueError):
            self.enc_service.decrypt(tampered)


class TestDatabaseDualWalletAndPayments(unittest.IsolatedAsyncioTestCase):
    """Test async database schema, user onboarding, dual wallets, and USAT payments."""

    async def asyncSetUp(self):
        self.test_db_path = f"data/test_usat_{uuid.uuid4().hex}.db"
        self.db = Database(db_path=self.test_db_path)
        await self.db.init_db()

    async def asyncTearDown(self):
        await asyncio.sleep(0.1)
        for ext in ("", "-wal", "-shm"):
            p = self.test_db_path + ext
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

    async def test_user_onboarding_and_registration(self):
        tg_id = 888001
        # 1. Unregistered user
        self.assertFalse(await self.db.is_user_registered(tg_id))

        # 2. Register name and mobile
        await self.db.get_or_create_user(tg_id, username="alice_celo", first_name="Alice")
        await self.db.update_user_name(tg_id, "Alice Smith")
        await self.db.update_user_mobile(tg_id, "+919876543210")

        self.assertTrue(await self.db.is_user_registered(tg_id))
        profile = await self.db.get_user_profile(tg_id)
        self.assertEqual(profile["full_name"], "Alice Smith")
        self.assertEqual(profile["mobile_number"], "+919876543210")

    async def test_dual_wallet_isolation(self):
        user_a = 888001
        user_b = 888002

        # User A adds Connected wallet (Option A)
        w1_id = await self.db.add_user_wallet(
            telegram_id=user_a,
            wallet_name="Main Connected",
            address="0x471EcE3750Da237f93B8E339c536989b8978a438",
            wallet_type="connected",
            encrypted_private_key=None,
        )
        self.assertGreater(w1_id, 0)

        # User A adds Imported wallet (Option B)
        w2_id = await self.db.add_user_wallet(
            telegram_id=user_a,
            wallet_name="Second Imported",
            address="0x633630449557458145C2b240409B1d7153d4B6E5",
            wallet_type="imported",
            encrypted_private_key="test_nonce:test_cipher",
        )
        self.assertGreater(w2_id, 0)

        # User B adds a wallet
        w3_id = await self.db.add_user_wallet(
            telegram_id=user_b,
            wallet_name="Bob Wallet",
            address="0x84D118A43b60bd73D113c0ef08F238BE866E3A2b",
            wallet_type="imported",
            encrypted_private_key="test_nonce_b:test_cipher_b",
        )

        # Verify User A only sees their 2 wallets
        wallets_a = await self.db.get_user_wallets(user_a)
        self.assertEqual(len(wallets_a), 2)
        self.assertEqual(wallets_a[0]["wallet_type"], "connected")
        self.assertIsNone(wallets_a[0]["encrypted_private_key"])
        self.assertEqual(wallets_a[1]["wallet_type"], "imported")
        self.assertIsNotNone(wallets_a[1]["encrypted_private_key"])

        # User A cannot access User B's wallet
        wallet_b_attempt = await self.db.get_user_wallet_by_id(w3_id, user_a)
        self.assertIsNone(wallet_b_attempt)

        # Admin can view all wallets
        admin_view = await self.db.get_wallet_by_id_admin(w2_id)
        self.assertIsNotNone(admin_view)
        self.assertEqual(admin_view["wallet_name"], "Second Imported")

    async def test_receiving_wallets_crud(self):
        # Default seeding should have 3 wallets
        initial = await self.db.get_receiving_wallets()
        self.assertEqual(len(initial), 3)

        # Admin adds receiving wallet
        new_id = await self.db.add_receiving_wallet("Fourth Wallet", "0x5678567856785678567856785678567856785678")
        self.assertGreater(new_id, 0)

        # Toggle inactive
        await self.db.toggle_receiving_wallet(new_id, False)
        active_only = await self.db.get_receiving_wallets(only_active=True)
        self.assertEqual(len(active_only), 3)

        # Toggle active
        await self.db.toggle_receiving_wallet(new_id, True)
        active_after = await self.db.get_receiving_wallets(only_active=True)
        self.assertEqual(len(active_after), 4)

        # Delete
        await self.db.delete_receiving_wallet(new_id)
        self.assertEqual(len(await self.db.get_receiving_wallets()), 3)

    async def test_usat_payment_lifecycle_and_totals(self):
        tg_id = 888001
        pid = "payment-uuid-101"

        # Create payment
        row_id = await self.db.create_usat_payment(
            payment_id=pid,
            telegram_id=tg_id,
            wallet_type="imported",
            from_address="0x471EcE3750Da237f93B8E339c536989b8978a438",
            to_address="0x633630449557458145C2b240409B1d7153d4B6E5",
            receiving_wallet_name="First Wallet",
            amount_usat="2.00",
            amount_base_units=2000000,
        )
        self.assertGreater(row_id, 0)

        # Check active payment detection
        active = await self.db.get_active_usat_payment(tg_id)
        self.assertIsNotNone(active)
        self.assertEqual(active["payment_id"], pid)

        # Update with successful transaction and 0.05 CELO gas subsidy
        await self.db.update_usat_payment_status(
            payment_id=pid,
            status="SUCCESS",
            tx_hash="0xusat_tx_hash_12345",
            block_number=12345,
            celo_funded=1,
            celo_fund_tx_hash="0xcelo_gas_hash_67890",
        )

        # Active should be cleared
        self.assertIsNone(await self.db.get_active_usat_payment(tg_id))

        # Check user totals & history
        total_paid = await self.db.get_user_total_paid(tg_id)
        self.assertEqual(total_paid, 2.00)

        payments = await self.db.get_user_payments(tg_id, limit=5)
        self.assertEqual(len(payments), 1)
        self.assertEqual(payments[0]["status"], "SUCCESS")
        self.assertEqual(payments[0]["celo_funded"], 1)

        # Check statistics
        stats = await self.db.get_usat_statistics()
        self.assertEqual(stats["total_payments"], 1)
        self.assertEqual(stats["successful"], 1)
        self.assertEqual(stats["total_usat_sent"], 2.00)
        self.assertEqual(stats["celo_subsidies_count"], 1)


class TestUSATTokenCalculations(unittest.TestCase):
    """Test integer base unit enforcement and token decimals."""

    def test_integer_base_units(self):
        client = CeloClient(expected_chain_id=42220)
        client.usat_decimals = 6

        # $2.00 USAT must be exactly 2,000,000 base units
        base_units = client.get_payment_amount_base_units()
        self.assertEqual(base_units, 2000000)
        self.assertIsInstance(base_units, int)

        # Formatted string verification
        formatted = client.format_usat(base_units)
        self.assertEqual(formatted, "2.000000")

        # Arbitrary units format
        self.assertEqual(client.format_usat(15500000), "15.500000")


if __name__ == "__main__":
    unittest.main()
