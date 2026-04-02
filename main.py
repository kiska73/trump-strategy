import time
import requests
import os
from datetime import datetime
from pybit.unified_trading import HTTP

# =================================================================
# CONFIGURAZIONE UTENTE (Imposta su Render)
# =================================================================
API_KEY = os.getenv('BYBIT_API_KEY')
API_SECRET = os.getenv('BYBIT_API_SECRET')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

SYMBOL = "ETHUSDT"
FIXED_SIZE_USD = 500  # Size in USDT
DELTA_THRESHOLD = 0.0001
SL_PERC = 1.3 / 100
TP_PERC = 5.0 / 100
# =================================================================

session = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET)
last_prediction = "Long" 

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    except Exception as e:
        print(f"Errore Telegram: {e}")

def is_position_open():
    """Controlla se c'è già una posizione aperta su SYMBOL"""
    try:
        res = session.get_positions(category="linear", symbol=SYMBOL)
        for pos in res['result']['list']:
            if float(pos['size']) != 0:
                return True
        return False
    except Exception as e:
        print(f"Errore controllo posizione: {e}")
        return True # Per sicurezza non apriamo se c'è errore

def get_daily_confidence():
    """Confronta prezzo attuale con chiusura di IERI"""
    try:
        res = session.get_kline(category="linear", symbol=SYMBOL, interval="D", limit=2)
        klines = res['result']['list']
        d_close_now = float(klines[0][4])      
        d_close_prev = float(klines[1][4]) 
        confidence = (d_close_now - d_close_prev) / d_close_prev
        return confidence, d_close_now
    except Exception as e:
        print(f"Errore dati daily: {e}")
        return None, None

def execute_smart_trade(side, qty, price):
    """Ordine Limit con fallback Market"""
    try:
        tp = price * (1 + TP_PERC) if side == "Buy" else price * (1 - TP_PERC)
        sl = price * (1 - SL_PERC) if side == "Buy" else price * (1 + SL_PERC)

        order = session.place_order(
            category="linear", symbol=SYMBOL, side=side, orderType="Limit",
            price=str(round(price, 2)), qty=str(qty),
            takeProfit=str(round(tp, 2)), stopLoss=str(round(sl, 2)),
            tpTriggerBy="MarkPrice", slTriggerBy="MarkPrice", timeInForce="GTC"
        )
        order_id = order['result']['orderId']
        send_telegram(f"🔔 Segnale {side} (TF15m).\nLimit inserito a {round(price, 2)}")

        time.sleep(120) # Aspetta 2 min

        check = session.get_open_orders(category="linear", symbol=SYMBOL, orderId=order_id)
        if check['result']['list']:
            session.cancel_order(category="linear", symbol=SYMBOL, orderId=order_id)
            session.place_order(
                category="linear", symbol=SYMBOL, side=side, orderType="Market",
                qty=str(qty), takeProfit=str(round(tp, 2)), stopLoss=str(round(sl, 2)),
                tpTriggerBy="MarkPrice", slTriggerBy="MarkPrice"
            )
            send_telegram(f"⚡ Entrato MARKET {side} (Qty: {qty})")
        else:
            send_telegram(f"✅ Limit fillato!")
    except Exception as e:
        send_telegram(f"❌ Errore esecuzione: {e}")

def run_loop():
    global last_prediction
    last_processed_time = "" 
    print(f"🚀 Bot in scansione ogni 15m su {SYMBOL}...")

    while True:
        now = datetime.utcnow()
        # Crea una stringa col minuto attuale arrotondato ai 15m
        # Es: 00:00, 00:15, 00:30, 00:45
        current_slot = f"{now.hour}:{now.minute}"
        
        # Scatta se siamo al minuto 0, 15, 30, 45 E sono passati almeno 5 secondi
        if now.minute in [0, 15, 30, 45] and now.second >= 5:
            if current_slot != last_processed_time:
                
                print(f"--- Check delle {current_slot} ---")
                confidence, current_price = get_daily_confidence()
                
                if confidence is not None:
                    # Logica Segnale
                    if confidence > DELTA_THRESHOLD:
                        prediction = "Long"
                    elif confidence < -DELTA_THRESHOLD:
                        prediction = "Short"
                    else:
                        prediction = last_prediction

                    # Se la direzione è cambiata o non ci sono posizioni, valuta apertura
                    if not is_position_open():
                        qty = round(FIXED_SIZE_USD / current_price, 2)
                        side = "Buy" if prediction == "Long" else "Sell"
                        execute_smart_trade(side, qty, current_price)
                    else:
                        print(f"Posizione già aperta. Confidence attuale: {round(confidence, 5)}")

                    last_prediction = prediction
                    last_processed_time = current_slot

        # Dorme 1 secondo per non perdere la finestra dei 5 secondi
        time.sleep(1)

if __name__ == "__main__":
    try:
        send_telegram("🤖 Bot TrumpShipper LIVE: Scansione ogni 15m attiva.")
        run_loop()
    except Exception as e:
        print(f"Errore critico: {e}")
