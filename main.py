import time
import requests
import os
import pandas as pd
from datetime import datetime, timezone, timedelta
from pybit.unified_trading import HTTP

# --- CONFIGURAZIONE ---
API_KEY = os.getenv('API_KEY')
API_SECRET = os.getenv('API_SECRET')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

SYMBOL = "ETHUSDT"
FIXED_SIZE_USD = 1000

# Nuovi Parametri richiesti
TP_PERC = 3.0 / 100
SL_PERC = 1.5 / 100
EMA_LENGTH = 18
TF_MINUTES = 1  # Analisi su TF1

session = HTTP(
    testnet=False,
    api_key=API_KEY,
    api_secret=API_SECRET,
    recv_window=20000
)

# Variabile per tracciare il blocco attuale e impedire rientri
current_bias_block = None
trade_done_in_block = False

# ------------------------------------------------

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except: pass

def get_current_position():
    try:
        res = session.get_positions(category="linear", symbol=SYMBOL)
        if res["retCode"] == 0:
            for pos in res["result"]["list"]:
                size = float(pos["size"])
                if size > 0:
                    return pos["side"], size, float(pos["avgPrice"]), float(pos["unrealisedPnl"])
        return None, 0, 0, 0
    except: return None, 0, 0, 0

def get_data_4h():
    """Recupera la chiusura dell'ultima candela 4H e i dati TF1 per EMA"""
    try:
        # Bias 4H
        d = session.get_kline(category="linear", symbol=SYMBOL, interval="240", limit=2)
        bias_4h_close = float(d["result"]["list"][1][4]) # Chiusura candela 4H precedente

        # Dati TF1 per EMA
        m = session.get_kline(category="linear", symbol=SYMBOL, interval="1", limit=100)
        df = pd.DataFrame(m["result"]["list"], columns=["time","open","high","low","close","vol","turnover"])
        df["close"] = df["close"].astype(float)
        df = df.iloc[::-1].reset_index(drop=True)
        
        ema = df["close"].ewm(span=EMA_LENGTH, adjust=False).mean().iloc[-1]
        current_price = df["close"].iloc[-1]
        
        return bias_4h_close, ema, current_price
    except Exception as e:
        print(f"Errore dati: {e}")
        return None, None, None

def execute_trade(side, price):
    try:
        qty = round(FIXED_SIZE_USD / price, 3)
        tp = round(price * (1 + TP_PERC) if side == "Buy" else price * (1 - TP_PERC), 2)
        sl = round(price * (1 - SL_PERC) if side == "Buy" else price * (1 + SL_PERC), 2)

        order = session.place_order(
            category="linear", symbol=SYMBOL, side=side, orderType="Market",
            qty=str(qty), takeProfit=str(tp), stopLoss=str(sl)
        )
        if order["retCode"] == 0:
            send_telegram(f"🔥 Entry {side} @ {price}\nTP: {tp} | SL: {sl}")
            return True
        return False
    except Exception as e:
        print(f"Errore order: {e}")
        return False

def run_strategy():
    global current_bias_block, trade_done_in_block
    send_telegram("Bot Trump 4H (TF1) Online")

    while True:
        try:
            now = datetime.now(timezone.utc)
            # Calcolo del blocco 4H attuale (00, 04, 08, 12, 16, 20)
            block_start_hour = (now.hour // 4) * 4
            this_block = now.replace(hour=block_start_hour, minute=0, second=0, microsecond=0)
            
            # Reset se cambiamo blocco di 4 ore
            if current_bias_block != this_block:
                current_bias_block = this_block
                trade_done_in_block = False
                send_telegram(f"Nuovo Bias H4: {this_block.strftime('%H:%M')}")

            # Sleep fino al prossimo minuto spaccato
            time.sleep(60 - datetime.now().second)

            bias_val, ema_val, price = get_data_4h()
            if bias_val is None: continue

            side_active, size, entry, pnl = get_current_position()

            # --- LOGICA CHIUSURA FORZATA (1 min prima del reset) ---
            # Se mancano meno di 2 minuti alla fine delle 4 ore (es. sono le 03:59)
            next_block = this_block + timedelta(hours=4)
            time_to_reset = (next_block - datetime.now(timezone.utc)).total_seconds()

            if size > 0 and time_to_reset <= 90: # Meno di 1.5 min al cambio
                exit_side = "Sell" if side_active == "Buy" else "Buy"
                session.place_order(category="linear", symbol=SYMBOL, side=exit_side, orderType="Market", qty=str(size))
                send_telegram("Chiusura preventiva fine blocco 4H")
                trade_done_in_block = True # Impedisce riaperture nell'ultimo minuto

            # --- LOGICA INGRESSO ---
            if size == 0 and not trade_done_in_block:
                # Long: Prezzo TF1 > Bias 4H e Prezzo > EMA 18
                if price > bias_val and price > ema_val:
                    if execute_trade("Buy", price):
                        trade_done_in_block = True
                
                # Short: Prezzo TF1 < Bias 4H e Prezzo < EMA 18
                elif price < bias_val and price < ema_val:
                    if execute_trade("Sell", price):
                        trade_done_in_block = True

            print(f"[{now.strftime('%H:%M')}] Price: {price} | Bias: {bias_val} | EMA: {ema_val:.2f} | TradeDone: {trade_done_in_block}")

        except Exception as e:
            print(f"Errore loop: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run_strategy()
