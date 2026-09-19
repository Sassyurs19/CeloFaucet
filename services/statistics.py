"""
Statistics service for calculating platform metrics and admin analytics.
"""

from __future__ import annotations

import math
from typing import Any, Dict

from config import config
from database import db
from wallet import wallet_manager
from celo import celo_client


class StatisticsService:
    """Provides high-level analytical summaries."""

    async def get_dashboard_stats(self) -> Dict[str, Any]:
        """Aggregate database stats along with real-time wallet estimates."""
        stats = await db.get_statistics()

        # Faucet wallet balance
        balance = 0.0
        if not config.dry_run and wallet_manager.is_configured:
            balance = await celo_client.get_balance(wallet_manager.address)

        # Estimated payouts: floor(balance / claim_amount)
        claim_amt = config.claim_amount if config.claim_amount > 0 else 0.1
        estimated_payouts = math.floor(balance / claim_amt) if balance > 0 else 0

        stats["faucet_balance"] = round(balance, 4)
        stats["estimated_payouts"] = estimated_payouts
        stats["claim_amount"] = config.claim_amount
        stats["dry_run"] = config.dry_run

        return stats


# Global statistics service
statistics_service = StatisticsService()
