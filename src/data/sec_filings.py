"""
sec_filings.py
───────────────
Fetches a company's recent SEC filings from the EDGAR public API.

Why SEC filings matter for equity research:
  10-K (annual report) and 10-Q (quarterly report) are the primary
  sources of truth for a company's financial condition. Whether they
  arrive on time is itself a signal: late filings are a classic red flag.

How it works (two free, key-less EDGAR endpoints):
  1. https://www.sec.gov/files/company_tickers.json
     One file mapping every ticker to its CIK (Central Index Key),
     the ID EDGAR uses for a company. Downloaded once and kept in memory.
  2. https://data.sec.gov/submissions/CIK##########.json
     That company's own filing list, newest first.

  An earlier version used EDGAR's full-text SEARCH endpoint with the
  ticker as a search phrase. That returns any document that merely
  mentions the ticker, ranked by relevance rather than date, and it read
  field names that endpoint does not return. The result was decade-old
  filings from unrelated documents and a permanent "REVIEW_NEEDED".

Scope:
  US-listed companies only. Indian tickers (.NS / .BO) do not file with
  the SEC, so they return NOT_AVAILABLE rather than a false warning.
"""

import time
import threading
from datetime import date, datetime

import requests

EDGAR_HEADERS = {
    "User-Agent": "TradeDesk Research Agent research@tradedesk.ai",
    "Accept-Encoding": "gzip, deflate",
}
EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

# Annual / quarterly / material-event forms. Foreign companies listed in
# the US (e.g. TSM, BABA) file 20-F or 40-F annually and 6-K for interim
# news, and are not required to file quarterly reports at all.
_ANNUAL_FORMS = ("10-K", "20-F", "40-F")
_QUARTERLY_FORMS = ("10-Q",)
_EVENT_FORMS = ("8-K", "6-K")

# An annual report is due within about 60-90 days of year end, so the
# gap between two of them is roughly a year. These limits allow slack
# before calling a company late.
_MAX_ANNUAL_AGE_DAYS = 460
_MAX_QUARTERLY_AGE_DAYS = 200

_TICKER_MAP_TTL_SECONDS = 24 * 3600
_ticker_map: dict = {}
_ticker_map_loaded_at = 0.0
_ticker_map_lock = threading.Lock()


def _load_ticker_map() -> dict:
    """Ticker -> zero-padded 10-digit CIK, for every SEC registrant."""
    global _ticker_map, _ticker_map_loaded_at
    with _ticker_map_lock:
        if _ticker_map and (time.time() - _ticker_map_loaded_at) < _TICKER_MAP_TTL_SECONDS:
            return _ticker_map
        resp = requests.get(EDGAR_TICKERS_URL, headers=EDGAR_HEADERS, timeout=15)
        resp.raise_for_status()
        fresh = {
            str(row["ticker"]).upper(): str(row["cik_str"]).zfill(10)
            for row in resp.json().values()
        }
        if fresh:
            _ticker_map = fresh
            _ticker_map_loaded_at = time.time()
        return _ticker_map


def get_company_cik(ticker: str) -> str | None:
    """Look up a company's CIK from its ticker. None if not an SEC registrant."""
    try:
        return _load_ticker_map().get(ticker.upper().strip())
    except Exception:
        return None


def _is_us_ticker(ticker: str) -> bool:
    return not ticker.upper().endswith((".NS", ".BO"))


