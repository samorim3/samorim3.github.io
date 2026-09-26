/**
 * Cloudflare Worker: Yahoo Finance Live Market & Fundamental API Gateway
 * Proxies live stock charts, price history, dividend distributions, institutional fundamental ratios,
 * ROIC (Return on Invested Capital), and 5-Year Share Count Evolution (Buybacks / Dilution).
 */

let cachedCookie = null;
let cachedCrumb = null;
let cachedCrumbTime = 0;

const USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

export default {
  async fetch(request, env, ctx) {
    const corsHeaders = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders });
    }

    const url = new URL(request.url);
    const ticker = (url.searchParams.get("ticker") || url.searchParams.get("symbol") || "").trim().toUpperCase();

    if (!ticker) {
      return new Response(JSON.stringify({ error: "Parâmetro 'ticker' em falta. Exemplo: /?ticker=MCD" }), {
        status: 400,
        headers: { ...corsHeaders, "Content-Type": "application/json" }
      });
    }

    // 1. Fetch Chart & Dividend History
    const chartUrl = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}?interval=1mo&range=5y&events=div`;
    let chartResult = null;
    let chartJson = null;

    try {
      const chartRes = await fetch(chartUrl, {
        headers: { "User-Agent": USER_AGENT }
      });
      if (chartRes.ok) {
        chartJson = await chartRes.json();
        chartResult = chartJson?.chart?.result?.[0];
      }
    } catch (err) {
      console.warn("Chart Fetch Error:", err);
    }

    // 2. Fetch Fundamentals & Timeseries in parallel via Yahoo Finance
    let fundamentals = null;
    try {
      const crumbInfo = await getCrumb();
      if (crumbInfo && crumbInfo.crumb && crumbInfo.cookie) {
        const modules = "financialData,defaultKeyStatistics,summaryDetail,incomeStatementHistory";
        const qsUrl = `https://query2.finance.yahoo.com/v10/finance/quoteSummary/${encodeURIComponent(ticker)}?modules=${modules}&crumb=${encodeURIComponent(crumbInfo.crumb)}`;
        
        const nowSec = Math.floor(Date.now() / 1000);
        const fiveYearsAgoSec = nowSec - (5 * 365 * 86400);
        const tsUrl = `https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/${encodeURIComponent(ticker)}?symbol=${encodeURIComponent(ticker)}&type=annualInvestedCapital,annualOperatingIncome,shares_out&period1=${fiveYearsAgoSec}&period2=${nowSec}&crumb=${encodeURIComponent(crumbInfo.crumb)}`;

        const [qsRes, tsRes] = await Promise.all([
          fetch(qsUrl, { headers: { "User-Agent": USER_AGENT, "Cookie": crumbInfo.cookie } }).catch(() => null),
          fetch(tsUrl, { headers: { "User-Agent": USER_AGENT, "Cookie": crumbInfo.cookie } }).catch(() => null)
        ]);

        let qsResult = null;
        if (qsRes && qsRes.ok) {
          const qsData = await qsRes.json();
          qsResult = qsData?.quoteSummary?.result?.[0];
        }

        let tsResult = null;
        if (tsRes && tsRes.ok) {
          const tsData = await tsRes.json();
          tsResult = tsData?.timeseries?.result;
        }

        if (qsResult) {
          fundamentals = parseFundamentals(qsResult, tsResult);
        }
      }
    } catch (err) {
      console.warn("Fundamentals Fetch Error:", err);
    }

    if (!chartResult && !fundamentals) {
      return new Response(JSON.stringify({ error: `Ticker ${ticker} não encontrado nos mercados globais.` }), {
        status: 404,
        headers: { ...corsHeaders, "Content-Type": "application/json" }
      });
    }

    // Attach fundamentals directly into the chart result object for seamless integration
    if (chartJson && chartJson.chart && chartJson.chart.result && chartJson.chart.result[0]) {
      chartJson.chart.result[0].fundamentals = fundamentals;
      return new Response(JSON.stringify(chartJson), {
        status: 200,
        headers: {
          ...corsHeaders,
          "Content-Type": "application/json",
          "Cache-Control": "public, max-age=120"
        }
      });
    }

    return new Response(JSON.stringify({ chart: chartJson, fundamentals: fundamentals }), {
      status: 200,
      headers: {
        ...corsHeaders,
        "Content-Type": "application/json",
        "Cache-Control": "public, max-age=120"
      }
    });
  }
};

