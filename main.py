import time
import requests
import os
import random
from datetime import datetime, timezone, timedelta
from pybit.unified_trading import HTTP

# =================================================================
# CONFIGURAZIONE (Match con il tuo Render Environment)
# =================================================================
API_KEY = os.getenv('API_KEY')
API_SECRET = os.getenv('API_SECRET')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

SYMBOL = "ETHUSDT"
FIXED_SIZE_USD = 500 
DELTA_THRESHOLD = 0.0001
SL_PERC = 1.3 / 100
TP_PERC = 5.0 / 100

# TIMEFRAME IMPOSTATO A 30 MINUTI
TF_MINUTES = 30 
# =================================================================

# Sessione Bybit con finestra di ricezione ampia per evitare lag
session = HTTP(
    testnet=False, 
    api_key=API_KEY, 
    api_secret=API_SECRET, 
    recv_window=10000
)

last_prediction = "Long" 

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    except:
        pass

def is_position_open():
    """Controlla se ci sono posizioni attive su ETH"""
    try:
        res = session.get_positions(category="linear", symbol=SYMBOL)
        if res.get('retCode') == 0:
            for pos in res['result']['list']:
                size_val = pos.get('size', "0")
                if size_val and size_val != "" and float(size_val) != 0:
                    return True
        return False
    except Exception as e:
        print(f"⚠️ Errore posizione: {e}")
        return False

def get_daily_confidence():
    """Calcola la forza del trend rispetto alla chiusura di ieri"""
    try:
        # Piccolo delay per non colpire l'API al millisecondo zero
        time.sleep(random.uniform(1, 3))
        res = session.get_kline(category="linear", symbol=SYMBOL, interval="D", limit=2)
        
        if res.get('retCode') != 0:
            print(f"⚠️ Bybit Error: {res.get('retMsg')}")
            return None, None
            
        klines = res['result']['list']
        # klines[0] oggi, klines[1] ieri
        d_close_now = float(klines[0][4])      
        d_close_prev = float(klines[1][4]) 
        confidence = (d_close_now - d_close_prev) / d_close_prev
        return confidence, d_close_now
    except Exception as e:
        print(f"🚨 Eccezione Klines: {e}")
        return None, None

def execute_smart_trade(side, qty, price):
    """Esegue ordine con 2 decimali e fallback Market"""
    try:
        tp = price * (1 + TP_PERC) if side == "Buy" else price * (1 - TP_PERC)
        sl = price * (1 - SL_PERC) if side == "Buy" else price * (1 + SL_PERC)

        print(f"🚀 ORDINE {side}: Qty {round(qty, 2)} @ {round(price, 2)}")
        
        order = session.place_order(
            category="linear", symbol=SYMBOL, side=side, orderType="Limit",
            price=str(round(price, 2)), qty=str(round(qty, 2)),
            takeProfit=str(round(tp, 2)), stopLoss=str(round(sl, 2)),
            tpTriggerBy="MarkPrice", slTriggerBy="MarkPrice", timeInForce="GTC"
        )
        
        if order.get('retCode') == 0:
            order_id = order['result']['orderId']
            send_telegram(f"🔔 Segnale {side} rilevato.\nPrezzo: {round(price, 2)}\nQty: {round(qty, 2)}")
            
            time.sleep(120) # Attesa fill
            
            # Controllo se l'ordine è ancora aperto
            check = session.get_open_orders(category="linear", symbol=SYMBOL, orderId=order_id)
            if check.get('retCode') == 0 and check['result']['list']:
                session.cancel_order(category="linear", symbol=SYMBOL, orderId=order_id)
                session.place_order(
                    category="linear", symbol=SYMBOL, side=side, orderType="Market",
                    qty=str(round(qty, 2)), takeProfit=str(round(tp, 2)), stopLoss=str(round(sl, 2)),
                    tpTriggerBy="MarkPrice", slTriggerBy="MarkPrice"
                )
                send_telegram(f"⚡ Entrato MARKET {side} (Limit scaduto)")
            else:
                send_telegram(f"✅ Ordine LIMIT eseguito!")
        else:
            send_telegram(f"❌ Bybit Error: {order.get('retMsg')}")
    except Exception as e:
        send_telegram(f"🚨 Errore Execute: {e}")

def run_loop():
    global last_prediction
    send_telegram(f"🤖 Bot TrumpShipper ONLINE (TF{TF_MINUTES}m).")
    print(f"🚀 Bot avviato su Timeframe {TF_MINUTES} minuti.")

    while True:
        try:
            now = datetime.now(timezone.utc)
            
            # --- CALCOLO PAUSA DINAMICA PER TF30 ---
            # Trova quanti minuti mancano alla prossima mezz'ora (00 o 30)
            minutes_to_next = TF_MINUTES - (now.minute % TF_MINUTES)
            # Sveglia a +12 secondi dall'inizio del minuto per "pulizia" API
            seconds_to_wait = (minutes_to_next * 60) - now.second + 12
            
            if seconds_to_wait <= 0: 
                seconds_to_wait = TF_MINUTES * 60 
            
            # Jitter casuale per non essere "uno dei tanti"
            total_sleep = seconds_to_wait + random.uniform(1, 4)
            
            next_run = now + timedelta(seconds=total_sleep)
            print(f"💤 Pausa di {int(total_sleep)}s. Prossimo check: {next_run.strftime('%H:%M:%S')} UTC")
            
            time.sleep(total_sleep)

            # --- AZIONE ---
            print(f"🎯 Sveglia slot {datetime.now(timezone.utc).strftime('%H:%M')} UTC")
            
            confidence, current_price = get_daily_confidence()
            
            if confidence is not None:
                if confidence > DELTA_THRESHOLD:
                    prediction = "Long"
                elif confidence < -DELTA_THRESHOLD:
                    prediction = "Short"
                else:
                    prediction = last_prediction

                pos_open = is_position_open()
                print(f"📊 [TF30] Conf: {round(confidence, 6)} | Pred: {prediction} | Open: {pos_open}")

                if not pos_open:
                    qty = FIXED_SIZE_USD / current_price
                    side = "Buy" if prediction == "Long" else "Sell"
                    execute_smart_trade(side, qty, current_price)
                
                last_prediction = prediction

        except Exception as e:
            print(f"🚨 Errore Loop: {e}")
            time.sleep(30)

if __name__ == "__main__":
    run_loop()
