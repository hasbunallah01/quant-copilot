# Quant Copilot — Architecture

This document is the canonical architecture reference for the
Bitget AI Hackathon Genesis S1 submission. It explains the data flow,
module boundaries, and the contract each layer exposes.

## High-level

```
                ┌──────────────────────────────────────────────────┐
                │                User / Strategy Code             │
                └───────────────┬──────────────────────────────────┘
                                │
                  BitgetBotRunner.evaluate() / place()
                                │
                                ▼
                ┌──────────────────────────────────────────────────┐
                │                RiskEngine  (risk_engine.py)     │
                │   YAML policy -> verdict {allowed, rule, reason}│
                └─────┬───────────────────────────────────┬───────┘
                      │ allowed                          │ blocked
                      ▼                                  ▼
            ┌──────────────────────┐         ┌──────────────────────┐
            │ BitgetClient         │         │ OrderResult          │
            │ (exchanges/bitget_)  │         │ {submitted:false}    │
            │ POST /place-order    │         │ log + return         │
            └─────────┬────────────┘         └──────────────────────┘
                      │
                      ▼
                Bitget exchange (REST v2)

  In parallel — observable plane:
  ─────────────────────────────────
   Trading bot log file
        │
        ▼
   LogWatcher (watcher.py)  ──rotates──▶  AnomalyDetector (detector.py)
        │                                         │
        │ parsed events                            │ anomalies
        ▼                                         ▼
   FastAPI dashboard  ◀──── WebSocket /api/ws ─── AI Doctor (ai_doctor.py)
   (dashboard.py)
        ▲
        │  browser (HTML/JS, no framework)
```

## Module boundaries

| Module                        | Responsibility                                                       | Public API                                                              |
|-------------------------------|----------------------------------------------------------------------|-------------------------------------------------------------------------|
| `copilot.watcher`             | Tail log files rotation-safely; parse log lines into events          | `LogWatcher`, `parse_log_line`                                          |
| `copilot.detector`            | Sliding-window detection of bot anomalies                            | `AnomalyDetector`, `Anomaly`                                            |
| `copilot.ai_doctor`           | Turn anomalies into {cause, where, fix, prevention}                  | `DiagnoseDoctor` (rule-based); `LLMDiagnoseDoctor` stub                |
| `copilot.risk_engine`         | Pre-trade YAML risk gate                                             | `RiskEngine`, `TradeRequest`, `CheckResult`                             |
| `copilot.exchanges.bitget_*`  | Bitget v2 REST + signing + public WS                                 | `BitgetClient`, `BitgetCredentials`, `BitgetBotRunner`, `BitgetAPIError`|
| `copilot.dashboard`           | FastAPI + WebSocket dashboard                                        | `app` (FastAPI), CLI: `python -m copilot.dashboard`                    |
| `demo_bot.bot`                | A deliberately-buggy bot for the demo                                | CLI: `python demo_bot/bot.py`                                           |

Every module is independently importable; no module reaches into the
private state of another. The FastAPI dashboard keeps shared state in
a single `STATE` object and the BitgetBotRunner owns its own risk engine.

## Data shapes

### Anomaly (detector output)

```json
{
  "id": "3f2c1d4e",
  "type": "INFINITE_LOOP",
  "severity": "CRITICAL",
  "message": "Identical trade repeated 4 times in <60s: BUY 0.5 BTC/USDT",
  "context": {
    "side": "BUY", "symbol": "BTC/USDT", "quantity": 0.5, "price": 65000.0,
    "repeat_count": 4, "window_seconds": 60.0
  },
  "suggested_action": "Likely infinite loop bug — the bot is firing the same trade ...",
  "detected_at": "2026-06-24T18:00:00.123456+00:00"
}
```

### Diagnosis (doctor output)

```json
{
  "anomaly_id": "3f2c1d4e",
  "anomaly_type": "INFINITE_LOOP",
  "severity": "CRITICAL",
  "summary": "Infinite trade loop detected. The bot fired the same trade 4 times within 60s ...",
  "cause": "Your strategy is checking entry conditions on every tick but forgetting ...",
  "where_to_look": "Your strategy class, specifically the entry condition in `on_tick()` ...",
  "fix": "Add a guard before placing the trade: ...",
  "prevention": "Quant Copilot's `max_identical_trades_per_minute` rule ..."
}
```

### Risk verdict (pre-trade)

```json
{
  "allowed": false,
  "reason": "Order notional 50000.00 USDT exceeds max position size of 1000.00 USDT",
  "rule_violated": "max_position_size",
  "context": {"notional": 50000.0, "max_position_size": 1000.0}
}
```

### Bitget v2 signing (v2)

```
prehash   = ACCESS-TIMESTAMP + METHOD + requestPath + (?queryString) + body
signature = base64(HMAC_SHA256(api_secret, prehash))
headers   = {
  ACCESS-KEY, ACCESS-SIGN, ACCESS-TIMESTAMP, ACCESS-PASSPHRASE,
  x-simulated-trading: 1   # when credentials.demo is True
}
```

Reproduced and verified in `tests/test_bitget_client.py::test_signed_endpoint_produces_correct_signature`.

## Failure modes & how the system contains them

| Failure                                       | Detected by                          | Contained by                                       |
|-----------------------------------------------|--------------------------------------|----------------------------------------------------|
| Infinite trade loop                           | `AnomalyDetector` (INFINITE_LOOP)    | `RiskEngine.max_identical_trades_per_minute`       |
| Runaway strategy                              | `AnomalyDetector` (HIGH_TRADE_FREQ)  | `RiskEngine.max_trades_per_minute`                 |
| Oversized single order                        | n/a                                  | `RiskEngine.max_position_size`, `max_order_pct_of_equity` |
| Drawdown / regime change                      | `AnomalyDetector` (SUDDEN_DRAWDOWN)  | `RiskEngine.kill_switch_drawdown`                  |
| Bitget rate limit / transient 5xx             | n/a                                  | `BitgetClient` retry/backoff (0.5s, 1s, 2s, 4s)    |
| Slippage spike                                | `AnomalyDetector` (SLIPPAGE_SPIKE)   | `RiskEngine.max_slippage_bps` (planned: enforce in runner) |
| Trading on a blacklisted symbol               | n/a                                  | `RiskEngine.blocked_symbols`                       |

The same risk policy applies in three places: the FastAPI dashboard
(`/api/check-trade`), the `BitgetBotRunner` (live order placement), and
any user code that wires its strategy to `RiskEngine.check_trade` directly.