/**
 * Retrieves valid session cookie and crumb from Yahoo Finance.
 * Caches in memory for 1 hour to maximize speed.
 */
async function getCrumb() {
  const now = Date.now();
  if (cachedCrumb && cachedCookie && (now - cachedCrumbTime) < 3600000) {
    return { cookie: cachedCookie, crumb: cachedCrumb };
  }

  try {
    const cookieRes = await fetch("https://fc.yahoo.com", {
      headers: { "User-Agent": USER_AGENT },
      redirect: "manual"
    });

    let setCookie = cookieRes.headers.get("set-cookie") || "";
    if (!setCookie) {
      const cookieResFollow = await fetch("https://fc.yahoo.com", {
        headers: { "User-Agent": USER_AGENT }
      });
      setCookie = cookieResFollow.headers.get("set-cookie") || "";
    }

    if (!setCookie) return null;

    const crumbRes = await fetch("https://query1.finance.yahoo.com/v1/test/getcrumb", {
      headers: {
        "User-Agent": USER_AGENT,
        "Cookie": setCookie
      }
    });

    if (crumbRes.ok) {
      const crumbText = (await crumbRes.text()).trim();
      if (crumbText && !crumbText.includes("<") && crumbText.length < 50) {
        cachedCookie = setCookie;
        cachedCrumb = crumbText;
        cachedCrumbTime = now;
        return { cookie: setCookie, crumb: crumbText };
      }
    }
  } catch (e) {
    console.warn("getCrumb Error:", e);
  }
  return null;
}

/**
 * Parses raw Yahoo Finance modules into clean institutional fundamental ratios
 */
