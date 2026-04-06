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

# Variabili di stato per evitare trade multipli nello stesso blocco 4H
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
    """Ritorna Side, Size, EntryPrice, PnL della posizione aperta"""
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
    """Recupera Bias 4H e EMA18 basati sull'ultima candela CHIUSA"""
    try:
        # 1. BIAS 4H: Chiusura della candela 4H precedente (quella già finita)
        d = session.get_kline(category="linear", symbol=SYMBOL, interval="240", limit=2)
        bias_4h_val = float(d["result"]["list"][1][4]) 

        # 2. DATI TF1: Analisi su grafico a 1 minuto
        m = session.get_kline(category="linear", symbol=SYMBOL, interval="1", limit=150)
        df = pd.DataFrame(m["result"]["list"], columns=["time","open","high","low","close","vol","turnover"])
        df["close"] = df["close"].astype(float)
        df = df.iloc[::-1].reset_index(drop=True)
        
        # Calcolo EMA 18
        df["ema"] = df["close"].ewm(span=EMA_LENGTH, adjust=False).mean()

        # PRENDIAMO I VALORI DELLA CANDELA CHIUSA (indice -2)
        # Il bot gira al minuto 18:01:02, quindi analizza la candela chiusa delle 18:00
        closed_candle_price = df["close"].iloc[-2]
        ema_val = df["ema"].iloc[-2]
        
        return bias_4h_val, ema_val, closed_candle_price
    except Exception as e:
        print(f"Errore recupero dati API: {e}")
        return None, None, None

def execute_trade(side, price):
    """Esegue l'ordine Market con TP e SL impostati"""
    try:
        qty = round(FIXED_SIZE_USD / price, 3)
        tp = round(price * (1 + TP_PERC) if side == "Buy" else price * (1 - TP_PERC), 2)
        sl = round(price * (1 - SL_PERC) if side == "Buy" else price * (1 + SL_PERC), 2)

        order = session.place_order(
            category="linear", symbol=SYMBOL, side=side, orderType="Market",
            qty=str(qty), takeProfit=str(tp), stopLoss=str(sl)
        )
        if order["retCode"] == 0:
            send_telegram(f"🚀 ORDINE {side} ESEGUITO\nPrezzo: {price}\nTP: {tp} | SL: {sl}")
            return True
        else:
            print(f"Errore Bybit nell'ordine: {order['retMsg']}")
            return False
    except Exception as e:
        print(f"Eccezione durante esecuzione ordine: {e}")
        return False

def run_strategy():
    global current_bias_block, trade_done_in_block
    send_telegram("🤖 Bot Trump 4H (Versione Anti-Whipsaw) Attivo")

    while True:
        try:
            now = datetime.now(timezone.utc)
            
            # Identificazione del blocco 4H corrente (00, 04, 08, 12, 16, 20)
            block_start_hour = (now.hour // 4) * 4
            this_block = now.replace(hour=block_start_hour, minute=0, second=0, microsecond=0)
            
            # Se siamo in un nuovo blocco di 4 ore, resettiamo il flag del trade
            if current_bias_block != this_block:
                current_bias_block = this_block
                trade_done_in_block = False
                send_telegram(f"🕒 Cambio blocco 4H: {this_block.strftime('%H:%M')} UTC. Operatività ripristinata.")

            # Attesa inizio minuto + 2 secondi per sincronizzazione server
            secs_to_wait = 60 - datetime.now().second
            time.sleep(secs_to_wait + 2) 

            # Recupero dati tecnici
            bias_val, ema_val, last_close = get_data_signals()
            if bias_val is None: continue

            side_active, size, entry, pnl = get_current_position()

            # --- LOGICA CHIUSURA FINE BLOCCO (Opzionale) ---
            # Chiude forzatamente se siamo a 2 minuti dal cambio bias per evitare incertezze
            next_block = this_block + timedelta(hours=4)
            time_to_reset = (next_block - datetime.now(timezone.utc)).total_seconds()

            if size > 0 and time_to_reset <= 120:
                exit_side = "Sell" if side_active == "Buy" else "Buy"
                session.place_order(category="linear", symbol=SYMBOL, side=exit_side, orderType="Market", qty=str(size))
                send_telegram("⚠️ Chiusura posizione preventiva per fine blocco 4H")
                trade_done_in_block = True # Blocca rientri nell'ultimo minuto

            # --- LOGICA INGRESSO ---
            # Entra solo se NON c'è una posizione aperta E se NON è già stato fatto un trade in questo blocco
            if size == 0 and not trade_done_in_block:
                
                # CONDIZIONE LONG: Chiusura TF1 > Bias 4H E Chiusura TF1 > EMA18
                if last_close > bias_val and last_close > ema_val:
                    if execute_trade("Buy", last_close):
                        trade_done_in_block = True
                
                # CONDIZIONE SHORT: Chiusura TF1 < Bias 4H E Chiusura TF1 < EMA18
                elif last_close < bias_val and last_close < ema_val:
                    if execute_trade("Sell", last_close):
                        trade_done_in_block = True

            # Debug a console
            status = "In attesa" if not trade_done_in_block else "Blocco completato"
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Prazzo: {last_close} | Bias: {bias_val} | EMA: {ema_val:.2f} | Stato: {status}")

        except Exception as e:
            print(f"Errore nel loop principale: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run_strategy()
