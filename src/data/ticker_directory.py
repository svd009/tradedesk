"""
ticker_directory.py
─────────────────────
A curated, static company-name -> ticker mapping, used to power the
"search by company name" feature for people who don't know the exact
ticker code.

Why static instead of a live search API?
  We already went through a real, painful lesson this session: Yahoo
  Finance can get rate-limited/blocked on shared cloud hosting, and every
  extra dependency on an external API is another thing that can silently
  break. A static list has zero network dependency, is instant, and
  covers the overwhelming majority of what people will actually search
  for (well-known large and mid-cap companies). It won't have every
  possible ticker, that's a deliberate tradeoff — coverage of the common
  case, with zero added fragility, rather than trying to cover everything
  and inheriting more of the exact reliability problems we just fixed.

This list can be extended over time — it's just a plain dict, add entries
as needed.
"""

COMPANY_TO_TICKER = {
    # ── US — Technology ──────────────────────────────────────────────
    "Apple": "AAPL",
    "Microsoft": "MSFT",
    "NVIDIA": "NVDA",
    "Alphabet (Google)": "GOOGL",
    "Amazon": "AMZN",
    "Meta Platforms (Facebook)": "META",
    "Tesla": "TSLA",
    "Netflix": "NFLX",
    "Adobe": "ADBE",
    "Salesforce": "CRM",
    "Oracle": "ORCL",
    "Intel": "INTC",
    "Advanced Micro Devices (AMD)": "AMD",
    "Qualcomm": "QCOM",
    "IBM": "IBM",
    "Cisco Systems": "CSCO",
    "Broadcom": "AVGO",
    "Uber Technologies": "UBER",
    "PayPal": "PYPL",
    "Palantir Technologies": "PLTR",
    "ServiceNow": "NOW",
    "Shopify": "SHOP",
    "Snowflake": "SNOW",
    "Micron Technology": "MU",
    "Texas Instruments": "TXN",

    # ── US — Finance ─────────────────────────────────────────────────
    "JPMorgan Chase": "JPM",
    "Bank of America": "BAC",
    "Wells Fargo": "WFC",
    "Goldman Sachs": "GS",
    "Morgan Stanley": "MS",
    "Visa": "V",
    "Mastercard": "MA",
    "American Express": "AXP",
    "Berkshire Hathaway": "BRK-B",

    # ── US — Consumer & Retail ───────────────────────────────────────
    "Walmart": "WMT",
    "Costco Wholesale": "COST",
    "The Coca-Cola Company": "KO",
    "PepsiCo": "PEP",
    "McDonald's": "MCD",
    "Starbucks": "SBUX",
    "Nike": "NKE",
    "Target": "TGT",
    "Home Depot": "HD",
    "Procter & Gamble": "PG",
    "Chipotle Mexican Grill": "CMG",

    # ── US — Healthcare ──────────────────────────────────────────────
    "Johnson & Johnson": "JNJ",
    "UnitedHealth Group": "UNH",
    "Pfizer": "PFE",
    "Eli Lilly": "LLY",
    "AbbVie": "ABBV",
    "Merck": "MRK",

    # ── US — Industrial, Energy, Auto ────────────────────────────────
    "Boeing": "BA",
    "Caterpillar": "CAT",
    "ExxonMobil": "XOM",
    "Chevron": "CVX",
    "Ford Motor Company": "F",
    "General Motors": "GM",
    "3M": "MMM",

    # ── US — Media & Communication ───────────────────────────────────
    "The Walt Disney Company": "DIS",
    "Comcast": "CMCSA",
    "Verizon Communications": "VZ",
    "AT&T": "T",

    # ── India (NSE) — Large Cap ──────────────────────────────────────
    "Reliance Industries": "RELIANCE.NS",
    "Tata Consultancy Services (TCS)": "TCS.NS",
    "HDFC Bank": "HDFCBANK.NS",
    "Infosys": "INFY.NS",
    "ICICI Bank": "ICICIBANK.NS",
    "State Bank of India (SBI)": "SBIN.NS",
    "Hindustan Unilever": "HINDUNILVR.NS",
    "ITC": "ITC.NS",
    "Bharti Airtel": "BHARTIARTL.NS",
    "Larsen & Toubro (L&T)": "LT.NS",
    "Kotak Mahindra Bank": "KOTAKBANK.NS",
    "Axis Bank": "AXISBANK.NS",
    "Bajaj Finance": "BAJFINANCE.NS",
    "Maruti Suzuki India": "MARUTI.NS",
    "Asian Paints": "ASIANPAINT.NS",
    "Wipro": "WIPRO.NS",
    "HCL Technologies": "HCLTECH.NS",
    "Sun Pharmaceutical Industries": "SUNPHARMA.NS",
    "Titan Company": "TITAN.NS",
    "UltraTech Cement": "ULTRACEMCO.NS",
    "Tata Motors": "TATAMOTORS.NS",
    "Tata Steel": "TATASTEEL.NS",
    "Mahindra & Mahindra": "M&M.NS",
    "Adani Enterprises": "ADANIENT.NS",
    "Adani Ports and SEZ": "ADANIPORTS.NS",
    "Nestle India": "NESTLEIND.NS",
    "Power Grid Corporation of India": "POWERGRID.NS",
    "NTPC": "NTPC.NS",
    "Coal India": "COALINDIA.NS",
    "IndusInd Bank": "INDUSINDBK.NS",
    "Bajaj Auto": "BAJAJ-AUTO.NS",
    "Grasim Industries": "GRASIM.NS",
    "Dr. Reddy's Laboratories": "DRREDDY.NS",
    "Cipla": "CIPLA.NS",
    "Eicher Motors": "EICHERMOT.NS",
    "Hero MotoCorp": "HEROMOTOCO.NS",
    "Britannia Industries": "BRITANNIA.NS",
    "Divi's Laboratories": "DIVISLAB.NS",
    "JSW Steel": "JSWSTEEL.NS",
    "Hindalco Industries": "HINDALCO.NS",
    "SBI Life Insurance": "SBILIFE.NS",
    "HDFC Life Insurance": "HDFCLIFE.NS",
    "Bharat Petroleum (BPCL)": "BPCL.NS",
    "Shree Cement": "SHREECEM.NS",
    "Tech Mahindra": "TECHM.NS",
    "Rico Auto Industries": "RICOAUTO.NS",
}


def search_companies(query: str, limit: int = 20) -> dict:
    """
    Case-insensitive substring search over the directory. Returns a dict
    of matching {company_name: ticker} pairs, company name shown first
    since that's what most people actually recognize.
    """
    if not query:
        return dict(list(COMPANY_TO_TICKER.items())[:limit])
    query_lower = query.lower()
    matches = {
        name: ticker for name, ticker in COMPANY_TO_TICKER.items()
        if query_lower in name.lower() or query_lower in ticker.lower()
    }
    return dict(list(matches.items())[:limit])