function parseFundamentals(qs, tsResult) {
  const fd = qs.financialData || {};
  const ks = qs.defaultKeyStatistics || {};
  const sd = qs.summaryDetail || {};
  const inc = qs.incomeStatementHistory?.incomeStatementHistory || [];

  const raw = (obj) => (obj && typeof obj.raw === 'number') ? obj.raw : null;

  // 1. Valuation & Profitability
  const pe = raw(sd.trailingPE) || raw(ks.trailingPE) || raw(sd.forwardPE) || raw(ks.forwardPE);
  const forwardPe = raw(sd.forwardPE) || raw(ks.forwardPE);
  const roe = raw(fd.returnOnEquity) != null ? +(raw(fd.returnOnEquity) * 100).toFixed(2) : null;
  const margin = raw(fd.operatingMargins) != null ? +(raw(fd.operatingMargins) * 100).toFixed(2) : null;

  // 2. Solvency & Balance Sheet (Net Debt / EBITDA)
  const totalDebt = raw(fd.totalDebt);
  const totalCash = raw(fd.totalCash);
  const ebitda = raw(fd.ebitda);
  let netDebt = null;
  if (totalDebt != null && totalCash != null) {
    netDebt = totalDebt - totalCash;
  }
  let netDebtEbitda = null;
  if (ebitda != null && ebitda > 0 && netDebt != null) {
    netDebtEbitda = +(netDebt / ebitda).toFixed(2);
  } else if (netDebt != null && netDebt <= 0) {
    netDebtEbitda = 0.0; // Net cash company
  }

  // 3. Free Cash Flow & Payout
  const fcf = raw(fd.freeCashflow);
  const shares = raw(ks.sharesOutstanding) || raw(ks.impliedSharesOutstanding);
  const divRate = raw(sd.dividendRate) || raw(sd.trailingAnnualDividendRate);
  let fcfPayout = null;
  if (fcf != null && fcf > 0 && shares != null && divRate != null && divRate > 0) {
    const totalDivPaid = shares * divRate;
    fcfPayout = +((totalDivPaid / fcf) * 100).toFixed(1);
  } else if (raw(sd.payoutRatio) != null && raw(sd.payoutRatio) > 0) {
    fcfPayout = +(raw(sd.payoutRatio) * 100).toFixed(1);
  }

  const epsPayout = raw(sd.payoutRatio) != null ? +(raw(sd.payoutRatio) * 100).toFixed(1) : null;

  // 4. Revenue & Net Income CAGR (Croissance CA)
  let revCagr5y = null;
  let niCagr5y = null;

  if (inc.length >= 2) {
    const recent = inc[0];
    const oldest = inc[inc.length - 1];
    const years = inc.length - 1;

    const rRecent = raw(recent.totalRevenue);
    const rOld = raw(oldest.totalRevenue);
    if (rRecent != null && rOld != null && rRecent > 0 && rOld > 0 && years > 0) {
      revCagr5y = +((Math.pow(rRecent / rOld, 1 / years) - 1) * 100).toFixed(1);
    }

    const niRecent = raw(recent.netIncome);
    const niOld = raw(oldest.netIncome);
    if (niRecent != null && niOld != null && niRecent > 0 && niOld > 0 && years > 0) {
      niCagr5y = +((Math.pow(niRecent / niOld, 1 / years) - 1) * 100).toFixed(1);
    }
  }

  // 5. ROIC (Return on Invested Capital: NOPAT / Invested Capital)
  let roic = null;
  let annualOpInc = null;
  let annualInvCap = null;
  let sharesArr = null;

  if (Array.isArray(tsResult)) {
    for (const item of tsResult) {
      if (item.annualOperatingIncome && Array.isArray(item.annualOperatingIncome) && item.annualOperatingIncome.length > 0) {
        annualOpInc = item.annualOperatingIncome[item.annualOperatingIncome.length - 1]?.reportedValue?.raw;
      }
      if (item.annualInvestedCapital && Array.isArray(item.annualInvestedCapital) && item.annualInvestedCapital.length > 0) {
        annualInvCap = item.annualInvestedCapital[item.annualInvestedCapital.length - 1]?.reportedValue?.raw;
      }
      if (item.shares_out && Array.isArray(item.shares_out)) {
        sharesArr = item.shares_out;
      }
    }
  }

  let taxRate = 0.21;
  if (inc.length > 0) {
    const incTax = raw(inc[0].incomeTaxExpense);
    const incPreTax = raw(inc[0].incomeBeforeTax);
    if (incTax != null && incPreTax != null && incPreTax > 0) {
      const calculatedRate = incTax / incPreTax;
      if (calculatedRate >= 0 && calculatedRate <= 0.45) {
        taxRate = calculatedRate;
      }
    }
  }

  const opIncome = annualOpInc || (inc.length > 0 ? raw(inc[0].operatingIncome) : null);
  if (opIncome != null && annualInvCap != null && annualInvCap > 0) {
    const nopat = opIncome * (1 - taxRate);
    roic = +((nopat / annualInvCap) * 100).toFixed(1);
  }

  // 6. Share Count Evolution (5Y Share buybacks / Dilution)
  let sharesChange5y = null;
  let sharesCagr5y = null;
  if (sharesArr && sharesArr.length >= 2) {
    const first = sharesArr[0];
    const last = sharesArr[sharesArr.length - 1];
    if (first > 0 && last > 0) {
      sharesChange5y = +(((last - first) / first) * 100).toFixed(1);
      sharesCagr5y = +((Math.pow(last / first, 1 / 5) - 1) * 100).toFixed(1);
    }
  }

  // 7. Market Cap
  let mcap = null;
  const mcapRaw = raw(sd.marketCap);
  if (mcapRaw) {
    if (mcapRaw >= 1e12) mcap = (mcapRaw / 1e12).toFixed(2) + "T";
    else if (mcapRaw >= 1e9) mcap = (mcapRaw / 1e9).toFixed(2) + "B";
    else if (mcapRaw >= 1e6) mcap = (mcapRaw / 1e6).toFixed(2) + "M";
  }

  return {
    pe,
    forwardPe,
    roe,
    roic,
    margin,
    totalDebt,
    totalCash,
    netDebt,
    ebitda,
    netDebtEbitda,
    fcf,
    fcfPayout,
    epsPayout,
    revCagr5y,
    niCagr5y,
    sharesChange5y,
    sharesCagr5y,
    mcap
  };
}
