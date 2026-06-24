# Submission Description for Bitget AI Hackathon Genesis S1

Copy-paste this into the Bitget submission form (Title + Description fields).

---

## Title

**Quant Copilot — AI debugging copilot & risk-gated Bitget adapter for crypto trading bots**

## One-line tagline

> The AI that watches your trading bot — and the risk gate that stands between it and Bitget.

## Category

**Trading Infrastructure** (Developer Category)

## Description (≈ 300 words)

Every quant developer has lived through this: a bot trades overnight, bleeds money, and by morning you have no idea why. Existing monitoring tools tell you *that* something broke — they never tell you *why* or *how to fix it*, and they don't actually *block* the bad orders from reaching the exchange.

**Quant Copilot** is the AI debugging copilot + risk-gated execution layer for crypto trading bots. It ships as an open-source Trading Infrastructure package built on Bitget v2.

**What it does:**
- 👀 **Watches** your bot's log file with a rotation-safe tail engine
- 🚨 **Detects** 5 anomaly types: infinite trade loops, sudden drawdowns, API rate limits, slippage spikes, runaway trade frequency
- 🧠 **Diagnoses** each anomaly with a senior-dev-style breakdown — likely cause, where to look, how to fix, and how to prevent it
- 🛡️ **Blocks** risky orders via a YAML pre-trade risk policy (position size, drawdown kill-switch, rate limits, blacklist) — used by both the dashboard and the live Bitget adapter
- 🔌 **Plugs into Bitget** with a minimal HMAC-signed v2 REST client + a `BitgetBotRunner` that runs every order through the same `RiskEngine`. Retry on 429/5xx with exponential backoff.
- 📊 **Live dashboard** (FastAPI + WebSocket) shows logs, anomalies, AI diagnoses, and risk verdicts in real time

**The 90-second demo:** start the dashboard, launch `demo_bot/bot.py` (intentional bug: missing position-state check), and watch Quant Copilot catch the infinite loop, generate an AI diagnosis, and block the third identical trade via the risk engine.

**Verifiable usage records** (9 artifacts under `logs/`): a real pytest run (39/39 passing), 5 Bitget v2 round-trips with full request/response, a live HTTP call to `api.bitget.com`, 3 live dashboard API calls, 5 pre-trade verdicts, anomaly replay output, and a real demo-bot run. See `docs/verifiable-usage-records.md`.

**Repo:** https://github.com/hasbunallah01/quant-copilot

## Tech stack

- Python 3.11
- FastAPI + WebSocket (dashboard)
- PyYAML (risk policy)
- Pydantic (request validation)
- Requests (Bitget v2 REST client + CoinGecko price feed)
- 39 passing pytest tests (14 core + 15 Bitget adapter + 9 dashboard API + 1 e2e)
- Zero paid dependencies (rule-based AI doctor included; LLM swap-in documented)

## Why this wins

- **Solves a real, daily problem** for every quant developer, not a hypothetical one.
- **Production-quality code** — modular, tested (39/39), documented, type-hinted, MIT licensed.
- **Works out of the box** — `pip install -r requirements.txt && pytest && python -m copilot.dashboard`.
- **Bitget-native** — v2 HMAC signing, demo-trading header, rate-limit handling. Not a name-drop.
- **Single source of truth for risk** — the same `RiskEngine` is consulted by the dashboard, the demo bot, and the live BitgetBotRunner.
- **Verifiable usage records** — 9 artifacts under `logs/`, all regeneratable.
- **Theme-perfect fit** — "KI × Krypto": AI debugging AI crypto trading.

## Links

- Repo: https://github.com/hasbunallah01/quant-copilot
- Verifiable artifacts: `docs/verifiable-usage-records.md`
- Architecture: `docs/architecture.md`
- Risk engine spec: `docs/risk-engine.md`
- Bitget integration: `docs/bitget-integration.md`