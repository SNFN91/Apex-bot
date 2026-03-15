import json, os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

STATE_FILE = "/tmp/state.json"

# Global active strategy (shared with bot via file)
active_strategy = "BOTH"

HTML = """<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta http-equiv="refresh" content="30"/>
<title>APEX BOT PRO</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@300;400;500&family=Clash+Display:wght@600;700&display=swap" rel="stylesheet"/>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#070b12;--surface:#0d1421;--border:#1a2535;
  --gold:#f0b90b;--green:#22c55e;--red:#ef4444;
  --text:#e2e8f0;--muted:#4b5563;--dim:#1e2d45;
}
body{background:var(--bg);color:var(--text);font-family:'DM Mono',monospace;min-height:100vh}
::-webkit-scrollbar{width:3px}::-webkit-scrollbar-thumb{background:var(--dim)}

/* HEADER */
.header{background:rgba(13,20,33,0.95);backdrop-filter:blur(10px);border-bottom:1px solid var(--border);padding:12px 24px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}
.brand{display:flex;align-items:center;gap:12px}
.brand-icon{width:36px;height:36px;border-radius:10px;background:linear-gradient(135deg,var(--gold),#e67e00);display:flex;align-items:center;justify-content:center;font-size:18px;box-shadow:0 0 20px rgba(240,185,11,0.3)}
.brand-name{font-family:'Clash Display',sans-serif;font-size:18px;color:#fff;letter-spacing:.02em}
.brand-sub{font-size:9px;color:var(--muted);letter-spacing:.15em;margin-top:1px}
.header-right{display:flex;align-items:center;gap:16px}
.live-dot{width:7px;height:7px;border-radius:50%;background:var(--green);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1;box-shadow:0 0 0 0 rgba(34,197,94,0.4)}50%{opacity:.7;box-shadow:0 0 0 6px rgba(34,197,94,0)}}
.live-text{font-size:11px;color:var(--green);letter-spacing:.1em}
.balance-chip{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:6px 14px;font-size:13px}
.balance-val{color:var(--gold);font-weight:500}

/* STRATEGY SWITCHER */
.switcher{display:flex;gap:10px;padding:20px 24px;max-width:1200px;margin:0 auto}
.strat-btn{flex:1;padding:14px 20px;border-radius:12px;border:2px solid var(--border);background:var(--surface);cursor:pointer;transition:all .2s;text-align:center;font-family:'DM Mono',monospace}
.strat-btn:hover{transform:translateY(-2px)}
.strat-btn.active-scalp{border-color:var(--gold);background:rgba(240,185,11,0.08);box-shadow:0 0 20px rgba(240,185,11,0.15)}
.strat-btn.active-trend{border-color:var(--green);background:rgba(34,197,94,0.08);box-shadow:0 0 20px rgba(34,197,94,0.15)}
.strat-btn.active-both{border-color:#a78bfa;background:rgba(167,139,250,0.08);box-shadow:0 0 20px rgba(167,139,250,0.15)}
.btn-icon{font-size:22px;margin-bottom:6px}
.btn-label{font-size:13px;font-weight:500;letter-spacing:.05em}
.btn-desc{font-size:10px;color:var(--muted);margin-top:3px}
.btn-active-badge{display:inline-block;margin-top:6px;font-size:9px;padding:2px 8px;border-radius:4px;letter-spacing:.1em}

/* BODY */
.body{padding:0 24px 24px;max-width:1200px;margin:0 auto}

/* STATS ROW */
.stats-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:16px}
@media(min-width:600px){.stats-grid{grid-template-columns:repeat(4,1fr)}}
.stat-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px}
.stat-label{font-size:9px;color:var(--muted);letter-spacing:.15em;margin-bottom:6px}
.stat-val{font-family:'Clash Display',sans-serif;font-size:22px;font-weight:700}
.stat-sub{font-size:10px;color:var(--muted);margin-top:4px}

/* DUAL PANEL */
.dual-panel{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
@media(max-width:768px){.dual-panel{grid-template-columns:1fr}}
.strategy-panel{background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden}
.panel-header{padding:14px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between}
.panel-title{font-family:'Clash Display',sans-serif;font-size:14px;letter-spacing:.05em}
.panel-badge{font-size:10px;padding:3px 10px;border-radius:6px;letter-spacing:.08em}
.scalp-badge{background:rgba(240,185,11,.15);color:var(--gold)}
.trend-badge{background:rgba(34,197,94,.15);color:var(--green)}
.panel-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--border)}
.panel-stat{background:var(--surface);padding:12px;text-align:center}
.panel-stat-label{font-size:9px;color:var(--muted);letter-spacing:.1em;margin-bottom:4px}
.panel-stat-val{font-size:15px;font-weight:600}
.positions-list{padding:12px 16px}
.position-item{background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:8px;padding:10px 12px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center}
.pos-symbol{font-weight:500;font-size:13px}
.pos-entry{font-size:10px;color:var(--muted);margin-top:2px}
.pos-pnl{font-size:13px;font-weight:600}
.no-positions{padding:20px;text-align:center;color:var(--dim);font-size:12px}
.coin-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:12px 16px}
.coin-card{background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:8px;padding:10px}
.coin-name{font-size:11px;color:var(--muted);margin-bottom:4px;display:flex;align-items:center;gap:5px}
.coin-dot{width:6px;height:6px;border-radius:50%}
.coin-price{font-size:15px;font-weight:500}

/* TRADES TABLE */
.trades-section{background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden;margin-bottom:16px}
.trades-header{padding:14px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between}
.trades-title{font-family:'Clash Display',sans-serif;font-size:14px}
.trades-filter{display:flex;gap:6px}
.filter-btn{padding:4px 10px;border-radius:6px;font-size:10px;cursor:pointer;border:1px solid var(--border);background:transparent;color:var(--muted);font-family:'DM Mono',monospace;letter-spacing:.06em;transition:all .15s}
.filter-btn.active{background:var(--dim);color:var(--text)}
table{width:100%;border-collapse:collapse;font-size:11px}
th{padding:8px 14px;text-align:left;color:var(--muted);font-size:10px;background:rgba(0,0,0,0.2);letter-spacing:.08em}
td{padding:9px 14px;border-bottom:1px solid rgba(255,255,255,0.03)}
.buy-badge{color:var(--green);background:rgba(34,197,94,.1);padding:2px 7px;border-radius:3px;font-size:10px;font-weight:500}
.sell-badge{color:var(--red);background:rgba(239,68,68,.1);padding:2px 7px;border-radius:3px;font-size:10px;font-weight:500}
.scalp-tag{color:var(--gold);background:rgba(240,185,11,.1);padding:1px 6px;border-radius:3px;font-size:9px}
.trend-tag{color:var(--green);background:rgba(34,197,94,.1);padding:1px 6px;border-radius:3px;font-size:9px}

/* FOOTER */
.footer{padding:10px 0;display:flex;justify-content:space-between;font-size:10px;color:var(--dim);flex-wrap:wrap;gap:6px}
.refresh-note{text-align:center;font-size:9px;color:var(--dim);padding-bottom:8px}

/* COIN COLORS */
.btc{background:#f7931a}.eth{background:#627eea}.sol{background:#9945ff}.xrp{background:#346aa9}
</style>
</head>
<body>

<div class="header">
  <div class="brand">
    <div class="brand-icon">⚡</div>
    <div>
      <div class="brand-name">APEX BOT PRO</div>
      <div class="brand-sub">DUAL STRATEGY • PAPER TRADING</div>
    </div>
  </div>
  <div class="header-right">
    <div style="display:flex;align-items:center;gap:6px">
      <div class="live-dot"></div>
      <span class="live-text">LIVE</span>
    </div>
    <div class="balance-chip">Balance: <span class="balance-val">__BALANCE__</span></div>
  </div>
</div>

<!-- STRATEGY SWITCHER -->
<div class="switcher">
  <a href="/set_strategy?mode=SCALP" style="text-decoration:none;flex:1">
    <div class="strat-btn __SCALP_ACTIVE__">
      <div class="btn-icon">⚡</div>
      <div class="btn-label" style="color:#f0b90b">SCALPING</div>
      <div class="btn-desc">1min RSI • $100/trade • TP 2%</div>
      __SCALP_BADGE__
    </div>
  </a>
  <a href="/set_strategy?mode=BOTH" style="text-decoration:none;flex:1">
    <div class="strat-btn __BOTH_ACTIVE__">
      <div class="btn-icon">🔄</div>
      <div class="btn-label" style="color:#a78bfa">BOTH</div>
      <div class="btn-desc">Run all strategies simultaneously</div>
      __BOTH_BADGE__
    </div>
  </a>
  <a href="/set_strategy?mode=TREND" style="text-decoration:none;flex:1">
    <div class="strat-btn __TREND_ACTIVE__">
      <div class="btn-icon">📈</div>
      <div class="btn-label" style="color:#22c55e">DAILY TREND</div>
      <div class="btn-desc">4h RSI • $200/trade • TP 5%</div>
      __TREND_BADGE__
    </div>
  </a>
</div>

<div class="body">

  <!-- STATS -->
  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-label">PAPER BALANCE</div>
      <div class="stat-val" style="color:var(--gold)">__BALANCE__</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">TOTAL P&L</div>
      <div class="stat-val" style="color:__TOTAL_PNL_COLOR__">__TOTAL_PNL__</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">OPEN POSITIONS</div>
      <div class="stat-val" style="color:#a78bfa">__OPEN_POS__</div>
      <div class="stat-sub">__SCALP_POS__ scalp · __TREND_POS__ trend</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">TOTAL TRADES</div>
      <div class="stat-val" style="color:#60a5fa">__TOTAL_TRADES__</div>
      <div class="stat-sub">__TOTAL_WINS__W / __TOTAL_LOSSES__L</div>
    </div>
  </div>

  <!-- DUAL STRATEGY PANELS -->
  <div class="dual-panel">

    <!-- SCALP PANEL -->
    <div class="strategy-panel">
      <div class="panel-header">
        <div class="panel-title" style="color:var(--gold)">⚡ SCALPING</div>
        <span class="panel-badge scalp-badge">RSI 1min • TP 2% • SL 1%</span>
      </div>
      <div class="panel-stats">
        <div class="panel-stat">
          <div class="panel-stat-label">P&L</div>
          <div class="panel-stat-val" style="color:__SCALP_PNL_COLOR__">__SCALP_PNL__</div>
        </div>
        <div class="panel-stat">
          <div class="panel-stat-label">WIN RATE</div>
          <div class="panel-stat-val" style="color:#60a5fa">__SCALP_WR__</div>
        </div>
        <div class="panel-stat">
          <div class="panel-stat-label">TRADES</div>
          <div class="panel-stat-val">__SCALP_TRADES__</div>
        </div>
      </div>
      <div class="positions-list">
        __SCALP_POSITIONS__
      </div>
    </div>

    <!-- TREND PANEL -->
    <div class="strategy-panel">
      <div class="panel-header">
        <div class="panel-title" style="color:var(--green)">📈 DAILY TREND</div>
        <span class="panel-badge trend-badge">RSI 4h • TP 5% • SL 2.5%</span>
      </div>
      <div class="panel-stats">
        <div class="panel-stat">
          <div class="panel-stat-label">P&L</div>
          <div class="panel-stat-val" style="color:__TREND_PNL_COLOR__">__TREND_PNL__</div>
        </div>
        <div class="panel-stat">
          <div class="panel-stat-label">WIN RATE</div>
          <div class="panel-stat-val" style="color:#60a5fa">__TREND_WR__</div>
        </div>
        <div class="panel-stat">
          <div class="panel-stat-label">TRADES</div>
          <div class="panel-stat-val">__TREND_TRADES__</div>
        </div>
      </div>
      <div class="positions-list">
        __TREND_POSITIONS__
      </div>
    </div>

  </div>

  <!-- LIVE PRICES -->
  <div class="strategy-panel" style="margin-bottom:16px">
    <div class="panel-header">
      <div class="panel-title">📊 LIVE PRICES</div>
      <span style="font-size:10px;color:var(--muted)">Updated: __UPDATED__</span>
    </div>
    <div class="coin-grid">__COIN_CARDS__</div>
  </div>

  <!-- TRADES TABLE -->
  <div class="trades-section">
    <div class="trades-header">
      <div class="trades-title">ORDER HISTORY (__TRADE_COUNT__)</div>
    </div>
    <div style="overflow-x:auto;max-height:400px;overflow-y:auto">
      <table>
        <thead><tr>
          <th>TIME</th><th>STRATEGY</th><th>PAIR</th><th>SIDE</th><th>PRICE</th><th>P&L</th><th>REASON</th>
        </tr></thead>
        <tbody>__TRADE_ROWS__</tbody>
      </table>
    </div>
  </div>

  <div class="footer">
    <span>⚡ Scalp: RSI(1m) Buy&lt;45 Sell&gt;60 TP2% SL1% $100</span>
    <span>📈 Trend: RSI(4h) Buy&lt;40 Sell&gt;65 TP5% SL2.5% $200</span>
    <span style="color:var(--gold)">Active: __ACTIVE_STRATEGY__</span>
  </div>
  <div class="refresh-note">⟳ Auto-refreshes every 30 seconds</div>
</div>

</body></html>"""

