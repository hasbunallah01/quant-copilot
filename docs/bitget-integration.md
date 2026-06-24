# Bitget Integration

Quant Copilot ships with a minimal but production-shaped Bitget v2
adapter (`copilot/exchanges/`). It is the bridge between your strategy
code and the exchange, gated by the same `RiskEngine` the dashboard uses.

## Components

| File                              | What it is                                                                                                              |
|-----------------------------------|-------------------------------------------------------------------------------------------------------------------------|
| `copilot/exchanges/bitget_client.py` | `BitgetClient` — REST client with v2 HMAC signing, retries on 429/5xx, injectable `requests.Session` for tests. Public methods: `get_server_time`, `get_tickers`, `get_candles`, `get_account` (signed), `place_order` (signed). |
| `copilot/exchanges/bitget_bot.py`    | `BitgetBotRunner` — wraps `BitgetClient` + `RiskEngine`. `evaluate()` returns the risk verdict; `place()` evaluates then submits (or simulates). |
| `copilot/exchanges/__init__.py`      | Re-exports the public surface.                                                                                          |
| `examples/bitget_runner_example.py`  | End-to-end usage with a mocked client (no API keys needed).                                                             |

## Signing (Bitget v2)

```
prehash   = ACCESS-TIMESTAMP + METHOD.upper() + requestPath + (?queryString) + body
signature = base64(HMAC_SHA256(api_secret, prehash))
headers   = ACCESS-KEY, ACCESS-SIGN, ACCESS-TIMESTAMP, ACCESS-PASSPHRASE
            + x-simulated-trading: 1   # when credentials.demo is True
```

This is verified in `tests/test_bitget_client.py::test_signed_endpoint_produces_correct_signature`
— the test recomputes the expected HMAC and asserts byte equality with
the header the client produced.

## Public WebSocket helper

`public_ws_subscribe_instructions([("ticker", "BTCUSDT"), ("candle5m", "ETHUSDT")])`
returns a JSON frame ready to send on `wss://ws.bitget.com/v2/ws/public`.
Authenticated WS streams are intentionally out of scope for this
minimal adapter; route them through the official SDK if you need them.

## Retry policy

* 429 → exponential backoff (0.5s, 1s, 2s, 4s, ...)
* 5xx → same
* 4xx (non-429) → raise `BitgetAPIError` immediately (caller's bug)
* `requests.RequestException` → same retry loop

The cap is `max_retries=3` by default. Override on the client.

## Risk-gated order placement

```python
from copilot.exchanges import BitgetBotRunner, BitgetClient, BitgetCredentials
from copilot.risk_engine import RiskEngine

client = BitgetClient(creds=BitgetCredentials.from_env())
risk   = RiskEngine("rules/default.yaml")
runner = BitgetBotRunner(client, risk, simulated=False)

# Dry-run: see what the engine would do
verdict = runner.evaluate(symbol="BTCUSDT", side="BUY", qty=0.001, price=65000)
print(verdict.allowed, verdict.rule_violated)

# Live: evaluate then submit if allowed
result = runner.place(symbol="BTCUSDT", side="BUY", qty=0.001, price=65000,
                      order_type="LIMIT")
if result.submitted:
    print("filled with orderId", result.order_response["orderId"])
```

`simulated=True` short-circuits the network call. Use it for paper
trading, CI, and backtests; flip to `False` once your API keys are set.

## Why a thin adapter and not the official SDK?

* The Bitget official SDK is large and changes often. For an audit-grade
  hackathon submission, judges should be able to read the entire
  integration layer in one sitting (~250 LOC).
* The adapter is **injection-friendly**: tests pass a mocked
  `requests.Session` and `BitgetClient`, so the runner itself never
  has to know whether it's talking to the real exchange.
* If you want the official SDK later, replace the `BitgetClient`
  with one that wraps the SDK; the rest of the system stays the same
  because `BitgetBotRunner` only depends on the public method names.