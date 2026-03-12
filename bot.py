import os, time, hmac, hashlib, requests, json, logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger(__name__)

API_KEY    = os.environ.get("BINANCE_API_KEY", "")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "")
BASE_URL   = "https://testnet.binance.vision/api"

STOP_LOSS    = 0.02
TAKE_PROFIT  = 0.035
RSI_PERIOD   = 14
RSI_OVERSOLD = 32
TRADE_QTY    = {"BTCUSDT": 0.001, "ETHUSDT": 0.01, "BNBUSDT": 0.1, "SOLUSDT": 0.5}
SYMBOLS      = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]

positions = {}
trades    = []
stats     = {"pnl": 0.0, "wins": 0, "losses": 0}

def sign(params):
    params["timestamp"] = int(time.time() * 1000)
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    sig = hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return qs + "&signature=" + sig

def api_get(path, params={}):
    qs = sign(dict(params))
    r = requests.get(f"{BASE_URL}{path}?{qs}", headers={"X-MBX-APIKEY": API_KEY}, timeout=10)
    r.raise_for_status()
    return r.json()

def api_post(path, params={}):
    qs = sign(dict(params))
    r = requests.post(f"{BASE_URL}{path}?{qs}", headers={"X-MBX-APIKEY": API_KEY}, timeout=10)
    r.raise_for_status()
    return r.json()

def get_price(symbol):
    r = requests.get(f"{BASE_URL}/v3/ticker/price?symbol={symbol}", timeout=10)
    return float(r.json()["price"])

def get_klines(symbol):
    r = requests.get(f"{BASE_URL}/v3/klines?symbol={symbol}&interval=15m&limit=50", timeout=10)
    return [float(k[4]) for k in r.json()]

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

def get_balance():
    try:
        acc = api_get("/v3/account")
        for b in acc.get("balances", []):
            if b["asset"] == "USDT":
                return float(b["free"])
    except: pass
    return 0.0

def save_state():
    state = {
        "positions": positions,
        "trades": trades[-50:],
        "stats": stats,
        "balance": get_balance(),
        "updated": datetime.utcnow().isoformat()
    }
    with open("/tmp/state.json", "w") as f:
        json.dump(state, f)

def bot_tick():
    for symbol in SYMBOLS:
        try:
            price  = get_price(symbol)
            klines = get_klines(symbol)
            rsi    = calc_rsi(klines)
            pos    = positions.get(symbol)

            # Exit logic
            if pos:
                pct = (price - pos["entry"]) / pos["entry"]
                if pct <= -STOP_LOSS or pct >= TAKE_PROFIT:
                    is_win = pct >= TAKE_PROFIT
                    try:
                        api_post("/v3/order", {"symbol": symbol, "side": "SELL", "type": "MARKET", "quantity": TRADE_QTY[symbol]})
                        pnl = pos["qty"] * (price - pos["entry"])
                        stats["pnl"] += pnl
                        if is_win: stats["wins"] += 1
                        else: stats["losses"] += 1
                        trades.append({"symbol": symbol, "side": "SELL", "price": price, "qty": pos["qty"], "pnl": round(pnl,4), "reason": "Take Profit" if is_win else "Stop Loss", "time": datetime.utcnow().isoformat()})
                        del positions[symbol]
                        log.info(f"{'✅ TP' if is_win else '🛑 SL'} {symbol} @ {price:.2f} PnL={pnl:+.2f}")
                    except Exception as e:
                        log.error(f"SELL error {symbol}: {e}")

            # Entry logic
            if symbol not in positions and rsi is not None and rsi < RSI_OVERSOLD:
                try:
                    qty = TRADE_QTY[symbol]
                    api_post("/v3/order", {"symbol": symbol, "side": "BUY", "type": "MARKET", "quantity": qty})
                    positions[symbol] = {"entry": price, "qty": qty, "time": datetime.utcnow().isoformat()}
                    trades.append({"symbol": symbol, "side": "BUY", "price": price, "qty": qty, "pnl": None, "reason": f"RSI {rsi:.1f}", "time": datetime.utcnow().isoformat()})
                    log.info(f"📈 BUY {symbol} @ {price:.2f} RSI={rsi:.1f}")
                except Exception as e:
                    log.error(f"BUY error {symbol}: {e}")

        except Exception as e:
            log.error(f"Tick error {symbol}: {e}")

    save_state()

if __name__ == "__main__":
    log.info("⚡ APEX BOT starting on Binance Testnet...")
    if not API_KEY or not API_SECRET:
        log.error("❌ Missing BINANCE_API_KEY or BINANCE_API_SECRET env vars!")
        exit(1)
    bal = get_balance()
    log.info(f"💰 Testnet USDT Balance: {bal:,.2f}")
    while True:
        try:
            bot_tick()
        except Exception as e:
            log.error(f"Bot error: {e}")
        log.info("⏳ Sleeping 15s...")
        time.sleep(15)
