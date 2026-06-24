# Changelog

All notable changes to Quant Copilot are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project adheres to [Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-06-24 — Bitget Hackathon Genesis S1 submission

### Added
- **Bitget v2 exchange adapter** (`copilot/exchanges/`):
  - `BitgetClient` — REST client with HMAC v2 signing, automatic 429/5xx
    retry with exponential backoff, injectable `requests.Session`.
  - `BitgetCredentials` + `from_env()` helper.
  - `BitgetBotRunner` — risk-gated order placement that runs the same
    `RiskEngine` policy used by the dashboard.
  - `public_ws_subscribe_instructions()` helper for the Bitget public WS.
- **Test suite** expanded from 10 to 39 tests, now runnable as `pytest -v`.
  - `tests/test_basic.py` — log parser, anomaly detector, AI doctor, risk engine.
  - `tests/test_bitget_client.py` — BitgetClient signing, retries, runner.
  - `tests/test_dashboard_api.py` — FastAPI HTTP layer via TestClient.
  - `tests/test_e2e_bot.py` — replay a real demo-bot log through the detector.
- **Verifiable usage records** under `logs/`:
  - `pytest-2026-06-24.txt`
  - `sample-api-io.json` / `.md`
  - `live-bitget-server-time.txt`
  - `dashboard-api-trace.json` / `.txt`
  - `risk-engine-checks.json`
  - `anomalies.json`
  - `demo-bot-run-2026-06-24.log`
- **Docs**: `docs/architecture.md`, `docs/risk-engine.md`,
  `docs/bitget-integration.md`, `docs/verifiable-usage-records.md`.
- **Community files**: `CONTRIBUTING.md`, `SECURITY.md`,
  `CHANGELOG.md`, `.env.example`,
  `.github/ISSUE_TEMPLATE/{bug_report,feature_request}.md`,
  `.github/PULL_REQUEST_TEMPLATE.md`,
  `.github/workflows/ci.yml`.
- `pytest.ini`, `examples/bitget_runner_example.py`,
  `scripts/generate_verifiable_artifacts.py`.

### Changed
- `tests/test_basic.py` now collects its tests in a list so it remains
  runnable as a script (`python tests/test_basic.py`) **and** as
  `pytest tests/test_basic.py`.

### Notes for judges
- 39 / 39 tests pass on Python 3.11 in a clean venv.
- The Bitget adapter is fully offline-runnable (mocked sessions in tests).
- The dashboard, demo bot, and BitgetBotRunner all share the **same**
  `RiskEngine` instance per process — there is no shadow policy anywhere.

## [0.1.0] - 2024 — Initial release

- Log watcher with rotation/truncation handling.
- Anomaly detector (5 types: INFINITE_LOOP, HIGH_TRADE_FREQUENCY,
  API_RATE_LIMIT, SUDDEN_DRAWDOWN, SLIPPAGE_SPIKE).
- Rule-based AI doctor with LLM swap-in stub.
- YAML-based RiskEngine with 9 rules.
- FastAPI + WebSocket dashboard (single-page, no JS framework).
- Demo bot with the intentional infinite-loop bug.
- 10 smoke tests.