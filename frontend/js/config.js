/**
 * Production Environment Configuration for CELO USDT Payment Web Application.
 *
 * When deployed to Firebase Hosting, configure your Render Web Service URL here
 * by setting window.API_BASE_URL before this script runs during deployment.
 *
 * Example:
 * window.API_BASE_URL = "https://celofaucet.onrender.com";
 */
(function () {
  const isFile = typeof window !== 'undefined' && window.location.protocol === 'file:';
  const isLocal = typeof window !== 'undefined' && (
    window.location.hostname === 'localhost' ||
    window.location.hostname === '127.0.0.1' ||
    window.location.hostname === '0.0.0.0'
  );

  // Render service configured in render.yaml
  const defaultProductionBackend = 'https://celofaucet.onrender.com';
  let targetUrl = '';
  if (isFile) {
    targetUrl = 'http://localhost:8080';
  } else if (isLocal) {
    if (window.location.port === '8080') {
      // Running directly on the Python backend server
      targetUrl = '';
    } else {
      // Local dev servers (Live Server 5500, Vite 5173, etc.) connect to the local Python backend
      targetUrl = window.VITE_API_URL || window.API_BASE_URL || 'http://localhost:8080';
    }
  } else {
    // Firebase Hosting or external domain
    targetUrl = window.VITE_API_URL || window.API_BASE_URL || defaultProductionBackend;
  }

  window.API_BASE_URL = (targetUrl || '').replace(/\/$/, '');
})();
