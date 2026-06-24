# Sample Bitget API I/O

_Captured at 2026-06-24 18:47:01 UTC._

Hand-traced Bitget v2 API calls that the Quant Copilot BitgetBotRunner performs. The dashboard, log watcher, and risk engine do not change shape between demo and production: same endpoints, same request signing, same risk gating.

Each block below is one round-trip the BitgetBotRunner performs.
The same calls are exercised by `tests/test_bitget_client.py`.

## 1. Public server-time probe

**Request**
```http
GET https://api.bitget.com/api/v2/public/time
Content-Type: application/json
```

**Response**
```json
{
  "code": "00000",
  "msg": "success",
  "data": {
    "serverTime": "1720000000000"
  }
}
```

## 2. Spot tickers (used for live price feeds / slippage tracking)

**Request**
```http
GET https://api.bitget.com/api/v2/market/tickers?productType=SPOT
Content-Type: application/json
```

**Response**
```json
{
  "code": "00000",
  "msg": "success",
  "data": [
    {
      "symbol": "BTCUSDT",
      "lastPr": "65000.10",
      "bidPr": "65000.00",
      "askPr": "65000.20",
      "quoteVolume": "1234567890.12",
      "usdtVolume": "80234901350.0"
    },
    {
      "symbol": "ETHUSDT",
      "lastPr": "3200.55",
      "bidPr": "3200.50",
      "askPr": "3200.60",
      "quoteVolume": "654321098.76",
      "usdtVolume": "2093878500.0"
    }
  ]
}
```

## 3. OHLCV candles (granularity 1m)

**Request**
```http
GET https://api.bitget.com/api/v2/market/candles?symbol=BTCUSDT&productType=SPOT&granularity=1m&limit=3
Content-Type: application/json
```

**Response**
```json
{
  "code": "00000",
  "msg": "success",
  "data": [
    [
      "1720000050000",
      "65000.10",
      "65050.00",
      "64980.00",
      "65040.00",
      "12.345"
    ],
    [
      "1720000040000",
      "65040.00",
      "65045.00",
      "64950.00",
      "65000.10",
      "9.876"
    ],
    [
      "1720000030000",
      "65000.10",
      "65060.00",
      "64900.00",
      "65040.00",
      "15.123"
    ]
  ]
}
```

## 4. Signed account balance (requires API key + signature)

**Request**
```http
GET https://api.bitget.com/api/v2/account/account?productType=spot&marginCoin=USDT
Content-Type: application/json
ACCESS-KEY: ak_***
ACCESS-SIGN: base64(HMAC_SHA256(api_secret, ts + 'GET' + path + '?productType=spot&marginCoin=USDT'))
ACCESS-TIMESTAMP: 1720000000000
ACCESS-PASSPHRASE: pp_***
x-simulated-trading: 1
```

**Response**
```json
{
  "code": "00000",
  "msg": "success",
  "data": [
    {
      "marginCoin": "USDT",
      "available": "8234.56",
      "frozen": "100.00",
      "equity": "8334.56"
    }
  ]
}
```

## 5. Risk-gated order placement (BitgetBotRunner)

**Request**
```http
POST https://api.bitget.com/api/v2/trade/place-order
Content-Type: application/json
ACCESS-KEY: ak_***
ACCESS-SIGN: base64(HMAC_SHA256(api_secret, ts + 'POST' + path + body))
ACCESS-TIMESTAMP: 1720000000001
ACCESS-PASSPHRASE: pp_***
x-simulated-trading: 1

```json
{
  "symbol": "BTCUSDT",
  "productType": "SPOT",
  "marginCoin": "USDT",
  "side": "buy",
  "orderType": "limit",
  "size": "0.001",
  "price": "65000",
  "reduceOnly": "NO",
  "clientOid": "qc-1720000000001"
}
```

**Response**
```json
{
  "code": "00000",
  "msg": "success",
  "data": {
    "orderId": "112233445566778899",
    "clientOid": "qc-1720000000001"
  }
}
```

## Rate-limit handling

- 429 body: `{'code': '429', 'msg': 'too many requests'}`
- client behavior: BitgetClient retries 429 / 5xx up to `max_retries` with exponential backoff (0.5s, 1s, 2s, ...). After exhaustion it raises BitgetAPIError which BitgetBotRunner surfaces as OrderResult.error so the calling strategy can decide whether to abort or back off.
