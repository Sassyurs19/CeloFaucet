/**
 * CELO USAT Payment Portal Application Controller
 * Professional Fintech / Web3 Interface
 * Zero emojis, Lucide SVG icons, dynamic USDT balance calculation,
 * 3-dot dropdown wallet cards, simplified profile, and comprehensive admin suite.
 */

const app = (function () {
  const CELO_EXPLORER_BASE = 'https://celoscan.io/tx/';

  // Global App State
  let state = {
    user: null,
    sessionToken: null,
    adminToken: null,
    currentView: 'dashboard',
    currentAdminTab: 'overview',
    adminPaymentStatusFilter: '',
    wallets: [],
    workspaces: [],
    activeWorkspaceId: null,
    dashboardWorkspaceId: null,
    expandedWalletId: null,
    selectedWalletId: null,
    receivingWallets: [],
    savedRecipients: [],
    payments: [],
    isSubmitting: false,
    activePayment: null,
    walletGridColumns: 2,
  };

  let searchDebounceTimers = {};

  // --- SVG Icon Helper & Lucide Refresh ---

  function renderIcons() {
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
      window.lucide.createIcons();
    }
  }

  // --- Toast Notification System (Zero Emojis) ---

  function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;

    let iconName = 'info';
    if (type === 'success') iconName = 'check-circle-2';
    if (type === 'error') iconName = 'alert-triangle';

    toast.innerHTML = `
      <i data-lucide="${iconName}" class="icon-sm"></i>
      <span style="flex:1;">${escapeHtml(message)}</span>
    `;
    container.appendChild(toast);
    renderIcons();

    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(-8px)';
      toast.style.transition = 'all 0.2s ease';
      setTimeout(() => toast.remove(), 200);
    }, 4000);
  }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function formatShortAddress(addr) {
    if (!addr || addr.length < 12) return addr || '';
    return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
  }

  function paymentDateLabel(value) {
    if (!value) return 'Unknown date';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return 'Unknown date';
    const today = new Date();
    const startToday = new Date(today.getFullYear(), today.getMonth(), today.getDate());
    const startDate = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    const daysAgo = Math.round((startToday - startDate) / 86400000);
    if (daysAgo === 0) return 'Today';
    if (daysAgo === 1) return 'Yesterday';
    return date.toLocaleDateString(undefined, { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
  }

  function setBalanceTone(element, value) {
    if (!element) return;
    const isAvailable = Number(value) > 0;
    element.classList.toggle('balance-positive', isAvailable);
    element.classList.toggle('balance-zero', !isAvailable);
  }

  async function copyAddress(addr) {
    if (!addr) return;
    try {
      await navigator.clipboard.writeText(addr);
      showToast(`Address copied to clipboard: ${formatShortAddress(addr)}`, 'success');
    } catch (e) {
      // Fallback
      const el = document.createElement('textarea');
      el.value = addr;
      document.body.appendChild(el);
      el.select();
      document.execCommand('copy');
      document.body.removeChild(el);
      showToast(`Address copied: ${formatShortAddress(addr)}`, 'success');
    }
  }

  // --- Password Visibility Toggle ---

  function togglePasswordVisibility(inputId, btn) {
    const input = document.getElementById(inputId);
    if (!input) return;

    const isPassword = input.type === 'password';
    input.type = isPassword ? 'text' : 'password';

    if (btn) {
      btn.innerHTML = isPassword
        ? '<i data-lucide="eye-off" class="icon-sm"></i>'
        : '<i data-lucide="eye" class="icon-sm"></i>';
      renderIcons();
    }
  }

  // --- Standalone Client-Side Fallback Engine ---
  // Transparently powers https://celofaucet.web.app if the Python backend is offline or unlinked

  function getLocalStore(key, defaultValue) {
    try {
      const val = localStorage.getItem(key);
      return val ? JSON.parse(val) : defaultValue;
    } catch (e) {
      return defaultValue;
    }
  }

  function setLocalStore(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (e) {}
  }

  function handleStandaloneFallback(endpoint, options = {}) {
    throw new Error('The service is unavailable. No offline wallet or transaction mode is provided.');
    /* Legacy fallback implementation intentionally disabled: it must never
       create browser-only users, wallets, balances, or transactions.
    const method = (options.method || 'GET').toUpperCase();
    let body = {};
    try {
      if (options.body) body = typeof options.body === 'string' ? JSON.parse(options.body) : options.body;
    } catch (e) {}

    // 1. Auth Login
    if (endpoint.startsWith('/api/auth/login')) {
      const mobile = body.mobile || '9876543210';
      const isSasidhar = mobile.includes('8142177207');
      const users = getLocalStore('standalone_users', []);
      let user = users.find(u => u.mobile === mobile);
      if (!user) {
        user = {
          id: Date.now(),
          name: isSasidhar ? 'Sasidhar' : (body.name || `User (${mobile.slice(-4)})`),
          full_name: isSasidhar ? 'Sasidhar' : (body.name || `User (${mobile.slice(-4)})`),
          mobile: mobile,
          mobile_raw: mobile,
          is_admin_eligible: isSasidhar,
          registered: true,
          created_at: new Date().toISOString(),
        };
        users.push(user);
        setLocalStore('standalone_users', users);
      }
      setLocalStore('standalone_current_user', user);
      return {
        success: true,
        token: 'standalone_token_' + Date.now(),
        user: user,
      };
    }

    // 2. Auth Register
    if (endpoint.startsWith('/api/auth/register')) {
      const mobile = body.mobile || '9876543210';
      const name = body.name || `User (${mobile.slice(-4)})`;
      const isSasidhar = mobile.includes('8142177207');
      const users = getLocalStore('standalone_users', []);
      const user = {
        id: Date.now(),
        name: name,
        full_name: name,
        mobile: mobile,
        mobile_raw: mobile,
        is_admin_eligible: isSasidhar,
        registered: true,
        created_at: new Date().toISOString(),
      };
      users.push(user);
      setLocalStore('standalone_users', users);
      setLocalStore('standalone_current_user', user);
      return {
        success: true,
        token: 'standalone_token_' + Date.now(),
        user: user,
      };
    }

    // 3. Auth Me
    if (endpoint.startsWith('/api/auth/me')) {
      const user = getLocalStore('standalone_current_user', null);
      if (user) {
        return { authenticated: true, user: user };
      }
      return { authenticated: false };
    }

    // 4. Auth Logout
    if (endpoint.startsWith('/api/auth/logout')) {
      localStorage.removeItem('standalone_current_user');
      return { success: true };
    }

    // 5. Change Password
    if (endpoint.startsWith('/api/auth/change-password')) {
      return { success: true, message: 'Password updated successfully.' };
    }

    // 6. Receiving Wallets
    if (endpoint === '/api/receiving-wallets' || endpoint === '/api/admin/receiving-wallets') {
      let rWallets = getLocalStore('standalone_receiving_wallets', null);
      if (!rWallets || rWallets.length === 0) {
        rWallets = [
          {
            id: 1,
            name: 'Celo Treasury Vault',
            address: '0x84D118A43b60bd73D113c0ef08F238BE866E3A2b',
            is_active: true,
            total_received_usat: 14.00,
            tx_count: 7,
          },
          {
            id: 2,
            name: 'Merchant Settlement',
            address: '0x7262528839000000000000000000000000000001',
            is_active: true,
            total_received_usat: 8.00,
            tx_count: 4,
          }
        ];
        setLocalStore('standalone_receiving_wallets', rWallets);
      }
      if (method === 'POST') {
        const newWallet = {
          id: Date.now(),
          name: body.name || 'New Vault',
          address: body.address || '',
          is_active: true,
          total_received_usat: 0.00,
          tx_count: 0,
        };
        rWallets.push(newWallet);
        setLocalStore('standalone_receiving_wallets', rWallets);
        return { success: true, receiving_wallet: newWallet };
      }
      return { receiving_wallets: rWallets };
    }

    // 7. Wallets CRUD
    if (endpoint.startsWith('/api/wallets')) {
      let wallets = getLocalStore('standalone_wallets', null);
      if (!wallets || wallets.length === 0) {
        wallets = [
          { id: 4, name: 'Prem 1', label: 'Prem 1', address: '0x17CE4F4456a96219e3c2f26CaBf128aDe9118563', wallet_type: 'connected', balance_celo: '0.0000', balance_usat: '0.00', is_connected: true },
          { id: 7, name: 'Eswar Nayak', label: 'Eswar Nayak', address: '0x14Dbe0cB26400F81FD222Bf7f84adAAE8a6E5dA5', wallet_type: 'connected', balance_celo: '0.0000', balance_usat: '0.00', is_connected: true },
          { id: 3, name: 'Vamsi', label: 'Vamsi', address: '0x2A984Ee45AE0910A2bb9257D68754F1e8Cd9F26b', wallet_type: 'connected', balance_celo: '0.0000', balance_usat: '0.00', is_connected: true },
          { id: 5, name: 'Prem 2', label: 'Prem 2', address: '0xC7eaa8F19EDEE91deddEc928B840aDe4739590e7', wallet_type: 'connected', balance_celo: '0.0000', balance_usat: '0.00', is_connected: true },
          { id: 6, name: 'Prem 4', label: 'Prem 4', address: '0xefc1B967FA0211DDA5b618340094059b45aB52cf', wallet_type: 'connected', balance_celo: '0.0000', balance_usat: '0.00', is_connected: true },
          { id: 8, name: 'Vamsi 1', label: 'Vamsi 1', address: '0x8e53785728208d1Dd5C5D202Ebe21039C0F52294', wallet_type: 'connected', balance_celo: '0.0000', balance_usat: '0.00', is_connected: true },
          { id: 9, name: 'Vamsi 2', label: 'Vamsi 2', address: '0x32775557961F4b1AA77352F754a7899633705F7e', wallet_type: 'connected', balance_celo: '0.0000', balance_usat: '0.00', is_connected: true },
          { id: 10, name: 'Vamsi 3', label: 'Vamsi 3', address: '0x88E59baa3BaBDBAc2C444af5BCB4d158ec4130b2', wallet_type: 'connected', balance_celo: '0.0000', balance_usat: '0.00', is_connected: true },
          { id: 11, name: 'Prem 5', label: 'Prem 5', address: '0x130015a10B5e2D4FDa95ED2e28aeEb9dAF05C402', wallet_type: 'connected', balance_celo: '0.0000', balance_usat: '0.00', is_connected: true },
          { id: 12, name: 'Prem 6', label: 'Prem 6', address: '0x803314F355E544Ed5a9767Ea0E64B56B7e0D905D', wallet_type: 'connected', balance_celo: '0.0000', balance_usat: '0.00', is_connected: true },
        ];
        setLocalStore('standalone_wallets', wallets);
      }
      if (method === 'POST' && endpoint.includes('/connect')) {
        const addr = body.address;
        const wName = body.name || body.label || ('Wallet ' + (wallets.length + 1));
        const existing = wallets.find(w => w.address && w.address.toLowerCase() === (addr || '').toLowerCase());
        if (existing) {
          if (body.name && !body.name.toLowerCase().startsWith('wallet ')) {
            existing.name = body.name;
            existing.label = body.name;
          }
          setLocalStore('standalone_wallets', wallets);
          return { success: true, is_existing: true, wallet: existing };
        }
        const newW = {
          id: Date.now(),
          address: addr,
          name: wName,
          label: wName,
          wallet_type: 'connected',
          balance_celo: '0.0000',
          balance_usat: '0.00',
          is_connected: true,
        };
        wallets.push(newW);
        setLocalStore('standalone_wallets', wallets);
        return { success: true, wallet: newW };
      }
      if (method === 'POST' && endpoint.includes('/import')) {
        const wName = body.name || body.label || ('Wallet ' + (wallets.length + 1));
        const addr = body.address || '0x2A984Ee45AE0910A2bb9257D68754F1e8Cd9F26b';
        const existing = wallets.find(w => w.address && w.address.toLowerCase() === addr.toLowerCase());
        if (existing) {
          if (body.name && !body.name.toLowerCase().startsWith('wallet ')) {
            existing.name = body.name;
            existing.label = body.name;
          }
          existing.wallet_type = 'imported';
          setLocalStore('standalone_wallets', wallets);
          return { success: true, is_existing: true, wallet: existing };
        }
        const newW = {
          id: Date.now(),
          address: addr,
          name: wName,
          label: wName,
          wallet_type: 'imported',
          balance_celo: '0.0000',
          balance_usat: '0.00',
        };
        wallets.push(newW);
        setLocalStore('standalone_wallets', wallets);
        return { success: true, wallet: newW };
      }
      if (method === 'DELETE') {
        const id = parseInt(endpoint.split('/').pop(), 10);
        wallets = wallets.filter(w => w.id !== id);
        setLocalStore('standalone_wallets', wallets);
        return { success: true };
      }
      if (method === 'PATCH') {
        const id = parseInt(endpoint.split('/').pop(), 10);
        const w = wallets.find(item => item.id === id);
        if (w && body.label) w.label = body.label;
        setLocalStore('standalone_wallets', wallets);
        return { success: true, wallet: w };
      }
      return { wallets: wallets };
    }

    // 8. Payments Create, Confirm & Cancel
    if ((endpoint.includes('/payments/') && endpoint.endsWith('/cancel')) || endpoint === '/api/payments/cancel-active') {
      const parts = endpoint.split('/');
      const pid = endpoint === '/api/payments/cancel-active' ? 'active' : parts[parts.length - 2];
      const payments = getLocalStore('standalone_payments', []);
      let p = null;
      if (pid === 'active' || pid === 'current' || pid === 'pending' || pid === 'cancel-active') {
        p = payments.find(item => item.status === 'PROCESSING' || item.status === 'PENDING' || item.status === 'AWAITING_USER_SIGNATURE');
      } else {
        p = payments.find(item => item.payment_id === pid || String(item.id) === pid);
        if (!p) {
          p = payments.find(item => item.status === 'PROCESSING' || item.status === 'PENDING');
        }
      }
      if (p) {
        if ((p.status === 'SUCCESS' || p.status === 'CONFIRMED') && p.tx_hash) {
          throw new Error('Transaction was already debited on Celo blockchain. Cannot cancel.');
        }
        p.status = 'CANCELLED';
        p.error_message = 'Cancelled by user';
        setLocalStore('standalone_payments', payments);
        return { success: true, message: 'Pending transaction cancelled successfully.', status: 'CANCELLED', payment_id: p.payment_id || p.id };
      }
      return { success: true, message: 'No pending transaction found or already cancelled.', status: 'CANCELLED' };
    }

    if (endpoint.startsWith('/api/payments/create')) {
      const pid = 'pay_' + Date.now();
      const sWallets = getLocalStore('standalone_wallets', []);
      const srcW = sWallets.find(w => w.id === body.source_wallet_id || w.id === body.wallet_id || w.address === body.source_address);
      const isImported = (srcW?.wallet_type || '').toLowerCase() === 'imported' || (body.wallet_type || '').toLowerCase() === 'imported';
      const mockTx = '0x' + Array.from({length: 64}, () => Math.floor(Math.random()*16).toString(16)).join('');
      const p = {
        payment_id: pid,
        amount: parseFloat(body.amount || 2.00),
        status: isImported ? 'SUCCESS' : 'PENDING',
        from_address: body.from_address || body.source_address || (srcW ? srcW.address : ''),
        to_address: body.to_address || body.recipient_address || '',
        tx_hash: isImported ? mockTx : null,
        created_at: new Date().toISOString(),
        execution_mode: isImported ? 'SERVER_SIGN' : 'WALLET_CONNECT',
        wallet_type: isImported ? 'imported' : 'connected',
      };
      const payments = getLocalStore('standalone_payments', []);
      payments.unshift(p);
      setLocalStore('standalone_payments', payments);
      return {
        success: true,
        payment_id: pid,
        status: isImported ? 'CONFIRMED' : 'AWAITING_USER_SIGNATURE',
        tx_hash: isImported ? mockTx : undefined,
        payment: p,
        amount: p.amount,
        currency: 'USAT',
        network: 'Celo Mainnet',
        from: p.from_address,
        to: p.to_address,
        wallet_type: isImported ? 'imported' : 'connected',
      };
    }

    if (endpoint.startsWith('/api/payments/confirm-hash')) {
      const payments = getLocalStore('standalone_payments', []);
      const p = payments.find(item => item.payment_id === body.payment_id);
      if (p) {
        p.status = 'SUCCESS';
        p.tx_hash = body.tx_hash;
        p.completed_at = new Date().toISOString();
        setLocalStore('standalone_payments', payments);
      }
      return { success: true, status: 'SUCCESS', tx_hash: body.tx_hash };
    }

    if (endpoint === '/api/payments' || endpoint.startsWith('/api/admin/payments')) {
      const payments = getLocalStore('standalone_payments', []);
      return { payments: payments, total: payments.length, page: 1, total_pages: 1 };
    }

    // 9. Admin Suite
    if (endpoint.startsWith('/api/admin/login')) {
      return { success: true, admin_token: 'standalone_admin_' + Date.now() };
    }

    if (endpoint.startsWith('/api/admin/dashboard')) {
      const payments = getLocalStore('standalone_payments', []);
      const wallets = getLocalStore('standalone_wallets', []);
      const successList = payments.filter(p => p.status === 'SUCCESS' || p.status === 'CONFIRMED');
      return {
        total_payments: payments.length,
        successful_payments: successList.length,
        total_volume_usat: successList.length * 2.00,
        active_wallets: wallets.length,
        paused: false,
      };
    }

    if (endpoint.startsWith('/api/admin/funding/transactions')) {
      const sTxs = getLocalStore('standalone_funding_txs', []);
      return { transactions: sTxs };
    }

    if (endpoint.startsWith('/api/admin/funding')) {
      const sTxs = getLocalStore('standalone_funding_txs', []);
      const fundingAddr = '0x84D118A43b60bd73D113c0ef08F238BE866E3A2b';
      // Real live on-chain funding wallet reserve balance
      return {
        funding_address: fundingAddr,
        funding_wallet: fundingAddr,
        celo_balance: '4.5117',
        balance_celo: 4.5117,
        threshold: 0.005,
        subsidy_amount: '0.05 CELO',
        total_subsidies_given: sTxs.length,
        total_subsidies: sTxs.length,
        total_celo_distributed: sTxs.length * 0.05,
        estimated_subsidies_remaining: Math.floor(4.5117 / 0.05),
        transactions: sTxs,
      };
    }

    if (endpoint.startsWith('/api/admin/wallets')) {
      const wallets = getLocalStore('standalone_wallets', []);
      return { wallets: wallets };
    }

    if (endpoint.startsWith('/api/admin/settings/pause')) {
      return { success: true, paused: false, message: 'Status updated.' };
    }

    return { success: true };
    */
  }

  // --- API Client Helper ---

  async function apiRequest(endpoint, options = {}) {
    const headers = {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    };

    if (state.sessionToken) {
      headers['Authorization'] = `Bearer ${state.sessionToken}`;
    }

    let baseUrl = (window.VITE_API_URL || window.API_BASE_URL || '').replace(/\/$/, '');
    const url = (baseUrl && endpoint.startsWith('/')) ? `${baseUrl}${endpoint}` : endpoint;
    
    let resp = null;
    let isJson = false;

    try {
      resp = await fetch(url, {
        ...options,
        headers,
        credentials: 'include',
      });
      const contentType = resp.headers.get('content-type') || '';
      isJson = contentType.includes('application/json');
    } catch (fetchErr) {
      console.warn(`Backend fetch failed for ${url}:`, fetchErr);
      isJson = false;
    }

    // If backend API is not available or returned non-JSON HTML (static Firebase rewrite)
    if (!resp || !isJson) {
      throw new Error('Unable to reach the service. Please try again.');
    }

    const data = await resp.json().catch(() => ({}));

    if (!resp.ok) {
      // A request can fail independently of the durable browser session. Confirm
      // the session with the dedicated endpoint before changing the whole UI.
      // DO NOT check again for login, registration, admin login, or /auth/me.
      const isAuthAttempt = endpoint.includes('/auth/login') || endpoint.includes('/auth/register') || endpoint.includes('/admin/login');
      if (resp.status === 401 && !isAuthAttempt && endpoint !== '/api/auth/me') {
        void checkSession();
      }
      throw new Error(data.error || data.message || `Server error (${resp.status})`);
    }

    return data;
  }

  // --- Routing & View Navigation ---

  function updateAdminVisibility() {
    const isEligible = Boolean(state.user && state.user.is_admin_eligible);
    const desktopAdmin = document.getElementById('nav-desktop-admin');
    const topAdmin = document.getElementById('btn-top-admin');

    if (desktopAdmin) desktopAdmin.style.display = isEligible ? 'flex' : 'none';
    if (topAdmin) topAdmin.style.display = isEligible ? 'inline-flex' : 'none';
    renderIcons();
  }

  function navigateTo(viewId) {
    if (viewId === 'auth') {
      showAuthView();
      return;
    }

    // Admin gate check
    if (viewId === 'admin') {
      const isEligible = Boolean(state.user && state.user.is_admin_eligible);
      if (!isEligible && !state.adminToken) {
        showToast('Access restricted: Admin Portal is not available.', 'error');
        return;
      }
    }

    state.currentView = viewId;
    try {
      sessionStorage.setItem('celo_active_view', viewId);
    } catch (e) {}

    if (!state.user && viewId !== 'admin') {
      showAuthView();
      return;
    }

    // Ensure taskbars appear when logged into the dashboard/app
    document.body.classList.remove('in-auth-mode');

    // Hide all views
    document.querySelectorAll('.view-section').forEach((v) => {
      v.style.display = 'none';
    });

    // Update desktop sidebar navigation active state
    document.querySelectorAll('.desktop-sidebar .nav-item').forEach((item) => {
      item.classList.toggle('active', item.getAttribute('data-view') === viewId);
    });

    // Update mobile navigation active state
    document.querySelectorAll('.mobile-bottom-nav .bottom-tab').forEach((tab) => {
      tab.classList.toggle('active', tab.getAttribute('data-view') === viewId);
    });

    // Show target view
    const target = document.getElementById(`view-${viewId}`);
    if (target) {
      target.style.display = 'block';
    }

    // Load view data
    if (viewId === 'dashboard') {
      loadDashboardData();
    } else if (viewId === 'wallets') {
      // Always begin with the workspace chooser so older wallets remain easy to find.
      state.activeWorkspaceId = null;
      loadWallets();
    } else if (viewId === 'payments') {
      loadPaymentsHistory();
    } else if (viewId === 'profile') {
      loadProfile();
    } else if (viewId === 'admin') {
      loadAdminView();
    }

    renderIcons();
  }

  function showAuthView() {
    // Completely hide taskbars on login and register pages
    document.body.classList.add('in-auth-mode');
    document.querySelectorAll('.view-section').forEach((v) => (v.style.display = 'none'));
    const authView = document.getElementById('view-auth');
    if (authView) authView.style.display = 'block';
    renderIcons();
  }

  function switchAuthMode(mode) {
    const regCard = document.getElementById('auth-card-register');
    const logCard = document.getElementById('auth-card-login');
    const regErr = document.getElementById('reg-error-alert');
    const logErr = document.getElementById('login-error-alert');
    if (regErr) regErr.style.display = 'none';
    if (logErr) logErr.style.display = 'none';

    if (mode === 'login') {
      if (regCard) regCard.style.display = 'none';
      if (logCard) logCard.style.display = 'block';
    } else {
      if (regCard) regCard.style.display = 'block';
      if (logCard) logCard.style.display = 'none';
    }
    renderIcons();
  }

  // --- Authentication ---

  async function checkSession() {
    try {
      const data = await apiRequest('/api/auth/me');
      if (data && data.authenticated && data.user) {
        state.user = data.user;
        updateTopUserBar();
        updateAdminVisibility();
        const savedView = sessionStorage.getItem('celo_active_view') || 'dashboard';
        navigateTo(savedView);
      } else if (data && data.authenticated === false) {
        // Explicit 401 unauthenticated
        handleLogout(false);
      } else {
        showAuthView();
      }
    } catch (err) {
      console.warn('Unable to verify server session:', err);
      showAuthView();
    }
  }

  async function handleAuthRegister(event) {
    event.preventDefault();
    const nameInput = document.getElementById('reg-name');
    const mobileInput = document.getElementById('reg-mobile');
    const pwdInput = document.getElementById('reg-password');
    const confirmInput = document.getElementById('reg-confirm-password');
    const btn = document.getElementById('btn-auth-submit');
    const errAlert = document.getElementById('reg-error-alert');

    if (errAlert) errAlert.style.display = 'none';

    const name = nameInput.value.trim();
    const mobile = mobileInput.value.trim();
    const password = pwdInput.value;
    const confirm_password = confirmInput.value;

    if (!name || !mobile || !password || !confirm_password) {
      showToast('Please fill in all registration fields.', 'error');
      return;
    }

    if (password.length < 8 || !/[a-zA-Z]/.test(password) || !/\d/.test(password)) {
      const msg = 'Password must be at least 8 characters with at least one letter and one number.';
      if (errAlert) { errAlert.textContent = msg; errAlert.style.display = 'block'; }
      showToast(msg, 'error');
      return;
    }

    if (password !== confirm_password) {
      const msg = 'Passwords do not match. Please verify and try again.';
      if (errAlert) { errAlert.textContent = msg; errAlert.style.display = 'block'; }
      showToast(msg, 'error');
      return;
    }

    try {
      btn.disabled = true;
      btn.innerHTML = '<i data-lucide="loader-2" class="icon-sm" style="animation:spin 1s linear infinite;"></i> Creating Account...';
      renderIcons();

      const data = await apiRequest('/api/auth/register', {
        method: 'POST',
        body: JSON.stringify({ name, mobile, password, confirm_password }),
      });

      if (!data || !data.user) {
        throw new Error(data?.error || data?.message || 'Registration failed: unexpected server response. Please verify backend API.');
      }

      state.sessionToken = null;
      state.user = data.user;

      const displayName = state.user?.full_name || state.user?.name || 'User';
      showToast(`Welcome, ${displayName}!`, 'success');
      updateTopUserBar();
      updateAdminVisibility();
      navigateTo('dashboard');
    } catch (err) {
      if (errAlert) {
        errAlert.textContent = err.message;
        errAlert.style.display = 'block';
      }
      showToast(err.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Create Account';
    }
  }

  async function handleAuthLogin(event) {
    event.preventDefault();
    const mobileInput = document.getElementById('login-mobile');
    const pwdInput = document.getElementById('login-password');
    const btn = document.getElementById('btn-login-submit');
    const errAlert = document.getElementById('login-error-alert');

    if (errAlert) errAlert.style.display = 'none';

    const mobile = mobileInput.value.trim();
    const password = pwdInput.value;

    if (!mobile || !password) {
      showToast('Please enter both mobile number and password.', 'error');
      return;
    }

    try {
      btn.disabled = true;
      btn.innerHTML = '<i data-lucide="loader-2" class="icon-sm" style="animation:spin 1s linear infinite;"></i> Logging in...';
      renderIcons();

      const data = await apiRequest('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ mobile, password }),
      });

      if (!data || !data.user) {
        throw new Error(data?.error || data?.message || 'Login failed: unexpected server response. Please verify backend API.');
      }

      state.sessionToken = null;
      state.user = data.user;

      const displayName = state.user?.full_name || state.user?.name || 'User';
      showToast(`Welcome back, ${displayName}!`, 'success');
      updateTopUserBar();
      updateAdminVisibility();
      navigateTo('dashboard');
    } catch (err) {
      let displayMsg = err.message;
      if (err.message && err.message.toLowerCase().includes('invalid mobile number or password')) {
        displayMsg = 'Invalid mobile number or password. If you have not created an account on this server yet, please click "Create Account" below.';
      }
      if (errAlert) {
        errAlert.textContent = displayMsg;
        errAlert.style.display = 'block';
      }
      showToast(displayMsg, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Login';
    }
  }

  async function handleChangePassword(event) {
    event.preventDefault();
    const currInput = document.getElementById('curr-password');
    const newInput = document.getElementById('new-password');
    const confirmInput = document.getElementById('confirm-new-password');
    const btn = document.getElementById('btn-change-pwd');

    const current_password = currInput.value;
    const new_password = newInput.value;
    const confirm_password = confirmInput.value;

    if (!current_password || !new_password || !confirm_password) {
      showToast('Please fill out all password fields.', 'error');
      return;
    }

    if (new_password.length < 8 || !/[a-zA-Z]/.test(new_password) || !/\d/.test(new_password)) {
      showToast('New password must be at least 8 characters with letters and numbers.', 'error');
      return;
    }

    if (new_password !== confirm_password) {
      showToast('New passwords do not match.', 'error');
      return;
    }

    try {
      btn.disabled = true;
      btn.innerHTML = '<i data-lucide="loader-2" class="icon-sm" style="animation:spin 1s linear infinite;"></i> Updating...';
      renderIcons();

      await apiRequest('/api/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({ current_password, new_password, confirm_password }),
      });

      showToast('Password updated successfully!', 'success');
      currInput.value = '';
      newInput.value = '';
      confirmInput.value = '';
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      btn.disabled = false;
      btn.innerHTML = '<i data-lucide="check" class="icon-sm"></i> <span>Update Password</span>';
      renderIcons();
    }
  }

  async function handleLogout(notifyServer = true) {
    if (notifyServer) {
      try {
        await apiRequest('/api/auth/logout', { method: 'POST' }).catch(() => {});
      } catch (e) {}
    }
    state.sessionToken = null;
    state.user = null;
    state.wallets = [];
    try { sessionStorage.removeItem('celo_active_view'); } catch (e) {}
    closeTopUserDropdown();
    updateTopUserBar();
    updateAdminVisibility();
    switchAuthMode('login');
    showAuthView();
    if (notifyServer) {
      showToast('Signed out successfully.', 'info');
    }
  }

  function updateTopUserBar() {
    const userContainer = document.getElementById('top-user-container');
    const userPill = document.getElementById('top-user-pill');
    const userName = document.getElementById('top-user-name');
    if (state.user) {
      if (userContainer) userContainer.style.display = 'inline-block';
      if (userPill) userPill.style.display = 'inline-flex';
      if (userName) userName.textContent = state.user.full_name || state.user.name || 'User';
    } else {
      if (userContainer) userContainer.style.display = 'none';
      if (userPill) userPill.style.display = 'none';
      closeTopUserDropdown();
    }
  }

  function toggleTopUserDropdown(event) {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    const dropdown = document.getElementById('top-user-dropdown');
    if (!dropdown) return;
    const isShown = dropdown.style.display === 'block';
    dropdown.style.display = isShown ? 'none' : 'block';
    renderIcons();
  }

  function closeTopUserDropdown() {
    const dropdown = document.getElementById('top-user-dropdown');
    if (dropdown) dropdown.style.display = 'none';
  }

  // --- Dashboard & Dynamic USDT Calculation ---

  async function loadDashboardData() {
    const refreshBtn = document.getElementById('btn-dash-refresh');
    if (refreshBtn) refreshBtn.classList.add('is-spinning');

    try {
      if (state.user) {
        const dashUser = document.getElementById('dash-user-name');
        if (dashUser) dashUser.textContent = state.user.full_name || state.user.name || 'User';
      }
      await Promise.all([loadWallets(), loadReceivingWallets(), loadSavedRecipients(), loadRewardPoolStatus()]);
    } finally {
      if (refreshBtn) refreshBtn.classList.remove('is-spinning');
    }
  }

  async function loadReceivingWallets() {
    try {
      const data = await apiRequest('/api/receiving-wallets');
      state.receivingWallets = data.receiving_wallets || [];
    } catch (err) {
      console.warn('Could not load receiving destinations:', err);
    }
  }

  async function loadRewardPoolStatus() {
    const balance = document.getElementById('dash-reward-pool-balance');
    if (!balance) return;
    try {
      const data = await apiRequest('/api/rewards/status');
      const amount = Number.parseFloat(data.balance_usat);
      balance.textContent = Number.isFinite(amount) ? amount.toFixed(2) : 'Unavailable';
    } catch (err) {
      // Never turn an RPC failure into a false zero balance.
      balance.textContent = 'Unavailable';
    }
  }

  async function loadSavedRecipients() {
    try {
      const data = await apiRequest('/api/recipients');
      state.savedRecipients = data.recipients || [];
      renderSavedRecipients();
    } catch (err) {
      // Saved recipients are optional UI data; do not interrupt the payment form.
      console.warn('Could not load saved recipients:', err);
    }
  }

  function renderSavedRecipients() {
    const select = document.getElementById('select-saved-recipient');
    if (!select) return;
    const selectedAddress = select.value;
    const recipients = state.savedRecipients || [];
    select.innerHTML = '<option value="">Saved recipients</option>' + recipients.map((recipient) =>
      `<option value="${escapeHtml(recipient.address)}">${escapeHtml(recipient.name)} · ${escapeHtml(formatShortAddress(recipient.address))}</option>`
    ).join('');
    if (recipients.some((recipient) => recipient.address === selectedAddress)) {
      select.value = selectedAddress;
    }
  }

  async function loadWallets() {
    try {
      await loadWorkspaces();
      const data = await apiRequest('/api/wallets');
      let wallets = data.wallets || [];

      // Alphabetical sorting of wallets by user-assigned name
      wallets.sort((a, b) => {
        const nameA = (a.name || a.label || '').trim().toLowerCase();
        const nameB = (b.name || b.label || '').trim().toLowerCase();
        return nameA.localeCompare(nameB, undefined, { numeric: true, sensitivity: 'base' });
      });

      state.wallets = wallets;

      // Render immediately with instant 0-latency feedback!
      renderWalletsSelect();
      renderWalletsList();
      renderIcons();

      // Balances come from the server-side Celo client. This avoids treating a
      // browser RPC/CORS failure as a zero balance and keeps all workspaces in sync.
      updateWalletBalanceDisplays();
      return true;
    } catch (err) {
      showToast('Failed to load wallets: ' + err.message, 'error');
      return false;
    }
  }

  async function refreshAllWalletBalances() {
    const button = document.getElementById('btn-wallets-refresh');
    if (button) button.classList.add('is-spinning');
    try {
      const refreshed = await loadWallets();
      if (refreshed) showToast('Workspace balances refreshed.', 'success');
    } catch (err) {
      showToast('Unable to refresh blockchain balances. Please try again shortly.', 'error');
    } finally {
      if (button) button.classList.remove('is-spinning');
    }
  }

  async function loadWorkspaces() {
    const data = await apiRequest('/api/workspaces');
    state.workspaces = data.workspaces || [];
    const activeStillExists = state.workspaces.some((workspace) => String(workspace.id) === String(state.activeWorkspaceId));
    if (!activeStillExists) {
      state.activeWorkspaceId = null;
    }
    renderWorkspaceSelect();
  }

  function renderWorkspaceSelect() {
    const importSelect = document.getElementById('import-wallet-workspace');
    const workspaceList = document.getElementById('wallet-workspace-list');
    const workspaceBar = document.querySelector('.wallet-workspace-bar');
    const addWalletButton = document.getElementById('btn-wallet-add');
    const options = (state.workspaces || []).map((workspace) =>
      `<option value="${escapeHtml(workspace.id)}">${escapeHtml(workspace.name)}${workspace.wallet_count !== undefined ? ` (${workspace.wallet_count})` : ''}</option>`
    ).join('');
    [importSelect].forEach((element) => {
      if (!element) return;
      const previous = element.value;
      element.innerHTML = options || '<option value="">Personal Workspace</option>';
      element.value = String(state.activeWorkspaceId || previous || '');
      if (!element.value && element.options.length) element.selectedIndex = 0;
    });
    if (workspaceList) {
      const activeWorkspace = (state.workspaces || []).find((workspace) => String(workspace.id) === String(state.activeWorkspaceId));
      if (activeWorkspace) {
        workspaceBar?.classList.remove('is-chooser');
        workspaceList.innerHTML = `<button type="button" class="workspace-back" onclick="app.backToWorkspaces()"><i data-lucide="arrow-left" class="icon-sm"></i> All Workspaces</button>
          <div class="workspace-current"><span>${escapeHtml(activeWorkspace.name)}</span><small>${Number(activeWorkspace.wallet_count || 0)} wallets</small><button type="button" class="btn-copy" onclick="app.openRenameWorkspaceModal(${activeWorkspace.id})" title="Rename workspace"><i data-lucide="pencil" class="icon-xs"></i></button></div>`;
        if (addWalletButton) addWalletButton.style.display = 'inline-flex';
      } else {
        workspaceBar?.classList.add('is-chooser');
        workspaceList.innerHTML = (state.workspaces || []).map((workspace) => {
          const workspaceWallets = (state.wallets || []).filter((wallet) => String(wallet.workspace_id) === String(workspace.id));
          const balance = workspaceWallets.reduce((sum, wallet) => sum + Number.parseFloat(wallet.usat_balance || 0), 0);
          return `<button type="button" class="workspace-choice" onclick="app.selectWalletWorkspace('${escapeHtml(workspace.id)}')">
            <i data-lucide="wallet" class="icon-sm"></i><span>${escapeHtml(workspace.name)}</span><strong class="${balance > 0 ? 'balance-positive' : 'balance-zero'}">$${balance.toFixed(2)}</strong><small>${Number(workspace.wallet_count || 0)} wallets · Enter</small>
          </button>`;
        }).join('') + `<button type="button" class="workspace-choice workspace-create-choice" onclick="app.openCreateWorkspaceModal()">
          <i data-lucide="folder-plus" class="icon-sm"></i><span>New Workspace</span><strong>+</strong><small>Create a separate wallet space</small>
        </button>`;
        if (addWalletButton) addWalletButton.style.display = 'none';
      }
      renderIcons();
    }
  }

  function selectWalletWorkspace(workspaceId) {
    state.activeWorkspaceId = workspaceId || null;
    state.expandedWalletId = null;
    renderWorkspaceSelect();
    renderWalletsList();
  }

  function backToWorkspaces() {
    state.activeWorkspaceId = null;
    state.expandedWalletId = null;
    renderWorkspaceSelect();
    renderWalletsList();
  }

  function openCreateWorkspaceModal() {
    const input = document.getElementById('workspace-name');
    if (input) input.value = '';
    openModal('modal-create-workspace');
    setTimeout(() => input?.focus(), 0);
  }

  async function submitCreateWorkspace(event) {
    event.preventDefault();
    const input = document.getElementById('workspace-name');
    const name = input?.value.trim();
    if (!name) return;
    try {
      const result = await apiRequest('/api/workspaces', {
        method: 'POST',
        body: JSON.stringify({ name }),
      });
      await loadWorkspaces();
      state.activeWorkspaceId = null;
      renderWorkspaceSelect();
      renderWalletsList();
      closeModal('modal-create-workspace');
      showToast(`${name} workspace created.`, 'success');
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  function openRenameWorkspaceModal(workspaceId) {
    const workspace = (state.workspaces || []).find((item) => String(item.id) === String(workspaceId));
    if (!workspace) return;
    const idInput = document.getElementById('rename-workspace-id');
    const nameInput = document.getElementById('rename-workspace-name');
    if (idInput) idInput.value = workspace.id;
    if (nameInput) nameInput.value = workspace.name;
    openModal('modal-rename-workspace');
    setTimeout(() => nameInput?.focus(), 0);
  }

  async function submitRenameWorkspace(event) {
    event.preventDefault();
    const workspaceId = document.getElementById('rename-workspace-id')?.value;
    const name = document.getElementById('rename-workspace-name')?.value.trim();
    if (!workspaceId || !name) return;
    try {
      await apiRequest(`/api/workspaces/${workspaceId}`, {
        method: 'PATCH',
        body: JSON.stringify({ name }),
      });
      await loadWorkspaces();
      renderWalletsList();
      closeModal('modal-rename-workspace');
      showToast('Workspace renamed.', 'success');
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  function updateWalletBalanceDisplays() {
    const totalUsdt = state.wallets.reduce((sum, wallet) => sum + parseFloat(wallet.usat_balance || 0), 0);
    const totalUsdtFormatted = `$${totalUsdt.toFixed(2)}`;
    const dashTotal = document.getElementById('dash-total-usdt-balance');
    const dashCount = document.getElementById('dash-wallets-count');
    const walSummaryUsdt = document.getElementById('wallets-summary-usdt');
    const walSummaryCount = document.getElementById('wallets-summary-count');
    if (dashTotal) dashTotal.textContent = totalUsdtFormatted;
    if (dashCount) dashCount.textContent = state.wallets.length;
    if (walSummaryUsdt) walSummaryUsdt.textContent = totalUsdtFormatted;
    if (walSummaryCount) walSummaryCount.textContent = state.wallets.length;
    setBalanceTone(dashTotal, totalUsdt);
    setBalanceTone(walSummaryUsdt, totalUsdt);
  }

  function toggleWalletDropdownCustom(event) {
    if (event) {
      event.stopPropagation();
    }
    const dropdown = document.getElementById('custom-wallet-dropdown');
    const trigger = document.getElementById('custom-wallet-trigger');
    const capsule = document.getElementById('dash-from-wallet-capsule');
    if (!dropdown || !trigger) return;
    const isOpen = dropdown.classList.contains('is-open');
    if (isOpen) {
      dropdown.classList.remove('is-open');
      trigger.classList.remove('is-open');
      if (capsule) capsule.classList.remove('dropdown-open');
    } else {
      dropdown.classList.add('is-open');
      trigger.classList.add('is-open');
      if (capsule) capsule.classList.add('dropdown-open');
    }
  }

  function closeCustomWalletDropdown() {
    const dropdown = document.getElementById('custom-wallet-dropdown');
    const trigger = document.getElementById('custom-wallet-trigger');
    const capsule = document.getElementById('dash-from-wallet-capsule');
    if (dropdown) dropdown.classList.remove('is-open');
    if (trigger) trigger.classList.remove('is-open');
    if (capsule) capsule.classList.remove('dropdown-open');
  }

  function selectCustomWallet(walletId, event) {
    if (event) {
      event.stopPropagation();
    }
    state.selectedWalletId = walletId;
    const select = document.getElementById('select-send-wallet');
    if (select) {
      select.value = String(walletId);
    }
    handleWalletSelected(walletId);
    closeCustomWalletDropdown();
  }

  function selectDashboardWorkspace(workspaceId) {
    state.dashboardWorkspaceId = workspaceId || null;
    state.selectedWalletId = null;
    renderWalletsSelect();
  }

  function renderWalletsSelect() {
    const select = document.getElementById('select-send-wallet');
    const addWalletBtn = document.getElementById('btn-dash-add-wallet');
    const customDropdown = document.getElementById('custom-wallet-dropdown');
    const dashboardWorkspaceSelect = document.getElementById('select-dashboard-workspace');
    const customContainer = document.getElementById('custom-wallet-select-container');
    const workspaceWallets = state.dashboardWorkspaceId
      ? state.wallets.filter((wallet) => String(wallet.workspace_id) === String(state.dashboardWorkspaceId))
      : [];

    if (dashboardWorkspaceSelect) {
      const previous = dashboardWorkspaceSelect.value;
      dashboardWorkspaceSelect.innerHTML = '<option value="">Choose a workspace first</option>' + (state.workspaces || []).map((workspace) =>
        `<option value="${escapeHtml(workspace.id)}">${escapeHtml(workspace.name)} (${Number(workspace.wallet_count || 0)} wallets)</option>`
      ).join('');
      dashboardWorkspaceSelect.value = state.dashboardWorkspaceId || previous || '';
    }
    if (customContainer) customContainer.style.display = state.dashboardWorkspaceId ? 'block' : 'none';

    // Hide "+ Add Wallet" button on dashboard if user already has wallets
    if (addWalletBtn) {
      addWalletBtn.style.display = state.dashboardWorkspaceId && workspaceWallets.length === 0 ? 'inline-flex' : 'none';
    }

    // Sort wallets alphabetically by name
    state.wallets.sort((a, b) => {
      const nameA = (a.name || a.label || '').trim().toLowerCase();
      const nameB = (b.name || b.label || '').trim().toLowerCase();
      return nameA.localeCompare(nameB, undefined, { numeric: true, sensitivity: 'base' });
    });

    if (select) {
      select.innerHTML = '';
      if (workspaceWallets.length === 0) {
        select.innerHTML = '<option value="">-- No wallet in this workspace --</option>';
      } else {
        workspaceWallets.forEach((w) => {
          const opt = document.createElement('option');
          opt.value = String(w.id);
          const name = w.name || w.label || 'My Wallet';
          const shortAddr = formatShortAddress(w.address);
          const usdt = parseFloat(w.usat_balance || 0).toFixed(2);
          opt.textContent = `${name} (${shortAddr}) — Available: $${usdt}`;
          select.appendChild(opt);
        });
      }
    }

    if (state.selectedWalletId && workspaceWallets.some((w) => String(w.id) === String(state.selectedWalletId))) {
      if (select) select.value = String(state.selectedWalletId);
    } else {
      state.selectedWalletId = null;
    }

    // Populate custom website-themed dropdown menu
    if (customDropdown) {
      if (!state.dashboardWorkspaceId) {
        customDropdown.innerHTML = '<div style="padding:14px; text-align:center; color:var(--text-muted); font-size:12px;">Choose a workspace first.</div>';
      } else if (workspaceWallets.length === 0) {
        customDropdown.innerHTML = `
          <div style="padding:14px; text-align:center; color:var(--text-muted); font-size:12px;">
            No wallets found. Click "+ Add Wallet" to connect or import one.
          </div>
        `;
      } else {
        let optionsHtml = '';
        workspaceWallets.forEach((w) => {
          const isSelected = String(state.selectedWalletId) === String(w.id);
          const wType = (w.wallet_type || w.type || 'connected').toLowerCase();
          const name = w.name || w.label || 'My Wallet';
          const usdt = parseFloat(w.usat_balance || 0).toFixed(2);
          const celo = parseFloat(w.celo_balance || 0).toFixed(4);

          optionsHtml += `
            <div class="wallet-option-item ${isSelected ? 'selected' : ''}" 
                 data-wallet-id="${w.id}" 
                 onclick="app.selectCustomWallet('${w.id}', event)">
              <div class="wallet-option-info">
                <div class="wallet-option-top">
                  <span style="font-weight:700; color:var(--text-primary); font-size:14px;">${escapeHtml(name)}</span>
                  <span class="badge ${wType === 'connected' ? 'badge-blue' : 'badge-green'}" style="font-size:9px; padding:1px 6px;">
                    ${wType === 'connected' ? 'Connected' : 'Imported'}
                  </span>
                </div>
                <div class="wallet-option-bottom">
                  <span class="code-address" style="font-size:11px;">${formatShortAddress(w.address)}</span>
                  <span style="color:var(--text-muted);">•</span>
                  <span style="color:var(--celo-green-dark); font-weight:600; display:inline-flex; align-items:center; gap:2px;">
                    <i data-lucide="fuel" class="icon-xs"></i> ${celo} CELO
                  </span>
                  <span style="color:var(--text-muted);">•</span>
                  <span class="wallet-option-balance" style="font-weight:600;">$${usdt} USAT</span>
                </div>
              </div>
              <div class="wallet-option-check">
                <i data-lucide="check" class="icon-sm"></i>
              </div>
            </div>
          `;
        });
        customDropdown.innerHTML = optionsHtml;
      }
    }

    handleWalletSelected();
  }

  function handleWalletSelected(overrideId) {
    const select = document.getElementById('select-send-wallet');
    const availUsdtEl = document.getElementById('dash-avail-usdt');
    const availCeloEl = document.getElementById('dash-avail-celo');

    // Trigger box elements
    const triggerAddr = document.getElementById('trigger-wallet-address');
    const triggerBadge = document.getElementById('trigger-wallet-badge');
    const triggerName = document.getElementById('trigger-wallet-name');
    const triggerSubtext = document.getElementById('trigger-wallet-subtext');
    const triggerGas = document.getElementById('trigger-wallet-gas');
    const triggerGasText = document.getElementById('trigger-wallet-gas-text');
    const triggerCopyBtn = document.getElementById('btn-trigger-copy-address');

    const walletId = (overrideId !== undefined && overrideId !== null)
      ? overrideId
      : (state.selectedWalletId || select?.value);

    const wallet = state.wallets.find((w) => String(w.id) === String(walletId));

    if (!wallet) {
      state.selectedWalletId = null;
      if (availUsdtEl) availUsdtEl.textContent = '0.00';
      if (availCeloEl) availCeloEl.textContent = '0.0000';
      if (triggerAddr) triggerAddr.textContent = 'Select sending wallet';
      if (triggerBadge) triggerBadge.style.display = 'none';
      if (triggerName) triggerName.textContent = '';
      if (triggerSubtext) triggerSubtext.style.display = 'none';
      if (triggerGas) triggerGas.style.display = 'none';
      if (triggerCopyBtn) triggerCopyBtn.style.display = 'none';
      setBalanceTone(availUsdtEl, 0);
      renderIcons();
      return;
    }

    state.selectedWalletId = wallet.id;
    if (select) select.value = String(wallet.id);

    const usat = parseFloat(wallet.usat_balance || 0);
    const celo = parseFloat(wallet.celo_balance || 0);
    const shortAddr = formatShortAddress(wallet.address);
    const wType = (wallet.wallet_type || wallet.type || 'connected').toLowerCase();
    const wName = wallet.name || wallet.label || 'My Wallet';

    if (availUsdtEl) availUsdtEl.textContent = usat.toFixed(2);
    if (availCeloEl) availCeloEl.textContent = celo.toFixed(4);
    setBalanceTone(availUsdtEl, usat);

    // Update Custom Trigger Display: Top displays custom wallet name, below displays short address, copy button & gas fee
    if (triggerAddr) {
      triggerAddr.textContent = wName;
      triggerAddr.title = `${wName} (${wallet.address})`;
    }
    if (triggerBadge) {
      triggerBadge.style.display = 'inline-flex';
      triggerBadge.className = `badge ${wType === 'connected' ? 'badge-blue' : 'badge-green'}`;
      triggerBadge.textContent = wType === 'connected' ? 'Connected' : 'Imported';
    }
    if (triggerCopyBtn) triggerCopyBtn.style.display = 'inline-flex';
    if (triggerSubtext) triggerSubtext.style.display = 'flex';
    if (triggerName) triggerName.textContent = shortAddr;
    if (triggerGas) {
      triggerGas.style.display = 'inline-flex';
      if (triggerGasText) triggerGasText.textContent = `${celo.toFixed(4)} CELO Gas • $${usat.toFixed(2)} USAT`;
    }

    // Synchronize selected highlight in custom dropdown
    document.querySelectorAll('.wallet-option-item').forEach((item) => {
      const itemWalletId = item.getAttribute('data-wallet-id');
      const isMatch = String(itemWalletId) === String(wallet.id);
      item.classList.toggle('selected', Boolean(isMatch));
    });

    renderIcons();
  }

  function setMaxAmount() {
    setAmountPercent(100);
  }

  function setAmountPercent(percent) {
    const wallet = state.wallets.find((w) => w.id === state.selectedWalletId);
    if (!wallet) {
      showToast('Please select a sending wallet first.', 'warning');
      return;
    }
    const usat = parseFloat(wallet.usat_balance || 0);
    if (usat <= 0) {
      showToast('Available wallet USDT balance is 0.00.', 'warning');
      return;
    }
    const amount = (usat * (percent / 100));
    const input = document.getElementById('input-transfer-amount');
    if (input) {
      input.value = percent === 100 ? usat.toFixed(2) : amount.toFixed(2);
      handleAmountChanged();
    }
  }

  function copySelectedAddress() {
    const wallet = state.wallets.find((w) => w.id === state.selectedWalletId);
    if (wallet?.address) {
      copyAddress(wallet.address);
    }
  }

  async function pasteRecipientAddress() {
    try {
      if (!navigator.clipboard || !navigator.clipboard.readText) {
        showToast('Please paste the address into the recipient box.', 'info');
        document.getElementById('input-recipient-address')?.focus();
        return;
      }
      const text = await navigator.clipboard.readText();
      const input = document.getElementById('input-recipient-address');
      if (input && text) {
        input.value = text.trim();
        handleRecipientChanged();
        showToast('Address pasted from clipboard', 'success');
      }
    } catch (e) {
      showToast('Please paste the address into the recipient box.', 'info');
      document.getElementById('input-recipient-address')?.focus();
    }
  }

  function handleQuickRecipientSelected() {
    const select = document.getElementById('select-quick-recipient');
    const input = document.getElementById('input-recipient-address');
    if (!select || !input) return;
    const val = select.value;
    if (val) {
      input.value = val;
      handleRecipientChanged();
    }
  }

  function handleSavedRecipientSelected() {
    const select = document.getElementById('select-saved-recipient');
    const input = document.getElementById('input-recipient-address');
    if (!select || !input || !select.value) return;
    input.value = select.value;
    handleRecipientChanged();
  }

  function openSaveRecipientModal() {
    const addressInput = document.getElementById('input-recipient-address');
    const rawAddress = addressInput?.value?.trim() || '';
    if (!isValidCeloAddress(rawAddress)) {
      showToast('Enter a valid recipient address before saving it.', 'warning');
      addressInput?.focus();
      return;
    }
    const nameInput = document.getElementById('saved-recipient-name');
    const savedAddressInput = document.getElementById('saved-recipient-address');
    if (nameInput) nameInput.value = '';
    if (savedAddressInput) savedAddressInput.value = rawAddress;
    openModal('modal-save-recipient');
    nameInput?.focus();
  }

  async function submitSaveRecipient(event) {
    event.preventDefault();
    const nameInput = document.getElementById('saved-recipient-name');
    const addressInput = document.getElementById('saved-recipient-address');
    const name = nameInput?.value?.trim() || '';
    const address = addressInput?.value?.trim() || '';
    if (!name || !isValidCeloAddress(address)) {
      showToast('Enter a name and valid Celo address.', 'error');
      return;
    }
    try {
      await apiRequest('/api/recipients', {
        method: 'POST',
        body: JSON.stringify({ name, address }),
      });
      await loadSavedRecipients();
      const select = document.getElementById('select-saved-recipient');
      if (select) select.value = address;
      closeModal('modal-save-recipient');
      showToast(`${name} saved as a recipient.`, 'success');
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  function handleAmountChanged() {
    const input = document.getElementById('input-transfer-amount');
    const displayEl = document.getElementById('dash-display-amount');
    const val = parseFloat(input?.value || 0);
    if (displayEl) {
      displayEl.textContent = isNaN(val) || val <= 0 ? '0.00' : val.toFixed(2);
    }
  }

  function isValidCeloAddress(addr) {
    if (!addr || typeof addr !== 'string') return false;
    return /^0x[a-fA-F0-9]{40}$/.test(addr.trim());
  }

  function handleRecipientChanged() {
    const input = document.getElementById('input-recipient-address');
    const badge = document.getElementById('recipient-format-badge');
    const hint = document.getElementById('recipient-validation-hint');
    if (!input) return;

    const addr = input.value.trim();
    if (!addr) {
      if (badge) {
        badge.style.display = 'none';
        badge.textContent = '';
      }
      if (hint) {
        hint.textContent = 'Enter a 42-character Celo/EVM address starting with 0x.';
        hint.style.color = 'var(--text-muted)';
      }
      return;
    }

    if (isValidCeloAddress(addr)) {
      if (badge) {
        badge.style.display = 'inline-block';
        badge.className = 'badge badge-green';
        badge.textContent = 'Valid EVM Address';
      }
      if (hint) {
        hint.textContent = 'Recipient address format is valid.';
        hint.style.color = 'var(--celo-green-dark)';
      }
    } else {
      if (badge) {
        badge.style.display = 'inline-block';
        badge.className = 'badge badge-warning';
        badge.textContent = 'Invalid Format';
      }
      if (hint) {
        hint.textContent = 'Must start with 0x followed by 40 hex characters.';
        hint.style.color = 'var(--danger)';
      }
    }
  }

  // --- Payment Execution Flow ---

  function openPaymentConfirmation() {
    const select = document.getElementById('select-send-wallet');
    let selectedId = select && select.value ? parseInt(select.value, 10) : state.selectedWalletId;
    let wallet = state.wallets.find((w) => String(w.id) === String(selectedId || state.selectedWalletId));

    if (!wallet) {
      showToast('Choose a workspace and a sending wallet before continuing.', 'warning');
      return;
    }

    const amountInput = document.getElementById('input-transfer-amount');
    const amountVal = parseFloat(amountInput?.value || 0);
    if (isNaN(amountVal) || amountVal <= 0) {
      showToast('Please enter a valid transfer amount greater than zero.', 'error');
      amountInput?.focus();
      return;
    }

    const usatBal = parseFloat(wallet.usat_balance || 0);
    if (amountVal > usatBal) {
      showToast(`Amount (${amountVal.toFixed(2)} USDT) exceeds ${wallet.name || 'wallet'} balance (${usatBal.toFixed(2)} USDT).`, 'error');
      amountInput?.focus();
      return;
    }

    const recipientInput = document.getElementById('input-recipient-address');
    const recipientAddr = (recipientInput?.value || '').trim();
    if (!isValidCeloAddress(recipientAddr)) {
      showToast('Please enter a valid 42-character Celo recipient address starting with 0x.', 'error');
      recipientInput?.focus();
      return;
    }

    if (recipientAddr.toLowerCase() === wallet.address.toLowerCase()) {
      showToast('Recipient address cannot be the same as your sending wallet address.', 'error');
      recipientInput?.focus();
      return;
    }

    // Populate confirmation modal
    const amountDisp = document.getElementById('confirm-amount-display');
    const fromDisp = document.getElementById('confirm-from-display');
    const toDisp = document.getElementById('confirm-to-display');

    if (amountDisp) amountDisp.textContent = `${amountVal.toFixed(2)} USDT`;
    if (fromDisp) fromDisp.textContent = `${wallet.name || wallet.label} (${formatShortAddress(wallet.address)})`;
    if (toDisp) toDisp.textContent = recipientAddr;

    // Show private key input section if wallet is not yet imported
    const pkSec = document.getElementById('confirm-pk-section');
    if (pkSec) pkSec.style.display = 'none';

    state.pendingPayment = {
      wallet,
      amount: amountVal,
      recipient: recipientAddr,
    };

    openModal('modal-payment-confirm');
    renderIcons();
  }

  async function confirmAndExecutePayment() {
    if (!state.pendingPayment || state.isSubmitting) return;

    const { wallet, amount, recipient } = state.pendingPayment;
    const wType = (wallet.wallet_type || wallet.type || '').toLowerCase();
    const isAlreadyImported = (wType === 'imported' || wType === 'imported_wallet');

    closeModal('modal-payment-confirm');
    state.isSubmitting = true;

    openPaymentModal();
    setPaymentStep(1, 'Verifying balances on Celo Mainnet...');

    let paymentConfirmed = false;
    try {
      const res = await apiRequest('/api/payments/create', {
        method: 'POST',
        body: JSON.stringify({
          source_wallet_id: wallet.id,
          wallet_id: wallet.id,
          amount: amount,
          recipient_address: recipient,
          to_address: recipient,
          source_address: wallet.address,
        }),
      });

      const payment = res.payment || res;
      const paymentId = res.payment_id || payment.id || payment.payment_id;
      state.activePayment = payment;

      if (res.celo_funded || payment.celo_funded) {
        setPaymentStep(2, '0.05 CELO Gas Subsidy sent! Gas confirmed on Celo.');
      } else {
        setPaymentStep(2, 'CELO gas balance sufficient. Proceeding to transfer...');
      }

      const isServerBroadcast = (
        isAlreadyImported ||
        Boolean(res.tx_hash) ||
        Boolean(payment.tx_hash) ||
        res.status === 'CONFIRMED' ||
        res.status === 'SUCCESS' ||
        payment.status === 'CONFIRMED' ||
        payment.status === 'SUCCESS'
      );

      if (isServerBroadcast) {
        setPaymentStep(3, `Broadcasting ${amount.toFixed(2)} USDT on Celo Mainnet...`);

        const confirmedTx = res.tx_hash || payment.tx_hash;
        if (confirmedTx) {
          finishPaymentSuccess(confirmedTx, wallet, recipient, amount);
          paymentConfirmed = true;
        } else {
          throw new Error(payment.error_message || res.error || 'Payment execution failed.');
        }
      } else {
        // Only attempt in-browser signing if an actual injected Web3 provider exists
        const provider = Web3Module.getProvider();
        if (provider) {
          setPaymentStep(3, `Please confirm the ${amount.toFixed(2)} USDT transfer in your connected wallet...`);
          const txHash = await Web3Module.sendUSATPayment(wallet.address, recipient, res.tx_params);

          setPaymentStep(3, 'Transaction broadcast! Confirming with backend...');
          await apiRequest('/api/payments/confirm-hash', {
            method: 'POST',
            body: JSON.stringify({
              payment_id: paymentId,
              tx_hash: txHash,
            }),
          });

          finishPaymentSuccess(txHash, wallet, recipient, amount);
          paymentConfirmed = true;
        } else {
          // No provider and no private key: prompt directly for the key
          closeModal('modal-payment-progress');
          openModal('modal-payment-confirm');
          const pkSec = document.getElementById('confirm-pk-section');
          if (pkSec) pkSec.style.display = 'block';
          showToast('Please enter the private key for this wallet to sign and send.', 'warning');
          document.getElementById('confirm-signing-pk')?.focus();
          return;
        }
      }
    } catch (err) {
      if (state.activePayment && (state.activePayment.payment_id || state.activePayment.id)) {
        const payId = state.activePayment.payment_id || state.activePayment.id;
        apiRequest(`/api/payments/${payId}/cancel`, { method: 'POST' }).catch(() => {});
      }
      setPaymentFailed(err.message || 'Payment failed.');
    } finally {
      state.isSubmitting = false;
      state.pendingPayment = null;
      if (!paymentConfirmed) loadWallets();
    }
  }

  function initiatePayment() {
    openPaymentConfirmation();
  }

  function openPaymentModal() {
    const modal = document.getElementById('modal-payment-progress');
    const headerTitle = document.getElementById('pay-modal-header-title');
    const stepInd = document.getElementById('pay-step-indicator');
    const receiptCard = document.getElementById('pay-receipt-card');
    const actionBtn = document.getElementById('btn-pay-modal-action');
    const cancelBtn = document.getElementById('btn-pay-modal-cancel');
    const linkCont = document.getElementById('pay-tx-link-container');
    const closeBtn = document.getElementById('btn-close-pay-modal');

    if (modal) modal.classList.add('active');
    if (headerTitle) headerTitle.textContent = 'Processing Payment';
    if (stepInd) stepInd.style.display = 'flex';
    if (receiptCard) receiptCard.style.display = 'none';
    if (actionBtn) actionBtn.style.display = 'none';
    if (cancelBtn) cancelBtn.style.display = 'none';
    if (linkCont) linkCont.style.display = 'none';
    if (closeBtn) closeBtn.style.display = 'none';

    for (let i = 1; i <= 4; i++) {
      const step = document.getElementById(`step-${i}`);
      if (step) step.className = 'step-item';
    }
  }

  function setPaymentStep(stepNumber, description) {
    for (let i = 1; i <= 4; i++) {
      const step = document.getElementById(`step-${i}`);
      if (step) {
        step.className = i <= stepNumber ? 'step-item active' : 'step-item';
      }
    }

    const titleEl = document.getElementById('pay-progress-title');
    const descEl = document.getElementById('pay-progress-desc');
    const iconCont = document.getElementById('pay-progress-icon-container');

    if (iconCont) {
      iconCont.innerHTML = '<i data-lucide="loader-2" class="icon-lg" style="animation:spin 1s linear infinite; color:var(--celo-green);"></i>';
    }

    if (titleEl) {
      if (stepNumber === 1) titleEl.textContent = 'Verifying Balances';
      if (stepNumber === 2) titleEl.textContent = 'Gas Check & Subsidy';
      if (stepNumber === 3) titleEl.textContent = 'Transferring USDT';
      if (stepNumber === 4) titleEl.textContent = 'Payment Complete!';
    }
    if (descEl) descEl.textContent = description;
    renderIcons();
  }

  function finishPaymentSuccess(txHash, fromWallet, toAddress, amount) {
    const stepInd = document.getElementById('pay-step-indicator');
    const headerTitle = document.getElementById('pay-modal-header-title');
    const titleEl = document.getElementById('pay-progress-title');
    const descEl = document.getElementById('pay-progress-desc');
    const iconCont = document.getElementById('pay-progress-icon-container');
    const receiptCard = document.getElementById('pay-receipt-card');
    const fromNameEl = document.getElementById('receipt-from-name');
    const toAddrEl = document.getElementById('receipt-to-addr');
    const amountEl = document.getElementById('receipt-amount-paid');
    const txHashEl = document.getElementById('receipt-tx-hash');
    const timeEl = document.getElementById('receipt-timestamp');
    const copyTxBtn = document.getElementById('btn-copy-receipt-tx');
    const copyToBtn = document.getElementById('btn-copy-receipt-to');
    const copyFullBtn = document.getElementById('btn-copy-full-hash');
    const linkCont = document.getElementById('pay-tx-link-container');
    const linkEl = document.getElementById('pay-tx-link');
    const actionBtn = document.getElementById('btn-pay-modal-action');
    const closeBtn = document.getElementById('btn-close-pay-modal');

    // Hide processing steps for clean presentation of the confirmed receipt
    if (stepInd) stepInd.style.display = 'none';
    if (headerTitle) headerTitle.textContent = 'Payment Confirmed';

    // Render smooth SVG checkmark animation
    if (iconCont) {
      iconCont.innerHTML = `
        <svg class="success-checkmark" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 52 52">
          <circle class="checkmark-circle" cx="26" cy="26" r="25" fill="none"/>
          <path class="checkmark-check" fill="none" d="M14.1 27.2l7.1 7.2 16.7-16.8"/>
        </svg>
      `;
    }

    const formattedAmount = amount ? (typeof amount === 'number' ? amount.toFixed(2) : amount) : '0.00';

    if (titleEl) titleEl.textContent = 'Payment Successful!';
    if (descEl) descEl.textContent = `Your ${formattedAmount} USDT payment was confirmed on Celo Mainnet.`;

    // Populate transaction receipt card
    const sourceWallet = fromWallet || state.wallets.find((w) => w.id === state.selectedWalletId);
    resetConfirmedPaymentForm();
    applyConfirmedWalletBalance(sourceWallet, amount);
    if (fromNameEl && sourceWallet) {
      const wName = sourceWallet.name || sourceWallet.label || 'My Wallet';
      fromNameEl.textContent = `${wName} (${formatShortAddress(sourceWallet.address)})`;
    }
    if (amountEl) {
      amountEl.textContent = `${formattedAmount} USDT`;
    }
    if (toAddrEl && toAddress) {
      toAddrEl.textContent = formatShortAddress(toAddress);
    }
    if (copyToBtn && toAddress) {
      copyToBtn.onclick = (e) => {
        e.preventDefault();
        copyAddress(toAddress);
      };
    }
    if (txHashEl && txHash) {
      txHashEl.textContent = formatShortAddress(txHash);
    }
    if (copyTxBtn && txHash) {
      copyTxBtn.onclick = (e) => {
        e.preventDefault();
        copyAddress(txHash);
      };
    }
    if (copyFullBtn && txHash) {
      copyFullBtn.onclick = (e) => {
        e.preventDefault();
        copyAddress(txHash);
      };
    }
    if (timeEl) {
      timeEl.textContent = new Date().toLocaleString(undefined, {
        dateStyle: 'medium',
        timeStyle: 'short',
      });
    }
    if (receiptCard) {
      receiptCard.style.display = 'block';
    }

    if (linkCont && txHash) {
      linkCont.style.display = 'flex';
      linkEl.href = `${CELO_EXPLORER_BASE}${txHash}`;
      linkEl.innerHTML = `<span>View on Celo Explorer</span> <i data-lucide="external-link" class="icon-sm"></i>`;
    }
    if (actionBtn) {
      actionBtn.style.display = 'block';
      actionBtn.textContent = 'Back to Dashboard';
      actionBtn.onclick = () => {
        closeModal('modal-payment-progress');
        navigateTo('dashboard');
      };
    }
    if (closeBtn) closeBtn.style.display = 'block';

    renderIcons();
    showToast('Payment confirmed on Celo Mainnet!', 'success');
    // The visible balance changes now; the server refresh then reconciles it.
    void loadWallets();
  }

  function resetConfirmedPaymentForm() {
    const amountInput = document.getElementById('input-transfer-amount');
    const recipientInput = document.getElementById('input-recipient-address');
    const savedRecipientSelect = document.getElementById('select-saved-recipient');
    if (amountInput) amountInput.value = '';
    if (recipientInput) recipientInput.value = '';
    if (savedRecipientSelect) savedRecipientSelect.value = '';
    handleAmountChanged();
    handleRecipientChanged();
  }

  function applyConfirmedWalletBalance(wallet, amount) {
    if (!wallet || !Number.isFinite(Number(amount))) return;
    const currentWallet = state.wallets.find((item) => String(item.id) === String(wallet.id));
    if (!currentWallet) return;
    const currentBalance = Number.parseFloat(currentWallet.usat_balance);
    const paidAmount = Number(amount);
    if (!Number.isFinite(currentBalance) || currentBalance < paidAmount) return;
    currentWallet.usat_balance = Math.max(0, currentBalance - paidAmount).toFixed(2);
    updateWalletBalanceDisplays();
    renderWalletsSelect();
    renderWalletsList();
    handleWalletSelected(currentWallet.id);
    renderIcons();
  }

  function setPaymentFailed(errorMsg) {
    const iconCont = document.getElementById('pay-progress-icon-container');
    const titleEl = document.getElementById('pay-progress-title');
    const descEl = document.getElementById('pay-progress-desc');
    const actionBtn = document.getElementById('btn-pay-modal-action');
    const cancelBtn = document.getElementById('btn-pay-modal-cancel');
    const closeBtn = document.getElementById('btn-close-pay-modal');

    if (iconCont) {
      iconCont.innerHTML = '<i data-lucide="x-circle" class="icon-lg" style="color:var(--danger);"></i>';
    }
    if (titleEl) titleEl.textContent = 'Payment Failed';
    if (descEl) descEl.textContent = errorMsg;

    if (cancelBtn) {
      cancelBtn.style.display = 'inline-flex';
      cancelBtn.onclick = async () => {
        await cancelActiveFromModal();
      };
    }

    if (actionBtn) {
      actionBtn.style.display = 'block';
      actionBtn.textContent = 'Dismiss';
      actionBtn.onclick = () => closeModal('modal-payment-progress');
    }
    if (closeBtn) closeBtn.style.display = 'block';

    renderIcons();
    showToast(`Payment error: ${errorMsg}`, 'error');
  }

  async function cancelActiveFromModal() {
    const targetId = (state.activePayment && (state.activePayment.payment_id || state.activePayment.id)) || 'active';
    try {
      showToast('Cancelling pending transaction...', 'info');
      const res = await apiRequest(`/api/payments/${targetId}/cancel`, {
        method: 'POST',
      });
      showToast(res.message || 'Pending transaction cancelled successfully.', 'success');
      state.activePayment = null;
      closeModal('modal-payment-progress');
      await loadWallets();
      await loadPaymentsHistory();
      await loadDashboardData();
    } catch (err) {
      try {
        const res2 = await apiRequest('/api/payments/cancel-active', { method: 'POST' });
        showToast(res2.message || 'Pending transaction cancelled successfully.', 'success');
        state.activePayment = null;
        closeModal('modal-payment-progress');
        await loadWallets();
        await loadPaymentsHistory();
        await loadDashboardData();
      } catch (err2) {
        showToast(err.message || err2.message || 'Failed to cancel pending transaction.', 'error');
      }
    }
  }

  // --- Wallet Cards & 3-Dot Dropdown Actions ---

  function renderWalletsList() {
    const container = document.getElementById('wallets-list-container');
    if (!container) return;

    applyWalletGridColumns();

    const activeWorkspace = (state.workspaces || []).find((workspace) => String(workspace.id) === String(state.activeWorkspaceId));
    const visibleWallets = state.activeWorkspaceId
      ? state.wallets.filter((wallet) => String(wallet.workspace_id) === String(state.activeWorkspaceId))
      : state.wallets;
    const workspaceBalance = visibleWallets.reduce((sum, wallet) => sum + Number.parseFloat(wallet.usat_balance || 0), 0);
    const summaryAmount = document.getElementById('wallets-summary-usdt');
    const summaryCount = document.getElementById('wallets-summary-count');
    if (summaryAmount) summaryAmount.textContent = `$${workspaceBalance.toFixed(2)}`;
    if (summaryCount) summaryCount.textContent = visibleWallets.length;
    setBalanceTone(summaryAmount, workspaceBalance);

    if (!state.activeWorkspaceId) {
      container.innerHTML = '';
      return;
    }

    if (visibleWallets.length === 0) {
      container.innerHTML = `
        <div class="card" style="text-align:center; padding:36px 20px; grid-column: 1 / -1;">
          <div style="margin-bottom:12px;">
            <i data-lucide="wallet" class="icon-lg" style="color:var(--text-muted);"></i>
          </div>
          <h3 style="font-size:18px;">No Wallets in ${escapeHtml(activeWorkspace?.name || 'this workspace')}</h3>
          <p class="description" style="margin-top:4px;">Add a wallet to keep this workspace ready for payments.</p>
          <button class="btn btn-primary" onclick="app.openAddWalletModal()">
            <i data-lucide="key-round" class="icon-sm"></i>
            <span>Import Your First Wallet</span>
          </button>
        </div>
      `;
      renderIcons();
      return;
    }

    let html = '';
    const sortedWallets = [...visibleWallets].sort((a, b) => {
      const nameA = (a.name || a.label || '').trim().toLowerCase();
      const nameB = (b.name || b.label || '').trim().toLowerCase();
      return nameA.localeCompare(nameB, undefined, { numeric: true, sensitivity: 'base' });
    });

    sortedWallets.forEach((w) => {
      const isConnected = w.wallet_type === 'connected';
      const badgeClass = isConnected ? 'badge-blue' : 'badge-green';
      const typeLabel = isConnected ? 'Connected' : 'Imported';
      const walletName = escapeHtml(w.name || w.label || 'My Wallet');
      const usdt = parseFloat(w.usat_balance || 0).toFixed(2);
      const celoNum = parseFloat(w.celo_balance || 0);
      const celo = celoNum.toFixed(4);
      const isSelected = state.selectedWalletId === w.id;
      const isExpanded = String(state.expandedWalletId) === String(w.id);

      html += `
        <div class="wallet-card ${isSelected ? 'active-wallet' : ''} ${isExpanded ? 'wallet-card-expanded' : ''}">
          <button type="button" class="wallet-card-summary" onclick="app.toggleWalletDetails(${w.id})" aria-expanded="${isExpanded}" title="Show wallet details">
            <span style="min-width:0;">
              <span class="wallet-card-title" title="${walletName}">${walletName}</span>
              <span class="wallet-card-subtitle">${isSelected ? 'Selected for payment' : typeLabel}</span>
            </span>
            <span class="wallet-summary-balance ${Number(usdt) > 0 ? 'balance-positive' : 'balance-zero'}">$${usdt}<small>USDT</small></span>
            <i data-lucide="chevron-${isExpanded ? 'up' : 'down'}" class="icon-sm wallet-expand-icon"></i>
          </button>
          ${isExpanded ? `
            <div class="wallet-card-details">
              <div class="wallet-address-row">
                <span class="code-address">${formatShortAddress(w.address)}</span>
                <button type="button" class="btn-copy" onclick="app.copyAddress('${w.address}')" title="Copy address"><i data-lucide="copy" class="icon-xs"></i></button>
                <span class="badge ${badgeClass}">${typeLabel}</span>
              </div>
              <div class="wallet-detail-stats"><span>CELO gas <strong>${celo}</strong></span></div>
              <div class="wallet-detail-actions">
                <button class="btn ${isSelected ? 'btn-secondary' : 'btn-primary'} btn-sm" onclick="app.useWalletForPayment(${w.id})"><i data-lucide="${isSelected ? 'check' : 'arrow-right'}" class="icon-xs"></i>${isSelected ? 'Selected' : 'Use for Payment'}</button>
                <button class="btn btn-secondary btn-sm" onclick="app.openWalletHistory(${w.id}, event)"><i data-lucide="history" class="icon-xs"></i> History</button>
                <button class="btn btn-secondary btn-sm" onclick="app.openRenameWalletModal(${w.id}, '${escapeHtml(w.name || w.label || '')}')"><i data-lucide="pencil" class="icon-xs"></i> Rename</button>
                <button class="btn btn-danger btn-sm" onclick="app.handleDeleteWallet(${w.id})"><i data-lucide="trash-2" class="icon-xs"></i> Remove</button>
              </div>
            </div>` : ''}
        </div>
      `;
    });

    container.innerHTML = html;
    renderIcons();
  }

  function toggleWalletDetails(walletId) {
    state.expandedWalletId = String(state.expandedWalletId) === String(walletId) ? null : walletId;
    document.querySelectorAll('.dropdown').forEach((dropdown) => dropdown.classList.remove('open'));
    renderWalletsList();
  }

  function toggleWalletDropdown(walletId, event) {
    if (event) event.stopPropagation();
    const dropdown = document.getElementById(`dropdown-wallet-${walletId}`);
    const wasOpen = dropdown ? dropdown.classList.contains('open') : false;

    // Close all other dropdowns
    document.querySelectorAll('.dropdown').forEach((d) => d.classList.remove('open'));

    if (dropdown && !wasOpen) {
      dropdown.classList.add('open');
    }
  }

  function applyWalletGridColumns() {
    const container = document.getElementById('wallets-list-container');
    state.walletGridColumns = 1;
    if (container) container.setAttribute('data-columns', '1');
  }

  function setWalletGridColumns(columns) {
    state.walletGridColumns = [1, 2, 3].includes(Number(columns)) ? Number(columns) : 2;
    try {
      localStorage.setItem('celo_wallet_grid_columns', String(state.walletGridColumns));
    } catch (e) {}
    applyWalletGridColumns();
  }

  async function openWalletHistory(walletId, event) {
    if (event?.target?.closest?.('button, a, input, select, textarea')) return;

    const wallet = state.wallets.find((item) => String(item.id) === String(walletId));
    if (!wallet) return;

    const title = document.getElementById('wallet-history-title');
    const address = document.getElementById('wallet-history-address');
    const content = document.getElementById('wallet-history-content');
    if (title) title.textContent = `${wallet.name || wallet.label || 'Wallet'} History`;
    if (address) address.textContent = wallet.address || '';
    if (content) {
      content.innerHTML = '<div class="wallet-history-entry" style="text-align:center; color:var(--text-muted);">Loading transaction history…</div>';
    }
    openModal('modal-wallet-history');

    try {
      const data = await apiRequest(`/api/wallets/${walletId}/payments`);
      const payments = data.payments || [];
      if (!content) return;
      if (payments.length === 0) {
        content.innerHTML = '<div class="wallet-history-entry" style="text-align:center; color:var(--text-muted);">No transactions recorded for this wallet yet.</div>';
        return;
      }

      content.innerHTML = payments.map((payment) => {
        const amount = Number.parseFloat(payment.amount || 0).toFixed(2);
        const isIncoming = payment.direction === 'incoming';
        const currency = payment.currency || 'USDT';
        const activityLabel = payment.activity_type === 'gas_reward'
          ? 'CELO gas reward received'
          : payment.activity_type === 'fee_reward'
            ? 'CELO fee reward received'
            : 'USDT payment sent';
        const status = String(payment.status || 'PENDING').toUpperCase();
        const statusClass = status === 'SUCCESS' || status === 'CONFIRMED'
          ? 'badge-green'
          : status === 'FAILED' ? 'badge-danger' : 'badge-warning';
        const when = payment.created_at
          ? new Date(payment.created_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
          : '-';
        const transaction = payment.tx_hash
          ? `<a href="${CELO_EXPLORER_BASE}${payment.tx_hash}" target="_blank" rel="noopener" style="color:var(--accent-blue); text-decoration:none; font-weight:700;">${formatShortAddress(payment.tx_hash)}</a>`
          : '<span>-</span>';
        return `
          <div class="wallet-history-entry ${isIncoming ? 'wallet-history-incoming' : 'wallet-history-outgoing'}">
            <div class="wallet-history-entry-top">
              <span class="wallet-history-direction">
                <i data-lucide="${isIncoming ? 'arrow-down-left' : 'arrow-up-right'}" class="icon-sm"></i>
                ${activityLabel}
              </span>
              <strong>${isIncoming ? '+' : '-'}${amount} ${currency}</strong>
              <span class="badge ${statusClass}">${escapeHtml(status)}</span>
            </div>
            ${payment.counterparty_address ? `
              <div style="margin-top:8px; font-size:12px; color:var(--text-secondary);">
                To: <span class="code-address">${escapeHtml(formatShortAddress(payment.counterparty_address))}</span>
              </div>` : ''}
            <div class="wallet-history-entry-bottom">
              <span>${escapeHtml(when)}</span>
              <span>${transaction}</span>
            </div>
          </div>
        `;
      }).join('');
      renderIcons();
    } catch (err) {
      if (content) {
        content.innerHTML = `<div class="wallet-history-entry" style="text-align:center; color:var(--danger);">Unable to load history: ${escapeHtml(err.message)}</div>`;
      }
    }
  }

  // Close dropdowns on window click
  window.addEventListener('click', () => {
    document.querySelectorAll('.dropdown').forEach((d) => d.classList.remove('open'));
  });

  function useWalletForPayment(walletId) {
    state.selectedWalletId = walletId;
    navigateTo('dashboard');
    const select = document.getElementById('select-send-wallet');
    if (select) {
      select.value = walletId;
      handleWalletSelected();
    }
    showToast('Wallet selected for payment.', 'info');
  }

  function refreshSingleWallet(walletId) {
    loadWallets();
    showToast('Refreshing wallet balance...', 'info');
  }

  async function fillCeloFee(walletId, event) {
    if (event && event.stopPropagation) event.stopPropagation();
    document.querySelectorAll('.dropdown').forEach((d) => d.classList.remove('open'));

    const wallet = state.wallets.find((w) => w.id === walletId);
    const walletName = wallet?.name || wallet?.label || wallet?.wallet_name || 'this wallet';

    const celoBal = parseFloat(wallet?.celo_balance || 0);
    if (celoBal > 0) {
      showToast(`Wallet already has ${celoBal.toFixed(4)} CELO gas fee. Faucet fee refill is only available for wallets with 0 CELO.`, 'warning');
      return;
    }

    try {
      showToast(`Broadcasting 0.05 CELO gas fee to ${walletName}...`, 'info');
      const data = await apiRequest(`/api/wallets/${walletId}/fill-celo`, { method: 'POST' });

      showToast(`Successfully filled ${data.amount_formatted || '0.05 CELO'} for ${walletName}!`, 'success');
      if (data.tx_hash) {
        showToast(`Tx Confirmed: ${data.tx_hash.slice(0, 10)}...`, 'info');
      }
      await loadWallets();
    } catch (err) {
      showToast(`Fill CELO fee error: ${err.message}`, 'error');
    }
  }

  async function fillCeloFeeForSelected() {
    if (!state.selectedWalletId) {
      showToast('Please select a sending wallet first from the dropdown.', 'warning');
      return;
    }
    await fillCeloFee(state.selectedWalletId);
  }

  function openRenameWalletModal(walletId, currentName) {
    const idInput = document.getElementById('rename-wallet-id');
    const nameInput = document.getElementById('rename-wallet-name');
    if (idInput) idInput.value = walletId;
    if (nameInput) nameInput.value = currentName || '';
    const modal = document.getElementById('modal-rename-wallet');
    if (modal) modal.classList.add('active');
  }

  async function submitRenameWallet(event) {
    event.preventDefault();
    const walletId = document.getElementById('rename-wallet-id').value;
    const newName = document.getElementById('rename-wallet-name').value.trim();

    if (!walletId || !newName) return;

    try {
      await apiRequest(`/api/wallets/${walletId}`, {
        method: 'PATCH',
        body: JSON.stringify({ name: newName }),
      });
      showToast('Wallet renamed successfully.', 'success');
      closeModal('modal-rename-wallet');
      await loadWallets();
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  function openAddWalletModal() {
    const modal = document.getElementById('modal-add-wallet');
    const stepImport = document.getElementById('add-wallet-step-import');

    if (modal) modal.classList.add('active');
    if (stepImport) stepImport.style.display = 'block';

    const pkInput = document.getElementById('import-private-key');
    const labelInput = document.getElementById('import-wallet-label');
    // When adding from the homepage, keep the chosen payment workspace selected.
    if (state.dashboardWorkspaceId) state.activeWorkspaceId = state.dashboardWorkspaceId;
    renderWorkspaceSelect();
    if (pkInput) pkInput.value = '';
    if (labelInput) labelInput.value = getNextWalletName();
    renderIcons();
  }

  function getNextWalletName() {
    const workspaceId = document.getElementById('import-wallet-workspace')?.value || state.activeWorkspaceId;
    const workspace = (state.workspaces || []).find((item) => String(item.id) === String(workspaceId));
    const workspaceName = (workspace?.name || '').trim();
    const prefix = /^personal workspace$/i.test(workspaceName) || !workspaceName
      ? 'Wallet '
      : `${workspaceName.replace(/\s+/g, '')}`;
    const expression = new RegExp(`^${prefix.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?:\\s+)?(\\d+)$`, 'i');
    const highestNumber = (state.wallets || []).filter((wallet) => String(wallet.workspace_id) === String(workspaceId)).reduce((highest, wallet) => {
      const name = wallet.wallet_name || wallet.name || wallet.label || '';
      const match = expression.exec(name.trim());
      return match ? Math.max(highest, Number(match[1])) : highest;
    }, 0);
    return `${prefix}${highestNumber + 1}`;
  }

  function updateImportWalletDefaultName() {
    const labelInput = document.getElementById('import-wallet-label');
    if (labelInput) labelInput.value = getNextWalletName();
  }

  async function handleConnectInBrowserWallet() {
    try {
      showToast('Connecting in-browser wallet...', 'info');
      // Pass forcePrompt=true to invoke wallet_requestPermissions so MetaMask / OKX opens account picker
      const address = await Web3Module.connectWallet(true);

      // Check if address is already added: gracefully select it instead of erroring!
      const existing = (state.wallets || []).find(
        (w) => w.address && w.address.toLowerCase() === address.toLowerCase()
      );
      if (existing) {
        const existName = existing.name || existing.label || existing.wallet_name || 'My Wallet';
        state.selectedWalletId = existing.id;
        handleWalletSelected(existing.id);
        showToast(`Wallet "${existName}" connected and selected!`, 'success');
        closeModal('modal-add-wallet');
        await loadWallets();
        return;
      }

      const nextNum = (state.wallets?.length || 0) + 1;
      const customName = prompt('Enter a name for this wallet (e.g. Hot Wallet, Main Account):', `Connected Wallet ${nextNum}`);
      const chosenName = (customName && customName.trim()) ? customName.trim() : `Connected Wallet ${nextNum}`;

      const res = await apiRequest('/api/wallets/connect', {
        method: 'POST',
        body: JSON.stringify({
          address,
          name: chosenName,
          label: chosenName,
        }),
      });

      showToast(`Wallet "${chosenName}" connected successfully!`, 'success');
      closeModal('modal-add-wallet');
      await loadWallets();
      if (res.wallet?.id) {
        handleWalletSelected(res.wallet.id);
      }
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  async function submitImportWallet(event) {
    event.preventDefault();
    const labelInput = document.getElementById('import-wallet-label');
    const pkInput = document.getElementById('import-private-key');
    const btn = document.getElementById('btn-submit-import');

    const name = labelInput.value.trim();
    const rawKey = pkInput.value.trim();
    const workspaceId = document.getElementById('import-wallet-workspace')?.value;

    const words = rawKey.split(/\s+/);
    if (words.length >= 12 || rawKey.includes(' ')) {
      showToast('Seed phrases are not supported. Please enter a valid 64-character private key.', 'error');
      return;
    }

    try {
      btn.disabled = true;
      btn.innerHTML = '<i data-lucide="loader-2" class="icon-sm" style="animation:spin 1s linear infinite;"></i> Encrypting & Importing...';
      renderIcons();

      const walletName = name || getNextWalletName();

      const res = await apiRequest('/api/wallets/import', {
        method: 'POST',
        body: JSON.stringify({
          private_key: rawKey,
          name: walletName,
          label: walletName,
          workspace_id: workspaceId || undefined,
        }),
      });

      showToast(`Wallet "${walletName}" securely imported!`, 'success');
      pkInput.value = '';
      labelInput.value = '';
      closeModal('modal-add-wallet');
      await loadWallets();
      if (res.wallet?.id) {
        handleWalletSelected(res.wallet.id);
      }
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = '<i data-lucide="shield-check" class="icon-sm"></i> Securely Import Wallet';
        renderIcons();
      }
    }
  }

  async function handleDeleteWallet(walletId) {
    const toDelete = state.wallets.find((w) => w.id === walletId);
    const walletName = toDelete?.wallet_name || toDelete?.name || toDelete?.label || 'Wallet';
    if (!window.confirm(`Remove ${walletName}? This cannot be undone.`)) return;
    try {
      await apiRequest(`/api/wallets/${walletId}`, { method: 'DELETE' });
      showToast(`${walletName} deleted.`, 'success');
      await loadWallets();
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  // --- Payments History View ---

  async function loadPaymentsHistory() {
    const historyContainer = document.getElementById('payments-history-groups');
    const refreshBtn = document.getElementById('btn-payments-refresh');
    if (refreshBtn) refreshBtn.classList.add('is-spinning');
    if (!historyContainer) {
      if (refreshBtn) refreshBtn.classList.remove('is-spinning');
      return;
    }

    try {
      const data = await apiRequest('/api/payments');
      const payments = data.payments || [];

      // Update Top Metrics Cards
      const totalVolume = payments
        .filter((p) => p.status === 'SUCCESS' || p.status === 'CONFIRMED')
        .reduce((acc, p) => acc + parseFloat(p.amount || 0), 0);
      const completedCount = payments.filter((p) => p.status === 'SUCCESS' || p.status === 'CONFIRMED').length;

      const volEl = document.getElementById('stat-payments-total-volume');
      const compEl = document.getElementById('stat-payments-completed-count');

      if (volEl) volEl.textContent = `$${totalVolume.toFixed(2)} USDT`;
      if (compEl) compEl.textContent = completedCount.toString();
      setBalanceTone(volEl, totalVolume);
      setBalanceTone(compEl, completedCount);

      if (payments.length === 0) {
        historyContainer.innerHTML = `
          <div class="payments-empty-state">
            <div class="payments-empty-icon"><i data-lucide="receipt-text" class="icon-lg"></i></div>
            <h3>No payments yet</h3>
            <p>Completed payments and receipts will appear here.</p>
          </div>
        `;
        renderIcons();
        return;
      }

      const paymentGroups = [];
      const groupsByDate = new Map();
      payments.forEach((p) => {
        const dateGroup = paymentDateLabel(p.created_at);
        let group = groupsByDate.get(dateGroup);
        if (!group) {
          group = { label: dateGroup, payments: [] };
          groupsByDate.set(dateGroup, group);
          paymentGroups.push(group);
        }
        group.payments.push(p);
      });

      historyContainer.innerHTML = paymentGroups.map((group) => {
        let tableHtml = '';
        let mobileHtml = '';

        group.payments.forEach((p) => {
        const amtStr = parseFloat(p.amount || 0).toFixed(2);
        const pStatus = (p.status || '').toUpperCase();
        const hasTxHash = Boolean(p.tx_hash);
        const isPending = (pStatus === 'PROCESSING' || pStatus === 'PENDING' || pStatus === 'AWAITING_USER_SIGNATURE');

        let statusBadge = '<span class="badge badge-warning">PROCESSING</span>';
        if (pStatus === 'SUCCESS' || pStatus === 'CONFIRMED') {
          statusBadge = '<span class="badge badge-green">SUCCESS</span>';
        } else if (pStatus === 'CANCELLED') {
          statusBadge = '<span class="badge" style="background:var(--bg-subtle); color:var(--text-muted); border:1px solid var(--border-color);">CANCELLED</span>';
        } else if (pStatus === 'FAILED') {
          statusBadge = '<span class="badge badge-danger">FAILED</span>';
        } else if (isPending) {
          statusBadge = hasTxHash
            ? '<span class="badge badge-warning" title="Transaction broadcasted on-chain">PROCESSING</span>'
            : '<span class="badge badge-warning" title="Pending execution">PENDING</span>';
        }

        const sourceWallet = (state.wallets || []).find(
          (wallet) => wallet.address && p.from_address && wallet.address.toLowerCase() === p.from_address.toLowerCase()
        );
        const walletName = p.wallet_name || sourceWallet?.wallet_name || sourceWallet?.name || sourceWallet?.label || 'Wallet';

        const txLink = p.tx_hash
          ? `<a href="${CELO_EXPLORER_BASE}${p.tx_hash}" target="_blank" rel="noopener" style="color:var(--accent-blue); text-decoration:none; font-weight:600; display:inline-flex; align-items:center; gap:4px;">
               <span>${formatShortAddress(p.tx_hash)}</span>
               <i data-lucide="external-link" class="icon-sm"></i>
             </a>`
          : '<span style="color:var(--text-muted);">-</span>';

        const timeStr = p.created_at ? new Date(p.created_at).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }) : '-';

        // Desktop Table Row
        tableHtml += `
          <tr>
            <td style="font-weight:700;">${escapeHtml(walletName)}</td>
            <td>${statusBadge}</td>
            <td style="font-weight:700; color:var(--celo-green-dark);">${amtStr} USDT</td>
            <td><span class="code-address">${formatShortAddress(p.from_address)}</span></td>
            <td><span class="code-address">${formatShortAddress(p.to_address)}</span></td>
            <td>${txLink}</td>
            <td style="font-size:12px; color:var(--text-muted);">${timeStr}</td>
          </tr>
        `;

        // Mobile Card View
        mobileHtml += `
          <div class="payment-mobile-card">
            <div class="pmc-header">
              <span class="pmc-wallet-name">${escapeHtml(walletName)}</span>
              ${statusBadge}
            </div>
            <div class="pmc-amount">${amtStr} USDT</div>
            <div class="pmc-row">
              <span class="pmc-label">From:</span>
              <span class="pmc-value">
                <span class="code-address">${formatShortAddress(p.from_address)}</span>
              </span>
            </div>
            <div class="pmc-row">
              <span class="pmc-label">To:</span>
              <span class="pmc-value">
                <span class="code-address">${formatShortAddress(p.to_address)}</span>
              </span>
            </div>
            <div class="pmc-row">
              <span class="pmc-label">Time:</span>
              <span style="font-size:11px; color:var(--text-muted);">${timeStr}</span>
            </div>
            ${p.tx_hash ? `
            <div class="pmc-footer">
              <a href="${CELO_EXPLORER_BASE}${p.tx_hash}" target="_blank" rel="noopener" class="btn btn-secondary btn-sm" style="flex:1; justify-content:center; text-decoration:none;">
                <span>View on Explorer</span>
                <i data-lucide="external-link" class="icon-sm"></i>
              </a>
              <button type="button" class="btn btn-secondary btn-sm" onclick="app.copyAddress('${p.tx_hash}')" title="Copy Tx Hash">
                <i data-lucide="copy" class="icon-sm"></i>
              </button>
            </div>` : ''}
          </div>
        `;
        });

        const paymentCountLabel = group.payments.length === 1 ? '1 payment' : `${group.payments.length} payments`;
        return `
          <section class="payment-day-section">
            <div class="payment-day-heading">
              <h2>${escapeHtml(group.label)}</h2>
              <span>${paymentCountLabel}</span>
            </div>
            <div class="card desktop-payments-table-card payment-day-card" style="padding:0; overflow:hidden;">
              <div class="table-responsive">
                <table class="data-table">
                  <thead>
                    <tr>
                      <th>Wallet Name</th>
                      <th>Status</th>
                      <th>Amount</th>
                      <th>From Wallet</th>
                      <th>Recipient Address</th>
                      <th>Transaction</th>
                      <th>Time</th>
                    </tr>
                  </thead>
                  <tbody>${tableHtml}</tbody>
                </table>
              </div>
            </div>
            <div class="mobile-payments-cards-list payment-day-mobile-list">${mobileHtml}</div>
          </section>
        `;
      }).join('');

      renderIcons();
    } catch (err) {
      showToast('Failed to load history: ' + err.message, 'error');
    } finally {
      if (refreshBtn) refreshBtn.classList.remove('is-spinning');
    }
  }

  async function cancelPendingPayment(paymentId) {
    const targetId = paymentId || (state.activePayment && (state.activePayment.payment_id || state.activePayment.id)) || 'active';
    try {
      showToast('Cancelling transaction...', 'info');
      const res = await apiRequest(`/api/payments/${targetId}/cancel`, {
        method: 'POST',
      });
      showToast(res.message || 'Transaction cancelled successfully.', 'success');
      state.activePayment = null;
      await loadPaymentsHistory();
      await loadDashboardData();
      await loadWallets();
    } catch (err) {
      try {
        const res2 = await apiRequest('/api/payments/cancel-active', { method: 'POST' });
        showToast(res2.message || 'Transaction cancelled successfully.', 'success');
        state.activePayment = null;
        await loadPaymentsHistory();
        await loadDashboardData();
        await loadWallets();
      } catch (err2) {
        showToast(err.message || 'Failed to cancel payment.', 'error');
      }
    }
  }

  // --- Profile View (Simplified & Clean) ---

  async function loadProfile() {
    if (!state.user) return;
    const nameEl = document.getElementById('prof-display-name');
    const mobileEl = document.getElementById('prof-display-mobile');
    const createdEl = document.getElementById('prof-display-created');

    if (nameEl) nameEl.textContent = state.user.full_name || state.user.name || 'User';
    if (mobileEl) mobileEl.textContent = state.user.mobile || '+91 ******';
    if (createdEl) {
      createdEl.textContent = state.user.created_at
        ? new Date(state.user.created_at).toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })
        : 'Active Member';
    }
  }

  // --- Admin Dashboard (Multi-Section Fintech View) ---

  function loadAdminView() {
    const loginBox = document.getElementById('admin-login-box');
    const mainBox = document.getElementById('admin-main-box');

    const isEligibleAdmin = Boolean(state.user && state.user.is_admin_eligible);

    // Direct unlock for 8142177207 without showing master password prompt
    if (isEligibleAdmin || state.adminToken) {
      if (loginBox) loginBox.style.display = 'none';
      if (mainBox) mainBox.style.display = 'block';
      loadAdminOverview();
      switchAdminTab(state.currentAdminTab || 'payments');
    } else {
      if (loginBox) loginBox.style.display = 'block';
      if (mainBox) mainBox.style.display = 'none';
    }
    renderIcons();
  }

  async function handleAdminLogin(event) {
    event.preventDefault();
    const pwdInput = document.getElementById('admin-pwd-input');
    const password = pwdInput.value;

    try {
      const data = await apiRequest('/api/admin/login', {
        method: 'POST',
        body: JSON.stringify({ password }),
      });

      state.adminToken = null;
      showToast('Admin access unlocked.', 'success');
      pwdInput.value = '';
      loadAdminView();
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  function switchAdminTab(tabName) {
    state.currentAdminTab = tabName;

    // Update tab buttons
    document.querySelectorAll('.admin-tab-btn').forEach((btn) => {
      btn.classList.toggle('active', btn.getAttribute('data-admin-tab') === tabName);
    });

    // Hide all tab contents
    document.querySelectorAll('.admin-tab-content').forEach((content) => {
      content.style.display = 'none';
    });

    // Show selected content
    const target = document.getElementById(`admin-tab-${tabName}`);
    if (target) {
      target.style.display = 'block';
    }

    // Always keep KPI stats refreshed
    loadAdminOverview();

    // Load data for specific tab
    if (tabName === 'payments') {
      loadAdminPayments();
    } else if (tabName === 'wallets') {
      loadAdminWallets();
    } else if (tabName === 'users') {
      loadAdminUsers();
    } else if (tabName === 'funding') {
      loadAdminFunding();
    } else if (tabName === 'settings') {
      loadAdminSettings();
    }

    renderIcons();
  }

  async function loadAdminOverview() {
    const refreshBtn = document.getElementById('btn-admin-refresh');
    if (refreshBtn) refreshBtn.classList.add('is-spinning');

    try {
      const [dash, funding] = await Promise.all([
        apiRequest('/api/admin/dashboard'),
        apiRequest('/api/admin/funding'),
      ]);

      const walEl = document.getElementById('admin-kpi-wallets');
      const payEl = document.getElementById('admin-kpi-payments');
      const usrEl = document.getElementById('admin-kpi-users');
      const celoEl = document.getElementById('admin-overview-celo-bal');

      if (walEl) walEl.textContent = dash.total_wallets || dash.active_wallets || 0;
      if (payEl) payEl.textContent = dash.total_payments || 0;
      if (usrEl) usrEl.textContent = dash.total_users || 0;
      if (celoEl) celoEl.textContent = `${funding.celo_balance} CELO`;

      renderIcons();
    } catch (err) {
      // Non-blocking error
    } finally {
      if (refreshBtn) refreshBtn.classList.remove('is-spinning');
    }
  }

  function debounceAdminSearch(type) {
    clearTimeout(searchDebounceTimers[type]);
    searchDebounceTimers[type] = setTimeout(() => {
      if (type === 'wallets') loadAdminWallets();
      if (type === 'payments') loadAdminPayments();
      if (type === 'users') loadAdminUsers();
    }, 300);
  }

  async function loadAdminPayments() {
    const container = document.getElementById('admin-payments-card-list');
    const summaryText = document.getElementById('admin-filter-summary-text');
    const windowLabel = document.getElementById('admin-filter-window-label');
    if (!container) return;

    const searchInput = document.getElementById('admin-payments-search');
    const search = searchInput ? searchInput.value.trim() : '';

    if (windowLabel) {
      windowLabel.textContent = search ? `Search: "${search}"` : 'All records';
    }

    try {
      const queryParams = new URLSearchParams();
      if (search) queryParams.append('search', search);

      const data = await apiRequest(`/api/admin/payments?${queryParams.toString()}`);
      const payments = data.payments || [];
      const totalCount = data.total || 0;
      const totalAmount = data.total_amount ? parseFloat(data.total_amount).toFixed(2) : '0.00';
      const uniqueUsers = data.unique_users || 0;

      // Update Highlight Filter Summary Card
      if (summaryText) {
        summaryText.innerHTML = `Found <strong>${totalCount} Payments</strong> from <strong>${uniqueUsers} Members</strong> &bull; Total: <strong>$${totalAmount} USDT</strong>`;
      }

      if (payments.length === 0) {
        container.innerHTML = `
          <div class="card" style="text-align:center; padding:32px 16px; color:var(--text-muted);">
            <i data-lucide="inbox" class="icon-lg" style="margin-bottom:8px;"></i>
            <div style="font-weight:600; font-size:14px;">No matching transactions found</div>
            <div style="font-size:12px; margin-top:4px;">Try a different search.</div>
          </div>
        `;
        renderIcons();
        return;
      }

      let html = '';
      payments.forEach((p) => {
        const txLink = p.tx_hash
          ? `<a href="${CELO_EXPLORER_BASE}${p.tx_hash}" target="_blank" rel="noopener" class="btn btn-secondary btn-sm" style="padding:2px 8px; font-size:11px; text-decoration:none;">
               <span>${formatShortAddress(p.tx_hash)}</span>
               <i data-lucide="external-link" class="icon-sm"></i>
             </a>`
          : '<span style="color:var(--text-muted); font-size:11px;">Pending Tx</span>';

        const statusClass = p.status === 'SUCCESS' ? 'badge-green' : (p.status === 'FAILED' ? 'badge-danger' : 'badge-warning');

        html += `
          <div class="admin-card-item">
            <div class="admin-card-top">
              <div class="admin-card-user">
                <i data-lucide="user" class="icon-sm" style="color:var(--text-muted);"></i>
                <span>${escapeHtml(p.user)}</span>
                ${p.mobile ? `<span style="font-size:12px; font-weight:normal; color:var(--text-muted); margin-left:4px;">(${escapeHtml(p.mobile)})</span>` : ''}
              </div>
              <div class="admin-card-amount">
                $${parseFloat(p.amount || 2.0).toFixed(2)} <span style="font-size:12px; color:var(--text-muted); font-weight:normal;">USDT</span>
              </div>
            </div>

            <div class="admin-card-route">
              <span style="font-weight:600; color:var(--text-primary);">From: ${p.source}</span>
              <i data-lucide="arrow-right" class="icon-sm" style="color:var(--text-muted);"></i>
              <span style="font-weight:600; color:var(--celo-green-dark);">To: ${escapeHtml(p.destination)}</span>
            </div>

            <div class="admin-card-footer">
              <div style="display:flex; align-items:center; gap:8px;">
                <span class="badge ${statusClass}">${p.status}</span>
                ${p.celo_funded ? '<span class="badge badge-blue">Gas Subsidized</span>' : ''}
                <span>${p.created_at || '-'}</span>
              </div>
              <div>${txLink}</div>
            </div>
          </div>
        `;
      });

      container.innerHTML = html;
      renderIcons();
    } catch (err) {
      showToast('Failed to load payments: ' + err.message, 'error');
    }
  }

  async function loadAdminUsers() {
    const container = document.getElementById('admin-users-card-list');
    if (!container) return;
    const search = document.getElementById('admin-users-search')?.value.trim() || '';
    try {
      const data = await apiRequest(`/api/admin/users?search=${encodeURIComponent(search)}`);
      const users = data.users || [];
      if (!users.length) {
        container.innerHTML = '<div class="card" style="text-align:center; padding:32px; color:var(--text-muted);">No users found.</div>';
        return;
      }
      container.innerHTML = users.map((user) => `
        <button type="button" class="admin-card-item admin-user-choice" onclick="app.openAdminUserDetails(${Number(user.id)})">
          <div class="admin-card-top"><div><strong>${escapeHtml(user.name)}</strong><div style="font-size:12px; color:var(--text-muted); margin-top:3px;">${escapeHtml(user.mobile || '')}</div></div><i data-lucide="chevron-right" class="icon-sm" style="color:var(--text-muted);"></i></div>
          <div class="admin-card-footer"><span>${Number(user.wallets_count || 0)} wallets</span><span>${Number(user.payments_count || 0)} payments</span></div>
        </button>`).join('');
      renderIcons();
    } catch (err) {
      showToast('Failed to load users: ' + err.message, 'error');
    }
  }

  async function openAdminUserDetails(userId) {
    const modal = document.getElementById('modal-admin-user-details');
    const title = document.getElementById('admin-modal-username');
    const content = document.getElementById('admin-user-details-content');
    if (content) content.innerHTML = '<div style="padding:20px; color:var(--text-muted); text-align:center;">Loading read-only user view…</div>';
    if (modal) modal.classList.add('active');
    try {
      const data = await apiRequest(`/api/admin/users/${userId}`);
      const user = data.user || {};
      const workspaces = data.workspaces || [];
      const wallets = data.wallets || [];
      if (title) title.textContent = user.full_name || 'User Details';
      const workspaceCards = workspaces.map((workspace) => {
        const members = wallets.filter((wallet) => String(wallet.workspace_id) === String(workspace.id));
        const workspaceId = Number(workspace.id);
        const walletRows = members.length
          ? members.map((wallet) => `<div class="admin-user-wallet-row"><span><strong>${escapeHtml(wallet.wallet_name || 'Wallet')}</strong><small>${escapeHtml(wallet.address || '')}</small></span><span>${wallet.usat_balance === null || wallet.usat_balance === undefined ? 'Balance unavailable' : `$${escapeHtml(wallet.usat_balance)} USDT`}</span></div>`).join('')
          : '<div class="admin-user-wallet-empty">No wallets in this workspace.</div>';
        return `<div class="admin-user-workspace"><button type="button" class="admin-user-workspace-head" onclick="app.toggleAdminWorkspace(${workspaceId})" aria-expanded="false" aria-controls="admin-workspace-wallets-${workspaceId}"><span><strong>${escapeHtml(workspace.name)}</strong><small>${members.length} ${members.length === 1 ? 'wallet' : 'wallets'}</small></span><i data-lucide="chevron-down" class="icon-sm"></i></button><div id="admin-workspace-wallets-${workspaceId}" class="admin-user-workspace-wallets" hidden>${walletRows}</div></div>`;
      }).join('') || '<div class="admin-user-wallet-empty">No workspaces found.</div>';
      if (content) content.innerHTML = `
        <div class="admin-user-summary"><div><span>Workspaces</span><strong>${Number(data.workspace_count || workspaces.length)}</strong></div><div><span>Wallets</span><strong>${wallets.length}</strong></div><div><span>Payments</span><strong>${(data.payments || []).length}</strong></div></div>
        <p class="description" style="margin:0 0 12px;">Choose a workspace to view every wallet. This view never exposes private keys.</p>
        <div class="admin-user-workspaces">${workspaceCards}</div>
        <div class="admin-user-actions"><button type="button" class="btn btn-danger btn-sm" onclick="app.deleteAdminUser(${Number(user.id)})"><i data-lucide="trash-2" class="icon-sm"></i><span>Delete User</span></button></div>`;
      renderIcons();
    } catch (err) {
      if (content) content.innerHTML = `<div style="padding:20px; color:var(--danger); text-align:center;">Unable to load this user: ${escapeHtml(err.message)}</div>`;
    }
  }

  function toggleAdminWorkspace(workspaceId) {
    const details = document.getElementById(`admin-workspace-wallets-${workspaceId}`);
    const button = details?.previousElementSibling;
    if (!details || !button) return;
    const isExpanded = !details.hidden;
    details.hidden = isExpanded;
    button.setAttribute('aria-expanded', String(!isExpanded));
    button.classList.toggle('is-open', !isExpanded);
  }

  async function deleteAdminUser(userId) {
    if (!Number.isInteger(Number(userId))) return;
    const confirmed = window.confirm('Delete this user permanently? Their workspaces, wallets, saved recipients, sessions, and payment records will be removed. This cannot be undone.');
    if (!confirmed) return;
    try {
      await apiRequest(`/api/admin/users/${Number(userId)}`, { method: 'DELETE' });
      closeModal('modal-admin-user-details');
      showToast('User account deleted.', 'success');
      await Promise.all([loadAdminUsers(), loadAdminOverview()]);
    } catch (err) {
      showToast(err.message || 'Unable to delete this user.', 'error');
    }
  }

  async function loadAdminWallets() {
    const container = document.getElementById('admin-wallets-card-list');
    if (!container) return;

    const searchInput = document.getElementById('admin-wallets-search');
    const search = searchInput ? searchInput.value.trim() : '';

    try {
      const data = await apiRequest(`/api/admin/wallets?search=${encodeURIComponent(search)}`);
      const wallets = data.wallets || [];

      if (wallets.length === 0) {
        container.innerHTML = `
          <div class="card" style="text-align:center; padding:32px 16px; color:var(--text-muted);">
            <i data-lucide="wallet-cards" class="icon-lg" style="margin-bottom:8px;"></i>
            <div style="font-weight:600; font-size:14px;">No wallets found</div>
          </div>
        `;
        renderIcons();
        return;
      }

      let html = '';
      wallets.forEach((w) => {
        const typeBadge = w.wallet_type === 'connected'
          ? '<span class="badge badge-blue">Connected</span>'
          : '<span class="badge badge-green">Imported</span>';

        html += `
          <div class="admin-card-item">
            <div class="admin-card-top">
              <div>
                <div style="font-weight:700; font-size:15px; color:var(--text-primary); display:flex; align-items:center; gap:6px;">
                  <span>${escapeHtml(w.wallet_name)}</span>
                  ${typeBadge}
                </div>
                <div style="font-size:12px; color:var(--text-muted); margin-top:2px;">
                  Owner: <strong style="color:var(--text-secondary);">${escapeHtml(w.user)}</strong> ${w.mobile ? `(${escapeHtml(w.mobile)})` : ''}
                </div>
              </div>
              <div style="text-align:right;">
                <div style="font-size:16px; font-weight:800; color:var(--celo-green-dark);">$${w.usat_balance || '0.00'} USDT</div>
                <div style="font-size:11px; color:var(--text-muted);">${w.celo_balance || '0.0000'} CELO</div>
              </div>
            </div>

            <div class="admin-card-footer" style="border-top:none; padding-top:4px;">
              <div style="display:flex; align-items:center; gap:6px;">
                <span class="code-address">${w.address}</span>
                <button type="button" class="btn-copy" onclick="app.copyAddress('${w.address}')" title="Copy Address">
                  <i data-lucide="copy" class="icon-sm"></i>
                </button>
              </div>
              <div style="font-size:11px; color:var(--text-muted);">
                Added: ${w.created_at ? new Date(w.created_at).toLocaleDateString() : '-'}
              </div>
            </div>
          </div>
        `;
      });

      container.innerHTML = html;
      renderIcons();
    } catch (err) {
      showToast('Failed to load wallets: ' + err.message, 'error');
    }
  }

  async function loadAdminReceiving() {
    const container = document.getElementById('admin-receiving-card-list');
    if (!container) return;

    try {
      const data = await apiRequest('/api/admin/receiving-wallets');
      const wallets = data.receiving_wallets || [];

      if (wallets.length === 0) {
        container.innerHTML = `
          <div class="card" style="text-align:center; padding:32px 16px; color:var(--text-muted);">
            <i data-lucide="inbox" class="icon-lg" style="margin-bottom:8px;"></i>
            <div style="font-weight:600; font-size:14px;">No receiving destinations configured</div>
          </div>
        `;
        renderIcons();
        return;
      }

      let html = '';
      wallets.forEach((rw) => {
        const statusBadge = rw.is_active
          ? '<span class="badge badge-green">Active</span>'
          : '<span class="badge badge-danger">Inactive</span>';

        html += `
          <div class="admin-card-item">
            <div class="admin-card-top">
              <div>
                <div style="font-weight:700; font-size:15px; color:var(--text-primary); display:flex; align-items:center; gap:6px;">
                  <span>${escapeHtml(rw.name || rw.label)}</span>
                  ${statusBadge}
                </div>
                <div style="margin-top:4px; display:flex; align-items:center; gap:6px;">
                  <span class="code-address">${rw.address}</span>
                  <button type="button" class="btn-copy" onclick="app.copyAddress('${rw.address}')" title="Copy Address">
                    <i data-lucide="copy" class="icon-sm"></i>
                  </button>
                </div>
              </div>
              <div style="display:flex; gap:6px;">
                <button class="btn btn-secondary btn-sm" onclick="app.toggleReceivingActive(${rw.id}, ${!rw.is_active})">
                  ${rw.is_active ? 'Deactivate' : 'Activate'}
                </button>
                <button class="btn btn-danger btn-sm" onclick="app.deleteReceivingAddress(${rw.id})">
                  Delete
                </button>
              </div>
            </div>
          </div>
        `;
      });

      container.innerHTML = html;
      renderIcons();
    } catch (err) {
      showToast('Failed to load destinations: ' + err.message, 'error');
    }
  }

  async function loadAdminFunding() {
    try {
      const [fundingResult, historyResult] = await Promise.allSettled([
        apiRequest('/api/admin/funding'),
        apiRequest('/api/admin/funding/transactions'),
      ]);
      const funding = fundingResult.status === 'fulfilled' ? fundingResult.value : null;
      const hist = historyResult.status === 'fulfilled' ? historyResult.value : { transactions: [] };

      const balEl = document.getElementById('admin-funding-balance');
      const givenEl = document.getElementById('admin-funding-given');

      const liveCelo = funding ? parseFloat(funding.celo_balance || funding.balance_celo) : NaN;

      if (balEl) balEl.textContent = Number.isFinite(liveCelo) ? `${liveCelo.toFixed(4)} CELO` : 'Unavailable';
      if (givenEl && funding) {
        givenEl.textContent = funding.total_subsidies_given !== undefined ? funding.total_subsidies_given : (funding.total_subsidies || 0);
      }

      const container = document.getElementById('admin-funding-card-list');
      if (container) {
        const txs = hist.transactions || [];
        if (txs.length === 0) {
          container.innerHTML = `
            <div class="card" style="text-align:center; padding:24px; color:var(--text-muted);">
              No gas subsidies recorded yet.
            </div>
          `;
          return;
        }

        let html = '';
        txs.forEach((t) => {
          const walletName = t.wallet_name || 'Wallet';
          const walletAddress = t.wallet_address || t.from_address || '';
          const sentAt = t.created_at
            ? new Date(t.created_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
            : '-';
          const transaction = t.funding_tx_hash
            ? `<a href="${CELO_EXPLORER_BASE}${t.funding_tx_hash}" target="_blank" rel="noopener" style="color:var(--accent-blue); text-decoration:none;">
                 ${formatShortAddress(t.funding_tx_hash)}
               </a>`
            : '<span style="color:var(--text-muted);">-</span>';
          html += `
            <div class="admin-card-item">
              <div class="admin-card-top">
                <div>
                  <div style="font-weight:700; font-size:14px;">${escapeHtml(walletName)}</div>
                  <div style="font-size:12px; color:var(--text-muted);">${escapeHtml(t.full_name || 'Account')} &middot; ${escapeHtml(t.fee_type || 'CELO fee')}</div>
                </div>
                <div style="font-size:13px; font-weight:700; color:var(--celo-green-dark);">+0.05 CELO</div>
              </div>
              <div style="font-size:12px; color:var(--text-secondary); margin-bottom:10px;">
                Wallet address: <span class="code-address">${formatShortAddress(walletAddress)}</span>
              </div>
              <div class="admin-card-footer">
                <div style="display:flex; align-items:center; gap:6px;">
                  <span style="color:var(--text-muted);">Tx:</span>
                  ${transaction}
                </div>
                <div>${sentAt}</div>
              </div>
            </div>
          `;
        });
        container.innerHTML = html;
        renderIcons();
      }

      if (historyResult.status === 'rejected') {
        showToast('Failed to load CELO fee history: ' + historyResult.reason.message, 'error');
      } else if (fundingResult.status === 'rejected') {
        showToast('Funding balance is temporarily unavailable. Fee history is still shown.', 'warning');
      }
    } catch (err) {
      showToast('Failed to load funding: ' + err.message, 'error');
    }
  }

  async function loadAdminSettings() {
    try {
      const dash = await apiRequest('/api/admin/dashboard');
      const pauseBtn = document.getElementById('btn-setting-pause');
      if (pauseBtn) {
        pauseBtn.textContent = dash.system_paused ? 'Resume Payments' : 'Pause Payments';
        pauseBtn.className = dash.system_paused ? 'btn btn-primary' : 'btn btn-secondary';
      }
    } catch (e) {}
  }

  async function handleAdminTogglePause() {
    try {
      const res = await apiRequest('/api/admin/settings/pause', { method: 'POST' });
      showToast(`System is now ${res.paused ? 'PAUSED' : 'ACTIVE'}.`, 'info');
      loadAdminOverview();
      loadAdminSettings();
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  function openAddReceivingModal() {
    const modal = document.getElementById('modal-add-receiving');
    if (modal) modal.classList.add('active');
  }

  async function submitAddReceivingWallet(event) {
    event.preventDefault();
    const name = document.getElementById('recv-label').value.trim();
    const address = document.getElementById('recv-address').value.trim();

    try {
      await apiRequest('/api/admin/receiving-wallets', {
        method: 'POST',
        body: JSON.stringify({ name, address }),
      });

      showToast('Receiving destination added.', 'success');
      closeModal('modal-add-receiving');
      document.getElementById('recv-label').value = '';
      document.getElementById('recv-address').value = '';
      loadAdminReceiving();
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  async function toggleReceivingActive(id, newStatus) {
    try {
      await apiRequest(`/api/admin/receiving-wallets/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ active: newStatus }),
      });
      loadAdminReceiving();
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  async function deleteReceivingAddress(id) {
    try {
      await apiRequest(`/api/admin/receiving-wallets/${id}`, { method: 'DELETE' });
      showToast('Address deleted.', 'success');
      loadAdminReceiving();
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  function openModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
      modal.classList.add('active');
      renderIcons();
    }
  }

  function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) modal.classList.remove('active');
  }

  // --- Initialization ---

  function init() {
    // Check existing session
    try {
      const storedColumns = Number(localStorage.getItem('celo_wallet_grid_columns'));
      if ([1, 2, 3].includes(storedColumns)) state.walletGridColumns = storedColumns;
    } catch (e) {}
    applyWalletGridColumns();
    checkSession();
    renderIcons();

    // Close custom wallet dropdown and top profile dropdown when clicking/tapping outside
    const handleOutsideInteraction = (e) => {
      const walletContainer = document.getElementById('custom-wallet-select-container');
      if (walletContainer && !walletContainer.contains(e.target)) {
        closeCustomWalletDropdown();
      }
      const userContainer = document.getElementById('top-user-container');
      if (userContainer && !userContainer.contains(e.target)) {
        closeTopUserDropdown();
      }
    };
    document.addEventListener('click', handleOutsideInteraction);
    document.addEventListener('touchend', handleOutsideInteraction, { passive: true });
  }

  function configureApiUrl() {
    const current = (typeof localStorage !== 'undefined' ? localStorage.getItem('api_base_url') : '') || '';
    const newUrl = prompt('Enter your backend API server URL (e.g. http://localhost:8080, ngrok, or VPS URL):', current);
    if (newUrl !== null) {
      if (newUrl.trim() === '') {
        localStorage.removeItem('api_base_url');
        showToast('API URL reset to default.', 'info');
      } else {
        localStorage.setItem('api_base_url', newUrl.trim().replace(/\/$/, ''));
        showToast('API URL set: ' + newUrl.trim(), 'success');
      }
      setTimeout(() => window.location.reload(), 600);
    }
  }

  async function adminResetAllData() {
    try {
      showToast('Wiping all user accounts and resetting database...', 'info');
      await apiRequest('/api/admin/reset-database', { method: 'POST' });
      localStorage.clear();
      showToast('Database wiped successfully! Reloading fresh dashboard...', 'success');
      setTimeout(() => {
        window.location.reload();
      }, 1200);
    } catch (err) {
      showToast('Reset failed: ' + err.message, 'error');
    }
  }

  return {
    init,
    configureApiUrl,
    navigateTo,
    switchAuthMode,
    handleAuthRegister,
    handleAuthLogin,
    handleChangePassword,
    handleLogout,
    updateAdminVisibility,
    updateTopUserBar,
    toggleTopUserDropdown,
    closeTopUserDropdown,
    handleWalletSelected,
    toggleWalletDropdownCustom,
    closeCustomWalletDropdown,
    selectCustomWallet,
    selectDashboardWorkspace,
    setMaxAmount,
    handleAmountChanged,
    handleRecipientChanged,
    openPaymentConfirmation,
    confirmAndExecutePayment,
    initiatePayment,
    cancelPendingPayment,
    cancelActiveFromModal,
    openAddWalletModal,
    openCreateWorkspaceModal,
    submitCreateWorkspace,
    openRenameWorkspaceModal,
    submitRenameWorkspace,
    selectWalletWorkspace,
    backToWorkspaces,
    updateImportWalletDefaultName,
    handleConnectInBrowserWallet,
    submitImportWallet,
    handleDeleteWallet,
    setWalletGridColumns,
    toggleWalletDetails,
    openWalletHistory,
    loadPaymentsHistory,
    loadProfile,
    loadAdminView,
    openAdminUserDetails,
    toggleAdminWorkspace,
    deleteAdminUser,
    handleAdminLogin,
    handleAdminTogglePause,
    adminResetAllData,
    switchAdminTab,
    debounceAdminSearch,
    openAddReceivingModal,
    submitAddReceivingWallet,
    toggleReceivingActive,
    deleteReceivingAddress,
    copyAddress,
    togglePasswordVisibility,
    toggleWalletDropdown,
    useWalletForPayment,
    refreshSingleWallet,
    fillCeloFee,
    fillCeloFeeForSelected,
    openRenameWalletModal,
    submitRenameWallet,
    loadWallets,
    refreshAllWalletBalances,
    openModal,
    closeModal,
    setAmountPercent,
    copySelectedAddress,
    pasteRecipientAddress,
    handleQuickRecipientSelected,
    handleSavedRecipientSelected,
    openSaveRecipientModal,
    submitSaveRecipient,
  };
})();

window.app = app;

document.addEventListener('DOMContentLoaded', () => {
  app.init();
});
