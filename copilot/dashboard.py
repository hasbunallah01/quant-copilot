"""
dashboard.py - FastAPI web dashboard for Quant Copilot.

Run with:
    python -m copilot.dashboard

Then open http://localhost:8000 in your browser.

Features:
  - Live log feed (WebSocket)
  - Real-time anomaly alerts with AI diagnoses
  - Risk engine status panel
  - Trade approval endpoint (POST /api/check-trade)
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .ai_doctor import DiagnoseDoctor
from .detector import AnomalyDetector
from .risk_engine import CheckResult, RiskEngine, TradeRequest
from .watcher import LogWatcher, parse_log_line


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = PROJECT_ROOT / "logs" / "demo.log"
RULES_FILE = PROJECT_ROOT / "rules" / "default.yaml"
HOST = os.environ.get("QUANTCOPILOT_HOST", "0.0.0.0")
PORT = int(os.environ.get("QUANTCOPILOT_PORT", "8000"))


# -----------------------------------------------------------------------------
# Shared state
# -----------------------------------------------------------------------------
class State:
    """Shared in-memory state for the dashboard."""

    def __init__(self):
        self.detector = AnomalyDetector()
        self.doctor = DiagnoseDoctor()
        self.risk_engine = RiskEngine(str(RULES_FILE))
        self.log_buffer: list[dict] = []  # last N parsed events
        self.anomalies: list[dict] = []  # last N anomalies with diagnoses
        self.connected_clients: set[WebSocket] = set()
        self.bot_status: str = "idle"  # idle, running, halted
        self._max_log_buffer = 200
        self._max_anomalies = 100

    def add_event(self, event: dict) -> None:
        self.log_buffer.append(event)
        if len(self.log_buffer) > self._max_log_buffer:
            self.log_buffer = self.log_buffer[-self._max_log_buffer:]

    def add_anomaly(self, anomaly_dict: dict) -> None:
        self.anomalies.append(anomaly_dict)
        if len(self.anomalies) > self._max_anomalies:
            self.anomalies = self.anomalies[-self._max_anomalies:]

    async def broadcast(self, message: dict) -> None:
        if not self.connected_clients:
            return
        text = json.dumps(message, default=str)
        dead = set()
        for ws in list(self.connected_clients):
            try:
                await ws.send_text(text)
            except Exception:
                dead.add(ws)
        self.connected_clients -= dead


STATE = State()


# -----------------------------------------------------------------------------
# Log watcher setup
# -----------------------------------------------------------------------------
def on_log_line(line: str) -> None:
    """Callback invoked by LogWatcher for each new log line."""
    event = parse_log_line(line)
    STATE.add_event(event)

    # Run anomaly detection
    new_anomalies = STATE.detector.feed(event)
    for anom in new_anomalies:
        diagnosis = STATE.doctor.diagnose(anom)
        combined = {
            **anom.to_dict(),
            "diagnosis": diagnosis,
        }
        STATE.add_anomaly(combined)
        # Schedule the broadcast (sync -> async)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(
                    STATE.broadcast({"type": "anomaly", "data": combined})
                )
        except RuntimeError:
            pass

    # Broadcast the raw event too
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(
                STATE.broadcast({"type": "event", "data": event})
            )
    except RuntimeError:
        pass


# -----------------------------------------------------------------------------
# FastAPI app
# -----------------------------------------------------------------------------
app = FastAPI(
    title="Quant Copilot",
    description="AI debugging copilot for crypto trading bots",
    version="0.1.0",
)


@app.on_event("startup")
async def startup_event():
    """Start the log watcher when the server starts."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.touch(exist_ok=True)
    watcher = LogWatcher(str(LOG_FILE), on_line=on_log_line, from_start=True)
    watcher.start()
    STATE._watcher = watcher  # keep reference
    STATE.bot_status = "running"


@app.on_event("shutdown")
async def shutdown_event():
    if hasattr(STATE, "_watcher"):
        STATE._watcher.stop()


class CheckTradeRequest(BaseModel):
    symbol: str
    side: str
    quantity: float
    price: float
    account_equity: float = 10000.0


class CheckTradeResponse(BaseModel):
    allowed: bool
    reason: str = ""
    rule_violated: Optional[str] = None


