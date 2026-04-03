import time
import requests
import os
from datetime import datetime, timezone
from pybit.unified_trading import HTTP

# =================================================================
# CONFIGURAZIONE (Verifica su Render Environment Variables)
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

# Sessione Bybit
session = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET)
last_prediction = "Long" 

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        res = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
        if res.status_code != 200:
            print(f"❌ Telegram Error: {res.text}")
    except Exception as e:
        print(f"❌ Telegram Connection Error: {e}")

def is_position_open():
    """Controlla posizioni attive - Safe Mode"""
    try:
        res = session.get_positions(category="linear", symbol=SYMBOL)
        if res['retCode'] == 0:
            for pos in res['result']['list']:
                size_val = pos.get('size', "0")
                if size_val and size_val != "" and float(size_val) != 0:
                    return True
            return False
        else:
            send_telegram(f"⚠️ Bybit API Error (Pos): {res['retMsg']}")
            return True # Prudenza
    except Exception as e:
        send_telegram(f"🚨 Eccezione Posizione: {e}")
        return True

def get_daily_confidence():
    """Calcola Confidence (Prezzo attuale vs Chiusura Ieri)"""
    try:
        res = session.get_kline(category="linear", symbol=SYMBOL, interval="D", limit=2)
        if res['retCode'] != 0:
            send_telegram(f"⚠️ Bybit API Error (Klines): {res['retMsg']}")
            return None, None
        klines = res['result']['list']
        # klines[0] = oggi (in corso), klines[1] = ieri (chiusa)
        d_close_now = float(klines[0][4])      
        d_close_prev = float(klines[1][4]) 
        confidence = (d_close_now - d_close_prev) / d_close_prev
        return confidence, d_close_now
    except Exception as e:
        send_telegram(f"🚨 Errore Recupero Dati: {e}")
        return None, None

def execute_smart_trade(side, qty, price):
    """Esegue ordine Limit con fallback Market (Precisione 2 decimali)"""
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
        
        if order['retCode'] != 0:
            send_telegram(f"❌ Bybit ha rifiutato l'ordine: {order['retMsg']}")
            return

        order_id = order['result']['orderId']
        send_telegram(f"🔔 Segnale {side} inviato.\nPrice: {round(price, 2)}\nQty: {round(qty, 2)}")

        # Attesa fill (120 secondi)
        time.sleep(120)

        # Controllo se l'ordine è ancora lì (non fillato)
        check = session.get_open_orders(category="linear", symbol=SYMBOL, orderId=order_id)
        if check['retCode'] == 0 and check['result']['list']:
            session.cancel_order(category="linear", symbol=SYMBOL, orderId=order_id)
            market_order = session.place_order(
                category="linear", symbol=SYMBOL, side=side, orderType="Market",
                qty=str(round(qty, 2)), 
                takeProfit=str(round(tp, 2)), 
                stopLoss=str(round(sl, 2)),
                tpTriggerBy="MarkPrice", slTriggerBy="MarkPrice"
            )
            if market_order['retCode'] == 0:
                send_telegram(f"⚡ Limit scaduto. Entrato MARKET {side}")
            else:
                send_telegram(f"❌ Errore entrata Market: {market_order['retMsg']}")
        else:
            send_telegram(f"✅ Ordine LIMIT fillato correttamente!")
            
    except Exception as e:
        send_telegram(f"🚨 ERRORE CRITICO ESECUZIONE: {e}")

def run_loop():
    global last_prediction
    last_processed_time = "" 
    print(f"🎯 Bot Cecchino TrumpShipper Online. Controllo ogni secondo.")

    while True:
        try:
            now = datetime.now(timezone.utc)
            current_slot = f"{now.hour}:{now.minute}"
            
            # Heartbeat: stampa ogni minuto al secondo zero nei log di Render
            if now.second == 0:
                print(f"🕒 {now.strftime('%H:%M:%S')} UTC - Monitoring...")
            
            # --- LOGICA TF15: Scatta esattamente al secondo 5 del minuto 0, 15, 30, 45 ---
            if now.minute in [0, 15, 30, 45] and now.second == 5:
                if current_slot != last_processed_time:
                    
                    confidence, current_price = get_daily_confidence()
                    
                    if confidence is not None:
                        # Determinazione Direzione
                        if confidence > DELTA_THRESHOLD:
                            prediction = "Long"
                        elif confidence < -DELTA_THRESHOLD:
                            prediction = "Short"
                        else:
                            prediction = last_prediction

                        pos_open = is_position_open()
                        
                        # Log di controllo
                        print(f"📊 [SLOT {current_slot}] Conf: {round(confidence, 6)} | Pred: {prediction} | Open: {pos_open}")

                        if not pos_open:
                            qty = FIXED_SIZE_USD / current_price
                            side = "Buy" if prediction == "Long" else "Sell"
                            execute_smart_trade(side, qty, current_price)
                        
                        last_prediction = prediction
                        last_processed_time = current_slot

            # Aspetta 1 secondo prima del prossimo giro di orologio
            time.sleep(1)
            
        except Exception as e:
            send_telegram(f"🚨 CRASH LOOP: {e}")
            time.sleep(10) # Pausa prima di ripartire dopo un crash

if __name__ == "__main__":
    send_telegram("🤖 Bot TrumpShipper: Cecchino caricato e pronto. Check ogni 15m.")
    run_loop()
