import os, time, hmac, hashlib, requests, json, logging, base64, urllib.parse
from datetime import datetime, timezone, date
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger(__name__)

# ═══ CONFIG ═══════════════════════════════════════════════════════════════════
TRADING_MODE      = os.environ.get("TRADING_MODE", "paper")
KRAKEN_API_KEY    = os.environ.get("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = os.environ.get("KRAKEN_API_SECRET", "")
KRAKEN_URL        = "https://api.kraken.com"

# ═══ SAFETY FEATURES ══════════════════════════════════════════════════════════
MAX_DAILY_LOSS = 10.0
DAILY_PROFIT_TARGET = 50.0
daily_loss = 0.0
daily_profit = 0.0
last_reset_day = None
MAX_POSITIONS = 4

# ═══ TELEGRAM CONFIG ═════════════════════════════════════════════════════════
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ═══ DUAL STRATEGY CONFIG – $50/DAY EDITION ═══════════════════════════════════
STRATEGIES = {
    "SCALP": {
        "rsi_interval": 1,
        "rsi_buy": 45,
        "rsi_sell": 55,
        "tp": 0.01,
        "sl": 0.003,
        "trade_size": 100,
        "max_hold_hours": 1,
        "label": "⚡ Scalping",
        "color": "#f0b90b"
    },
    "TREND": {
        "rsi_interval": 240,
        "rsi_buy": 45,
        "rsi_sell": 75,
        "tp": 0.05,
        "sl": 0.04,
        "trade_size": 200,
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
scalp_balance    = 10000.0
trend_balance    = 10000.0
price_cache      = {}
last_price_cache = {}
rsi_cache        = {}
last_rsi_cache   = {}
active_strategy  = "SCALP"

# Persistent storage path
STATE_PATH = "/data/state.json"
BACKUP_PATH = "/data/state_backup.json"
if not os.path.exists("/data"):
    STATE_PATH = "/tmp/state.json"
    BACKUP_PATH = "/tmp/state_backup.json"
    log.warning("⚠️ No /data volume found, using /tmp (ephemeral)")

session = requests.Session()
retries = Retry(total=5, backoff_factor=2, status_forcelist=[502, 503, 504])
session.mount('https://', HTTPAdapter(max_retries=retries))

# ═══ TELEGRAM ════════════════════════════════════════════════════════════════
def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        requests.post(url, data=data, timeout=5)
    except Exception as e:
        log.error(f"Telegram error: {e}")

# ═══ SAFETY CHECKS (basic: only emergency & daily limits) ════════════════════
def check_safety_limits_basic():
    global daily_loss, daily_profit, last_reset_day
    if os.path.exists("/tmp/STOP_TRADING"):
        log.warning("🛑 Emergency stop file detected – trading paused")
        return False
    today = date.today().isoformat()
    if last_reset_day != today:
        daily_loss = 0.0
        daily_profit = 0.0
        last_reset_day = today
        log.info(f"📅 Daily counters reset – Loss: ${daily_loss:.2f} | Profit target: ${DAILY_PROFIT_TARGET}")
        send_telegram(f"📅 <b>New trading day</b>\nProfit target: ${DAILY_PROFIT_TARGET}\nLoss limit: ${MAX_DAILY_LOSS}")
    total_pnl = scalp_stats["pnl"] + trend_stats["pnl"]
    if total_pnl >= DAILY_PROFIT_TARGET:
        log.info(f"🎯 Daily profit target reached! ${total_pnl:.2f} >= ${DAILY_PROFIT_TARGET} – stopping trades")
        send_telegram(f"🎯 <b>Daily profit target reached!</b>\nProfit: ${total_pnl:.2f}\nTrading paused until midnight")
        return False
    total_today_loss = abs(min(0, total_pnl - (scalp_stats.get("pnl_yesterday", 0) + trend_stats.get("pnl_yesterday", 0))))
    if total_today_loss > MAX_DAILY_LOSS:
        log.warning(f"🛑 Daily loss limit reached (${total_today_loss:.2f} > ${MAX_DAILY_LOSS}) – stopping trades")
        send_telegram(f"⚠️ <b>Daily loss limit reached</b>\nLoss: ${total_today_loss:.2f}\nTrading paused until midnight")
        return False
    return True

# ═══ MANUAL CLOSE FUNCTION ═══════════════════════════════════════════════════
def close_all_positions():
    """Close all open positions (paper mode only)"""
    if TRADING_MODE != "paper":
        log.warning("Manual close attempted in live mode – ignored")
        return
    log.info("🛑 Manual close initiated")
    # Close scalp positions
    for symbol, pos in list(scalp_positions.items()):
        price = price_cache.get(symbol, pos["entry"])
        pnl = pos["qty"] * (price - pos["entry"])
        paper_sell_scalp(symbol, price, pos["qty"], pnl)
        del scalp_positions[symbol]
    # Close trend positions
    for symbol, pos in list(trend_positions.items()):
        price = price_cache.get(symbol, pos["entry"])
        pnl = pos["qty"] * (price - pos["entry"])
        paper_sell_trend(symbol, price, pos["qty"], pnl)
        del trend_positions[symbol]
    save_state()
    log.info("✅ All positions closed manually")
    send_telegram("🛑 <b>Manual close executed</b>\nAll positions closed.")

# ═══ PRICES & INDICATORS ═════════════════════════════════════════════════════
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
    if len(prices) < 15: return None
    recent = prices[-15:]
    gains = losses = 0
    for i in range(1, len(recent)):
        d = recent[i] - recent[i-1]
        if d > 0: gains += d
        else: losses += abs(d)
    ag, al = gains/14, losses/14
    if al == 0: return 100
    return round(100 - 100/(1 + ag/al), 2)

def calculate_bollinger_bands(prices, period=20, std_dev=2):
    if len(prices) < period:
        return None, None, None
    recent = prices[-period:]
    middle_band = sum(recent) / period
    variance = sum((x - middle_band) ** 2 for x in recent) / period
    std = variance ** 0.5
    lower_band = middle_band - (std_dev * std)
    upper_band = middle_band + (std_dev * std)
    return lower_band, middle_band, upper_band

def is_lower_band_touch(price, prices, threshold_pct=0.01):
    lower_band, middle, upper = calculate_bollinger_bands(prices)
    if lower_band is None:
        return False
    if price <= lower_band * (1 + threshold_pct):
        return True
    return False

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
def paper_buy_scalp(symbol, price):
    global scalp_balance
    qty = round(STRATEGIES["SCALP"]["trade_size"] / price, 6)
    cost = qty * price
    if scalp_balance < cost:
        log.warning(f"Insufficient scalp balance for {symbol}")
        return None
    scalp_balance -= cost
    log.info(f"📄 ⚡ SCALP BUY {symbol} qty={qty} @ ${price:,.2f} | Scalp Balance: ${scalp_balance:,.2f}")
    send_telegram(f"🟢 <b>SCALP BUY</b>\n{symbol} @ ${price:,.2f}\nQty: {qty}\nBalance: ${scalp_balance:,.2f}")
    return qty

def paper_sell_scalp(symbol, price, qty, pnl=None):
    global scalp_balance
    scalp_balance += qty * price
    pnl_text = f" | PnL: ${pnl:+.2f}" if pnl is not None else ""
    log.info(f"📄 ⚡ SCALP SELL {symbol} qty={qty} @ ${price:,.2f}{pnl_text} | Scalp Balance: ${scalp_balance:,.2f}")
    if pnl is not None:
        emoji = "✅" if pnl >= 0 else "🔴"
        send_telegram(f"{emoji} <b>SCALP SELL</b>\n{symbol} @ ${price:,.2f}\nPnL: ${pnl:+.2f}\nBalance: ${scalp_balance:,.2f}")

def paper_buy_trend(symbol, price):
    global trend_balance
    qty = round(STRATEGIES["TREND"]["trade_size"] / price, 6)
    cost = qty * price
    if trend_balance < cost:
        log.warning(f"Insufficient trend balance for {symbol}")
        return None
    trend_balance -= cost
    log.info(f"📄 📈 TREND BUY {symbol} qty={qty} @ ${price:,.2f} | Trend Balance: ${trend_balance:,.2f}")
    send_telegram(f"🟢 <b>TREND BUY</b>\n{symbol} @ ${price:,.2f}\nQty: {qty}\nBalance: ${trend_balance:,.2f}")
    return qty

def paper_sell_trend(symbol, price, qty, pnl=None):
    global trend_balance
    trend_balance += qty * price
    pnl_text = f" | PnL: ${pnl:+.2f}" if pnl is not None else ""
    log.info(f"📄 📈 TREND SELL {symbol} qty={qty} @ ${price:,.2f}{pnl_text} | Trend Balance: ${trend_balance:,.2f}")
    if pnl is not None:
        emoji = "✅" if pnl >= 0 else "🔴"
        send_telegram(f"{emoji} <b>TREND SELL</b>\n{symbol} @ ${price:,.2f}\nPnL: ${pnl:+.2f}\nBalance: ${trend_balance:,.2f}")

def get_balances():
    if TRADING_MODE == "live":
        total = kraken_get_balance()
        return total, total, total
    return scalp_balance, trend_balance, scalp_balance + trend_balance

# ═══ EXIT CHECKS (always run) ═════════════════════════════════════════════════
def run_exits(strategy_name, cfg, positions, trades, stats, sell_func):
    for s in SYMBOLS:
        symbol = s["symbol"]
        try:
            price = price_cache.get(symbol)
            if not price: continue
            pos = positions.get(symbol)
            if not pos: continue

            rsi = get_rsi(s["kraken_ohlc"], symbol, cfg["rsi_interval"])
            pct = (price - pos["entry"]) / pos["entry"]
            reason = None

            if pct >= cfg["tp"]:
                reason = f"Take Profit +{pct*100:.2f}%"
            elif pct <= -cfg["sl"]:
                reason = f"Stop Loss {pct*100:.2f}%"
            elif rsi is not None and rsi > cfg["rsi_sell"]:
                reason = f"RSI Exit {rsi}"

            # Time-based exit
            if strategy_name == "SCALP" and "max_hold_hours" in cfg:
                hold_time = datetime.now(timezone.utc) - datetime.fromisoformat(pos["time"])
                if hold_time.total_seconds() > cfg["max_hold_hours"] * 3600:
                    reason = f"Time exit ({int(hold_time.total_seconds()/3600)}h)"

            if reason:
                is_win = pct >= 0
                if TRADING_MODE == "live":
                    kraken_place_order(s["kraken_order"], "sell", pos["qty"])
                else:
                    pnl = pos["qty"] * (price - pos["entry"])
                    sell_func(symbol, price, pos["qty"], pnl)
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

        except Exception as e:
            log.error(f"[{strategy_name}] Exit error {symbol}: {e}")

# ═══ ENTRY CHECKS (only if under max positions) ═══════════════════════════════
def run_entries(strategy_name, cfg, positions, trades, stats, buy_func):
    total_positions = len(scalp_positions) + len(trend_positions)
    if total_positions >= MAX_POSITIONS:
        return

    for s in SYMBOLS:
        symbol = s["symbol"]
        try:
            price = price_cache.get(symbol)
            if not price: continue
            if symbol in positions:
                continue

            rsi = get_rsi(s["kraken_ohlc"], symbol, cfg["rsi_interval"])
            entry_signal = False
            signal_reason = ""

            if rsi is not None and rsi < cfg["rsi_buy"]:
                entry_signal = True
                signal_reason = f"RSI {rsi} < {cfg['rsi_buy']}"

            if strategy_name == "SCALP" and not entry_signal:
                try:
                    r = session.get(
                        f"{KRAKEN_URL}/0/public/OHLC?pair={s['kraken_ohlc']}&interval={cfg['rsi_interval']}",
                        timeout=30
                    )
                    data = r.json()
                    if not data.get("error") and data["result"]:
                        result = data["result"]
                        key = [k for k in result.keys() if k != "last"][0]
                        closes = [float(c[4]) for c in result[key][-50:]]
                        if is_lower_band_touch(price, closes):
                            entry_signal = True
                            signal_reason = f"Bollinger touch ${price:,.2f}"
                except Exception as e:
                    log.error(f"Bollinger error {symbol}: {e}")

            if entry_signal:
                log.info(f"🎯 [{strategy_name}] BUY SIGNAL {symbol} | {signal_reason}")
                if TRADING_MODE == "live":
                    qty = round(cfg["trade_size"] / price, 6)
                    kraken_place_order(s["kraken_order"], "buy", qty)
                else:
                    qty = buy_func(symbol, price)
                    if qty is None: continue
                positions[symbol] = {"entry": price, "qty": qty, "time": datetime.now(timezone.utc).isoformat()}
                trades.append({
                    "symbol": f"{symbol}/USD", "side": "BUY",
                    "price": price, "qty": qty, "pnl": None,
                    "reason": signal_reason,
                    "strategy": strategy_name,
                    "time": datetime.now(timezone.utc).isoformat()
                })
                log.info(f"📈 [{strategy_name}] BUY {symbol} @ ${price:,.2f}")
                break  # one buy per tick
            else:
                log.info(f"⏳ [{strategy_name}] {symbol} RSI={rsi} — waiting for signal")

        except Exception as e:
            log.error(f"[{strategy_name}] Entry error {symbol}: {e}")

# ═══ SAVE/LOAD STATE ═════════════════════════════════════════════════════════
def save_state():
    try:
        scalp_bal, trend_bal, total_bal = get_balances()
        scalp_stats["pnl_yesterday"] = scalp_stats.get("pnl", 0)
        trend_stats["pnl_yesterday"] = trend_stats.get("pnl", 0)
        state = {
            "scalp": {
                "balance": scalp_bal,
                "positions": scalp_positions,
                "trades": scalp_trades[-50:],
                "stats": scalp_stats
            },
            "trend": {
                "balance": trend_bal,
                "positions": trend_positions,
                "trades": trend_trades[-50:],
                "stats": trend_stats
            },
            "total_balance": total_bal,
            "mode": TRADING_MODE,
            "active_strategy": active_strategy,
            "prices": price_cache,
            "daily_profit_target": DAILY_PROFIT_TARGET,
            "updated": datetime.now(timezone.utc).isoformat()
        }
        with open(STATE_PATH, "w") as f:
            json.dump(state, f)
        backup_name = f"/data/state_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(backup_name, "w") as f:
            json.dump(state, f)
        import glob
        backups = sorted(glob.glob("/data/state_*.json"))
        for old in backups[:-50]:
            os.remove(old)
    except Exception as e:
        log.error(f"save_state error: {e}")

def load_state():
    global scalp_balance, trend_balance, scalp_positions, trend_positions
    global scalp_trades, trend_trades, scalp_stats, trend_stats
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH) as f:
                state = json.load(f)
            scalp = state.get("scalp", {})
            trend = state.get("trend", {})
            scalp_balance = scalp.get("balance", 10000.0)
            scalp_positions = scalp.get("positions", {})
            scalp_trades = scalp.get("trades", [])
            scalp_stats = scalp.get("stats", {"pnl":0, "wins":0, "losses":0})
            trend_balance = trend.get("balance", 10000.0)
            trend_positions = trend.get("positions", {})
            trend_trades = trend.get("trades", [])
            trend_stats = trend.get("stats", {"pnl":0, "wins":0, "losses":0})
            log.info(f"✅ Loaded state: Scalp ${scalp_balance:,.2f} Trend ${trend_balance:,.2f}")
            return True
    except Exception as e:
        log.error(f"load_state error: {e}")
    return False

# ═══ BOT TICK – FIXED + MANUAL CLOSE CHECK ═══════════════════════════════════
def bot_tick():
    global rsi_cache, active_strategy
    rsi_cache = {}
    try:
        if os.path.exists("/tmp/active_strategy.txt"):
            with open("/tmp/active_strategy.txt") as f:
                active_strategy = f.read().strip()
    except: pass

    # Check for manual close signal
    if os.path.exists("/tmp/CLOSE_ALL"):
        log.info("🛑 Manual close signal detected")
        if TRADING_MODE == "paper":
            close_all_positions()
        else:
            log.warning("Manual close signal ignored in live mode")
        os.remove("/tmp/CLOSE_ALL")

    # Basic safety checks (no max positions check)
    if not check_safety_limits_basic():
        save_state()
        return

    scalp_bal, trend_bal, total_bal = get_balances()
    now = datetime.now(timezone.utc).strftime('%H:%M:%S')
    log.info(f"--- Tick {now} | {TRADING_MODE.upper()} | Total: ${total_bal:,.2f} (Scalp: ${scalp_bal:,.2f} Trend: ${trend_bal:,.2f}) | View: {active_strategy} ---")

    if not fetch_all_prices():
        log.error("No prices available")
        return

    # ALWAYS run exits
    run_exits("SCALP", STRATEGIES["SCALP"], scalp_positions, scalp_trades, scalp_stats, paper_sell_scalp)
    run_exits("TREND", STRATEGIES["TREND"], trend_positions, trend_trades, trend_stats, paper_sell_trend)

    # Only run entries if under max positions
    total_positions = len(scalp_positions) + len(trend_positions)
    if total_positions < MAX_POSITIONS:
        run_entries("SCALP", STRATEGIES["SCALP"], scalp_positions, scalp_trades, scalp_stats, paper_buy_scalp)
        run_entries("TREND", STRATEGIES["TREND"], trend_positions, trend_trades, trend_stats, paper_buy_trend)
    else:
        log.info(f"⏳ Max positions reached ({total_positions}/{MAX_POSITIONS}) – waiting for exits (entries blocked)")

    save_state()

# ═══ MAIN ════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    log.info(f"⚡📈 APEX BOT – $50/DAY EDITION (with manual close)")
    log.info(f"⚡ SCALP: RSI(1m) Buy<45 OR Bollinger | Sell>55 TP1% SL0.3% | Time exit 1h")
    log.info(f"📈 TREND: RSI(4h) Buy<45 Sell>75 TP5% SL4% $200")
    log.info(f"🎯 Daily profit target: ${DAILY_PROFIT_TARGET} | Daily loss limit: ${MAX_DAILY_LOSS} | Max positions: {MAX_POSITIONS}")
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        log.info(f"📱 Telegram alerts: ENABLED")
        send_telegram(f"🚀 <b>APEX BOT STARTED – $50/DAY EDITION</b>\nTP: 1% | SL: 0.3% | RSI sell: 55\nTime exit: 1h\nDaily target: ${DAILY_PROFIT_TARGET}")
    else:
        log.info(f"📱 Telegram alerts: DISABLED (set TELEGRAM_* env vars to enable)")

    if not load_state():
        log.info("No existing state found, starting fresh")

    if TRADING_MODE == "live":
        if not KRAKEN_API_KEY or not KRAKEN_API_SECRET:
            log.error("❌ Missing API keys!")
            exit(1)
        log.info(f"💰 Live Mode – Using real Kraken balance")
    else:
        log.info(f"💰 Paper Mode – Balances: Scalp ${scalp_balance:,.2f} Trend ${trend_balance:,.2f}")

    save_state()
    fetch_all_prices()

    while True:
        try:
            bot_tick()
        except Exception as e:
            log.error(f"Bot error: {e}")
            send_telegram(f"⚠️ <b>Bot error</b>\n{str(e)[:100]}")
        log.info("⏳ Sleeping 30s...")
        time.sleep(30)
