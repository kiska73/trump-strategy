import time
import requests
import os
import random
from datetime import datetime, timezone, timedelta
from pybit.unified_trading import HTTP

# =================================================================
# CONFIGURAZIONE (Parametri ottimizzati per replica Pine Script)
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

# Variabili di stato
last_prediction = "Long" 
last_trade_day = datetime.now(timezone.utc).day

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    except:
        pass

def get_current_position_info():
    """Ritorna (side, qty) se c'è una posizione, altrimenti (None, 0)"""
    try:
        res = session.get_positions(category="linear", symbol=SYMBOL)
        if res.get('retCode') == 0:
            for pos in res['result']['list']:
                size = float(pos.get('size', 0))
                if size > 0:
                    return pos.get('side'), size
        return None, 0
    except Exception as e:
        print(f"⚠️ Errore check posizione: {e}")
        return None, 0

def force_daily_reset():
    """Chiude qualsiasi posizione aperta all'inizio del nuovo giorno"""
    try:
        side, qty = get_current_position_info()
        if side:
            exit_side = "Sell" if side == "Buy" else "Buy"
            session.place_order(
                category="linear", symbol=SYMBOL, side=exit_side, 
                orderType="Market", qty=str(qty)
            )
            send_telegram(f"🌅 **RESET GIORNALIERO**\nChiuso {side} per fine candela Daily.\nSi riparte da zero!")
            time.sleep(5)
    except Exception as e:
        send_telegram(f"🚨 Errore durante il reset giornaliero: {e}")

def get_daily_confidence():
    """Recupera dati Klines e calcola la direzione"""
    try:
        time.sleep(random.uniform(1, 2))
        res = session.get_kline(category="linear", symbol=SYMBOL, interval="D", limit=2)
        if res.get('retCode') != 0:
            return None, None
        klines = res['result']['list']
        # klines[0] è oggi (in corso), klines[1] è ieri (chiusa)
        d_close_now = float(klines[0][4])      
        d_close_prev = float(klines[1][4]) 
        confidence = (d_close_now - d_close_prev) / d_close_prev
        return confidence, d_close_now
    except Exception as e:
        print(f"🚨 Eccezione Klines: {e}")
        return None, None

def execute_smart_trade(side, qty, price):
    """Esegue ordine Limit con fallback Market e TP/SL"""
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
            send_telegram(f"🚀 **NUOVA OPERAZIONE {side}**\nPrezzo: {round(price, 2)}\nQty: {round(qty, 2)}")
            
            time.sleep(120) # Attesa per esecuzione Limit
            
            check = session.get_open_orders(category="linear", symbol=SYMBOL, orderId=order_id)
            
            if check.get('retCode') == 0 and not check['result']['list']:
                send_telegram("✅ Ordine LIMIT eseguito.")
            elif check.get('retCode') == 0 and check['result']['list']:
                try:
                    session.cancel_order(category="linear", symbol=SYMBOL, orderId=order_id)
                    session.place_order(
                        category="linear", symbol=SYMBOL, side=side, orderType="Market",
                        qty=str(round(qty, 2)), takeProfit=str(round(tp, 2)), stopLoss=str(round(sl, 2)),
                        tpTriggerBy="MarkPrice", slTriggerBy="MarkPrice"
                    )
                    send_telegram(f"⚡ Entrato MARKET (Limit scaduto)")
                except Exception as e:
                    if "110001" in str(e): send_telegram("✅ Ordine eseguito last second.")
        else:
            send_telegram(f"❌ Bybit Error: {order.get('retMsg')}")
    except Exception as e:
        send_telegram(f"🚨 Errore critico: {e}")

def run_loop():
    global last_prediction, last_trade_day
    print(f"🤖 Bot Online | TF: {TF_MINUTES}m | Daily Reset: Attivo")

    while True:
        try:
            now = datetime.now(timezone.utc)
            
            # --- 1. CONTROLLO RESET GIORNALIERO (Mezzanotte UTC) ---
            if now.day != last_trade_day:
                force_daily_reset()
                last_trade_day = now.day
            
            # --- 2. CALCOLO ATTESA PROSSIMO SLOT ---
            minutes_to_next = TF_MINUTES - (now.minute % TF_MINUTES)
            seconds_to_wait = (minutes_to_next * 60) - now.second + 12
            
            print(f"💤 In attesa del prossimo check ({int(seconds_to_wait)}s)...")
            time.sleep(max(seconds_to_wait, 5))

            # --- 3. ANALISI E OPERATIVITÀ ---
            confidence, current_price = get_daily_confidence()
            
            if confidence is not None:
                if confidence > DELTA_THRESHOLD:
                    prediction = "Long"
                elif confidence < -DELTA_THRESHOLD:
                    prediction = "Short"
                else:
                    prediction = last_prediction

                side_active, _ = get_current_position_info()
                
                # Entra solo se siamo Flat (nessuna posizione)
                if side_active is None:
                    qty = FIXED_SIZE_USD / current_price
                    side_to_open = "Buy" if prediction == "Long" else "Sell"
                    execute_smart_trade(side_to_open, qty, current_price)
                
                last_prediction = prediction

        except Exception as e:
            print(f"Errore loop: {e}")
            time.sleep(60)

if __name__ == "__main__":
    run_loop()