COIN_COLORS = {"BTC": "btc", "ETH": "eth", "SOL": "sol", "XRP": "xrp"}
COIN_PRICES_BASE = {"BTC": 71000, "ETH": 2100, "SOL": 88, "XRP": 1.4}

def pnl_color(v): return "#22c55e" if v >= 0 else "#ef4444"
def win_rate(w, l): return f"{w/(w+l)*100:.0f}%" if (w+l) > 0 else "—"

def render_positions(positions, prices, cfg):
    if not positions:
        return '<div class="no-positions">⏳ No open positions — waiting for signal</div>'
    html = ""
    for sym, pos in positions.items():
        price = prices.get(sym, pos["entry"])
        pct = (price - pos["entry"]) / pos["entry"] * 100
        color = pnl_color(pct)
        html += f'''<div class="position-item">
          <div>
            <div class="pos-symbol">
              <span class="coin-dot {COIN_COLORS.get(sym,'btc')}" style="display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px"></span>
              {sym}/USD
            </div>
            <div class="pos-entry">Entry: ${pos["entry"]:,.2f} | TP: ${pos["entry"]*(1+cfg["tp"]):,.2f} | SL: ${pos["entry"]*(1-cfg["sl"]):,.2f}</div>
          </div>
          <div class="pos-pnl" style="color:{color}">{pct:+.2f}%</div>
        </div>'''
    return html

