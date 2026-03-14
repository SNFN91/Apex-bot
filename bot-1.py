import os, time, hmac, hashlib, requests, json, logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger(__name__)

# ═══ CONFIG ══════════════════════════════════════════════════════════════════
TRADING_MODE = os.environ.get("TRADING_MODE", "paper")  # "paper" or "live"
KRAKEN_API_KEY    = os.environ.get("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = os.environ.get("KRAKEN_API_SECRET", "")
KRAKEN_URL = "https://api.kraken.com"

STOP_LOSS    = 0.02
TAKE_PROFIT  = 0.035
RSI_PERIOD   = 14
RSI_OVERSOLD = 32
TRADE_USDT   = 100  # per trade in paper mode

SYMBOLS = [
    {"symbol": "BTCUSDT",  "kraken": "XXBTZUSD", "qty": 0.001},
    {"symbol": "ETHUSDT",  "kraken": "XETHZUSD", "qty": 0.01},
    {"symbol": "SOLUSDT",  "kraken": "SOLUSD",   "qty": 0.5},
    {"symbol": "XRPUSDT",  "kraken": "XXRPZUSD", "qty": 10.0},
]

# ═══ STATE ═══════════════════════════════════════════════════════════════════
positions = {}
trades    = []
stats     = {"pnl": 0.0, "wins": 0, "losses": 0}
paper_balance = 10000.0  # starting paper balance

# ═══ MARKET DATA (Binance public — no restrictions) ═══════════════════════════
def get_price(symbol):
    try:
        r = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}", timeout=10)
        data = r.json()
        if "price" in data:
            return float(data["price"])
    except Exception as e:
        log.error(f"get_price error {symbol}: {e}")
    return None

def get_klines(symbol):
    try:
        r = requests.get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit=50", timeout=10)
        data = r.json()
        if isinstance(data, list) and len(data) > 0:
            return [float(k[4]) for k in data]
    except Exception as e:
        log.error(f"get_klines error {symbol}: {e}")
    return []

def calc_rsi(prices):
    if len(prices) < RSI_PERIOD + 1:
        return None
    recent = prices[-(RSI_PERIOD+1):]
    gains = losses = 0
    for i in range(1, len(recent)):
        d = recent[i] - recent[i-1]
        if d > 0: gains += d
        else: losses += abs(d)
    ag, al = gains/RSI_PERIOD, losses/RSI_PERIOD
    if al == 0: return 100
    return 100 - 100/(1 + ag/al)

# ═══ KRAKEN LIVE TRADING ══════════════════════════════════════════════════════
def kraken_sign(urlpath, data):
    import base64, urllib.parse
    postdata = urllib.parse.urlencode(data)
    encoded = (str(data['nonce']) + postdata).encode()
    message = urlpath.encode() + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(KRAKEN_API_SECRET), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()

def kraken_post(urlpath, data):
    data['nonce'] = str(int(time.time() * 1000))
    headers = {
        'API-Key': KRAKEN_API_KEY,
        'API-Sign': kraken_sign(urlpath, data)
    }
    r = requests.post(f"{KRAKEN_URL}{urlpath}", headers=headers, data=data, timeout=10)
    r.raise_for_status()
    result = r.json()
    if result.get('error'):
        raise Exception(f"Kraken error: {result['error']}")
    return result['result']

def kraken_get_balance():
    try:
        result = kraken_post("/0/private/Balance", {})
        return float(result.get("ZUSD", result.get("USD", 0)))
    except Exception as e:
        log.error(f"Kraken balance error: {e}")
    return 0.0

def kraken_place_order(pair, side, qty):
    return kraken_post("/0/private/AddOrder", {
        "pair": pair,
        "type": side,
        "ordertype": "market",
        "volume": str(qty)
    })

# ═══ PAPER TRADING ════════════════════════════════════════════════════════════
def paper_buy(symbol_info, price):
    global paper_balance
    qty = round(TRADE_USDT / price, 6)
    cost = qty * price
    if paper_balance < cost:
        log.warning(f"Insufficient paper balance for {symbol_info['symbol']}")
        return None
    paper_balance -= cost
    log.info(f"📄 PAPER BUY {symbol_info['symbol']} @ ${price:.2f} qty={qty}")
    return qty

def paper_sell(symbol_info, price, qty):
    global paper_balance
    paper_balance += qty * price
    log.info(f"📄 PAPER SELL {symbol_info['symbol']} @ ${price:.2f} qty={qty}")