@app.get("/api/status")
async def get_status():
    return {
        "status": STATE.bot_status,
        "log_count": len(STATE.log_buffer),
        "anomaly_count": len(STATE.anomalies),
        "kill_switch_active": STATE.risk_engine._kill_switch_active,
        "open_positions": STATE.risk_engine._open_positions,
        "current_equity": STATE.risk_engine._current_equity,
        "peak_equity": STATE.risk_engine._peak_equity,
    }


@app.get("/api/logs")
async def get_logs():
    return {"events": STATE.log_buffer}


@app.get("/api/anomalies")
async def get_anomalies():
    return {"anomalies": STATE.anomalies}


@app.post("/api/check-trade", response_model=CheckTradeResponse)
async def check_trade(req: CheckTradeRequest):
    trade = TradeRequest(
        symbol=req.symbol,
        side=req.side.upper(),
        quantity=req.quantity,
        price=req.price,
        account_equity=req.account_equity,
    )
    result: CheckResult = STATE.risk_engine.check_trade(trade)
    payload = result.to_dict()

    # If allowed, record the trade in the engine's history
    if result.allowed:
        STATE.risk_engine.record_trade(trade)

    # Broadcast the check result
    await STATE.broadcast(
        {
            "type": "risk_check",
            "data": {
                "symbol": req.symbol,
                "side": req.side,
                "quantity": req.quantity,
                "price": req.price,
                "allowed": result.allowed,
                "reason": result.reason,
                "rule_violated": result.rule_violated,
            },
        }
    )

    return CheckTradeResponse(
        allowed=result.allowed,
        reason=result.reason,
        rule_violated=result.rule_violated,
    )


@app.post("/api/reset-kill-switch")
async def reset_kill_switch():
    STATE.risk_engine.reset_kill_switch()
    return {"ok": True, "kill_switch_active": False}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    STATE.connected_clients.add(ws)
    try:
        # Send a snapshot on connect
        await ws.send_text(
            json.dumps(
                {
                    "type": "snapshot",
                    "data": {
                        "events": STATE.log_buffer[-50:],
                        "anomalies": STATE.anomalies[-20:],
                    },
                },
                default=str,
            )
        )
        # Keep the connection alive; we don't expect inbound messages
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        STATE.connected_clients.discard(ws)


