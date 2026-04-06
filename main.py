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

last_closed_trade_id = None

# ------------------------------------------------

def send_telegram(message):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    try:
        requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML"
            },
            timeout=10
        )
    except:
        pass

# ------------------------------------------------

def get_current_position():

    try:

        res = session.get_positions(
            category="linear",
            symbol=SYMBOL
        )

        if res["retCode"] == 0:

            for pos in res["result"]["list"]:

                size = float(pos["size"])

                if size > 0:
                    return (
                        pos["side"],
                        size,
                        float(pos["avgPrice"]),
                        float(pos["unrealisedPnl"])
                    )

        return None, 0, 0, 0

    except Exception as e:
        print("Errore posizione:", e)
        return None, 0, 0, 0

# ------------------------------------------------

def get_last_closed_trade():

    global last_closed_trade_id

    try:

        res = session.get_closed_pnl(
            category="linear",
            symbol=SYMBOL,
            limit=1
        )

        trades = res["result"]["list"]

        if not trades:
            return None

        trade = trades[0]

        trade_id = trade["orderId"]

        if trade_id == last_closed_trade_id:
            return None

        last_closed_trade_id = trade_id

        pnl = float(trade["closedPnl"])
        side = trade["side"]

        return side, pnl

    except Exception as e:
        print("Errore closed pnl:", e)
        return None

# ------------------------------------------------

def get_data():

    try:

        d_res = session.get_kline(
            category="linear",
            symbol=SYMBOL,
            interval="D",
            limit=2
        )

        daily_close_yesterday = float(
            d_res["result"]["list"][1][4]
        )

        m5_res = session.get_kline(
            category="linear",
            symbol=SYMBOL,
            interval=str(TF_MINUTES),
            limit=100
        )

        df = pd.DataFrame(
            m5_res["result"]["list"],
            columns=[
                "time","open","high","low",
                "close","volume","turnover"
            ]
        )

        df["close"] = df["close"].astype(float)

        df = df.iloc[::-1].reset_index(drop=True)

        df["ema"] = df["close"].ewm(
            span=EMA_LENGTH,
            adjust=False
        ).mean()

        return (
            daily_close_yesterday,
            df["ema"].iloc[-1],
            df["close"].iloc[-1]
        )

    except Exception as e:

        print("Errore dati:", e)

        return None, None, None

# ------------------------------------------------

def execute_trade(side, price):

    try:

        raw_qty = FIXED_SIZE_USD / price
        qty = float(f"{raw_qty:.2f}")

        tp = round(
            price * (1 + TP_PERC)
            if side == "Buy"
            else price * (1 - TP_PERC),
            2
        )

        sl = round(
            price * (1 - SL_PERC)
            if side == "Buy"
            else price * (1 + SL_PERC),
            2
        )

        send_telegram(
            f"🔍 Bias {side}\n"
            f"Limit a {price:.2f}"
        )

        order = session.place_order(
            category="linear",
            symbol=SYMBOL,
            side=side,
            orderType="Limit",
            qty=str(qty),
            price=str(round(price,2)),
            takeProfit=str(tp),
            stopLoss=str(sl)
        )

        if order["retCode"] != 0:
            send_telegram(
                f"❌ Errore ordine:\n{order['retMsg']}"
            )
            return

        order_id = order["result"]["orderId"]

        time.sleep(120)

        check = session.get_open_orders(
            category="linear",
            symbol=SYMBOL,
            orderId=order_id
        )

        if check["result"]["list"]:

            try:
                session.cancel_order(
                    category="linear",
                    symbol=SYMBOL,
                    orderId=order_id
                )
            except:
                pass

            session.place_order(
                category="linear",
                symbol=SYMBOL,
                side=side,
                orderType="Market",
                qty=str(qty),
                takeProfit=str(tp),
                stopLoss=str(sl)
            )

            send_telegram(
                f"⚡ Entrata Market\n"
                f"{side} {qty} ETH\n"
                f"TP {tp}\nSL {sl}"
            )

        else:

            send_telegram(
                f"💎 Limit Fill\n"
                f"{side} {qty} ETH\n"
                f"Entry {price:.2f}\n"
                f"TP {tp}\nSL {sl}"
            )

    except Exception as e:

        send_telegram(f"🚨 Errore trade: {e}")

# ------------------------------------------------

def run_strategy():

    send_telegram("🤖 Bot Online")

    last_day = datetime.now(timezone.utc).day

    while True:

        try:

            now = datetime.now(timezone.utc)

            wait = (
                (TF_MINUTES - (now.minute % TF_MINUTES)) * 60
                - now.second + 2
            )

            if wait <= 0:
                wait = 2

            time.sleep(wait)

            closed = get_last_closed_trade()

            if closed:

                side, pnl = closed

                pnl_perc = (pnl / FIXED_SIZE_USD) * 100

                if pnl > 0:

                    send_telegram(
                        f"✅ TP preso\n"
                        f"PNL {pnl_perc:.2f}%"
                    )

                else:

                    send_telegram(
                        f"❌ SL preso\n"
                        f"PNL {pnl_perc:.2f}%"
                    )

            daily_yesterday, ema_val, price = get_data()

            if daily_yesterday is None:
                continue

            side_active, size, entry, pnl = get_current_position()

            current_day = datetime.now(timezone.utc).day

            if current_day != last_day:

                if size > 0:

                    exit_side = "Sell" if side_active == "Buy" else "Buy"

                    session.place_order(
                        category="linear",
                        symbol=SYMBOL,
                        side=exit_side,
                        orderType="Market",
                        qty=str(size)
                    )

                    send_telegram(
                        "🌅 Reset Daily\n"
                        "Posizione chiusa"
                    )

                else:

                    send_telegram(
                        "🌅 Reset Daily\n"
                        "Nessuna posizione"
                    )

                last_day = current_day

            if size == 0:

                if (
                    price > daily_yesterday
                    and (not USE_EMA_FILTER or price > ema_val)
                ):

                    execute_trade("Buy", price)

                elif (
                    price < daily_yesterday
                    and (not USE_EMA_FILTER or price < ema_val)
                ):

                    execute_trade("Sell", price)

            else:

                print(
                    f"{side_active} attiva | "
                    f"PNL {pnl:.2f} USDT"
                )

        except Exception as e:

            print("Errore loop:", e)
            time.sleep(10)

# ------------------------------------------------

if __name__ == "__main__":
    run_strategy()
