import time
import requests
import os
from datetime import datetime, timezone
from pybit.unified_trading import HTTP

# =================================================================
# CONFIGURAZIONE UTENTE (Imposta come Environment Variables su Render)
# =================================================================
API_KEY = os.getenv('BYBIT_API_KEY')
API_SECRET = os.getenv('BYBIT_API_SECRET')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

SYMBOL = "ETHUSDT"
FIXED_SIZE_USD = 500  # Size fissa in USDT
DELTA_THRESHOLD = 0.0001
SL_PERC = 1.3 / 100
TP_PERC = 5.0 / 100
# =================================================================

# Inizializzazione Sessione Bybit
session = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET)
last_prediction = "Long" 

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    except Exception as e:
        print(f"Errore Telegram: {e}")

def is_position_open():
    """Verifica se ci sono posizioni attive su ETH"""
    try:
        res = session.get_positions(category="linear", symbol=SYMBOL)
        if res['retCode'] == 0:
            for pos in res['result']['list']:
                if float(pos['size']) != 0:
                    return True
        return False
    except Exception as e:
        print(f"Errore nel controllo posizione: {e}")
        return True # Prudenza: se c'è errore, facciamo finta sia aperta

def get_daily_confidence():
    """Calcola la Confidence: (Prezzo attuale - Chiusura Ieri) / Chiusura Ieri"""
    try:
        # Recupero kline Daily (limit 2: oggi e ieri)
        res = session.get_kline(category="linear", symbol=SYMBOL, interval="D", limit=2)
        if res['retCode'] != 0:
            print(f"Errore API Bybit: {res['retMsg']}")
            return None, None
            
        klines = res['result']['list']
        # Bybit klines: [0] è candela attuale (aperta), [1] è la candela chiusa di ieri
        d_close_now = float(klines[0][4])      
        d_close_prev = float(klines[1][4]) 
        
        confidence = (d_close_now - d_close_prev) / d_close_prev
        return confidence, d_close_now
    except Exception as e:
        print(f"Eccezione nel recupero dati kline: {e}")
        return None, None

def execute_smart_trade(side, qty, price):
    """Esegue ordine Limit con fallback Market dopo 120 secondi"""
    try:
        # Calcolo TP e SL dinamico
        tp = price * (1 + TP_PERC) if side == "Buy" else price * (1 - TP_PERC)
        sl = price * (1 - SL_PERC) if side == "Buy" else price * (1 + SL_PERC)

        # 1. Invio ordine LIMIT
        order = session.place_order(
            category="linear", symbol=SYMBOL, side=side, orderType="Limit",
            price=str(round(price, 2)), qty=str(qty),
            takeProfit=str(round(tp, 2)), stopLoss=str(round(sl, 2)),
            tpTriggerBy="MarkPrice", slTriggerBy="MarkPrice", timeInForce="GTC"
        )
        order_id = order['result']['orderId']
        send_telegram(f"🔔 Segnale {side} rilevato a {round(price, 2)}. Ordine LIMIT inviato.")

        # 2. Attesa per il Fill
        time.sleep(120)

        # 3. Controllo se l'ordine è ancora attivo
        check = session.get_open_orders(category="linear", symbol=SYMBOL, orderId=order_id)
        if check['result']['list']:
            # Se ancora presente, cancelliamo e entriamo Market
            session.cancel_order(category="linear", symbol=SYMBOL, orderId=order_id)
            session.place_order(
                category="linear", symbol=SYMBOL, side=side, orderType="Market",
                qty=str(qty), takeProfit=str(round(tp, 2)), stopLoss=str(round(sl, 2)),
                tpTriggerBy="MarkPrice", slTriggerBy="MarkPrice"
            )
            send_telegram(f"⚡ Limit non eseguito. Entrato MARKET {side} (Qty: {qty})")
        else:
            send_telegram(f"✅ Ordine LIMIT eseguito con successo!")
            
    except Exception as e:
        send_telegram(f"❌ Errore durante l'esecuzione del trade: {e}")

def run_loop():
    global last_prediction
    last_processed_time = "" 
    print(f"🚀 Bot TrumpShipper Operativo. Scansione ogni 15 minuti su {SYMBOL}...")

    while True:
        # Uso timezone-aware objects per evitare i Warning di Render
        now = datetime.now(timezone.utc)
        
        # Identifico lo slot temporale (00, 15, 30, 45)
        current_slot = f"{now.hour}:{now.minute}"
        
        # Scatta se siamo nel minuto giusto e abbiamo superato i primi 5 secondi di "assestamento"
        if now.minute in [0, 15, 30, 45] and now.second >= 5:
            if current_slot != last_processed_time:
                
                print(f"--- Check delle {current_slot} UTC ---")
                confidence, current_price = get_daily_confidence()
                
                if confidence is not None:
                    # Logica Segnale (Replica Pine Script)
                    if confidence > DELTA_THRESHOLD:
                        prediction = "Long"
                    elif confidence < -DELTA_THRESHOLD:
                        prediction = "Short"
                    else:
                        prediction = last_prediction # nz(prediction[1])

                    # Controllo se operare
                    if not is_position_open():
                        qty = round(FIXED_SIZE_USD / current_price, 2)
                        side = "Buy" if prediction == "Long" else "Sell"
                        execute_smart_trade(side, qty, current_price)
                    else:
                        print(f"Analisi: {prediction} | Stato: Posizione già aperta. Attendo chiusura.")

                    last_prediction = prediction
                    last_processed_time = current_slot

        # Dorme 10 secondi per non saturare le API Rate Limits
        time.sleep(10)

if __name__ == "__main__":
    try:
        send_telegram("🤖 Bot TrumpShipper: Sistema riavviato e in scansione.")
        run_loop()
    except Exception as e:
        print(f"Errore critico nel loop principale: {e}")
