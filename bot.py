import os, time, hmac, hashlib, requests, json, logging, base64, urllib.parse
from datetime import datetime, timezone, date, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import numpy as np
from collections import deque
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import pandas_ta as ta

# TA-Lib import (optional)
try:
    import talib
    TALIB_AVAILABLE = True
except ImportError:
    TALIB_AVAILABLE = False

# ML imports
try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import StandardScaler
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    log = logging.getLogger(__name__)
    log.warning("⚠️ scikit-learn not installed. ML predictor will be disabled.")

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger(__name__)

# ═══ CONFIG ═══════════════════════════════════════════════════════════════════
TRADING_MODE      = os.environ.get("TRADING_MODE", "paper")
KRAKEN_API_KEY    = os.environ.get("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = os.environ.get("KRAKEN_API_SECRET", "")
KRAKEN_URL        = "https://api.kraken.com"
KRAKEN_TAKER_FEE  = 0.0026  # 0.26% taker fee for fee simulation

# ═══ SAFETY FEATURES ══════════════════════════════════════════════════════════
MAX_DAILY_LOSS = 10.0
DAILY_PROFIT_TARGET = 50.0
DAILY_LOSS_PCT_LIMIT = 0.03          # Stop trading if down 3% on the day (Fix 4)
DAILY_PROFIT_PCT_TARGET = 0.02       # Halve position size if up 2% on the day (Fix 5)
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

# ═══ ML PREDICTOR (PHASE 3) ══════════════════════════════════════════════════
ML_ENABLED = True
ML_CONFIDENCE_THRESHOLD = 0.60
ML_MIN_TRADES = 30
ml_model = None
ml_scaler = None
ml_trained = False
ml_last_training_trades = 0

# ═══ VWAP FILTER (PHASE 3.5) ═════════════════════════════════════════════════
VWAP_FILTER_ENABLED = True          # Can be toggled
vwap_cache = {}                     # symbol -> {'value': float, 'timestamp': float}
VWAP_CACHE_SECONDS = 300            # Refresh every 5 minutes

# ═══ ATR CONFIG (UPGRADE 2) ═════════════════════════════════════════════════
atr_cache = {}                      # symbol -> {'value': float, 'timestamp': float}
ATR_CACHE_SECONDS = 300             # Refresh every 5 minutes
ATR_PERIOD = 14

def regime_to_int(regime):
    """Convert regime string to integer for ML."""
    mapping = {
        'ranging': 0,
        'trending_up': 1,
        'trending_down': 2,
        'volatile': 3
    }
    return mapping.get(regime, 0)

def prepare_ml_features(trade_data):
    """Extract features from trade record (7 features)."""
    return [
        trade_data.get('rsi_at_entry', 50),           # RSI value at entry
        trade_data.get('bb_distance', 0),             # Distance from lower BB as %
        trade_data.get('regime', 0),                  # Market regime (0-3)
        trade_data.get('volume_ratio', 1.0),          # Volume ratio (current/avg)
        trade_data.get('vwap_distance', 0),           # Distance from VWAP as %
        trade_data.get('atr_value', 0),               # ATR value (Upgrade 2)
        trade_data.get('vwap_deviation_pct', 0)       # VWAP deviation % (Upgrade 3)
    ]

def train_ml_model():
    """Train Random Forest classifier on scalp trade history."""
    global ml_model, ml_scaler, ml_trained, ml_last_training_trades
    
    if not SKLEARN_AVAILABLE:
        log.warning("⚠️ scikit-learn not available. ML predictor disabled.")
        return False
    
    # Collect all scalp trades with features AND pnl populated
    trade_features = []
    trade_labels = []
    
    for trade in scalp_trades:
        if (trade.get('rsi_at_entry') is not None and 
            trade.get('pnl') is not None and
            trade.get('strategy') == "SCALP"):
            features = prepare_ml_features(trade)
            trade_features.append(features)
            # WIN = pnl > 0 (profitable trade)
            trade_labels.append(1 if trade['pnl'] > 0 else 0)
    
    if len(trade_features) < ML_MIN_TRADES:
        log.info(f"📊 ML: Not enough trades ({len(trade_features)}/{ML_MIN_TRADES}) – waiting for more data")
        return False
    
    # Train only if we have new trades since last training
    if ml_trained and len(trade_features) == ml_last_training_trades:
        return True
    
    try:
        X = np.array(trade_features)
        y = np.array(trade_labels)
        
        # Scale features
        ml_scaler = StandardScaler()
        X_scaled = ml_scaler.fit_transform(X)
        
        # Train Random Forest
        ml_model = RandomForestClassifier(
            n_estimators=100,
            max_depth=5,
            min_samples_split=5,
            random_state=42,
            n_jobs=-1
        )
        ml_model.fit(X_scaled, y)
        
        ml_trained = True
        ml_last_training_trades = len(trade_features)
        
        # Log training results
        accuracy = ml_model.score(X_scaled, y)
        log.info(f"🤖 ML: Trained Random Forest on {len(trade_features)} trades | Accuracy: {accuracy:.2%}")
        send_telegram(f"🤖 <b>ML Predictor Trained</b>\nTrades: {len(trade_features)}\nAccuracy: {accuracy:.2%}\nMin confidence: {ML_CONFIDENCE_THRESHOLD:.0%}")
        
        return True
    except Exception as e:
        log.error(f"ML training error: {e}")
        return False

def predict_trade_profit(rsi_value, bb_distance, regime_str, volume_ratio, vwap_distance, atr_value=0, vwap_deviation_pct=0):
    """Predict if trade will be profitable. Returns (confidence, should_trade)."""
    # FIX 1: add ml_scaler is None guard
    if not ML_ENABLED or not SKLEARN_AVAILABLE or not ml_trained or ml_scaler is None:
        return 0.5, True  # Default: allow trade if ML not ready
    
    try:
        regime_int = regime_to_int(regime_str)
        features = [[rsi_value, bb_distance, regime_int, volume_ratio, vwap_distance, atr_value, vwap_deviation_pct]]
        
        # Scale features
        features_scaled = ml_scaler.transform(features)
        
        # Get probability of positive outcome
        prob = ml_model.predict_proba(features_scaled)[0]
        confidence = prob[1]  # Probability of class 1 (win)
        
        should_trade = confidence >= ML_CONFIDENCE_THRESHOLD
        return confidence, should_trade
    except Exception as e:
        log.error(f"ML prediction error: {e}")
        return 0.5, True  # Default to allow trade on error

# ═══ HOURLY TELEGRAM SUMMARY (PHASE 2) ═══════════════════════════════════════
last_hour_summary = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
hourly_trades = []

def check_hourly_summary():
    global last_hour_summary
    now = datetime.now(timezone.utc)
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    if current_hour > last_hour_summary:
        send_hourly_summary()
        last_hour_summary = current_hour

# FIX 2: Add trading session filter
def is_valid_trading_session():
    hour = datetime.now(timezone.utc).hour
    london_open = 7 <= hour <= 12
    ny_open = 13 <= hour <= 17
    return london_open or ny_open

# FIX 3: Fee‑aware risk‑reward gate
def trade_is_fee_viable(tp, sl):
    round_trip_fee = KRAKEN_TAKER_FEE * 2  # 0.52%
    net_reward = tp - round_trip_fee
    net_risk = sl + round_trip_fee
    if net_risk == 0:
        return False
    rr_ratio = net_reward / net_risk
    # FIX: updated threshold from 1.5 to 0.8
    return rr_ratio >= 0.8

# FIX 5: Profit scale‑down multiplier
def get_profit_scale_multiplier():
    starting_balance = 20000.0
    total_balance = scalp_balance + trend_balance
    daily_pnl_pct = (total_balance - starting_balance) / starting_balance
    if daily_pnl_pct >= DAILY_PROFIT_PCT_TARGET:
        log.info(f"✅ Daily profit target hit ({daily_pnl_pct:.2%}) – reducing position size to 50%")
        return 0.5
    return 1.0

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
    ml_status = "Active" if ml_trained else f"Training ({len(scalp_trades)}/{ML_MIN_TRADES})"
    message = (
        f"📊 <b>Hourly Summary</b>\n"
        f"Trades: {total_trades} | Wins: {wins} | Losses: {losses}\n"
        f"Net P&L: ${net_pnl:.2f}\n"
        f"Win Rate: {win_rate:.1f}%\n"
        f"Open positions: {len(scalp_positions) + len(trend_positions)}\n"
        f"Current RSI buy: {current_rsi_buy}\n"
        f"🤖 ML: {ml_status}"
    )
    send_telegram(message)
    hourly_trades = []

# ═══ DUAL STRATEGY CONFIG – Scalp only BTC, Trend both ═══════════════════════
STRATEGIES = {
    "SCALP": {
        "rsi_interval": 1,
        "rsi_buy": current_rsi_buy,          # dynamic
        "rsi_sell": 80,                       # ⬅️ changed to 80
        "tp": 0.015,                          # UPDATED: 1.5% take profit
        "sl": 0.006,                          # UPDATED: 0.6% stop loss
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

# ═══ COOLDOWN AFTER SCALP EXIT ═══════════════════════════════════════════════
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
    global ml_model, ml_scaler, ml_trained, ml_last_training_trades
    global vwap_cache, atr_cache
    
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
    
    # Reset ML model
    ml_model = None
    ml_scaler = None
    ml_trained = False
    ml_last_training_trades = 0
    
    # Reset caches
    vwap_cache = {}
    atr_cache = {}
    
    save_state()
    log.info("✅ Paper account reset complete")
    send_telegram("🔄 <b>Paper account reset</b>\nFresh start with $10,000")

# ═══ RESET SCALP STATS FUNCTION ══════════════════════════════════════════════
def reset_scalp_stats():
    """Reset only scalp balance and stats. Preserve trades, positions, and RSI learning."""
    global scalp_balance, scalp_stats
    
    log.info("🔄 Resetting scalp stats only")
    
    # Reset only these specific variables
    scalp_balance = 10000.0
    scalp_stats = {"pnl": 0.0, "wins": 0, "losses": 0}
    
    save_state()
    log.info("✅ Scalp stats reset complete")
    send_telegram("🔄 <b>Scalp stats reset</b>\nBalance: $10,000 | P&L: $0")

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
    
    # FIX 4: Daily loss kill switch based on percentage
    starting_balance = 20000.0  # approximate starting total
    total_balance = scalp_balance + trend_balance
    daily_pnl_pct = (total_balance - starting_balance) / starting_balance
    if daily_pnl_pct <= -DAILY_LOSS_PCT_LIMIT:
        log.warning(f"⛔ Daily loss limit hit ({daily_pnl_pct:.2%}) – trading paused")
        send_telegram(f"⛔ <b>Daily loss kill switch triggered</b>\nDown {daily_pnl_pct:.2%} today\nTrading paused")
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
    """Calculate RSI using pandas-ta."""
    if len(prices) < RSI_PERIOD + 1:
        return None
    try:
        import pandas as pd
        series = pd.Series(prices)
        rsi_series = ta.rsi(series, length=RSI_PERIOD)
        if rsi_series is not None and not rsi_series.empty:
            return round(rsi_series.iloc[-1], 2)
        return None
    except Exception as e:
        log.error(f"RSI calculation error: {e}")
        # Fallback to manual calculation if pandas-ta fails
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
    """Calculate Bollinger Bands using pandas-ta."""
    if len(prices) < period:
        return None, None, None
    try:
        import pandas as pd
        series = pd.Series(prices)
        bbands = ta.bbands(series, length=period, std=std_dev)
        if bbands is not None and not bbands.empty:
            cols = bbands.columns.tolist()
            lower = bbands.iloc[-1][cols[0]]
            middle = bbands.iloc[-1][cols[1]]
            upper = bbands.iloc[-1][cols[4]]
            return lower, middle, upper
        return None, None, None
    except Exception as e:
        log.error(f"Bollinger Bands calculation error: {e}")
        # Fallback to manual calculation
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

def get_volume_ratio(kraken_ohlc, interval):
    """Get current volume vs 20-period average volume."""
    try:
        r = session.get(
            f"{KRAKEN_URL}/0/public/OHLC?pair={kraken_ohlc}&interval={interval}",
            timeout=30
        )
        data = r.json()
        if data.get("error") or not data["result"]:
            return 1.0
        result = data["result"]
        key = [k for k in result.keys() if k != "last"][0]
        candles = result[key]
        if len(candles) < VOLUME_PERIOD + 1:
            return 1.0
        volumes = [float(c[6]) for c in candles[-VOLUME_PERIOD-1:]]
        current_volume = volumes[-1]
        avg_volume = sum(volumes[:-1]) / VOLUME_PERIOD
        return current_volume / avg_volume if avg_volume > 0 else 1.0
    except Exception as e:
        log.error(f"Volume ratio error: {e}")
        return 1.0

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

# ═══ VWAP CALCULATION ════════════════════════════════════════════════════════
def get_daily_vwap(kraken_ohlc):
    """Fetch 1-minute OHLC for the current day and calculate VWAP."""
    global vwap_cache
    now_ts = time.time()
    
    # Check cache
    if kraken_ohlc in vwap_cache:
        cached = vwap_cache[kraken_ohlc]
        if now_ts - cached['timestamp'] < VWAP_CACHE_SECONDS:
            return cached['value']
    
    try:
        # Start of day (UTC)
        start_of_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        since = int(start_of_day.timestamp())
        
        # Fetch up to 1440 candles (24h) – enough for the day
        url = f"{KRAKEN_URL}/0/public/OHLC"
        params = {
            "pair": kraken_ohlc,
            "interval": 1,
            "since": since
        }
        r = session.get(url, params=params, timeout=30)
        data = r.json()
        if data.get("error"):
            log.error(f"VWAP fetch error: {data['error']}")
            return None
        
        result = data["result"]
        key = [k for k in result.keys() if k != "last"][0]
        candles = result[key]
        
        if not candles:
            log.warning(f"No candles for VWAP calculation for {kraken_ohlc}")
            return None
        
        sum_typical_volume = 0.0
        sum_volume = 0.0
        
        for candle in candles:
            # candle: [time, open, high, low, close, vwap, volume, count]
            high = float(candle[2])
            low = float(candle[3])
            close = float(candle[4])
            volume = float(candle[6])
            typical_price = (high + low + close) / 3.0
            sum_typical_volume += typical_price * volume
            sum_volume += volume
        
        if sum_volume == 0:
            return None
        
        vwap = sum_typical_volume / sum_volume
        
        # Cache
        vwap_cache[kraken_ohlc] = {'value': vwap, 'timestamp': now_ts}
        return vwap
    except Exception as e:
        log.error(f"Error calculating VWAP for {kraken_ohlc}: {e}")
        return None

# ═══ ATR CALCULATION (UPGRADE 2) ════════════════════════════════════════════
def get_atr(kraken_ohlc, interval, period=14):
    """Fetch OHLC data and calculate ATR using pandas-ta."""
    global atr_cache
    cache_key = f"{kraken_ohlc}_{interval}"
    now_ts = time.time()
    
    # Check cache
    if cache_key in atr_cache:
        cached = atr_cache[cache_key]
        if now_ts - cached['timestamp'] < ATR_CACHE_SECONDS:
            return cached['value']
    
    try:
        r = session.get(
            f"{KRAKEN_URL}/0/public/OHLC?pair={kraken_ohlc}&interval={interval}",
            timeout=30
        )
        data = r.json()
        if data.get("error") or not data["result"]:
            return None
        
        result = data["result"]
        key = [k for k in result.keys() if k != "last"][0]
        candles = result[key]
        
        if len(candles) < period + 1:
            return None
        
        # Extract high, low, close
        highs = [float(c[2]) for c in candles[-(period+1):]]
        lows = [float(c[3]) for c in candles[-(period+1):]]
        closes = [float(c[4]) for c in candles[-(period+1):]]
        
        import pandas as pd
        high_series = pd.Series(highs)
        low_series = pd.Series(lows)
        close_series = pd.Series(closes)
        
        atr_series = ta.atr(high_series, low_series, close_series, length=period)
        if atr_series is not None and not atr_series.empty:
            atr_value = atr_series.iloc[-1]
            # Cache
            atr_cache[cache_key] = {'value': atr_value, 'timestamp': now_ts}
            return atr_value
        return None
    except Exception as e:
        log.error(f"ATR calculation error for {kraken_ohlc}: {e}")
        return None

def get_dynamic_trade_size(base_size, atr_value):
    """Calculate dynamic trade size based on ATR."""
    if atr_value is None:
        return round(base_size)
    
    if atr_value < 200:
        multiplier = 1.3
    elif atr_value < 500:
        multiplier = 1.0
    elif atr_value < 800:
        multiplier = 0.7
    else:
        multiplier = 0.5
    
    return round(base_size * multiplier)

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
def paper_buy_scalp(symbol, price, trade_size=None):
    global scalp_balance
    # Only allow BTC for scalp
    if symbol != "BTC":
        return None
    # Use provided trade_size or fall back to config default
    resolved_trade_size = trade_size if trade_size is not None else STRATEGIES["SCALP"]["trade_size"]
    qty = round(resolved_trade_size / price, 6)
    cost = qty * price
    fee = cost * KRAKEN_TAKER_FEE
    total_cost = cost + fee
    if scalp_balance < total_cost:
        log.warning(f"Insufficient scalp balance for {symbol} (need ${total_cost:.2f}, have ${scalp_balance:.2f})")
        return None
    scalp_balance -= total_cost
    log.info(f"📄 ⚡ SCALP BUY {symbol} qty={qty} @ ${price:,.2f} | Cost: ${cost:.2f} | Fee: ${fee:.2f} | Scalp Balance: ${scalp_balance:,.2f}")
    return qty

def paper_sell_scalp(symbol, price, qty, pnl=None):
    global scalp_balance, hourly_trades, scalp_last_exit_time
    if symbol != "BTC":
        return
    gross_proceeds = qty * price
    fee = gross_proceeds * KRAKEN_TAKER_FEE
    net_proceeds = gross_proceeds - fee
    scalp_balance += net_proceeds
    
    # Calculate fee-adjusted PnL if entry price was stored
    fee_adjusted_pnl = pnl
    if pnl is not None:
        # Recalculate PnL with fees: (sell_net - buy_total_cost) 
        # But we stored raw pnl in position, so we approximate:
        # Raw PnL was qty * (price - entry)
        # Fee-adjusted: qty * price * (1 - fee) - qty * entry * (1 + fee)
        # = raw_pnl - fee * qty * (price + entry)
        # For simplicity, we just note the fee impact
        total_fees = gross_proceeds * KRAKEN_TAKER_FEE + (qty * price * KRAKEN_TAKER_FEE)  # entry fee approx
        fee_adjusted_pnl = pnl - fee
    
    pnl_text = f" | Gross PnL: ${pnl:+.2f} | Fee: ${fee:.2f} | Net: ${fee_adjusted_pnl:+.2f}" if pnl is not None else f" | Fee: ${fee:.2f}"
    log.info(f"📄 ⚡ SCALP SELL {symbol} qty={qty} @ ${price:,.2f}{pnl_text} | Scalp Balance: ${scalp_balance:,.2f}")
    if pnl is not None:
        trade_record = {
            'symbol': symbol,
            'pnl': fee_adjusted_pnl if fee_adjusted_pnl is not None else pnl - fee,
            'rsi_buy_used': STRATEGIES["SCALP"]["rsi_buy"],
            'time': datetime.now(timezone.utc).isoformat()
        }
        hourly_trades.append(trade_record)
        update_rsi_performance(trade_record)
        # Record exit time for cooldown
        scalp_last_exit_time[symbol] = time.time()
        
        # Retrain ML model after new trade
        train_ml_model()

def paper_buy_trend(symbol, price):
    global trend_balance
    # Only allow BTC for trend as well (new)
    if symbol != "BTC":
        return None
    qty = round(STRATEGIES["TREND"]["trade_size"] / price, 6)
    cost = qty * price
    fee = cost * KRAKEN_TAKER_FEE
    total_cost = cost + fee
    if trend_balance < total_cost:
        log.warning(f"Insufficient trend balance for {symbol} (need ${total_cost:.2f}, have ${trend_balance:.2f})")
        return None
    trend_balance -= total_cost
    log.info(f"📄 📈 TREND BUY {symbol} qty={qty} @ ${price:,.2f} | Cost: ${cost:.2f} | Fee: ${fee:.2f} | Trend Balance: ${trend_balance:,.2f}")
    return qty

def paper_sell_trend(symbol, price, qty, pnl=None):
    global trend_balance
    if symbol != "BTC":
        return
    gross_proceeds = qty * price
    fee = gross_proceeds * KRAKEN_TAKER_FEE
    net_proceeds = gross_proceeds - fee
    trend_balance += net_proceeds
    
    fee_adjusted_pnl = None
    if pnl is not None:
        fee_adjusted_pnl = pnl - fee
    
    pnl_text = f" | Gross PnL: ${pnl:+.2f} | Fee: ${fee:.2f} | Net: ${fee_adjusted_pnl:+.2f}" if pnl is not None else f" | Fee: ${fee:.2f}"
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
        # Restrict both SCALP and TREND to BTC only
        if (strategy_name == "SCALP" or strategy_name == "TREND") and symbol != "BTC":
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
                
                # Update the matching entry trade with pnl for ML training
                if strategy_name == "SCALP":
                    for t in trades:
                        if (t['symbol'] == f"{symbol}/USD" and 
                            t['pnl'] is None and 
                            t.get('rsi_at_entry') is not None):
                            t['pnl'] = round(pnl, 4)
                            break
                
                del positions[symbol]
                log.info(f"{'✅' if is_win else '🛑'} [{strategy_name}] SELL {symbol} | {reason} | PnL={pnl:+.4f}")

        except (KeyError, ValueError, TypeError) as e:
            log.exception(f"[{strategy_name}] Exit error {symbol}: {e}")
        except requests.exceptions.RequestException as e:
            log.exception(f"[{strategy_name}] Exit network error {symbol}: {e}")

# ═══ ENTRY CHECKS ════════════════════════════════════════════════════════════
def run_entries(strategy_name, cfg, positions, trades, stats, buy_func):
    # FIX: Add global scalp_balance to fix scoping error
    global scalp_balance
    total_positions = len(scalp_positions) + len(trend_positions)
    if total_positions >= MAX_POSITIONS:
        return
    
    # FIX 2: Trading session filter
    if not is_valid_trading_session():
        log.info(f"⏰ Outside trading session (London 07-12 UTC / NY 13-17 UTC) – no new entries")
        return

    for s in SYMBOLS:
        symbol = s["symbol"]
        # Restrict both SCALP and TREND to BTC only
        if (strategy_name == "SCALP" or strategy_name == "TREND") and symbol != "BTC":
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
            is_vwap_reversion = False  # Flag for VWAP reversion entries
            
            # Store ML features if entry signal detected
            ml_rsi = None
            ml_bb_distance = None
            ml_regime = regime
            ml_volume_ratio = None
            ml_vwap_distance = None
            ml_atr_value = None
            ml_vwap_deviation_pct = None

            # Signal 1: RSI – uses current_rsi_buy (dynamic)
            if rsi is not None and rsi < cfg["rsi_buy"]:
                entry_signal = True
                signal_reason = f"RSI {rsi} < {cfg['rsi_buy']}"
                ml_rsi = rsi

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
                        lower_band, _, _ = calculate_bollinger_bands(closes)
                        if lower_band is not None and price <= lower_band * 1.01:
                            entry_signal = True
                            signal_reason = f"Bollinger touch ${price:,.2f}"
                            ml_rsi = rsi if rsi is not None else 50
                            ml_bb_distance = (price - lower_band) / lower_band * 100
                except Exception as e:
                    log.error(f"Bollinger error {symbol}: {e}")

            # === VWAP FILTER FOR SCALP (Signal 1 & 2 only) ===
            # Only apply VWAP filter block to RSI and Bollinger signals, not VWAP Reversion
            vwap = None
            if strategy_name == "SCALP" and (entry_signal and not is_vwap_reversion) and VWAP_FILTER_ENABLED:
                vwap = get_daily_vwap(s["kraken_ohlc"])
                if vwap is None:
                    log.warning(f"⚠️ [{strategy_name}] {symbol} VWAP unavailable – skipping entry")
                    continue
                
                if price < vwap:
                    log.info(f"⏳ [{strategy_name}] {symbol} blocked by VWAP filter (price ${price:,.2f} < VWAP ${vwap:,.2f})")
                    continue
                else:
                    signal_reason += f" | Above VWAP"
                    ml_vwap_distance = (price - vwap) / vwap * 100

            # === 5-MINUTE RSI CONFIRMATION FOR SCALP (Signal 1 & 2) ===
            if entry_signal and strategy_name == "SCALP" and not is_vwap_reversion:
                rsi_5m = get_rsi(s["kraken_ohlc"], symbol, 5)  # 5‑minute interval
                if rsi_5m is None:
                    log.warning(f"⚠️ [{strategy_name}] {symbol} 5‑min RSI unavailable – skipping entry")
                    continue
                
                if rsi_5m >= 50:
                    log.info(f"⏳ [{strategy_name}] {symbol} blocked by 5‑min RSI ({rsi_5m:.1f} >= 50) – higher timeframe not confirmed")
                    continue
                else:
                    signal_reason += f" | 5m RSI {rsi_5m:.1f}"

            # === SIGNAL 3: VWAP REVERSION ENTRY (Upgrade 3) ===
            if strategy_name == "SCALP" and not entry_signal and symbol == "BTC":
                # Fetch VWAP if not already fetched
                if vwap is None:
                    vwap = get_daily_vwap(s["kraken_ohlc"])
                
                if vwap is not None:
                    # Calculate deviation
                    deviation_pct = (price - vwap) / vwap * 100  # negative = below VWAP
                    
                    # Condition 1: Price is between 0.10% and 0.45% BELOW VWAP
                    if -0.45 <= deviation_pct <= -0.10:
                        # Condition 2: Current price is higher than price 2 candles ago (recovering)
                        r = session.get(
                            f"{KRAKEN_URL}/0/public/OHLC?pair={s['kraken_ohlc']}&interval=1",
                            timeout=30
                        )
                        data = r.json()
                        if not data.get("error") and data["result"]:
                            result = data["result"]
                            key = [k for k in result.keys() if k != "last"][0]
                            candles = result[key]
                            
                            if len(candles) >= 3:
                                last_3_closes = [float(c[4]) for c in candles[-3:]]
                                # Current price should be higher than price 2 candles ago
                                if price > last_3_closes[0]:
                                    # Condition 3: 5-min RSI is below 45
                                    rsi_5m = get_rsi(s["kraken_ohlc"], symbol, 5)
                                    if rsi_5m is not None and rsi_5m < 45:
                                        # Condition 4: Market regime is NOT trending_down
                                        if regime != 'trending_down':
                                            # Condition 5: ML confidence > 0.55 (lower threshold)
                                            # Prepare features for ML prediction
                                            ml_rsi_temp = rsi if rsi is not None else 50
                                            
                                            # Get Bollinger distance
                                            bb_dist = 0
                                            try:
                                                closes = [float(c[4]) for c in candles[-50:]]
                                                lower_band, _, _ = calculate_bollinger_bands(closes)
                                                if lower_band is not None:
                                                    bb_dist = (price - lower_band) / lower_band * 100
                                            except:
                                                pass
                                            
                                            vol_ratio = get_volume_ratio(s["kraken_ohlc"], cfg["rsi_interval"])
                                            
                                            # Get ATR for ML feature
                                            atr_val = get_atr(s["kraken_ohlc"], cfg["rsi_interval"])
                                            
                                            ml_confidence, _ = predict_trade_profit(
                                                ml_rsi_temp, bb_dist, regime, vol_ratio, 
                                                deviation_pct, atr_val if atr_val else 0, deviation_pct
                                            )
                                            
                                            if ml_confidence > 0.55:
                                                entry_signal = True
                                                is_vwap_reversion = True
                                                signal_reason = f"VWAP Reversion ${price:,.2f} | VWAP ${vwap:,.2f} | Deviation {deviation_pct:.2f}%"
                                                ml_rsi = ml_rsi_temp
                                                ml_bb_distance = bb_dist
                                                ml_volume_ratio = vol_ratio
                                                ml_vwap_distance = deviation_pct
                                                ml_atr_value = atr_val if atr_val else 0
                                                ml_vwap_deviation_pct = deviation_pct
                                                log.info(f"🔄 [SCALP] VWAP Reversion entry triggered for {symbol}")

            # Calculate ML features if entry signal detected (for Signal 1 & 2)
            if entry_signal and strategy_name == "SCALP" and not is_vwap_reversion:
                if ml_rsi is None:
                    ml_rsi = rsi if rsi is not None else 50
                if ml_bb_distance is None:
                    # Get Bollinger distance if not already calculated
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
                            lower_band, _, _ = calculate_bollinger_bands(closes)
                            if lower_band is not None:
                                ml_bb_distance = (price - lower_band) / lower_band * 100
                            else:
                                ml_bb_distance = 0
                    except:
                        ml_bb_distance = 0
                
                # Get volume ratio
                ml_volume_ratio = get_volume_ratio(s["kraken_ohlc"], cfg["rsi_interval"])
                
                # Get ATR for ML feature
                ml_atr_value = get_atr(s["kraken_ohlc"], cfg["rsi_interval"])
                if ml_atr_value is None:
                    ml_atr_value = 0
                
                # If VWAP distance wasn't set (filter disabled or price above), set to 0
                if ml_vwap_distance is None:
                    ml_vwap_distance = 0
                
                ml_vwap_deviation_pct = 0  # Not a VWAP reversion entry
                
                # ML prediction with all 7 features
                confidence, should_trade = predict_trade_profit(
                    ml_rsi, ml_bb_distance, ml_regime, ml_volume_ratio, 
                    ml_vwap_distance, ml_atr_value, ml_vwap_deviation_pct
                )
                log.info(f"🤖 [{strategy_name}] {symbol} ML confidence: {confidence:.2%} (threshold: {ML_CONFIDENCE_THRESHOLD:.0%})")
                
                if not should_trade:
                    log.info(f"⏳ [{strategy_name}] {symbol} ML rejected entry (confidence {confidence:.2%} < {ML_CONFIDENCE_THRESHOLD:.0%})")
                    continue
                else:
                    signal_reason += f" | ML {confidence:.0%}"

            # Trend filter (only for Signal 1 & 2)
            if entry_signal and strategy_name == "SCALP" and not is_vwap_reversion and TREND_FILTER_ENABLED:
                if not price_above_ema(s["kraken_ohlc"], symbol, cfg["rsi_interval"], price):
                    log.info(f"⏳ [{strategy_name}] {symbol} signal blocked by trend filter (price below EMA)")
                    continue
                else:
                    signal_reason += " | Trend up"

            # Volume filter (only for Signal 1 & 2)
            if entry_signal and strategy_name == "SCALP" and not is_vwap_reversion and VOLUME_FILTER_ENABLED:
                spike, curr_vol, avg_vol = volume_spike_detected(s["kraken_ohlc"], cfg["rsi_interval"])
                if not spike:
                    log.info(f"⏳ [{strategy_name}] {symbol} signal blocked by volume filter (curr={curr_vol:.0f}, avg={avg_vol:.0f})")
                    continue
                else:
                    signal_reason += f" | Volume spike ({curr_vol/avg_vol:.1f}x)"

            if entry_signal:
                # FIX 3: Fee-aware risk-reward gate for scalp
                if strategy_name == "SCALP":
                    if not trade_is_fee_viable(cfg["tp"], cfg["sl"]):
                        log.info(f"⏳ [{strategy_name}] {symbol} trade not fee-viable – skipping")
                        continue
                
                # Get dynamic trade size based on ATR (Upgrade 2)
                if strategy_name == "SCALP":
                    atr_value = get_atr(s["kraken_ohlc"], cfg["rsi_interval"])
                    base_trade_size = cfg["trade_size"]
                    
                    # Apply dynamic sizing
                    if regime == 'volatile' and REGIME_DETECTION_ENABLED:
                        # Volatile regime already reduces size to 50%
                        base_trade_size = int(base_trade_size * 0.5)
                    
                    # FIX 5: Profit scale‑down multiplier
                    base_trade_size = int(base_trade_size * get_profit_scale_multiplier())
                    
                    trade_size = get_dynamic_trade_size(base_trade_size, atr_value)
                    
                    # FIX 1: ATR None crash protection
                    atr_display = f"{atr_value:.0f}" if atr_value is not None else "N/A"
                    
                    # If VWAP reversion entry, reduce size by 30% (70% of normal)
                    if is_vwap_reversion:
                        trade_size = round(trade_size * 0.7)
                        log.info(f"📐 [SCALP] ATR={atr_display} → trade size adjusted to ${trade_size} (VWAP Reversion 70%)")
                    else:
                        log.info(f"📐 [SCALP] ATR={atr_display} → trade size adjusted to ${trade_size}")
                else:
                    trade_size = cfg["trade_size"]
                    if regime == 'volatile' and REGIME_DETECTION_ENABLED:
                        trade_size = int(trade_size * 0.5)
                        log.info(f"[{strategy_name}] {symbol} volatile regime – reducing trade size to ${trade_size}")

                log.info(f"🎯 [{strategy_name}] BUY SIGNAL {symbol} | {signal_reason}")
                if TRADING_MODE == "live":
                    qty = round(trade_size / price, 6)
                    kraken_place_order(s["kraken_order"], "buy", qty)
                    # FIX 2: qty_actual undefined in live mode
                    qty_actual = qty
                else:
                    if trade_size != cfg["trade_size"]:
                        qty = round(trade_size / price, 6)
                        cost = qty * price
                        fee = cost * KRAKEN_TAKER_FEE
                        total_cost = cost + fee
                        if scalp_balance < total_cost:
                            log.warning(f"Insufficient scalp balance for {symbol} with adjusted size")
                            continue
                        scalp_balance -= total_cost
                        log.info(f"📄 ⚡ SCALP BUY {symbol} qty={qty} @ ${price:,.2f} (adjusted size) | Cost: ${cost:.2f} | Fee: ${fee:.2f} | Scalp Balance: ${scalp_balance:,.2f}")
                        qty_actual = qty
                    else:
                        if strategy_name == "SCALP":
                            qty_actual = paper_buy_scalp(symbol, price, trade_size)
                        else:
                            qty_actual = buy_func(symbol, price)
                        if qty_actual is None: continue
                
                # Store ML features with the trade for future training
                trade_entry = {
                    "entry": price, "qty": qty_actual, 
                    "time": datetime.now(timezone.utc).isoformat()
                }
                if strategy_name == "SCALP":
                    trade_entry["rsi_at_entry"] = ml_rsi
                    trade_entry["bb_distance"] = ml_bb_distance
                    trade_entry["regime"] = regime_to_int(ml_regime)
                    trade_entry["volume_ratio"] = ml_volume_ratio
                    trade_entry["vwap_distance"] = ml_vwap_distance
                    trade_entry["atr_value"] = ml_atr_value if ml_atr_value is not None else get_atr(s["kraken_ohlc"], cfg["rsi_interval"])
                    trade_entry["vwap_deviation_pct"] = ml_vwap_deviation_pct if ml_vwap_deviation_pct is not None else 0
                
                positions[symbol] = trade_entry
                trades.append({
                    "symbol": f"{symbol}/USD", "side": "BUY",
                    "price": price, "qty": qty_actual, "pnl": None,
                    "reason": signal_reason,
                    "strategy": strategy_name,
                    "time": datetime.now(timezone.utc).isoformat(),
                    "rsi_at_entry": ml_rsi if strategy_name == "SCALP" else None,
                    "bb_distance": ml_bb_distance if strategy_name == "SCALP" else None,
                    "regime": regime_to_int(ml_regime) if strategy_name == "SCALP" else None,
                    "volume_ratio": ml_volume_ratio if strategy_name == "SCALP" else None,
                    "vwap_distance": ml_vwap_distance if strategy_name == "SCALP" else None,
                    "atr_value": trade_entry.get("atr_value") if strategy_name == "SCALP" else None,
                    "vwap_deviation_pct": trade_entry.get("vwap_deviation_pct") if strategy_name == "SCALP" else None
                })
                log.info(f"📈 [{strategy_name}] BUY {symbol} @ ${price:,.2f} (size: {qty_actual})")
                break
            else:
                log.info(f"⏳ [{strategy_name}] {symbol} RSI={rsi} — waiting for signal")

        except (KeyError, ValueError, TypeError) as e:
            log.exception(f"[{strategy_name}] Entry error {symbol}: {e}")
        except requests.exceptions.RequestException as e:
            log.exception(f"[{strategy_name}] Entry network error {symbol}: {e}")

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
                "trades": scalp_trades[-200:],
                "stats": scalp_stats
            },
            "trend": {
                "balance": trend_bal,
                "positions": trend_positions,
                "trades": trend_trades[-200:],
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
            "ml_trained": ml_trained,
            "ml_last_training_trades": ml_last_training_trades,
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
    global ml_trained, ml_last_training_trades
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
            # Load ML state
            ml_trained = state.get("ml_trained", False)
            ml_last_training_trades = state.get("ml_last_training_trades", 0)
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
    
    # Check for scalp stats reset flag
    if os.path.exists("/tmp/RESET_SCALP_STATS"):
        log.info("🔄 Scalp stats reset signal detected")
        reset_scalp_stats()
        os.remove("/tmp/RESET_SCALP_STATS")
    
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
    
    # Retrain ML model periodically if not trained yet
    if not ml_trained and len(scalp_trades) >= ML_MIN_TRADES:
        train_ml_model()

# ═══ MAIN ════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    log.info(f"⚡📈 APEX BOT – BTC SCALP ONLY, RSI 20/80, $50 TRADE SIZE")
    log.info(f"⚡ SCALP: BTC only, RSI({RSI_PERIOD}) Buy<{current_rsi_buy} OR Bollinger | 5m RSI gate <50 | VWAP filter: {'ON' if VWAP_FILTER_ENABLED else 'OFF'} | Sell>80 TP1.5% SL0.6% | Time exit 1h | Trend filter: {TREND_FILTER_ENABLED}")
    if REGIME_DETECTION_ENABLED:
        log.info(f"🧠 Market regime detection: ON")
    if DYNAMIC_RSI_ENABLED:
        log.info(f"📈 Dynamic RSI adaptation: ON (candidates: {RSI_CANDIDATES})")
    if ML_ENABLED and SKLEARN_AVAILABLE:
        log.info(f"🤖 ML Predictor: ON (Random Forest, min trades: {ML_MIN_TRADES}, confidence: {ML_CONFIDENCE_THRESHOLD:.0%})")
    elif ML_ENABLED and not SKLEARN_AVAILABLE:
        log.info(f"⚠️ ML Predictor: DISABLED (scikit-learn not installed)")
    if VOLUME_FILTER_ENABLED:
        log.info(f"📊 Volume filter: ON")
    log.info(f"📈 TREND: BTC only, RSI(4h) Buy<45 Sell>75 TP5% SL4% $200")
    log.info(f"🎯 Daily profit target: ${DAILY_PROFIT_TARGET} | Daily loss limit: ${MAX_DAILY_LOSS} | Max positions: {MAX_POSITIONS}")
    log.info(f"📱 Telegram hourly summaries: ENABLED")
    log.info(f"🔄 Reset endpoint: http://your-bot:8081/reset_paper")
    log.info(f"💰 Fee simulation: ON ({KRAKEN_TAKER_FEE:.2%} taker fee)")
    log.info(f"📊 Technical libraries: pandas-ta ON | TA-Lib: {'ON' if TALIB_AVAILABLE else 'OFF'}")
    log.info(f"📐 ATR dynamic sizing: ON | VWAP Reversion entry: ON | ML features: 7")
    send_telegram(f"🚀 <b>APEX BOT – BTC SCALP ONLY</b>\nRSI 20/80, $50 trades\n5m RSI gate <50\nVWAP filter: {'ON' if VWAP_FILTER_ENABLED else 'OFF'}\nDynamic adaptation ON\n🤖 ML Predictor: {'ON' if (ML_ENABLED and SKLEARN_AVAILABLE) else 'OFF'}\nFee simulation: ON ({KRAKEN_TAKER_FEE:.2%})\nATR dynamic sizing: ON\nVWAP Reversion: ON\nHourly summaries")

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
    
    # Initial ML training if enough trades exist
    if SKLEARN_AVAILABLE and len(scalp_trades) >= ML_MIN_TRADES:
        train_ml_model()

    while True:
        try:
            bot_tick()
        except Exception as e:
            log.error(f"Bot error: {e}")
            send_telegram(f"⚠️ <b>Bot error</b>\n{str(e)[:100]}")
        log.info("⏳ Sleeping 30s...")
        time.sleep(30)
