import os
import json
import socket
import re
import sys
import io
import argparse
import requests
from bs4 import BeautifulSoup
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime

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

GF_TICKER_MAP = {
    "ADS.DE": "ADS:ETR",
    "ADS": "ADS:ETR",
    "NKE": "NKE:NYSE",
    "GALP": "GALP:ELI",
    "GALP.LS": "GALP:ELI",
    "AD.AS": "AD:AMS",
    "AD": "AD:AMS",
    "AHOLD": "AD:AMS",
    "AHOLD DELHAIZE": "AD:AMS",
    "NOVO-B": "NOVO-B:CO",
    "PETR4": "PETR4:BVMF",
    "AAPL": "AAPL:NASDAQ",
    "MSFT": "MSFT:NASDAQ",
    "TSLA": "TSLA:NASDAQ"
}

def send_telegram_alert(msg):
    """Sends alert message to configured Telegram chat."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)

    if not token or not chat_id:
        print("[Warning] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID environment variables not set.")
        return False
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": msg,
        "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        res_data = response.json()
        if res_data.get("ok"):
            print(f"[Telegram] Alert sent successfully: {msg[:40]}...")
            return True
        else:
            print(f"[Telegram API Error] {res_data.get('description')}")
            return False
    except Exception as e:
        print(f"[Exception] Error sending Telegram alert: {e}")
        return False


def calculate_cagr(start_val, end_val, periods):
    """Calculates Compound Annual Growth Rate (CAGR)."""
    if start_val is None or end_val is None:
        return None
    try:
        start_val = float(start_val)
        end_val = float(end_val)
        periods = float(periods)
        if start_val <= 0 or end_val <= 0 or periods <= 0:
            return None
        return ((end_val / start_val) ** (1.0 / periods)) - 1.0
    except Exception:
        return None


def calculate_dgi_data(ticker_sym):
    """
    Calculates DGI Score (0 to 100) across 5 core pillars:
    1. Dividend Yield (2% - 6%)
    2. Payout Ratio on FCF (<70%)
    3. Net Debt / EBITDA (<3.0x)
    4. 5Y Revenue & Net Income CAGR
    5. 5Y Dividend CAGR & Chowder Rule
    """
    aliases = {
        "GALP": "GALP.LS",
        "NOVO-B": "NOVO-B.CO",
        "ADS": "ADS.DE",
        "AHOLD": "AD.AS",
        "AHOLD DELHAIZE": "AD.AS",
        "AD": "AD.AS"
    }
    symbol_to_try = aliases.get(ticker_sym.upper(), ticker_sym)

    try:
        stock = yf.Ticker(symbol_to_try)
        info = stock.info or {}
    except Exception as e:
        print(f"Error fetching yfinance for {ticker_sym}: {e}")
        return None

    # Current price & details
    current_price = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('previousClose')
    currency = info.get('currency', 'USD')
    sym_char = "€" if currency == "EUR" else ("$" if currency == "USD" else f"{currency} ")

    # 1. Dividend Rate & Yield
    div_rate = info.get('dividendRate') or info.get('trailingAnnualDividendRate')
    div_yield_raw = info.get('dividendYield') or info.get('trailingAnnualDividendYield') or 0.0

    if div_rate and current_price and current_price > 0:
        div_yield_pct = (float(div_rate) / float(current_price)) * 100.0
    elif div_yield_raw:
        div_yield_pct = (div_yield_raw * 100.0) if (div_yield_raw < 0.25) else float(div_yield_raw)
    else:
        div_yield_pct = 0.0

    if (div_rate is None or div_rate <= 0) and div_yield_pct > 0 and current_price and current_price > 0:
        div_rate = round((div_yield_pct / 100.0) * current_price, 2)

    if 2.5 <= div_yield_pct <= 6.0:
        p1_score = 20.0
    elif (2.0 <= div_yield_pct < 2.5) or (6.0 < div_yield_pct <= 7.0):
        p1_score = 16.0
    elif (1.5 <= div_yield_pct < 2.0) or (7.0 < div_yield_pct <= 8.5):
        p1_score = 10.0
    elif (1.0 <= div_yield_pct < 1.5) or (8.5 < div_yield_pct <= 10.0):
        p1_score = 5.0
    else:
        p1_score = 0.0

    # 2. Payout on FCF
    cash_flow = stock.cashflow
    fcf_payout = None
    eps_payout = (info.get('payoutRatio') or 0.0) * 100.0

    if not cash_flow.empty:
        try:
            op_cf_key = next((k for k in ['Operating Cash Flow', 'Total Cash From Operating Activities', 'OperatingCashFlow'] if k in cash_flow.index), None)
            capex_key = next((k for k in ['Capital Expenditure', 'Capital Expenditures', 'CapitalExpenditure'] if k in cash_flow.index), None)
            div_paid_key = next((k for k in ['Common Stock Dividend Paid', 'Cash Dividends Paid', 'Payment Of Dividends & Other Cash Distributions'] if k in cash_flow.index), None)

            if op_cf_key and capex_key and div_paid_key:
                op_cf = cash_flow.loc[op_cf_key].iloc[0]
                capex = abs(cash_flow.loc[capex_key].iloc[0])
                fcf = op_cf - capex
                div_paid = abs(cash_flow.loc[div_paid_key].iloc[0])
                if fcf > 0:
                    fcf_payout = (div_paid / fcf) * 100.0
        except Exception:
            pass

    eval_payout = fcf_payout if fcf_payout is not None else (eps_payout if eps_payout > 0 else None)
    payout_lbl = "FCF" if fcf_payout is not None else "EPS"

    if eval_payout is not None:
        if 0 < eval_payout <= 50.0:
            p2_score = 20.0
        elif 50.0 < eval_payout <= 70.0:
            p2_score = 16.0
        elif 70.0 < eval_payout <= 85.0:
            p2_score = 10.0
        elif 85.0 < eval_payout <= 100.0:
            p2_score = 5.0
        else:
            p2_score = 0.0
    else:
        p2_score = 0.0

    # 3. Leverage (Net Debt / EBITDA)
    ebitda = info.get('ebitda')
    total_debt = info.get('totalDebt')
    total_cash = info.get('totalCash') or info.get('cashAndCashEquivalents')
    net_debt = info.get('netDebt')
    if net_debt is None and total_debt is not None and total_cash is not None:
        net_debt = total_debt - total_cash

    net_debt_ebitda = (net_debt / ebitda) if (net_debt is not None and ebitda and ebitda > 0) else None

    if net_debt_ebitda is not None:
        if net_debt <= 0 or net_debt_ebitda <= 1.5:
            p3_score = 20.0
        elif 1.5 < net_debt_ebitda <= 2.5:
            p3_score = 16.0
        elif 2.5 < net_debt_ebitda <= 3.2:
            p3_score = 10.0
        elif 3.2 < net_debt_ebitda <= 4.5:
            p3_score = 5.0
        else:
            p3_score = 0.0
    else:
        p3_score = 10.0

    # 4. Revenue & Net Income CAGR
    income_stmt = stock.income_stmt
    rev_cagr = None
    ni_cagr = None
    if not income_stmt.empty:
        try:
            rev_row = next((r for r in ['Total Revenue', 'Operating Revenue', 'Revenue'] if r in income_stmt.index), None)
            ni_row = next((r for r in ['Net Income', 'Net Income Common Stockholders'] if r in income_stmt.index), None)
            if rev_row:
                rev_s = income_stmt.loc[rev_row].dropna()
                if len(rev_s) >= 2:
                    rev_cagr = calculate_cagr(rev_s.iloc[-1], rev_s.iloc[0], len(rev_s) - 1)
            if ni_row:
                ni_s = income_stmt.loc[ni_row].dropna()
                if len(ni_s) >= 2:
                    ni_cagr = calculate_cagr(ni_s.iloc[-1], ni_s.iloc[0], len(ni_s) - 1)
        except Exception:
            pass

    rev_pts = 10.0 if (rev_cagr and rev_cagr >= 0.06) else (7.0 if (rev_cagr and rev_cagr >= 0.03) else (4.0 if (rev_cagr and rev_cagr >= 0) else 0.0))
    ni_pts = 10.0 if (ni_cagr and ni_cagr >= 0.07) else (7.0 if (ni_cagr and ni_cagr >= 0.03) else (4.0 if (ni_cagr and ni_cagr >= 0) else 0.0))
    p4_score = rev_pts + ni_pts

    # 5. Dividend CAGR & Chowder Rule
    div_history = stock.dividends
    dps_cagr_5y = None
    if not div_history.empty:
        try:
            ann_div = div_history.groupby(div_history.index.year).sum()
            ann_div = ann_div[ann_div > 0]
            curr_yr = datetime.now().year
            ann_div_comp = ann_div[ann_div.index < curr_yr]
            if len(ann_div_comp) >= 6:
                dps_cagr_5y = calculate_cagr(ann_div_comp.iloc[-6], ann_div_comp.iloc[-1], 5)
            elif len(ann_div_comp) >= 2:
                dps_cagr_5y = calculate_cagr(ann_div_comp.iloc[0], ann_div_comp.iloc[-1], len(ann_div_comp) - 1)
        except Exception:
            pass

    chowder = (div_yield_pct + (dps_cagr_5y * 100.0)) if (dps_cagr_5y is not None and div_yield_pct > 0) else (div_yield_pct if div_yield_pct > 0 else None)

    dps_pts = 10.0 if (dps_cagr_5y and dps_cagr_5y >= 0.07) else (7.0 if (dps_cagr_5y and dps_cagr_5y >= 0.04) else (4.0 if (dps_cagr_5y and dps_cagr_5y >= 0.01) else 0.0))
    chw_pts = 10.0 if (chowder and chowder >= 12.0) else (7.0 if (chowder and chowder >= 8.0) else (4.0 if (chowder and chowder >= 5.0) else 0.0))
    p5_score = dps_pts + chw_pts

    # Ex-Dividend Date
    ex_div_timestamp = info.get('exDividendDate')
    ex_div_date_str = None

    try:
        cal = getattr(stock, 'calendar', None)
        if isinstance(cal, dict) and 'Ex-Dividend Date' in cal:
            ex_val = cal['Ex-Dividend Date']
            if ex_val:
                ex_div_date_str = str(ex_val)
        elif isinstance(cal, pd.DataFrame) and 'Ex-Dividend Date' in cal.index:
            ex_div_date_str = str(cal.loc['Ex-Dividend Date'].iloc[0])
    except Exception:
        pass

    if not ex_div_date_str and ex_div_timestamp:
        try:
            if isinstance(ex_div_timestamp, (int, float)):
                ex_div_date_str = datetime.fromtimestamp(ex_div_timestamp).strftime('%Y-%m-%d')
            else:
                ex_div_date_str = str(ex_div_timestamp)
        except Exception:
            pass

    total_score = round(p1_score + p2_score + p3_score + p4_score + p5_score, 1)

    if total_score >= 85.0:
        verdict = "Top Tier DGI (Aristocrat)"
        icon = "⭐"
        summary = "High quality defensive profile: robust balance sheet, safe payout, and consistent dividend growth."
    elif total_score >= 70.0:
        verdict = "Solid DGI Quality"
        icon = "🟢"
        summary = "Solid fundamentals with sustainable dividend distributions."
    elif total_score >= 50.0:
        verdict = "Moderate / Neutral"
        icon = "🟡"
        summary = "Meets core requirements; monitor specific metrics."
    elif total_score >= 35.0:
        verdict = "Weak / High Risk"
        icon = "🟠"
        summary = "Subdued dividend growth or weaker solvency metrics."
    else:
        verdict = "Avoid / High Risk"
        icon = "🔴"
        summary = "High leverage or dividend uncovered by free cash flow."

    return {
        'ticker': ticker_sym.upper(),
        'name': info.get('shortName') or info.get('longName') or ticker_sym,
        'currency_symbol': sym_char,
        'price': current_price,
        'dividend_rate': round(float(div_rate), 2) if div_rate is not None else None,
        'dividend_yield_pct': div_yield_pct,
        'ex_dividend_date': ex_div_date_str,
        'eval_payout': eval_payout,
        'payout_lbl': payout_lbl,
        'net_debt_ebitda': net_debt_ebitda,
        'revenue_cagr_5y': rev_cagr,
        'net_income_cagr_5y': ni_cagr,
        'dps_cagr_5y': dps_cagr_5y,
        'chowder_number': chowder,
        'p1_score': p1_score,
        'p2_score': p2_score,
        'p3_score': p3_score,
        'p4_score': p4_score,
        'p5_score': p5_score,
        'total_score': total_score,
        'verdict': verdict,
        'verdict_icon': icon,
        'verdict_summary': summary
    }


def format_telegram_dgi_card(dgi):
    """Formats an executive DGI Scorecard for Telegram."""
    if not dgi:
        return "❌ Could not calculate DGI fundamental data."

    sym = dgi.get('currency_symbol', '$')
    price_str = f"{sym}{dgi['price']:.2f}" if dgi.get('price') else "N/A"
    div_rate_val = dgi.get('dividend_rate')
    div_rate_str = f"{sym}{div_rate_val:.2f}" if div_rate_val is not None else "N/A"
    ex_date_str = dgi.get('ex_dividend_date') or "N/A"

    rev_s = f"{dgi['revenue_cagr_5y']*100:.1f}%" if dgi['revenue_cagr_5y'] is not None else "N/A"
    ni_s = f"{dgi['net_income_cagr_5y']*100:.1f}%" if dgi['net_income_cagr_5y'] is not None else "N/A"
    dps_s = f"{dgi['dps_cagr_5y']*100:.1f}%" if dgi['dps_cagr_5y'] is not None else "N/A"
    chw_s = f"{dgi['chowder_number']:.1f}%" if dgi['chowder_number'] is not None else "N/A"
    payout_s = f"{dgi['eval_payout']:.1f}%" if dgi['eval_payout'] is not None else "N/A"
    debt_s = f"{dgi['net_debt_ebitda']:.2f}x" if dgi['net_debt_ebitda'] is not None else "Net Cash"

    msg = (
        f"📊 *DGI FUNDAMENTAL ANALYSIS: {dgi['ticker']}*\n"
        f"🏢 *{dgi['name']}* | Price: `{price_str}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 *DGI SCORE:* *{dgi['total_score']:.0f}/100* {dgi['verdict_icon']}\n"
        f"📌 *Rating:* {dgi['verdict']}\n\n"
        f"🎯 *Core Pillars & Dividends:*\n"
        f"• 1. *Yield & Dividendo:* `{dgi['dividend_yield_pct']:.2f}%` ({div_rate_str} / ação)\n"
        f"   └ 📅 *Data Ex-Dividendo:* `{ex_date_str}` ({dgi['p1_score']:.0f}/20 pts)\n"
        f"• 2. *Payout ({dgi['payout_lbl']}):* `{payout_s}` ({dgi['p2_score']:.0f}/20 pts)\n"
        f"• 3. *Debt/EBITDA:* `{debt_s}` ({dgi['p3_score']:.0f}/20 pts)\n"
        f"• 4. *5Y Growth:* Rev `{rev_s}` | Net Inc `{ni_s}` ({dgi['p4_score']:.0f}/20 pts)\n"
        f"• 5. *Div Growth & Chowder:* Div `{dps_s}` | Chowder `{chw_s}` ({dgi['p5_score']:.0f}/20 pts)\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 _{dgi['verdict_summary']}_"
    )
    return msg


def get_google_finance_price(ticker_symbol):
    """Fetches real-time stock price directly from Google Finance."""
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
            n6_div = soup.find('div', class_='N6SYTe')
            if n6_div:
                text = n6_div.get_text(strip=True)
                clean_num = re.sub(r'[^\d.]', '', text.replace(',', ''))
                if clean_num:
                    val = float(clean_num)
                    print(f"  [Google Finance] {ticker_symbol} ({sym}) -> {val} ({text})")
                    return val

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
    """Fallback price lookup using yfinance."""
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
    price = get_google_finance_price(ticker_symbol)
    if price is not None:
        return price
    print(f"[Warning] Google Finance lookup failed for {ticker_symbol}. Trying yfinance fallback...")
    return get_yfinance_fallback(ticker_symbol)


def main():
    parser = argparse.ArgumentParser(description="Stock Price Alerts & DGI Fundamental Analyzer for Telegram")
    parser.add_argument("--dgi", type=str, help="Calculate and send DGI Scorecard for a specific ticker (ex: JNJ, PEP, GALP.LS)")
    parser.add_argument("--dgi-all", action="store_true", help="Calculate and send DGI Scorecards for all watchlist tickers")
    args = parser.parse_args()

    # Mode 1: Send DGI report for a single ticker
    if args.dgi:
        sym = args.dgi.strip().upper()
        print(f"Calculating DGI Score for {sym}...")
        dgi_data = calculate_dgi_data(sym)
        if dgi_data:
            msg = format_telegram_dgi_card(dgi_data)
            print(f"\n--- Telegram Message Preview ---\n{msg}\n------------------------------")
            send_telegram_alert(msg)
        else:
            print(f"Could not calculate DGI for {sym}")
        return

    # Mode 2: Send DGI report for all watchlist items
    watchlist_path = os.path.join(os.path.dirname(__file__), "watchlist.json")
    if not os.path.exists(watchlist_path):
        print("[Error] watchlist.json file not found.")
        return

    with open(watchlist_path, "r", encoding="utf-8") as f:
        watchlist = json.load(f)

    if args.dgi_all:
        print(f"Calculating DGI Scorecards for all {len(watchlist)} watchlist tickers...")
        for item in watchlist:
            t = item.get("ticker")
            dgi_data = calculate_dgi_data(t)
            if dgi_data:
                msg = format_telegram_dgi_card(dgi_data)
                send_telegram_alert(msg)
                item["dgiScore"] = dgi_data["total_score"]
                item["dgiVerdict"] = dgi_data["verdict"]
                if dgi_data.get("dividend_rate") is not None:
                    item["dividendRate"] = dgi_data["dividend_rate"]
                if dgi_data.get("dividend_yield_pct") is not None:
                    item["dividendYield"] = round(dgi_data["dividend_yield_pct"], 2)
                if dgi_data.get("ex_dividend_date"):
                    item["exDividendDate"] = dgi_data["ex_dividend_date"]
        with open(watchlist_path, "w", encoding="utf-8") as f:
            json.dump(watchlist, f, indent=2)
        print("Completed sending DGI Scorecards.")
        return

    # Default Mode: Price monitoring with enriched DGI alerts
    print(f"Starting stock price check & DGI monitoring for {len(watchlist)} favorite companies...")
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

        # Calculate DGI score and dividend data for watchlist enrichments
        dgi_data = calculate_dgi_data(ticker_symbol)
        dgi_info_str = ""
        if dgi_data:
            item["dgiScore"] = dgi_data["total_score"]
            item["dgiVerdict"] = dgi_data["verdict"]
            if dgi_data.get("dividend_rate") is not None:
                item["dividendRate"] = dgi_data["dividend_rate"]
            if dgi_data.get("dividend_yield_pct") is not None:
                item["dividendYield"] = round(dgi_data["dividend_yield_pct"], 2)
            if dgi_data.get("ex_dividend_date"):
                item["exDividendDate"] = dgi_data["ex_dividend_date"]

            div_sym = dgi_data.get('currency_symbol', '$')
            div_val = dgi_data.get('dividend_rate')
            div_str = f"{div_sym}{div_val:.2f}" if div_val is not None else "N/A"
            ex_str = dgi_data.get('ex_dividend_date') or "N/D"

            dgi_info_str = (
                f"\n🏆 *DGI Score:* `{dgi_data['total_score']:.0f}/100` {dgi_data['verdict_icon']}\n"
                f"• 💰 *Dividendo:* `{div_str} / ação` (`{dgi_data['dividend_yield_pct']:.2f}%`) | *Ex-Div:* `{ex_str}`\n"
                f"• Payout ({dgi_data['payout_lbl']}): `{dgi_data['eval_payout'] or 0:.1f}%` | Chowder: `{dgi_data['chowder_number'] or 0:.1f}%`"
            )

        print(f"Stock: {ticker_symbol} ({name}) | Current = {price:.2f} | Buy Target = {min_buy:.2f} | Sell Target = {max_sell:.2f} | DGI Score = {item.get('dgiScore', 'N/D')} | Div = {item.get('dividendRate', 'N/D')}")

        if min_buy > 0 and price <= min_buy:
            alert_msg = (
                f"🟢 *STOCK BUY ALERT:* {ticker_symbol} ({name})\n"
                f"Current Price: *{price:.2f}* (Target Buy: {min_buy:.2f})"
                f"{dgi_info_str}"
            )
            send_telegram_alert(alert_msg)
            alerts_triggered += 1
        elif max_sell > 0 and price >= max_sell:
            alert_msg = (
                f"🔴 *STOCK SELL ALERT:* {ticker_symbol} ({name})\n"
                f"Current Price: *{price:.2f}* (Target Sell: {max_sell:.2f})"
                f"{dgi_info_str}"
            )
            send_telegram_alert(alert_msg)
            alerts_triggered += 1

    # Save updated watchlist with last fetched live prices and DGI scores
    with open(watchlist_path, "w", encoding="utf-8") as f:
        json.dump(watchlist, f, indent=2)

    # Save updated quotes.json for web frontend fallback cache
    quotes_path = os.path.join(os.path.dirname(__file__), "quotes.json")
    existing_quotes = {}
    if os.path.exists(quotes_path):
        try:
            with open(quotes_path, "r", encoding="utf-8") as f:
                qdata = json.load(f)
                existing_quotes = qdata.get("quotes", {})
        except Exception:
            existing_quotes = {}

    for item in watchlist:
        t = item.get("ticker")
        lp = item.get("lastPrice")
        if t and lp:
            existing_quotes[t] = round(float(lp), 2)
            if t == "GALP.LS": existing_quotes["GALP"] = round(float(lp), 2)
            if t == "ADS.DE": existing_quotes["ADS"] = round(float(lp), 2)
            if t == "PETR4.SA": existing_quotes["PETR4"] = round(float(lp), 2)

    quotes_payload = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "quotes": existing_quotes
    }
    with open(quotes_path, "w", encoding="utf-8") as f:
        json.dump(quotes_payload, f, indent=2)

    print(f"Price check completed. Triggered {alerts_triggered} alert(s). Saved updated watchlist.json and quotes.json.")

if __name__ == "__main__":
    main()
