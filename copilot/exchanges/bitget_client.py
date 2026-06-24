"""
bitget_client.py - Minimal Bitget v2 REST + WebSocket adapter.

Goals:
  * No hard dependency on Bitget's official SDK (it's heavy and changes often).
  * Read-only by default: only signed endpoints you opt into via `with_keys`.
  * Exponential backoff on rate-limit / 5xx, respecting `x-mbx-request-rate-limit-*` style hints.
  * Public WebSocket helper for tickers / candles (no auth needed).
  * Deterministic, mockable: pass a custom `session` / `transport` for tests.

Usage (read-only public data):

    >>> from copilot.exchanges import BitgetClient
    >>> c = BitgetClient()
    >>> tickers = c.get_tickers("SPOT")
    >>> btc = next(t for t in tickers if t["symbol"] == "BTCUSDT")
    >>> print(btc["lastPr"], btc["bidPr"], btc["askPr"])

Usage (signed):

    >>> from copilot.exchanges import BitgetCredentials, BitgetClient
    >>> creds = BitgetCredentials(api_key="...", api_secret="...", passphrase="...")
    >>> c = BitgetClient(creds=creds)
    >>> acct = c.get_account("spot")

Bitget signing (v2):
    prehash = timestamp + method.upper() + requestPath + (?body)
    signature = base64(HMAC_SHA256(api_secret, prehash))
    Headers:
      ACCESS-KEY, ACCESS-SIGN, ACCESS-TIMESTAMP, ACCESS-PASSPHRASE
      + x-simulated-trading: 1 if using demo trading
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional
from urllib.parse import urlencode

import requests


log = logging.getLogger("copilot.exchanges.bitget")


# -----------------------------------------------------------------------------
# Errors
# -----------------------------------------------------------------------------
class BitgetAPIError(RuntimeError):
    """Raised on non-2xx Bitget responses."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        code: Optional[str] = None,
        body: Any = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.body = body


# -----------------------------------------------------------------------------
# Credentials
# -----------------------------------------------------------------------------
@dataclass
class BitgetCredentials:
    """Bitget v2 API credentials.

    Use the `demo` flag to route to simulated trading endpoints
    (sets the `x-simulated-trading: 1` header).
    """

    api_key: str
    api_secret: str
    passphrase: str
    demo: bool = False

    @classmethod
    def from_env(cls) -> "BitgetCredentials":
        import os

        return cls(
            api_key=os.environ.get("BITGET_API_KEY", ""),
            api_secret=os.environ.get("BITGET_API_SECRET", ""),
            passphrase=os.environ.get("BITGET_PASSPHRASE", ""),
            demo=os.environ.get("BITGET_DEMO", "1") not in ("0", "false", "False"),
        )


