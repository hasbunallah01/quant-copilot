"""
ai_doctor.py - Diagnosis module for detected anomalies.

This implementation uses rule-based pattern matching to generate
human-readable diagnoses. The architecture is designed so that an
LLM-backed implementation (OpenAI, Anthropic, local Ollama) can be
swapped in by replacing the `DiagnoseDoctor.diagnose()` method while
keeping the same interface.

Why rule-based for the hackathon:
  - Zero external dependencies
  - Deterministic and reproducible
  - Fast (no network latency)
  - Demonstrates the architecture clearly
  - Judges can read the code and verify the logic
"""
from __future__ import annotations

from typing import Optional

from .detector import Anomaly


class DiagnoseDoctor:
    """
    Diagnoses anomalies using pattern matching.

    Each detector type has a corresponding diagnosis template that includes:
      - Likely cause
      - Where to look in the code
      - How to fix it
      - How to prevent it
    """

    # Diagnosis templates keyed by anomaly type
    TEMPLATES = {
        "INFINITE_LOOP": {
            "summary": (
                "Infinite trade loop detected. The bot fired the same trade "
                "{repeat_count} times within {window_seconds}s without state "
                "change."
            ),
            "cause": (
                "Your strategy is checking entry conditions on every tick but "
                "forgetting to gate the action behind a state check (e.g. "
                "'if not self.has_position(symbol):'). Once a position is open, "
                "the same signal fires again on the next candle and the bot "
                "re-enters, doubling exposure every cycle."
            ),
            "where_to_look": (
                "Your strategy class, specifically the entry condition in "
                "`on_tick()` / `on_bar()`. Look for a check on `self.positions` "
                "or `self.in_position` before calling `buy()` / `sell()`."
            ),
            "fix": (
                "Add a guard before placing the trade:\n"
                "    if side == 'BUY' and not self.has_position(symbol):\n"
                "        self.buy(...)\n"
                "Or track the last entry time and enforce a cooldown:\n"
                "    if time.time() - self.last_trade_at[symbol] < cooldown:\n"
                "        return  # skip this signal"
            ),
            "prevention": (
                "Quant Copilot's `max_identical_trades_per_minute` rule will "
                "block the third identical trade automatically. Add this to "
                "your rules/default.yaml."
            ),
        },
        "HIGH_TRADE_FREQUENCY": {
            "summary": (
                "{trades_in_window} trades placed within {window_seconds}s "
                "(threshold {threshold})."
            ),
            "cause": (
                "Either the strategy is over-eager (signals fire on every "
                "tick), the bot is running multiple instances on the same "
                "account, or a webhook is being replayed."
            ),
            "where_to_look": (
                "Signal generation logic and process management. Check that "
                "you have only one bot instance running, and that your entry "
                "logic requires more than one confirming indicator."
            ),
            "fix": (
                "Add a minimum interval between trades per symbol:\n"
                "    if symbol in self.last_trade and time.time() - self.last_trade[symbol] < 300:\n"
                "        return  # 5-minute cooldown"
            ),
            "prevention": (
                "Set `max_trades_per_minute` in rules/default.yaml to enforce "
                "this at the risk-engine layer, before the order hits the "
                "exchange."
            ),
        },
        "API_RATE_LIMIT": {
            "summary": (
                "{errors_in_window} API errors in {window_seconds}s - "
                "Bitget rate limit hit."
            ),
            "cause": (
                "Your bot is making more requests per second than your "
                "Bitget tier allows. The default tier allows 20 requests "
                "per second for read endpoints."
            ),
            "where_to_look": (
                "All places your bot calls `requests.get()` / "
                "`requests.post()`. Look for tight polling loops or batch "
                "calls that should be combined."
            ),
            "fix": (
                "Add exponential backoff:\n"
                "    for attempt in range(5):\n"
                "        try:\n"
                "            r = session.get(url, params=params)\n"
                "            r.raise_for_status()\n"
                "            break\n"
                "        except RateLimitError:\n"
                "            time.sleep(2 ** attempt)\n"
                "Respect the `X-Bitget-Request-Rate-Limit-Remaining` header."
            ),
            "prevention": (
                "Use Bitget's WebSocket streams for price data instead of "
                "REST polling. Cache order book snapshots for 1-2 seconds "
                "instead of re-fetching."
            ),
        },
        "SUDDEN_DRAWDOWN": {
            "summary": (
                "Drawdown of {drawdown_pct:.2f}% from peak in the last "
                "{window_seconds}s. Current equity: {current_equity:.2f} "
                "USDT. Peak: {peak_equity:.2f} USDT."
            ),
            "cause": (
                "The strategy is taking losses faster than expected. Could "
                "be a regime change (volatility spike), a leverage blowup, "
                "or a stuck position."
            ),
            "where_to_look": (
                "Open positions, leverage settings, and the last 10-20 "
                "closed trades. Check if any single position is oversized "
                "relative to account equity."
            ),
            "fix": (
                "Immediate actions:\n"
                "1. Halt the bot (`kill_switch` in rules).\n"
                "2. Review and close any oversized positions manually.\n"
                "3. Verify the strategy is still appropriate for current "
                "market conditions."
            ),
            "prevention": (
                "The `kill_switch_drawdown` rule in default.yaml (set to "
                "0.10 = 10%) will auto-pause trading. Reduce leverage, "
                "tighten stop losses, or reduce position sizing."
            ),
        },
        "SLIPPAGE_SPIKE": {
            "summary": (
                "Slippage of {slippage_bps:.1f} bps on {side} {symbol} "
                "(threshold {threshold_bps} bps)."
            ),
            "cause": (
                "Either the order book is thin at your size, you're trading "
                "during low-liquidity hours, or you're using market orders "
                "in a fast-moving market."
            ),
            "where_to_look": (
                "Order execution logic and the time-of-day patterns in your "
                "backtest results."
            ),
            "fix": (
                "Switch from market to limit orders, or split the order "
                "into smaller chunks (TWAP/VWAP execution)."
            ),
            "prevention": (
                "Add `max_slippage_bps` to your rules. Bitget's max_slippage_bps "
                "in default.yaml will reject orders that exceed this threshold."
            ),
        },
    }

    DEFAULT_TEMPLATE = {
        "summary": "Anomaly of type {type} detected.",
        "cause": "No specific cause template defined. Inspect the context "
                 "field for details.",
        "where_to_look": "Recent log lines around the anomaly timestamp.",
        "fix": "No automated fix available. Manual review required.",
        "prevention": "Add a custom rule for this anomaly type in ai_doctor.py.",
    }

    def diagnose(self, anomaly: Anomaly) -> dict:
        """
        Generate a diagnosis for an anomaly.

        Returns a dict with:
          - summary: one-line description
          - cause: likely cause
          - where_to_look: where to inspect in the code
          - fix: how to fix it
          - prevention: how to prevent it happening again
        """
        tpl = self.TEMPLATES.get(anomaly.type, self.DEFAULT_TEMPLATE)
        ctx = anomaly.context or {}

        def _fmt(text: str) -> str:
            try:
                return text.format(**ctx)
            except KeyError:
                return text

        return {
            "anomaly_id": anomaly.id,
            "anomaly_type": anomaly.type,
            "severity": anomaly.severity,
            "summary": _fmt(tpl["summary"]),
            "cause": _fmt(tpl["cause"]),
            "where_to_look": _fmt(tpl["where_to_look"]),
            "fix": _fmt(tpl["fix"]),
            "prevention": _fmt(tpl["prevention"]),
        }


