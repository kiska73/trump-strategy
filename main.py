import time
import requests
import os
import pandas as pd
from datetime import datetime, timezone
from pybit.unified_trading import HTTP

# =================================================================
# CONFIGURAZIONE (Usa Environment Variables su Render!)
# =================================================================
API_KEY = os.getenv('API_KEY')
API_SECRET = os.getenv('API_SECRET')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

SYMBOL = "ETHUSDT"
FIXED_SIZE_USD = 1000  # Quantità in Dollari per ogni trade
TP_PERC = 6.0 / 100    # Take Profit 6%
SL_PERC = 2.0 / 100    # Stop Loss 2%
TF_MINUTES = 5         # Timeframe 5 minuti
EMA_LENGTH = 20        # Lunghezza Media Mobile
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
    except:
        pass

def get_data():
    """Recupera chiusura daily di ieri e calcola EMA 20 sulle candele 5m"""
    try:
        # 1. Chiusura Daily di ieri
        d_res = session.get_kline(category="linear", symbol=SYMBOL, interval="D", limit=2)
        daily_close_yesterday = float(d_res['result']['list'][1][4])

        # 2. Candele 5m per calcolo EMA
        m5_res = session.get_kline(category="linear", symbol=SYMBOL, interval=str(TF_MINUTES), limit=100)
        
        # Creazione DataFrame
        df = pd.DataFrame(m5_res['result']['list'], columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        df['close'] = df['close'].astype(float)
        
        # Invertiamo i dati (Bybit li manda dal più recente al più vecchio)
        df = df.iloc[::-1].reset_index(drop=True)
        
        # Calcolo EMA (Metodo EWM di Pandas = identico a Pine Script EMA)
        df['ema'] = df['close'].ewm(span=EMA_LENGTH, adjust=False).mean()
        
        current_ema = df['ema'].iloc[-1]
        current_price = df['close'].iloc[-1]

        return daily_close_yesterday, current_ema, current_price
    except Exception as e:
        print(f"🚨 Errore recupero dati: {e}")
        return None, None, None

def get_current_position():
    """Controlla se ci sono posizioni aperte sul simbolo"""
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

def execute_trade(side, price):
    """Esegue l'ordine Market con TP e SL impostati"""
    try:
        qty = round(FIXED_SIZE_USD / price, 3)
        # Calcolo TP e SL
        tp = round(price * (1 + TP_PERC), 2) if side == "Buy" else round(price * (1 - TP_PERC), 2)
        sl = round(price * (1 - SL_PERC), 2) if side == "Buy" else round(price * (1 + SL_PERC), 2)
        
        order = session.place_order(
            category="linear",
            symbol=SYMBOL,
            side=side,
            orderType="Market",
            qty=str(qty),
            takeProfit=str(tp),
            stopLoss=str(sl),
            tpTriggerBy="MarkPrice",
            slTriggerBy="MarkPrice"
        )
        
        if order.get('retCode') == 0:
            emoji = "🟢" if side == "Buy" else "🔴"
            msg = (f"{emoji} <b>NUOVO ORDINE {side.upper()}</b>\n"
                   f"Prezzo: {price}\n"
                   f"TP: {tp} | SL: {sl}")
            send_telegram(msg)
            print(f"✅ Ordine {side} eseguito!")
        else:
            print(f"❌ Errore Bybit: {order.get('retMsg')}")
            
    except Exception as e:
        send_telegram(f"🚨 Errore durante l'esecuzione del trade: {e}")

def run_strategy():
    print(f"🚀 BOT ONLINE | {SYMBOL} | TF {TF_MINUTES}m")
    send_telegram(f"🤖 <b>Bot Online</b>\nAsset: {SYMBOL}\nTF: {TF_MINUTES}m\nFiltro EMA: {USE_EMA_FILTER}")
    
    last_day = datetime.now(timezone.utc).day

    while True:
        try:
            now = datetime.now(timezone.utc)
            
            # --- SINCRONIZZAZIONE 5 MINUTI ---
            # Calcola quanto manca alla fine della candela + 3 secondi di sicurezza
            seconds_to_wait = (TF_MINUTES - (now.minute % TF_MINUTES)) * 60 - now.second + 3
            if seconds_to_wait <= 0: seconds_to_wait = 3
            
            print(f"⏳ In attesa della prossima candela... ({seconds_to_wait}s)")
            time.sleep(seconds_to_wait)

            # --- ANALISI ---
            daily_yesterday, ema_val, current_price = get_data()
            if daily_yesterday is None: continue

            side_active, size = get_current_position()

            # 1. RESET GIORNALIERO (Chiusura posizioni a mezzanotte UTC)
            current_day = datetime.now(timezone.utc).day
            if current_day != last_day:
                if size > 0:
                    exit_side = "Sell" if side_active == "Buy" else "Buy"
                    session.place_order(category="linear", symbol=SYMBOL, side=exit_side, orderType="Market", qty=str(size))
                    send_telegram("🌅 <b>Reset Daily:</b> Posizione chiusa per cambio giornata.")
                last_day = current_day

            # 2. LOGICA DI INGRESSO (Solo se non abbiamo già una posizione)
            if size == 0:
                # Condizioni EMA
                ema_long_ok = not USE_EMA_FILTER or current_price > ema_val
                ema_short_ok = not USE_EMA_FILTER or current_price < ema_val

                # LONG: Prezzo > Chiusura Ieri E Filtro EMA OK
                if current_price > daily_yesterday and ema_long_ok:
                    execute_trade("Buy", current_price)
                
                # SHORT: Prezzo < Chiusura Ieri E Filtro EMA OK
                elif current_price < daily_yesterday and ema_short_ok:
                    execute_trade("Sell", current_price)

        except Exception as e:
            print(f"🚨 Errore nel loop: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run_strategy()
