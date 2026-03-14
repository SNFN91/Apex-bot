import os, time, hmac, hashlib, requests, json, logging
from datetime import datetime

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

# CoinGecko IDs for price + Kraken pairs for live trading
SYMBOLS = [
    {"symbol": "BTC/USD",  "coingecko": "bitcoin",  "kraken": "XXBTZUSD", "qty": 0.001},
    {"symbol": "ETH/USD",  "coingecko": "ethereum", "kraken": "XETHZUSD", "qty": 0.01},
    {"symbol": "SOL/USD",  "coingecko": "solana",   "kraken": "SOLUSD",   "qty": 0.5},
    {"symbol": "XRP/USD",  "coingecko": "ripple",   "kraken": "XXRPZUSD", "qty": 10.0},
]

positions     = {}
trades        = []
stats         = {"pnl": 0.0, "wins": 0, "losses": 0}
paper_balance = 10000.0
price_cache   = {}

# ═══ COINGECKO PRICES (no restrictions) ══════════════════════════════════════
def fetch_all_prices():
    try:
        ids = ",".join(s["coingecko"] for s in SYMBOLS)
        r = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd",
            timeout=15
        )
        data = r.json()
        log.info(f"CoinGecko response: {data}")
        for s in SYMBOLS:
            cg_id = s["coingecko"]
            if cg_id in data and "usd" in data[cg_id]:
                price_cache[s["symbol"]] = float(data[cg_id]["usd"])
                log.info(f"✅ {s['symbol']} = ${price_cache[s['symbol']]:,.2f}")
        return True
    except Exception as e:
        log.error(f"CoinGecko price error: {e}")
        return False

def get_price(symbol):
    return price_cache.get(symbol)

# ═══ RSI via CoinGecko OHLC ═══════════════════════════════════════════════════
def get_rsi(coingecko_id):
    try:
        # Get 1-day OHLC (free tier gives 1d candles, enough for trend signal)
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{coingecko_id}/ohlc?vs_currency=usd&days=14",
            timeout=15
        )
        data = r.json()
        if isinstance(data, list) and len(data) > RSI_PERIOD:
            closes = [float(d[4]) for d in data]
            return calc_rsi(closes)
    except Exception as e:
        log.error(f"RSI error {coingecko_id}: {e}")
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

# ═══ PAPER TRADING ════════════════════════════════════════════════════════════
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

# ═══ SAVE STATE ═══════════════════════════════════════════════════════════════
def save_state():
    try:
        state = {
            "positions": positions,
            "trades": trades[-50:],
            "stats": stats,
            "balance": get_balance(),
            "mode": TRADING_MODE,
            "prices": price_cache,
            "updated": datetime.utcnow().isoformat()
        }
        with open("/tmp/state.json", "w") as f:
            json.dump(state, f)
    except Exception as e:
        log.error(f"save_state error: {e}")

# ═══ BOT TICK ════════════════════════════════════════════════════════════════
def bot_tick():
    log.info(f"--- Tick {datetime.utcnow().strftime('%H:%M:%S')} | {TRADING_MODE.upper()} | ${get_balance():,.2f} ---")

    # Fetch all prices in one call
    fetch_all_prices()

    for s in SYMBOLS:
        symbol = s["symbol"]
        try:
            price = get_price(symbol)
            if price is None:
                log.warning(f"No price for {symbol}, skipping")
                continue

            rsi = get_rsi(s["coingecko"])
            log.info(f"{symbol} RSI={rsi:.1f if rsi else 'N/A'}")
            pos = positions.get(symbol)

            # Exit logic
            if pos:
                pct = (price - pos["entry"]) / pos["entry"]
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
                    log.info(f"{'✅ TP' if is_win else '🛑 SL'} {symbol} @ ${price:,.2f} PnL={pnl:+.2f}")

            # Entry logic
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
                log.info(f"📈 {'LIVE' if TRADING_MODE == 'live' else 'PAPER'} BUY {symbol} @ ${price:,.2f}")

        except Exception as e:
            log.error(f"Tick error {symbol}: {e}")

    save_state()
    # CoinGecko free tier: max 10-30 calls/min — sleep between ticks
    time.sleep(2)

# ═══ MAIN ════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    log.info(f"⚡ APEX BOT | {'🔴 LIVE' if TRADING_MODE == 'live' else '📄 PAPER'} mode")
    log.info("📊 Prices: CoinGecko (no geo restrictions)")
    if TRADING_MODE == "live":
        if not KRAKEN_API_KEY or not KRAKEN_API_SECRET:
            log.error("❌ Missing KRAKEN_API_KEY or KRAKEN_API_SECRET!")
            exit(1)
        log.info(f"💰 Kraken Balance: ${kraken_get_balance():,.2f}")
    else:
        log.info(f"💰 Paper Balance: ${paper_balance:,.2f}")

    # Test CoinGecko
    if fetch_all_prices():
        log.info("✅ CoinGecko working!")
    else:
        log.error("❌ CoinGecko failed!")

    while True:
        try:
            bot_tick()
        except Exception as e:
            log.error(f"Bot error: {e}")
        log.info("⏳ Sleeping 60s...")
        time.sleep(60)