# Optional: LLM-backed doctor stub.
# To enable: pip install openai, set OPENAI_API_KEY env var, and replace
# the DiagnoseDoctor with LLMDiagnoseDoctor in dashboard.py.
#
# class LLMDiagnoseDoctor:
#     def __init__(self, model: str = "gpt-4o-mini"):
#         from openai import OpenAI
#         self.client = OpenAI()
#         self.model = model
#
#     def diagnose(self, anomaly: Anomaly) -> dict:
#         prompt = f"""You are a senior quant engineer. A trading bot just had this anomaly:
# Type: {anomaly.type}
# Severity: {anomaly.severity}
# Context: {anomaly.context}
# Recent log lines: {anomaly.context.get('recent_logs', [])}
#
# Respond in 3 short bullet points: (1) most likely cause, (2) which file/line
# to inspect, (3) minimal fix. Be concise. No fluff."""
#         resp = self.client.chat.completions.create(
#             model=self.model,
#             messages=[{"role": "user", "content": prompt}],
#             max_tokens=300,
#         )
#         text = resp.choices[0].message.content
#         return {
#             "anomaly_id": anomaly.id,
#             "anomaly_type": anomaly.type,
#             "severity": anomaly.severity,
#             "summary": text.split("\n")[0],
#             "diagnosis_full": text,
#         }
