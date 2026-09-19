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
  // Support window.API_BASE_URL, Vite-style VITE_API_URL, localStorage override, or default to production Render URL
  const storedUrl = typeof localStorage !== 'undefined' ? localStorage.getItem('api_base_url') : '';
  const configuredUrl = window.VITE_API_URL || window.API_BASE_URL || storedUrl || 'https://celofaucet.onrender.com';
  
  window.API_BASE_URL = configuredUrl.replace(/\/$/, '');
})();
