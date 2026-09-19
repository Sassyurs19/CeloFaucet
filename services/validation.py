"""
Address validation service for Celo and EVM-compatible addresses.
Ensures strict format verification, prevents malformed inputs, and returns checksummed addresses.
"""

from __future__ import annotations

import re
from typing import Tuple, Optional
from web3 import Web3

# Strict hex pattern: 0x followed by exactly 40 hexadecimal characters
HEX_ADDRESS_REGEX = re.compile(r"^0x[a-fA-F0-9]{40}$")


def validate_celo_address(address_str: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Validates a Celo wallet address.

    Returns:
        (is_valid, checksum_address, error_description)
    """
    if not address_str or not isinstance(address_str, str):
        return False, None, "Address input cannot be empty."

    cleaned = address_str.strip()

    # Reject common non-address inputs
    if cleaned.startswith("@"):
        return False, None, "Telegram usernames are not valid wallet addresses."

    if "://" in cleaned or cleaned.startswith("www."):
        return False, None, "URLs or web links are not valid wallet addresses."

    if "." in cleaned:
        return (
            False,
            None,
            "Domain names or ENS addresses (e.g. .eth, .celo) are not supported. "
            "Please provide a 0x hex address.",
        )

    # Check prefix
    if not cleaned.startswith("0x") and not cleaned.startswith("0X"):
        return (
            False,
            None,
            "Address must start with '0x'. Example: 0x1234567890abcdef1234567890abcdef12345678",
        )

    # Check length and hex format
    if len(cleaned) != 42:
        return (
            False,
            None,
            f"Invalid address length ({len(cleaned)} characters). "
            "A standard Celo address must have exactly 42 characters including '0x'.",
        )

    if not HEX_ADDRESS_REGEX.match(cleaned):
        return (
            False,
            None,
            "Address contains non-hexadecimal characters. Only 0-9 and a-f are valid.",
        )

    # Safe checksum normalization
    try:
        checksummed = Web3.to_checksum_address(cleaned)
        return True, checksummed, None
    except Exception as e:
        return False, None, f"Address formatting error: {e}"
