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

STOP_LOSS    = 0.01    # 1% stop loss — tight scalping
TAKE_PROFIT  = 0.02    # 2% take profit — fast exits
RSI_PERIOD   = 14
RSI_BUY      = 50      # buy when RSI < 50 (shallower dip = more signals)
RSI_SELL     = 60      # sell when RSI > 60 (recovering)
RSI_INTERVAL = 1       # 1-minute candles — instant scalping
TRADE_USDT   = 100     # $100 per trade — scalping mode

SYMBOLS = [
    {"symbol": "BTC", "kraken_ticker": "XXBTZUSD", "kraken_ohlc": "XBTUSD",  "kraken_order": "XXBTZUSD"},
    {"symbol": "ETH", "kraken_ticker": "XETHZUSD", "kraken_ohlc": "ETHUSD",  "kraken_order": "XETHZUSD"},
    {"symbol": "SOL", "kraken_ticker": "SOLUSD",   "kraken_ohlc": "SOLUSD",  "kraken_order": "SOLUSD"},
    {"symbol": "XRP", "kraken_ticker": "XXRPZUSD", "kraken_ohlc": "XRPUSD",  "kraken_order": "XXRPZUSD"},
]

positions     = {}
trades        = []
stats         = {"pnl": 0.0, "wins": 0, "losses": 0}
paper_balance = 10000.0
price_cache      = {}
last_price_cache = {}
rsi_cache        = {}
last_rsi_cache   = {}  # fallback when OHLC fetch fails

session = requests.Session()
retries = Retry(total=5, backoff_factor=2, status_forcelist=[502, 503, 504])
session.mount('https://', HTTPAdapter(max_retries=retries))

# ═══ PRICES ═══════════════════════════════════════════════════════════════════
def fetch_all_prices():
    global price_cache, last_price_cache
    temp_prices = {}
    pairs = ",".join(s["kraken_ticker"] for s in SYMBOLS)
    for attempt in range(3):
        try:
            r = session.get(f"{KRAKEN_URL}/0/public/Ticker?pair={pairs}", timeout=45)
            data = r.json()
            if data.get("error"):
                log.error(f"Kraken ticker error: {data['error']}")
                return False
            for s in SYMBOLS:
                key = s["kraken_ticker"]
                if key in data["result"]:
                    temp_prices[s["symbol"]] = float(data["result"][key]["c"][0])
                    log.info(f"✅ {s['symbol']} = ${temp_prices[s['symbol']]:,.2f}")
            # Success — update both caches
            price_cache = temp_prices
            last_price_cache = temp_prices.copy()
            return True
        except Exception as e:
            log.warning(f"Price fetch attempt {attempt+1} failed: {e}")
            if attempt < 2: time.sleep(2 ** attempt)
    # All attempts failed — fall back to last known prices
    if last_price_cache:
        log.warning("⚠️ Using last known prices from previous tick")
        price_cache = last_price_cache.copy()
        return True
    log.error("❌ No prices available at all")
    return False

# ═══ RSI (1-min candles) ══════════════════════════════════════════════════════
def get_rsi(kraken_ohlc, symbol):
    if symbol in rsi_cache:
        return rsi_cache[symbol]
    try:
        r = session.get(
            f"{KRAKEN_URL}/0/public/OHLC?pair={kraken_ohlc}&interval={RSI_INTERVAL}",
            timeout=30
        )
        data = r.json()
        if data.get("error") and data["error"]:
            if symbol in last_rsi_cache:
                log.warning(f"⚠️ Using cached RSI for {symbol}: {last_rsi_cache[symbol]}")
                return last_rsi_cache[symbol]
            return None
        result = data["result"]
        key = [k for k in result.keys() if k != "last"][0]
        closes = [float(c[4]) for c in result[key][-30:]]
        rsi = calc_rsi(closes)
        if rsi is not None:
            rsi_cache[symbol] = rsi
            last_rsi_cache[symbol] = rsi  # update fallback cache
        return rsi
    except Exception as e:
        log.error(f"get_rsi error {kraken_ohlc}: {e}")
        if symbol in last_rsi_cache:
            log.warning(f"⚠️ Using cached RSI for {symbol}: {last_rsi_cache[symbol]}")
            return last_rsi_cache[symbol]
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
    r = session.post(f"{KRAKEN_URL}{urlpath}", headers=headers, data=data, timeout=45)
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
        "ordertype": "market", "volume": str(round(qty, 6))
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

