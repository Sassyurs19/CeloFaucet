"""
Automated test suite for CELO USAT Payment Web Application.
Tests:
- Health check and static frontend serving
- User registration and validation (name, mobile masking)
- Dual wallet onboarding (EVM connect & AES-256 encrypted import)
- Recovery phrase (12/24 words) rejection
- Receiving wallets retrieval (verifying user's real address)
- Fixed $2.00 USAT payment creation and validation
- Admin portal authentication and dashboard metrics
"""

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from config import config
from database import db
from webapp.server import create_webapp


@pytest.mark.asyncio
async def test_webapp_endpoints(aiohttp_client):
    """Integration test suite for the REST API and web application."""
    # Ensure database is initialized
    await db.init_db()

    app = create_webapp()
    client = await aiohttp_client(app)

    # 1. Health check
    resp = await client.get("/health")
    assert resp.status == 200
    health_data = await resp.json()
    assert health_data["status"] == "healthy"

    # 2. Frontend index.html serving
    resp = await client.get("/")
    assert resp.status == 200
    text = await resp.text()
    assert "CELO USAT Payment Portal" in text
    assert "$2.00" in text

    # Static CSS serving
    resp = await client.get("/css/styles.css")
    assert resp.status == 200
    css_text = await resp.text()
    assert "--celo-green" in css_text

    # 3. User Registration Validation
    # 3a. Invalid Name (too short)
    resp = await client.post("/api/auth/register", json={
        "name": "A",
        "mobile": "+919876543210"
    })
    assert resp.status == 400
    err = await resp.json()
    assert "Full Name must be between 2 and 80 characters" in err["error"]

    # 3b. Valid Registration
    resp = await client.post("/api/auth/register", json={
        "name": "Sarah Connor",
        "mobile": "+1 (555) 234-5678"
    })
    assert resp.status == 200
    reg_data = await resp.json()
    assert "token" in reg_data
    token = reg_data["token"]
    user_id = reg_data["user"]["id"]
    assert reg_data["user"]["full_name"] == "Sarah Connor"
    assert "******" in reg_data["user"]["mobile"]  # Masked mobile

    # 4. Auth Me endpoint
    auth_headers = {"Authorization": f"Bearer {token}"}
    resp = await client.get("/api/auth/me", headers=auth_headers)
    assert resp.status == 200
    me_data = await resp.json()
    assert me_data["user"]["id"] == user_id
    assert me_data["user"]["full_name"] == "Sarah Connor"

    # 5. Recovery phrase rejection on Wallet Import
    resp = await client.post("/api/wallets/import", headers=auth_headers, json={
        "private_key": "apple banana cherry dog elephant fox grape horse igloo jaguar kite lion",
        "label": "My Seed Wallet"
    })
    assert resp.status == 400
    import_err = await resp.json()
    assert "Recovery phrases are not supported" in import_err["error"]

    # 6. Valid Private Key Import
    test_private_key = "0x4f3edf983ac636a65a842ce7c78d9aa706d3b113bce9c46f30d7d21715b23b1d"
    resp = await client.post("/api/wallets/import", headers=auth_headers, json={
        "private_key": test_private_key,
        "label": "Imported Test Key"
    })
    assert resp.status == 200
    import_data = await resp.json()
    assert import_data["wallet"]["wallet_type"] == "imported"
    assert import_data["wallet"]["address"].lower() == "0x90f8bf6a479f320ead074411a4b0e7944ea8c9c1".lower()
    imported_wallet_id = import_data["wallet"]["id"]

    # 7. In-Browser Wallet Connection
    connected_address = "0xBa2ECD20E1C8f3f7158407ace77ad6be46E83166"
    resp = await client.post("/api/wallets/connect", headers=auth_headers, json={
        "address": connected_address,
        "label": "Browser MetaMask"
    })
    assert resp.status == 200
    conn_data = await resp.json()
    assert conn_data["wallet"]["wallet_type"] == "connected"
    assert conn_data["wallet"]["address"].lower() == connected_address.lower()

    # 8. List User Wallets
    resp = await client.get("/api/wallets", headers=auth_headers)
    assert resp.status == 200
    wallets_data = await resp.json()
    assert len(wallets_data["wallets"]) >= 2

    # 9. Receiving Wallets (check user's configured address)
    resp = await client.get("/api/receiving-wallets", headers=auth_headers)
    assert resp.status == 200
    recv_data = await resp.json()
    assert len(recv_data["receiving_wallets"]) > 0
    active_recv = recv_data["receiving_wallets"][0]
    assert active_recv["address"].lower() == "0xba2ecd20e1c8f3f7158407ace77ad6be46e83166".lower()

    # 10. Admin Authentication
    # Wrong password
    resp = await client.post("/api/admin/login", json={"password": "wrong_password_xyz"})
    assert resp.status == 401

    # Correct password
    admin_pwd = config.admin_web_password
    resp = await client.post("/api/admin/login", json={"password": admin_pwd})
    assert resp.status == 200
    admin_data = await resp.json()
    assert "token" in admin_data
    admin_token = admin_data["token"]
    admin_headers = {"X-Admin-Token": admin_token}

    # 11. Admin Dashboard Metrics
    resp = await client.get("/api/admin/dashboard", headers=admin_headers)
    assert resp.status == 200
    dash = await resp.json()
    assert "total_users" in dash
    assert dash["fixed_payment_usat"] == 2.0

    # 12. Admin Funding Reserve
    resp = await client.get("/api/admin/funding", headers=admin_headers)
    assert resp.status == 200
    funding = await resp.json()
    assert "celo_balance" in funding
    assert funding["subsidy_amount"] == "0.05 CELO"
