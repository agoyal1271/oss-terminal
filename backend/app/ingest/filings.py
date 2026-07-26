"""Company profile + recent filings list via SEC's submissions API."""

from __future__ import annotations

from app.config import settings
from app.core.http_cache import cached_get_json

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

EDGAR_FILING_INDEX = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={form}"
EDGAR_DOC_BASE = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accn_nodash}/{doc}"


def fetch_submissions(cik_str: str) -> dict:
    url = SUBMISSIONS_URL.format(cik=cik_str)
    return cached_get_json(
        namespace="sec_submissions",
        url=url,
        ttl=settings.ttl_submissions,
        headers={"User-Agent": settings.sec_user_agent},
    )


def get_company_profile(cik_str: str) -> dict:
    data = fetch_submissions(cik_str)
    return {
        "name": data.get("name"),
        "cik": data.get("cik"),
        "sic": data.get("sic"),
        "sic_description": data.get("sicDescription"),
        "exchanges": data.get("exchanges", []),
        "tickers": data.get("tickers", []),
        "ein": data.get("ein"),
        "description": data.get("description"),
        "category": data.get("category"),
        "fiscal_year_end": data.get("fiscalYearEnd"),
        "website": data.get("website"),
        "investor_website": data.get("investorWebsite"),
        "address": (data.get("addresses") or {}).get("business"),
    }


def get_recent_filings(cik_str: str, limit: int = 25, forms: list[str] | None = None) -> list[dict]:
    data = fetch_submissions(cik_str)
    recent = data["filings"]["recent"]
    cik_int = int(cik_str)

    rows = []
    n = len(recent["form"])
    for i in range(n):
        form = recent["form"][i]
        if forms and form not in forms:
            continue
        accn = recent["accessionNumber"][i]
        accn_nodash = accn.replace("-", "")
        primary_doc = recent["primaryDocument"][i]
        rows.append({
            "form": form,
            "filing_date": recent["filingDate"][i],
            "report_date": recent["reportDate"][i] if i < len(recent["reportDate"]) else None,
            "accession_number": accn,
            "primary_document": primary_doc,
            "document_url": EDGAR_DOC_BASE.format(cik_int=cik_int, accn_nodash=accn_nodash, doc=primary_doc) if primary_doc else None,
            "filing_index_url": f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accn_nodash}/{accn}-index.htm",
        })
        if len(rows) >= limit:
            break
    return rows
