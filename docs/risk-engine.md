# Risk Engine Specification

The RiskEngine is the **single source of truth** for "is this trade safe to send
to the exchange?" Every layer of Quant Copilot (dashboard, BitgetBotRunner,
external user code) consults the same engine with the same YAML policy.

## File location

`rules/default.yaml`. Edit and reload by restarting the dashboard, or
call `RiskEngine.reload()` from code.

## Rule order

The engine evaluates rules in a fixed order and **returns the first failure**.
The order is intentional: cheap, structural checks first (kill switch,
blacklist), then sizing (max position size, % of equity), then aggregate
exposure, then rate limits, then daily loss.

| #  | Rule key                          | What it checks                                                | Default         |
|----|-----------------------------------|---------------------------------------------------------------|-----------------|
| 0  | `kill_switch_drawdown`            | Drawdown from peak equity (engine-internal state)             | `0.10` (10%)    |
| 1  | `blocked_symbols`                 | Symbol is on the blacklist (overrides whitelist)              | `[SCAM/USDT]`   |
| 2  | `allowed_symbols`                 | Symbol is in the whitelist (empty = all)                      | `[]`            |
| 3  | `max_position_size`               | Single-order notional cap (USDT)                              | `1000.0`        |
| 4  | `max_order_pct_of_equity`         | Single-order notional / equity                                | `0.20` (20%)    |
| 5  | `max_total_exposure`              | Sum of open positions + this order (USDT)                     | `5000.0`        |
| 6  | `max_trades_per_minute`           | Total trades in a rolling 60s window                          | `3`             |
| 7  | `max_identical_trades_per_minute` | Identical (symbol+side) trades in 60s                         | `2`             |
| 8  | `max_daily_loss`                  | Cumulative PnL ≤ -X USDT pauses trading                       | `200.0`         |
| (planned) | `max_slippage_bps`        | Reject orders with expected slippage above threshold          | `50`            |

## Verdict shape

```python
CheckResult(
    allowed=True|False,
    reason="human-readable explanation",
    rule_violated="rule_key" | None,
    context={...rule-specific...},
)
```

Every verdict goes back to the caller and is also broadcast on the
WebSocket so the dashboard renders it.

## State tracked by the engine

* `_trade_history: deque[(ts, TradeRequest)]` — capped at 1000 entries.
* `_daily_pnl: deque[(ts, pnl)]` — capped at 10000 entries.
* `_open_positions: dict[symbol, notional]` — running notional exposure.
* `_peak_equity`, `_current_equity`, `_kill_switch_active`.

The engine is **not thread-safe** by design — it is intended to run inside
one event loop (the dashboard) or one trading strategy thread. If you need
to call it from multiple threads, wrap it with a `threading.Lock`.

## Reset semantics

`RiskEngine.reset_kill_switch()` resets both `_kill_switch_active` and the
peak equity to the current equity, so the engine resumes trading at the
post-drawdown baseline. The dashboard exposes this as `POST /api/reset-kill-switch`.

## Why YAML and not Python?

* Ops can change limits without a redeploy.
* The same file is the single source of truth across dashboard, runner,
  and unit tests.
* It's the most common config format quant ops already have in CI/CD.