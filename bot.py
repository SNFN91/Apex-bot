import os, time, hmac, hashlib, requests, json, logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger(__name__)

# ═══ CONFIG ══════════════════════════════════════════════════════════════════
TRADING_MODE = os.environ.get("TRADING_MODE", "paper")
KRAKEN_API_KEY    = os.environ.get("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = os.environ.get("KRAKEN_API_SECRET", "")
KRAKEN_URL = "https://api.kraken.com"

STOP_LOSS    = 0.02
TAKE_PROFIT  = 0.035
RSI_PERIOD   = 14
RSI_OVERSOLD = 32
TRADE_USDT   = 100

SYMBOLS = [
    {"symbol": "BTCUSDT",  "kraken": "XXBTZUSD", "qty": 0.001},
    {"symbol": "ETHUSDT",  "kraken": "XETHZUSD", "qty": 0.01},
    {"symbol": "SOLUSDT",  "kraken": "SOLUSD",   "qty": 0.5},
    {"symbol": "XRPUSDT",  "kraken": "XXRPZUSD", "qty": 10.0},
]

positions     = {}
trades        = []
stats         = {"pnl": 0.0, "wins": 0, "losses": 0}
paper_balance = 10000.0

# ═══ MARKET DATA ══════════════════════════════════════════════════════════════
def get_price(symbol):
    try:
        r = requests.get(
            f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}",
            timeout=10
        )
        data = r.json()
        log.info(f"Price API response {symbol}: {data}")
        if "price" in data:
            return float(data["price"])
        log.error(f"No price key in response for {symbol}: {data}")
    except Exception as e:
        log.error(f"get_price error {symbol}: {e}")
    return None

def get_klines(symbol):
    try:
        r = requests.get(
            f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit=50",
            timeout=10
        )
        data = r.json()
        if isinstance(data, list) and len(data) > 0:
            return [float(k[4]) for k in data]
        log.error(f"Unexpected klines response for {symbol}: {data}")
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

# ═══ KRAKEN LIVE ══════════════════════════════════════════════════════════════
def kraken_sign(urlpath, data):
    import base64, urllib.parse
    postdata = urllib.parse.urlencode(data)
    encoded = (str(data['nonce']) + postdata).encode()
    message = urlpath.encode() + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(KRAKEN_API_SECRET), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()

def kraken_post(urlpath, data):
    data['nonce'] = str(int(time.time() * 1000))
    headers = {'API-Key': KRAKEN_API_KEY, 'API-Sign': kraken_sign(urlpath, data)}
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
        "pair": pair, "type": side,
        "ordertype": "market", "volume": str(qty)
    })

# ═══ PAPER ════════════════════════════════════════════════════════════════════
def paper_buy(symbol, price):
    global paper_balance
    qty = round(TRADE_USDT / price, 6)
    cost = qty * price
    if paper_balance < cost:
        log.warning(f"Insufficient paper balance for {symbol}")
        return None
    paper_balance -= cost
    log.info(f"📄 PAPER BUY {symbol} qty={qty} @ ${price:.2f} | Balance: ${paper_balance:.2f}")
    return qty

def paper_sell(symbol, price, qty):
    global paper_balance
    paper_balance += qty * price
    log.info(f"📄 PAPER SELL {symbol} qty={qty} @ ${price:.2f} | Balance: ${paper_balance:.2f}")

def get_balance():
    if TRADING_MODE == "live":
        return kraken_get_balance()
    return paper_balance

# ═══ SAVE STATE ═══════════════════════════════════════════════════════════════
def save_state():
    try:
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
    except Exception as e:
        log.error(f"save_state error: {e}")

# ═══ BOT TICK ════════════════════════════════════════════════════════════════
def bot_tick():
    log.info(f"--- Tick @ {datetime.utcnow().strftime('%H:%M:%S')} | Mode: {TRADING_MODE} | Balance: ${get_balance():.2f} ---")
    for s in SYMBOLS:
        symbol = s["symbol"]
        try:
            price = get_price(symbol)
            if price is None:
                log.warning(f"Skipping {symbol} — no price")
                continue
            log.info(f"{symbol} = ${price:.2f}")

            klines = get_klines(symbol)
            rsi = calc_rsi(klines)
            log.info(f"{symbol} RSI = {rsi:.1f if rsi else 'N/A'}")
            pos = positions.get(symbol)

            # Exit
            if pos:
                pct = (price - pos["entry"]) / pos["entry"]
                log.info(f"{symbol} position PnL = {pct*100:.2f}%")
                if pct <= -STOP_LOSS or pct >= TAKE_PROFIT:
                    is_win = pct >= TAKE_PROFIT
                    if TRADING_MODE == "live":
                        kraken_place_order(s["kraken"], "sell", pos["qty"])
                    else:
                        paper_sell(symbol, price, pos["qty"])
                    pnl = pos["qty"] * (price - pos["entry"])
                    stats["pnl"] += pnl
                    if is_win: stats["wins"] += 1
                    else: stats["losses"] += 1
                    trades.append({"symbol": symbol, "side": "SELL", "price": price, "qty": pos["qty"], "pnl": round(pnl,4), "reason": "Take Profit" if is_win else "Stop Loss", "time": datetime.utcnow().isoformat(), "mode": TRADING_MODE})
                    del positions[symbol]
                    log.info(f"{'✅ TP' if is_win else '🛑 SL'} {symbol} @ {price:.2f} PnL={pnl:+.2f}")

            # Entry
            if symbol not in positions and rsi is not None and rsi < RSI_OVERSOLD:
                log.info(f"🎯 BUY SIGNAL {symbol} RSI={rsi:.1f}")
                if TRADING_MODE == "live":
                    qty = s["qty"]
                    kraken_place_order(s["kraken"], "buy", qty)
                else:
                    qty = paper_buy(symbol, price)
                    if qty is None:
                        continue
                positions[symbol] = {"entry": price, "qty": qty, "time": datetime.utcnow().isoformat()}
                trades.append({"symbol": symbol, "side": "BUY", "price": price, "qty": qty, "pnl": None, "reason": f"RSI {rsi:.1f}", "time": datetime.utcnow().isoformat(), "mode": TRADING_MODE})
                log.info(f"📈 BUY {symbol} @ {price:.2f} qty={qty}")

        except Exception as e:
            log.error(f"Tick error {symbol}: {e}")

    save_state()

# ═══ MAIN ════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    log.info(f"⚡ APEX BOT starting in {'🔴 LIVE' if TRADING_MODE == 'live' else '📄 PAPER'} mode")
    log.info("📊 Prices: Binance public API")
    if TRADING_MODE == "live":
        if not KRAKEN_API_KEY or not KRAKEN_API_SECRET:
            log.error("❌ Missing KRAKEN_API_KEY or KRAKEN_API_SECRET!")
            exit(1)
        log.info(f"💰 Kraken Balance: ${kraken_get_balance():,.2f}")
    else:
        log.info(f"💰 Paper Balance: ${paper_balance:,.2f}")
    try:
        price = get_price("BTCUSDT")
        if price:
            log.info(f"✅ Market data OK — BTC = ${price:.2f}")
        else:
            log.error("❌ Could not fetch BTC price!")
    except Exception as e:
        log.error(f"❌ Market data error: {e}")
    while True:
        try:
            bot_tick()
        except Exception as e:
            log.error(f"Bot error: {e}")
        log.info("⏳ Sleeping 15s...")
        time.sleep(15)
