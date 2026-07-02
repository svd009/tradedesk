"""
sec_filings.py
───────────────
Fetches recent SEC filings from the EDGAR public API.

Why SEC filings matter for equity research:
  10-K (annual report) and 10-Q (quarterly report) are the primary
  sources of truth for a company's financial condition, risk factors,
  and management commentary. Analysts spend more time reading these
  than any other document. The EDGAR API is completely free and
  provides programmatic access to all filings.

What the Fundamentals Agent (SA2) uses from here:
  - Recent 10-K or 10-Q filing text for risk factors, MD&A section
  - Filing dates to understand reporting cadence
  - Any 8-K filings for material events (earnings, acquisitions, etc.)

EDGAR API — no key required:
  https://data.sec.gov/submissions/{cik}.json
  https://efts.sec.gov/LATEST/search-index?q={ticker}&dateRange=custom
"""

import requests
from config import SEC_FILING_CHARS

EDGAR_HEADERS = {
    "User-Agent": "TradeDesk Research Agent research@tradedesk.ai",
    "Accept-Encoding": "gzip, deflate",
}

EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
EDGAR_DOCUMENT_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{filename}"


def get_company_cik(ticker: str) -> str | None:
    """
    Look up a company's CIK (Central Index Key) number from its ticker.
    CIK is the unique identifier EDGAR uses for all companies.
    """
    try:
        url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=&CIK={ticker}&type=10-K&dateb=&owner=include&count=1&search_text=&output=atom"
        resp = requests.get(url, headers=EDGAR_HEADERS, timeout=10)
        # Extract CIK from response
        import re
        match = re.search(r"CIK=(\d+)", resp.text)
        if match:
            return match.group(1).zfill(10)
        return None
    except Exception:
        return None


def get_recent_filings(ticker: str, filing_types: list = None) -> dict:
    """
    Fetch recent SEC filings for a company.

    Args:
        ticker:       Stock ticker symbol
        filing_types: List of form types to fetch e.g. ["10-K", "10-Q", "8-K"]
                      Defaults to all three.

    Returns:
        {
          "ticker": str,
          "filings": list of filing metadata dicts,
          "error": str or None,
        }
    """
    if filing_types is None:
        filing_types = ["10-K", "10-Q", "8-K"]

    try:
        # Search EDGAR for recent filings
        params = {
            "q": f'"{ticker}"',
            "dateRange": "custom",
            "startdt": "2023-01-01",
            "forms": ",".join(filing_types),
        }
        resp = requests.get(
            "https://efts.sec.gov/LATEST/search-index",
            params=params,
            headers=EDGAR_HEADERS,
            timeout=15,
        )

        if resp.status_code != 200:
            return {"ticker": ticker, "filings": [], "error": f"EDGAR API returned {resp.status_code}"}

        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])

        filings = []
        for hit in hits[:8]:  # limit to 8 most recent
            src = hit.get("_source", {})
            filings.append({
                "form_type": src.get("form_type", ""),
                "filed_date": src.get("file_date", ""),
                "company_name": src.get("display_names", [ticker])[0] if src.get("display_names") else ticker,
                "description": src.get("period_of_report", ""),
                "url": f"https://www.sec.gov{src.get('file_num', '')}",
            })

        return {"ticker": ticker, "filings": filings, "error": None}

    except Exception as e:
        return {"ticker": ticker, "filings": [], "error": str(e)}


def get_filing_summary(ticker: str) -> dict:
    """
    Get a structured summary of recent SEC filings for an agent to reason over.

    Rather than fetching full filing text (which would blow the context window),
    we return structured metadata that gives the agent:
      - When the company last reported
      - How many recent 8-K material events were filed
      - Whether filings are on schedule (regulatory compliance signal)
    """
    try:
        result = get_recent_filings(ticker, ["10-K", "10-Q", "8-K"])
        filings = result.get("filings", [])

        annual_reports = [f for f in filings if f["form_type"] == "10-K"]
        quarterly_reports = [f for f in filings if f["form_type"] == "10-Q"]
        material_events = [f for f in filings if f["form_type"] == "8-K"]

        return {
            "ticker": ticker,
            "most_recent_10k": annual_reports[0] if annual_reports else None,
            "most_recent_10q": quarterly_reports[0] if quarterly_reports else None,
            "recent_8k_count": len(material_events),
            "recent_8k_dates": [f["filed_date"] for f in material_events[:5]],
            "total_filings_found": len(filings),
            "filing_regularity": "ON_SCHEDULE" if (annual_reports and quarterly_reports) else "REVIEW_NEEDED",
            "raw_filings": filings[:5],
            "error": result.get("error"),
        }

    except Exception as e:
        return {"ticker": ticker, "error": str(e)}
