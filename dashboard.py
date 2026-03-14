import json, os
from http.server import HTTPServer, BaseHTTPRequestHandler

STATE_FILE = "/tmp/state.json"

HTML = """<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta http-equiv="refresh" content="60"/>
<title>APEX BOT</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Syne:wght@700;800&display=swap" rel="stylesheet"/>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#080c14;color:#e2e8f0;font-family:'JetBrains Mono',monospace;min-height:100vh}
.header{background:#0a0f1a;border-bottom:1px solid #1e2d45;padding:12px 20px;display:flex;align-items:center;justify-content:space-between}
.logo{display:flex;align-items:center;gap:10px}
.logo-icon{width:32px;height:32px;border-radius:8px;background:linear-gradient(135deg,#f0b90b,#f57c00);display:flex;align-items:center;justify-content:center;font-size:16px}
.logo-text{font-family:'Syne',sans-serif;font-weight:800;font-size:15px;color:#f8fafc}
.logo-sub{font-size:9px;color:#1e2d45;letter-spacing:.14em}
.live{font-size:11px;color:#22c55e;display:flex;align-items:center;gap:5px}
.dot{width:7px;height:7px;border-radius:50%;background:#22c55e;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.body{padding:16px 20px;max-width:1000px;margin:0 auto}
.stats{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:16px}
@media(min-width:600px){.stats{grid-template-columns:repeat(4,1fr)}}
.stat{background:#0d1421;border:1px solid #1e2d45;border-radius:10px;padding:14px}
.stat-label{font-size:9px;color:#1e2d45;letter-spacing:.14em;margin-bottom:5px}
.stat-value{font-family:'Syne',sans-serif;font-weight:700;font-size:20px}
.coins{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:16px}
@media(min-width:600px){.coins{grid-template-columns:repeat(4,1fr)}}
.coin{background:#0d1421;border:1px solid #1e2d45;border-radius:10px;padding:14px}
.coin.open{border-color:#f0b90b;box-shadow:0 0 16px rgba(240,185,11,.1)}
.coin-name{font-family:'Syne',sans-serif;font-weight:700;font-size:13px;margin-bottom:6px;display:flex;justify-content:space-between}
.coin-price{font-size:18px;font-weight:700;color:#f8fafc;margin-bottom:6px}
.coin-rsi{font-size:11px;margin-bottom:8px}
.rsi-bar{height:3px;background:#1e2d45;border-radius:2px;overflow:hidden;margin-bottom:6px}
.rsi-fill{height:100%;border-radius:2px;transition:width .6s}
.open-badge{font-size:8px;background:rgba(240,185,11,.15);color:#f0b90b;padding:2px 6px;border-radius:3px}
.pos-box{background:#080c14;border-radius:6px;padding:8px;font-size:10px;margin-top:8px}
.pos-row{display:flex;justify-content:space-between;margin-bottom:3px}
.trades{background:#0d1421;border:1px solid #1e2d45;border-radius:10px;overflow:hidden}
.trades-header{padding:11px 15px;border-bottom:1px solid #1e2d45;font-family:'Syne',sans-serif;font-weight:700;font-size:11px;color:#f0b90b;letter-spacing:.1em}
table{width:100%;border-collapse:collapse;font-size:11px}
th{padding:8px 12px;text-align:left;color:#1e2d45;font-size:10px;background:#080c14}
td{padding:8px 12px;border-bottom:1px solid #0a0f1a}
.buy{color:#22c55e;background:rgba(34,197,94,.1);padding:2px 6px;border-radius:3px;font-size:10px;font-weight:700}
.sell{color:#ef4444;background:rgba(239,68,68,.1);padding:2px 6px;border-radius:3px;font-size:10px;font-weight:700}
.footer{margin-top:12px;padding:10px 14px;background:#0a0f1a;border-radius:8px;border:1px solid #1e2d45;font-size:10px;color:#1e2d45;display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px}
.signal-box{background:#0d1421;border:1px solid #1e2d45;border-radius:10px;padding:14px;margin-bottom:16px}
.signal-title{font-family:'Syne',sans-serif;font-weight:700;font-size:11px;color:#f0b90b;letter-spacing:.1em;margin-bottom:10px}
.signal-row{display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid #0a0f1a;font-size:12px}
.signal-row:last-child{border-bottom:none}
.rsi-value{font-weight:700;padding:2px 8px;border-radius:4px;font-size:11px}
.rsi-oversold{background:rgba(34,197,94,.15);color:#22c55e}
.rsi-overbought{background:rgba(239,68,68,.15);color:#ef4444}
.rsi-neutral{background:rgba(245,158,11,.15);color:#f59e0b}
.waiting{color:#334155;font-size:11px;text-align:center;padding:8px}
</style>
</head><body>
<div class="header">
  <div class="logo">
    <div class="logo-icon">⚡</div>
    <div>
      <div class="logo-text">APEX BOT</div>
      <div class="logo-sub">PAPER TRADING • 24/7 • KRAKEN PRICES</div>
    </div>
  </div>
  <div class="live"><div class="dot"></div> LIVE</div>
</div>
<div class="body">
  __CONTENT__
  <div style="font-size:9px;color:#1e2d45;text-align:center;margin-top:8px">⟳ Auto-refreshes every 60 seconds</div>
</div>
</body></html>"""

