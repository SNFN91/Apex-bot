import os, time, hmac, hashlib, requests, json, logging, base64, urllib.parse
from datetime import datetime, timezone, date, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import numpy as np
from collections import deque
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

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

# ═══ VOLUME FILTER CONFIG (optional) ═════════════════════════════════════════
VOLUME_FILTER_ENABLED = False
VOLUME_MULTIPLIER = 2.0
VOLUME_PERIOD = 20

# ═══ TREND FILTER CONFIG ═════════════════════════════════════════════════════
TREND_FILTER_ENABLED = True
TREND_EMA_PERIOD = 20

# ═══ MARKET REGIME DETECTION (PHASE 1) ═══════════════════════════════════════
REGIME_DETECTION_ENABLED = True
REGIME_LOOKBACK = 30
VOLATILITY_THRESHOLD = 1.5
TREND_THRESHOLD = 0.5

# ═══ RSI CONFIG (NEW) ════════════════════════════════════════════════════════
RSI_PERIOD = 7                       # Shorter period for faster scalping

# ═══ DYNAMIC RSI ADAPTATION (PHASE 2) ════════════════════════════════════════
DYNAMIC_RSI_ENABLED = True
RSI_CANDIDATES = [18, 19, 20, 21, 22]   # Now centered around 20
DEFAULT_RSI_BUY = 20
RSI_PERFORMANCE_WINDOW = 50
rsi_performance = {val: {'wins': 0, 'losses': 0, 'total_pnl': 0.0, 'trades': []} for val in RSI_CANDIDATES}
current_rsi_buy = DEFAULT_RSI_BUY
rsi_adapt_counter = 0
RSI_ADAPT_FREQUENCY = 20

# ═══ HOURLY TELEGRAM SUMMARY (PHASE 2) ═══════════════════════════════════════
last_hour_summary = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
hourly_trades = []

