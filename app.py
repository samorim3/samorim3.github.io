import os
import requests
import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime

# ==========================================
# 1. CONFIGURAÇÃO DA PÁGINA & ESTILIZAÇÃO
# ==========================================
st.set_page_config(
    page_title="DGI Screener & Telegram Analyzer",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS para estética visual moderna e refinada
st.markdown('''
<style>
    .metric-card {
        background: linear-gradient(135deg, rgba(255, 255, 255, 0.05) 0%, rgba(255, 255, 255, 0.02) 100%);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 12px;
        padding: 16px 18px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
        transition: transform 0.2s ease, border-color 0.2s ease;
        margin-bottom: 12px;
    }
    .metric-card:hover {
        transform: translateY(-2px);
        border-color: rgba(66, 153, 225, 0.6);
    }
    .score-badge-excellent {
        background-color: #2e7d32;
        color: #ffffff;
        padding: 6px 16px;
        border-radius: 20px;
        font-weight: 700;
        font-size: 1.05rem;
        display: inline-block;
    }
    .score-badge-strong {
        background-color: #388e3c;
        color: #ffffff;
        padding: 6px 16px;
        border-radius: 20px;
        font-weight: 700;
        font-size: 1.05rem;
        display: inline-block;
    }
    .score-badge-moderate {
        background-color: #f57f17;
        color: #ffffff;
        padding: 6px 16px;
        border-radius: 20px;
        font-weight: 700;
        font-size: 1.05rem;
        display: inline-block;
    }
    .score-badge-weak {
        background-color: #d84315;
        color: #ffffff;
        padding: 6px 16px;
        border-radius: 20px;
        font-weight: 700;
        font-size: 1.05rem;
        display: inline-block;
    }
    .score-badge-avoid {
        background-color: #c62828;
        color: #ffffff;
        padding: 6px 16px;
        border-radius: 20px;
        font-weight: 700;
        font-size: 1.05rem;
        display: inline-block;
    }
</style>
''', unsafe_allow_html=True)


# ==========================================
# 2. FUNÇÕES DE CÁLCULO E INTEGRAÇÃO TELEGRAM
# ==========================================
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


def format_currency(val, symbol="$"):
    if val is None or pd.isna(val):
        return "N/D"
    abs_val = abs(val)
    if abs_val >= 1e12:
        return f"{symbol}{val/1e12:.2f}T"
    elif abs_val >= 1e9:
        return f"{symbol}{val/1e9:.2f}B"
    elif abs_val >= 1e6:
        return f"{symbol}{val/1e6:.2f}M"
    elif abs_val >= 1e3:
        return f"{symbol}{val/1e3:.2f}K"
    else:
        return f"{symbol}{val:.2f}"


def send_telegram_dgi_alert(data, token=None, chat_id=None):
    bot_token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    target_chat = chat_id or os.environ.get("TELEGRAM_CHAT_ID")

    if not bot_token or not target_chat:
        return False, "TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não definidos (configure na barra lateral ou variáveis de ambiente)."

    s = data['scores']
    sym = data.get('currency_symbol', '$')
    price_str = f"{sym}{data['price']:.2f}" if data.get('price') else "N/D"

    rev_s = f"{data['revenue_cagr_5y']*100:.1f}%" if data['revenue_cagr_5y'] is not None else "N/D"
    ni_s = f"{data['net_income_cagr_5y']*100:.1f}%" if data['net_income_cagr_5y'] is not None else "N/D"
    dps_s = f"{data['dps_cagr_5y']*100:.1f}%" if data['dps_cagr_5y'] is not None else "N/D"
    chw_s = f"{data['chowder_number']:.1f}%" if data['chowder_number'] is not None else "N/D"
    payout_s = f"{s['p2_payout']['val']:.1f}%" if s['p2_payout']['val'] is not None else "N/D"
    debt_s = f"{data['net_debt_ebitda']:.2f}x" if data['net_debt_ebitda'] is not None else "Caixa Líq."

    msg = (
        f"📊 *ANÁLISE FUNDAMENTAL DGI: {data['ticker']}*\n"
        f"🏢 *{data['name']}* | Cotação: `{price_str}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 *SCORE DGI:* *{s['total']:.0f}/100* {data['verdict_icon']}\n"
        f"📌 *Classificação:* {data['verdict']}\n\n"
        f"🎯 *Os 5 Pilares DGI:*\n"
        f"• 1. *Yield:* `{data['dividend_yield_pct']:.2f}%` ({s['p1_yield']['score']:.0f}/20 pts)\n"
        f"• 2. *Payout ({s['p2_payout']['metric']}):* `{payout_s}` ({s['p2_payout']['score']:.0f}/20 pts)\n"
        f"• 3. *Dívida/EBITDA:* `{debt_s}` ({s['p3_leverage']['score']:.0f}/20 pts)\n"
        f"• 4. *Cresc. Operac. (5y):* Rev `{rev_s}` | Lucro `{ni_s}` ({s['p4_growth']['score']:.0f}/20 pts)\n"
        f"• 5. *Cresc. Div. & Chowder:* Div `{dps_s}` | Chowder `{chw_s}` ({s['p5_chowder']['score']:.0f}/20 pts)\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 _{data['verdict_summary']}_"
    )

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": target_chat, "text": msg, "parse_mode": "Markdown"}

    try:
        res = requests.post(url, json=payload, timeout=10)
        res_data = res.json()
        if res_data.get("ok"):
            return True, "Relatório DGI enviado com sucesso para o Telegram!"
        else:
            return False, f"Erro na API do Telegram: {res_data.get('description')}"
    except Exception as e:
        return False, f"Falha na comunicação: {str(e)}"


@st.cache_data(ttl=3600, show_spinner=False)
def analyze_company(ticker_sym):
    aliases = {"GALP": "GALP.LS", "NOVO-B": "NOVO-B.CO", "ADS": "ADS.DE"}
    symbol_clean = aliases.get(ticker_sym.upper(), ticker_sym.upper())

    try:
        stock = yf.Ticker(symbol_clean)
        info = stock.info or {}
    except Exception as e:
        return None, f"Erro ao aceder ao Yahoo Finance para '{ticker_sym}': {str(e)}"
    
    current_price = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('previousClose')
    if not current_price and not info.get('shortName'):
        return None, f"Não foi possível encontrar dados para '{ticker_sym}'. Verifique o ticker (ex: JNJ, PEP, BN.PA, GALP.LS)."

    currency = info.get('currency', 'USD')
    currency_symbol = "€" if currency == "EUR" else ("$" if currency == "USD" else f"{currency} ")

    # 1. PILAR 1: DIVIDEND YIELD
    div_yield_raw = info.get('dividendYield') or info.get('trailingAnnualDividendYield') or 0.0
    div_yield_pct = (div_yield_raw * 100.0) if (div_yield_raw and div_yield_raw < 1.0) else float(div_yield_raw or 0.0)

    if 2.5 <= div_yield_pct <= 6.0:
        p1_score = 20.0
        p1_status = "Ideal (2.5% - 6.0%)"
        p1_desc = "Yield equilibrado, sustentável e sem sinais de yield trap."
    elif (2.0 <= div_yield_pct < 2.5) or (6.0 < div_yield_pct <= 7.0):
        p1_score = 16.0
        p1_status = "Bom (2.0%-2.5% ou 6.0%-7.0%)"
        p1_desc = "Yield sólido e competitivo no universo DGI."
    elif (1.5 <= div_yield_pct < 2.0) or (7.0 < div_yield_pct <= 8.5):
        p1_score = 10.0
        p1_status = "Moderado / Cautela"
        p1_desc = "Yield moderado ou elevado (requer atenção à sustentabilidade)."
    elif (1.0 <= div_yield_pct < 1.5) or (8.5 < div_yield_pct <= 10.0):
        p1_score = 5.0
        p1_status = "Atenção / Risco"
        p1_desc = "Yield muito baixo ou excessivamente alto."
    else:
        p1_score = 0.0
        p1_status = "Insuficiente ou Risco (>10% ou 0%)"
        p1_desc = "Sem dividendo regular ou yield de risco."

    # 2. PILAR 2: SUSTENTABILIDADE (PAYOUT SOBRE FCF)
    cash_flow = stock.cashflow
    income_stmt = stock.income_stmt
    balance_sheet = stock.balance_sheet

    fcf_payout = None
    fcf_val = None
    div_paid_val = None
    eps_payout = (info.get('payoutRatio') or 0.0) * 100.0

    if not cash_flow.empty:
        try:
            op_cf_key = next((k for k in ['Operating Cash Flow', 'Total Cash From Operating Activities', 'OperatingCashFlow'] if k in cash_flow.index), None)
            capex_key = next((k for k in ['Capital Expenditure', 'Capital Expenditures', 'CapitalExpenditure'] if k in cash_flow.index), None)
            div_paid_key = next((k for k in ['Common Stock Dividend Paid', 'Cash Dividends Paid', 'Payment Of Dividends & Other Cash Distributions'] if k in cash_flow.index), None)

            if op_cf_key and capex_key:
                op_cf = cash_flow.loc[op_cf_key].iloc[0]
                capex = abs(cash_flow.loc[capex_key].iloc[0])
                fcf_val = op_cf - capex

            if div_paid_key:
                div_paid_val = abs(cash_flow.loc[div_paid_key].iloc[0])

            if fcf_val is not None and div_paid_val is not None and fcf_val > 0:
                fcf_payout = (div_paid_val / fcf_val) * 100.0
        except Exception:
            fcf_payout = None

    payout_metric_used = "FCF" if fcf_payout is not None else "EPS"
    eval_payout = fcf_payout if fcf_payout is not None else (eps_payout if eps_payout > 0 else None)

    if eval_payout is not None:
        if 0 < eval_payout <= 50.0:
            p2_score = 20.0
            p2_status = f"Excelente ({eval_payout:.1f}% s/ {payout_metric_used})"
            p2_desc = f"Ampla margem de segurança s/ {payout_metric_used}."
        elif 50.0 < eval_payout <= 70.0:
            p2_score = 16.0
            p2_status = f"Saudável ({eval_payout:.1f}% s/ {payout_metric_used})"
            p2_desc = "Nível de payout sustentável e perfeitamente equilibrado."
        elif 70.0 < eval_payout <= 85.0:
            p2_score = 10.0
            p2_status = f"Elevado ({eval_payout:.1f}% s/ {payout_metric_used})"
            p2_desc = "Payout esticado. Deixa pouca folga de reinvestimento."
        elif 85.0 < eval_payout <= 100.0:
            p2_score = 5.0
            p2_status = f"Crítico ({eval_payout:.1f}% s/ {payout_metric_used})"
            p2_desc = "Próximo de 100% da geração de caixa consumida."
        else:
            p2_score = 0.0
            p2_status = f"Insustentável ({eval_payout:.1f}% s/ {payout_metric_used})"
            p2_desc = "Dividendo superior à geração de caixa livre."
    else:
        p2_score = 0.0
        p2_status = "N/D"
        p2_desc = "Dados insuficientes para cálculo de payout."

    # 3. PILAR 3: SAÚDE FINANCEIRA & ALAVANCAGEM
    ebitda = info.get('ebitda')
    total_debt = info.get('totalDebt')
    total_cash = info.get('totalCash') or info.get('cashAndCashEquivalents')
    net_debt = info.get('netDebt')

    if net_debt is None and total_debt is not None and total_cash is not None:
        net_debt = total_debt - total_cash

    if (net_debt is None or ebitda is None) and not balance_sheet.empty and not income_stmt.empty:
        try:
            if total_debt is None:
                debt_row = next((r for r in ['Total Debt', 'TotalDebt'] if r in balance_sheet.index), None)
                if debt_row: total_debt = balance_sheet.loc[debt_row].iloc[0]
            if total_cash is None:
                cash_row = next((r for r in ['Cash And Cash Equivalents', 'CashCashEquivalentsAndShortTermInvestments'] if r in balance_sheet.index), None)
                if cash_row: total_cash = balance_sheet.loc[cash_row].iloc[0]
            if net_debt is None and total_debt is not None and total_cash is not None:
                net_debt = total_debt - total_cash
            if ebitda is None:
                ebitda_row = next((r for r in ['EBITDA', 'Normalized EBITDA'] if r in income_stmt.index), None)
                if ebitda_row: ebitda = income_stmt.loc[ebitda_row].iloc[0]
        except Exception:
            pass

    net_debt_ebitda = (net_debt / ebitda) if (net_debt is not None and ebitda and ebitda > 0) else None

    if net_debt_ebitda is not None:
        if net_debt <= 0 or net_debt_ebitda <= 1.5:
            p3_score = 20.0
            p3_status = f"Balanço Fortaleza ({net_debt_ebitda:.2f}x)" if net_debt_ebitda > 0 else "Caixa Líquido Positivo"
            p3_desc = "Excelente solidez financeira ou balanço com caixa líquido."
        elif 1.5 < net_debt_ebitda <= 2.5:
            p3_score = 16.0
            p3_status = f"Alavancagem Saudável ({net_debt_ebitda:.2f}x)"
            p3_desc = "Nível de endividamento confortável e perfeitamente gerível."
        elif 2.5 < net_debt_ebitda <= 3.2:
            p3_score = 10.0
            p3_status = f"Alavancagem Moderada ({net_debt_ebitda:.2f}x)"
            p3_desc = "Aceitável para setores de fluxo estável."
        elif 3.2 < net_debt_ebitda <= 4.5:
            p3_score = 5.0
            p3_status = f"Alavancagem Esticada ({net_debt_ebitda:.2f}x)"
            p3_desc = "Endividamento elevado."
        else:
            p3_score = 0.0
            p3_status = f"Risco Alto ({net_debt_ebitda:.2f}x)"
            p3_desc = "Alavancagem excessiva (>4.5x)."
    else:
        p3_score = 10.0
        p3_status = "N/D (Ex: Setor Financeiro)"
        p3_desc = "Métrica Net Debt/EBITDA não aplicável diretamente."

    # 4. PILAR 4: CRESCIMENTO OPERACIONAL A 5 ANOS
    rev_cagr_5y = None
    ni_cagr_5y = None
    historical_financials = []

    if not income_stmt.empty:
        try:
            rev_row = next((r for r in ['Total Revenue', 'Operating Revenue', 'Revenue'] if r in income_stmt.index), None)
            ni_row = next((r for r in ['Net Income', 'Net Income Common Stockholders'] if r in income_stmt.index), None)
            cols = sorted(income_stmt.columns)
            if len(cols) >= 2:
                for c in cols:
                    dt_str = c.strftime('%Y') if hasattr(c, 'strftime') else str(c)[:4]
                    r_v = income_stmt.loc[rev_row, c] if rev_row else None
                    n_v = income_stmt.loc[ni_row, c] if ni_row else None
                    historical_financials.append({
                        'Year': dt_str,
                        'Revenue': float(r_v) if r_v is not None and not pd.isna(r_v) else None,
                        'NetIncome': float(n_v) if n_v is not None and not pd.isna(n_v) else None
                    })
                if rev_row:
                    rev_s = income_stmt.loc[rev_row].dropna()
                    if len(rev_s) >= 2: rev_cagr_5y = calculate_cagr(rev_s.iloc[-1], rev_s.iloc[0], len(rev_s) - 1)
                if ni_row:
                    ni_s = income_stmt.loc[ni_row].dropna()
                    if len(ni_s) >= 2: ni_cagr_5y = calculate_cagr(ni_s.iloc[-1], ni_s.iloc[0], len(ni_s) - 1)
        except Exception:
            pass

    rev_pts = 10.0 if (rev_cagr_5y and rev_cagr_5y >= 0.06) else (7.0 if (rev_cagr_5y and rev_cagr_5y >= 0.03) else (4.0 if (rev_cagr_5y and rev_cagr_5y >= 0) else 0.0))
    ni_pts = 10.0 if (ni_cagr_5y and ni_cagr_5y >= 0.07) else (7.0 if (ni_cagr_5y and ni_cagr_5y >= 0.03) else (4.0 if (ni_cagr_5y and ni_cagr_5y >= 0) else 0.0))
    p4_score = rev_pts + ni_pts
    rev_display = f"{rev_cagr_5y*100:.1f}%" if rev_cagr_5y is not None else "N/D"
    ni_display = f"{ni_cagr_5y*100:.1f}%" if ni_cagr_5y is not None else "N/D"
    p4_status = f"Receita: {rev_display} | Lucro: {ni_display}"
    p4_desc = "Crescimento anual composto recente de receitas e lucros da empresa."

    # 5. PILAR 5: CRESCIMENTO DO DIVIDENDO & REGRA DE CHOWDER
    div_history = stock.dividends
    dps_cagr_5y = None
    dps_cagr_3y = None
    annual_divs = pd.Series(dtype=float)

    if not div_history.empty:
        try:
            annual_divs = div_history.groupby(div_history.index.year).sum()
            annual_divs = annual_divs[annual_divs > 0]
            curr_yr = datetime.now().year
            ann_div_comp = annual_divs[annual_divs.index < curr_yr]
            if len(ann_div_comp) >= 6:
                dps_cagr_5y = calculate_cagr(ann_div_comp.iloc[-6], ann_div_comp.iloc[-1], 5)
            elif len(ann_div_comp) >= 2:
                dps_cagr_5y = calculate_cagr(ann_div_comp.iloc[0], ann_div_comp.iloc[-1], len(ann_div_comp) - 1)
            if len(ann_div_comp) >= 4:
                dps_cagr_3y = calculate_cagr(ann_div_comp.iloc[-4], ann_div_comp.iloc[-1], 3)
        except Exception:
            pass

    chowder = (div_yield_pct + (dps_cagr_5y * 100.0)) if (dps_cagr_5y is not None and div_yield_pct > 0) else (div_yield_pct if div_yield_pct > 0 else None)
    dps_pts = 10.0 if (dps_cagr_5y and dps_cagr_5y >= 0.07) else (7.0 if (dps_cagr_5y and dps_cagr_5y >= 0.04) else (4.0 if (dps_cagr_5y and dps_cagr_5y >= 0.01) else (5.0 if div_yield_pct > 4.0 else 0.0)))
    chw_pts = 10.0 if (chowder and chowder >= 12.0) else (7.0 if (chowder and chowder >= 8.0) else (4.0 if (chowder and chowder >= 5.0) else 0.0))
    p5_score = dps_pts + chw_pts

    dps_display = f"{dps_cagr_5y*100:.1f}%" if dps_cagr_5y is not None else "N/D"
    chowder_display = f"{chowder:.1f}%" if chowder is not None else "N/D"
    p5_status = f"CAGR Div: {dps_display} | Chowder: {chowder_display}"
    p5_desc = "Crescimento de proventos a 5 anos e pontuação combinada da Regra de Chowder."

    # SCORE FINAL
    total_score = round(p1_score + p2_score + p3_score + p4_score + p5_score, 1)

    if total_score >= 85.0:
        verdict = "Excelente (Perfil Dividend Aristocrat / Alta Qualidade)"
        verdict_badge = "score-badge-excellent"
        verdict_icon = "⭐"
        verdict_summary = "Empresa de altíssima qualidade DGI: balanço robusto, yield equilibrado, payout saudável e crescimento consistente do dividendo."
    elif total_score >= 70.0:
        verdict = "Forte (Boa Oportunidade DGI)"
        verdict_badge = "score-badge-strong"
        verdict_icon = "🟢"
        verdict_summary = "Fundamentos sólidos com dividendos seguros e histórico consistente de crescimento operacional."
    elif total_score >= 50.0:
        verdict = "Moderado / Neutro"
        verdict_badge = "score-badge-moderate"
        verdict_icon = "🟡"
        verdict_summary = "Cumpre requisitos essenciais de DGI, mas apresenta pontos de atenção em alavancagem, cobertura ou ritmo de crescimento."
    elif total_score >= 35.0:
        verdict = "Fraco / Risco Elevado"
        verdict_badge = "score-badge-weak"
        verdict_icon = "🟠"
        verdict_summary = "Métricas fracas ou deterioração em pilares chave. Risco de estagnação ou cortes futuros em caso de pressão operacional."
    else:
        verdict = "Evitar / Yield Trap Potencial"
        verdict_badge = "score-badge-avoid"
        verdict_icon = "🔴"
        verdict_summary = "Elevado risco financeiro, fluxo de caixa insuficiente para cobrir proventos ou ausência de crescimento de dividendos."

    data = {
        'ticker': ticker_sym.upper(),
        'name': info.get('shortName') or info.get('longName') or ticker_sym,
        'sector': info.get('sector', 'N/D'),
        'industry': info.get('industry', 'N/D'),
        'country': info.get('country', 'N/D'),
        'currency': currency,
        'currency_symbol': currency_symbol,
        'price': current_price,
        'market_cap': info.get('marketCap'),
        'trailing_pe': info.get('trailingPE'),
        'forward_pe': info.get('forwardPE'),
        'price_to_book': info.get('priceToBook'),
        'roe': info.get('returnOnEquity'),
        'operating_margin': info.get('operatingMargins'),
        'free_cashflow': fcf_val,
        'total_debt': total_debt,
        'total_cash': total_cash,
        'net_debt': net_debt,
        'ebitda': ebitda,
        'net_debt_ebitda': net_debt_ebitda,
        'dividend_yield_pct': div_yield_pct,
        'fcf_payout_pct': fcf_payout,
        'eps_payout_pct': eps_payout,
        'dps_cagr_5y': dps_cagr_5y,
        'dps_cagr_3y': dps_cagr_3y,
        'chowder_number': chowder,
        'revenue_cagr_5y': rev_cagr_5y,
        'net_income_cagr_5y': ni_cagr_5y,
        'annual_divs': annual_divs,
        'historical_financials': historical_financials,
        'scores': {
            'total': total_score,
            'p1_yield': {'score': p1_score, 'max': 20, 'status': p1_status, 'desc': p1_desc, 'val': div_yield_pct},
            'p2_payout': {'score': p2_score, 'max': 20, 'status': p2_status, 'desc': p2_desc, 'val': eval_payout, 'metric': payout_metric_used},
            'p3_leverage': {'score': p3_score, 'max': 20, 'status': p3_status, 'desc': p3_desc, 'val': net_debt_ebitda},
            'p4_growth': {'score': p4_score, 'max': 20, 'status': p4_status, 'desc': p4_desc, 'rev_cagr': rev_cagr_5y, 'ni_cagr': ni_cagr_5y},
            'p5_chowder': {'score': p5_score, 'max': 20, 'status': p5_score, 'desc': p5_desc, 'dps_cagr': dps_cagr_5y, 'chowder': chowder},
        },
        'verdict': verdict,
        'verdict_badge': verdict_badge,
        'verdict_icon': verdict_icon,
        'verdict_summary': verdict_summary
    }

    return data, None


# ==========================================
# 3. SIDEBAR / CONTROLES & TELEGRAM CONFIG
# ==========================================
st.sidebar.markdown("### 🔍 Configuração do Screener")

popular_presets = ["JNJ", "PEP", "PG", "KO", "ABBV", "MCD", "MSFT", "TXN", "GALP.LS", "BN.PA", "NESN.SW"]
selected_preset = st.sidebar.selectbox("Empresas Populares (Presets):", ["-- Selecionar Ticker --"] + popular_presets)

default_ticker = "" if selected_preset == "-- Selecionar Ticker --" else selected_preset
ticker_input = st.sidebar.text_input("Ticker da Empresa:", value=default_ticker, placeholder="Ex: AAPL, KO, GALP.LS").strip().upper()

run_analysis = st.sidebar.button("🚀 Analisar Empresa", use_container_width=True, type="primary")

st.sidebar.markdown("---")
with st.sidebar.expander("📲 Configuração do Bot Telegram", expanded=False):
    st.caption("Integração com o seu bot de alertas de cotações e DGI.")
    custom_tg_token = st.text_input("Bot Token (opcional):", value=os.environ.get("TELEGRAM_BOT_TOKEN", ""), type="password")
    custom_tg_chat = st.text_input("Chat ID (opcional):", value=os.environ.get("TELEGRAM_CHAT_ID", ""))

st.sidebar.markdown('''
**Os 5 Pilares DGI:**
* 🎯 **1. Dividend Yield**: 2% a 6% (evita yield traps)
* 🛡️ **2. Sustentabilidade**: Payout s/ FCF < 70%
* 🏦 **3. Solidez & Alavancagem**: Dívida Líq./EBITDA < 3.0x
* 📈 **4. Crescimento Operacional**: CAGR Receita e Lucro
* 👑 **5. Crescimento do Dividendo**: CAGR 5y & Chowder
''')

st.sidebar.caption("Integrado com o sistema de alertas do Telegram e Yahoo Finance.")

# ==========================================
# 4. CORPO PRINCIPAL DA APLICAÇÃO
# ==========================================
st.title("📈 Analisador Fundamental de Dividend Growth (DGI)")
st.caption("Sistema de pontuação (0 a 100) e alertas integrados com o Bot do Telegram.")

if ticker_input:
    with st.spinner(f"A recolher demonstrações financeiras de {ticker_input}..."):
        data, error = analyze_company(ticker_input)

    if error:
        st.error(error)
    elif data:
        sym = data['currency_symbol']
        price_str = f"{sym}{data['price']:.2f}" if data['price'] else "N/D"
        mcap_str = format_currency(data['market_cap'], sym)

        st.markdown(f'''
        <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.1); border-radius: 12px; padding: 18px; margin-bottom: 20px;">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px;">
                <div>
                    <h2 style="margin: 0; padding: 0; font-weight: 700; color: #ffffff;">{data['name']} <span style="color: #64B5F6; font-size: 1.2rem;">({data['ticker']})</span></h2>
                    <p style="margin: 4px 0 0 0; color: #a0aec0; font-size: 0.95rem;">
                        <strong>Setor:</strong> {data['sector']} &nbsp;|&nbsp; 
                        <strong>Indústria:</strong> {data['industry']} &nbsp;|&nbsp; 
                        <strong>Moeda:</strong> {data['currency']}
                    </p>
                </div>
                <div style="text-align: right;">
                    <div style="font-size: 1.8rem; font-weight: 800; color: #ffffff;">{price_str}</div>
                    <div style="color: #a0aec0; font-size: 0.9rem;">Market Cap: <strong>{mcap_str}</strong></div>
                </div>
            </div>
        </div>
        ''', unsafe_allow_html=True)

        # RESUMO EXECUTIVO DO SCORE DGI
        col_score_l, col_score_r = st.columns([1, 2])

        with col_score_l:
            total_score = data['scores']['total']
            badge_class = data['verdict_badge']

            st.markdown(f'''
            <div style="text-align: center; background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.1); border-radius: 14px; padding: 22px; margin-bottom: 12px;">
                <div style="font-size: 0.9rem; text-transform: uppercase; letter-spacing: 1px; color: #a0aec0; margin-bottom: 6px;">Pontuação Global DGI</div>
                <div style="font-size: 3.6rem; font-weight: 900; line-height: 1; color: #ffffff; margin-bottom: 10px;">
                    {total_score:.0f}<span style="font-size: 1.7rem; color: #718096;">/100</span>
                </div>
                <div class="{badge_class}">
                    {data['verdict_icon']} {data['verdict']}
                </div>
            </div>
            ''', unsafe_allow_html=True)

            if st.button("📲 Enviar Relatório para o Telegram", use_container_width=True):
                ok, tg_res = send_telegram_dgi_alert(data, custom_tg_token, custom_tg_chat)
                if ok:
                    st.success(tg_res)
                else:
                    st.warning(tg_res)

        with col_score_r:
            st.markdown(f"### 📋 Parecer Fundamentalista")
            st.info(f"{data['verdict_summary']}")

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Dividend Yield", f"{data['dividend_yield_pct']:.2f}%")
            
            payout_val = data['scores']['p2_payout']['val']
            payout_lbl = data['scores']['p2_payout']['metric']
            m2.metric(f"Payout ({payout_lbl})", f"{payout_val:.1f}%" if payout_val is not None else "N/D")
            
            leverage_val = data['net_debt_ebitda']
            m3.metric("Dívida Líq./EBITDA", f"{leverage_val:.2f}x" if leverage_val is not None else "N/D")
            
            chowder_val = data['chowder_number']
            m4.metric("Regra de Chowder", f"{chowder_val:.1f}%" if chowder_val is not None else "N/D")

        st.markdown("---")

        # DETALHAMENTO DOS 5 PILARES (CARDS)
        st.subheader("🎯 Avaliação dos 5 Pilares DGI")
        
        p_cols = st.columns(5)
        pillars_info = [
            ("1. Dividend Yield", data['scores']['p1_yield'], "🎯", f"{data['dividend_yield_pct']:.2f}%"),
            ("2. Sustentabilidade FCF", data['scores']['p2_payout'], "🛡️", f"{data['scores']['p2_payout']['val']:.1f}%" if data['scores']['p2_payout']['val'] is not None else "N/D"),
            ("3. Solidez / Balanço", data['scores']['p3_leverage'], "🏦", f"{data['net_debt_ebitda']:.2f}x" if data['net_debt_ebitda'] is not None else "Caixa Líq."),
            ("4. Cresc. Operacional", data['scores']['p4_growth'], "📈", f"Rev: {data['scores']['p4_growth']['rev_cagr']*100:.1f}%" if data['scores']['p4_growth']['rev_cagr'] else "N/D"),
            ("5. Div. CAGR & Chowder", data['scores']['p5_chowder'], "👑", f"Chowder: {data['chowder_number']:.1f}%" if data['chowder_number'] else "N/D")
        ]

        for i, (title, p_data, icon, main_metric) in enumerate(pillars_info):
            with p_cols[i]:
                st.markdown(f'''
                <div class="metric-card">
                    <div style="font-size: 1.1rem; margin-bottom: 4px;">{icon} <strong>{title}</strong></div>
                    <div style="font-size: 1.4rem; font-weight: 800; color: #4CAF50; margin-bottom: 2px;">{p_data['score']:.0f} <span style="font-size: 0.85rem; color: #a0aec0;">/ {p_data['max']} pts</span></div>
                    <div style="font-size: 1.05rem; font-weight: 600; color: #ffffff; margin-bottom: 6px;">{main_metric}</div>
                    <div style="font-size: 0.8rem; color: #cbd5e0; line-height: 1.3;">{p_data['desc']}</div>
                </div>
                ''', unsafe_allow_html=True)
                progress_val = min(max(p_data['score'] / p_data['max'], 0.0), 1.0)
                st.progress(progress_val)

        # ABAS INTERATIVAS
        st.markdown("<br>", unsafe_allow_html=True)
        tab_overview, tab_divs, tab_financials, tab_table = st.tabs([
            "📊 Radar & Scorecard",
            "💰 Histórico & Crescimento de Dividendos",
            "📑 Demonstrações & Fluxo de Caixa",
            "⚖️ Tabela Fundamentalista Completa"
        ])

        # TAB 1: RADAR & SCORECARD
        with tab_overview:
            col_radar, col_breakdown = st.columns([1, 1])

            with col_radar:
                st.markdown("#### 🕸️ Perfil Radar dos 5 Pilares")
                
                categories = [
                    '1. Dividend Yield',
                    '2. Sustentabilidade FCF',
                    '3. Solidez / Balanço',
                    '4. Cresc. Operacional',
                    '5. Div. CAGR & Chowder'
                ]
                scores_list = [
                    data['scores']['p1_yield']['score'],
                    data['scores']['p2_payout']['score'],
                    data['scores']['p3_leverage']['score'],
                    data['scores']['p4_growth']['score'],
                    data['scores']['p5_chowder']['score']
                ]
                
                fig_radar = go.Figure()
                fig_radar.add_trace(go.Scatterpolar(
                    r=scores_list + [scores_list[0]],
                    theta=categories + [categories[0]],
                    fill='toself',
                    fillcolor='rgba(66, 153, 225, 0.3)',
                    line=dict(color='#3182ce', width=2),
                    name=data['ticker']
                ))

                fig_radar.update_layout(
                    polar=dict(
                        radialaxis=dict(
                            visible=True,
                            range=[0, 20],
                            tickfont=dict(size=10, color="#a0aec0"),
                            gridcolor="rgba(255, 255, 255, 0.15)"
                        ),
                        angularaxis=dict(
                            tickfont=dict(size=11, color="#ffffff"),
                            gridcolor="rgba(255, 255, 255, 0.15)"
                        ),
                        bgcolor="rgba(0,0,0,0)"
                    ),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    showlegend=False,
                    margin=dict(l=40, r=40, t=30, b=30),
                    height=380
                )
                st.plotly_chart(fig_radar, use_container_width=True)

            with col_breakdown:
                st.markdown("#### 🔍 Diagnóstico Detalhado por Pilar")
                
                checklist = [
                    ("Dividend Yield Saudável (2% a 6%)", data['dividend_yield_pct'] >= 2.0 and data['dividend_yield_pct'] <= 6.0, f"{data['dividend_yield_pct']:.2f}%"),
                    ("Payout s/ FCF Seguro (< 70%)", (data['scores']['p2_payout']['val'] is not None and data['scores']['p2_payout']['val'] <= 70.0), f"{data['scores']['p2_payout']['val']:.1f}%" if data['scores']['p2_payout']['val'] is not None else "N/D"),
                    ("Alavancagem Controlada (Dívida Líq./EBITDA < 3.0x)", (data['net_debt_ebitda'] is None or data['net_debt_ebitda'] <= 3.0), f"{data['net_debt_ebitda']:.2f}x" if data['net_debt_ebitda'] is not None else "Caixa Líq."),
                    ("Crescimento de Receita 5 Anos Positivo", (data['revenue_cagr_5y'] is not None and data['revenue_cagr_5y'] > 0), f"{data['revenue_cagr_5y']*100:.1f}%" if data['revenue_cagr_5y'] else "N/D"),
                    ("Crescimento de Lucro Líquido 5 Anos Positivo", (data['net_income_cagr_5y'] is not None and data['net_income_cagr_5y'] > 0), f"{data['net_income_cagr_5y']*100:.1f}%" if data['net_income_cagr_5y'] else "N/D"),
                    ("CAGR Dividendos 5 Anos >= 5%", (data['dps_cagr_5y'] is not None and data['dps_cagr_5y'] >= 0.05), f"{data['dps_cagr_5y']*100:.1f}%" if data['dps_cagr_5y'] else "N/D"),
                    ("Regra de Chowder >= 10% (Yield + Div CAGR)", (data['chowder_number'] is not None and data['chowder_number'] >= 10.0), f"{data['chowder_number']:.1f}%" if data['chowder_number'] else "N/D")
                ]

                for label, passed, val_text in checklist:
                    icon_chk = "✅" if passed else "❌"
                    color_chk = "#4CAF50" if passed else "#F44336"
                    st.markdown(f'''
                    <div style="display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; background: rgba(255,255,255,0.02); border-radius: 6px; margin-bottom: 6px;">
                        <span>{icon_chk} {label}</span>
                        <span style="font-weight: 700; color: {color_chk};">{val_text}</span>
                    </div>
                    ''', unsafe_allow_html=True)

        # TAB 2: HISTÓRICO DE DIVIDENDOS & CHOWDER
        with tab_divs:
            st.markdown("#### 💰 Histórico de Dividendos por Ação & Regra de Chowder")
            
            annual_divs = data['annual_divs']
            if not annual_divs.empty:
                col_d1, col_d2 = st.columns([2, 1])

                with col_d1:
                    df_divs = annual_divs.reset_index()
                    df_divs.columns = ['Ano', 'Dividendo_Por_Acao']
                    df_divs['Ano'] = df_divs['Ano'].astype(str)

                    fig_div = px.bar(
                        df_divs,
                        x='Ano',
                        y='Dividendo_Por_Acao',
                        title=f"Evolução Anual de Proventos ({data['currency']})",
                        labels={'Dividendo_Por_Acao': f"Dividendo ({data['currency']})", 'Ano': 'Ano'},
                        color_discrete_sequence=['#4299E1']
                    )
                    fig_div.update_layout(
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        xaxis=dict(gridcolor="rgba(255,255,255,0.1)"),
                        yaxis=dict(gridcolor="rgba(255,255,255,0.1)")
                    )
                    st.plotly_chart(fig_div, use_container_width=True)

                with col_d2:
                    st.markdown("##### 📌 Métricas de Dividend Growth")
                    cagr_5y_str = f"{data['dps_cagr_5y']*100:.2f}%" if data['dps_cagr_5y'] is not None else "N/D"
                    cagr_3y_str = f"{data['dps_cagr_3y']*100:.2f}%" if data['dps_cagr_3y'] is not None else "N/D"
                    chowder_str = f"{data['chowder_number']:.2f}%" if data['chowder_number'] is not None else "N/D"

                    st.markdown(f'''
                    * **Dividend Yield Atual:** `{data['dividend_yield_pct']:.2f}%`
                    * **CAGR Dividendos (3 Anos):** `{cagr_3y_str}`
                    * **CAGR Dividendos (5 Anos):** `{cagr_5y_str}`
                    * **Número de Chowder:** `{chowder_str}`
                    ''')
                    
                    st.info('''
                    **Como interpretar a Regra de Chowder:**
                    * **Yield < 3%**: Requer Chowder >= 15% (alto crescimento).
                    * **Yield >= 3%**: Requer Chowder >= 12% (equilíbrio ótimo).
                    * **Utilities / REITs (Yield > 4.5%)**: Requer Chowder >= 8%.
                    ''')
            else:
                st.warning("Não foram encontrados registos históricos de dividendos para esta empresa.")

        # TAB 3: FLUXO DE CAIXA E DEMONSTRAÇÕES
        with tab_financials:
            st.markdown("#### 📑 Fluxo de Caixa Livre vs. Demonstrações")
            
            hist_fin = data['historical_financials']
            if hist_fin:
                df_fin = pd.DataFrame(hist_fin)
                
                col_f1, col_f2 = st.columns(2)
                with col_f1:
                    fig_rev = px.line(
                        df_fin,
                        x='Year',
                        y=['Revenue', 'NetIncome'],
                        title="Evolução de Receita e Lucro Líquido",
                        labels={'value': f"Montante ({data['currency']})", 'Year': 'Ano', 'variable': 'Métrica'},
                        markers=True,
                        color_discrete_map={'Revenue': '#63B3ED', 'NetIncome': '#68D391'}
                    )
                    fig_rev.update_layout(
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        xaxis=dict(gridcolor="rgba(255,255,255,0.1)"),
                        yaxis=dict(gridcolor="rgba(255,255,255,0.1)")
                    )
                    st.plotly_chart(fig_rev, use_container_width=True)

                with col_f2:
                    st.markdown("##### 🛡️ Cobertura e Geração de Caixa")
                    st.markdown(f'''
                    * **Fluxo de Caixa Livre (FCF):** `{format_currency(data['free_cashflow'], sym)}`
                    * **Dívida Total:** `{format_currency(data['total_debt'], sym)}`
                    * **Caixa & Equivalentes:** `{format_currency(data['total_cash'], sym)}`
                    * **Dívida Líquida:** `{format_currency(data['net_debt'], sym)}`
                    * **EBITDA:** `{format_currency(data['ebitda'], sym)}`
                    ''')
                    
                    if data['scores']['p2_payout']['val'] is not None:
                        val = data['scores']['p2_payout']['val']
                        if val <= 70:
                            st.success(f"✅ Dividendo altamente sustentável. O Payout sobre Free Cash Flow é de apenas {val:.1f}%.")
                        else:
                            st.warning(f"⚠️ Atenção: Payout de {val:.1f}% deixa pouca folga de caixa.")
            else:
                st.info("Demonstrações financeiras anuais detalhadas não disponíveis para este ticker.")

        # TAB 4: TABELA FUNDAMENTALISTA COMPLETA
        with tab_table:
            st.markdown("#### ⚖️ Resumo Fundamentalista e Múltiplos de Mercado")
            
            table_data = [
                {"Métrica": "Preço Atual", "Valor": price_str, "Categoria": "Mercado"},
                {"Métrica": "Capitalização de Mercado", "Valor": mcap_str, "Categoria": "Mercado"},
                {"Métrica": "P/E Trailing (Preço / Lucro)", "Valor": f"{data['trailing_pe']:.2f}x" if data['trailing_pe'] else "N/D", "Categoria": "Valuation"},
                {"Métrica": "P/E Forward", "Valor": f"{data['forward_pe']:.2f}x" if data['forward_pe'] else "N/D", "Categoria": "Valuation"},
                {"Métrica": "Price to Book (P/B)", "Valor": f"{data['price_to_book']:.2f}x" if data['price_to_book'] else "N/D", "Categoria": "Valuation"},
                {"Métrica": "Return on Equity (ROE)", "Valor": f"{data['roe']*100:.2f}%" if data['roe'] else "N/D", "Categoria": "Rentabilidade"},
                {"Métrica": "Margem Operacional", "Valor": f"{data['operating_margin']*100:.2f}%" if data['operating_margin'] else "N/D", "Categoria": "Rentabilidade"},
                {"Métrica": "Dividend Yield", "Valor": f"{data['dividend_yield_pct']:.2f}%", "Categoria": "Dividendos"},
                {"Métrica": "Payout Ratio (FCF)", "Valor": f"{data['fcf_payout_pct']:.2f}%" if data['fcf_payout_pct'] is not None else "N/D", "Categoria": "Dividendos"},
                {"Métrica": "Payout Ratio (EPS)", "Valor": f"{data['eps_payout_pct']:.2f}%" if data['eps_payout_pct'] is not None else "N/D", "Categoria": "Dividendos"},
                {"Métrica": "CAGR Dividendos 5 Anos", "Valor": f"{data['dps_cagr_5y']*100:.2f}%" if data['dps_cagr_5y'] is not None else "N/D", "Categoria": "Dividendos"},
                {"Métrica": "Número de Chowder", "Valor": f"{data['chowder_number']:.2f}%" if data['chowder_number'] is not None else "N/D", "Categoria": "Dividendos"},
                {"Métrica": "Dívida Líquida / EBITDA", "Valor": f"{data['net_debt_ebitda']:.2f}x" if data['net_debt_ebitda'] is not None else "N/D", "Categoria": "Solidez"},
                {"Métrica": "CAGR Receita (5 Anos)", "Valor": f"{data['revenue_cagr_5y']*100:.2f}%" if data['revenue_cagr_5y'] is not None else "N/D", "Categoria": "Crescimento"},
                {"Métrica": "CAGR Lucro Líquido (5 Anos)", "Valor": f"{data['net_income_cagr_5y']*100:.2f}%" if data['net_income_cagr_5y'] is not None else "N/D", "Categoria": "Crescimento"},
            ]
            df_summary = pd.DataFrame(table_data)
            st.dataframe(df_summary, use_container_width=True, hide_index=True)

else:
    st.info("👈 Introduza o código de uma ação na barra lateral para iniciar a análise fundamental de DGI.")
