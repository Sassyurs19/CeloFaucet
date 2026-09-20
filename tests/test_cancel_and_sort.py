"""
Test payment cancellation endpoint and alphabetical wallet sorting.
"""
import asyncio
import os
import sys
import unittest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import db
from webapp.server import create_webapp
from config import config

from unittest.mock import patch, AsyncMock
from celo import celo_client

class TestCancelAndSort(AioHTTPTestCase):
    async def get_application(self):
        return create_webapp()

    @unittest_run_loop
    async def test_admin_auto_seed_and_cancel_payment(self):
        # Mock on-chain balance checks so payment can be created in test
        patcher1 = patch.object(celo_client, "get_usat_balance", new_callable=AsyncMock)
        patcher2 = patch.object(celo_client, "get_celo_balance", new_callable=AsyncMock)
        mock_usat = patcher1.start()
        mock_celo = patcher2.start()
        self.addCleanup(patcher1.stop)
        self.addCleanup(patcher2.stop)
        mock_usat.return_value = (10_000_000, "10.00")
        mock_celo.return_value = 1.0

        # 1. Initialize DB to trigger auto-seed
        await db.init_db()

        # 2. Login as auto-seeded master admin 8142177207 with Sasi#123
        login_resp = await self.client.post("/api/auth/login", json={
            "mobile": "8142177207",
            "password": "Sasi#123"
        })
        self.assertEqual(login_resp.status, 200)
        login_data = await login_resp.json()
        self.assertTrue(login_data["success"])
        token = login_data["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 3. Add 3 wallets with different names out of order
        await self.client.post("/api/wallets/connect", headers=headers, json={
            "name": "Zebra Vault",
            "address": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
        })
        await self.client.post("/api/wallets/connect", headers=headers, json={
            "name": "Alpha Stash",
            "address": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
        })
        await self.client.post("/api/wallets/connect", headers=headers, json={
            "name": "Beta Reserve",
            "address": "0x90F79bf6EB2c4f870365E785982E1f101E93b906"
        })

        # 4. Get wallets and verify alphabetical order
        w_resp = await self.client.get("/api/wallets", headers=headers)
        self.assertEqual(w_resp.status, 200)
        w_data = await w_resp.json()
        names = [w["name"] for w in w_data["wallets"]]
        self.assertEqual(names, sorted(names, key=str.lower))
        print("[OK] Wallets returned in alphabetical order:", names)

        # 5. Create a payment from first wallet
        first_w = w_data["wallets"][0]
        p_resp = await self.client.post("/api/payments/create", headers=headers, json={
            "source_wallet_id": first_w["id"],
            "amount": 2.0,
            "recipient_address": "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65"
        })
        self.assertEqual(p_resp.status, 200)
        p_data = await p_resp.json()
        payment_id = p_data["payment_id"]
        print(f"[OK] Created payment: {payment_id} with status: {p_data.get('status')}")

        # 6. Try creating a second payment (should be blocked as 409 active payment)
        blocked_resp = await self.client.post("/api/payments/create", headers=headers, json={
            "source_wallet_id": first_w["id"],
            "amount": 2.0,
            "recipient_address": "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65"
        })
        self.assertEqual(blocked_resp.status, 409)
        print("[OK] Concurrency check correctly blocked second payment while first is pending")

        # 7. Cancel the pending payment
        cancel_resp = await self.client.post(f"/api/payments/{payment_id}/cancel", headers=headers)
        self.assertEqual(cancel_resp.status, 200)
        cancel_data = await cancel_resp.json()
        self.assertTrue(cancel_data["success"])
        self.assertEqual(cancel_data["status"], "CANCELLED")
        print(f"[OK] Successfully cancelled payment: {cancel_data['message']}")

        # 8. Verify user can now create a new payment immediately after cancellation
        new_p_resp = await self.client.post("/api/payments/create", headers=headers, json={
            "source_wallet_id": first_w["id"],
            "amount": 2.0,
            "recipient_address": "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65"
        })
        self.assertEqual(new_p_resp.status, 200)
        new_p_data = await new_p_resp.json()
        self.assertTrue(new_p_data["success"])
        print(f"[OK] New payment created successfully after cancellation: {new_p_data['payment_id']}")

        # 9. Clean up test payment and test wallets to keep DB clean
        await self.client.post(f"/api/payments/{new_p_data['payment_id']}/cancel", headers=headers)
        async with db.connect() as conn:
            await conn.execute("DELETE FROM user_wallets WHERE wallet_name IN ('Zebra Vault', 'Alpha Stash', 'Beta Reserve')")
            await conn.commit()

if __name__ == "__main__":
    unittest.main()