# -----------------------------------------------------------------------------
# HTML dashboard (single-page, no JS framework)
# -----------------------------------------------------------------------------
INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>Quant Copilot — Live Dashboard</title>
<style>
  :root {
    --bg: #0b0d12;
    --panel: #131722;
    --border: #1f2937;
    --text: #e6e9ef;
    --muted: #8b93a7;
    --accent: #4f8cff;
    --green: #2ecc71;
    --yellow: #f1c40f;
    --orange: #e67e22;
    --red: #e74c3c;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.5;
  }
  header {
    background: var(--panel);
    border-bottom: 1px solid var(--border);
    padding: 16px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  header h1 { font-size: 20px; font-weight: 600; }
  header h1 .accent { color: var(--accent); }
  .status-pill {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
  }
  .status-running { background: rgba(46, 204, 113, 0.15); color: var(--green); }
  .status-idle    { background: rgba(139, 147, 167, 0.15); color: var(--muted); }
  .status-halted  { background: rgba(231, 76, 60, 0.15); color: var(--red); }
  .container { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding: 16px; }
  .panel {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
  }
  .panel-header {
    padding: 12px 16px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .panel-header h2 { font-size: 14px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); }
  .panel-body { padding: 12px 16px; max-height: 70vh; overflow-y: auto; }
  .log-line {
    font-family: "SF Mono", Monaco, "Cascadia Code", monospace;
    font-size: 12px;
    padding: 4px 0;
    border-bottom: 1px solid rgba(255,255,255,0.04);
    word-break: break-all;
  }
  .log-line .ts { color: var(--muted); margin-right: 8px; }
  .log-line .lvl { font-weight: 600; margin-right: 8px; }
  .lvl-INFO  { color: var(--accent); }
  .lvl-WARN  { color: var(--yellow); }
  .lvl-ERROR { color: var(--red); }
  .anomaly {
    border-left: 3px solid var(--orange);
    background: rgba(230, 126, 34, 0.08);
    padding: 12px;
    margin-bottom: 12px;
    border-radius: 4px;
  }
  .anomaly.HIGH     { border-color: var(--orange); background: rgba(230, 126, 34, 0.08); }
  .anomaly.CRITICAL { border-color: var(--red);    background: rgba(231, 76, 60, 0.10); }
  .anomaly.MEDIUM   { border-color: var(--yellow); background: rgba(241, 196, 15, 0.08); }
  .anomaly-header {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 8px;
  }
  .anomaly-type { font-weight: 600; font-size: 13px; }
  .anomaly-severity {
    font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 3px;
    text-transform: uppercase;
  }
  .sev-LOW      { background: rgba(139,147,167,0.2); color: var(--muted); }
  .sev-MEDIUM   { background: rgba(241,196,15,0.2);  color: var(--yellow); }
  .sev-HIGH     { background: rgba(230,126,34,0.2);  color: var(--orange); }
  .sev-CRITICAL { background: rgba(231,76,60,0.2);   color: var(--red); }
  .anomaly-msg { font-size: 13px; color: var(--text); margin-bottom: 8px; }
  .diagnosis { font-size: 12px; color: var(--muted); }
  .diagnosis strong { color: var(--text); }
  .diagnosis pre {
    background: rgba(0,0,0,0.3);
    padding: 8px;
    border-radius: 4px;
    font-size: 11px;
    overflow-x: auto;
    margin-top: 4px;
    color: #cbd5e1;
  }
  .empty { color: var(--muted); font-style: italic; padding: 24px; text-align: center; }
  .risk-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .risk-cell { padding: 8px; background: rgba(0,0,0,0.2); border-radius: 4px; }
  .risk-cell .label { font-size: 11px; color: var(--muted); text-transform: uppercase; }
  .risk-cell .value { font-size: 16px; font-weight: 600; margin-top: 2px; }
  .footer { padding: 12px 24px; color: var(--muted); font-size: 12px; text-align: center; border-top: 1px solid var(--border); }
  .pulse { animation: pulse 2s ease-in-out infinite; }
  @keyframes pulse { 0%,100%{opacity:1;} 50%{opacity:0.4;} }
</style>
</head>
<body>
<header>
  <h1>📊 <span class="accent">Quant Copilot</span> — Live Dashboard</h1>
  <div>
    <span id="status-pill" class="status-pill status-running pulse">● CONNECTING</span>
  </div>
</header>

<div class="container">
  <section class="panel" style="grid-column: 1 / -1;">
    <div class="panel-header">
      <h2>🛡️ Risk Engine Status</h2>
      <button onclick="resetKillSwitch()" style="background:transparent;color:var(--accent);border:1px solid var(--accent);padding:4px 10px;border-radius:4px;cursor:pointer;font-size:12px;">Reset Kill Switch</button>
    </div>
    <div class="panel-body">
      <div class="risk-grid" id="risk-grid">
        <div class="risk-cell"><div class="label">Status</div><div class="value" id="risk-status">—</div></div>
        <div class="risk-cell"><div class="label">Current Equity</div><div class="value" id="risk-equity">—</div></div>
        <div class="risk-cell"><div class="label">Peak Equity</div><div class="value" id="risk-peak">—</div></div>
        <div class="risk-cell"><div class="label">Drawdown</div><div class="value" id="risk-drawdown">—</div></div>
        <div class="risk-cell"><div class="label">Open Positions</div><div class="value" id="risk-positions">—</div></div>
        <div class="risk-cell"><div class="label">Total Exposure</div><div class="value" id="risk-exposure">—</div></div>
      </div>
    </div>
  </section>

  <section class="panel">
    <div class="panel-header">
      <h2>📜 Live Log Feed</h2>
      <span id="log-count" style="color:var(--muted);font-size:12px;">0 events</span>
    </div>
    <div class="panel-body" id="log-body">
      <div class="empty">Waiting for log events...</div>
    </div>
  </section>

  <section class="panel">
    <div class="panel-header">
      <h2>🚨 Anomalies & AI Diagnoses</h2>
      <span id="anomaly-count" style="color:var(--muted);font-size:12px;">0 alerts</span>
    </div>
    <div class="panel-body" id="anomaly-body">
      <div class="empty">No anomalies detected yet. The copilot is watching.</div>
    </div>
  </section>
</div>

<div class="footer">
  Quant Copilot v0.1.0 · Bitget AI Hackathon Genesis S1 · Theme: KI × Krypto
</div>

<script>
  const logBody = document.getElementById("log-body");
  const anomalyBody = document.getElementById("anomaly-body");
  const statusPill = document.getElementById("status-pill");
  const logCount = document.getElementById("log-count");
  const anomalyCount = document.getElementById("anomaly-count");

  function setStatus(text, cls) {
    statusPill.textContent = "● " + text;
    statusPill.className = "status-pill " + cls;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
  }

  function renderEvent(ev) {
    if (logBody.querySelector(".empty")) logBody.innerHTML = "";
    const ts = ev.timestamp || "—";
    const lvl = ev.level || "INFO";
    const msg = ev.message || ev.raw || "";
    const line = document.createElement("div");
    line.className = "log-line";
    line.innerHTML = `<span class="ts">[${escapeHtml(ts)}]</span><span class="lvl lvl-${escapeHtml(lvl)}">${escapeHtml(lvl)}</span>${escapeHtml(msg)}`;
    logBody.appendChild(line);
    while (logBody.children.length > 200) logBody.removeChild(logBody.firstChild);
    logBody.scrollTop = logBody.scrollHeight;
    logCount.textContent = logBody.children.length + " events";
  }

  function renderAnomaly(a) {
    if (anomalyBody.querySelector(".empty")) anomalyBody.innerHTML = "";
    const sev = a.severity || "MEDIUM";
    const d = a.diagnosis || {};
    const div = document.createElement("div");
    div.className = "anomaly " + sev;
    div.innerHTML = `
      <div class="anomaly-header">
        <span class="anomaly-type">⚠ ${escapeHtml(a.type)}</span>
        <span class="anomaly-severity sev-${escapeHtml(sev)}">${escapeHtml(sev)}</span>
      </div>
      <div class="anomaly-msg">${escapeHtml(a.message || "")}</div>
      <div class="diagnosis">
        <strong>Cause:</strong> ${escapeHtml(d.cause || "")}<br><br>
        <strong>Where to look:</strong> ${escapeHtml(d.where_to_look || "")}<br><br>
        <strong>Fix:</strong>
        <pre>${escapeHtml(d.fix || "")}</pre>
      </div>
    `;
    anomalyBody.appendChild(div);
    while (anomalyBody.children.length > 50) anomalyBody.removeChild(anomalyBody.firstChild);
    anomalyCount.textContent = anomalyBody.children.length + " alerts";
  }

  function updateRisk(s) {
    document.getElementById("risk-status").textContent =
      s.kill_switch_active ? "🔴 HALTED" : "🟢 Active";
    document.getElementById("risk-equity").textContent =
      "$" + (s.current_equity || 0).toFixed(2);
    document.getElementById("risk-peak").textContent =
      "$" + (s.peak_equity || 0).toFixed(2);
    const dd = s.peak_equity > 0
      ? (((s.peak_equity - s.current_equity) / s.peak_equity) * 100).toFixed(2) + "%"
      : "0%";
    document.getElementById("risk-drawdown").textContent = dd;
    const positions = Object.keys(s.open_positions || {}).length;
    document.getElementById("risk-positions").textContent = positions;
    const exposure = Object.values(s.open_positions || {})
      .reduce((a, b) => a + Math.abs(b), 0);
    document.getElementById("risk-exposure").textContent = "$" + exposure.toFixed(2);
  }

  async function refreshStatus() {
    try {
      const r = await fetch("/api/status");
      const s = await r.json();
      updateRisk(s);
    } catch (e) { /* ignore */ }
  }

  async function resetKillSwitch() {
    await fetch("/api/reset-kill-switch", { method: "POST" });
    refreshStatus();
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/ws`);
    ws.onopen = () => {
      setStatus("LIVE", "status-running");
    };
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "event") renderEvent(msg.data);
        else if (msg.type === "anomaly") renderAnomaly(msg.data);
        else if (msg.type === "snapshot") {
          (msg.data.events || []).forEach(renderEvent);
          (msg.data.anomalies || []).forEach(renderAnomaly);
        }
      } catch (err) { /* ignore */ }
    };
    ws.onclose = () => {
      setStatus("DISCONNECTED", "status-halted");
      setTimeout(connect, 2000);
    };
    ws.onerror = () => ws.close();
  }

  connect();
  refreshStatus();
  setInterval(refreshStatus, 3000);
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content=INDEX_HTML)


def main() -> None:
    """CLI entrypoint: `python -m copilot.dashboard`"""
    import uvicorn
    print(f"\n  Quant Copilot dashboard starting on http://{HOST}:{PORT}\n")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
