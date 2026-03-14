import os, time, hmac, hashlib, requests, json, logging, base64, urllib.parse
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger(__name__)

TRADING_MODE      = os.environ.get("TRADING_MODE", "paper")
KRAKEN_API_KEY    = os.environ.get("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = os.environ.get("KRAKEN_API_SECRET", "")
KRAKEN_URL        = "https://api.kraken.com"

STOP_LOSS    = 0.02
TAKE_PROFIT  = 0.035
RSI_PERIOD   = 14
RSI_OVERSOLD = 32
TRADE_USDT   = 100

SYMBOLS = [
    {"symbol": "BTC",  "kraken_pair": "XBTUSD",  "kraken_order": "XXBTZUSD", "qty": 0.001},
    {"symbol": "ETH",  "kraken_pair": "ETHUSD",  "kraken_order": "XETHZUSD", "qty": 0.01},
    {"symbol": "SOL",  "kraken_pair": "SOLUSD",  "kraken_order": "SOLUSD",   "qty": 0.5},
    {"symbol": "XRP",  "kraken_pair": "XRPUSD",  "kraken_order": "XXRPZUSD", "qty": 10.0},
]

positions     = {}
trades        = []
stats         = {"pnl": 0.0, "wins": 0, "losses": 0}
paper_balance = 10000.0
price_cache   = {}

# ═══ KRAKEN PUBLIC API (prices + OHLC — no auth, no geo restrictions) ════════
def fetch_all_prices():
    try:
        pairs = ",".join(s["kraken_pair"] for s in SYMBOLS)
        r = requests.get(f"{KRAKEN_URL}/0/public/Ticker?pair={pairs}", timeout=15)
        data = r.json()
        log.info(f"Kraken ticker response: {data}")
        if data.get("error"):
            log.error(f"Kraken ticker error: {data['error']}")
            return False
        for s in SYMBOLS:
            for key, val in data["result"].items():
                if s["kraken_pair"].upper() in key.upper() or key.upper() in s["kraken_pair"].upper():
                    price = float(val["c"][0])
                    price_cache[s["symbol"]] = price
                    log.info(f"✅ {s['symbol']} = ${price:,.2f}")
        return len(price_cache) > 0
    except Exception as e:
        log.error(f"fetch_all_prices error: {e}")
        return False

def get_rsi(kraken_pair):
    try:
        r = requests.get(
            f"{KRAKEN_URL}/0/public/OHLC?pair={kraken_pair}&interval=240",
            timeout=15
        )
        data = r.json()
        if data.get("error"):
            return None
        result = data["result"]
        key = [k for k in result.keys() if k != "last"][0]
        closes = [float(candle[4]) for candle in result[key][-30:]]
        return calc_rsi(closes)
    except Exception as e:
        log.error(f"get_rsi error {kraken_pair}: {e}")
    return None

def calc_rsi(prices):
    if len(prices) < RSI_PERIOD + 1:
        return None
    recent = prices[-(RSI_PERIOD+1):]
    gains = losses = 0
    for i in range(1, len(recent)):
        d = recent[i] - recent[i-1]
        if d > 0: gains += d
        else: losses += abs(d)
    ag = gains / RSI_PERIOD
    al = losses / RSI_PERIOD
    if al == 0: return 100
    return round(100 - 100 / (1 + ag / al), 2)

# ═══ KRAKEN PRIVATE (live orders) ════════════════════════════════════════════
def kraken_sign(urlpath, data):
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
    log.info(f"📄 PAPER BUY {symbol} qty={qty} @ ${price:,.2f} | Balance: ${paper_balance:,.2f}")
    return qty

def paper_sell(symbol, price, qty):
    global paper_balance
    paper_balance += qty * price
    log.info(f"📄 PAPER SELL {symbol} qty={qty} @ ${price:,.2f} | Balance: ${paper_balance:,.2f}")

def get_balance():
    if TRADING_MODE == "live":
        return kraken_get_balance()
    return paper_balance

def save_state():
    try:
        state = {
            "positions": positions,
            "trades": trades[-50:],
            "stats": stats,
            "balance": get_balance(),
            "mode": TRADING_MODE,
            "prices": price_cache,
            "updated": datetime.now(timezone.utc).isoformat()
        }
        with open("/tmp/state.json", "w") as f:
            json.dump(state, f)
    except Exception as e:
        log.error(f"save_state error: {e}")

# ═══ BOT TICK ════════════════════════════════════════════════════════════════
def bot_tick():
    now = datetime.now(timezone.utc).strftime('%H:%M:%S')
    log.info(f"--- Tick {now} | {TRADING_MODE.upper()} | ${get_balance():,.2f} ---")

    if not fetch_all_prices():
        log.error("Could not fetch prices, skipping tick")
        return

    for s in SYMBOLS:
        symbol = s["symbol"]
        try:
            price = price_cache.get(symbol)
            if not price:
                log.warning(f"No price for {symbol}")
                continue

            rsi = get_rsi(s["kraken_pair"])
            log.info(f"{symbol} = ${price:,.2f} | RSI = {rsi}")
            pos = positions.get(symbol)

            # Exit
            if pos:
                pct = (price - pos["entry"]) / pos["entry"]
                if pct <= -STOP_LOSS or pct >= TAKE_PROFIT:
                    is_win = pct >= TAKE_PROFIT
                    if TRADING_MODE == "live":
                        kraken_place_order(s["kraken_order"], "sell", pos["qty"])
                    else:
                        paper_sell(symbol, price, pos["qty"])
                    pnl = pos["qty"] * (price - pos["entry"])
                    stats["pnl"] += pnl
                    if is_win: stats["wins"] += 1
                    else: stats["losses"] += 1
                    trades.append({
                        "symbol": f"{symbol}/USD", "side": "SELL",
                        "price": price, "qty": pos["qty"],
                        "pnl": round(pnl, 4),
                        "reason": "Take Profit" if is_win else "Stop Loss",
                        "time": datetime.now(timezone.utc).isoformat(),
                        "mode": TRADING_MODE
                    })
                    del positions[symbol]
                    log.info(f"{'✅ TP' if is_win else '🛑 SL'} {symbol} @ ${price:,.2f} PnL={pnl:+.2f}")

            # Entry
            if symbol not in positions and rsi is not None and rsi < RSI_OVERSOLD:
                log.info(f"🎯 BUY SIGNAL {symbol} RSI={rsi}")
                if TRADING_MODE == "live":
                    kraken_place_order(s["kraken_order"], "buy", s["qty"])
                    qty = s["qty"]
                else:
                    qty = paper_buy(symbol, price)
                    if qty is None:
                        continue
                positions[symbol] = {"entry": price, "qty": qty, "time": datetime.now(timezone.utc).isoformat()}
                trades.append({
                    "symbol": f"{symbol}/USD", "side": "BUY",
                    "price": price, "qty": qty, "pnl": None,
                    "reason": f"RSI {rsi}",
                    "time": datetime.now(timezone.utc).isoformat(),
                    "mode": TRADING_MODE
                })
                log.info(f"📈 {'LIVE' if TRADING_MODE == 'live' else 'PAPER'} BUY {symbol} @ ${price:,.2f}")

        except Exception as e:
            log.error(f"Tick error {symbol}: {e}")

    save_state()

# ═══ MAIN ════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    log.info(f"⚡ APEX BOT | {'🔴 LIVE' if TRADING_MODE == 'live' else '📄 PAPER'} mode")
    log.info("📊 Prices: Kraken public API (no restrictions)")

    if TRADING_MODE == "live":
        if not KRAKEN_API_KEY or not KRAKEN_API_SECRET:
            log.error("❌ Missing KRAKEN_API_KEY or KRAKEN_API_SECRET!")
            exit(1)
        log.info(f"💰 Kraken Balance: ${kraken_get_balance():,.2f}")
    else:
        log.info(f"💰 Paper Balance: ${paper_balance:,.2f}")

    if fetch_all_prices():
        log.info(f"✅ Kraken public API working! Prices: {price_cache}")
    else:
        log.error("❌ Could not fetch prices from Kraken public API!")

    while True:
        try:
            bot_tick()
        except Exception as e:
            log.error(f"Bot error: {e}")
        log.info("⏳ Sleeping 120s...")
        time.sleep(120)
