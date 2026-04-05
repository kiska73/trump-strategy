import time
import requests
import os
import pandas as pd
from datetime import datetime, timezone
from pybit.unified_trading import HTTP

# --- CONFIGURAZIONE ---
API_KEY = os.getenv('API_KEY')
API_SECRET = os.getenv('API_SECRET')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

SYMBOL = "ETHUSDT"
FIXED_SIZE_USD = 1000 
TP_PERC = 6.0 / 100
SL_PERC = 1.8 / 100
TF_MINUTES = 5
EMA_LENGTH = 20
USE_EMA_FILTER = True

session = HTTP(
    testnet=False, 
    api_key=API_KEY, 
    api_secret=API_SECRET, 
    recv_window=20000 
)

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
    except: pass

def get_current_position():
    """Ritorna Side, Size, EntryPrice e PNL non realizzato"""
    try:
        res = session.get_positions(category="linear", symbol=SYMBOL)
        if res.get('retCode') == 0:
            for pos in res['result']['list']:
                size = float(pos.get('size', 0))
                if size > 0:
                    return pos.get('side'), size, float(pos.get('avgPrice', 0)), float(pos.get('unrealisedPnl', 0))
        return None, 0, 0, 0
    except Exception as e:
        print(f"Errore Check Posizione: {e}")
        return None, 0, 0, 0

def get_data():
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

def execute_trade(side, price):
    try:
        raw_qty = FIXED_SIZE_USD / price
        qty = float(f"{raw_qty:.2f}") 
        tp = round(price * (1 + TP_PERC), 2) if side == "Buy" else round(price * (1 - TP_PERC), 2)
        sl = round(price * (1 - SL_PERC), 2) if side == "Buy" else round(price * (1 + SL_PERC), 2)

        send_telegram(f"🔍 <b>Bias {side}:</b> Ordine Limit piazzato a {price}")

        order = session.place_order(
            category="linear", symbol=SYMBOL, side=side, orderType="Limit",
            qty=str(qty), price=str(price), takeProfit=str(tp), stopLoss=str(sl),
            tpTriggerBy="MarkPrice", slTriggerBy="MarkPrice"
        )

        if order.get('retCode') != 0:
            send_telegram(f"❌ <b>Errore:</b> {order.get('retMsg')}")
            return

        order_id = order['result']['orderId']
        time.sleep(120) 

        check = session.get_open_orders(category="linear", symbol=SYMBOL, orderId=order_id)
        if len(check['result']['list']) > 0:
            session.cancel_order(category="linear", symbol=SYMBOL, orderId=order_id)
            session.place_order(
                category="linear", symbol=SYMBOL, side=side, orderType="Market",
                qty=str(qty), takeProfit=str(tp), stopLoss=str(sl)
            )
            send_telegram(f"⚡ <b>Limit fallito, entro Market:</b>\n{side} {qty} ETH\nTP: {tp} | SL: {sl}")
        else:
            send_telegram(f"💎 <b>Limit Fillato:</b>\n{side} {qty} ETH @ {price}\nTP: {tp} | SL: {sl}")

    except Exception as e:
        send_telegram(f"🚨 Errore: {e}")

def run_strategy():
    # --- LOGICA DI RIAVVIO ---
    side_active, size, entry_p, pnl = get_current_position()
    status_msg = "🤖 <b>Bot Online su Render</b>\n"
    if size > 0:
        status_msg += f"✅ Posizione Rilevata: {side_active}\nQuantità: {size} ETH\nEntrata: {entry_p}"
    else:
        status_msg += "⏸ Nessuna posizione attiva. In attesa di segnale."
    send_telegram(status_msg)
    
    last_day = datetime.now(timezone.utc).day

    while True:
        try:
            now = datetime.now(timezone.utc)
            seconds_to_wait = (TF_MINUTES - (now.minute % TF_MINUTES)) * 60 - now.second + 2
            if seconds_to_wait <= 0: seconds_to_wait = 2
            time.sleep(seconds_to_wait)

            daily_yesterday, ema_val, current_price = get_data()
            if daily_yesterday is None: continue

            side_active, size, entry_p, pnl = get_current_position()

            # RESET GIORNALIERO E REPORT PNL
            current_day = datetime.now(timezone.utc).day
            if current_day != last_day:
                if size > 0:
                    pnl_perc = (pnl / FIXED_SIZE_USD) * 100
                    exit_side = "Sell" if side_active == "Buy" else "Buy"
                    session.place_order(category="linear", symbol=SYMBOL, side=exit_side, orderType="Market", qty=str(size))
                    send_telegram(f"🌅 <b>Reset Daily:</b>\nPosizione {side_active} chiusa.\nPNL Stimato: <b>{pnl_perc:.2f}%</b>")
                else:
                    send_telegram("🌅 <b>Reset Daily:</b> Nessuna posizione attiva.")
                last_day = current_day

            # ANALISI INGRESSO
            if size == 0:
                if current_price > daily_yesterday and (not USE_EMA_FILTER or current_price > ema_val):
                    execute_trade("Buy", current_price)
                elif current_price < daily_yesterday and (not USE_EMA_FILTER or current_price < ema_val):
                    execute_trade("Sell", current_price)
            else:
                print(f"ℹ️ {side_active} attiva ({size}). PNL: {pnl} USDT")

        except Exception as e:
            print(f"Errore: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run_strategy()
