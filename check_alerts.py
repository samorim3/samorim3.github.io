import os
import json
import requests
import yfinance as yf

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def send_telegram_alert(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Error: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID environment variables are not set.")
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg,
        "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        res_data = response.json()
        if res_data.get("ok"):
            print(f"✅ Alert sent to Telegram: {msg[:40]}...")
            return True
        else:
            print(f"❌ Telegram API Error: {res_data.get('description')}")
            return False
    except Exception as e:
        print(f"❌ Exception sending Telegram alert: {e}")
        return False

def main():
    watchlist_path = os.path.join(os.path.dirname(__file__), "watchlist.json")
    if not os.path.exists(watchlist_path):
        print("❌ Error: watchlist.json file not found.")
        return

    with open(watchlist_path, "r", encoding="utf-8") as f:
        watchlist = json.load(f)

    print(f"🔍 Starting stock price check for {len(watchlist)} watchlist items...")

    for item in watchlist:
        ticker_symbol = item.get("ticker")
        name = item.get("name", ticker_symbol)
        min_buy = float(item.get("minBuy", 0))
        max_sell = float(item.get("maxSell", 0))

        try:
            ticker = yf.Ticker(ticker_symbol)
            fast_info = getattr(ticker, 'fast_info', None)
            price = None
            if fast_info and 'lastPrice' in fast_info:
                price = fast_info['lastPrice']
            if not price:
                info = ticker.info
                price = info.get('currentPrice') or info.get('regularMarketPrice')

            if not price:
                print(f"⚠️ Could not fetch price for {ticker_symbol}")
                continue

            print(f"📊 {ticker_symbol} ({name}): Current Price = {price:.2f} | Target Buy = {min_buy:.2f} | Target Sell = {max_sell:.2f}")

            if min_buy > 0 and price <= min_buy:
                send_telegram_alert(f"🟢 *ALERTA DE COMPRA:* {ticker_symbol} ({name})\nCotação Atual: *{price:.2f}*\nMínimo Target (Compra): {min_buy:.2f}")
            elif max_sell > 0 and price >= max_sell:
                send_telegram_alert(f"🔴 *ALERTA DE VENDA:* {ticker_symbol} ({name})\nCotação Atual: *{price:.2f}*\nMáximo Target (Venda): {max_sell:.2f}")

        except Exception as e:
            print(f"❌ Error checking {ticker_symbol}: {e}")

if __name__ == "__main__":
    main()
