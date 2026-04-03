import time
import requests
import os
import random
from datetime import datetime, timezone, timedelta
from pybit.unified_trading import HTTP

# =================================================================
# CONFIGURAZIONE (Match esatto con il tuo Render Environment)
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
# =================================================================

# Inizializzazione Sessione
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
    except Exception as e:
        print(f"Errore Telegram: {e}")

def is_position_open():
    """Verifica se ci sono posizioni aperte (Safe Mode)"""
    try:
        res = session.get_positions(category="linear", symbol=SYMBOL)
        if res.get('retCode') == 0:
            for pos in res['result']['list']:
                size_val = pos.get('size', "0")
                if size_val and size_val != "" and float(size_val) != 0:
                    return True
            return False
        else:
            print(f"⚠️ Errore API Posizione: {res.get('retMsg')}")
            return False
    except Exception as e:
        print(f"🚨 Eccezione Posizione: {e}")
        return False

def get_daily_confidence():
    """Calcola Confidence con protezione anti-spam API"""
    try:
        # Piccolo ritardo casuale prima di chiamare Bybit
        time.sleep(random.uniform(1, 3))
        res = session.get_kline(category="linear", symbol=SYMBOL, interval="D", limit=2)
        
        if res.get('retCode') != 0:
            print(f"⚠️ Errore API Klines: {res.get('retMsg')}")
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
    """Esecuzione ordine Limit con fallback Market"""
    try:
        tp = price * (1 + TP_PERC) if side == "Buy" else price * (1 - TP_PERC)
        sl = price * (1 - SL_PERC) if side == "Buy" else price * (1 + SL_PERC)

        print(f"🚀 INVIO ORDINE {side}: Qty {round(qty, 2)} a {round(price, 2)}")
        
        order = session.place_order(
            category="linear", symbol=SYMBOL, side=side, orderType="Limit",
            price=str(round(price, 2)), 
            qty=str(round(qty, 2)),
            takeProfit=str(round(tp, 2)), 
            stopLoss=str(round(sl, 2)),
            tpTriggerBy="MarkPrice", slTriggerBy="MarkPrice", timeInForce="GTC"
        )
        
        if order.get('retCode') == 0:
            order_id = order['result']['orderId']
            send_telegram(f"🔔 Ordine {side} inviato.\nPrice: {round(price, 2)}\nQty: {round(qty, 2)}")
            
            # Aspettiamo 120 secondi che il Limit venga fillato
            time.sleep(120)
            
            # Controllo se è ancora aperto
            check = session.get_open_orders(category="linear", symbol=SYMBOL, orderId=order_id)
            if check.get('retCode') == 0 and check['result']['list']:
                session.cancel_order(category="linear", symbol=SYMBOL, orderId=order_id)
                session.place_order(
                    category="linear", symbol=SYMBOL, side=side, orderType="Market",
                    qty=str(round(qty, 2)), 
                    takeProfit=str(round(tp, 2)), 
                    stopLoss=str(round(sl, 2)),
                    tpTriggerBy="MarkPrice", slTriggerBy="MarkPrice"
                )
                send_telegram(f"⚡ Entrato MARKET {side} (Limit scaduto)")
            else:
                send_telegram(f"✅ LIMIT FILLATO!")
        else:
            send_telegram(f"❌ Bybit ha rifiutato l'ordine: {order.get('retMsg')}")
            
    except Exception as e:
        send_telegram(f"🚨 ERRORE ESECUZIONE: {e}")

def run_loop():
    global last_prediction
    send_telegram("🤖 Bot TrumpShipper: Online. Sincronizzazione slot 15m avviata.")
    print("🚀 Bot in funzione...")

    while True:
        try:
            # 1. CALCOLO PAUSA DINAMICA
            now = datetime.now(timezone.utc)
            minutes_to_next = 15 - (now.minute % 15)
            # Ci svegliamo 10 secondi dopo lo scoccare del minuto per evitare il traffico API
            seconds_to_wait = (minutes_to_next * 60) - now.second + 10
            
            if seconds_to_wait <= 0: seconds_to_wait = 900 
            
            # Aggiungiamo jitter casuale per evitare Errore 10006
            total_sleep = seconds_to_wait + random.uniform(2, 6)
            
            next_run = now + timedelta(seconds=total_sleep)
            print(f"💤 Pausa di {int(total_sleep)}s. Prossimo check: {next_run.strftime('%H:%M:%S')} UTC")
            
            time.sleep(total_sleep)

            # 2. AZIONE
            print(f"🎯 Sveglia slot {datetime.now(timezone.utc).strftime('%H:%M')} UTC")
            
            confidence, current_price = get_daily_confidence()
            
            if confidence is not None:
                # Logica Segnale
                if confidence > DELTA_THRESHOLD:
                    prediction = "Long"
                elif confidence < -DELTA_THRESHOLD:
                    prediction = "Short"
                else:
                    prediction = last_prediction

                pos_open = is_position_open()
                print(f"📊 [DATI] Conf: {round(confidence, 6)} | Pred: {prediction} | Open: {pos_open}")

                if not pos_open:
                    qty = FIXED_SIZE_USD / current_price
                    side = "Buy" if prediction == "Long" else "Sell"
                    execute_smart_trade(side, qty, current_price)
                
                last_prediction = prediction

        except Exception as e:
            print(f"🚨 Errore nel loop: {e}")
            time.sleep(30)

if __name__ == "__main__":
    # Verifica immediata delle chiavi
    if not API_KEY or not API_SECRET:
        print("❌ CHIAVI NON TROVATE! Controlla le variabili su Render.")
    else:
        run_loop()
