import time
import requests
import os
import pandas as pd
import math
from datetime import datetime, timezone, timedelta
from pybit.unified_trading import HTTP

# --- CONFIGURAZIONE ---
API_KEY = os.getenv('API_KEY')
API_SECRET = os.getenv('API_SECRET')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

SYMBOL = "ETHUSDT"
FIXED_SIZE_USD = 1000

# Parametri Strategia
TP_PERC = 3.0 / 100
SL_PERC = 1.5 / 100
EMA_LENGTH = 18

session = HTTP(
    testnet=False,
    api_key=API_KEY,
    api_secret=API_SECRET,
    recv_window=20000
)

# Variabili di stato
current_bias_block = None
trade_done_in_block = False

# ------------------------------------------------

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except: pass

def get_precision(symbol):
    """Recupera la precisione corretta per QTY e PRICE dal server Bybit"""
    try:
        res = session.get_instruments_info(category="linear", symbol=symbol)
        if res["retCode"] == 0:
            info = res["result"]["list"][0]
            qty_step = float(info["lotSizeFilter"]["qtyStep"])
            price_step = float(info["priceFilter"]["tickSize"])
            
            # Calcola il numero di decimali dal qtyStep (es: 0.01 -> 2)
            qty_precision = max(0, int(-math.log10(qty_step)))
            price_precision = max(0, int(-math.log10(price_step)))
            return qty_precision, price_precision
    except:
        return 2, 2 # Default prudenziale
    return 2, 2

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

def get_data_signals():
    try:
        d = session.get_kline(category="linear", symbol=SYMBOL, interval="240", limit=2)
        bias_4h_val = float(d["result"]["list"][1][4]) 

        m = session.get_kline(category="linear", symbol=SYMBOL, interval="1", limit=150)
        df = pd.DataFrame(m["result"]["list"], columns=["time","open","high","low","close","vol","turnover"])
        df["close"] = df["close"].astype(float)
        df = df.iloc[::-1].reset_index(drop=True)
        
        df["ema"] = df["close"].ewm(span=EMA_LENGTH, adjust=False).mean()

        closed_candle_price = df["close"].iloc[-2]
        ema_val = df["ema"].iloc[-2]
        
        return bias_4h_val, ema_val, closed_candle_price
    except Exception as e:
        print(f"Errore recupero dati API: {e}")
        return None, None, None

def execute_trade(side, price):
    """Esegue l'ordine Market con TP e SL adattati alla precisione di Bybit"""
    try:
        qty_prec, price_prec = get_precision(SYMBOL)
        
        # Calcolo quantità con troncamento (non arrotondamento) per evitare errori di margine
        raw_qty = FIXED_SIZE_USD / price
        qty = math.floor(raw_qty * (10**qty_prec)) / (10**qty_prec)
        
        # Calcolo TP e SL arrotondati correttamente per il prezzo
        tp = round(price * (1 + TP_PERC) if side == "Buy" else price * (1 - TP_PERC), price_prec)
        sl = round(price * (1 - SL_PERC) if side == "Buy" else price * (1 + SL_PERC), price_prec)

        order = session.place_order(
            category="linear", symbol=SYMBOL, side=side, orderType="Market",
            qty=str(qty), takeProfit=str(tp), stopLoss=str(sl)
        )
        
        if order["retCode"] == 0:
            msg = f"🚀 ORDINE {side} ESEGUITO\nQuantità: {qty}\nPrezzo: {price}\nTP: {tp} | SL: {sl}"
            print(msg)
            send_telegram(msg)
            return True
        else:
            print(f"Errore Bybit (RetCode: {order['retCode']}): {order['retMsg']}")
            return False
    except Exception as e:
        print(f"Eccezione durante esecuzione ordine: {e}")
        return False

def run_strategy():
    global current_bias_block, trade_done_in_block
    print("🤖 Bot Trump 4H Avviato...")
    send_telegram("🤖 Bot Trump 4H (Versione Anti-Whipsaw) Attivo")

    while True:
        try:
            now = datetime.now(timezone.utc)
            
            # Reset Blocco 4H
            block_start_hour = (now.hour // 4) * 4
            this_block = now.replace(hour=block_start_hour, minute=0, second=0, microsecond=0)
            
            if current_bias_block != this_block:
                current_bias_block = this_block
                trade_done_in_block = False
                send_telegram(f"🕒 Nuovo blocco 4H: {this_block.strftime('%H:%M')} UTC.")

            # Sync al minuto (attende l'inizio del minuto + 2 sec)
            secs_to_wait = 60 - datetime.now().second
            time.sleep(secs_to_wait + 2) 

            bias_val, ema_val, last_close = get_data_signals()
            if bias_val is None: continue

            side_active, size, entry, pnl = get_current_position()

            # Chiusura preventiva fine blocco
            next_block = this_block + timedelta(hours=4)
            time_to_reset = (next_block - datetime.now(timezone.utc)).total_seconds()

            if size > 0 and time_to_reset <= 120:
                exit_side = "Sell" if side_active == "Buy" else "Buy"
                session.place_order(category="linear", symbol=SYMBOL, side=exit_side, orderType="Market", qty=str(size))
                send_telegram("⚠️ Chiusura preventiva fine blocco")
                trade_done_in_block = True 

            # Logica Ingresso
            if size == 0 and not trade_done_in_block:
                if last_close > bias_val and last_close > ema_val:
                    if execute_trade("Buy", last_close):
                        trade_done_in_block = True
                
                elif last_close < bias_val and last_close < ema_val:
                    if execute_trade("Sell", last_close):
                        trade_done_in_block = True

            status = "In attesa" if not trade_done_in_block else "Blocco completato/Posizione aperta"
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Prezzo: {last_close} | Bias: {bias_val} | EMA: {ema_val:.2f} | {status}")

        except Exception as e:
            print(f"Errore loop: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run_strategy()
