# ⚡ Production-Ready Telegram Celo Faucet Bot

A high-performance, asynchronous Telegram bot built with **Python 3.12+**, **aiogram 3.x**, **Web3.py**, and **SQLite**. It distributes **0.1 CELO per request** to user-submitted Celo Mainnet wallet addresses to cover network transaction fees.

Includes full anti-abuse protection, transaction locking, zero claim limits per user, an administrative dashboard, and a safe `DRY_RUN` simulation mode.

---

> [!CAUTION]
> ### 🔒 CRITICAL SECURITY WARNINGS
> - **NEVER commit your `.env` file or publish your private keys.**
> - **NEVER paste your 12/24-word seed phrase anywhere** — not in Telegram, not in terminal, not in code.
> - **USE A DEDICATED OKX FAUCET WALLET.** Do NOT use your personal, primary, or high-value wallet. Only keep small amounts of CELO (e.g., 1–5 CELO) in the faucet wallet.

---

## 🏗️ Architecture & Features

- **Asynchronous Architecture:** Non-blocking async I/O powered by `aiogram 3.x` and `aiosqlite`.
- **Celo Mainnet Native:** Directly interacts with Celo Mainnet RPC (Chain ID: `42220`) using current `web3.py` standards.
- **Double-Payment & Race Prevention:**
  - Database-backed claim state machine (`PROCESSING` → `SUCCESS` / `FAILED`).
  - Active claim lock: users cannot launch concurrent claims while one is processing.
  - Concurrency lock (`asyncio.Lock`) on nonce retrieval and transaction signing to prevent nonce collisions.
- **Input Sanitization & Validation:**
  - Strict EVM hex address validation (`0x` + exactly 40 hex characters).
  - Safe EIP-55 checksum normalization.
  - Automatic rejection of URLs, domain names, ENS/CNS names, usernames, and non-hex inputs.
- **Safe Test Mode (`DRY_RUN`):** Test the entire Telegram UI, state transitions, and database logging without broadcasting real blockchain transactions.
- **Admin Dashboard:** Real-time wallet balance, remaining payout capacity, all-time and daily statistics, paginated transaction history, and instant Pause/Resume faucet toggle.

---

## 📁 Project Structure

```text
celo-faucet-bot/
│
├── bot.py                        # Application entrypoint & aiogram polling loop
├── config.py                     # Environment & typed configuration dataclass
├── database.py                   # Async SQLite database wrapper (aiosqlite) with WAL mode
├── wallet.py                     # Local Web3 wallet management & address derivation
├── celo.py                       # Celo Mainnet RPC client & chain verification
│
├── handlers/
│   ├── __init__.py
│   ├── start.py                  # /start, main menu, and about screens
│   ├── faucet.py                 # FSM address input, verification & payout flow
│   ├── history.py                # Paginated personal user claim history & public status
│   └── admin.py                  # Admin panel, stats, balance, tx viewer & pause toggle
│
├── services/
│   ├── __init__.py
│   ├── transaction_service.py    # Nonce locking, signing, broadcasting & receipt tracking
│   ├── validation.py             # EVM/Celo address validation & checksumming
│   └── statistics.py             # Admin analytics & aggregation queries
│
├── keyboards/
│   ├── __init__.py
│   ├── main.py                   # Main menu, navigation & back buttons
│   └── admin.py                  # Admin controls & transaction pagination keyboards
│
├── tests/
│   └── test_faucet.py            # Automated test suite (validation, DB, RPC & dry run)
│
├── .env.example                  # Configuration template
├── .gitignore                    # Secrets, DB files, and cache exclusions
├── requirements.txt              # Pinned Python dependencies
└── README.md                     # Complete setup and deployment guide
```

---

## 🚀 8-Step Setup & Deployment Guide

Follow these steps sequentially to set up and run your faucet bot.

