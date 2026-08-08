import os
import json
import socket
import requests
import yfinance as yf

# Set global socket timeout (15s) to prevent requests hanging indefinitely
socket.setdefaulttimeout(15)

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

def get_current_price(ticker_symbol):
    """
    Attempts multiple fast and resilient methods to fetch current price via yfinance.
    Handles exchange suffixes (e.g. GALP -> GALP.LS for Euronext Lisbon).
    """
    aliases = {
        "GALP": "GALP.LS",
        "NOVO-B": "NOVO-B.CO",
        "ADS": "ADS.DE",
        "PETR4": "PETR4.SA"
    }
    symbol_to_try = aliases.get(ticker_symbol.upper(), ticker_symbol)

    symbols_list = [symbol_to_try]
    if "." not in symbol_to_try:
        symbols_list.append(f"{symbol_to_try}.LS")
        symbols_list.append(f"{symbol_to_try}.SA")

    for sym in symbols_list:
        try:
            ticker = yf.Ticker(sym)

            # Method 1: Try fast_info attributes (fastest and lightest)
            try:
                fast_info = getattr(ticker, 'fast_info', None)
                if fast_info:
                    if hasattr(fast_info, 'last_price') and fast_info.last_price is not None:
                        return float(fast_info.last_price)
                    if hasattr(fast_info, 'lastPrice') and fast_info.lastPrice is not None:
                        return float(fast_info.lastPrice)
                    if isinstance(fast_info, dict):
                        price = fast_info.get('lastPrice') or fast_info.get('last_price')
                        if price:
                            return float(price)
            except Exception:
                pass

            # Method 2: Try 1-day history (fast & reliable Yahoo historical endpoint)
            try:
                df = ticker.history(period="1d", timeout=10)
                if not df.empty and 'Close' in df.columns:
                    return float(df['Close'].iloc[-1])
            except Exception:
                pass

            # Method 3: Fallback to ticker.info
            try:
                info = ticker.info
                if info:
                    price = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('previousClose')
                    if price:
                        return float(price)
            except Exception:
                pass

        except Exception as e:
            print(f"  ❌ Exception fetching ticker {sym}: {e}")

    return None

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

        price = get_current_price(ticker_symbol)

        if price is None:
            print(f"⚠️ Could not fetch price for {ticker_symbol}")
            continue

        print(f"📊 {ticker_symbol} ({name}): Current Price = {price:.2f} | Target Buy = {min_buy:.2f} | Target Sell = {max_sell:.2f}")

        if min_buy > 0 and price <= min_buy:
            send_telegram_alert(f"🟢 *BUY ALERT:* {ticker_symbol} ({name})\nCurrent Price: *{price:.2f}*\nTarget Buy Price: {min_buy:.2f}")
        elif max_sell > 0 and price >= max_sell:
            send_telegram_alert(f"🔴 *SELL ALERT:* {ticker_symbol} ({name})\nCurrent Price: *{price:.2f}*\nTarget Sell Price: {max_sell:.2f}")

if __name__ == "__main__":
    main()