# ═══ STATE ════════════════════════════════════════════════════════════════════
def save_state():
    try:
        state = {
            "positions": positions,
            "trades": trades[-50:],
            "stats": stats,
            "balance": get_balance(),
            "mode": TRADING_MODE,
            "prices": price_cache,
            "rsi": rsi_cache,
            "last_rsi": last_rsi_cache,
            "updated": datetime.now(timezone.utc).isoformat()
        }
        with open("/tmp/state.json", "w") as f:
            json.dump(state, f)
    except Exception as e:
        log.error(f"save_state error: {e}")

# ═══ BOT TICK ════════════════════════════════════════════════════════════════
def bot_tick():
    global rsi_cache
    rsi_cache = {}  # refresh every tick

    now = datetime.now(timezone.utc).strftime('%H:%M:%S')
    log.info(f"--- Tick {now} | {TRADING_MODE.upper()} | ${get_balance():,.2f} ---")

    if not fetch_all_prices():
        log.error("No prices available — skipping tick")
        return

    for s in SYMBOLS:
        symbol = s["symbol"]
        try:
            price = price_cache.get(symbol)
            if not price:
                continue

            rsi = get_rsi(s["kraken_ohlc"], symbol)
            log.info(f"{symbol} = ${price:,.2f} | RSI({RSI_INTERVAL}m) = {rsi}")
            pos = positions.get(symbol)

            # ── EXIT ──────────────────────────────────────────────────────────
            if pos:
                pct = (price - pos["entry"]) / pos["entry"]
                reason = None
                if pct >= TAKE_PROFIT:
                    reason = f"Take Profit +{pct*100:.2f}%"
                elif pct <= -STOP_LOSS:
                    reason = f"Stop Loss {pct*100:.2f}%"
                elif rsi is not None and rsi > RSI_SELL:
                    reason = f"RSI Overbought {rsi}"

                if reason:
                    is_win = pct >= 0
                    qty = pos["qty"]
                    if TRADING_MODE == "live":
                        kraken_place_order(s["kraken_order"], "sell", qty)
                    else:
                        paper_sell(symbol, price, qty)
                    pnl = qty * (price - pos["entry"])
                    stats["pnl"] += pnl
                    if is_win: stats["wins"] += 1
                    else: stats["losses"] += 1
                    trades.append({
                        "symbol": f"{symbol}/USD", "side": "SELL",
                        "price": price, "qty": qty,
                        "pnl": round(pnl, 4), "reason": reason,
                        "time": datetime.now(timezone.utc).isoformat(),
                        "mode": TRADING_MODE
                    })
                    del positions[symbol]
                    log.info(f"{'✅' if is_win else '🛑'} SELL {symbol} @ ${price:,.2f} | {reason} | PnL={pnl:+.4f}")

            # ── ENTRY ─────────────────────────────────────────────────────────
            if symbol not in positions and rsi is not None and rsi < RSI_BUY:
                log.info(f"🎯 BUY SIGNAL {symbol} RSI={rsi} < {RSI_BUY}")
                qty = round(TRADE_USDT / price, 6)
                if TRADING_MODE == "live":
                    kraken_place_order(s["kraken_order"], "buy", qty)
                else:
                    qty = paper_buy(symbol, price)
                    if qty is None:
                        continue
                positions[symbol] = {
                    "entry": price, "qty": qty,
                    "time": datetime.now(timezone.utc).isoformat()
                }
                trades.append({
                    "symbol": f"{symbol}/USD", "side": "BUY",
                    "price": price, "qty": qty, "pnl": None,
                    "reason": f"RSI {rsi} < {RSI_BUY}",
                    "time": datetime.now(timezone.utc).isoformat(),
                    "mode": TRADING_MODE
                })
                log.info(f"📈 {'LIVE' if TRADING_MODE == 'live' else 'PAPER'} BUY {symbol} @ ${price:,.2f} qty={qty}")
            elif symbol not in positions:
                log.info(f"⏳ {symbol} RSI={rsi} — waiting for RSI < {RSI_BUY}")

        except Exception as e:
            log.error(f"Tick error {symbol}: {e}")

    save_state()

# ═══ MAIN ════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    log.info(f"⚡ APEX BOT | {'🔴 LIVE' if TRADING_MODE == 'live' else '📄 PAPER'} mode")
    log.info(f"📊 Strategy: RSI({RSI_INTERVAL}min) | Buy<{RSI_BUY} | Sell>{RSI_SELL} | TP{TAKE_PROFIT*100}% | SL{STOP_LOSS*100}%")
    log.info(f"💰 Trade size: ${TRADE_USDT} per position")
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
