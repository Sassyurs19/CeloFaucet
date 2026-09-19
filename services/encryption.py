"""
Encryption service for securing wallet private keys at rest.
Implements AES-256-GCM authenticated encryption with a unique 12-byte nonce per record.
Plaintext private keys are never stored, logged, or exposed.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from typing import Optional
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from config import config


class EncryptionService:
    """Provides authenticated AES-256-GCM encryption and decryption."""

    def __init__(self, master_key_hex: Optional[str] = None) -> None:
        key_str = master_key_hex or config.wallet_encryption_key
        self._key_bytes = self._derive_key_bytes(key_str)
        self._aesgcm = AESGCM(self._key_bytes)

    @staticmethod
    def _derive_key_bytes(key_input: str) -> bytes:
        """Ensure exactly 32 bytes (256 bits) for AES-256-GCM."""
        cleaned = key_input.strip()
        if not cleaned:
            # Fallback to random ephemeral key if none configured
            return secrets.token_bytes(32)
        try:
            if len(cleaned) == 64:
                return bytes.fromhex(cleaned)
        except ValueError:
            pass
        # If not 64 hex characters, derive deterministic 32-byte key via SHA-256
        return hashlib.sha256(cleaned.encode("utf-8")).digest()

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a plaintext private key string with AES-256-GCM.
        
        Returns:
            Format: '<nonce_hex>:<ciphertext_and_tag_hex>'
        """
        if not plaintext:
            raise ValueError("Cannot encrypt empty string.")
        
        # Standard 12-byte (96-bit) nonce for GCM
        nonce = secrets.token_bytes(12)
        data = plaintext.encode("utf-8")
        ciphertext = self._aesgcm.encrypt(nonce, data, None)
        
        return f"{nonce.hex()}:{ciphertext.hex()}"

    def decrypt(self, encrypted_payload: str) -> str:
        """
        Decrypt and authenticate an encrypted payload.
        
        Args:
            encrypted_payload: '<nonce_hex>:<ciphertext_and_tag_hex>'
            
        Returns:
            Plaintext string.
            
        Raises:
            ValueError: If payload format or authentication tag verification fails.
        """
        if not encrypted_payload or ":" not in encrypted_payload:
            raise ValueError("Invalid encrypted payload format.")
        
        parts = encrypted_payload.split(":", 1)
        if len(parts) != 2:
            raise ValueError("Malformed encrypted payload.")
        
        try:
            nonce = bytes.fromhex(parts[0])
            ciphertext = bytes.fromhex(parts[1])
            decrypted_bytes = self._aesgcm.decrypt(nonce, ciphertext, None)
            return decrypted_bytes.decode("utf-8")
        except Exception as e:
            raise ValueError(f"Decryption failed: authentication tag mismatch or corrupted data.") from None


# Global encryption service instance
encryption_service = EncryptionService()
