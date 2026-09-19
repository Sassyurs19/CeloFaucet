/**
 * Web3 Provider & Celo In-Browser Wallet Integration
 * Supports EIP-6963 multi-wallet discovery (MetaMask, OKX, Valora, Backpack, Trust Wallet, Rabby).
 * Resolves extension conflicts and enforces Celo Mainnet (Chain ID 42220 / 0xa4ec).
 */

const Web3Module = (function () {
  const CELO_CHAIN_ID_HEX = '0xa4ec'; // 42220
  const CELO_CHAIN_ID_DEC = 42220;
  const USAT_CONTRACT_ADDRESS = '0xd2ab3c9a02dbbab236bfec45d1d755df4267f771';
  // 2.00 USAT = 2,000,000 base units (6 decimals) -> hex 0x1e8480
  const USAT_TRANSFER_AMOUNT_HEX = '00000000000000000000000000000000000000000000000000000000001e8480';
  const ERC20_TRANSFER_METHOD = '0xa9059cbb';

  const CELO_CHAIN_CONFIG = {
    chainId: CELO_CHAIN_ID_HEX,
    chainName: 'Celo Mainnet',
    nativeCurrency: {
      name: 'CELO',
      symbol: 'CELO',
      decimals: 18,
    },
    rpcUrls: ['https://forno.celo.org'],
    blockExplorerUrls: ['https://celoscan.io'],
  };

  let connectedAccount = null;
  const announcedProviders = [];

  // EIP-6963: Multi-Injected Provider Discovery to avoid window.ethereum collisions
  if (typeof window !== 'undefined') {
    window.addEventListener('eip6963:announceProvider', (event) => {
      if (event.detail && !announcedProviders.some(p => p.info?.uuid === event.detail.info?.uuid)) {
        announcedProviders.push(event.detail);
      }
    });
    try {
      window.dispatchEvent(new Event('eip6963:requestProvider'));
    } catch (e) {
      // Ignored in strict environments
    }
  }

  /**
   * Safely locate active Web3 provider without crashing on extension collision.
   */
  function getProvider() {
    if (typeof window === 'undefined') return null;

    // 1. Check EIP-6963 announced providers (e.g. OKX, MetaMask, Backpack)
    if (announcedProviders.length > 0) {
      const okx = announcedProviders.find(p => p.info?.name?.toLowerCase().includes('okx'));
      if (okx) return okx.provider;
      const mm = announcedProviders.find(p => p.info?.name?.toLowerCase().includes('metamask'));
      if (mm) return mm.provider;
      return announcedProviders[0].provider;
    }

    // 2. Check window.okxwallet directly
    if (window.okxwallet && typeof window.okxwallet.request === 'function') {
      return window.okxwallet;
    }

    // 3. Check window.ethereum.providers array (multiple injected extensions)
    if (window.ethereum?.providers && Array.isArray(window.ethereum.providers) && window.ethereum.providers.length > 0) {
      const okx = window.ethereum.providers.find(p => p.isOKExWallet || p.isOkxWallet);
      if (okx) return okx;
      const mm = window.ethereum.providers.find(p => p.isMetaMask && !p.isBraveWallet);
      if (mm) return mm;
      return window.ethereum.providers[0];
    }

    // 4. Standard window.ethereum fallback
    if (window.ethereum && typeof window.ethereum.request === 'function') {
      return window.ethereum;
    }

    return null;
  }

  function hasInjectedProvider() {
    return Boolean(getProvider());
  }

  /**
   * Request user to connect their EVM wallet.
   * Returns lowercase checksummed/normalized address.
   */
  async function connectWallet() {
    const provider = getProvider();
    if (!provider) {
      throw new Error('No EVM wallet detected. Please open in OKX Wallet, MetaMask, or Valora.');
    }

    try {
      const accounts = await provider.request({
        method: 'eth_requestAccounts',
      });

      if (!accounts || accounts.length === 0) {
        throw new Error('No account returned from wallet.');
      }

      connectedAccount = accounts[0].toLowerCase();
      await ensureCeloNetwork();
      setupListeners(provider);
      return connectedAccount;
    } catch (err) {
      if (err.code === 4001) {
        throw new Error('Connection request was rejected by user.');
      }
      throw err;
    }
  }

  /**
   * Ensure user is switched to Celo Mainnet (42220).
   */
  async function ensureCeloNetwork() {
    const provider = getProvider();
    if (!provider) return;

    try {
      const currentChainId = await provider.request({ method: 'eth_chainId' });
      if (currentChainId && currentChainId.toLowerCase() === CELO_CHAIN_ID_HEX) {
        return;
      }

      try {
        await provider.request({
          method: 'wallet_switchEthereumChain',
          params: [{ chainId: CELO_CHAIN_ID_HEX }],
        });
      } catch (switchError) {
        if (switchError.code === 4902 || switchError.message?.includes('Unrecognized chain')) {
          await provider.request({
            method: 'wallet_addEthereumChain',
            params: [CELO_CHAIN_CONFIG],
          });
        } else {
          throw switchError;
        }
      }
    } catch (err) {
      console.warn('Network switch notice:', err);
      throw new Error('Please switch your wallet network to Celo Mainnet.');
    }
  }

  /**
   * Format ERC-20 transfer calldata for 2.00 USAT to recipient.
   */
  function buildTransferCalldata(recipientAddress) {
    const cleanAddr = recipientAddress.toLowerCase().replace(/^0x/, '');
    if (cleanAddr.length !== 40) {
      throw new Error('Invalid recipient address format.');
    }
    const paddedAddr = cleanAddr.padStart(64, '0');
    return ERC20_TRANSFER_METHOD + paddedAddr + USAT_TRANSFER_AMOUNT_HEX;
  }

  /**
   * Prompts user in their connected wallet to sign and broadcast the USDT transaction.
   * Uses backend prepared tx_params when provided (avoiding hardcoded contract / amounts).
   * Returns tx_hash string.
   */
  async function sendUSATPayment(fromAddress, recipientAddress, txParams = null) {
    const provider = getProvider();
    if (!provider) {
      throw new Error('No EVM wallet found for signing. Please open in OKX, MetaMask, or Valora.');
    }

    await ensureCeloNetwork();

    let finalParams = txParams;
    if (!finalParams) {
      const calldata = buildTransferCalldata(recipientAddress);
      finalParams = {
        from: fromAddress,
        to: window.USAT_CONTRACT_ADDRESS || '0xd2ab3c9a02dbbab236bfec45d1d755df4267f771',
        data: calldata,
        value: '0x0',
      };
    }

    try {
      const txHash = await provider.request({
        method: 'eth_sendTransaction',
        params: [finalParams],
      });

      if (!txHash) {
        throw new Error('Wallet did not return a transaction hash.');
      }

      return txHash;
    } catch (err) {
      if (err.code === 4001 || (err.message && err.message.toLowerCase().includes('user rejected'))) {
        throw new Error('Transaction was cancelled by user in wallet.');
      }
      throw err;
    }
  }

  let listenersAttached = false;
  function setupListeners(provider) {
    if (listenersAttached || !provider || typeof provider.on !== 'function') return;
    try {
      provider.on('accountsChanged', (accounts) => {
        if (accounts && accounts.length > 0) {
          connectedAccount = accounts[0].toLowerCase();
        } else {
          connectedAccount = null;
        }
      });
      listenersAttached = true;
    } catch (e) {
      // Ignore listener attachment warnings
    }
  }

  return {
    hasInjectedProvider,
    getProvider,
    connectWallet,
    ensureCeloNetwork,
    sendUSATPayment,
    getConnectedAccount: () => connectedAccount,
  };
})();
