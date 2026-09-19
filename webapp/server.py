"""
Lightweight async WebApp server for non-custodial Celo wallet connections and in-wallet transaction signing.
Uses aiohttp to serve the mobile-optimized, dark-mode Telegram WebApp and handle API callbacks.
"""

from __future__ import annotations

import asyncio
import json
import logging
from aiohttp import web
from pathlib import Path
from web3 import Web3

from config import config
from database import db
from celo import celo_client

logger = logging.getLogger(__name__)

# In-memory store for pending payments waiting for in-wallet approval
# payment_id -> asyncio.Future[str] (resolved with tx_hash)
PENDING_PAYMENT_FUTURES: dict[str, asyncio.Future] = {}


def get_html_template(content: str, title: str = "CELO USAT WebApp") -> str:
    """Generate consistent dark-mode styling with Telegram WebApp SDK integration."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>{title}</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        :root {{
            --bg-color: #0d1117;
            --card-bg: #161b22;
            --border-color: #30363d;
            --text-main: #f0f6fc;
            --text-muted: #8b949e;
            --celo-green: #35d07f;
            --celo-dark-green: #28a745;
            --accent: #58a6ff;
            --danger: #f85149;
        }}
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        }}
        body {{
            background-color: var(--bg-color);
            color: var(--text-main);
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            padding: 20px;
        }}
        .container {{
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 28px 24px;
            width: 100%;
            max-width: 420px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
            text-align: center;
        }}
        .logo {{
            font-size: 38px;
            margin-bottom: 12px;
        }}
        h1 {{
            font-size: 20px;
            font-weight: 700;
            margin-bottom: 8px;
            letter-spacing: -0.3px;
        }}
        p.subtitle {{
            font-size: 14px;
            color: var(--text-muted);
            margin-bottom: 24px;
            line-height: 1.4;
        }}
        .info-box {{
            background-color: rgba(53, 208, 127, 0.08);
            border: 1px solid rgba(53, 208, 127, 0.25);
            border-radius: 10px;
            padding: 14px;
            margin-bottom: 24px;
            text-align: left;
            font-size: 13px;
        }}
        .info-row {{
            display: flex;
            justify-content: space-between;
            margin-bottom: 8px;
        }}
        .info-row:last-child {{
            margin-bottom: 0;
        }}
        .info-label {{
            color: var(--text-muted);
        }}
        .info-val {{
            font-weight: 600;
            font-family: monospace;
            color: var(--text-main);
        }}
        .btn {{
            display: block;
            width: 100%;
            background: linear-gradient(135deg, #35d07f, #22a058);
            color: #000;
            font-weight: 700;
            font-size: 15px;
            border: none;
            border-radius: 12px;
            padding: 14px;
            cursor: pointer;
            transition: all 0.2s ease;
            text-decoration: none;
            box-shadow: 0 4px 12px rgba(53, 208, 127, 0.3);
        }}
        .btn:hover {{
            opacity: 0.92;
            transform: translateY(-1px);
        }}
        .btn:disabled {{
            background: #21262d;
            color: #6e7681;
            cursor: not-allowed;
            box-shadow: none;
        }}
        .status-msg {{
            margin-top: 16px;
            font-size: 13px;
            min-height: 20px;
        }}
        .security-badge {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-size: 12px;
            color: var(--celo-green);
            margin-top: 18px;
            background: rgba(53, 208, 127, 0.1);
            padding: 6px 12px;
            border-radius: 20px;
        }}
    </style>
</head>
<body>
    <div class="container">
        {content}
    </div>
    <script>
        if (window.Telegram && window.Telegram.WebApp) {{
            window.Telegram.WebApp.ready();
            window.Telegram.WebApp.expand();
        }}
    </script>
</body>
</html>
"""


