import json, os, glob
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime

STATE_FILE = "/data/state.json"  # Use persistent storage
active_strategy = "SCALP"  # Default view
TRADING_MODE = os.environ.get("TRADING_MODE", "paper")  # Read mode

# HTML Template with Manual Close Button and Reset Scalp Stats Button
HTML = """<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta http-equiv="refresh" content="30"/>
<title>APEX BOT • FINAL</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@300;400;500&family=Clash+Display:wght@600;700&display=swap" rel="stylesheet"/>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#070b12;--surface:#0d1421;--border:#1a2535;--gold:#f0b90b;--green:#22c55e;--red:#ef4444;--blue:#3b82f6;--text:#e2e8f0;--muted:#4b5563;--dim:#1e2d45;}
body{background:var(--bg);color:var(--text);font-family:'DM Mono',monospace;min-height:100vh}
::-webkit-scrollbar{width:3px}::-webkit-scrollbar-thumb{background:var(--dim)}

.header{background:rgba(13,20,33,0.95);backdrop-filter:blur(10px);border-bottom:1px solid var(--border);padding:12px 24px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}
.brand{display:flex;align-items:center;gap:12px}
.brand-icon{width:36px;height:36px;border-radius:10px;background:linear-gradient(135deg,var(--gold),#e67e00);display:flex;align-items:center;justify-content:center;font-size:18px;box-shadow:0 0 20px rgba(240,185,11,0.3)}
.brand-name{font-family:'Clash Display',sans-serif;font-size:18px;color:#fff;letter-spacing:.02em}
.brand-sub{font-size:9px;color:var(--muted);letter-spacing:.15em;margin-top:1px}
.header-right{display:flex;align-items:center;gap:16px}
.live-dot{width:7px;height:7px;border-radius:50%;background:var(--green);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1;box-shadow:0 0 0 0 rgba(34,197,94,0.4)}50%{opacity:.7;box-shadow:0 0 0 6px rgba(34,197,94,0)}}
.live-text{font-size:11px;color:var(--green);letter-spacing:.1em}
.total-balance{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:6px 14px;font-size:13px}
.total-balance span{color:#a78bfa;font-weight:500}

.switcher{display:flex;gap:10px;padding:20px 24px;max-width:1200px;margin:0 auto}
.strat-btn{flex:1;padding:14px 20px;border-radius:12px;border:2px solid var(--border);background:var(--surface);cursor:pointer;transition:all .2s;text-align:center;font-family:'DM Mono',monospace;text-decoration:none;display:block}
.strat-btn:hover{transform:translateY(-2px)}
.strat-btn.active-scalp{border-color:var(--gold);background:rgba(240,185,11,0.08);box-shadow:0 0 20px rgba(240,185,11,0.15)}
.strat-btn.active-trend{border-color:var(--green);background:rgba(34,197,94,0.08);box-shadow:0 0 20px rgba(34,197,94,0.15)}
.btn-icon{font-size:22px;margin-bottom:6px}
.btn-label{font-size:13px;font-weight:500;letter-spacing:.05em}
.btn-desc{font-size:10px;color:var(--muted);margin-top:3px}
.btn-active-badge{display:inline-block;margin-top:6px;font-size:9px;padding:2px 8px;border-radius:4px;letter-spacing:.1em}

/* Button Container */
.button-container{display:flex;gap:10px;margin:10px 24px;justify-content:center}
.close-btn{background:#ef4444;color:white;border:none;padding:12px 24px;border-radius:8px;font-size:14px;cursor:pointer;font-family:'DM Mono',monospace;font-weight:bold;transition:all .2s;border:1px solid #ef4444;flex:1;max-width:250px}
.close-btn:hover{background:#dc2626;transform:translateY(-2px);box-shadow:0 0 15px rgba(239,68,68,0.3)}
.reset-stats-btn{background:#3b82f6;color:white;border:none;padding:12px 24px;border-radius:8px;font-size:14px;cursor:pointer;font-family:'DM Mono',monospace;font-weight:bold;transition:all .2s;border:1px solid #3b82f6;flex:1;max-width:250px}
.reset-stats-btn:hover{background:#2563eb;transform:translateY(-2px);box-shadow:0 0 15px rgba(59,130,246,0.3)}

.body{padding:0 24px 24px;max-width:1200px;margin:0 auto}

.strategy-header{display:flex;align-items:center;gap:12px;margin-bottom:20px;padding:12px 16px;background:var(--surface);border:1px solid var(--border);border-radius:12px}
.strategy-icon{font-size:28px}
.strategy-title{font-family:'Clash Display',sans-serif;font-size:20px}
.strategy-desc{font-size:11px;color:var(--muted);margin-top:4px}

.stats-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px}
.stat-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px}
.stat-label{font-size:9px;color:var(--muted);letter-spacing:.15em;margin-bottom:6px}
.stat-val{font-family:'Clash Display',sans-serif;font-size:24px;font-weight:700}
.stat-sub{font-size:10px;color:var(--muted);margin-top:4px}

.positions-section{background:var(--surface);border:1px solid var(--border);border-radius:12px;margin-bottom:24px;overflow:hidden}
.section-header{padding:14px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between}
.section-title{font-family:'Clash Display',sans-serif;font-size:14px;letter-spacing:.05em}
.position-item{background:rgba(255,255,255,0.02);border-bottom:1px solid var(--border);padding:12px 16px;display:flex;justify-content:space-between;align-items:center}
.position-item:last-child{border-bottom:none}
.pos-symbol{font-weight:500;font-size:14px}
.pos-entry{font-size:11px;color:var(--muted);margin-top:2px}
.pos-pnl{font-size:14px;font-weight:600}
.no-positions{padding:20px;text-align:center;color:var(--dim);font-size:12px}

.coin-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;padding:12px 16px}
.coin-card{background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:8px;padding:10px}
.coin-name{font-size:11px;color:var(--muted);margin-bottom:4px;display:flex;align-items:center;gap:5px}
.coin-dot{width:6px;height:6px;border-radius:50%}
.coin-price{font-size:15px;font-weight:500}
.strategy-tag{font-size:8px;padding:2px 5px;border-radius:3px;margin-left:5px}
.tag-scalp{background:rgba(240,185,11,.15);color:var(--gold)}
.tag-daily{background:rgba(34,197,94,.15);color:var(--green)}

.trades-section{background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden;margin-bottom:16px}
table{width:100%;border-collapse:collapse;font-size:11px}
th{padding:8px 14px;text-align:left;color:var(--muted);font-size:10px;background:rgba(0,0,0,0.2);letter-spacing:.08em}
td{padding:9px 14px;border-bottom:1px solid rgba(255,255,255,0.03)}
.buy-badge{color:var(--green);background:rgba(34,197,94,.1);padding:2px 7px;border-radius:3px;font-size:10px;font-weight:500}
.sell-badge{color:var(--red);background:rgba(239,68,68,.1);padding:2px 7px;border-radius:3px;font-size:10px;font-weight:500}

.footer{padding:16px 0 8px;display:flex;justify-content:space-between;font-size:10px;color:var(--dim);flex-wrap:wrap;gap:6px;border-top:1px solid var(--border);margin-top:16px}
.refresh-note{text-align:center;font-size:9px;color:var(--dim);padding-bottom:8px}

.btc{background:#f7931a}.eth{background:#627eea}.sol{background:#9945ff}.xrp{background:#346aa9}
</style>
</head>
<body>

<div class="header">
  <div class="brand">
    <div class="brand-icon">⚡📈</div>
    <div>
      <div class="brand-name">APEX BOT FINAL</div>
      <div class="brand-sub">DUAL STRATEGY • PAPER TRADING</div>
    </div>
  </div>
  <div class="header-right">
    <div style="display:flex;align-items:center;gap:6px">
      <div class="live-dot"></div>
      <span class="live-text">LIVE</span>
    </div>
    <div class="total-balance">Total: <span>__TOTAL_BALANCE__</span></div>
  </div>
</div>

<!-- STRATEGY SWITCHER -->
<div class="switcher">
  <a href="/set_strategy?mode=SCALP" class="strat-btn __SCALP_ACTIVE__">
    <div class="btn-icon">⚡</div>
    <div class="btn-label" style="color:#f0b90b">SCALPING</div>
    <div class="btn-desc">1min RSI • $50 • TP1% SL0.3% • Time exit 1h • $50/trade</div>
    __SCALP_BADGE__
  </a>
  <a href="/set_strategy?mode=TREND" class="strat-btn __TREND_ACTIVE__">
    <div class="btn-icon">📈</div>
    <div class="btn-label" style="color:#22c55e">DAILY TREND</div>
    <div class="btn-desc">4h RSI • $200 • TP5% SL4% • $200/trade</div>
    __TREND_BADGE__
  </a>
</div>

<!-- BUTTON CONTAINER WITH TWO BUTTONS -->
<div class="button-container">
  <button onclick="closeAllPositions()" class="close-btn">
    🛑 CLOSE ALL POSITIONS
  </button>
  <button onclick="resetScalpStats()" class="reset-stats-btn">
    🔄 RESET SCALP STATS
  </button>
</div>

<div class="body">
  __STRATEGY_HEADER__
  
  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-label">__STRATEGY_NAME__ BALANCE</div>
      <div class="stat-val" style="color:__STRATEGY_COLOR__">__BALANCE__</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">TOTAL P&L</div>
      <div class="stat-val" style="color:__PNL_COLOR__">__PNL__</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">OPEN POSITIONS</div>
      <div class="stat-val" style="color:#a78bfa">__OPEN_POSITIONS__</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">WIN RATE</div>
      <div class="stat-val" style="color:#60a5fa">__WIN_RATE__</div>
      <div class="stat-sub">__WINS__W / __LOSSES__L</div>
    </div>
  </div>

  <div class="positions-section">
    <div class="section-header">
      <div class="section-title">OPEN POSITIONS (__OPEN_POSITIONS__)</div>
      <span style="font-size:10px;color:var(--muted)">TP: __TP__% • SL: __SL__%</span>
    </div>
    <div class="positions-list">
      __POSITIONS_LIST__
    </div>
  </div>

  <div class="positions-section">
    <div class="section-header">
      <div class="section-title">📊 LIVE PRICES</div>
      <span style="font-size:10px;color:var(--muted)">Updated: __UPDATED__</span>
    </div>
    <div class="coin-grid">
      __COIN_CARDS__
    </div>
  </div>

  <div class="trades-section">
    <div class="section-header">
      <div class="section-title">📋 ORDER HISTORY (__TRADE_COUNT__)</div>
    </div>
    <div style="overflow-x:auto;max-height:400px;overflow-y:auto">
      <table>
        <thead><tr><th>TIME</th><th>PAIR</th><th>SIDE</th><th>PRICE</th><th>P&L</th><th>REASON</th></tr></thead>
        <tbody>__TRADE_ROWS__</tbody>
      </table>
    </div>
  </div>

  <div class="footer">
    <span>⚡ Scalp: RSI(1m) Buy<45 Sell>55 TP1% SL0.3% $50 | Time exit 1h</span>
    <span>📈 Trend: RSI(4h) Buy<45 Sell>75 TP5% SL4% $200</span>
    <span style="color:__ACTIVE_COLOR__">Active: __ACTIVE_STRATEGY__</span>
  </div>
  <div class="refresh-note">⟳ Auto-refreshes every 30 seconds</div>
</div>

<script>
function closeAllPositions() {
  if (!confirm('⚠️ This will close ALL open positions. Continue?')) return;
  
  const btn = document.querySelector('.close-btn');
  const originalText = btn.innerText;
  btn.innerText = '⏳ Closing...';
  btn.disabled = true;
  
  fetch('/close_all')
    .then(response => {
      if (response.ok) {
        alert('✅ Close signal sent. Positions will be closed within 30 seconds.');
        setTimeout(() => location.reload(), 2000);
      } else {
        response.text().then(text => {
          alert('❌ Error: ' + text);
          btn.innerText = originalText;
          btn.disabled = false;
        });
      }
    })
    .catch(err => {
      alert('❌ Fetch error: ' + err);
      btn.innerText = originalText;
      btn.disabled = false;
    });
}

function resetScalpStats() {
  if (!confirm('⚠️ This will reset scalp trading stats (wins/losses/P&L). Continue?')) return;
  
  const btn = document.querySelector('.reset-stats-btn');
  const originalText = btn.innerText;
  btn.innerText = '⏳ Resetting...';
  btn.disabled = true;
  
  fetch('/reset_scalp_stats')
    .then(response => {
      if (response.ok) {
        alert('✅ Reset scalp stats signal sent. Stats will reset within 30 seconds.');
        setTimeout(() => location.reload(), 2000);
      } else {
        response.text().then(text => {
          alert('❌ Error: ' + text);
          btn.innerText = originalText;
          btn.disabled = false;
        });
      }
    })
    .catch(err => {
      alert('❌ Fetch error: ' + err);
      btn.innerText = originalText;
      btn.disabled = false;
    });
}
</script>

</body></html>"""

