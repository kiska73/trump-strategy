import time
import requests
import os
import random
from datetime import datetime, timezone
from pybit.unified_trading import HTTP

# =================================================================
# CONFIGURAZIONE
# =================================================================
API_KEY = os.getenv('API_KEY')
API_SECRET = os.getenv('API_SECRET')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

SYMBOL = "ETHUSDT"
FIXED_SIZE_USD = 1000 
DELTA_THRESHOLD = 0.0001
SL_PERC = 1.3 / 100
TP_PERC = 6.0 / 100
TF_MINUTES = 15  
# =================================================================

session = HTTP(
    testnet=False, 
    api_key=API_KEY, 
    api_secret=API_SECRET, 
    recv_window=10000
)

last_prediction = "Long" 
last_trade_day = datetime.now(timezone.utc).day

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
    except:
        pass

def get_current_position_info():
    try:
        res = session.get_positions(category="linear", symbol=SYMBOL)
        if res.get('retCode') == 0:
            for pos in res['result']['list']:
                size = float(pos.get('size', 0))
                if size > 0:
                    return pos.get('side'), size, float(pos.get('avgPrice', 0)), float(pos.get('markPrice', 0))
        return None, 0, 0, 0
    except Exception as e:
        print(f"⚠️ Errore check posizione: {e}")
        return None, 0, 0, 0

def force_daily_reset():
    try:
        side, size, entry_price, mark_price = get_current_position_info()
        if side and size > 0:
            # Calcolo % netta del movimento di prezzo
            if side == "Buy":
                price_diff_perc = ((mark_price - entry_price) / entry_price) * 100
            else:
                price_diff_perc = ((entry_price - mark_price) / entry_price) * 100
            
            exit_side = "Sell" if side == "Buy" else "Buy"
            session.place_order(
                category="linear", symbol=SYMBOL, side=exit_side, 
                orderType="Market", qty=str(size)
            )
            
            emoji = "🤑" if price_diff_perc > 0 else "🩸"
            sign = "+" if price_diff_perc > 0 else ""
            
            msg = (f"🌅 <b>RESET GIORNALIERO</b>\n"
                   f"Chiusa pos: <b>{side.upper()}</b>\n"
                   f"Risultato: <b>{sign}{round(price_diff_perc, 2)}%</b> {emoji}")
            
            send_telegram(msg)
            time.sleep(5)
    except Exception as e:
        send_telegram(f"🚨 Errore reset: {e}")

def get_daily_confidence():
    try:
        time.sleep(random.uniform(0.5, 1.5))
        res = session.get_kline(category="linear", symbol=SYMBOL, interval="D", limit=2)
        if res.get('retCode') != 0: return None, None
        klines = res['result']['list']
        d_close_now = float(klines[0][4])      
        d_close_prev = float(klines[1][4]) 
        confidence = (d_close_now - d_close_prev) / d_close_prev
        return confidence, d_close_now
    except Exception as e:
        print(f"🚨 Eccezione Klines: {e}")
        return None, None

def execute_smart_trade(side, qty, price):
    try:
        tp = price * (1 + TP_PERC) if side == "Buy" else price * (1 - TP_PERC)
        sl = price * (1 - SL_PERC) if side == "Buy" else price * (1 + SL_PERC)
        direction = "🟢 LONG" if side == "Buy" else "🔴 SHORT"

        order = session.place_order(
            category="linear", symbol=SYMBOL, side=side, orderType="Limit",
            price=str(round(price, 2)), qty=str(round(qty, 3)),
            takeProfit=str(round(tp, 2)), stopLoss=str(round(sl, 2)),
            tpTriggerBy="MarkPrice", slTriggerBy="MarkPrice", timeInForce="GTC"
        )
        
        if order.get('retCode') == 0:
            order_id = order['result']['orderId']
            send_telegram(f"🎯 <b>SEGNALE {TF_MINUTES}m</b>\n<b>{direction}</b>\nLimit: {round(price, 2)}\nQty: {round(qty, 3)}")
            
            time.sleep(45) 
            check = session.get_open_orders(category="linear", symbol=SYMBOL, orderId=order_id)
            
            if check.get('retCode') == 0 and len(check['result']['list']) > 0:
                session.cancel_order(category="linear", symbol=SYMBOL, orderId=order_id)
                session.place_order(
                    category="linear", symbol=SYMBOL, side=side, orderType="Market",
                    qty=str(round(qty, 3)), takeProfit=str(round(tp, 2)), stopLoss=str(round(sl, 2)),
                    tpTriggerBy="MarkPrice", slTriggerBy="MarkPrice"
                )
                send_telegram(f"⚡ <b>Limit cancellato.</b> Entrato a Mercato.")
            else:
                send_telegram(f"✅ <b>Limit Fillato.</b> Siamo in posizione.")
        else:
            send_telegram(f"❌ <b>Bybit:</b> {order.get('retMsg')}")
    except Exception as e:
        send_telegram(f"🚨 Errore trade: {e}")

def run_loop():
    global last_prediction, last_trade_day
    
    send_telegram(f"🚀 <b>BOT ONLINE</b>\n<b>Coppia:</b> {SYMBOL} | <b>TF:</b> {TF_MINUTES}m")
    print(f"🤖 Bot Online | {SYMBOL}")

    while True:
        try:
            now = datetime.now(timezone.utc)
            
            if now.day != last_trade_day:
                force_daily_reset()
                last_trade_day = now.day
            
            minutes_to_next = TF_MINUTES - (now.minute % TF_MINUTES)
            seconds_to_wait = (minutes_to_next * 60) - now.second + 5 
            
            if seconds_to_wait > 0:
                time.sleep(seconds_to_wait)

            confidence, current_price = get_daily_confidence()
            if confidence is not None:
                if confidence > DELTA_THRESHOLD:
                    prediction = "Long"
                elif confidence < -DELTA_THRESHOLD:
                    prediction = "Short"
                else:
                    prediction = last_prediction

                side_active, _, _, _ = get_current_position_info()
                
                if side_active is None:
                    qty = FIXED_SIZE_USD / current_price
                    side_to_open = "Buy" if prediction == "Long" else "Sell"
                    execute_smart_trade(side_to_open, qty, current_price)
                
                last_prediction = prediction

        except Exception as e:
            print(f"Errore loop: {e}")
            time.sleep(30)

if __name__ == "__main__":
    run_loop()