async def handle_connect_page(request: web.Request) -> web.Response:
    """Serve the Connect Wallet page."""
    frontend_dir = Path(__file__).parent.parent / "frontend"
    connect_file = frontend_dir / "connect.html"
    if connect_file.exists():
        return web.FileResponse(connect_file)

    user_id = request.query.get("user_id", "")
    
    content = f"""
        <div class="logo">⚡</div>
        <h1>Connect Celo Wallet</h1>
        <p class="subtitle">Connect your non-custodial Celo wallet securely. Your private key and seed phrase are never requested.</p>
        
        <div class="info-box">
            <div class="info-row">
                <span class="info-label">Network:</span>
                <span class="info-val">Celo Mainnet (42220)</span>
            </div>
            <div class="info-row">
                <span class="info-label">Security:</span>
                <span class="info-val">Non-Custodial</span>
            </div>
        </div>

        <button id="connectBtn" class="btn" onclick="connectWallet()">🔗 Connect Wallet</button>
        <div id="status" class="status-msg"></div>

        <div class="security-badge">
            🔒 100% Non-Custodial & Secure
        </div>

        <script>
            async function connectWallet() {{
                const status = document.getElementById('status');
                const btn = document.getElementById('connectBtn');
                
                if (typeof window.ethereum === 'undefined') {{
                    status.innerHTML = '<span style="color:var(--danger)">No Web3 wallet found. Open in MetaMask, OKX, or Valora browser.</span>';
                    return;
                }}

                try {{
                    btn.disabled = true;
                    status.innerText = 'Requesting account authorization...';
                    
                    // Request accounts
                    const accounts = await window.ethereum.request({{ method: 'eth_requestAccounts' }});
                    if (!accounts || accounts.length === 0) {{
                        throw new Error('No accounts selected');
                    }}
                    
                    const address = accounts[0];
                    status.innerText = 'Connected: ' + address.slice(0, 6) + '...' + address.slice(-4);

                    // Verify or switch to Celo Mainnet (42220 / 0xa4ec)
                    try {{
                        await window.ethereum.request({{
                            method: 'wallet_switchEthereumChain',
                            params: [{{ chainId: '0xa4ec' }}]
                        }});
                    }} catch (switchErr) {{
                        if (switchErr.code === 4902) {{
                            await window.ethereum.request({{
                                method: 'wallet_addEthereumChain',
                                params: [{{
                                    chainId: '0xa4ec',
                                    chainName: 'Celo Mainnet',
                                    nativeCurrency: {{ name: 'CELO', symbol: 'CELO', decimals: 18 }},
                                    rpcUrls: ['https://forno.celo.org'],
                                    blockExplorerUrls: ['https://celoscan.io']
                                }}]
                            }});
                        }}
                    }}

                    status.innerHTML = '<span style="color:var(--celo-green)">✅ Wallet Connected! Sending to bot...</span>';

                    // Send to Telegram WebApp if available
                    const payload = JSON.stringify({{
                        action: 'wallet_connected',
                        address: address,
                        user_id: '{user_id}'
                    }});

                    if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData) {{
                        window.Telegram.WebApp.sendData(payload);
                        setTimeout(() => window.Telegram.WebApp.close(), 1000);
                    }} else {{
                        // Fallback API POST
                        await fetch('/api/connect', {{
                            method: 'POST',
                            headers: {{ 'Content-Type': 'application/json' }},
                            body: payload
                        }});
                        status.innerHTML = '<span style="color:var(--celo-green)">✅ Done! You can return to Telegram.</span>';
                    }}

                }} catch (err) {{
                    btn.disabled = false;
                    status.innerHTML = '<span style="color:var(--danger)">Error: ' + (err.message || err) + '</span>';
                }}
            }}
        </script>
    """
    return web.Response(text=get_html_template(content, "Connect Celo Wallet"), content_type="text/html")


