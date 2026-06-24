# 📊 Quant Copilot

> **AI debugging copilot for crypto trading bots.**
> Built for the **Bitget AI Hackathon Genesis S1** · Theme: *KI × Krypto*

---

## 🎯 The Problem

Every quant developer has lived through this:

- Bot trades overnight, loses money, and you don't know why
- A bug fires the same trade 200 times in a row
- API rate limits silently throttle your strategy
- Drawdowns hit double digits before you notice

Existing tools give you **monitoring** ("your bot is down"). They don't tell you **why** or **how to fix it**.

## 💡 The Solution

**Quant Copilot** is the dev tool that finally lets you sleep:

1. 👀 **Watches** your bot's log file in real time
2. 🚨 **Detects** anomalies: infinite loops, drawdowns, rate limits, slippage spikes
3. 🧠 **Diagnoses** them with a senior-dev-level explanation (cause + fix + prevention)
4. 🛡️ **Blocks** bad trades *before* they reach the exchange via a YAML risk policy

The "AI × Crypto" theme: an AI that watches your AI.

---

## ✨ Features

| Component | What it does |
|---|---|
| **Log Watcher** | Tails bot logs in real time. Handles file rotation, truncation, and slow producers. |
| **Anomaly Detector** | Sliding-window detection for infinite loops, sudden drawdowns, API rate limits, slippage spikes, and high trade frequency. |
| **AI Doctor** | Rule-based diagnosis engine that generates *cause + where to look + how to fix* for every anomaly. (LLM-backed stub included — swap in OpenAI/Anthropic in 5 lines.) |
| **Risk Engine** | YAML-defined risk policies. Pre-trade gatekeeper that blocks orders exceeding position size, daily loss, rate limits, drawdown, etc. |
| **Live Dashboard** | FastAPI + WebSocket UI showing live logs, anomalies with AI diagnoses, and risk engine state. |

---

## 🖼️ Screenshots

The dashboard running during the demo, capturing the bot's infinite loop in real time:

![Quant Copilot Dashboard](assets/screenshot.png)

> *The dashboard auto-scrolls, color-codes severity, and shows the AI diagnosis inline with each anomaly. The risk panel tracks equity, drawdown, and open positions live.*

---

## 🏗️ Architecture

```
┌────────────────────┐
│  Your Trading Bot  │
│  (any framework)   │
└────────┬───────────┘
         │ writes log lines
         ▼
┌────────────────────┐         ┌──────────────────┐
│   Log Watcher      │────────▶│  Anomaly         │
│   (watcher.py)     │  events │  Detector        │
└────────────────────┘         │  (detector.py)   │
                               └─────────┬────────┘
                                         │ anomalies
                                         ▼
                               ┌──────────────────┐
                               │  AI Doctor       │
                               │  (ai_doctor.py)  │
                               │  ─ diagnoses     │
                               └─────────┬────────┘
                                         │ diagnoses
                                         ▼
   ┌─────────────────────────────────────────────────────┐
   │   FastAPI Dashboard (dashboard.py)                   │
   │   • Live log feed          • Risk engine status      │
   │   • Anomaly alerts         • WebSocket push          │
   └────────────────────┬─────────────────────────────────┘
                        │ /api/check-trade
                        ▼
               ┌──────────────────┐
               │  Risk Engine     │  YAML rules
               │  (risk_engine.py)│ ◀──── rules/default.yaml
               │  ─ blocks trades │
               └──────────────────┘
```

---

## 🚀 Quickstart

### 1. Install

```bash
git clone https://github.com/hasbunallah01/quant-copilot.git
cd quant-copilot
pip install -r requirements.txt
```

### 2. Run the dashboard

```bash
python -m copilot.dashboard
```

Open **http://localhost:8000** in your browser.

### 3. Run the demo bot (separate terminal)

```bash
python demo_bot/bot.py
```

Watch the dashboard detect the bot's intentional infinite-loop bug within seconds, generate an AI diagnosis, and block subsequent trades via the risk engine.

### 4. Wire it into your own bot

Add this single line before placing any order:

```python
import requests
result = requests.post("http://localhost:8000/api/check-trade", json={
    "symbol": "BTC/USDT",
    "side": "BUY",
    "quantity": 0.5,
    "price": 65000,
    "account_equity": 10000,
}).json()
if not result["allowed"]:
    print("BLOCKED:", result["reason"])
    return
# else: place the order
```

That's it. The risk engine is now your pre-trade gatekeeper.

---

## 📁 Project Structure

