import asyncio
import sqlite3
import requests
import uuid

BASE_URL = "http://localhost:8080"

def test_frontend_markup():
    r = requests.get(f"{BASE_URL}/")
    assert r.status_code == 200, "Failed to load index.html"
    html = r.text

    # 1. Verify profile removed from mobile-bottom-nav
    bottom_nav_idx = html.find('class="mobile-bottom-nav"')
    assert bottom_nav_idx != -1
    bottom_nav_chunk = html[bottom_nav_idx:bottom_nav_idx+500]
    assert 'data-view="profile"' not in bottom_nav_chunk, "Profile must be removed from mobile-bottom-nav"
    assert 'data-view="dashboard"' in bottom_nav_chunk
    assert 'data-view="wallets"' in bottom_nav_chunk
    assert 'data-view="payments"' in bottom_nav_chunk
    print("PASS: Profile successfully removed from bottom nav; only Dashboard, Wallets, Payments remain.")

    # 2. Verify admin console elements
    assert 'id="admin-payments-search"' in html
    assert 'id="admin-payments-date"' in html
    assert 'id="admin-payments-time-from"' in html
    assert 'id="admin-payments-time-to"' in html
    assert 'id="admin-payments-filter-summary"' in html
    assert 'id="admin-payments-card-list"' in html
    assert 'id="admin-wallets-card-list"' in html
    assert 'id="admin-receiving-card-list"' in html
    assert 'id="admin-funding-card-list"' in html
    assert 'id="btn-setting-pause"' in html
    print("PASS: Admin console elements & card containers verified in HTML.")


def test_admin_search_and_time_filters():
    # Login as admin (Sasidhar 8142177207)
    s = requests.Session()
    login_res = s.post(f"{BASE_URL}/api/auth/login", json={"mobile": "8142177207", "password": "Sasi#123"})
    assert login_res.status_code == 200, f"Login failed: {login_res.text}"
    token = login_res.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Ensure a test user 'Poornima' exists and has payments in DB
    conn = sqlite3.connect('data/faucet.db')
    cur = conn.cursor()
    
    # Check or insert user Poornima
    cur.execute("SELECT id FROM users WHERE full_name = 'Poornima'")
    p_user = cur.fetchone()
    if not p_user:
        cur.execute("INSERT INTO users (telegram_id, full_name, mobile_number, normalized_mobile) VALUES (999901, 'Poornima', '9876543210', '9876543210')")
        p_user_id = cur.lastrowid
    else:
        p_user_id = p_user[0]

    # Insert test payment for Poornima at local time 16:30:00 (4:30 PM)
    # Using datetime('now', 'start of day', '+16 hours', '+30 minutes')
    cur.execute("SELECT id FROM usat_payments WHERE payment_id = 'test_poornima_pay_1'")
    if not cur.fetchone():
        cur.execute("""
            INSERT INTO usat_payments (
                payment_id, user_id, telegram_id, wallet_type, from_address, to_address,
                receiving_wallet_name, amount_usat, status, tx_hash, created_at
            ) VALUES (
                'test_poornima_pay_1', ?, 999901, 'imported', '0x1111111111111111111111111111111111111111',
                '0xba2ECD20E1C8f3F7158407aCe77aD6bE46E83166', 'Admin (Sassy)', '2.00', 'SUCCESS',
                '0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890',
                datetime('now', 'localtime', 'start of day', '+16 hours', '+30 minutes', 'utc')
            )
        """, (p_user_id,))

    # Insert another test payment for user Rahul at 20:00:00 (8:00 PM) - outside 4 PM to 7 PM window
    cur.execute("SELECT id FROM users WHERE full_name = 'Rahul'")
    r_user = cur.fetchone()
    if not r_user:
        cur.execute("INSERT INTO users (telegram_id, full_name, mobile_number, normalized_mobile) VALUES (999902, 'Rahul', '9876543211', '9876543211')")
        r_user_id = cur.lastrowid
    else:
        r_user_id = r_user[0]

    cur.execute("SELECT id FROM usat_payments WHERE payment_id = 'test_rahul_pay_1'")
    if not cur.fetchone():
        cur.execute("""
            INSERT INTO usat_payments (
                payment_id, user_id, telegram_id, wallet_type, from_address, to_address,
                receiving_wallet_name, amount_usat, status, tx_hash, created_at
            ) VALUES (
                'test_rahul_pay_1', ?, 999902, 'connected', '0x2222222222222222222222222222222222222222',
                '0xba2ECD20E1C8f3F7158407aCe77aD6bE46E83166', 'Admin (Sassy)', '2.00', 'SUCCESS',
                '0x9876543210abcdef9876543210abcdef9876543210abcdef9876543210abcdef',
                datetime('now', 'localtime', 'start of day', '+20 hours', '+00 minutes', 'utc')
            )
        """, (r_user_id,))

    conn.commit()
    conn.close()

    # 1. Test Search by Name 'Poornima'
    res_search = s.get(f"{BASE_URL}/api/admin/payments?search=Poornima", headers=headers)
    assert res_search.status_code == 200, res_search.text
    data_search = res_search.json()
    assert data_search["total"] >= 1, "Should find at least 1 payment for Poornima"
    assert any(p["user"] == "Poornima" for p in data_search["payments"]), "Poornima payments must be visible"
    print(f"PASS: Search 'Poornima' returned {data_search['total']} payment(s) successfully.")

    # 2. Test Time Window: 16:00 to 19:00 (4:00 PM to 7:00 PM)
    res_time = s.get(f"{BASE_URL}/api/admin/payments?time_from=16:00&time_to=19:00", headers=headers)
    assert res_time.status_code == 200, res_time.text
    data_time = res_time.json()
    print(f"Time filter 16:00 - 19:00 returned total: {data_time['total']}, amount: ${data_time['total_amount']}, unique members: {data_time['unique_users']}")
    assert data_time["total"] >= 1, "Poornima payment at 16:30 must be included in 16:00-19:00"
    # Ensure Rahul's payment at 20:00 is NOT in this window
    for p in data_time["payments"]:
        assert p["user"] != "Rahul", "Rahul payment at 20:00 must be excluded from 16:00-19:00"
    print("PASS: Time filter (4:00 PM to 7:00 PM) correctly isolated transactions in that window.")

    # 3. Test Wallets listing returns both USDT and CELO balance
    res_wallets = s.get(f"{BASE_URL}/api/admin/wallets", headers=headers)
    assert res_wallets.status_code == 200
    wallets_data = res_wallets.json()
    assert "wallets" in wallets_data
    if wallets_data["wallets"]:
        w0 = wallets_data["wallets"][0]
        assert "usat_balance" in w0
        assert "celo_balance" in w0
        print(f"PASS: Wallets display both USDT (${w0['usat_balance']}) and CELO ({w0['celo_balance']}) balances.")


if __name__ == "__main__":
    print("=== Running Admin Remake & Filter Verification ===")
    test_frontend_markup()
    test_admin_search_and_time_filters()
    print("=== ALL TESTS PASSED 100%! ===")