COIN_COLORS = {"BTC": "#f7931a", "ETH": "#627eea", "SOL": "#9945ff", "XRP": "#346aa9"}

def rsi_color(rsi):
    if rsi is None: return "#4b5563"
    if rsi < 32: return "#22c55e"
    if rsi > 68: return "#ef4444"
    return "#f59e0b"

def rsi_class(rsi):
    if rsi is None: return "rsi-neutral"
    if rsi < 32: return "rsi-oversold"
    if rsi > 68: return "rsi-overbought"
    return "rsi-neutral"

def render(state):
    bal     = state.get("balance", 0)
    stats   = state.get("stats", {})
    pos     = state.get("positions", {})
    trades  = state.get("trades", [])
    prices  = state.get("prices", {})
    pnl     = stats.get("pnl", 0)
    wins    = stats.get("wins", 0)
    losses  = stats.get("losses", 0)
    total   = wins + losses
    wr      = f"{wins/total*100:.1f}%" if total > 0 else "—"
    updated = state.get("updated", "—")[:19].replace("T", " ")
    mode    = state.get("mode", "paper").upper()

    pnl_color = "#22c55e" if pnl >= 0 else "#ef4444"

    stats_html = f"""<div class="stats">
      <div class="stat"><div class="stat-label">PAPER BALANCE</div><div class="stat-value" style="color:#f0b90b">${bal:,.2f}</div></div>
      <div class="stat"><div class="stat-label">REALIZED P&L</div><div class="stat-value" style="color:{pnl_color}">{'+' if pnl>=0 else ''}${pnl:.2f}</div></div>
      <div class="stat"><div class="stat-label">WIN RATE</div><div class="stat-value" style="color:#60a5fa">{wr}</div><div style="font-size:10px;color:#334155;margin-top:3px">{wins}W / {losses}L</div></div>
      <div class="stat"><div class="stat-label">OPEN POSITIONS</div><div class="stat-value" style="color:#a78bfa">{len(pos)}</div></div>
    </div>"""

    # RSI Signal Panel
    signal_rows = ""
    symbols = ["BTC", "ETH", "SOL", "XRP"]
    for sym in symbols:
        price = prices.get(sym, 0)
        p = pos.get(sym)
        rsi_val = None  # RSI not stored in state yet
        color = COIN_COLORS.get(sym, "#fff")
        pnl_str = ""
        if p and price:
            pct = (price - p["entry"]) / p["entry"] * 100
            pnl_str = f'<span style="color:{"#22c55e" if pct>=0 else "#ef4444"}">{pct:+.2f}%</span>'
        signal_rows += f"""<div class="signal-row">
          <span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{color};margin-right:6px"></span>{sym}/USD</span>
          <span style="color:#f8fafc;font-weight:700">${price:,.2f}</span>
          <span style="color:#334155">{'🟡 OPEN ' + pnl_str if p else '⏳ Watching'}</span>
        </div>"""

    signal_html = f"""<div class="signal-box">
      <div class="signal-title">📊 LIVE PRICES & STATUS — Scanning every 60s</div>
      {signal_rows}
      <div class="waiting">🎯 Bot buys when RSI &lt; 32 (oversold) • Currently waiting for signal • Last scan: {updated}</div>
    </div>"""

    # Trades table
    rows = ""
    for t in reversed(trades[-20:]):
        pnl_td = f"+${t['pnl']:.2f}" if t.get('pnl') is not None and t['pnl']>=0 else (f"${t['pnl']:.2f}" if t.get('pnl') is not None else "—")
        pnl_color_td = "#22c55e" if t.get('pnl') is not None and t['pnl']>=0 else ("#ef4444" if t.get('pnl') is not None else "#334155")
        time_str = t.get('time','')[:19].replace('T',' ')
        rows += f"""<tr>
          <td style="color:#334155">{time_str}</td>
          <td style="color:#f8fafc;font-weight:700">{t.get('symbol','')}</td>
          <td><span class="{'buy' if t['side']=='BUY' else 'sell'}">{t['side']}</span></td>
          <td style="color:#e2e8f0">${t.get('price',0):,.2f}</td>
          <td style="color:{pnl_color_td}">{pnl_td}</td>
          <td style="color:#334155">{t.get('reason','')}</td>
        </tr>"""

    trades_html = f"""<div class="trades">
      <div class="trades-header">ORDER HISTORY ({len(trades)})</div>
      <div style="overflow-x:auto"><table>
        <thead><tr><th>TIME</th><th>PAIR</th><th>SIDE</th><th>PRICE</th><th>P&L</th><th>REASON</th></tr></thead>
        <tbody>{''.join([rows]) if rows else '<tr><td colspan="6" style="text-align:center;color:#1e2d45;padding:20px">No trades yet — waiting for RSI &lt; 32 signal</td></tr>'}</tbody>
      </table></div>
    </div>"""

    footer = f'<div class="footer"><span>📐 RSI(14) • Buy &lt;32 • SL 2% • TP 3.5% • 60s scan</span><span style="color:#f0b90b">Mode: {mode} • {updated}</span></div>'

    return HTML.replace("__CONTENT__", stats_html + signal_html + trades_html + footer)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE) as f:
                    state = json.load(f)
            else:
                state = {}
            body = render(state).encode()
            self.send_response(200)
            self.send_header("Content-Type","text/html")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"🌐 Dashboard running on port {port}")
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()
