import time
import requests
import os
from datetime import datetime, timezone
from pybit.unified_trading import HTTP

# =================================================================
# CONFIGURAZIONE (Assicurati che siano su Render)
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

session = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET)
last_prediction = "Long" 

def send_telegram(message):
    """Invia notifiche e logga errori di invio"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        res = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
        if res.status_code != 200:
            print(f"❌ Errore invio Telegram: {res.text}")
    except Exception as e:
        print(f"❌ Errore connessione Telegram: {e}")

def is_position_open():
    """Verifica posizioni e avvisa se le API falliscono"""
    try:
        res = session.get_positions(category="linear", symbol=SYMBOL)
        if res['retCode'] == 0:
            for pos in res['result']['list']:
                size_val = pos.get('size', "0")
                if size_val and size_val != "" and float(size_val) != 0:
                    return True
            return False
        else:
            # Se Bybit risponde con un errore interno (es. manutenzione)
            send_telegram(f"⚠️ Bybit Error (Position Check): {res['retMsg']}")
            return True # Prudenza: non apriamo nulla se non siamo sicuri
    except Exception as e:
        send_telegram(f"🚨 Eccezione Critica (Position Check): {e}")
        return True

def get_daily_confidence():
    """Recupera dati e avvisa su Telegram in caso di buco dati"""
    try:
        res = session.get_kline(category="linear", symbol=SYMBOL, interval="D", limit=2)
        if res['retCode'] != 0:
            send_telegram(f"⚠️ Errore Dati Bybit: {res['retMsg']}")
            return None, None
        klines = res['result']['list']
        d_close_now = float(klines[0][4])      
        d_close_prev = float(klines[1][4]) 
        confidence = (d_close_now - d_close_prev) / d_close_prev
        return confidence, d_close_now
    except Exception as e:
        send_telegram(f"🚨 Errore Connessione Dati: {e}")
        return None, None

def execute_smart_trade(side, qty, price):
    """Esecuzione ordine con report dettagliato errori"""
    try:
        tp = price * (1 + TP_PERC) if side == "Buy" else price * (1 - TP_PERC)
        sl = price * (1 - SL_PERC) if side == "Buy" else price * (1 + SL_PERC)

        print(f"🚀 TENTATIVO ORDINE: {side} | Qty: {round(qty, 2)}")
        
        order = session.place_order(
            category="linear", symbol=SYMBOL, side=side, orderType="Limit",
            price=str(round(price, 2)), 
            qty=str(round(qty, 2)),
            takeProfit=str(round(tp, 2)), 
            stopLoss=str(round(sl, 2)),
            tpTriggerBy="MarkPrice", slTriggerBy="MarkPrice", timeInForce="GTC"
        )
        
        if order['retCode'] != 0:
            send_telegram(f"❌ Ordine Rifiutato da Bybit: {order['retMsg']}")
            return

        order_id = order['result']['orderId']
        send_telegram(f"🔔 Segnale {side} inviato.\nPrezzo: {round(price, 2)}\nQty: {round(qty, 2)}")

        time.sleep(120)

        # Controllo Fill
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
            send_telegram(f"✅ LIMIT FILLATO!")
            
    except Exception as e:
        send_telegram(f"🚨 ERRORE FATALE EXECUTE: {e}")

def run_loop():
    global last_prediction
    last_processed_time = "" 
    print(f"🤖 Bot TrumpShipper con Alert Attivi.")

    while True:
        try:
            now = datetime.now(timezone.utc)
            current_slot = f"{now.hour}:{now.minute}"
            
            # Log di presenza (Heartbeat) solo su Render
            if now.second < 10:
                 print(f"🕒 {now.strftime('%H:%M:%S')} UTC - Monitoring...")
                 time.sleep(10)
                 continue

            # Check Slot TF15 + 5 secondi
            if now.minute in [0, 15, 30, 45] and now.second >= 5:
                if current_slot != last_processed_time:
                    
                    confidence, current_price = get_daily_confidence()
                    
                    if confidence is not None:
                        if confidence > DELTA_THRESHOLD:
                            prediction = "Long"
                        elif confidence < -DELTA_THRESHOLD:
                            prediction = "Short"
                        else:
                            prediction = last_prediction

                        pos_open = is_position_open()
                        
                        # Log su Render
                        print(f"📊 [SLOT {current_slot}] Conf: {round(confidence, 6)} | Pred: {prediction} | Aperta: {pos_open}")

                        if not pos_open:
                            qty = FIXED_SIZE_USD / current_price
                            side = "Buy" if prediction == "Long" else "Sell"
                            execute_smart_trade(side, qty, current_price)
                        
                        last_prediction = prediction
                        last_processed_time = current_slot

            time.sleep(10)
            
        except Exception as e:
            # Questo cattura qualsiasi errore nel loop principale e ti avvisa
            send_telegram(f"🚨 CRASH NEL LOOP PRINCIPALE: {e}")
            time.sleep(60) # Aspetta un minuto prima di riprovare per evitare spam

if __name__ == "__main__":
    send_telegram("🤖 Bot TrumpShipper: Sistema Online e Alert Attivi.")
    run_loop()
