"""
bitget_bot.py - Risk-gated order placement on top of BitgetClient.

`BitgetBotRunner` is the bridge between any user trading strategy and the
Bitget exchange. Every order goes through `RiskEngine.check_trade()` first,
matching the dashboard's `/api/check-trade` semantics, so the same risk
policy applies to live trading and to backtests/demos.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

from ..risk_engine import RiskEngine, TradeRequest, CheckResult
from .bitget_client import BitgetClient, BitgetAPIError, BitgetCredentials


log = logging.getLogger("copilot.exchanges.bot")


@dataclass
class OrderResult:
    """Result of a (risk-checked + possibly submitted) order."""

    submitted: bool
    allowed: bool
    reason: str = ""
    rule_violated: Optional[str] = None
    order_response: Optional[dict] = None
    error: Optional[str] = None


class BitgetBotRunner:
    """Wraps a `BitgetClient` with the Quant Copilot `RiskEngine`.

    Use this in your strategy loop:

        client  = BitgetClient(creds=BitgetCredentials.from_env())
        rules   = RiskEngine("rules/default.yaml")
        bot     = BitgetBotRunner(client, rules)

        decision = bot.evaluate(symbol="BTCUSDT", side="BUY", qty=0.001, price=65000)
        if decision.allowed:
            order = bot.place(symbol="BTCUSDT", side="BUY", qty=0.001, price=65000,
                              order_type="LIMIT")
            if order.submitted:
                log.info("Order placed: %s", order.order_response)
    """

    def __init__(
        self,
        client: BitgetClient,
        risk_engine: RiskEngine,
        *,
        account_equity_getter: Optional[Callable[[], float]] = None,
        simulated: bool = True,
    ):
        self.client = client
        self.risk = risk_engine
        self.account_equity_getter = account_equity_getter or (lambda: 10000.0)
        self.simulated = simulated
        # Whether the runner will actually call `place_order` on the exchange.
        # When `simulated=True` we record to the risk engine but skip the
        # network call (useful for paper trading / CI).
        self.last_evaluated: Optional[CheckResult] = None
        self.last_submitted: Optional[OrderResult] = None

    # ---- public API ------------------------------------------------------
    def evaluate(
        self,
        *,
        symbol: str,
        side: str,
        qty: float,
        price: float,
    ) -> CheckResult:
        """Run the risk engine on a proposed trade. No I/O."""
        # Bitget uses "BTCUSDT"; the risk engine expects "BTC/USDT" - normalize.
        norm_symbol = self._normalize_symbol(symbol)
        req = TradeRequest(
            symbol=norm_symbol,
            side=side.upper(),
            quantity=qty,
            price=price,
            account_equity=self.account_equity_getter(),
        )
        result = self.risk.check_trade(req)
        self.last_evaluated = result
        return result

    def place(
        self,
        *,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        order_type: str = "MARKET",
        client_oid: Optional[str] = None,
    ) -> OrderResult:
        """Risk-check then (optionally) submit to Bitget.

        If `simulated=True`, no network call is made; the risk engine still
        records the trade so rate-limit / drawdown logic behaves identically.
        """
        eval_result = self.evaluate(symbol=symbol, side=side, qty=qty, price=price)

        if not eval_result.allowed:
            log.warning(
                "BLOCKED %s %s qty=%s price=%s: %s (rule=%s)",
                side, symbol, qty, price, eval_result.reason, eval_result.rule_violated,
            )
            res = OrderResult(
                submitted=False,
                allowed=False,
                reason=eval_result.reason,
                rule_violated=eval_result.rule_violated,
            )
            self.last_submitted = res
            return res

        # record in risk engine regardless of network outcome
        req = TradeRequest(
            symbol=self._normalize_symbol(symbol),
            side=side.upper(),
            quantity=qty,
            price=price,
            account_equity=self.account_equity_getter(),
        )
        self.risk.record_trade(req)

        if self.simulated:
            log.info(
                "SIMULATED %s %s qty=%s price=%s (risk passed, no network)",
                side, symbol, qty, price,
            )
            res = OrderResult(
                submitted=True,
                allowed=True,
                order_response={"simulated": True, "symbol": symbol, "side": side,
                                "size": str(qty), "price": str(price)},
            )
            self.last_submitted = res
            return res

        try:
            response = self.client.place_order(
                symbol=symbol,
                side=side,
                order_type=order_type,
                size=str(qty),
                price=None if order_type.upper() == "MARKET" else str(price),
                client_oid=client_oid,
            )
            res = OrderResult(
                submitted=True,
                allowed=True,
                order_response=response,
            )
        except BitgetAPIError as e:
            res = OrderResult(
                submitted=False,
                allowed=True,
                error=str(e),
            )
        self.last_submitted = res
        return res

    # ---- helpers ---------------------------------------------------------
    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """Accept either `BTCUSDT` or `BTC/USDT`, return `BTC/USDT`."""
        s = symbol.upper().strip()
        if "/" not in s and s.endswith("USDT"):
            s = s[:-4] + "/USDT"
        return s