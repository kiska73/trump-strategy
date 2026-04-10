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
OFFSET_MINUTES = 30  # Strategia "Trump": entra 30 min dopo l'inizio del blocco 4H

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
            qty_precision = max(0, int(-math.log10(qty_step)))
            price_precision = max(0, int(-math.log10(price_step)))
            return qty_precision, price_precision
    except:
        return 2, 2
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
        # Bias basato su candele standard 4H (240 min)
        d = session.get_kline(category="linear", symbol=SYMBOL, interval="240", limit=2)
        bias_4h_val = float(d["result"]["list"][1][4]) # Chiusura dell'ultima candela 4H completata

        # EMA e Prezzo basati su timeframe 1m per precisione d'ingresso
        m = session.get_kline(category="linear", symbol=SYMBOL, interval="1", limit=150)
        df = pd.DataFrame(m["result"]["list"], columns=["time","open","high","low","close","vol","turnover"])
        df["close"] = df["close"].astype(float)
        df = df.iloc[::-1].reset_index(drop=True)
        
        df["ema"] = df["close"].ewm(span=EMA_LENGTH, adjust=False).mean()

        last_price = df["close"].iloc[-1]
        ema_val = df["ema"].iloc[-1]
        
        return bias_4h_val, ema_val, last_price
    except Exception as e:
        print(f"Errore recupero dati: {e}")
        return None, None, None

def execute_trade(side, price):
    try:
        qty_prec, price_prec = get_precision(SYMBOL)
        raw_qty = FIXED_SIZE_USD / price
        qty = math.floor(raw_qty * (10**qty_prec)) / (10**qty_prec)
        
        tp = round(price * (1 + TP_PERC) if side == "Buy" else price * (1 - TP_PERC), price_prec)
        sl = round(price * (1 - SL_PERC) if side == "Buy" else price * (1 + SL_PERC), price_prec)

        order = session.place_order(
            category="linear", symbol=SYMBOL, side=side, orderType="Market",
            qty=str(qty), takeProfit=str(tp), stopLoss=str(sl)
        )
        
        if order["retCode"] == 0:
            msg = f"🚀 TRUMP TRADE: {side}\nQty: {qty}\nPrice: {price}\nTP: {tp} | SL: {sl}"
            print(msg)
            send_telegram(msg)
            return True
        return False
    except Exception as e:
        print(f"Errore esecuzione: {e}")
        return False

def run_strategy():
    global current_bias_block, trade_done_in_block
    print("🇺🇸 Bot Trump 4H (30-min Offset) Avviato...")
    send_telegram("🇺🇸 Bot Trump 4H attivo. Offset: 30min dopo il blocco standard.")

    while True:
        try:
            now = datetime.now(timezone.utc)
            
            # --- LOGICA CALCOLO BLOCCO SFASATO ---
            standard_hour = (now.hour // 4) * 4
            # Il blocco per il bot inizia alle HH:30 invece che alle HH:00
            this_block_start = now.replace(hour=standard_hour, minute=OFFSET_MINUTES, second=0, microsecond=0)
            
            # Se siamo tra le 00:00 e le 00:29, apparteniamo ancora al blocco delle 20:30 del giorno prima
            if now < this_block_start:
                # Sottraiamo 4 ore per trovare l'inizio del blocco precedente
                prev_time = this_block_start - timedelta(hours=4)
                this_block_start = prev_time

            # Reset se entriamo in un nuovo intervallo di 4 ore
            if current_bias_block != this_block_start:
                current_bias_block = this_block_start
                trade_done_in_block = False
                send_telegram(f"🕒 Nuovo ciclo operativo iniziato: {this_block_start.strftime('%H:%M')} UTC")

            # Controllo ogni minuto
            time.sleep(60)

            bias_val, ema_val, last_price = get_data_signals()
            if bias_val is None: continue

            side_active, size, entry, pnl = get_current_position()

            # 1. CHIUSURA PREVENTIVA (2 minuti prima del prossimo "Trump Block")
            next_block = this_block_start + timedelta(hours=4)
            seconds_to_reset = (next_block - now).total_seconds()

            if size > 0 and seconds_to_reset <= 120:
                exit_side = "Sell" if side_active == "Buy" else "Buy"
                session.place_order(category="linear", symbol=SYMBOL, side=exit_side, orderType="Market", qty=str(size))
                send_telegram("⚠️ Fine turno: Chiusura posizione pre-nuovo blocco.")
                trade_done_in_block = True 

            # 2. LOGICA INGRESSO (Solo se siamo oltre i 30 min e non abbiamo ancora operato)
            if size == 0 and not trade_done_in_block:
                # Verifichiamo se il prezzo conferma il bias dopo il "rumore" iniziale
                if last_price > bias_val and last_price > ema_val:
                    if execute_trade("Buy", last_price):
                        trade_done_in_block = True
                
                elif last_price < bias_val and last_price < ema_val:
                    if execute_trade("Sell", last_price):
                        trade_done_in_block = True

            status = "Wait 30m" if now < this_block_start else "Scanning"
            if trade_done_in_block: status = "Done"
            print(f"[{now.strftime('%H:%M')}] Px: {last_price} | Bias: {bias_val} | EMA: {ema_val:.1f} | Stat: {status}")

        except Exception as e:
            print(f"Errore loop: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run_strategy()
