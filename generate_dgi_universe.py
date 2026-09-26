"""
generate_dgi_universe.py
Compiles the complete Western DGI universe of ~1,000 unique companies across:
- S&P 500 (US Large Caps)
- STOXX Europe 600 (European Large/Mid Caps across 17 countries)
- Euronext Lisboa / PSI (Portugal)
- Spanish IBEX additions
"""

import json
import io
import urllib.request
import pandas as pd

PORTUGAL_PSI = [
    {"ticker": "GALP.LS", "name": "Galp Energia", "country": "Portugal", "index": "PSI Lisboa", "sector": "Energy"},
    {"ticker": "EDP.LS", "name": "EDP - Energias de Portugal", "country": "Portugal", "index": "PSI Lisboa", "sector": "Utilities"},
    {"ticker": "EDPR.LS", "name": "EDP Renováveis", "country": "Portugal", "index": "PSI Lisboa", "sector": "Utilities"},
    {"ticker": "JMT.LS", "name": "Jerónimo Martins", "country": "Portugal", "index": "PSI Lisboa", "sector": "Consumer Defensive"},
    {"ticker": "NVG.LS", "name": "The Navigator Company", "country": "Portugal", "index": "PSI Lisboa", "sector": "Basic Materials"},
    {"ticker": "NOS.LS", "name": "NOS SGPS", "country": "Portugal", "index": "PSI Lisboa", "sector": "Communication"},
    {"ticker": "RENE.LS", "name": "REN - Redes Energéticas Nacionais", "country": "Portugal", "index": "PSI Lisboa", "sector": "Utilities"},
    {"ticker": "COR.LS", "name": "Corticeira Amorim", "country": "Portugal", "index": "PSI Lisboa", "sector": "Consumer Cyclical"},
    {"ticker": "SEM.LS", "name": "Semapa", "country": "Portugal", "index": "PSI Lisboa", "sector": "Basic Materials"},
    {"ticker": "CTT.LS", "name": "CTT Correios de Portugal", "country": "Portugal", "index": "PSI Lisboa", "sector": "Industrials"},
    {"ticker": "SON.LS", "name": "Sonae SGPS", "country": "Portugal", "index": "PSI Lisboa", "sector": "Consumer Defensive"},
    {"ticker": "BCP.LS", "name": "Banco Comercial Português (BCP)", "country": "Portugal", "index": "PSI Lisboa", "sector": "Financials"},
    {"ticker": "EGL.LS", "name": "Mota-Engil", "country": "Portugal", "index": "PSI Lisboa", "sector": "Industrials"},
    {"ticker": "ALTR.LS", "name": "Altri SGPS", "country": "Portugal", "index": "PSI Lisboa", "sector": "Basic Materials"},
    {"ticker": "IBS.LS", "name": "Ibersol", "country": "Portugal", "index": "PSI Lisboa", "sector": "Consumer Cyclical"},
    {"ticker": "GVOLT.LS", "name": "Greenvolt", "country": "Portugal", "index": "PSI Lisboa", "sector": "Utilities"}
]

SPAIN_EXTRA = [
    {"ticker": "ENG.MC", "name": "Enagás", "country": "Spain", "index": "IBEX 35", "sector": "Utilities"},
    {"ticker": "ELE.MC", "name": "Endesa", "country": "Spain", "index": "IBEX 35", "sector": "Utilities"},
    {"ticker": "MAP.MC", "name": "Mapfre", "country": "Spain", "index": "IBEX 35", "sector": "Financials"},
    {"ticker": "ACS.MC", "name": "ACS Group", "country": "Spain", "index": "IBEX 35", "sector": "Industrials"},
    {"ticker": "COL.MC", "name": "Colonial", "country": "Spain", "index": "IBEX 35", "sector": "Real Estate"}
]

COUNTRY_MAP = {
    'Switzerland': '.SW', 'United Kingdom': '.L', 'Netherlands': '.AS',
    'France': '.PA', 'Germany': '.DE', 'Spain': '.MC', 'Italy': '.MI',
    'Sweden': '.ST', 'Denmark': '.CO', 'Finland': '.HE', 'Norway': '.OL',
    'Portugal': '.LS', 'Belgium': '.BR', 'Austria': '.VI', 'Ireland': '.IR'
}

def get_universe():
    universe = {}

    # 1. Portugal PSI
    for p in PORTUGAL_PSI:
        universe[p["ticker"]] = p

    # 2. Spain Extra
    for s in SPAIN_EXTRA:
        universe[s["ticker"]] = s

    # 3. S&P 500
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=15).read().decode('utf-8')
        sp_df = pd.read_html(io.StringIO(html))[0]
        for _, row in sp_df.iterrows():
            sym = str(row['Symbol']).strip().replace('.', '-')
            name = str(row['Security']).strip()
            sector = str(row.get('GICS Sector', 'General')).strip()
            if sym and sym not in universe:
                universe[sym] = {
                    "ticker": sym,
                    "name": name,
                    "country": "USA",
                    "index": "S&P 500",
                    "sector": sector
                }
        print(f"Loaded S&P 500 companies")
    except Exception as e:
        print(f"Error loading S&P 500: {e}")

    # 4. STOXX Europe 600
    try:
        url = "https://en.wikipedia.org/wiki/STOXX_Europe_600"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=15).read().decode('utf-8')
        stoxx_df = pd.read_html(io.StringIO(html))[3]
        for _, row in stoxx_df.iterrows():
            raw_ticker = str(row['Ticker']).strip()
            name = str(row['Company']).strip()
            country = str(row['Country']).strip()
            sector = str(row.get('ICB Sector', 'General')).strip()
            suffix = COUNTRY_MAP.get(country, '')
            if raw_ticker and suffix:
                full_ticker = raw_ticker + suffix
                if full_ticker not in universe:
                    universe[full_ticker] = {
                        "ticker": full_ticker,
                        "name": name,
                        "country": country,
                        "index": "STOXX Europe 600",
                        "sector": sector
                    }
        print(f"Loaded STOXX Europe 600 companies")
    except Exception as e:
        print(f"Error loading STOXX 600: {e}")

    items = list(universe.values())
    print(f"Total Unique Universe: {len(items)} companies")
    with open("dgi_universe.json", "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    return items

if __name__ == "__main__":
    get_universe()
