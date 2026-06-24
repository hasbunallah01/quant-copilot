"""Bitget exchange integration for Quant Copilot.

Thin adapter over Bitget v2 API:
  - REST client (signed requests, time sync, retry/backoff)
  - Public WebSocket helper (tickers, candles)
  - `BitgetBotRunner` that wraps a strategy callback with the existing
    `RiskEngine` as a pre-trade gate, so every order goes through
    the same risk checks the dashboard exposes.

Why an adapter, not a full SDK:
  - The Bitget official SDK is heavy and changes often.
  - For a hackathon infra submission we want the integration layer to be
    auditable in < 200 LOC.
  - Users can swap in the official SDK later by implementing the same
    `BotRunner.place_order` interface.

References:
  - Bitget API v2 docs: https://www.bitget.com/api-doc/common/intro
  - Bitget Agent Hub:    https://github.com/Bitget-AI/agent_hub
"""
from .bitget_client import BitgetClient, BitgetCredentials, BitgetAPIError
from .bitget_bot import BitgetBotRunner

__all__ = [
    "BitgetClient",
    "BitgetCredentials",
    "BitgetAPIError",
    "BitgetBotRunner",
]