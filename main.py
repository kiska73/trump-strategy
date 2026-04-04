import time
import requests
import os
import pandas as pd
from datetime import datetime, timezone
from pybit.unified_trading import HTTP

# =================================================================
# CONFIGURAZIONE (Usa Environment Variables!)
# =================================================================
API_KEY = os.getenv('API_KEY')
API_SECRET = os.getenv('API_SECRET')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

SYMBOL = "ETHUSDT"
FIXED_SIZE_USD = 1000  # Margine usato per ogni trade
TP_PERC = 6.0 / 100    # Take Profit 6%
SL_PERC = 1.8 / 100    # Stop Loss 2%
TF_MINUTES = 5         # Timeframe candele
EMA_LENGTH = 20        
USE_EMA_FILTER = True

# =================================================================

session = HTTP(
    testnet=False, 
    api_key=API_KEY, 
    api_secret=API_SECRET, 
    recv_window=10000
)

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
    except: pass

def get_data():
    """Recupera chiusura ieri e dati EMA"""
    try:
        d_res = session.get_kline(category="linear", symbol=SYMBOL, interval="D", limit=2)
        daily_close_yesterday = float(d_res['result']['list'][1][4])

        m5_res = session.get_kline(category="linear", symbol=SYMBOL, interval=str(TF_MINUTES), limit=100)
        df = pd.DataFrame(m5_res['result']['list'], columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        df['close'] = df['close'].astype(float)
        df = df.iloc[::-1].reset_index(drop=True)
        df['ema'] = df['close'].ewm(span=EMA_LENGTH, adjust=False).mean()
        
        return daily_close_yesterday, df['ema'].iloc[-1], df['close'].iloc[-1]
    except Exception as e:
        print(f"🚨 Errore API Dati: {e}")
        return None, None, None

def get_current_position():
    """Verifica se ci sono posizioni attive su ETHUSDT"""
    try:
        res = session.get_positions(category="linear", symbol=SYMBOL)
        if res.get('retCode') == 0:
            for pos in res['result']['list']:
                size = float(pos.get('size', 0))
                if size > 0:
                    # Ritorna Side (Buy/Sell) e la quantità
                    return pos.get('side'), size
        return None, 0
    except Exception as e:
        print(f"⚠️ Errore Check Posizione: {e}")
        return None, 0

def execute_trade(side, price):
    """Logica d'ingresso: 2 min Limit -> Fallback Market"""
    try:
        # Calcolo quantità e livelli (Arrotondati per ETHUSDT)
        raw_qty = FIXED_SIZE_USD / price
        qty = float(f"{raw_qty:.2f}") 
        tp = round(price * (1 + TP_PERC), 2) if side == "Buy" else round(price * (1 - TP_PERC), 2)
        sl = round(price * (1 - SL_PERC), 2) if side == "Buy" else round(price * (1 + SL_PERC), 2)

        # 1. INVIO ORDINE LIMIT
        print(f" cercando di entrare LIMIT {side} a {price}...")
        order = session.place_order(
            category="linear", symbol=SYMBOL, side=side, orderType="Limit",
            qty=str(qty), price=str(price), takeProfit=str(tp), stopLoss=str(sl),
            tpTriggerBy="MarkPrice", slTriggerBy="MarkPrice"
        )

        if order.get('retCode') != 0:
            send_telegram(f"❌ Errore Limit: {order.get('retMsg')}")
            return

        order_id = order['result']['orderId']
        
        # 2. ATTESA 2 MINUTI
        time.sleep(120)

        # 3. CONTROLLO E EVENTUALE FALLBACK MARKET
        check = session.get_open_orders(category="linear", symbol=SYMBOL, orderId=order_id)
        orders_list = check['result']['list']

        if len(orders_list) > 0:
            # L'ordine è ancora lì (non fillato o parziale)
            remaining_qty = float(orders_list[0]['leavesQty'])
            if remaining_qty > 0:
                print(f"⚠️ Tempo scaduto. Chiudo limit e entro MARKET per {remaining_qty}")
                session.cancel_order(category="linear", symbol=SYMBOL, orderId=order_id)
                
                # Entra a mercato per la parte mancante
                session.place_order(
                    category="linear", symbol=SYMBOL, side=side, orderType="Market",
                    qty=str(remaining_qty), takeProfit=str(tp), stopLoss=str(sl)
                )
                send_telegram(f"⚡ <b>Entrata Market (Fallback):</b> {side} {remaining_qty}")
        else:
            send_telegram(f"💎 <b>Limit Fillato:</b> {side} {qty} @ {price}")

    except Exception as e:
        send_telegram(f"🚨 Errore critico esecuzione: {e}")

def run_strategy():
    print(f"🚀 BOT AVVIATO | {SYMBOL} | Modalità: 1 Posizione alla volta")
    send_telegram(f"🤖 <b>Bot Online</b>\nLogica: 1 Posizione Max\nEntrata: Limit (2m) poi Market")
    
    last_day = datetime.now(timezone.utc).day

    while True:
        try:
            # --- SINCRONIZZAZIONE ---
            now = datetime.now(timezone.utc)
            seconds_to_wait = (TF_MINUTES - (now.minute % TF_MINUTES)) * 60 - now.second + 2
            if seconds_to_wait <= 0: seconds_to_wait = 2
            time.sleep(seconds_to_wait)

            # --- ANALISI ---
            daily_yesterday, ema_val, current_price = get_data()
            if daily_yesterday is None: continue

            # CONTROLLO POSIZIONI ESISTENTI
            side_active, size = get_current_position()

            # 1. RESET GIORNALIERO (Mezzanotte UTC)
            current_day = datetime.now(timezone.utc).day
            if current_day != last_day:
                if size > 0:
                    exit_side = "Sell" if side_active == "Buy" else "Buy"
                    session.place_order(category="linear", symbol=SYMBOL, side=exit_side, orderType="Market", qty=str(size))
                    send_telegram("🌅 <b>Reset Daily:</b> Posizione chiusa.")
                last_day = current_day

            # 2. LOGICA DI INGRESSO (Solo se NON ci sono posizioni aperte)
            if size == 0:
                # Condizioni Long
                if current_price > daily_yesterday and (not USE_EMA_FILTER or current_price > ema_val):
                    execute_trade("Buy", current_price)
                
                # Condizioni Short
                elif current_price < daily_yesterday and (not USE_EMA_FILTER or current_price < ema_val):
                    execute_trade("Sell", current_price)
            else:
                print(f"ℹ️ Posizione {side_active} già aperta ({size} ETH). Salto analisi.")

        except Exception as e:
            print(f"🚨 Errore loop: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run_strategy()
