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

TF_MINUTES = 30 
# =================================================================

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
    """Controlla se ci sono posizioni attive"""
    try:
        res = session.get_positions(category="linear", symbol=SYMBOL)
        if res.get('retCode') == 0:
            for pos in res['result']['list']:
                size_val = pos.get('size', "0")
                if size_val and size_val != "" and float(size_val) != 0:
                    return True
        return False
    except Exception as e:
        print(f"⚠️ Errore check posizione: {e}")
        return False

def get_daily_confidence():
    """Recupera dati Klines"""
    try:
        time.sleep(random.uniform(1, 3))
        res = session.get_kline(category="linear", symbol=SYMBOL, interval="D", limit=2)
        if res.get('retCode') != 0:
            return None, None
        klines = res['result']['list']
        d_close_now = float(klines[0][4])      
        d_close_prev = float(klines[1][4]) 
        confidence = (d_close_now - d_close_prev) / d_close_prev
        return confidence, d_close_now
    except Exception as e:
        print(f"🚨 Eccezione Klines: {e}")
        return None, None

def execute_smart_trade(side, qty, price):
    """Logica di entrata con gestione messaggi pulita"""
    try:
        tp = price * (1 + TP_PERC) if side == "Buy" else price * (1 - TP_PERC)
        sl = price * (1 - SL_PERC) if side == "Buy" else price * (1 + SL_PERC)

        order = session.place_order(
            category="linear", symbol=SYMBOL, side=side, orderType="Limit",
            price=str(round(price, 2)), qty=str(round(qty, 2)),
            takeProfit=str(round(tp, 2)), stopLoss=str(round(sl, 2)),
            tpTriggerBy="MarkPrice", slTriggerBy="MarkPrice", timeInForce="GTC"
        )
        
        if order.get('retCode') == 0:
            order_id = order['result']['orderId']
            send_telegram(f"🚀 ORDINE {side} INVIATO\nPrezzo: {round(price, 2)}\nQty: {round(qty, 2)}")
            
            time.sleep(120) 
            
            # Controllo se eseguito
            check = session.get_open_orders(category="linear", symbol=SYMBOL, orderId=order_id)
            
            if check.get('retCode') == 0 and not check['result']['list']:
                send_telegram("✅ ORDINE FILLATO (Limit)")
            
            elif check.get('retCode') == 0 and check['result']['list']:
                try:
                    session.cancel_order(category="linear", symbol=SYMBOL, orderId=order_id)
                    session.place_order(
                        category="linear", symbol=SYMBOL, side=side, orderType="Market",
                        qty=str(round(qty, 2)), takeProfit=str(round(tp, 2)), stopLoss=str(round(sl, 2)),
                        tpTriggerBy="MarkPrice", slTriggerBy="MarkPrice"
                    )
                    send_telegram(f"⚡ ENTRATO MARKET {side} (Limit scaduto)")
                except Exception as e:
                    if "110001" in str(e):
                        send_telegram("✅ ORDINE FILLATO (Last second)")
                    else:
                        send_telegram(f"🚨 Errore cancel/market: {e}")
        else:
            send_telegram(f"❌ Bybit rifiuto ordine: {order.get('retMsg')}")
    except Exception as e:
        send_telegram(f"🚨 Errore critico esecuzione: {e}")

def run_loop():
    global last_prediction
    print("🚀 Bot avviato in modalità Silenziosa (TF30).")

    while True:
        try:
            now = datetime.now(timezone.utc)
            minutes_to_next = TF_MINUTES - (now.minute % TF_MINUTES)
            seconds_to_wait = (minutes_to_next * 60) - now.second + 12
            
            if seconds_to_wait <= 0: 
                seconds_to_wait = TF_MINUTES * 60 
            
            total_sleep = seconds_to_wait + random.uniform(1, 4)
            print(f"💤 Sleeping... Next check in {int(total_sleep)}s")
            time.sleep(total_sleep)

            confidence, current_price = get_daily_confidence()
            
            if confidence is not None:
                if confidence > DELTA_THRESHOLD:
                    prediction = "Long"
                elif confidence < -DELTA_THRESHOLD:
                    prediction = "Short"
                else:
                    prediction = last_prediction

                if not is_position_open():
                    qty = FIXED_SIZE_USD / current_price
                    side = "Buy" if prediction == "Long" else "Sell"
                    execute_smart_trade(side, qty, current_price)
                
                last_prediction = prediction

        except Exception as e:
            # Mandiamo messaggio solo se l'errore persiste o è grave
            print(f"Errore loop: {e}")
            time.sleep(60)

if __name__ == "__main__":
    run_loop()