```
quant-copilot/
├── copilot/
│   ├── __init__.py
│   ├── watcher.py        # log file tailing + parsing
│   ├── detector.py       # sliding-window anomaly detection
│   ├── ai_doctor.py      # rule-based diagnosis (+ LLM stub)
│   ├── risk_engine.py    # YAML-based risk policy
│   └── dashboard.py      # FastAPI + WebSocket UI
├── demo_bot/
│   └── bot.py            # intentionally buggy bot for the demo
├── rules/
│   └── default.yaml      # risk policy
├── logs/
│   └── demo.log          # runtime logs (gitignored)
├── assets/
│   └── screenshot.png    # dashboard screenshot
├── tests/
│   └── test_basic.py     # smoke tests
├── requirements.txt
├── .gitignore
└── README.md
```

---

## 🛡️ Risk Rules (`rules/default.yaml`)

| Rule | Default | What it does |
|---|---|---|
| `max_position_size` | 1000 USDT | Blocks orders larger than this |
| `max_total_exposure` | 5000 USDT | Total exposure across all positions |
| `max_daily_loss` | 200 USDT | Pauses trading if daily loss exceeds this |
| `max_trades_per_minute` | 3 | Prevents runaway trade loops |
| `max_identical_trades_per_minute` | 2 | Catches infinite-loop bugs |
| `kill_switch_drawdown` | 0.10 (10%) | Halts everything on big drawdown |
| `max_order_pct_of_equity` | 0.20 (20%) | Position-sizing cap relative to account |
| `max_slippage_bps` | 50 bps | Rejects orders with high expected slippage |
| `blocked_symbols` | `[SCAM/USDT]` | Blacklist (override wins over whitelist) |
| `allowed_symbols` | `[]` (all) | Optional whitelist |

Edit `rules/default.yaml` to match your strategy's risk profile. Reload by restarting the dashboard.

---

## 🧠 Anomaly Types Detected

| Type | Severity | What it catches |
|---|---|---|
| `INFINITE_LOOP` | CRITICAL | Same trade (symbol/side/qty/price) repeated 3+ times in 60s |
| `HIGH_TRADE_FREQUENCY` | HIGH | More than 3 trades in 60s (general rate check) |
| `SUDDEN_DRAWDOWN` | HIGH / CRITICAL | Equity falls >5% from peak in a 5-min window |
| `API_RATE_LIMIT` | HIGH | 10+ API errors in 60s |
| `SLIPPAGE_SPIKE` | MEDIUM | Single trade slippage >100 bps |

For each anomaly, the AI doctor produces:
- **Summary** — what happened
- **Cause** — why it likely happened
- **Where to look** — which file/line in your code
- **Fix** — concrete code patch
- **Prevention** — how to stop it happening again

---

## 🤖 The Demo Bot (intentional bug)

The included `demo_bot/bot.py` has **one missing line of code** on purpose: a check for `self.has_position` before calling `self.buy()`. This is one of the most common bugs in real trading bots.

What you'll see in the dashboard:
1. Bot starts → first BUY fires
2. Second tick → signal still true → bot buys **again** (this is the bug)
3. Third tick → bot buys **again**
4. Quant Copilot detects: 🚨 `INFINITE_LOOP` · CRITICAL
5. AI doctor says: "missing position-state check before buy()"
6. Risk engine blocks the 4th identical trade
7. Drawdown accumulates → kill switch triggers at 10%

The 90-second story for the hackathon video.

---

## 🔌 Swap in a Real LLM (Optional)

The AI doctor ships with a rule-based engine so it works offline. To upgrade to GPT-4 / Claude / Llama, see the stub at the bottom of `copilot/ai_doctor.py`:

```python
from openai import OpenAI
client = OpenAI()
# ... use the LLMDiagnoseDoctor class with your own prompt
```

The interface (`diagnose(anomaly) -> dict`) stays the same.

---

## 🧪 Tests

```bash
python -m pytest tests/
```

Smoke tests cover:
- Log parsing
- Anomaly detection (infinite loop, drawdown, rate limit)
- Risk engine (each rule individually)
- Integration: end-to-end with the demo bot

---

## 📦 Dependencies

- `fastapi` + `uvicorn` — web framework
- `pyyaml` — risk policy parsing
- `requests` — CoinGecko price feed
- `pydantic` — request/response validation
- `watchdog` — (optional) faster file events

No LLM API key required for the base system. No exchange API key required for the demo.

---

## 🎬 Hackathon Submission Notes

- **Track:** Trading Infrastructure (Developer Category)
- **Theme fit:** "KI × Krypto" — the AI watches your crypto trading bot
- **Demo length:** 90 seconds
- **Demo flow:** open dashboard → start demo bot → watch copilot catch the bug → read AI diagnosis → see risk block fire
- **Why we win:** every quant dev has lost sleep to a mystery bug. Quant Copilot is the dev tool that fixes that. Solves a real, daily problem, not a hypothetical one.

---

## 📜 License

MIT — see LICENSE file.

---

## 👤 Author

Built by [hasbunallah01](https://github.com/hasbunallah01) for the Bitget AI Hackathon Genesis S1.
