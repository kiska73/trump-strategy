import time
import requests
import pandas as pd
from datetime import datetime
from pybit.unified_trading import HTTP

# =================================================================
# CONFIGURAZIONE UTENTE
# =================================================================
API_KEY      = "26tNwg57oCDvlNidYT"
API_SECRET   = "WQ84S2dhZ9FVoXkJ7WqWCt6F7HSXR4fsrqhH"
TELEGRAM_TOKEN   = "6916198243:AAFTF66uLYSeqviL5YnfGtbUkSjTwPzah6s"
TELEGRAM_CHAT_ID = "820279313"

SYMBOL = "ETHUSDT"
FIXED_SIZE_USD = 500  # Size fissa in USDT per ogni trade
DELTA_THRESHOLD = 0.0001
SL_PERC = 1.3 / 100
TP_PERC = 5.0 / 100
# =================================================================

session = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET)

# Variabile di stato per mantenere la direzione (come nz(prediction[1]))
last_prediction = "Long" 

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    except Exception as e:
        print(f"Errore Telegram: {e}")

def get_daily_confidence():
    """Recupera i dati giornalieri per calcolare il trend"""
    try:
        res = session.get_kline(category="linear", symbol=SYMBOL, interval="D", limit=2)
        klines = res['result']['list']
        # Bybit: [0] è candela attuale, [1] è ieri
        d_close_now = float(klines[0][4])      
        d_close_prev = float(klines[1][4]) 
        confidence = (d_close_now - d_close_prev) / d_close_prev
        return confidence, d_close_now
    except Exception as e:
        print(f"Errore nel recupero dati daily: {e}")
        return None, None

def execute_smart_trade(side, qty, price):
    """Gestione ordine Limit con fallback Market dopo 2 minuti"""
    try:
        # Calcolo TP e SL
        if side == "Buy":
            tp = price * (1 + TP_PERC)
            sl = price * (1 - SL_PERC)
        else:
            tp = price * (1 - TP_PERC)
            sl = price * (1 + SL_PERC)

        # 1. Invio Ordine LIMIT
        order = session.place_order(
            category="linear",
            symbol=SYMBOL,
            side=side,
            orderType="Limit",
            price=str(round(price, 2)),
            qty=str(qty),
            takeProfit=str(round(tp, 2)),
            stopLoss=str(round(sl, 2)),
            tpTriggerBy="MarkPrice",
            slTriggerBy="MarkPrice",
            timeInForce="GTC"
        )
        order_id = order['result']['orderId']
        send_telegram(f"🔔 Segnale {side} rilevato.\nInviato LIMIT a {round(price, 2)}...")

        # 2. Attesa 120 secondi
        time.sleep(120)

        # 3. Verifica se fillato
        check = session.get_open_orders(category="linear", symbol=SYMBOL, orderId=order_id)
        if check['result']['list']:
            # Se ancora aperto, cancella e vai Market
            session.cancel_order(category="linear", symbol=SYMBOL, orderId=order_id)
            session.place_order(
                category="linear",
                symbol=SYMBOL,
                side=side,
                orderType="Market",
                qty=str(qty),
                takeProfit=str(round(tp, 2)),
                stopLoss=str(round(sl, 2)),
                tpTriggerBy="MarkPrice",
                slTriggerBy="MarkPrice"
            )
            send_telegram(f"⚡ Limit non fillato. Entrato MARKET {side} (Qty: {qty})")
        else:
            send_telegram(f"✅ Ordine LIMIT fillato con successo!")

    except Exception as e:
        send_telegram(f"❌ Errore esecuzione: {e}")

def run_loop():
    global last_prediction
    last_processed_day = -1
    print(f"Bot TrumpShipper attivo su {SYMBOL}. In attesa dell'apertura Daily (00:01 UTC)...")

    while True:
        now = datetime.utcnow()
        
        # Scatta 1 minuto dopo la mezzanotte UTC
        if now.hour == 0 and now.minute == 1 and now.day != last_processed_day:
            
            confidence, current_price = get_daily_confidence()
            
            if confidence is not None:
                # Logica del segnale
                if confidence > DELTA_THRESHOLD:
                    prediction = "Long"
                elif confidence < -DELTA_THRESHOLD:
                    prediction = "Short"
                else:
                    prediction = last_prediction # nz(prediction[1])

                # Calcolo quantità (fisso 2 decimali per ETH)
                qty = round(FIXED_SIZE_USD / current_price, 2)
                side = "Buy" if prediction == "Long" else "Sell"

                # Esecuzione
                execute_smart_trade(side, qty, current_price)
                
                last_prediction = prediction
                last_processed_day = now.day
                print(f"Operazione del giorno completata: {prediction}")

        # Controllo ogni 30 secondi per non mancare il minuto 01
        time.sleep(30)

if __name__ == "__main__":
    try:
        send_telegram("🤖 Bot TrumpShipper ETH Live: Sistema Pronto.")
        run_loop()
    except KeyboardInterrupt:
        print("Bot fermato manualmente.")
