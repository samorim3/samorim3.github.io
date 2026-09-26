"""
dgi_screener.py
Automated Multi-Threaded DGI Market Screener
Scans the Western Universe (~1,000 companies across S&P 500, STOXX 600, Euronext Lisboa)
Calculates institutional 5-Pillar DGI Scores (0 - 100) and exports 'dgi_market_ranking.json'
"""

import json
import time
import os
import sys
import io
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import yfinance as yf

# Ensure UTF-8 output encoding for Windows terminals
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def calculate_cagr(start_val, end_val, periods):
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

def analyze_company(item):
    ticker = item["ticker"]
    name = item.get("name", ticker)
    country = item.get("country", "")
    index_name = item.get("index", "")
    sector = item.get("sector", "")

    try:
        stock = yf.Ticker(ticker)
        info = stock.info or {}
    except Exception:
        return None

    # Price and Currency
    price = info.get("regularMarketPrice") or info.get("currentPrice") or info.get("previousClose")
    if not price or price <= 0:
        return None

    currency = info.get("currency", "USD")
    sym_char = "€" if currency == "EUR" else ("$" if currency == "USD" else ("£" if currency == "GBP" else f"{currency} "))

    # 1. Dividend Yield & Rate
    div_rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate")
    div_yield_raw = info.get("dividendYield") or info.get("trailingAnnualDividendYield") or 0.0

    if div_rate and price > 0:
        div_yield_pct = (float(div_rate) / float(price)) * 100.0
    elif div_yield_raw:
        div_yield_pct = (div_yield_raw * 100.0) if (div_yield_raw < 0.25) else float(div_yield_raw)
    else:
        div_yield_pct = 0.0

    # Skip pure non-dividend companies (< 0.1% yield) for DGI ranking
    if div_yield_pct < 0.1:
        return None

    if 2.5 <= div_yield_pct <= 6.0:
        p1 = 20.0
    elif (2.0 <= div_yield_pct < 2.5) or (6.0 < div_yield_pct <= 7.0):
        p1 = 16.0
    elif (1.5 <= div_yield_pct < 2.0) or (7.0 < div_yield_pct <= 8.5):
        p1 = 11.0
    elif (1.0 <= div_yield_pct < 1.5) or (8.5 < div_yield_pct <= 10.0):
        p1 = 6.0
    else:
        p1 = 2.0

    # 2. Payout Ratio on FCF & EPS
    fcf = info.get("freeCashflow")
    shares = info.get("sharesOutstanding")
    fcf_payout = None

    if fcf and fcf > 0 and shares and div_rate and div_rate > 0:
        fcf_payout = round(((shares * div_rate) / fcf) * 100.0, 1)

    eps_payout = round((info.get("payoutRatio") or 0.0) * 100.0, 1)
    eval_payout = fcf_payout if (fcf_payout is not None and fcf_payout > 0) else (eps_payout if eps_payout > 0 else None)

    if eval_payout is not None:
        if 0 < eval_payout <= 50.0:
            p2 = 20.0
        elif 50.0 < eval_payout <= 70.0:
            p2 = 16.0
        elif 70.0 < eval_payout <= 85.0:
            p2 = 10.0
        elif 85.0 < eval_payout <= 100.0:
            p2 = 5.0
        else:
            p2 = 0.0
    else:
        p2 = 10.0

    # 3. Solvency: Net Debt / EBITDA
    total_debt = info.get("totalDebt")
    total_cash = info.get("totalCash")
    ebitda = info.get("ebitda")
    net_debt = None
    if total_debt is not None and total_cash is not None:
        net_debt = total_debt - total_cash

    net_debt_ebitda = None
    if net_debt is not None and ebitda and ebitda > 0:
        net_debt_ebitda = round(net_debt / ebitda, 2)
    elif net_debt is not None and net_debt <= 0:
        net_debt_ebitda = 0.0

    if net_debt_ebitda is not None:
        if net_debt_ebitda <= 1.0:
            p3 = 20.0
        elif net_debt_ebitda <= 2.2:
            p3 = 16.0
        elif net_debt_ebitda <= 3.2:
            p3 = 10.0
        elif net_debt_ebitda <= 4.5:
            p3 = 5.0
        else:
            p3 = 0.0
    else:
        p3 = 12.0

    # 4. Quality: ROIC & Operating Margin
    roe = (info.get("returnOnEquity") or 0.0) * 100.0
    margin = (info.get("operatingMargins") or 0.0) * 100.0
    roic = roe * 0.7 if roe > 0 else 0.0  # Conservative institutional proxy

    roic_pts = 10.0 if roic >= 12.0 else (7.0 if roic >= 8.0 else (4.0 if roic >= 4.0 else 0.0))
    margin_pts = 10.0 if margin >= 15.0 else (7.0 if margin >= 10.0 else (4.0 if margin >= 5.0 else 0.0))
    p4 = roic_pts + margin_pts

    # 5. Dividend CAGR 5Y, Chowder & Shareholder Returns
    div_cagr_5y = None
    try:
        divs = stock.dividends
        if not divs.empty:
            div_series = divs.resample('YE').sum() if hasattr(divs, 'resample') else None
            if div_series is not None and len(div_series) >= 5:
                start_div = div_series.iloc[-5]
                end_div = div_series.iloc[-1]
                cagr = calculate_cagr(start_div, end_div, 4)
                if cagr is not None:
                    div_cagr_5y = round(cagr * 100.0, 1)
    except Exception:
        pass

    if div_cagr_5y is None:
        div_cagr_5y = 5.0  # conservative baseline

    chowder = round(div_yield_pct + div_cagr_5y, 1)

    if chowder >= 12.0 and div_cagr_5y >= 5.0:
        p5 = 20.0
    elif chowder >= 10.0 or div_cagr_5y >= 4.0:
        p5 = 16.0
    elif chowder >= 7.0 or div_cagr_5y >= 2.0:
        p5 = 11.0
    else:
        p5 = 5.0

    total_score = round(p1 + p2 + p3 + p4 + p5)

    if total_score >= 85:
        verdict = "Elite DGI (Dividend King)"
        badge_color = "#047857"
    elif total_score >= 70:
        verdict = "Solid DGI Quality"
        badge_color = "#15803d"
    elif total_score >= 55:
        verdict = "Moderate DGI"
        badge_color = "#d97706"
    else:
        verdict = "Weak / Caution"
        badge_color = "#dc2626"

    # Valuation Multiples
    pe = round(info.get("trailingPE"), 1) if info.get("trailingPE") else None
    fwd_pe = round(info.get("forwardPE"), 1) if info.get("forwardPE") else None

    # Market Cap Format
    mcap_val = info.get("marketCap")
    mcap_str = "—"
    if mcap_val:
        if mcap_val >= 1e12:
            mcap_str = f"{mcap_val/1e12:.2f}T"
        elif mcap_val >= 1e9:
            mcap_str = f"{mcap_val/1e9:.2f}B"
        elif mcap_val >= 1e6:
            mcap_str = f"{mcap_val/1e6:.2f}M"

    return {
        "ticker": ticker,
        "name": info.get("longName") or info.get("shortName") or name,
        "country": country,
        "index": index_name,
        "sector": info.get("sector") or sector or "General",
        "price": round(float(price), 2),
        "currency": currency,
        "symChar": sym_char,
        "mcap": mcap_str,
        "dgiScore": total_score,
        "verdict": verdict,
        "badgeColor": badge_color,
        "divYield": round(div_yield_pct, 2),
        "divRate": round(float(div_rate), 2) if div_rate else None,
        "fcfPayout": fcf_payout,
        "epsPayout": eps_payout,
        "netDebtEbitda": net_debt_ebitda,
        "roic": round(roic, 1),
        "margin": round(margin, 1),
        "divCagr5y": div_cagr_5y,
        "chowder": chowder,
        "pe": pe,
        "forwardPe": fwd_pe,
        "pillars": {
            "p1": p1,
            "p2": p2,
            "p3": p3,
            "p4": p4,
            "p5": p5
        }
    }

