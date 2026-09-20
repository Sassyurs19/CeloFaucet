/**
 * Production Environment Configuration for CELO USDT Payment Web Application.
 *
 * When deployed to Firebase Hosting, configure your Render Web Service URL here
 * or set it dynamically in the browser console via localStorage.setItem('api_base_url', 'https://your-service.onrender.com').
 *
 * Example:
 * window.API_BASE_URL = "https://celo-usdt-backend.onrender.com";
 */
(function () {
  const isLocal = typeof window !== 'undefined' && (
    window.location.hostname === 'localhost' ||
    window.location.hostname === '127.0.0.1' ||
    window.location.hostname === '0.0.0.0'
  );

  const storedUrl = typeof localStorage !== 'undefined' ? (localStorage.getItem('api_base_url') || '') : '';
  
  const defaultProductionBackend = 'https://celofaucet.onrender.com';
  let targetUrl = '';
  if (isLocal) {
    if (storedUrl) {
      targetUrl = storedUrl;
    } else if (window.location.port === '8080') {
      // Running directly on the Python backend server
      targetUrl = '';
    } else {
      // Local dev servers (Live Server 5500, 3000, etc.) connect to the live backend
      targetUrl = window.VITE_API_URL || window.API_BASE_URL || defaultProductionBackend;
    }
  } else {
    // Firebase Hosting or external domain
    targetUrl = window.VITE_API_URL || window.API_BASE_URL || storedUrl || defaultProductionBackend;
  }

  window.API_BASE_URL = (targetUrl || '').replace(/\/$/, '');
})();
