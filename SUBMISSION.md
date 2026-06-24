# Submission Description for Bitget AI Hackathon Genesis S1

Copy-paste this into the Bitget submission form (Title + Description fields).

---

## Title

**Quant Copilot — AI debugging copilot for crypto trading bots**

## One-line tagline

> The AI that watches your trading bot while you sleep.

## Category

**Trading Infrastructure** (Developer Category)

## Description (≈ 250 words)

Every quant developer has lived through this: a bot trades overnight, bleeds money, and by morning you have no idea why. Existing monitoring tools tell you *that* something broke — they never tell you *why* or *how to fix it*.

**Quant Copilot** is an AI debugging copilot for crypto trading bots. It sits between your bot and the exchange, watching the log in real time, detecting anomalies, explaining them in plain English, and **blocking bad trades before they reach the order book**.

**What it does:**
- 👀 **Watches** your bot's log file with a rotation-safe tail engine
- 🚨 **Detects** 5 anomaly types: infinite trade loops, sudden drawdowns, API rate limits, slippage spikes, and runaway trade frequency
- 🧠 **Diagnoses** each anomaly with a senior-dev-style breakdown — likely cause, where to look in your code, how to fix it, and how to prevent it happening again
- 🛡️ **Blocks** risky orders via a YAML-based pre-trade risk policy (position size, drawdown kill-switch, rate limits, blacklist, etc.)
- 📊 **Live dashboard** (FastAPI + WebSocket) shows everything in real time

**The 90-second demo:** start the dashboard, launch the included `demo_bot/bot.py` (it has a real-world bug — a missing position-state check), and watch the copilot catch the infinite loop in real time, generate an AI diagnosis, and block the third identical trade automatically.

Built for the **KI × Krypto** theme: an AI that watches your AI.

**Repo:** https://github.com/hasbunallah01/quant-copilot

## Tech stack

- Python 3.11
- FastAPI + WebSocket (dashboard)
- PyYAML (risk policy)
- Pydantic (request validation)
- Watchdog (file events)
- 10 passing smoke tests
- Zero paid dependencies (rule-based AI doctor included; LLM swap-in documented)

## Why this wins

- **Solves a real, daily problem** for every quant developer, not a hypothetical one
- **Production-quality code** — modular, tested, documented, type-hinted
- **Works out of the box** — `pip install -r requirements.txt && python -m copilot.dashboard`
- **Theme-perfect fit** — "KI × Krypto": AI debugging AI crypto trading
- **Demo tells a story** — 90 seconds from "bot is fine" to "AI caught the bug, here's why, here's the fix"
- **Extensible** — swap the rule-based doctor for an LLM in 5 lines