def render(state, active_strat):
    bal = state.get("balance", 10000)
    scalp_stats = state.get("scalp_stats", {"pnl":0,"wins":0,"losses":0})
    trend_stats = state.get("trend_stats", {"pnl":0,"wins":0,"losses":0})
    scalp_pos = state.get("scalp_positions", {})
    trend_pos = state.get("trend_positions", {})
    all_trades = state.get("all_trades", [])
    prices = state.get("prices", {})
    updated = state.get("updated", "—")[:19].replace("T"," ")

    total_pnl = scalp_stats["pnl"] + trend_stats["pnl"]
    total_wins = scalp_stats["wins"] + trend_stats["wins"]
    total_losses = scalp_stats["losses"] + trend_stats["losses"]
    total_trades = len(state.get("scalp_trades",[])) + len(state.get("trend_trades",[]))

    # Strategy button states
    scalp_active = "active-scalp" if active_strat == "SCALP" else ""
    trend_active = "active-trend" if active_strat == "TREND" else ""
    both_active  = "active-both"  if active_strat == "BOTH"  else ""

    scalp_badge = '<div class="btn-active-badge" style="background:rgba(240,185,11,.2);color:#f0b90b">● ACTIVE</div>' if active_strat in ("SCALP","BOTH") else ""
    trend_badge = '<div class="btn-active-badge" style="background:rgba(34,197,94,.2);color:#22c55e">● ACTIVE</div>' if active_strat in ("TREND","BOTH") else ""
    both_badge  = '<div class="btn-active-badge" style="background:rgba(167,139,250,.2);color:#a78bfa">● ACTIVE</div>' if active_strat == "BOTH" else ""

    # Coin cards
    coin_cards = ""
    for sym, cls in COIN_COLORS.items():
        price = prices.get(sym, 0)
        in_scalp = sym in scalp_pos
        in_trend = sym in trend_pos
        tags = ""
        if in_scalp: tags += '<span class="scalp-tag">⚡SCALP</span> '
        if in_trend: tags += '<span class="trend-tag">📈TREND</span>'
        coin_cards += f'''<div class="coin-card">
          <div class="coin-name">
            <span class="coin-dot {cls}" style="display:inline-block;width:6px;height:6px;border-radius:50%"></span>
            {sym}/USD {tags}
          </div>
          <div class="coin-price">${price:,.2f}</div>
        </div>'''

    # Trade rows
    rows = ""
    for t in all_trades[:30]:
        pnl_v = t.get("pnl")
        pnl_str = f'+${pnl_v:.2f}' if pnl_v is not None and pnl_v >= 0 else (f'${pnl_v:.2f}' if pnl_v is not None else '—')
        pnl_c = pnl_color(pnl_v) if pnl_v is not None else "#4b5563"
        strat = t.get("strategy","SCALP")
        stag = f'<span class="scalp-tag">⚡</span>' if strat=="SCALP" else f'<span class="trend-tag">📈</span>'
        side_cls = "buy-badge" if t["side"]=="BUY" else "sell-badge"
        time_str = t.get("time","")[:19].replace("T"," ")
        rows += f'''<tr>
          <td style="color:var(--muted)">{time_str}</td>
          <td>{stag}</td>
          <td style="font-weight:500">{t.get("symbol","")}</td>
          <td><span class="{side_cls}">{t["side"]}</span></td>
          <td>${t.get("price",0):,.2f}</td>
          <td style="color:{pnl_c}">{pnl_str}</td>
          <td style="color:var(--muted);font-size:10px">{t.get("reason","")}</td>
        </tr>'''

    if not rows:
        rows = '<tr><td colspan="7" style="text-align:center;color:var(--dim);padding:24px">No trades yet — bot is scanning markets</td></tr>'

    from importlib import import_module
    cfg_scalp = {"tp":0.02,"sl":0.01}
    cfg_trend = {"tp":0.05,"sl":0.025}

    html = HTML
    html = html.replace("__BALANCE__", f"${bal:,.2f}")
    html = html.replace("__TOTAL_PNL__", f"{'+' if total_pnl>=0 else ''}${total_pnl:.2f}")
    html = html.replace("__TOTAL_PNL_COLOR__", pnl_color(total_pnl))
    html = html.replace("__OPEN_POS__", str(len(scalp_pos)+len(trend_pos)))
    html = html.replace("__SCALP_POS__", str(len(scalp_pos)))
    html = html.replace("__TREND_POS__", str(len(trend_pos)))
    html = html.replace("__TOTAL_TRADES__", str(total_trades))
    html = html.replace("__TOTAL_WINS__", str(total_wins))
    html = html.replace("__TOTAL_LOSSES__", str(total_losses))
    html = html.replace("__SCALP_PNL__", f"{'+' if scalp_stats['pnl']>=0 else ''}${scalp_stats['pnl']:.2f}")
    html = html.replace("__SCALP_PNL_COLOR__", pnl_color(scalp_stats['pnl']))
    html = html.replace("__SCALP_WR__", win_rate(scalp_stats['wins'], scalp_stats['losses']))
    html = html.replace("__SCALP_TRADES__", str(scalp_stats['wins']+scalp_stats['losses']))
    html = html.replace("__TREND_PNL__", f"{'+' if trend_stats['pnl']>=0 else ''}${trend_stats['pnl']:.2f}")
    html = html.replace("__TREND_PNL_COLOR__", pnl_color(trend_stats['pnl']))
    html = html.replace("__TREND_WR__", win_rate(trend_stats['wins'], trend_stats['losses']))
    html = html.replace("__TREND_TRADES__", str(trend_stats['wins']+trend_stats['losses']))
    html = html.replace("__SCALP_POSITIONS__", render_positions(scalp_pos, prices, cfg_scalp))
    html = html.replace("__TREND_POSITIONS__", render_positions(trend_pos, prices, cfg_trend))
    html = html.replace("__COIN_CARDS__", coin_cards)
    html = html.replace("__TRADE_COUNT__", str(len(all_trades)))
    html = html.replace("__TRADE_ROWS__", rows)
    html = html.replace("__UPDATED__", updated)
    html = html.replace("__ACTIVE_STRATEGY__", active_strat)
    html = html.replace("__SCALP_ACTIVE__", scalp_active)
    html = html.replace("__TREND_ACTIVE__", trend_active)
    html = html.replace("__BOTH_ACTIVE__", both_active)
    html = html.replace("__SCALP_BADGE__", scalp_badge)
    html = html.replace("__TREND_BADGE__", trend_badge)
    html = html.replace("__BOTH_BADGE__", both_badge)
    return html

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        global active_strategy
        parsed = urlparse(self.path)

        # Handle strategy switching
        if parsed.path == "/set_strategy":
            params = parse_qs(parsed.query)
            mode = params.get("mode", ["BOTH"])[0].upper()
            if mode in ("SCALP", "TREND", "BOTH"):
                active_strategy = mode
                # Write to file so bot picks it up
                with open("/tmp/active_strategy.txt", "w") as f:
                    f.write(mode)
            # Redirect back to dashboard
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return

        # Serve dashboard
        try:
            # Read active strategy from file (set by dashboard or bot)
            if os.path.exists("/tmp/active_strategy.txt"):
                with open("/tmp/active_strategy.txt") as f:
                    active_strategy = f.read().strip()

            state = {}
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE) as f:
                    state = json.load(f)

            body = render(state, active_strategy).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
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
