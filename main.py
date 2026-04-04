import time
import requests
import os
import pandas as pd
import pandas_ta as ta  # Ti serve questa libreria: pip install pandas-ta
from datetime import datetime, timezone
from pybit.unified_trading import HTTP

# =================================================================
# CONFIGURAZIONE
# =================================================================
API_KEY = os.getenv('API_KEY')
API_SECRET = os.getenv('API_SECRET')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

SYMBOL = "ETHUSDT"
FIXED_SIZE_USD = 1000 
TP_PERC = 6.0 / 100
SL_PERC = 2.0 / 100
TF_MINUTES = 5  # Timeframe 5 minuti
EMA_LENGTH = 20
USE_EMA_FILTER = True

# =================================================================

session = HTTP(
    testnet=False, 
    api_key=API_KEY, 
    api_secret=API_SECRET, 
    recv_window=10000
)

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
    except: pass

def get_data():
    """Recupera chiusura daily di ieri e candele 5m per EMA"""
    try:
        # 1. Chiusura Daily di ieri
        d_res = session.get_kline(category="linear", symbol=SYMBOL, interval="D", limit=2)
        daily_close_yesterday = float(d_res['result']['list'][1][4])

        # 2. Candele 5m per calcolo EMA
        m5_res = session.get_kline(category="linear", symbol=SYMBOL, interval=str(TF_MINUTES), limit=100)
        df = pd.DataFrame(m5_res['result']['list'], columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        df['close'] = df['close'].astype(float)
        
        # Invertiamo il dataframe perché Bybit restituisce i dati dalla più recente alla più vecchia
        df = df.iloc[::-1].reset_index(drop=True)
        
        # Calcolo EMA
        ema_series = ta.ema(df['close'], length=EMA_LENGTH)
        current_ema = ema_series.iloc[-1]
        current_price = df['close'].iloc[-1]

        return daily_close_yesterday, current_ema, current_price
    except Exception as e:
        print(f"🚨 Errore recupero dati: {e}")
        return None, None, None

def get_position():
    try:
        res = session.get_positions(category="linear", symbol=SYMBOL)
        if res.get('retCode') == 0:
            pos = res['result']['list'][0]
            return pos.get('side'), float(pos.get('size', 0))
        return None, 0
    except: return None, 0

def execute_trade(side, price):
    try:
        qty = round(FIXED_SIZE_USD / price, 3)
        tp = round(price * (1 + TP_PERC), 2) if side == "Buy" else round(price * (1 - TP_PERC), 2)
        sl = round(price * (1 - SL_PERC), 2) if side == "Buy" else round(price * (1 + SL_PERC), 2)
        
        order = session.place_order(
            category="linear", symbol=SYMBOL, side=side, orderType="Market",
            qty=str(qty), takeProfit=str(tp), stopLoss=str(sl),
            tpTriggerBy="MarkPrice", slTriggerBy="MarkPrice"
        )
        if order.get('retCode') == 0:
            icon = "🟢" if side == "Buy" else "🔴"
            send_telegram(f"{icon} <b>ENTRY {side}</b>\nPrezzo: {price}\nTP: {tp} | SL: {sl}")
    except Exception as e:
        send_telegram(f"🚨 Errore trade: {e}")

def run_strategy():
    print(f"🤖 Bot avviato su {SYMBOL} (TF 5m)")
    last_day = datetime.now(timezone.utc).day

    while True:
        now = datetime.now(timezone.utc)
        
        # --- LOGICA DI TIMING: Esecuzione ogni 5 min + 3 secondi ---
        # Calcola quanti secondi mancano alla prossima chiusura candela (05, 10, 15...)
        next_tick = (TF_MINUTES - (now.minute % TF_MINUTES)) * 60 - now.second + 3
        if next_tick <= 0: next_tick = 3 # Sicurezza per non dormire tempi negativi
        
        print(f"⏳ In attesa della chiusura candela... (prossimo check tra {next_tick}s)")
        time.sleep(next_tick)

        # --- AZIONI POST-CHIUSURA ---
        daily_yesterday, ema_val, current_price = get_data()
        if daily_yesterday is None: continue

        side_active, size = get_position()

        # 1. Reset Giornaliero (se è cambiato il giorno, chiudi tutto)
        current_day = datetime.now(timezone.utc).day
        if current_day != last_day:
            if size > 0:
                session.place_order(category="linear", symbol=SYMBOL, side="Sell" if side_active=="Buy" else "Buy", 
                                    orderType="Market", qty=str(size))
                send_telegram("🌅 <b>Reset Giornaliero:</b> Posizioni chiuse.")
            last_day = current_day

        # 2. Logica di Ingresso (se non ci sono posizioni aperte)
        if size == 0:
            ema_long_ok = not USE_EMA_FILTER or current_price > ema_val
            ema_short_ok = not USE_EMA_FILTER or current_price < ema_val

            if current_price > daily_yesterday and ema_long_ok:
                execute_trade("Buy", current_price)
            elif current_price < daily_yesterday and ema_short_ok:
                execute_trade("Sell", current_price)

if __name__ == "__main__":
    run_strategy()