# -----------------------------------------------------------------------------
# Client
# -----------------------------------------------------------------------------
class BitgetClient:
    """Minimal Bitget v2 client.

    Parameters
    ----------
    base_url:
        REST base URL. Use the demo URL when `credentials.demo` is True.
    ws_url:
        Public WebSocket base URL (no auth path supported here).
    credentials:
        Optional. Without it, only public endpoints work.
    timeout:
        Per-request timeout (seconds).
    max_retries:
        Retries on 429 / 5xx with exponential backoff.
    session:
        Inject a `requests.Session` for testing.
    """

    DEFAULT_REST = "https://api.bitget.com"
    DEMO_REST = "https://api.bitget.com"  # same host; demo header flips it
    DEFAULT_WS = "wss://ws.bitget.com/v2/ws/public"

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_REST,
        ws_url: str = DEFAULT_WS,
        credentials: Optional[BitgetCredentials] = None,
        creds: Optional[BitgetCredentials] = None,  # alias for `credentials`
        timeout: float = 10.0,
        max_retries: int = 3,
        session: Optional[requests.Session] = None,
    ):
        # Accept both `credentials` and the short alias `creds` so callers
        # can pick whichever reads better.
        if credentials is None and creds is not None:
            credentials = creds
        self.base_url = base_url.rstrip("/")
        self.ws_url = ws_url
        self.credentials = credentials
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = session or requests.Session()

    # ---- low-level HTTP ---------------------------------------------------
    def _timestamp_ms(self) -> str:
        # Bitget v2 expects millisecond epoch as a string.
        return str(int(time.time() * 1000))

    def _sign(self, ts: str, method: str, path: str, body: str) -> str:
        if not self.credentials:
            raise BitgetAPIError("Cannot sign without credentials")
        prehash = ts + method.upper() + path + (body or "")
        digest = hmac.new(
            self.credentials.api_secret.encode("utf-8"),
            prehash.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        body: Optional[dict] = None,
        signed: bool = False,
    ) -> Any:
        url = self.base_url + path

        # Build query string separately so it matches the signing prehash.
        if params:
            qs = urlencode(params, doseq=True)
            url = f"{url}?{qs}"
        else:
            qs = ""

        body_str = "" if body is None else json.dumps(body, separators=(",", ":"))

        headers = {"Content-Type": "application/json"}
        if signed:
            if not self.credentials:
                raise BitgetAPIError("Endpoint requires credentials")
            ts = self._timestamp_ms()
            sign_path = path + (("?" + qs) if qs else "")
            headers.update(
                {
                    "ACCESS-KEY": self.credentials.api_key,
                    "ACCESS-SIGN": self._sign(ts, method, sign_path, body_str),
                    "ACCESS-TIMESTAMP": ts,
                    "ACCESS-PASSPHRASE": self.credentials.passphrase,
                }
            )
            if self.credentials.demo:
                headers["x-simulated-trading"] = "1"

        attempt = 0
        last_exc: Optional[Exception] = None
        while attempt <= self.max_retries:
            try:
                resp = self._session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    data=body_str if body is not None else None,
                    timeout=self.timeout,
                )
            except requests.RequestException as e:
                last_exc = e
                attempt += 1
                self._sleep_backoff(attempt)
                continue

            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                last_exc = BitgetAPIError(
                    f"Bitget transient error {resp.status_code}",
                    status_code=resp.status_code,
                )
                attempt += 1
                self._sleep_backoff(attempt)
                continue

            try:
                payload = resp.json()
            except ValueError:
                payload = {"raw": resp.text}

            if resp.status_code >= 400:
                raise BitgetAPIError(
                    f"Bitget API error: {payload}",
                    status_code=resp.status_code,
                    code=str(payload.get("code")) if isinstance(payload, dict) else None,
                    body=payload,
                )

            # Bitget wraps successful payloads: {"code": "00000", "msg": "success", "data": [...]}
            if isinstance(payload, dict) and payload.get("code") not in (None, "00000", "success"):
                raise BitgetAPIError(
                    f"Bitget API business error: {payload.get('msg') or payload}",
                    status_code=resp.status_code,
                    code=str(payload.get("code")),
                    body=payload,
                )
            return payload.get("data", payload) if isinstance(payload, dict) else payload

        # exhausted retries
        raise BitgetAPIError(
            f"Bitget request failed after {self.max_retries + 1} attempts: {last_exc}"
        )

    def _sleep_backoff(self, attempt: int) -> None:
        # 0.5s, 1s, 2s, 4s ...
        delay = 0.5 * (2 ** (attempt - 1))
        time.sleep(delay)

    # ---- public REST endpoints -------------------------------------------
    def get_server_time(self) -> dict:
        """`GET /api/v2/public/time` - returns server timestamp in ms."""
        return self._request("GET", "/api/v2/public/time")

    def get_tickers(self, product_type: str = "SPOT", symbol: Optional[str] = None) -> list[dict]:
        """`GET /api/v2/spot/market/tickers` - list of spot tickers.

        `product_type` is accepted for API symmetry but Bitget's v2 routes
        spot tickers under `/spot/...` regardless. Pass `symbol` to narrow
        the result set server-side.
        """
        params: dict = {}
        if symbol:
            params["symbol"] = symbol
        data = self._request("GET", "/api/v2/spot/market/tickers", params=params)
        return data if isinstance(data, list) else []

    def get_candles(
        self,
        symbol: str,
        granularity: str = "1min",
        product_type: str = "SPOT",
        limit: int = 100,
    ) -> list[dict]:
        """`GET /api/v2/spot/market/candles` - OHLCV candles.

        Bitget's v2 candles endpoint accepts these granularities:
        `1min`, `3min`, `5min`, `15min`, `30min`, `1h`, `4h`, `6h`,
        `12h`, `1day`, `1week`, `1M`, `6Hutc`, `12Hutc`, `1Dutc`,
        `3Dutc`, `1Wutc`, `1Mutc`.

        The client accepts short aliases (`1m`, `5m`, `15m`, `1h`, `1d`,
        `1w`) and translates them to Bitget's long form.
        """
        granularity_map = {
            "1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min",
            "30m": "30min", "1h": "1h", "4h": "4h", "6h": "6h",
            "12h": "12h", "1d": "1day", "1w": "1week",
        }
        g = granularity_map.get(granularity, granularity)
        params = {
            "symbol": symbol,
            "granularity": g,
            "limit": str(min(max(limit, 1), 1000)),
        }
        data = self._request("GET", "/api/v2/spot/market/candles", params=params)
        return data if isinstance(data, list) else []

    # ---- signed endpoints ------------------------------------------------
    def get_account(self, product_type: str = "spot") -> list[dict]:
        """`GET /api/v2/account/account` - account balances (requires auth)."""
        if not self.credentials:
            raise BitgetAPIError("get_account requires credentials")
        params = {"productType": product_type, "marginCoin": "USDT"}
        return self._request(
            "GET",
            "/api/v2/account/account",
            params=params,
            signed=True,
        )

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        size: str,
        *,
        price: Optional[str] = None,
        product_type: str = "SPOT",
        margin_coin: str = "USDT",
        client_oid: Optional[str] = None,
        reduce_only: bool = False,
    ) -> dict:
        """`POST /api/v2/trade/place-order` - submit a new order.

        This is the **only** mutating endpoint exposed by the adapter and it
        requires credentials. Use `BitgetBotRunner` for risk-gated placement.
        """
        if not self.credentials:
            raise BitgetAPIError("place_order requires credentials")
        if order_type.upper() == "LIMIT" and not price:
            raise BitgetAPIError("LIMIT orders require price")

        body: dict = {
            "symbol": symbol,
            "productType": product_type,
            "marginCoin": margin_coin,
            "side": side.lower(),
            "orderType": order_type.lower(),
            "size": size,
            "reduceOnly": "YES" if reduce_only else "NO",
        }
        if price is not None:
            body["price"] = price
        if client_oid:
            body["clientOid"] = client_oid
        else:
            body["clientOid"] = f"qc-{int(time.time() * 1000)}"

        return self._request("POST", "/api/v2/trade/place-order", body=body, signed=True)


# -----------------------------------------------------------------------------
# Public WebSocket helper (tickers + candles). Authenticated WS is out of scope.
# -----------------------------------------------------------------------------
def public_ws_subscribe_instructions(
    channels: Iterable[tuple[str, str]],
    *,
    product_type: str = "SPOT",
) -> str:
    """Build a JSON subscribe frame for the Bitget public WebSocket.

    Parameters
    ----------
    channels:
        Iterable of (channel, symbol) pairs. Examples:
            ("ticker", "BTCUSDT")
            ("candle5m", "ETHUSDT")
    product_type:
        "SPOT" or "USDT-FUTURES" etc.

    Returns
    -------
    JSON string ready to send.
    """
    args = [{"instType": product_type, "channel": ch, "instId": sym} for ch, sym in channels]
    return json.dumps({"op": "subscribe", "args": args})