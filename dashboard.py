import json, os
from http.server import HTTPServer, BaseHTTPRequestHandler

STATE_FILE = "/tmp/state.json"

HTML = """<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta http-equiv="refresh" content="15"/>
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
.live-badge{font-size:10px;color:#22c55e;display:flex;align-items:center;gap:5px}
.dot{width:6px;height:6px;border-radius:50%;background:#22c55e;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.body{padding:16px 20px;max-width:900px;margin:0 auto}
.stats{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:16px}
@media(min-width:600px){.stats{grid-template-columns:repeat(4,1fr)}}
.stat-card{background:#0d1421;border:1px solid #1e2d45;border-radius:10px;padding:14px}
.stat-label{font-size:9px;color:#1e2d45;letter-spacing:.14em;margin-bottom:5px}
.stat-value{font-family:'Syne',sans-serif;font-weight:700;font-size:20px}
.coins{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:16px}
.coin-card{background:#0d1421;border:1px solid #1e2d45;border-radius:10px;padding:14px}
.coin-card.open{border-color:#f0b90b;box-shadow:0 0 16px rgba(240,185,11,.1)}
.coin-name{font-family:'Syne',sans-serif;font-weight:700;font-size:13px;color:#f8fafc;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center}
.open-badge{font-size:8px;background:rgba(240,185,11,.15);color:#f0b90b;padding:2px 6px;border-radius:3px}
.coin-price{font-size:16px;font-weight:700;color:#f8fafc;margin-bottom:6px}
.pos-box{background:#080c14;border-radius:6px;padding:8px;font-size:10px;margin-top:8px}
.pos-row{display:flex;justify-content:space-between;margin-bottom:3px}
.trades-section{background:#0d1421;border:1px solid #1e2d45;border-radius:10px;overflow:hidden}
.trades-header{padding:11px 15px;border-bottom:1px solid #1e2d45;font-family:'Syne',sans-serif;font-weight:700;font-size:11px;color:#f0b90b;letter-spacing:.1em}
table{width:100%;border-collapse:collapse;font-size:11px}
th{padding:8px 12px;text-align:left;color:#1e2d45;font-size:10px;background:#080c14}
td{padding:8px 12px;border-bottom:1px solid #0a0f1a}
.buy{color:#22c55e;background:rgba(34,197,94,.1);padding:2px 6px;border-radius:3px;font-size:10px;font-weight:700}
.sell{color:#ef4444;background:rgba(239,68,68,.1);padding:2px 6px;border-radius:3px;font-size:10px;font-weight:700}
.footer{margin-top:12px;padding:10px 14px;background:#0a0f1a;border-radius:8px;border:1px solid #1e2d45;font-size:10px;color:#1e2d45;display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px}
.refresh-note{font-size:9px;color:#1e2d45;text-align:center;margin-top:8px}
</style>
</head><body>
<div class="header">
  <div class="logo">
    <div class="logo-icon">⚡</div>
    <div>
      <div class="logo-text">APEX BOT</div>
      <div class="logo-sub">BINANCE SPOT TESTNET • 24/7</div>
    </div>
  </div>
  <div class="live-badge"><div class="dot"></div> LIVE</div>
</div>
<div class="body">
  __CONTENT__
  <div class="refresh-note">⟳ Auto-refreshes every 15 seconds</div>
</div>
</body></html>"""

SYMBOLS = [
  {"pair":"BTC/USDT","symbol":"BTCUSDT","color":"#f7931a"},
  {"pair":"ETH/USDT","symbol":"ETHUSDT","color":"#627eea"},
  {"pair":"BNB/USDT","symbol":"BNBUSDT","color":"#f3ba2f"},
  {"pair":"SOL/USDT","symbol":"SOLUSDT","color":"#9945ff"},
]