COIN_COLORS = {"BTC": "btc", "ETH": "eth", "SOL": "sol", "XRP": "xrp"}

def pnl_color(v): return "#22c55e" if v >= 0 else "#ef4444"
def win_rate(w, l): return f"{w/(w+l)*100:.1f}%" if (w+l) > 0 else "—"

def render_positions(positions, prices, tp, sl):
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
            <div class="pos-entry">Entry: ${pos["entry"]:,.2f} | TP: ${pos["entry"]*(1+tp):,.2f} | SL: ${pos["entry"]*(1-sl):,.2f}</div>
          </div>
          <div class="pos-pnl" style="color:{color}">{pct:+.2f}%</div>
        </div>'''
    return html

def render(state, view_mode):
    scalp = state.get("scalp", {})
    trend = state.get("trend", {})
    prices = state.get("prices", {})
    total_balance = state.get("total_balance", 20000)
    updated = state.get("updated", "—")[:19].replace("T", " ")

    if view_mode == "SCALP":
        data = scalp
        strategy_name = "SCALPING"
        strategy_icon = "⚡"
        color = "#f0b90b"
        tp = 0.01
        sl = 0.003
        desc = "1min RSI • Buy <45 • Sell >55 • TP 1% • SL 0.3% • Time exit 1h • $50/trade"
        tag_text = "SCALP"
        tag_class = "tag-scalp"
    else:
        data = trend
        strategy_name = "DAILY TREND"
        strategy_icon = "📈"
        color = "#22c55e"
        tp = 0.05
        sl = 0.04
        desc = "4h RSI • Buy <45 • Sell >75 • TP 5% • SL 4% • $200/trade"
        tag_text = "DAILY"
        tag_class = "tag-daily"

    balance = data.get("balance", 10000)
    stats = data.get("stats", {"pnl": 0, "wins": 0, "losses": 0})
    positions = data.get("positions", {})
    trades = data.get("trades", [])

    pnl = stats["pnl"]
    wins = stats["wins"]
    losses = stats["losses"]
    total_trades = wins + losses

    strategy_header = f'''
    <div class="strategy-header" style="border-left:4px solid {color}">
        <div class="strategy-icon">{strategy_icon}</div>
        <div>
            <div class="strategy-title" style="color:{color}">{strategy_name}</div>
            <div class="strategy-desc">{desc}</div>
        </div>
    </div>
    '''

    coin_cards = ""
    for sym, cls in COIN_COLORS.items():
        price = prices.get(sym, 0)
        in_scalp = sym in scalp.get("positions", {})
        in_trend = sym in trend.get("positions", {})
        tags = []
        
        if view_mode == "SCALP" and in_scalp:
            tags.append(f'<span class="strategy-tag {tag_class}">{strategy_icon}{tag_text}</span>')
        elif view_mode == "TREND" and in_trend:
            tags.append(f'<span class="strategy-tag {tag_class}">{strategy_icon}{tag_text}</span>')
        
        tags_html = " ".join(tags)
        
        coin_cards += f'''<div class="coin-card">
          <div class="coin-name">
            <span class="coin-dot {cls}" style="display:inline-block;width:6px;height:6px;border-radius:50%"></span>
            {sym}/USD {tags_html}
          </div>
          <div class="coin-price">${price:,.2f}</div>
        </div>'''

    trade_rows = ""
    for t in trades[-30:]:
        pnl_v = t.get("pnl")
        pnl_str = f'+${pnl_v:.2f}' if pnl_v is not None and pnl_v >= 0 else (f'-${abs(pnl_v):.2f}' if pnl_v is not None and pnl_v < 0 else '—')
        pnl_c = pnl_color(pnl_v) if pnl_v is not None else "#4b5563"
        side_cls = "buy-badge" if t["side"] == "BUY" else "sell-badge"
        time_str = t.get("time", "")[:19].replace("T", " ")
        trade_rows += f'''<tr>
          <td style="color:var(--muted)">{time_str}</td>
          <td style="font-weight:500">{t.get("symbol","")}</td>
          <td><span class="{side_cls}">{t["side"]}</span></td>
          <td>${t.get("price",0):,.2f}</td>
          <td style="color:{pnl_c}">{pnl_str}</td>
          <td style="color:var(--muted);font-size:10px">{t.get("reason","")}</td>
        </tr>'''

    if not trade_rows:
        trade_rows = f'<tr><td colspan="6" style="text-align:center;color:var(--dim);padding:24px">No {strategy_name.lower()} trades yet — bot is scanning</td></tr>'

    scalp_active = "active-scalp" if view_mode == "SCALP" else ""
    trend_active = "active-trend" if view_mode == "TREND" else ""
    scalp_badge = '<div class="btn-active-badge" style="background:rgba(240,185,11,.2);color:#f0b90b">● ACTIVE</div>' if view_mode == "SCALP" else ""
    trend_badge = '<div class="btn-active-badge" style="background:rgba(34,197,94,.2);color:#22c55e">● ACTIVE</div>' if view_mode == "TREND" else ""

    html = HTML
    html = html.replace("__TOTAL_BALANCE__", f"${total_balance:,.2f}")
    html = html.replace("__STRATEGY_HEADER__", strategy_header)
    html = html.replace("__STRATEGY_NAME__", strategy_name)
    html = html.replace("__STRATEGY_COLOR__", color)
    html = html.replace("__BALANCE__", f"${balance:,.2f}")
    html = html.replace("__PNL__", f"{'+' if pnl >= 0 else ''}${pnl:.2f}")
    html = html.replace("__PNL_COLOR__", pnl_color(pnl))
    html = html.replace("__OPEN_POSITIONS__", str(len(positions)))
    html = html.replace("__WIN_RATE__", win_rate(wins, losses))
    html = html.replace("__WINS__", str(wins))
    html = html.replace("__LOSSES__", str(losses))
    html = html.replace("__TP__", str(int(tp*100)))
    html = html.replace("__SL__", str(int(sl*100) if sl >= 0.01 else str(int(sl*1000)/10)))  # Handle 0.3% display
    html = html.replace("__POSITIONS_LIST__", render_positions(positions, prices, tp, sl))
    html = html.replace("__COIN_CARDS__", coin_cards)
    html = html.replace("__TRADE_COUNT__", str(total_trades))
    html = html.replace("__TRADE_ROWS__", trade_rows)
    html = html.replace("__UPDATED__", updated)
    html = html.replace("__ACTIVE_STRATEGY__", view_mode)
    html = html.replace("__ACTIVE_COLOR__", color)
    html = html.replace("__SCALP_ACTIVE__", scalp_active)
    html = html.replace("__TREND_ACTIVE__", trend_active)
    html = html.replace("__SCALP_BADGE__", scalp_badge)
    html = html.replace("__TREND_BADGE__", trend_badge)
    return html

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    
    def do_GET(self):
        global active_strategy
        parsed = urlparse(self.path)

        # Strategy switching
        if parsed.path == "/set_strategy":
            params = parse_qs(parsed.query)
            mode = params.get("mode", ["SCALP"])[0].upper()
            if mode in ("SCALP", "TREND"):
                active_strategy = mode
                with open("/tmp/active_strategy.txt", "w") as f:
                    f.write(mode)
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return

        # Manual close endpoint
        if parsed.path == "/close_all":
            try:
                with open("/tmp/CLOSE_ALL", "w") as f:
                    f.write("1")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Close signal sent")
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode())
            return

        # NEW: Reset scalp stats endpoint
        if parsed.path == "/reset_scalp_stats":
            try:
                with open("/tmp/RESET_SCALP_STATS", "w") as f:
                    f.write("1")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Reset scalp stats signal sent")
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode())
            return

        # Serve dashboard
        try:
            if os.path.exists("/tmp/active_strategy.txt"):
                with open("/tmp/active_strategy.txt") as f:
                    active_strategy = f.read().strip()

            state = {}
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE) as f:
                    state = json.load(f)
            else:
                # Try backup locations
                backups = sorted(glob.glob("/data/state_*.json"))
                if backups:
                    with open(backups[-1]) as f:
                        state = json.load(f)
                    print(f"Restored from backup: {backups[-1]}")

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