async def handle_pay_page(request: web.Request) -> web.Response:
    """Serve the In-Wallet USAT Payment Signing page."""
    frontend_dir = Path(__file__).parent.parent / "frontend"
    pay_file = frontend_dir / "pay.html"
    if pay_file.exists():
        return web.FileResponse(pay_file)

    payment_id = request.query.get("payment_id", "")
    from_addr = request.query.get("from", "")
    to_addr = request.query.get("to", "")
    rec_name = request.query.get("rec_name", "Receiving Wallet")
    
    trunc_from = f"{from_addr[:6]}...{from_addr[-4:]}" if len(from_addr) > 10 else from_addr
    trunc_to = f"{to_addr[:6]}...{to_addr[-4:]}" if len(to_addr) > 10 else to_addr
    
    # Base units for $2.00 USAT
    base_units = celo_client.get_payment_amount_base_units()
    tx_params = celo_client.build_usat_transfer_tx_params(from_addr, to_addr, base_units)
    tx_params_json = json.dumps(tx_params)

    content = f"""
        <div class="logo">💵</div>
        <h1>Approve $2.00 USAT</h1>
        <p class="subtitle">Please confirm the $2.00 USAT payment in your connected wallet.</p>
        
        <div class="info-box">
            <div class="info-row">
                <span class="info-label">Amount:</span>
                <span class="info-val" style="color:var(--celo-green)">$2.00 USAT</span>
            </div>
            <div class="info-row">
                <span class="info-label">From:</span>
                <span class="info-val">{trunc_from}</span>
            </div>
            <div class="info-row">
                <span class="info-label">To ({rec_name}):</span>
                <span class="info-val">{trunc_to}</span>
            </div>
            <div class="info-row">
                <span class="info-label">Network:</span>
                <span class="info-val">Celo Mainnet</span>
            </div>
        </div>

        <button id="signBtn" class="btn" onclick="signAndSend()">💳 Approve & Pay in Wallet</button>
        <div id="status" class="status-msg"></div>

        <div class="security-badge">
            🔐 Signed directly by your wallet
        </div>

        <script>
            const txParams = {tx_params_json};
            const paymentId = '{payment_id}';

            async function signAndSend() {{
                const status = document.getElementById('status');
                const btn = document.getElementById('signBtn');

                if (typeof window.ethereum === 'undefined') {{
                    status.innerHTML = '<span style="color:var(--danger)">No Web3 wallet found. Please open in your wallet browser.</span>';
                    return;
                }}

                try {{
                    btn.disabled = true;
                    status.innerText = 'Switching to Celo Mainnet...';

                    // Ensure Celo Mainnet
                    try {{
                        await window.ethereum.request({{
                            method: 'wallet_switchEthereumChain',
                            params: [{{ chainId: '0xa4ec' }}]
                        }});
                    }} catch (switchErr) {{
                        if (switchErr.code === 4902) {{
                            await window.ethereum.request({{
                                method: 'wallet_addEthereumChain',
                                params: [{{
                                    chainId: '0xa4ec',
                                    chainName: 'Celo Mainnet',
                                    nativeCurrency: {{ name: 'CELO', symbol: 'CELO', decimals: 18 }},
                                    rpcUrls: ['https://forno.celo.org'],
                                    blockExplorerUrls: ['https://celoscan.io']
                                }}]
                            }});
                        }}
                    }}

                    status.innerText = 'Please confirm the transaction in your wallet prompt...';

                    // Request transaction signature and broadcast from wallet
                    const txHash = await window.ethereum.request({{
                        method: 'eth_sendTransaction',
                        params: [txParams]
                    }});

                    status.innerHTML = '<span style="color:var(--celo-green)">✅ Broadcasted! Tx: ' + txHash.slice(0, 10) + '...</span>';

                    // Notify bot
                    const payload = JSON.stringify({{
                        action: 'payment_submitted',
                        payment_id: paymentId,
                        tx_hash: txHash
                    }});

                    if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData) {{
                        window.Telegram.WebApp.sendData(payload);
                        setTimeout(() => window.Telegram.WebApp.close(), 1500);
                    }} else {{
                        await fetch('/api/payment_submitted', {{
                            method: 'POST',
                            headers: {{ 'Content-Type': 'application/json' }},
                            body: payload
                        }});
                        status.innerHTML = '<span style="color:var(--celo-green)">✅ Transaction submitted! You may return to Telegram.</span>';
                    }}

                }} catch (err) {{
                    btn.disabled = false;
                    status.innerHTML = '<span style="color:var(--danger)">Transaction rejected or failed: ' + (err.message || err) + '</span>';
                }}
            }}
        </script>
    """
    return web.Response(text=get_html_template(content, "Pay $2.00 USAT"), content_type="text/html")


async def handle_api_connect(request: web.Request) -> web.Response:
    """API endpoint when wallet is connected via browser fallback."""
    try:
        data = await request.json()
        address = data.get("address")
        user_id = data.get("user_id")
        if address and user_id:
            logger.info("API: Wallet connected for user %s: %s", user_id, address)
            return web.json_response({"status": "ok", "address": address})
        return web.json_response({"status": "error", "message": "Missing fields"}, status=400)
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)


async def handle_api_payment_submitted(request: web.Request) -> web.Response:
    """API endpoint when transaction is submitted by connected wallet."""
    try:
        data = await request.json()
        payment_id = data.get("payment_id")
        tx_hash = data.get("tx_hash")
        
        if payment_id and tx_hash:
            logger.info("API: Payment %s submitted with tx_hash: %s", payment_id, tx_hash)
            # Resolve pending asyncio future if waiting
            fut = PENDING_PAYMENT_FUTURES.get(payment_id)
            if fut and not fut.done():
                fut.set_result(tx_hash)
            return web.json_response({"status": "ok", "tx_hash": tx_hash})
        return web.json_response({"status": "error", "message": "Missing payment_id or tx_hash"}, status=400)
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)


