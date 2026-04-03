import time
import requests
import os
import random
from datetime import datetime, timezone, timedelta
from pybit.unified_trading import HTTP

# =================================================================
# CONFIGURAZIONE (Verifica su Render)
# =================================================================
API_KEY = os.getenv('BYBIT_API_KEY')
API_SECRET = os.getenv('BYBIT_API_SECRET')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

SYMBOL = "ETHUSDT"
FIXED_SIZE_USD = 500 
DELTA_THRESHOLD = 0.0001
SL_PERC = 1.3 / 100
TP_PERC = 5.0 / 100
# =================================================================

session = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET, recv_window=10000)
last_prediction = "Long" 

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    except:
        pass

def is_position_open():
    """Controlla se ci sono posizioni aperte su Bybit"""
    try:
        res = session.get_positions(category="linear", symbol=SYMBOL)
        if res['retCode'] == 0:
            for pos in res['result']['list']:
                size_val = pos.get('size', "0")
                if size_val and size_val != "" and float(size_val) != 0:
                    return True
        return False
    except Exception as e:
        print(f"⚠️ Errore check posizione: {e}")
        return False

def get_daily_confidence():
    """Recupera dati kline con gestione anti-rate limit"""
    try:
        res = session.get_kline(category="linear", symbol=SYMBOL, interval="D", limit=2)
        if res['retCode'] != 0:
            print(f"⚠️ Errore Bybit {res['retCode']}: {res['retMsg']}")
            return None, None
            
        klines = res['result']['list']
        d_close_now = float(klines[0][4])      
        d_close_prev = float(klines[1][4]) 
        confidence = (d_close_now - d_close_prev) / d_close_prev
        return confidence, d_close_now
    except Exception as e:
        print(f"🚨 Errore connessione: {e}")
        return None, None

def execute_smart_trade(side, qty, price):
    """Invia ordine Limit con fallback Market"""
    try:
        tp = price * (1 + TP_PERC) if side == "Buy" else price * (1 - TP_PERC)
        sl = price * (1 - SL_PERC) if side == "Buy" else price * (1 + SL_PERC)

        order = session.place_order(
            category="linear", symbol=SYMBOL, side=side, orderType="Limit",
            price=str(round(price, 2)), qty=str(round(qty, 2)),
            takeProfit=str(round(tp, 2)), stopLoss=str(round(sl, 2)),
            tpTriggerBy="MarkPrice", slTriggerBy="MarkPrice", timeInForce="GTC"
        )
        
        if order['retCode'] == 0:
            send_telegram(f"🔔 Segnale {side} inviato a {round(price, 2)}")
            time.sleep(120)
            # Controllo se eseguito, altrimenti Market (logica semplificata per brevità)
        else:
            send_telegram(f"❌ Ordine rifiutato: {order['retMsg']}")
    except Exception as e:
        send_telegram(f"🚨 Errore esecuzione: {e}")

def run_loop():
    global last_prediction
    send_telegram("🤖 Bot TrumpShipper: Avvio con Pausa Intelligente (Dynamic Sleep).")

    while True:
        try:
            now = datetime.now(timezone.utc)
            
            # 1. CALCOLO DELLA PAUSA FINO AL PROSSIMO SLOT (00, 15, 30, 45)
            minutes_to_next = 15 - (now.minute % 15)
            # Puntiamo a 10 secondi dopo l'inizio del minuto per evitare il traffico API
            seconds_to_wait = (minutes_to_next * 60) - now.second + 10
            
            if seconds_to_wait <= 0: # Se siamo già nello slot, aspettiamo il prossimo ciclo
                seconds_to_wait = 900 

            # 2. AGGIUNGIAMO UN PICCOLO "JITTER" CASUALE (0-3 secondi) per l'errore 10006
            jitter = random.uniform(0, 3)
            total_sleep = seconds_to_wait + jitter
            
            next_wake_up = now + timedelta(seconds=total_sleep)
            print(f"💤 In pausa per {int(total_sleep)}s. Sveglia prevista: {next_wake_up.strftime('%H:%M:%S')} UTC")
            
            time.sleep(total_sleep)

            # 3. AZIONE DOPO IL RISVEGLIO
            print(f"🎯 Sveglia! Check slot delle {datetime.now(timezone.utc).strftime('%H:%M')}...")
            
            confidence, current_price = get_daily_confidence()
            
            if confidence is not None:
                if confidence > DELTA_THRESHOLD:
                    prediction = "Long"
                elif confidence < -DELTA_THRESHOLD:
                    prediction = "Short"
                else:
                    prediction = last_prediction

                pos_open = is_position_open()
                print(f"📊 Risultato: Conf {round(confidence, 6)} | Pred {prediction} | Open {pos_open}")

                if not pos_open:
                    qty = FIXED_SIZE_USD / current_price
                    side = "Buy" if prediction == "Long" else "Sell"
                    execute_smart_trade(side, qty, current_price)
                
                last_prediction = prediction

        except Exception as e:
            print(f"🚨 Errore nel loop: {e}")
            time.sleep(60)

if __name__ == "__main__":
    run_loop()