# ═══ GET BALANCE ══════════════════════════════════════════════════════════════
def get_balance():
    if TRADING_MODE == "live":
        return kraken_get_balance()
    return paper_balance

# ═══ BOT TICK ════════════════════════════════════════════════════════════════
def bot_tick():
    for s in SYMBOLS:
        symbol = s["symbol"]
        try:
            price = get_price(symbol)
            if price is None:
                continue
            log.info(f"{symbol} = ${price:.2f}")

            klines = get_klines(symbol)
            rsi    = calc_rsi(klines)
            pos    = positions.get(symbol)

            # ── EXIT LOGIC ──────────────────────────────────────────────────
            if pos:
                pct = (price - pos["entry"]) / pos["entry"]
                if pct <= -STOP_LOSS or pct >= TAKE_PROFIT:
                    is_win = pct >= TAKE_PROFIT
                    try:
                        if TRADING_MODE == "live":
                            kraken_place_order(s["kraken"], "sell", pos["qty"])
                        else:
                            paper_sell(s, price, pos["qty"])

                        pnl = pos["qty"] * (price - pos["entry"])
                        stats["pnl"] += pnl
                        if is_win: stats["wins"] += 1
                        else: stats["losses"] += 1
                        trades.append({
                            "symbol": symbol, "side": "SELL", "price": price,
                            "qty": pos["qty"], "pnl": round(pnl, 4),
                            "reason": "Take Profit" if is_win else "Stop Loss",
                            "time": datetime.utcnow().isoformat(),
                            "mode": TRADING_MODE
                        })
                        del positions[symbol]
                        log.info(f"{'✅ TP' if is_win else '🛑 SL'} {symbol} @ {price:.2f} PnL={pnl:+.2f}")
                    except Exception as e:
                        log.error(f"SELL error {symbol}: {e}")

            # ── ENTRY LOGIC ─────────────────────────────────────────────────
            if symbol not in positions and rsi is not None and rsi < RSI_OVERSOLD:
                try:
                    if TRADING_MODE == "live":
                        qty = s["qty"]
                        kraken_place_order(s["kraken"], "buy", qty)
                    else:
                        qty = paper_buy(s, price)
                        if qty is None:
                            continue

                    positions[symbol] = {"entry": price, "qty": qty, "time": datetime.utcnow().isoformat()}
                    trades.append({
                        "symbol": symbol, "side": "BUY", "price": price,
                        "qty": qty, "pnl": None,
                        "reason": f"RSI {rsi:.1f}",
                        "time": datetime.utcnow().isoformat(),
                        "mode": TRADING_MODE
                    })
                    log.info(f"📈 {'LIVE' if TRADING_MODE == 'live' else 'PAPER'} BUY {symbol} @ {price:.2f} RSI={rsi:.1f}")
                except Exception as e:
                    log.error(f"BUY error {symbol}: {e}")

        except Exception as e:
            log.error(f"Tick error {symbol}: {e}")

    save_state()

def save_state():
    state = {
        "positions": positions,
        "trades": trades[-50:],
        "stats": stats,
        "balance": get_balance(),
        "mode": TRADING_MODE,
        "updated": datetime.utcnow().isoformat()
    }
    with open("/tmp/state.json", "w") as f:
        json.dump(state, f)

# ═══ MAIN ════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    log.info(f"⚡ APEX BOT starting in {'🔴 LIVE' if TRADING_MODE == 'live' else '📄 PAPER'} mode")
    log.info("📊 Market data: Binance public API (no restrictions)")

    if TRADING_MODE == "live":
        if not KRAKEN_API_KEY or not KRAKEN_API_SECRET:
            log.error("❌ Missing KRAKEN_API_KEY or KRAKEN_API_SECRET!")
            exit(1)
        bal = kraken_get_balance()
        log.info(f"💰 Kraken USD Balance: ${bal:,.2f}")
    else:
        log.info(f"💰 Paper Balance: ${paper_balance:,.2f}")

    # Test market data
    try:
        price = get_price("BTCUSDT")
        log.info(f"✅ Market data working — BTC = ${price:.2f}")
    except Exception as e:
        log.error(f"❌ Market data error: {e}")

    while True:
        try:
            bot_tick()
        except Exception as e:
            log.error(f"Bot error: {e}")
        log.info("⏳ Sleeping 15s...")
        time.sleep(15)
