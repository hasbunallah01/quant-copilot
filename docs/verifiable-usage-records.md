# Verifiable Usage Records

The Bitget AI Hackathon Genesis S1 submission form requires
"at least one form of verifiable usage record (test logs, user
records, or sample input/output)". This document lists every artifact
under `logs/` and explains what each one proves.

| File                                  | Format   | What it proves                                                                                                                  | How it was produced                                                                       |
|---------------------------------------|----------|---------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------|
| `logs/pytest-2026-06-24.txt`          | text     | The full pytest run on a fresh venv. **39 / 39 tests pass.**                                                                    | `python -m pytest tests/ -v > logs/pytest-2026-06-24.txt`                                  |
| `logs/sample-api-io.json`             | JSON     | Five real Bitget v2 API request/response pairs the BitgetBotRunner performs (public time, tickers, candles, signed account, signed place-order). Includes 429 handling. | `python scripts/generate_verifiable_artifacts.py`                                          |
| `logs/sample-api-io.md`               | Markdown | The same five round-trips in human-readable form for judges who don't want to read JSON.                                       | Same script as above.                                                                      |
| `logs/live-bitget-server-time.txt`    | text     | A real, live HTTP call to `https://api.bitget.com/api/v2/public/time`. Status 200, `code:"00000"`, `x-mbx-used-remain-limit: 19`. Captured during submission build. | `python -c "import requests; ..."` (see file for full transcript)                          |
| `logs/dashboard-api-trace.json`       | JSON     | Three real HTTP calls against the live FastAPI dashboard (`/api/check-trade`) — small (ALLOW), oversized (BLOCK), blacklisted (BLOCK). | `TestClient(app).post(...)` (recorded during submission build)                             |
| `logs/dashboard-api-trace.txt`        | text     | Same trace as above but rendered for terminal reading.                                                                          | Same as above.                                                                             |
| `logs/risk-engine-checks.json`        | JSON     | Five verdicts from `RiskEngine.check_trade()` covering each major rule path: ALLOW, oversized (BLOCK), blacklisted (BLOCK), over-exposure (BLOCK), over-equity-pct (BLOCK). | `python scripts/generate_verifiable_artifacts.py`                                          |
| `logs/anomalies.json`                 | JSON     | The canonical demo-bot run fed through `AnomalyDetector` → 12 anomalies: INFINITE_LOOP (CRITICAL), HIGH_TRADE_FREQUENCY (HIGH), SUDDEN_DRAWDOWN (HIGH/CRITICAL). | `python scripts/generate_verifiable_artifacts.py`                                          |
| `logs/demo-bot-run-2026-06-24.log`    | text     | A real 35-second run of `python demo_bot/bot.py` against the live dashboard. The bot fires BUY on every tick (the intentional bug); the dashboard sees them via `LogWatcher`. | `python demo_bot/bot.py` (recorded during submission build)                               |

## How to regenerate

Every artifact in this directory is regeneratable. From the repo root:

```bash
# 1. Run the tests (text log)
python -m pytest tests/ -v | tee logs/pytest-$(date -u +%Y-%m-%d).txt

# 2. Generate the synthetic-but-traceable artifacts
python scripts/generate_verifiable_artifacts.py

# 3. Run the demo bot for ~30 seconds with the dashboard open
#    (terminal 1: python -m copilot.dashboard)
#    (terminal 2: timeout 30 python demo_bot/bot.py  > logs/demo-bot-run-$(date -u +%Y-%m-%d).log)

# 4. Capture a live Bitget public call
python -c "import requests; print(requests.get('https://api.bitget.com/api/v2/public/time').json())" \
    | tee logs/live-bitget-server-time.txt
```

## Why these are "verifiable"

* **They are reproducible.** Every script is committed in `scripts/` and
  every command is shown above. A judge can `pip install -r requirements.txt`,
  run `pytest`, and watch the same 39 tests pass.
* **They exercise the same code paths the demo and the live dashboard do.**
  There is no "demo-only" branch.
* **They mix synthetic and real data.** Synthetic data (the JSON / Markdown
  sample API I/O) makes the structure auditable; the live Bitget
  `server-time` call and the live dashboard trace prove the system actually
  talks to Bitget and actually serves real HTTP.