async def handle_index(request: web.Request) -> web.Response:
    """Serve the single-page application entry point."""
    frontend_dir = Path(__file__).parent.parent / "frontend"
    index_file = frontend_dir / "index.html"
    if index_file.exists():
        return web.FileResponse(index_file)
    return web.Response(text="Frontend index.html not found", status=404)


async def handle_favicon(request: web.Request) -> web.Response:
    """Serve dynamic SVG favicon to avoid browser 404 errors."""
    svg_icon = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="#10b981" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="10"></circle>'
        '<path d="M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8"></path>'
        '<path d="M12 18V6"></path>'
        '</svg>'
    )
    return web.Response(text=svg_icon, content_type="image/svg+xml")


def is_allowed_origin(origin: str) -> bool:
    """Validate whether an origin is authorized for CORS."""
    if not origin:
        return False
    clean_origin = origin.lower().rstrip("/")
    allowed = {
        "https://celofaucet.web.app",
        "https://celofaucet.firebaseapp.com",
    }
    if config.frontend_url:
        allowed.add(config.frontend_url.lower().rstrip("/"))
    if config.webapp_public_url:
        allowed.add(config.webapp_public_url.lower().rstrip("/"))

    if clean_origin in allowed:
        return True

    # Allow localhost and 127.0.0.1 on any port for development
    if clean_origin.startswith("http://localhost") or clean_origin.startswith("http://127.0.0.1"):
        return True

    return False


@web.middleware
async def cors_and_error_middleware(request: web.Request, handler) -> web.Response:
    """Apply strict CORS policies and sanitize production errors without exposing stack traces."""
    origin = request.headers.get("Origin", "")

    # Preflight OPTIONS handler
    if request.method == "OPTIONS":
        response = web.Response(status=204)
        if is_allowed_origin(origin):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = (
                "Authorization, Content-Type, X-Session-Id, X-Admin-Token, Accept"
            )
            response.headers["Access-Control-Max-Age"] = "86400"
        return response

    try:
        response = await handler(request)
    except web.HTTPException as ex:
        response = ex
    except Exception as ex:
        logger.exception("Unhandled production exception on %s: %s", request.path, ex)
        response = web.json_response(
            {"error": "An internal server error occurred. Please try again later."},
            status=500,
        )

    if is_allowed_origin(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = (
            "Authorization, Content-Type, X-Session-Id, X-Admin-Token, Accept"
        )

    return response


async def handle_health_check(request: web.Request) -> web.Response:
    """Production health check returning HTTP 200 with service status."""
    return web.json_response({
        "status": "ok",
        "service": "celo-usdt-api"
    }, status=200)


def create_webapp() -> web.Application:
    """Build and configure the aiohttp application with middleware and routes."""
    app = web.Application(middlewares=[cors_and_error_middleware])
    
    # Mount production REST API routes
    from webapp.api import register_api_routes
    register_api_routes(app)

    # Favicon route
    app.router.add_get("/favicon.ico", handle_favicon)

    # Static assets
    frontend_dir = Path(__file__).parent.parent / "frontend"
    css_dir = frontend_dir / "css"
    js_dir = frontend_dir / "js"
    if css_dir.exists():
        app.router.add_static("/css", css_dir)
    if js_dir.exists():
        app.router.add_static("/js", js_dir)

    # SPA Web Pages
    app.router.add_get("/", handle_index)
    app.router.add_get("/dashboard", handle_index)
    app.router.add_get("/wallets", handle_index)
    app.router.add_get("/payments", handle_index)
    app.router.add_get("/profile", handle_index)
    app.router.add_get("/admin", handle_index)

    # Telegram bot fallback pages (backward compatibility)
    app.router.add_get("/connect", handle_connect_page)
    app.router.add_get("/pay", handle_pay_page)
    app.router.add_post("/api/connect", handle_api_connect)
    app.router.add_post("/api/payment_submitted", handle_api_payment_submitted)
    
    # Production Health check
    app.router.add_get("/health", handle_health_check)
    return app


async def start_webapp_server(host: str = "0.0.0.0", port: int = 8080) -> web.AppRunner:
    """Start the aiohttp web server concurrently."""
    app = create_webapp()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("WebApp server running on http://%s:%d", host, port)
    return runner


