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
  
  let targetUrl = '';
  if (isLocal) {
    // When running locally, always use same-origin local backend unless explicitly pointing to another local port
    if (storedUrl && (storedUrl.includes('localhost') || storedUrl.includes('127.0.0.1'))) {
      targetUrl = storedUrl;
    } else {
      targetUrl = '';
    }
  } else {
    // When running on Firebase Hosting or external domain
    targetUrl = window.VITE_API_URL || window.API_BASE_URL || storedUrl || 'https://celofaucet.onrender.com';
  }

  window.API_BASE_URL = (targetUrl || '').replace(/\/$/, '');
})();