# ═══ DUAL STRATEGY CONFIG – Scalp only BTC, Trend both ═══════════════════════
STRATEGIES = {
    "SCALP": {
        "rsi_interval": 1,
        "rsi_buy": current_rsi_buy,          # dynamic
        "rsi_sell": 80,                       # ⬅️ changed to 80
        "tp": 0.01,
        "sl": 0.003,
        "trade_size": 50,                      # ⬅️ reduced to $50 temporarily
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

# ═══ SYMBOLS – Scalp only BTC, Trend both ════════════════════════════════════
SYMBOLS = [
    {"symbol": "BTC", "kraken_ticker": "XXBTZUSD", "kraken_ohlc": "XBTUSD",  "kraken_order": "XXBTZUSD"},
    {"symbol": "ETH", "kraken_ticker": "XETHZUSD", "kraken_ohlc": "ETHUSD",  "kraken_order": "XETHZUSD"},
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

# ═══ COOLDOWN AFTER SCALP EXIT (NEW) ═════════════════════════════════════════
SCALP_COOLDOWN_SECONDS = 300   # 5 minutes
scalp_last_exit_time = {}       # symbol -> timestamp (UTC)

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

# ═══ SIMPLE HTTP SERVER FOR COMMANDS ═════════════════════════════════════════
class CommandHandler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    
    def do_GET(self):
        if self.path == "/reset_paper":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Reset command received")
            # Create flag file for reset
            with open("/tmp/RESET_PAPER", "w") as f:
                f.write("1")
        else:
            self.send_response(404)
            self.end_headers()

def start_command_server():
    server = HTTPServer(('0.0.0.0', 8081), CommandHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("🛠️ Command server running on port 8081")

# ═══ RESET FUNCTION ══════════════════════════════════════════════════════════
def reset_paper_account():
    """Reset paper trading account to fresh state."""
    global scalp_balance, trend_balance, scalp_positions, trend_positions
    global scalp_trades, trend_trades, scalp_stats, trend_stats
    global rsi_performance, current_rsi_buy, rsi_adapt_counter
    global hourly_trades, daily_loss, daily_profit, scalp_last_exit_time
    
    if TRADING_MODE != "paper":
        log.warning("Reset attempted in live mode – ignored")
        return
    
    log.info("🔄 Resetting paper account to fresh state")
    
    # Reset balances
    scalp_balance = 10000.0
    trend_balance = 10000.0
    
    # Clear all positions
    scalp_positions = {}
    trend_positions = {}
    
    # Clear trade history
    scalp_trades = []
    trend_trades = []
    hourly_trades = []
    
    # Reset stats
    scalp_stats = {"pnl": 0.0, "wins": 0, "losses": 0}
    trend_stats = {"pnl": 0.0, "wins": 0, "losses": 0}
    
    # Reset daily counters
    daily_loss = 0.0
    daily_profit = 0.0
    
    # Reset RSI learning
    current_rsi_buy = DEFAULT_RSI_BUY
    STRATEGIES["SCALP"]["rsi_buy"] = current_rsi_buy
    rsi_adapt_counter = 0
    rsi_performance = {val: {'wins': 0, 'losses': 0, 'total_pnl': 0.0, 'trades': []} for val in RSI_CANDIDATES}
    
    # Reset cooldown timers
    scalp_last_exit_time = {}
    
    save_state()
    log.info("✅ Paper account reset complete")
    send_telegram("🔄 <b>Paper account reset</b>\nFresh start with $10,000")

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

def send_hourly_summary():
    """Send a summary of the last hour's trading activity."""
    global hourly_trades, last_hour_summary
    if not hourly_trades:
        return
    total_trades = len(hourly_trades)
    wins = sum(1 for t in hourly_trades if t['pnl'] > 0)
    losses = sum(1 for t in hourly_trades if t['pnl'] < 0)
    net_pnl = sum(t['pnl'] for t in hourly_trades)
    win_rate = (wins / total_trades * 100) if total_trades else 0
    message = (
        f"📊 <b>Hourly Summary</b>\n"
        f"Trades: {total_trades} | Wins: {wins} | Losses: {losses}\n"
        f"Net P&L: ${net_pnl:.2f}\n"
        f"Win Rate: {win_rate:.1f}%\n"
        f"Open positions: {len(scalp_positions) + len(trend_positions)}\n"
        f"Current RSI buy: {current_rsi_buy}"
    )
    send_telegram(message)
    hourly_trades = []

def check_hourly_summary():
    global last_hour_summary
    now = datetime.now(timezone.utc)
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    if current_hour > last_hour_summary:
        send_hourly_summary()
        last_hour_summary = current_hour

# ═══ DYNAMIC RSI ADAPTATION ══════════════════════════════════════════════════
def update_rsi_performance(trade):
    global rsi_performance, rsi_adapt_counter, current_rsi_buy
    rsi_val = trade.get('rsi_buy_used')
    if rsi_val is None or rsi_val not in rsi_performance:
        return
    perf = rsi_performance[rsi_val]
    perf['trades'].append(trade)
    if len(perf['trades']) > RSI_PERFORMANCE_WINDOW:
        perf['trades'].pop(0)
    rsi_adapt_counter += 1
    if rsi_adapt_counter >= RSI_ADAPT_FREQUENCY:
        adapt_rsi_threshold()
        rsi_adapt_counter = 0

def adapt_rsi_threshold():
    global current_rsi_buy
    best_val = DEFAULT_RSI_BUY
    best_win_rate = -1
    for val, perf in rsi_performance.items():
        if len(perf['trades']) < 5:
            continue
        wins = sum(1 for t in perf['trades'] if t['pnl'] > 0)
        losses = sum(1 for t in perf['trades'] if t['pnl'] < 0)
        total = wins + losses
        if total == 0:
            continue
        win_rate = wins / total
        if win_rate > best_win_rate:
            best_win_rate = win_rate
            best_val = val
    if best_val != current_rsi_buy:
        log.info(f"🔄 Adapting RSI buy threshold from {current_rsi_buy} to {best_val} (win rate {best_win_rate:.2%})")
        current_rsi_buy = best_val
        STRATEGIES["SCALP"]["rsi_buy"] = current_rsi_buy
        send_telegram(f"🔄 RSI buy threshold changed to {current_rsi_buy}")

# ═══ SAFETY CHECKS ═══════════════════════════════════════════════════════════
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

# ═══ CLOSE ALL POSITIONS ═════════════════════════════════════════════════════
def close_all_positions():
    log.info("🛑 Manual close initiated")
    
    # Close all scalp positions
    for symbol, pos in list(scalp_positions.items()):
        price = price_cache.get(symbol, pos["entry"])
        pnl = pos["qty"] * (price - pos["entry"])
        paper_sell_scalp(symbol, price, pos["qty"], pnl)
        del scalp_positions[symbol]
    
    # Close all trend positions
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
        r = session.get(
            f"{KRAKEN_URL}/0/public/OHLC?pair={kraken_ohlc}&interval={interval}",
            timeout=30
        )
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
    """Calculate RSI using global RSI_PERIOD."""
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

def volume_spike_detected(kraken_ohlc, interval):
    try:
        r = session.get(
            f"{KRAKEN_URL}/0/public/OHLC?pair={kraken_ohlc}&interval={interval}",
            timeout=30
        )
        data = r.json()
        if data.get("error") or not data["result"]:
            return False, 0, 0
        result = data["result"]
        key = [k for k in result.keys() if k != "last"][0]
        candles = result[key]
        if len(candles) < VOLUME_PERIOD + 1:
            return False, 0, 0
        volumes = [float(c[6]) for c in candles[-VOLUME_PERIOD-1:]]
        current_volume = volumes[-1]
        avg_volume = sum(volumes[:-1]) / VOLUME_PERIOD
        spike = current_volume > avg_volume * VOLUME_MULTIPLIER
        return spike, current_volume, avg_volume
    except Exception as e:
        log.error(f"Volume filter error: {e}")
        return False, 0, 0

def price_above_ema(kraken_ohlc, symbol, interval, current_price):
    try:
        r = session.get(
            f"{KRAKEN_URL}/0/public/OHLC?pair={kraken_ohlc}&interval={interval}",
            timeout=30
        )
        data = r.json()
        if data.get("error") or not data["result"]:
            return False
        result = data["result"]
        key = [k for k in result.keys() if k != "last"][0]
        closes = [float(c[4]) for c in result[key][-TREND_EMA_PERIOD:]]
        if len(closes) < TREND_EMA_PERIOD:
            return False
        sma = sum(closes) / TREND_EMA_PERIOD
        return current_price > sma
    except Exception as e:
        log.error(f"Trend filter error: {e}")
        return False

def detect_market_regime(kraken_ohlc, interval):
    try:
        r = session.get(
            f"{KRAKEN_URL}/0/public/OHLC?pair={kraken_ohlc}&interval={interval}",
            timeout=30
        )
        data = r.json()
        if data.get("error") or not data["result"]:
            return 'ranging'
        result = data["result"]
        key = [k for k in result.keys() if k != "last"][0]
        candles = result[key]
        if len(candles) < REGIME_LOOKBACK:
            return 'ranging'
        closes = np.array([float(c[4]) for c in candles[-REGIME_LOOKBACK:]])
        returns = np.diff(closes) / closes[:-1]
        volatility = np.std(returns)
        momentum = (closes[-1] - closes[0]) / closes[0] * 100
        if len(closes) > REGIME_LOOKBACK + 5:
            past_vol = np.std(np.diff(closes[-REGIME_LOOKBACK-5:-5]) / closes[-REGIME_LOOKBACK-5:-5])
        else:
            past_vol = volatility
        if volatility > past_vol * VOLATILITY_THRESHOLD:
            return 'volatile'
        elif momentum > TREND_THRESHOLD:
            return 'trending_up'
        elif momentum < -TREND_THRESHOLD:
            return 'trending_down'
        else:
            return 'ranging'
    except Exception as e:
        log.error(f"Regime detection error: {e}")
        return 'ranging'

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
    # Only allow BTC for scalp
    if symbol != "BTC":
        return None
    qty = round(STRATEGIES["SCALP"]["trade_size"] / price, 6)
    cost = qty * price
    if scalp_balance < cost:
        log.warning(f"Insufficient scalp balance for {symbol}")
        return None
    scalp_balance -= cost
    log.info(f"📄 ⚡ SCALP BUY {symbol} qty={qty} @ ${price:,.2f} | Scalp Balance: ${scalp_balance:,.2f}")
    return qty

def paper_sell_scalp(symbol, price, qty, pnl=None):
    global scalp_balance, hourly_trades, scalp_last_exit_time
    if symbol != "BTC":
        return
    scalp_balance += qty * price
    pnl_text = f" | PnL: ${pnl:+.2f}" if pnl is not None else ""
    log.info(f"📄 ⚡ SCALP SELL {symbol} qty={qty} @ ${price:,.2f}{pnl_text} | Scalp Balance: ${scalp_balance:,.2f}")
    if pnl is not None:
        trade_record = {
            'symbol': symbol,
            'pnl': pnl,
            'rsi_buy_used': STRATEGIES["SCALP"]["rsi_buy"],
            'time': datetime.now(timezone.utc).isoformat()
        }
        hourly_trades.append(trade_record)
        update_rsi_performance(trade_record)
        # Record exit time for cooldown
        scalp_last_exit_time[symbol] = time.time()

def paper_buy_trend(symbol, price):
    global trend_balance
    qty = round(STRATEGIES["TREND"]["trade_size"] / price, 6)
    cost = qty * price
    if trend_balance < cost:
        log.warning(f"Insufficient trend balance for {symbol}")
        return None
    trend_balance -= cost
    log.info(f"📄 📈 TREND BUY {symbol} qty={qty} @ ${price:,.2f} | Trend Balance: ${trend_balance:,.2f}")
    return qty

def paper_sell_trend(symbol, price, qty, pnl=None):
    global trend_balance
    trend_balance += qty * price
    pnl_text = f" | PnL: ${pnl:+.2f}" if pnl is not None else ""
    log.info(f"📄 📈 TREND SELL {symbol} qty={qty} @ ${price:,.2f}{pnl_text} | Trend Balance: ${trend_balance:,.2f}")

def get_balances():
    if TRADING_MODE == "live":
        total = kraken_get_balance()
        return total, total, total
    return scalp_balance, trend_balance, scalp_balance + trend_balance

# ═══ EXIT CHECKS ═════════════════════════════════════════════════════════════
def run_exits(strategy_name, cfg, positions, trades, stats, sell_func):
    for s in SYMBOLS:
        symbol = s["symbol"]
        # For scalp, only process BTC
        if strategy_name == "SCALP" and symbol != "BTC":
            continue
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

            if strategy_name == "SCALP" and "max_hold_hours" in cfg:
                hold_time = datetime.now(timezone.utc) - datetime.fromisoformat(pos["time"])
                if hold_time.total_seconds() > cfg["max_hold_hours"] * 3600:
                    reason = f"Time exit ({int(hold_time.total_seconds()/3600)}h)"

            if reason:
                is_win = pct >= 0
                # Calculate pnl before using it
                pnl = pos["qty"] * (price - pos["entry"])
                if TRADING_MODE == "live":
                    kraken_place_order(s["kraken_order"], "sell", pos["qty"])
                else:
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

# ═══ ENTRY CHECKS ════════════════════════════════════════════════════════════
def run_entries(strategy_name, cfg, positions, trades, stats, buy_func):
    total_positions = len(scalp_positions) + len(trend_positions)
    if total_positions >= MAX_POSITIONS:
        return

    for s in SYMBOLS:
        symbol = s["symbol"]
        # For scalp, only process BTC
        if strategy_name == "SCALP" and symbol != "BTC":
            continue
        try:
            price = price_cache.get(symbol)
            if not price: continue
            if symbol in positions:
                continue

            # --- Cooldown check for scalp ---
            if strategy_name == "SCALP":
                last_exit = scalp_last_exit_time.get(symbol)
                if last_exit is not None:
                    seconds_since_exit = time.time() - last_exit
                    if seconds_since_exit < SCALP_COOLDOWN_SECONDS:
                        log.info(f"⏳ [{strategy_name}] {symbol} in cooldown ({seconds_since_exit:.0f}s < {SCALP_COOLDOWN_SECONDS}s) – skipping entry")
                        continue

            regime = detect_market_regime(s["kraken_ohlc"], cfg["rsi_interval"])
            log.info(f"[{strategy_name}] {symbol} market regime: {regime}")

            if strategy_name == "SCALP" and regime == 'trending_down' and REGIME_DETECTION_ENABLED:
                log.info(f"⏳ [{strategy_name}] {symbol} skipping entry due to downtrend regime")
                continue

            rsi = get_rsi(s["kraken_ohlc"], symbol, cfg["rsi_interval"])
            entry_signal = False
            signal_reason = ""

            # Signal 1: RSI – uses current_rsi_buy (dynamic)
            if rsi is not None and rsi < cfg["rsi_buy"]:
                entry_signal = True
                signal_reason = f"RSI {rsi} < {cfg['rsi_buy']}"

            # Signal 2: Bollinger touch (scalp only) – ENFORCE BTC ONLY
            if strategy_name == "SCALP" and not entry_signal and symbol == "BTC":
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

            # Trend filter
            if entry_signal and strategy_name == "SCALP" and TREND_FILTER_ENABLED:
                if not price_above_ema(s["kraken_ohlc"], symbol, cfg["rsi_interval"], price):
                    log.info(f"⏳ [{strategy_name}] {symbol} signal blocked by trend filter (price below EMA)")
                    continue
                else:
                    signal_reason += " | Trend up"

            # Volume filter
            if entry_signal and strategy_name == "SCALP" and VOLUME_FILTER_ENABLED:
                spike, curr_vol, avg_vol = volume_spike_detected(s["kraken_ohlc"], cfg["rsi_interval"])
                if not spike:
                    log.info(f"⏳ [{strategy_name}] {symbol} signal blocked by volume filter (curr={curr_vol:.0f}, avg={avg_vol:.0f})")
                    continue
                else:
                    signal_reason += f" | Volume spike ({curr_vol/avg_vol:.1f}x)"

            if entry_signal:
                trade_size = cfg["trade_size"]
                if regime == 'volatile' and REGIME_DETECTION_ENABLED:
                    trade_size = int(trade_size * 0.5)
                    log.info(f"[{strategy_name}] {symbol} volatile regime – reducing trade size to ${trade_size}")

                log.info(f"🎯 [{strategy_name}] BUY SIGNAL {symbol} | {signal_reason}")
                if TRADING_MODE == "live":
                    qty = round(trade_size / price, 6)
                    kraken_place_order(s["kraken_order"], "buy", qty)
                else:
                    if trade_size != cfg["trade_size"]:
                        qty = round(trade_size / price, 6)
                        cost = qty * price
                        if scalp_balance < cost:
                            log.warning(f"Insufficient scalp balance for {symbol} with reduced size")
                            continue
                        scalp_balance -= cost
                        log.info(f"📄 ⚡ SCALP BUY {symbol} qty={qty} @ ${price:,.2f} (volatile size) | Scalp Balance: ${scalp_balance:,.2f}")
                        qty_actual = qty
                    else:
                        qty_actual = buy_func(symbol, price)
                        if qty_actual is None: continue
                positions[symbol] = {"entry": price, "qty": qty_actual, "time": datetime.now(timezone.utc).isoformat()}
                trades.append({
                    "symbol": f"{symbol}/USD", "side": "BUY",
                    "price": price, "qty": qty_actual, "pnl": None,
                    "reason": signal_reason,
                    "strategy": strategy_name,
                    "time": datetime.now(timezone.utc).isoformat()
                })
                log.info(f"📈 [{strategy_name}] BUY {symbol} @ ${price:,.2f} (size: {qty_actual})")
                break
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
            "rsi_performance": rsi_performance,
            "current_rsi_buy": current_rsi_buy,
            "scalp_last_exit_time": scalp_last_exit_time,
            "updated": datetime.now(timezone.utc).isoformat()
        }
        with open(STATE_PATH, "w") as f:
            json.dump(state, f)
        
        # Only create backups if /data directory exists
        if os.path.exists("/data"):
            import glob
            backup_name = f"/data/state_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(backup_name, "w") as f:
                json.dump(state, f)
            backups = sorted(glob.glob("/data/state_*.json"))
            for old in backups[:-50]:
                os.remove(old)
    except Exception as e:
        log.error(f"save_state error: {e}")

def load_state():
    global scalp_balance, trend_balance, scalp_positions, trend_positions
    global scalp_trades, trend_trades, scalp_stats, trend_stats
    global rsi_performance, current_rsi_buy, scalp_last_exit_time
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
            rsi_performance = state.get("rsi_performance", rsi_performance)
            # Ensure loaded RSI value is always one of the candidates
            loaded_rsi = state.get("current_rsi_buy", DEFAULT_RSI_BUY)
            current_rsi_buy = loaded_rsi if loaded_rsi in RSI_CANDIDATES else DEFAULT_RSI_BUY
            STRATEGIES["SCALP"]["rsi_buy"] = current_rsi_buy
            # Load cooldown timers
            scalp_last_exit_time = state.get("scalp_last_exit_time", {})
            log.info(f"✅ Loaded state: Scalp ${scalp_balance:,.2f} Trend ${trend_balance:,.2f} | RSI buy: {current_rsi_buy}")
            return True
    except Exception as e:
        log.error(f"load_state error: {e}")
    return False

# ═══ BOT TICK ════════════════════════════════════════════════════════════════
def bot_tick():
    global rsi_cache, active_strategy
    rsi_cache = {}
    
    # Check for reset flag
    if os.path.exists("/tmp/RESET_PAPER"):
        reset_paper_account()
        os.remove("/tmp/RESET_PAPER")
    
    try:
        if os.path.exists("/tmp/active_strategy.txt"):
            with open("/tmp/active_strategy.txt") as f:
                active_strategy = f.read().strip()
    except: pass

    # Manual close signal (now works in all modes)
    if os.path.exists("/tmp/CLOSE_ALL"):
        log.info("🛑 Manual close signal detected")
        close_all_positions()
        os.remove("/tmp/CLOSE_ALL")

    if not check_safety_limits_basic():
        save_state()
        return

    check_hourly_summary()

    scalp_bal, trend_bal, total_bal = get_balances()
    now = datetime.now(timezone.utc).strftime('%H:%M:%S')
    log.info(f"--- Tick {now} | {TRADING_MODE.upper()} | Total: ${total_bal:,.2f} (Scalp: ${scalp_bal:,.2f} Trend: ${trend_bal:,.2f}) | View: {active_strategy} | RSI buy: {current_rsi_buy} ---")

    if not fetch_all_prices():
        log.error("No prices available")
        return

    run_exits("SCALP", STRATEGIES["SCALP"], scalp_positions, scalp_trades, scalp_stats, paper_sell_scalp)
    run_exits("TREND", STRATEGIES["TREND"], trend_positions, trend_trades, trend_stats, paper_sell_trend)

    total_positions = len(scalp_positions) + len(trend_positions)
    if total_positions < MAX_POSITIONS:
        run_entries("SCALP", STRATEGIES["SCALP"], scalp_positions, scalp_trades, scalp_stats, paper_buy_scalp)
        run_entries("TREND", STRATEGIES["TREND"], trend_positions, trend_trades, trend_stats, paper_buy_trend)
    else:
        log.info(f"⏳ Max positions reached ({total_positions}/{MAX_POSITIONS}) – waiting for exits (entries blocked)")

    save_state()

# ═══ MAIN ════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    log.info(f"⚡📈 APEX BOT – BTC SCALP ONLY, RSI 20/80, $50 TRADE SIZE")
    log.info(f"⚡ SCALP: BTC only, RSI({RSI_PERIOD}) Buy<{current_rsi_buy} OR Bollinger | Sell>80 TP1% SL0.3% | Time exit 1h | Trend filter: {TREND_FILTER_ENABLED}")
    if REGIME_DETECTION_ENABLED:
        log.info(f"🧠 Market regime detection: ON")
    if DYNAMIC_RSI_ENABLED:
        log.info(f"📈 Dynamic RSI adaptation: ON (candidates: {RSI_CANDIDATES})")
    if VOLUME_FILTER_ENABLED:
        log.info(f"📊 Volume filter: ON")
    log.info(f"📈 TREND: BTC/ETH, RSI(4h) Buy<45 Sell>75 TP5% SL4% $200")
    log.info(f"🎯 Daily profit target: ${DAILY_PROFIT_TARGET} | Daily loss limit: ${MAX_DAILY_LOSS} | Max positions: {MAX_POSITIONS}")
    log.info(f"📱 Telegram hourly summaries: ENABLED")
    log.info(f"🔄 Reset endpoint: http://your-bot:8081/reset_paper")
    send_telegram(f"🚀 <b>APEX BOT – BTC SCALP ONLY</b>\nRSI 20/80, $50 trades\nDynamic adaptation ON\nHourly summaries")

    # Start command server
    start_command_server()

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