def render(state):
    bal     = state.get("balance", 0)
    stats   = state.get("stats", {})
    pos     = state.get("positions", {})
    trades  = state.get("trades", [])
    pnl     = stats.get("pnl", 0)
    wins    = stats.get("wins", 0)
    losses  = stats.get("losses", 0)
    total   = wins + losses
    wr      = f"{wins/total*100:.1f}%" if total > 0 else "—"
    updated = state.get("updated","—")

    pnl_color = "#22c55e" if pnl >= 0 else "#ef4444"

    stats_html = f"""<div class="stats">
      <div class="stat-card"><div class="stat-label">TESTNET USDT</div><div class="stat-value" style="color:#f0b90b">${bal:,.2f}</div></div>
      <div class="stat-card"><div class="stat-label">REALIZED P&L</div><div class="stat-value" style="color:{pnl_color}">{'+' if pnl>=0 else ''}${pnl:.2f}</div></div>
      <div class="stat-card"><div class="stat-label">WIN RATE</div><div class="stat-value" style="color:#60a5fa">{wr}</div><div style="font-size:10px;color:#334155;margin-top:3px">{wins}W / {losses}L</div></div>
      <div class="stat-card"><div class="stat-label">OPEN POSITIONS</div><div class="stat-value" style="color:#a78bfa">{len(pos)}</div></div>
    </div>"""

    coins_html = '<div class="coins">'
    for s in SYMBOLS:
        sym = s["symbol"]
        p   = pos.get(sym)
        is_open = p is not None
        price_str = "—"
        pos_html = ""
        if p:
            ep = p.get("entry",0)
            qty = p.get("qty",0)
            sl = ep*(1-0.02)
            tp = ep*(1+0.035)
            pos_html = f"""<div class="pos-box">
              <div class="pos-row"><span style="color:#334155">Entry</span><span style="color:#e2e8f0">${ep:.2f}</span></div>
              <div class="pos-row"><span style="color:#ef4444">Stop Loss</span><span style="color:#ef4444">${sl:.2f}</span></div>
              <div class="pos-row"><span style="color:#22c55e">Take Profit</span><span style="color:#22c55e">${tp:.2f}</span></div>
            </div>"""
        coins_html += f"""<div class="coin-card {'open' if is_open else ''}">
          <div class="coin-name">
            <span><span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:{s['color']};margin-right:6px"></span>{s['pair']}</span>
            {'<span class="open-badge">OPEN</span>' if is_open else ''}
          </div>
          {pos_html}
        </div>"""
    coins_html += "</div>"

    rows = ""
    for t in reversed(trades[-20:]):
        pnl_td = f"+${t['pnl']:.2f}" if t.get('pnl') is not None and t['pnl']>=0 else (f"${t['pnl']:.2f}" if t.get('pnl') is not None else "—")
        pnl_color_td = "#22c55e" if t.get('pnl') is not None and t['pnl']>=0 else ("#ef4444" if t.get('pnl') is not None else "#334155")
        rows += f"""<tr>
          <td style="color:#334155">{t.get('time','')[:19]}</td>
          <td style="color:#f8fafc;font-weight:700">{t.get('symbol','')}</td>
          <td><span class="{'buy' if t['side']=='BUY' else 'sell'}">{t['side']}</span></td>
          <td style="color:#e2e8f0">${t.get('price',0):.2f}</td>
          <td style="color:{pnl_color_td}">{pnl_td}</td>
          <td style="color:#334155">{t.get('reason','')}</td>
        </tr>"""

    trades_html = f"""<div class="trades-section">
      <div class="trades-header">ORDER HISTORY ({len(trades)})</div>
      <div style="overflow-x:auto"><table>
        <thead><tr><th>TIME</th><th>PAIR</th><th>SIDE</th><th>PRICE</th><th>P&L</th><th>REASON</th></tr></thead>
        <tbody>{''.join([rows]) if rows else '<tr><td colspan="6" style="text-align:center;color:#1e2d45;padding:20px">No trades yet</td></tr>'}</tbody>
      </table></div>
    </div>"""

    footer = f'<div class="footer"><span>📐 RSI(14) • SL 2% • TP 3.5% • 15s scan</span><span style="color:#f0b90b">Updated: {updated[:19]}</span></div>'

    return HTML.replace("__CONTENT__", stats_html + coins_html + trades_html + footer)

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
