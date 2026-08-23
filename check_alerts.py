import os
import json
import socket
import re
import sys
import io
import requests
from bs4 import BeautifulSoup
import yfinance as yf

# Ensure UTF-8 output encoding for Windows terminals
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Set global socket timeout to prevent requests from hanging
socket.setdefaulttimeout(15)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HTTP_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9'
}

HTTP_COOKIES = {
    'SOCS': 'CAESEwgDEgk0ODE3Nzk3MjAaAmVuIAEaBgiA_LyaBg'
}

# Mapping common tickers to Google Finance search format
GF_TICKER_MAP = {
    "ADS.DE": "ADS:ETR",
    "ADS": "ADS:ETR",
    "NKE": "NKE:NYSE",
    "GALP": "GALP:ELI",
    "GALP.LS": "GALP:ELI",
    "NOVO-B": "NOVO-B:CO",
    "PETR4": "PETR4:BVMF",
    "AAPL": "AAPL:NASDAQ",
    "MSFT": "MSFT:NASDAQ",
    "TSLA": "TSLA:NASDAQ"
}

def send_telegram_alert(msg):
    """Sends alert message to configured Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Warning] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID environment variables not set.")
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
            print(f"[Telegram] Alert sent: {msg[:50]}...")
            return True
        else:
            print(f"[Telegram API Error] {res_data.get('description')}")
            return False
    except Exception as e:
        print(f"[Exception] Error sending Telegram alert: {e}")
        return False

def get_google_finance_price(ticker_symbol):
    """
    Fetches real-time stock price directly from Google Finance.
    Handles exchange prefixes/suffixes and parses live quote HTML.
    """
    ticker_clean = ticker_symbol.strip().upper()
    gf_symbol = GF_TICKER_MAP.get(ticker_clean)
    
    symbols_to_try = []
    if gf_symbol:
        symbols_to_try.append(gf_symbol)
    else:
        if ":" in ticker_clean:
            symbols_to_try.append(ticker_clean)
        else:
            symbols_to_try.extend([
                f"{ticker_clean}:NYSE",
                f"{ticker_clean}:NASDAQ",
                f"{ticker_clean}:ETR",
                f"{ticker_clean}:ELI"
            ])

    for sym in symbols_to_try:
        url = f"https://www.google.com/finance/quote/{sym}?hl=en"
        try:
            res = requests.get(url, headers=HTTP_HEADERS, cookies=HTTP_COOKIES, timeout=10)
            if res.status_code != 200:
                continue

            soup = BeautifulSoup(res.text, 'html.parser')
            
            # Primary Google Finance quote container: <div class="N6SYTe">
            n6_div = soup.find('div', class_='N6SYTe')
            if n6_div:
                text = n6_div.get_text(strip=True)
                clean_num = re.sub(r'[^\d.]', '', text.replace(',', ''))
                if clean_num:
                    val = float(clean_num)
                    print(f"  [Google Finance] {ticker_symbol} ({sym}) -> {val} ({text})")
                    return val

            # Fallback class search: YMlKec or fxfa-c
            for el in soup.find_all(class_=re.compile(r'YMlKec|fxfa-c')):
                text = el.get_text(strip=True)
                if any(c in text for c in ['$', '€', '£']) or re.search(r'\d', text):
                    clean_num = re.sub(r'[^\d.]', '', text.replace(',', ''))
                    if clean_num:
                        try:
                            val = float(clean_num)
                            print(f"  [Google Finance Fallback] {ticker_symbol} ({sym}) -> {val} ({text})")
                            return val
                        except ValueError:
                            pass
        except Exception as e:
            print(f"  [Error] Scraping Google Finance for {sym}: {e}")

    return None

def get_yfinance_fallback(ticker_symbol):
    """Fallback price lookup using yfinance if Google Finance is unavailable."""
    aliases = {
        "GALP": "GALP.LS",
        "NOVO-B": "NOVO-B.CO",
        "ADS": "ADS.DE"
    }
    symbol_to_try = aliases.get(ticker_symbol.upper(), ticker_symbol)

    try:
        ticker = yf.Ticker(symbol_to_try)
        fast_info = getattr(ticker, 'fast_info', None)
        if fast_info:
            price = getattr(fast_info, 'last_price', None) or getattr(fast_info, 'lastPrice', None)
            if price:
                print(f"  [yfinance] {ticker_symbol} -> {float(price):.2f}")
                return float(price)

        df = ticker.history(period="1d", timeout=10)
        if not df.empty and 'Close' in df.columns:
            val = float(df['Close'].iloc[-1])
            print(f"  [yfinance History] {ticker_symbol} -> {val:.2f}")
            return val
    except Exception as e:
        print(f"  [yfinance Error] {ticker_symbol}: {e}")

    return None

def get_current_price(ticker_symbol):
    """Gets stock price using Google Finance primary, yfinance secondary."""
    price = get_google_finance_price(ticker_symbol)
    if price is not None:
        return price
    print(f"[Warning] Google Finance lookup failed for {ticker_symbol}. Trying yfinance fallback...")
    return get_yfinance_fallback(ticker_symbol)

def calculate_dcf(fcf, growth_5y=0.10, growth_10y=0.075, terminal_growth=0.03, discount_rate=0.09, shares_out=1, cash=0, debt=0):
    """Modelo DCF a 20 anos com valor terminal de Perpetuidade de Gordon (Adam Khoo)"""
    if fcf <= 0 or shares_out <= 0:
        return 0.0
    
    pv_future_cash = 0.0
    current_cash = fcf
    
    # Anos 1 a 5
    for year in range(1, 6):
        current_cash *= (1 + growth_5y)
        pv_future_cash += current_cash / ((1 + discount_rate) ** year)
        
    # Anos 6 a 10
    for year in range(6, 11):
        current_cash *= (1 + growth_10y)
        pv_future_cash += current_cash / ((1 + discount_rate) ** year)
        
    # Anos 11 a 20
    for year in range(11, 21):
        current_cash *= (1 + terminal_growth)
        pv_future_cash += current_cash / ((1 + discount_rate) ** year)

    # Perpetuidade de Gordon no ano 20
    if discount_rate > terminal_growth:
        terminal_val_20 = (current_cash * (1 + terminal_growth)) / (discount_rate - terminal_growth)
        pv_terminal = terminal_val_20 / ((1 + discount_rate) ** 20)
        pv_future_cash += pv_terminal
        
    equity_value = pv_future_cash + cash - debt
    fair_value = equity_value / shares_out
    return max(0.0, fair_value)

def main():
    watchlist_path = os.path.join(os.path.dirname(__file__), "watchlist.json")
    if not os.path.exists(watchlist_path):
        print("[Error] watchlist.json file not found.")
        return

    with open(watchlist_path, "r", encoding="utf-8") as f:
        watchlist = json.load(f)

    print(f"Starting Google Finance stock price check for {len(watchlist)} favorite companies...")

    alerts_triggered = 0

    for item in watchlist:
        ticker_symbol = item.get("ticker")
        name = item.get("name", ticker_symbol)
        min_buy = float(item.get("minBuy", 0))
        max_sell = float(item.get("maxSell", 0))

        price = get_current_price(ticker_symbol)

        if price is None:
            print(f"[Error] Could not fetch price for {ticker_symbol}")
            continue

        item["lastPrice"] = price

        print(f"Stock: {ticker_symbol} ({name}) | Current = {price:.2f} | Target Buy = {min_buy:.2f} | Target Sell = {max_sell:.2f}")

        if min_buy > 0 and price <= min_buy:
            send_telegram_alert(f"🟢 *STOCK BUY ALERT:* {ticker_symbol} ({name})\nCurrent Price: *{price:.2f}*\nTarget Buy Threshold: {min_buy:.2f}")
            alerts_triggered += 1
        elif max_sell > 0 and price >= max_sell:
            send_telegram_alert(f"🔴 *STOCK SELL ALERT:* {ticker_symbol} ({name})\nCurrent Price: *{price:.2f}*\nTarget Sell Threshold: {max_sell:.2f}")
            alerts_triggered += 1

    # Save updated watchlist with last fetched live prices
    with open(watchlist_path, "w", encoding="utf-8") as f:
        json.dump(watchlist, f, indent=2)

    print(f"Price check completed. Triggered {alerts_triggered} alert(s). Saved updated watchlist.json.")

if __name__ == "__main__":
    main()
