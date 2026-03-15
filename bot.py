import os, time, hmac, hashlib, requests, json, logging, base64, urllib.parse
from datetime import datetime, timezone
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger(__name__)

TRADING_MODE      = os.environ.get("TRADING_MODE", "paper")
KRAKEN_API_KEY    = os.environ.get("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = os.environ.get("KRAKEN_API_SECRET", "")
KRAKEN_URL        = "https://api.kraken.com"

# ═══ DUAL STRATEGY CONFIG ════════════════════════════════════════════════════
STRATEGIES = {
    "SCALP": {
        "rsi_interval": 1,        # 1-minute candles
        "rsi_buy": 45,            # buy when RSI < 45
        "rsi_sell": 60,           # sell when RSI > 60
        "tp": 0.02,               # 2% take profit
        "sl": 0.015,              # 1.5% stop loss — more breathing room
        "trade_size": 100,        # $100 per trade
        "label": "⚡ Scalping",
        "color": "#f0b90b"
    },
    "TREND": {
        "rsi_interval": 240,      # 4-hour candles for daily trend
        "rsi_buy": 40,            # buy when RSI < 40
        "rsi_sell": 65,           # sell when RSI > 65
        "tp": 0.05,               # 5% take profit
        "sl": 0.025,              # 2.5% stop loss
        "trade_size": 200,        # $200 per trade
        "label": "📈 Daily Trend",
        "color": "#22c55e"
    }
}

SYMBOLS = [
    {"symbol": "BTC", "kraken_ticker": "XXBTZUSD", "kraken_ohlc": "XBTUSD",  "kraken_order": "XXBTZUSD"},
    {"symbol": "ETH", "kraken_ticker": "XETHZUSD", "kraken_ohlc": "ETHUSD",  "kraken_order": "XETHZUSD"},
    {"symbol": "SOL", "kraken_ticker": "SOLUSD",   "kraken_ohlc": "SOLUSD",  "kraken_order": "SOLUSD"},
    {"symbol": "XRP", "kraken_ticker": "XXRPZUSD", "kraken_ohlc": "XRPUSD",  "kraken_order": "XXRPZUSD"},
]

# ═══ STATE ═══════════════════════════════════════════════════════════════════
scalp_positions  = {}
trend_positions  = {}
scalp_trades     = []
trend_trades     = []
scalp_stats      = {"pnl": 0.0, "wins": 0, "losses": 0}
trend_stats      = {"pnl": 0.0, "wins": 0, "losses": 0}
paper_balance    = 10000.0
price_cache      = {}
last_price_cache = {}
rsi_cache        = {}
last_rsi_cache   = {}
active_strategy  = "BOTH"  # SCALP, TREND, or BOTH

session = requests.Session()
retries = Retry(total=5, backoff_factor=2, status_forcelist=[502, 503, 504])
session.mount('https://', HTTPAdapter(max_retries=retries))

# ═══ PRICES ══════════════════════════════════════════════════════════════════
def fetch_all_prices():
    global price_cache, last_price_cache
    temp = {}
    pairs = ",".join(s["kraken_ticker"] for s in SYMBOLS)
    for attempt in range(3):
        try:
            r = session.get(f"{KRAKEN_URL}/0/public/Ticker?pair={pairs}", timeout=45)
            data = r.json()
            if data.get("error"): return False
            for s in SYMBOLS:
                key = s["kraken_ticker"]
                if key in data["result"]:
                    temp[s["symbol"]] = float(data["result"][key]["c"][0])
                    log.info(f"✅ {s['symbol']} = ${temp[s['symbol']]:,.2f}")
            price_cache = temp
            last_price_cache = temp.copy()
            return True
        except Exception as e:
            log.warning(f"Price fetch attempt {attempt+1} failed: {e}")
            if attempt < 2: time.sleep(2 ** attempt)
    if last_price_cache:
        log.warning("⚠️ Using cached prices")
        price_cache = last_price_cache.copy()
        return True
    return False

def get_rsi(kraken_ohlc, symbol, interval):
    cache_key = f"{symbol}_{interval}"
    if cache_key in rsi_cache:
        return rsi_cache[cache_key]
    try:
        r = session.get(f"{KRAKEN_URL}/0/public/OHLC?pair={kraken_ohlc}&interval={interval}", timeout=30)
        data = r.json()
        if data.get("error") and data["error"]:
            if cache_key in last_rsi_cache:
                log.warning(f"⚠️ Using cached RSI for {symbol}({interval}m)")
                return last_rsi_cache[cache_key]
            return None
        result = data["result"]
        key = [k for k in result.keys() if k != "last"][0]
        closes = [float(c[4]) for c in result[key][-30:]]
        rsi = calc_rsi(closes)
        if rsi is not None:
            rsi_cache[cache_key] = rsi
            last_rsi_cache[cache_key] = rsi
        return rsi
    except Exception as e:
        log.error(f"get_rsi error {kraken_ohlc}: {e}")
        if cache_key in last_rsi_cache:
            return last_rsi_cache[cache_key]
    return None

def calc_rsi(prices):
    if len(prices) < 15: return None  # need 14+1 prices
    recent = prices[-15:]  # last 15 = 14 periods
    gains = losses = 0
    for i in range(1, len(recent)):
        d = recent[i] - recent[i-1]
        if d > 0: gains += d
        else: losses += abs(d)
    ag, al = gains/14, losses/14  # RSI period = 14
    if al == 0: return 100
    return round(100 - 100/(1 + ag/al), 2)

# ═══ KRAKEN LIVE ══════════════════════════════════════════════════════════════
def kraken_sign(urlpath, data):
    postdata = urllib.parse.urlencode(data)
    encoded = (str(data['nonce']) + postdata).encode()
    message = urlpath.encode() + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(KRAKEN_API_SECRET), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()

def kraken_post(urlpath, data):
    data['nonce'] = str(int(time.time() * 1000))
    headers = {'API-Key': KRAKEN_API_KEY, 'API-Sign': kraken_sign(urlpath, data)}
    r = session.post(f"{KRAKEN_URL}{urlpath}", headers=headers, data=data, timeout=30)
    r.raise_for_status()
    result = r.json()
    if result.get('error'): raise Exception(f"Kraken error: {result['error']}")
    return result['result']

def kraken_get_balance():
    try:
        result = kraken_post("/0/private/Balance", {})
        return float(result.get("ZUSD", result.get("USD", 0)))
    except Exception as e:
        log.error(f"Balance error: {e}")
    return 0.0

def kraken_place_order(pair, side, qty):
    return kraken_post("/0/private/AddOrder", {
        "pair": pair, "type": side, "ordertype": "market", "volume": str(round(qty, 6))
    })

# ═══ PAPER TRADING ════════════════════════════════════════════════════════════
def paper_buy(symbol, price, trade_size, strategy_label):
    global paper_balance
    qty = round(trade_size / price, 6)
    cost = qty * price
    if paper_balance < cost:
        log.warning(f"Insufficient balance for {symbol} {strategy_label}")
        return None
    paper_balance -= cost
    log.info(f"📄 {strategy_label} BUY {symbol} qty={qty} @ ${price:,.2f} | Balance: ${paper_balance:,.2f}")
    return qty

def paper_sell(symbol, price, qty, strategy_label):
    global paper_balance
    paper_balance += qty * price
    log.info(f"📄 {strategy_label} SELL {symbol} qty={qty} @ ${price:,.2f} | Balance: ${paper_balance:,.2f}")

def get_balance():
    if TRADING_MODE == "live": return kraken_get_balance()
    return paper_balance

# ═══ STRATEGY TICK ════════════════════════════════════════════════════════════
def run_strategy(strategy_name, cfg, positions, trades, stats):
    for s in SYMBOLS:
        symbol = s["symbol"]
        try:
            price = price_cache.get(symbol)
            if not price: continue

            rsi = get_rsi(s["kraken_ohlc"], symbol, cfg["rsi_interval"])
            log.info(f"[{strategy_name}] {symbol} = ${price:,.2f} | RSI({cfg['rsi_interval']}m) = {rsi}")
            pos = positions.get(symbol)

            # ── EXIT ──────────────────────────────────────────────────────────
            if pos:
                pct = (price - pos["entry"]) / pos["entry"]
                reason = None
                if pct >= cfg["tp"]:
                    reason = f"Take Profit +{pct*100:.2f}%"
                elif pct <= -cfg["sl"]:
                    reason = f"Stop Loss {pct*100:.2f}%"
                elif rsi is not None and rsi > cfg["rsi_sell"]:
                    reason = f"RSI Exit {rsi}"

                if reason:
                    is_win = pct >= 0
                    if TRADING_MODE == "live":
                        kraken_place_order(s["kraken_order"], "sell", pos["qty"])
                    else:
                        paper_sell(symbol, price, pos["qty"], cfg["label"])
                    pnl = pos["qty"] * (price - pos["entry"])
                    stats["pnl"] += pnl
                    if is_win: stats["wins"] += 1
                    else: stats["losses"] += 1
                    trades.append({
                        "symbol": f"{symbol}/USD", "side": "SELL",
                        "price": price, "qty": pos["qty"],
                        "pnl": round(pnl, 4), "reason": reason,
                        "strategy": strategy_name,
                        "time": datetime.now(timezone.utc).isoformat()
                    })
                    del positions[symbol]
                    log.info(f"{'✅' if is_win else '🛑'} [{strategy_name}] SELL {symbol} | {reason} | PnL={pnl:+.4f}")

            # ── ENTRY ─────────────────────────────────────────────────────────
            if symbol not in positions and rsi is not None and rsi < cfg["rsi_buy"]:
                log.info(f"🎯 [{strategy_name}] BUY SIGNAL {symbol} RSI={rsi}")
                if TRADING_MODE == "live":
                    qty = round(cfg["trade_size"] / price, 6)
                    kraken_place_order(s["kraken_order"], "buy", qty)
                else:
                    qty = paper_buy(symbol, price, cfg["trade_size"], cfg["label"])
                    if qty is None: continue
                positions[symbol] = {"entry": price, "qty": qty, "time": datetime.now(timezone.utc).isoformat()}
                trades.append({
                    "symbol": f"{symbol}/USD", "side": "BUY",
                    "price": price, "qty": qty, "pnl": None,
                    "reason": f"RSI {rsi} < {cfg['rsi_buy']}",
                    "strategy": strategy_name,
                    "time": datetime.now(timezone.utc).isoformat()
                })
                log.info(f"📈 [{strategy_name}] BUY {symbol} @ ${price:,.2f}")
            elif symbol not in positions:
                log.info(f"⏳ [{strategy_name}] {symbol} RSI={rsi} — waiting for RSI < {cfg['rsi_buy']}")

        except Exception as e:
            log.error(f"[{strategy_name}] Tick error {symbol}: {e}")

# ═══ SAVE STATE ═══════════════════════════════════════════════════════════════
def save_state():
    try:
        all_trades = sorted(
            [*scalp_trades[-25:], *trend_trades[-25:]],
            key=lambda x: x["time"], reverse=True
        )[:50]
        state = {
            "scalp_positions": scalp_positions,
            "trend_positions": trend_positions,
            "scalp_trades": scalp_trades[-50:],
            "trend_trades": trend_trades[-50:],
            "all_trades": all_trades,
            "scalp_stats": scalp_stats,
            "trend_stats": trend_stats,
            "balance": get_balance(),
            "mode": TRADING_MODE,
            "active_strategy": active_strategy,
            "prices": price_cache,
            "rsi": rsi_cache,
            "updated": datetime.now(timezone.utc).isoformat()
        }
        with open("/tmp/state.json", "w") as f:
            json.dump(state, f)
    except Exception as e:
        log.error(f"save_state error: {e}")

# ═══ BOT TICK ════════════════════════════════════════════════════════════════
def bot_tick():
    global rsi_cache, active_strategy
    rsi_cache = {}
    # Read active strategy from dashboard
    try:
        if os.path.exists("/tmp/active_strategy.txt"):
            with open("/tmp/active_strategy.txt") as f:
                active_strategy = f.read().strip()
    except: pass
    now = datetime.now(timezone.utc).strftime('%H:%M:%S')
    log.info(f"--- Tick {now} | {TRADING_MODE.upper()} | ${get_balance():,.2f} | Active: {active_strategy} ---")

    if not fetch_all_prices():
        log.error("No prices available")
        return

    if active_strategy in ("SCALP", "BOTH"):
        run_strategy("SCALP", STRATEGIES["SCALP"], scalp_positions, scalp_trades, scalp_stats)

    if active_strategy in ("TREND", "BOTH"):
        run_strategy("TREND", STRATEGIES["TREND"], trend_positions, trend_trades, trend_stats)

    save_state()

# ═══ MAIN ════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    log.info(f"⚡ APEX BOT DUAL STRATEGY | {TRADING_MODE.upper()} mode")
    log.info(f"⚡ SCALP: RSI(1m) Buy<45 Sell>60 TP2% SL1.5% $100/trade")
    log.info(f"📈 TREND: RSI(4h) Buy<40 Sell>65 TP5% SL2.5% $200/trade")
    log.info(f"💰 Max exposure: 4x$100 + 4x$200 = $1,200 (12% of balance)")
    if TRADING_MODE == "live":
        if not KRAKEN_API_KEY or not KRAKEN_API_SECRET:
            log.error("❌ Missing API keys!")
            exit(1)
        log.info(f"💰 Kraken Balance: ${kraken_get_balance():,.2f}")
    else:
        log.info(f"💰 Paper Balance: ${paper_balance:,.2f}")
    save_state()
    fetch_all_prices()
    while True:
        try:
            bot_tick()
        except Exception as e:
            log.error(f"Bot error: {e}")
        log.info("⏳ Sleeping 30s...")
        time.sleep(30)
