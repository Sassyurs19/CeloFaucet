/**
 * CELO USAT Payment Portal Application Controller
 * Professional Fintech / Web3 Interface
 * Zero emojis, Lucide SVG icons, dynamic USDT balance calculation,
 * 3-dot dropdown wallet cards, simplified profile, and comprehensive admin suite.
 */

const app = (function () {
  const STORAGE_KEY_TOKEN = 'usat_session_token';
  const STORAGE_KEY_ADMIN = 'usat_admin_token';
  const CELO_EXPLORER_BASE = 'https://celoscan.io/tx/';

  // Global App State
  let state = {
    user: null,
    sessionToken: localStorage.getItem(STORAGE_KEY_TOKEN) || null,
    adminToken: localStorage.getItem(STORAGE_KEY_ADMIN) || null,
    currentView: 'dashboard',
    currentAdminTab: 'overview',
    adminPaymentStatusFilter: '',
    wallets: [],
    selectedWalletId: null,
    receivingWallets: [],
    payments: [],
    isSubmitting: false,
    activePayment: null,
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
    const storedBaseUrl = typeof localStorage !== 'undefined' ? localStorage.getItem('api_base_url') : '';
    const baseUrl = (window.VITE_API_URL || window.API_BASE_URL || storedBaseUrl || '').replace(/\/$/, '');
    if (baseUrl) {
      throw new Error(`Production request failed for ${endpoint}`);
    }
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
      let wallets = getLocalStore('standalone_wallets', []);
      if (method === 'POST' && endpoint.includes('/connect')) {
        const newW = {
          id: Date.now(),
          address: body.address,
          label: 'Connected EVM Wallet',
          wallet_type: 'CONNECTED',
          balance_celo: 0.2500,
          balance_usat: 2.00,
          is_connected: true,
        };
        wallets.push(newW);
        setLocalStore('standalone_wallets', wallets);
        return { success: true, wallet: newW };
      }
      if (method === 'POST' && endpoint.includes('/import')) {
        const newW = {
          id: Date.now(),
          address: body.address || '0x84D118A43b60bd73D113c0ef08F238BE866E3A2b',
          label: body.label || 'Imported Wallet',
          wallet_type: 'IMPORTED',
          balance_celo: 0.1000,
          balance_usat: 2.00,
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

    // 8. Payments Create & Confirm
    if (endpoint.startsWith('/api/payments/create')) {
      const pid = 'pay_' + Date.now();
      const p = {
        payment_id: pid,
        amount: 2.00,
        status: 'PENDING',
        from_address: body.from_address || '',
        to_address: body.to_address || '',
        created_at: new Date().toISOString(),
        execution_mode: 'WALLET_CONNECT',
      };
      const payments = getLocalStore('standalone_payments', []);
      payments.unshift(p);
      setLocalStore('standalone_payments', payments);
      return {
        success: true,
        payment_id: pid,
        amount: 2.00,
        currency: 'USAT',
        network: 'Celo Mainnet',
        from: body.from_address,
        to: body.to_address,
        execution_mode: 'WALLET_CONNECT',
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
      return {
        total_payments: payments.length || 12,
        successful_payments: payments.filter(p => p.status === 'SUCCESS').length || 10,
        total_volume_usat: (payments.length || 12) * 2.00,
        active_wallets: wallets.length || 4,
        paused: false,
      };
    }

    if (endpoint.startsWith('/api/admin/funding')) {
      return {
        funding_wallet: '0x84D118A43b60bd73D113c0ef08F238BE866E3A2b',
        balance_celo: 1.4520,
        threshold: 0.005,
        subsidy_amount: 0.05,
        total_subsidies: 12,
        total_celo_distributed: 0.60,
        transactions: [
          {
            id: 1,
            recipient: '0x84D118A43b60bd73D113c0ef08F238BE866E3A2b',
            amount: 0.05,
            tx_hash: '0x3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b',
            created_at: new Date().toISOString(),
          }
        ]
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
    if (state.adminToken) {
      headers['X-Admin-Token'] = state.adminToken;
    }

    const storedBaseUrl = typeof localStorage !== 'undefined' ? localStorage.getItem('api_base_url') : '';
    const baseUrl = (window.VITE_API_URL || window.API_BASE_URL || storedBaseUrl || '').replace(/\/$/, '');
    const url = (baseUrl && endpoint.startsWith('/')) ? `${baseUrl}${endpoint}` : endpoint;
    
    let resp = null;
    let isJson = false;

    try {
      resp = await fetch(url, {
        ...options,
        headers,
      });
      const contentType = resp.headers.get('content-type') || '';
      isJson = contentType.includes('application/json');
    } catch (fetchErr) {
      if (baseUrl) {
        throw new Error('Unable to connect to Render backend API. Please verify your connection or try again.');
      }
      isJson = false;
    }

    // If backend API is not available or returned non-JSON HTML (static Firebase rewrite)
    if (!resp || !isJson) {
      if (baseUrl) {
        throw new Error(`Invalid response (${resp ? resp.status : 'offline'}) from backend.`);
      }
      return handleStandaloneFallback(endpoint, options);
    }

    const data = await resp.json().catch(() => ({}));

    if (!resp.ok) {
      // If a protected session endpoint returns 401, log out (session expired/invalid)
      // DO NOT call handleLogout on login, register, or admin auth attempts
      const isAuthAttempt = endpoint.includes('/auth/login') || endpoint.includes('/auth/register') || endpoint.includes('/admin/login');
      if (resp.status === 401 && !isAuthAttempt) {
        handleLogout(false);
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
    // Admin gate check
    if (viewId === 'admin') {
      const isEligible = Boolean(state.user && state.user.is_admin_eligible);
      if (!isEligible && !state.adminToken) {
        showToast('Access restricted: Admin Portal is not available.', 'error');
        return;
      }
    }

    state.currentView = viewId;

    if (!state.sessionToken && viewId !== 'auth' && viewId !== 'admin') {
      showAuthView();
      return;
    }

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
    if (!state.sessionToken || state.sessionToken.startsWith('standalone_')) {
      if (state.sessionToken) {
        localStorage.removeItem(STORAGE_KEY_TOKEN);
        state.sessionToken = null;
      }
      updateAdminVisibility();
      showAuthView();
      return;
    }

    try {
      const data = await apiRequest('/api/auth/me');
      if (data.authenticated && data.user) {
        state.user = data.user;
        updateTopUserBar();
        updateAdminVisibility();
        navigateTo('dashboard');
      } else {
        handleLogout(false);
      }
    } catch (err) {
      handleLogout(false);
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

      state.sessionToken = data.token;
      state.user = data.user;
      localStorage.setItem(STORAGE_KEY_TOKEN, data.token);

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

      state.sessionToken = data.token;
      state.user = data.user;
      localStorage.setItem(STORAGE_KEY_TOKEN, data.token);

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
    if (notifyServer && state.sessionToken) {
      try {
        await apiRequest('/api/auth/logout', { method: 'POST' }).catch(() => {});
      } catch (e) {}
    }
    state.sessionToken = null;
    state.user = null;
    state.wallets = [];
    localStorage.removeItem(STORAGE_KEY_TOKEN);
    updateTopUserBar();
    updateAdminVisibility();
    switchAuthMode('login');
    showAuthView();
    if (notifyServer) {
      showToast('Signed out successfully.', 'info');
    }
  }

  function updateTopUserBar() {
    const userPill = document.getElementById('top-user-pill');
    const userName = document.getElementById('top-user-name');
    if (state.user) {
      if (userPill) userPill.style.display = 'flex';
      if (userName) userName.textContent = state.user.full_name || state.user.name || 'User';
    } else {
      if (userPill) userPill.style.display = 'none';
    }
  }

  // --- Dashboard & Dynamic USDT Calculation ---

  async function loadDashboardData() {
    if (state.user) {
      const dashUser = document.getElementById('dash-user-name');
      if (dashUser) dashUser.textContent = state.user.full_name || state.user.name || 'User';
    }
    await Promise.all([loadWallets(), loadReceivingWallets()]);
  }

  async function loadReceivingWallets() {
    try {
      const data = await apiRequest('/api/receiving-wallets');
      state.receivingWallets = data.receiving_wallets || [];

      const select = document.getElementById('select-receiving-wallet');
      if (!select) return;

      select.innerHTML = '';
      if (state.receivingWallets.length === 0) {
        select.innerHTML = '<option value="">No receiving addresses configured</option>';
        return;
      }

      state.receivingWallets.forEach((rw) => {
        const opt = document.createElement('option');
        opt.value = rw.id;
        opt.textContent = `${rw.name || rw.label || 'Receiving Address'} (${formatShortAddress(rw.address)})`;
        select.appendChild(opt);
      });

      // Auto-select Admin (Sassy) or first active
      const defaultRecv = state.receivingWallets.find((r) => r.name && r.name.toLowerCase().includes('sassy')) || state.receivingWallets[0];
      if (defaultRecv) {
        select.value = defaultRecv.id;
      }
    } catch (err) {
      showToast('Failed to load receiving destinations: ' + err.message, 'error');
    }
  }

  async function loadWallets() {
    try {
      const data = await apiRequest('/api/wallets');
      state.wallets = data.wallets || [];

      // Calculate dynamic live total USDT balance across all user wallets
      const totalUsdt = state.wallets.reduce((sum, w) => sum + parseFloat(w.usat_balance || 0), 0);
      const totalUsdtFormatted = `$${totalUsdt.toFixed(2)}`;

      // Update Dashboard Hero Card
      const dashTotal = document.getElementById('dash-total-usdt-balance');
      const dashCount = document.getElementById('dash-wallets-count');
      if (dashTotal) dashTotal.textContent = totalUsdtFormatted;
      if (dashCount) dashCount.textContent = state.wallets.length;

      // Update Wallets Summary Card
      const walSummaryUsdt = document.getElementById('wallets-summary-usdt');
      const walSummaryCount = document.getElementById('wallets-summary-count');
      if (walSummaryUsdt) walSummaryUsdt.textContent = totalUsdtFormatted;
      if (walSummaryCount) walSummaryCount.textContent = state.wallets.length;

      renderWalletsSelect();
      renderWalletsList();
      renderIcons();
    } catch (err) {
      showToast('Failed to load wallets: ' + err.message, 'error');
    }
  }

  function renderWalletsSelect() {
    const select = document.getElementById('select-send-wallet');
    if (!select) return;

    select.innerHTML = '';
    if (state.wallets.length === 0) {
      select.innerHTML = '<option value="">-- No wallets added yet. Click + Add Wallet --</option>';
      handleWalletSelected();
      return;
    }

    const defaultOpt = document.createElement('option');
    defaultOpt.value = '';
    defaultOpt.textContent = '-- Select your wallet --';
    select.appendChild(defaultOpt);

    state.wallets.forEach((w) => {
      const opt = document.createElement('option');
      opt.value = w.id;
      const name = w.name || w.label || 'My Wallet';
      const shortAddr = formatShortAddress(w.address);
      const usdt = parseFloat(w.usat_balance || 0).toFixed(2);
      // Display: Wallet Name (0x...) — Available: X.XX USDT
      opt.textContent = `${name} (${shortAddr}) — Available: ${usdt} USDT`;
      select.appendChild(opt);
    });

    if (state.selectedWalletId && state.wallets.some((w) => w.id === state.selectedWalletId)) {
      select.value = state.selectedWalletId;
    } else if (state.wallets.length === 1) {
      select.value = state.wallets[0].id;
    }

    handleWalletSelected();
  }

  function handleWalletSelected() {
    const select = document.getElementById('select-send-wallet');
    const summaryBox = document.getElementById('dash-wallet-summary-box');
    const selectedNameEl = document.getElementById('dash-selected-wallet-name');
    const selectedAddrEl = document.getElementById('dash-selected-wallet-address');
    const selectedBalEl = document.getElementById('dash-selected-wallet-balance');
    const amountHintEl = document.getElementById('amount-validation-hint');

    const walletId = parseInt(select?.value, 10);
    const wallet = state.wallets.find((w) => w.id === walletId);

    if (!wallet) {
      state.selectedWalletId = null;
      if (summaryBox) summaryBox.style.display = 'none';
      if (amountHintEl) amountHintEl.textContent = 'Maximum: Available wallet USDT balance.';
      return;
    }

    state.selectedWalletId = walletId;
    if (summaryBox) summaryBox.style.display = 'flex';

    const name = wallet.name || wallet.label || 'My Wallet';
    const usat = parseFloat(wallet.usat_balance || 0);
    const shortAddr = formatShortAddress(wallet.address);

    if (selectedNameEl) selectedNameEl.textContent = name;
    if (selectedAddrEl) selectedAddrEl.textContent = `(${shortAddr})`;
    if (selectedBalEl) selectedBalEl.textContent = `${usat.toFixed(2)} USDT`;
    if (amountHintEl) amountHintEl.textContent = `Available Balance: ${usat.toFixed(2)} USDT in ${name}`;
  }

  function setMaxAmount() {
    const wallet = state.wallets.find((w) => w.id === state.selectedWalletId);
    if (!wallet) {
      showToast('Please select a sending wallet first.', 'error');
      return;
    }
    const usat = parseFloat(wallet.usat_balance || 0);
    const input = document.getElementById('input-transfer-amount');
    if (input) {
      input.value = usat > 0 ? usat.toFixed(2) : '0.00';
      handleAmountChanged();
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
      if (badge) badge.style.display = 'none';
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
    const selectedId = select && select.value ? parseInt(select.value, 10) : state.selectedWalletId;
    const wallet = state.wallets.find((w) => String(w.id) === String(selectedId || state.selectedWalletId));

    if (!wallet) {
      showToast('Please select a sending wallet first from the dropdown.', 'error');
      select?.focus();
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

    state.pendingPayment = {
      wallet,
      amount: amountVal,
      recipient: recipientAddr,
    };

    openModal('modal-payment-confirm');
  }

  async function confirmAndExecutePayment() {
    closeModal('modal-payment-confirm');
    if (!state.pendingPayment || state.isSubmitting) return;

    const { wallet, amount, recipient } = state.pendingPayment;
    state.isSubmitting = true;

    openPaymentModal();
    setPaymentStep(1, 'Verifying balances on Celo Mainnet...');

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
        await new Promise((r) => setTimeout(r, 1200));
      } else {
        setPaymentStep(2, 'CELO gas balance sufficient. Proceeding to transfer...');
      }

      const wType = (wallet.wallet_type || wallet.type || '').toLowerCase();
      if (wType === 'imported') {
        setPaymentStep(3, `Broadcasting ${amount.toFixed(2)} USDT on Celo Mainnet...`);
        await new Promise((r) => setTimeout(r, 1000));

        const confirmedTx = res.tx_hash || payment.tx_hash;
        if (confirmedTx) {
          finishPaymentSuccess(confirmedTx, wallet, recipient, amount);
        } else {
          throw new Error(payment.error_message || res.error || 'Payment execution failed.');
        }
      } else {
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
      }
    } catch (err) {
      setPaymentFailed(err.message || 'Payment failed.');
    } finally {
      state.isSubmitting = false;
      state.pendingPayment = null;
      loadWallets();
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
    const linkCont = document.getElementById('pay-tx-link-container');
    const closeBtn = document.getElementById('btn-close-pay-modal');

    if (modal) modal.classList.add('active');
    if (headerTitle) headerTitle.textContent = 'Processing Payment';
    if (stepInd) stepInd.style.display = 'flex';
    if (receiptCard) receiptCard.style.display = 'none';
    if (actionBtn) actionBtn.style.display = 'none';
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
      actionBtn.textContent = 'Done & View Activity';
      actionBtn.onclick = () => {
        closeModal('modal-payment-progress');
        navigateTo('payments');
      };
    }
    if (closeBtn) closeBtn.style.display = 'block';

    renderIcons();
    showToast('Payment confirmed on Celo Mainnet!', 'success');
  }

  function setPaymentFailed(errorMsg) {
    const iconCont = document.getElementById('pay-progress-icon-container');
    const titleEl = document.getElementById('pay-progress-title');
    const descEl = document.getElementById('pay-progress-desc');
    const actionBtn = document.getElementById('btn-pay-modal-action');
    const closeBtn = document.getElementById('btn-close-pay-modal');

    if (iconCont) {
      iconCont.innerHTML = '<i data-lucide="x-circle" class="icon-lg" style="color:var(--danger);"></i>';
    }
    if (titleEl) titleEl.textContent = 'Payment Failed';
    if (descEl) descEl.textContent = errorMsg;

    if (actionBtn) {
      actionBtn.style.display = 'block';
      actionBtn.textContent = 'Dismiss';
      actionBtn.onclick = () => closeModal('modal-payment-progress');
    }
    if (closeBtn) closeBtn.style.display = 'block';

    renderIcons();
    showToast(`Payment error: ${errorMsg}`, 'error');
  }

  // --- Wallet Cards & 3-Dot Dropdown Actions ---

  function renderWalletsList() {
    const container = document.getElementById('wallets-list-container');
    if (!container) return;

    if (state.wallets.length === 0) {
      container.innerHTML = `
        <div class="card" style="text-align:center; padding:36px 20px; grid-column: 1 / -1;">
          <div style="margin-bottom:12px;">
            <i data-lucide="wallet" class="icon-lg" style="color:var(--text-muted);"></i>
          </div>
          <h3 style="font-size:18px;">No Wallets Added Yet</h3>
          <p class="description" style="margin-top:4px;">Connect an in-browser wallet or import a private key to start making payments.</p>
          <button class="btn btn-primary" onclick="app.openAddWalletModal()">
            <i data-lucide="plus" class="icon-sm"></i>
            <span>Add Your First Wallet</span>
          </button>
        </div>
      `;
      renderIcons();
      return;
    }

    let html = '';
    state.wallets.forEach((w) => {
      const isConnected = w.wallet_type === 'connected';
      const badgeClass = isConnected ? 'badge-blue' : 'badge-green';
      const typeLabel = isConnected ? 'Connected Wallet' : 'Imported Wallet';
      const walletName = escapeHtml(w.name || w.label || 'My Wallet');
      const usdt = parseFloat(w.usat_balance || 0).toFixed(2);
      const celo = parseFloat(w.celo_balance || 0).toFixed(4);

      html += `
        <div class="wallet-card">
          <div class="wallet-card-header">
            <div>
              <div class="wallet-card-title">${walletName}</div>
              <div class="wallet-address-row">
                <span class="code-address">${formatShortAddress(w.address)}</span>
                <button type="button" class="btn-copy" onclick="app.copyAddress('${w.address}')" title="Copy Address">
                  <i data-lucide="copy" class="icon-sm"></i>
                </button>
                <span class="badge ${badgeClass}" style="margin-left:4px;">${typeLabel}</span>
              </div>
            </div>

            <!-- 3-Dot Dropdown Menu -->
            <div class="dropdown" id="dropdown-wallet-${w.id}">
              <button type="button" class="dropdown-toggle" onclick="app.toggleWalletDropdown(${w.id}, event)" title="Wallet Actions">
                <i data-lucide="more-horizontal"></i>
              </button>
              <div class="dropdown-menu">
                <button type="button" class="dropdown-item" onclick="app.useWalletForPayment(${w.id})">
                  <i data-lucide="arrow-right" class="icon-sm"></i>
                  <span>Use for Payment</span>
                </button>
                <button type="button" class="dropdown-item" onclick="app.openRenameWalletModal(${w.id}, '${escapeHtml(w.name || w.label || '')}')">
                  <i data-lucide="pencil" class="icon-sm"></i>
                  <span>Rename</span>
                </button>
                <button type="button" class="dropdown-item" onclick="app.refreshSingleWallet(${w.id})">
                  <i data-lucide="refresh-cw" class="icon-sm"></i>
                  <span>Refresh Balance</span>
                </button>
                <button type="button" class="dropdown-item danger" onclick="app.handleDeleteWallet(${w.id})">
                  <i data-lucide="trash-2" class="icon-sm"></i>
                  <span>Remove Wallet</span>
                </button>
              </div>
            </div>
          </div>

          <div class="wallet-balance-row">
            <div class="wallet-balance-label">USDT Balance</div>
            <div class="wallet-usdt-amount">$${usdt}</div>
            <div class="wallet-celo-amount">${celo} CELO Gas</div>
          </div>

          <div>
            <button class="btn btn-secondary btn-sm" style="width:100%;" onclick="app.useWalletForPayment(${w.id})">
              <i data-lucide="arrow-right-circle" class="icon-sm"></i>
              <span>Select for Checkout</span>
            </button>
          </div>
        </div>
      `;
    });

    container.innerHTML = html;
    renderIcons();
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
    const stepChoice = document.getElementById('add-wallet-step-choice');
    const stepImport = document.getElementById('add-wallet-step-import');

    if (modal) modal.classList.add('active');
    if (stepChoice) stepChoice.style.display = 'block';
    if (stepImport) stepImport.style.display = 'none';

    const pkInput = document.getElementById('import-private-key');
    const labelInput = document.getElementById('import-wallet-label');
    if (pkInput) pkInput.value = '';
    if (labelInput) labelInput.value = '';
    renderIcons();
  }

  function showImportWalletForm() {
    const stepChoice = document.getElementById('add-wallet-step-choice');
    const stepImport = document.getElementById('add-wallet-step-import');
    if (stepChoice) stepChoice.style.display = 'none';
    if (stepImport) stepImport.style.display = 'block';
    renderIcons();
  }

  function backToAddWalletChoice() {
    const stepChoice = document.getElementById('add-wallet-step-choice');
    const stepImport = document.getElementById('add-wallet-step-import');
    if (stepChoice) stepChoice.style.display = 'block';
    if (stepImport) stepImport.style.display = 'none';
    renderIcons();
  }

  async function handleConnectInBrowserWallet() {
    try {
      showToast('Connecting in-browser wallet...', 'info');
      const address = await Web3Module.connectWallet();

      await apiRequest('/api/wallets/connect', {
        method: 'POST',
        body: JSON.stringify({
          address,
          name: 'Connected In-Browser Wallet',
        }),
      });

      showToast('Wallet connected successfully!', 'success');
      closeModal('modal-add-wallet');
      await loadWallets();
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

    const words = rawKey.split(/\s+/);
    if (words.length >= 12 || rawKey.includes(' ')) {
      showToast('Recovery phrases are not supported. Please use Connect Wallet or a private key.', 'error');
      return;
    }

    try {
      btn.disabled = true;
      btn.innerHTML = '<i data-lucide="loader-2" class="icon-sm" style="animation:spin 1s linear infinite;"></i> Encrypting & Importing...';
      renderIcons();

      await apiRequest('/api/wallets/import', {
        method: 'POST',
        body: JSON.stringify({
          private_key: rawKey,
          name: name || 'Imported Celo Wallet',
        }),
      });

      showToast('Wallet securely imported!', 'success');
      pkInput.value = '';
      labelInput.value = '';
      closeModal('modal-add-wallet');
      await loadWallets();
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Import Wallet';
    }
  }

  async function handleDeleteWallet(walletId) {
    if (!confirm('Are you sure you want to remove this wallet?')) return;

    try {
      await apiRequest(`/api/wallets/${walletId}`, { method: 'DELETE' });
      showToast('Wallet removed.', 'success');
      await loadWallets();
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  // --- Payments History View ---

  async function loadPaymentsHistory() {
    const tbody = document.getElementById('payments-table-body');
    const mobileContainer = document.getElementById('payments-mobile-cards-container');
    if (!tbody) return;

    try {
      const data = await apiRequest('/api/payments');
      const payments = data.payments || [];

      // Update Top Metrics Cards
      const totalVolume = payments
        .filter((p) => p.status === 'SUCCESS' || p.status === 'CONFIRMED')
        .reduce((acc, p) => acc + parseFloat(p.amount || 0), 0);
      const completedCount = payments.filter((p) => p.status === 'SUCCESS' || p.status === 'CONFIRMED').length;
      const gasCount = payments.filter((p) => p.celo_funded).length;

      const volEl = document.getElementById('stat-payments-total-volume');
      const compEl = document.getElementById('stat-payments-completed-count');
      const gasEl = document.getElementById('stat-payments-gas-count');

      if (volEl) volEl.textContent = `$${totalVolume.toFixed(2)} USDT`;
      if (compEl) compEl.textContent = completedCount.toString();
      if (gasEl) gasEl.textContent = gasCount.toString();

      if (payments.length === 0) {
        tbody.innerHTML = `
          <tr>
            <td colspan="7" style="text-align:center; padding:32px; color:var(--text-muted);">
              No payment transactions recorded yet.
            </td>
          </tr>
        `;
        if (mobileContainer) {
          mobileContainer.innerHTML = `
            <div class="card" style="text-align:center; padding:32px; color:var(--text-muted);">
              No payment transactions recorded yet.
            </div>
          `;
        }
        return;
      }

      let tableHtml = '';
      let mobileHtml = '';

      payments.forEach((p) => {
        const amtStr = parseFloat(p.amount || 0).toFixed(2);
        let statusBadge = '<span class="badge badge-warning">PROCESSING</span>';
        if (p.status === 'SUCCESS' || p.status === 'CONFIRMED') {
          statusBadge = '<span class="badge badge-green">SUCCESS</span>';
        } else if (p.status === 'FAILED') {
          statusBadge = '<span class="badge badge-danger">FAILED</span>';
        }

        const txLink = p.tx_hash
          ? `<a href="${CELO_EXPLORER_BASE}${p.tx_hash}" target="_blank" rel="noopener" style="color:var(--accent-blue); text-decoration:none; font-weight:600; display:inline-flex; align-items:center; gap:4px;">
               <span>${formatShortAddress(p.tx_hash)}</span>
               <i data-lucide="external-link" class="icon-sm"></i>
             </a>`
          : '<span style="color:var(--text-muted);">-</span>';

        const gasFundedBadge = p.celo_funded
          ? '<span class="badge badge-green">0.05 CELO Gas</span>'
          : '<span style="color:var(--text-muted); font-size:12px;">Standard</span>';

        const dateStr = p.created_at ? new Date(p.created_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '-';

        // Desktop Table Row
        tableHtml += `
          <tr>
            <td>${statusBadge}</td>
            <td style="font-weight:700; color:var(--celo-green-dark);">${amtStr} USDT</td>
            <td><span class="code-address">${formatShortAddress(p.from_address)}</span></td>
            <td><span class="code-address">${formatShortAddress(p.to_address)}</span></td>
            <td>${txLink}</td>
            <td>${gasFundedBadge}</td>
            <td style="font-size:12px; color:var(--text-muted);">${dateStr}</td>
          </tr>
        `;

        // Mobile Card View
        mobileHtml += `
          <div class="payment-mobile-card">
            <div class="pmc-header">
              <span class="pmc-amount">${amtStr} USDT</span>
              ${statusBadge}
            </div>
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
              <span class="pmc-label">Gas Subsidy:</span>
              <span class="pmc-value">${gasFundedBadge}</span>
            </div>
            <div class="pmc-row">
              <span class="pmc-label">Date:</span>
              <span style="font-size:11px; color:var(--text-muted);">${dateStr}</span>
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

      tbody.innerHTML = tableHtml;
      if (mobileContainer) {
        mobileContainer.innerHTML = mobileHtml;
      }
      renderIcons();
    } catch (err) {
      showToast('Failed to load history: ' + err.message, 'error');
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

      state.adminToken = data.token;
      localStorage.setItem(STORAGE_KEY_ADMIN, data.token);
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
    } else if (tabName === 'receiving') {
      loadAdminReceiving();
    } else if (tabName === 'funding') {
      loadAdminFunding();
    } else if (tabName === 'settings') {
      loadAdminSettings();
    }

    renderIcons();
  }

  async function loadAdminOverview() {
    try {
      const [dash, funding] = await Promise.all([
        apiRequest('/api/admin/dashboard'),
        apiRequest('/api/admin/funding'),
      ]);

      const volEl = document.getElementById('admin-kpi-volume');
      const payEl = document.getElementById('admin-kpi-payments');
      const usrEl = document.getElementById('admin-kpi-users');
      const celoEl = document.getElementById('admin-overview-celo-bal');

      if (volEl) volEl.textContent = `$${parseFloat(dash.total_volume_usat || 0).toFixed(2)}`;
      if (payEl) payEl.textContent = dash.total_payments || 0;
      if (usrEl) usrEl.textContent = dash.total_users || 0;
      if (celoEl) celoEl.textContent = `${funding.celo_balance} CELO`;

      renderIcons();
    } catch (err) {
      // Non-blocking error
    }
  }

  function debounceAdminSearch(type) {
    clearTimeout(searchDebounceTimers[type]);
    searchDebounceTimers[type] = setTimeout(() => {
      if (type === 'wallets') loadAdminWallets();
      if (type === 'payments') loadAdminPayments();
    }, 300);
  }

  function triggerAdminPaymentsFilter() {
    loadAdminPayments();
  }

  function setAdminDatePreset(preset) {
    const dateInput = document.getElementById('admin-payments-date');
    if (!dateInput) return;
    if (preset === 'today') {
      const now = new Date();
      const year = now.getFullYear();
      const month = String(now.getMonth() + 1).padStart(2, '0');
      const day = String(now.getDate()).padStart(2, '0');
      dateInput.value = `${year}-${month}-${day}`;
    }
    loadAdminPayments();
  }

  function resetAdminPaymentsFilter() {
    const searchInput = document.getElementById('admin-payments-search');
    const dateInput = document.getElementById('admin-payments-date');
    const fromInput = document.getElementById('admin-payments-time-from');
    const toInput = document.getElementById('admin-payments-time-to');
    if (searchInput) searchInput.value = '';
    if (dateInput) dateInput.value = '';
    if (fromInput) fromInput.value = '';
    if (toInput) toInput.value = '';
    state.adminPaymentStatusFilter = '';
    document.querySelectorAll('.filter-pill').forEach((p) => {
      p.classList.toggle('active', p.getAttribute('data-status') === '');
    });
    loadAdminPayments();
  }

  function setAdminPaymentStatusFilter(status) {
    state.adminPaymentStatusFilter = status;
    document.querySelectorAll('.filter-pill').forEach((p) => {
      p.classList.toggle('active', p.getAttribute('data-status') === status);
    });
    loadAdminPayments();
  }

  async function loadAdminPayments() {
    const container = document.getElementById('admin-payments-card-list');
    const summaryText = document.getElementById('admin-filter-summary-text');
    const windowLabel = document.getElementById('admin-filter-window-label');
    if (!container) return;

    const searchInput = document.getElementById('admin-payments-search');
    const dateInput = document.getElementById('admin-payments-date');
    const fromInput = document.getElementById('admin-payments-time-from');
    const toInput = document.getElementById('admin-payments-time-to');

    const search = searchInput ? searchInput.value.trim() : '';
    const dateVal = dateInput ? dateInput.value.trim() : '';
    const timeFrom = fromInput ? fromInput.value.trim() : '';
    const timeTo = toInput ? toInput.value.trim() : '';
    const status = state.adminPaymentStatusFilter || '';

    // Build filter label
    let filterBadges = [];
    if (search) filterBadges.push(`Search: "${search}"`);
    if (dateVal) filterBadges.push(`Date: ${dateVal}`);
    if (timeFrom || timeTo) filterBadges.push(`Time: ${timeFrom || '00:00'} - ${timeTo || '23:59'}`);
    if (status) filterBadges.push(`Status: ${status}`);

    if (windowLabel) {
      windowLabel.textContent = filterBadges.length > 0 ? filterBadges.join(' | ') : 'All records';
    }

    try {
      const queryParams = new URLSearchParams();
      if (search) queryParams.append('search', search);
      if (status) queryParams.append('status', status);
      if (dateVal) queryParams.append('date', dateVal);
      if (timeFrom) queryParams.append('time_from', timeFrom);
      if (timeTo) queryParams.append('time_to', timeTo);

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
            <div style="font-size:12px; margin-top:4px;">Try searching a different name, or clear time/date filters.</div>
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
      const [funding, hist] = await Promise.all([
        apiRequest('/api/admin/funding'),
        apiRequest('/api/admin/funding/transactions'),
      ]);

      const balEl = document.getElementById('admin-funding-balance');
      const givenEl = document.getElementById('admin-funding-given');
      if (balEl) balEl.textContent = `${funding.celo_balance} CELO`;
      if (givenEl) givenEl.textContent = funding.total_subsidies_given || 0;

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
          html += `
            <div class="admin-card-item">
              <div class="admin-card-top">
                <div>
                  <div style="font-weight:700; font-size:14px;">User: ${escapeHtml(t.user_name || t.full_name || 'User')}</div>
                  <div style="font-size:12px; color:var(--text-muted);">Recipient: <span class="code-address">${formatShortAddress(t.recipient_wallet)}</span></div>
                </div>
                <div style="font-size:13px; font-weight:700; color:var(--celo-green-dark);">+0.05 CELO</div>
              </div>
              <div class="admin-card-footer">
                <div style="display:flex; align-items:center; gap:6px;">
                  <span style="color:var(--text-muted);">Tx:</span>
                  <a href="${CELO_EXPLORER_BASE}${t.funding_tx_hash}" target="_blank" rel="noopener" style="color:var(--accent-blue); text-decoration:none;">
                    ${formatShortAddress(t.funding_tx_hash)}
                  </a>
                </div>
                <div>${t.created_at || '-'}</div>
              </div>
            </div>
          `;
        });
        container.innerHTML = html;
        renderIcons();
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
    if (!confirm('Are you sure you want to delete this receiving address?')) return;
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
    checkSession();
    renderIcons();
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
    handleWalletSelected,
    setMaxAmount,
    handleAmountChanged,
    handleRecipientChanged,
    openPaymentConfirmation,
    confirmAndExecutePayment,
    initiatePayment,
    openAddWalletModal,
    showImportWalletForm,
    backToAddWalletChoice,
    handleConnectInBrowserWallet,
    submitImportWallet,
    handleDeleteWallet,
    loadPaymentsHistory,
    loadProfile,
    loadAdminView,
    handleAdminLogin,
    handleAdminTogglePause,
    switchAdminTab,
    debounceAdminSearch,
    triggerAdminPaymentsFilter,
    setAdminDatePreset,
    resetAdminPaymentsFilter,
    setAdminPaymentStatusFilter,
    openAddReceivingModal,
    submitAddReceivingWallet,
    toggleReceivingActive,
    deleteReceivingAddress,
    copyAddress,
    togglePasswordVisibility,
    toggleWalletDropdown,
    useWalletForPayment,
    refreshSingleWallet,
    openRenameWalletModal,
    submitRenameWallet,
    loadWallets,
    openModal,
    closeModal,
  };
})();

document.addEventListener('DOMContentLoaded', () => {
  app.init();
});