def get_recent_filings(ticker: str, filing_types: list = None) -> dict:
    """
    Fetch a company's recent SEC filings, newest first.

    Returns:
        {"ticker": str, "filings": [ {form_type, filed_date, description, url}, ... ],
         "error": str or None}
    """
    if filing_types is None:
        filing_types = list(_ANNUAL_FORMS + _QUARTERLY_FORMS + _EVENT_FORMS)

    try:
        cik = get_company_cik(ticker)
        if not cik:
            return {"ticker": ticker, "filings": [],
                    "error": f"{ticker} was not found in the SEC's company list"}

        resp = requests.get(EDGAR_SUBMISSIONS_URL.format(cik=cik),
                            headers=EDGAR_HEADERS, timeout=15)
        if resp.status_code != 200:
            return {"ticker": ticker, "filings": [],
                    "error": f"EDGAR API returned {resp.status_code}"}

        data = resp.json()
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        filed = recent.get("filingDate", [])
        period = recent.get("reportDate", [])
        accession = recent.get("accessionNumber", [])
        document = recent.get("primaryDocument", [])
        company_name = data.get("name", ticker)

        filings = []
        for i, form in enumerate(forms):
            if form not in filing_types:
                continue
            acc = accession[i] if i < len(accession) else ""
            doc = document[i] if i < len(document) else ""
            url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                   f"{acc.replace('-', '')}/{doc}") if acc and doc else ""
            filings.append({
                "form_type": form,
                "filed_date": filed[i] if i < len(filed) else "",
                "company_name": company_name,
                "description": period[i] if i < len(period) else "",  # period covered
                "url": url,
            })
        return {"ticker": ticker, "filings": filings, "error": None}

    except Exception as e:
        return {"ticker": ticker, "filings": [], "error": str(e)}


def _age_in_days(filing: dict | None) -> int | None:
    if not filing or not filing.get("filed_date"):
        return None
    try:
        filed = datetime.strptime(filing["filed_date"], "%Y-%m-%d").date()
        return (date.today() - filed).days
    except ValueError:
        return None


def get_filing_summary(ticker: str) -> dict:
    """
    A compact summary of recent filings for the Fundamentals agent:
    when the company last reported, how many material-event filings it
    made recently, and whether its reporting is on schedule.
    """
    if not _is_us_ticker(ticker):
        return {
            "ticker": ticker,
            "filing_regularity": "NOT_AVAILABLE",
            "note": "Not a US-listed company, so it does not file with the SEC. "
                    "This is expected and is not a negative signal.",
            "error": None,
        }

    try:
        result = get_recent_filings(ticker)
        filings = result.get("filings", [])
        if result.get("error") or not filings:
            return {
                "ticker": ticker,
                "filing_regularity": "NOT_AVAILABLE",
                "note": "SEC filing data could not be retrieved. Treat this as "
                        "missing data, not as a compliance concern.",
                "total_filings_found": 0,
                "error": result.get("error"),
            }

        annual = [f for f in filings if f["form_type"] in _ANNUAL_FORMS]
        quarterly = [f for f in filings if f["form_type"] in _QUARTERLY_FORMS]
        events = [f for f in filings if f["form_type"] in _EVENT_FORMS]

        annual_age = _age_in_days(annual[0] if annual else None)
        quarterly_age = _age_in_days(quarterly[0] if quarterly else None)
        foreign_filer = bool(annual) and annual[0]["form_type"] in ("20-F", "40-F")

        annual_ok = annual_age is not None and annual_age <= _MAX_ANNUAL_AGE_DAYS
        quarterly_ok = foreign_filer or (
            quarterly_age is not None and quarterly_age <= _MAX_QUARTERLY_AGE_DAYS
        )
        regularity = "ON_SCHEDULE" if (annual_ok and quarterly_ok) else "REVIEW_NEEDED"

        events_last_90d = [f for f in events if (_age_in_days(f) or 9999) <= 90]

        return {
            "ticker": ticker,
            "most_recent_annual_report": annual[0] if annual else None,
            "most_recent_quarterly_report": quarterly[0] if quarterly else None,
            "days_since_annual_report": annual_age,
            "days_since_quarterly_report": quarterly_age,
            "foreign_filer": foreign_filer,
            "material_event_filings_last_90_days": len(events_last_90d),
            "recent_event_dates": [f["filed_date"] for f in events[:5]],
            "total_filings_found": len(filings),
            # Older key names, kept so existing scripts keep working.
            "most_recent_10k": annual[0] if annual else None,
            "most_recent_10q": quarterly[0] if quarterly else None,
            "recent_8k_count": len(events),
            "filing_regularity": regularity,
            "raw_filings": filings[:5],
            "error": None,
        }
    except Exception as e:
        return {"ticker": ticker, "filing_regularity": "NOT_AVAILABLE", "error": str(e)}