### STEP 1 → Create Telegram Bot
1. Open Telegram and search for [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and choose a display name (e.g., `Celo Faucet`) and username (e.g., `MyCeloGasBot`).
3. BotFather will provide your **HTTP API Token** (e.g., `8931742359:AAEJ...`).
4. Keep this token ready for Step 4.

### STEP 2 → Create OKX Faucet Wallet
1. Open the **OKX App** or **OKX Web3 Wallet** browser extension.
2. Select **Create a New Wallet** (or add a new standalone account).
3. **DO NOT** use your main personal wallet.
4. Back up your seed phrase securely offline.
5. In your OKX wallet settings, select **Export Private Key** for this specific new account.
6. Copy the private key (a 64-character hexadecimal string).

### STEP 3 → Fund Faucet Wallet
1. In your OKX wallet, copy your public Celo address.
2. Transfer a small test amount of **CELO** (e.g., 1.0 to 5.0 CELO) from an exchange or your personal wallet to this faucet address.
3. Verify on [Celoscan](https://celoscan.io) that the deposit was confirmed.

### STEP 4 → Configure `.env`
1. In your project directory, copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
2. Open `.env` and fill in your values:
   ```env
   # Telegram Bot Token from @BotFather
   TELEGRAM_BOT_TOKEN=your_bot_token_here

   # Your Telegram Numeric User ID (obtain from @userinfobot)
   ADMIN_TELEGRAM_ID=YOUR_NUMERIC_TELEGRAM_ID

   # Celo RPC (Forno is Celo's official public RPC)
   CELO_RPC_URL=https://forno.celo.org
   CELO_CHAIN_ID=42220
   NETWORK_NAME=Celo Mainnet

   # Dedicated OKX Faucet Wallet credentials
   FUNDING_WALLET_PRIVATE_KEY=0xYOUR_64_CHAR_HEX_PRIVATE_KEY
   FUNDING_WALLET_ADDRESS=0xYOUR_FUNDING_WALLET_PUBLIC_ADDRESS
   WALLET_ENCRYPTION_KEY=YOUR_64_CHARACTER_HEX_ENCRYPTION_KEY

   # Faucet Parameters
   CLAIM_AMOUNT=0.1
   MIN_GAS_RESERVE=0.02
   EXPLORER_TX_URL=https://celoscan.io/tx/
   DATABASE_PATH=data/faucet.db

   # Start with DRY_RUN=true for testing
   DRY_RUN=true
   ```

> [!TIP]
> To find your Telegram Numeric ID, send `/start` to [@userinfobot](https://t.me/userinfobot).

### STEP 5 → Install Dependencies
Ensure you have Python 3.12+ installed:
```bash
python --version
```

Create a virtual environment and install requirements:
```bash
python -m venv .venv
# On Windows PowerShell:
.venv\Scripts\Activate.ps1
# On Linux / macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

### STEP 6 → Run in DRY_RUN Mode
With `DRY_RUN=true` in `.env`, run the bot:
```bash
python bot.py
```

You will see the startup banner:
```text
====================================================================
⚡ CELO FAUCET BOT STARTING
====================================================================
Network:        Celo Mainnet (Chain ID: 42220)
RPC Endpoint:   https://forno.celo.org
Faucet Wallet:  0x1234...5678
Payout Amount:  0.1 CELO per claim
Admin Telegram: 123456789
Mode:           ⚠️  DRY RUN MODE ENABLED
                No real CELO will be sent.
====================================================================
```

### STEP 7 → Test Bot Interaction
1. Open your bot in Telegram and click `/start`.
2. Tap **💸 Get 0.1 CELO**.
3. Send a test Celo address (e.g., your personal wallet address).
4. Observe the flow: Address verified → Processing → Confirmed (Simulated hash).
5. Check **📜 My History** to confirm the claim record appears.
6. Check `/admin` to verify administrative dashboards and metrics.
7. Run automated test suite:
   ```bash
   python -m unittest tests/test_faucet.py
   ```

### STEP 8 → Enable Real Transactions
Once satisfied with the test results:
1. Open `.env`.
2. Change:
   ```env
   DRY_RUN=false
   ```
3. Restart the bot:
   ```bash
   python bot.py
   ```
4. Perform a real claim. Within ~5 seconds, 0.1 CELO will be transferred on Celo Mainnet and a valid Celoscan transaction link will be displayed!

---

## 🛡️ Security Checklist

| Item | Status | Description |
| :--- | :---: | :--- |
| **Dedicated Wallet** | ✅ | Isolated OKX wallet with low balance; main funds never exposed. |
| **Environment Secrets** | ✅ | Private keys and tokens read strictly via environment variables. |
| **Git Exclusion** | ✅ | `.env`, `data/*.db`, and `__pycache__` strictly excluded in `.gitignore`. |
| **SQL Injection Prevention** | ✅ | 100% parameterized queries via `aiosqlite`. |
| **Double-Spend Protection** | ✅ | Concurrency checks + DB states prevent duplicate active transactions. |
| **Nonce Collisions** | ✅ | `asyncio.Lock` serializes transaction nonce assignment. |
| **Fail-Safe Network Check** | ✅ | Verifies connected Chain ID (`42220`) on launch and before payouts. |
| **Sanitized Logging** | ✅ | Private keys and secrets are never printed to terminal or logs. |

---

## 🔧 Production Deployment (Systemd Service)

### Production database

Render production uses PostgreSQL through the `DATABASE_URL` environment variable. Set it from a managed Render PostgreSQL service using its internal connection URL; do not commit that URL. On first backend startup, the non-destructive schema initializer creates missing tables and indexes. It does not drop tables or seed users/wallets. Local development without `DATABASE_URL` continues to use `DATABASE_PATH` SQLite.

To initialize or upgrade the PostgreSQL schema manually, run `python -m scripts.init_postgres` with `NODE_ENV=production` and `DATABASE_URL` set in the execution environment. The command is non-destructive.
If an existing SQLite database contains records that must be retained, initialize PostgreSQL first and then run
`python -m scripts.import_sqlite_to_postgres --sqlite data/faucet.db` with `DATABASE_URL` set. The import only adds
missing rows; it never deletes or replaces PostgreSQL records. Stop the application while importing so no writes are missed.

For 24/7 background operation on Linux VPS (Ubuntu/Debian):

1. Create a systemd unit file:
   ```bash
   sudo nano /etc/systemd/system/celo-faucet.service
   ```
2. Add the following configuration:
   ```ini
   [Unit]
   Description=Telegram Celo Faucet Bot
   After=network.target

   [Service]
   Type=simple
   User=ubuntu
   WorkingDirectory=/home/ubuntu/celo-faucet-bot
   ExecStart=/home/ubuntu/celo-faucet-bot/.venv/bin/python bot.py
   Restart=always
   RestartSec=5
   EnvironmentFile=/home/ubuntu/celo-faucet-bot/.env

   [Install]
   WantedBy=multi-user.target
   ```
3. Enable and start:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable celo-faucet
   sudo systemctl start celo-faucet
   sudo systemctl status celo-faucet
   ```

---

## ❓ Troubleshooting

- **`UnicodeEncodeError` on Windows Terminal:**
  - Resolved automatically in `bot.py` via `sys.stdout.reconfigure(encoding="utf-8")`.
- **`Insufficient faucet balance`:**
  - Deposit at least `CLAIM_AMOUNT + MIN_GAS_RESERVE` (e.g. > 0.12 CELO) to your faucet wallet.
- **`RPC connection failed`:**
  - Check your internet connection or switch `CELO_RPC_URL` in `.env` to another provider (e.g., QuickNode, Alchemy, or Infura).
- **Admin commands not working:**
  - Verify your numeric Telegram ID in `ADMIN_TELEGRAM_ID` using [@userinfobot](https://t.me/userinfobot).

---

## 📜 License
MIT License. Built for Celo ecosystem developers.