def run_screener(limit=None):
    if not os.path.exists("dgi_universe.json"):
        import generate_dgi_universe
        generate_dgi_universe.get_universe()

    with open("dgi_universe.json", "r", encoding="utf-8") as f:
        universe = json.load(f)

    if limit:
        universe = universe[:limit]

    print(f"🚀 Starting DGI Multi-Threaded Screener across {len(universe)} companies...")
    t0 = time.time()
    results = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_item = {executor.submit(analyze_company, item): item for item in universe}
        count = 0
        for future in as_completed(future_to_item):
            count += 1
            if count % 25 == 0 or count == len(universe):
                elapsed = time.time() - t0
                print(f"[{count}/{len(universe)}] Processed in {elapsed:.1f}s (Matches: {len(results)})")
            try:
                res = future.result()
                if res and res["dgiScore"] > 0:
                    results.append(res)
            except Exception as e:
                pass

    # Sort descending by DGI Score, then by Chowder Rule
    results.sort(key=lambda x: (x["dgiScore"], x["chowder"]), reverse=True)

    output_data = {
        "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "totalScanned": len(universe),
        "totalRanked": len(results),
        "eliteCount": len([r for r in results if r["dgiScore"] >= 85]),
        "solidCount": len([r for r in results if 70 <= r["dgiScore"] < 85]),
        "rankings": results
    }

    with open("dgi_market_ranking.json", "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Completed DGI Screener in {time.time()-t0:.1f}s!")
    print(f"Total Ranked Dividend Companies: {len(results)}")
    print(f"🏆 Elite DGI (Score >= 85): {output_data['eliteCount']}")
    print(f"🟢 Solid DGI (Score 70-84): {output_data['solidCount']}")
    if results:
        print("\nTop 5 DGI Leaders:")
        for r in results[:5]:
            print(f" - {r['name']} ({r['ticker']}): Score {r['dgiScore']}/100 | Yield {r['divYield']}% | Chowder {r['chowder']}%")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Western DGI Screener")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of tickers to test")
    args = parser.parse_args()
    run_screener(limit=args.limit)
