"""Extract meaningful excerpts from SEC filing documents.

SEC filing HTML has no machine-readable section markup, so this uses the
same technique any filing reader relies on: strip tags to plain text, then
find each "Item N." heading. The **first** occurrence in a 10-K/10-Q is
almost always the table of contents (a short line, immediately followed by
the next item's heading); the real section body is the **last** occurrence.
Validated by hand against a live Apple 10-K during development.
"""

from __future__ import annotations

import html as htmllib
import re

from app.config import settings
from app.core.http_cache import cached_get_text

ALLOWED_DOC_PREFIX = "https://www.sec.gov/Archives/edgar/"

# https://www.sec.gov/files/form8-k.pdf -- the full current Item list.
ITEM_8K_DESCRIPTIONS = {
    "1.01": "Entry into a Material Definitive Agreement",
    "1.02": "Termination of a Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership",
    "1.04": "Mine Safety – Reporting of Shutdowns and Patterns of Violations",
    "1.05": "Material Cybersecurity Incidents",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition",
    "2.03": "Creation of a Direct Financial Obligation",
    "2.04": "Triggering Events That Accelerate a Direct Financial Obligation",
    "2.05": "Costs Associated with Exit or Disposal Activities",
    "2.06": "Material Impairments",
    "3.01": "Notice of Delisting or Failure to Satisfy a Listing Rule",
    "3.02": "Unregistered Sales of Equity Securities",
    "3.03": "Material Modification to Rights of Security Holders",
    "4.01": "Changes in Registrant's Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financial Statements",
    "5.01": "Changes in Control of Registrant",
    "5.02": "Departure/Election of Directors or Officers; Compensatory Arrangements",
    "5.03": "Amendments to Articles of Incorporation or Bylaws",
    "5.04": "Temporary Suspension of Trading Under Employee Benefit Plans",
    "5.05": "Amendments to Code of Ethics",
    "5.06": "Change in Shell Company Status",
    "5.07": "Submission of Matters to a Vote of Security Holders",
    "5.08": "Shareholder Director Nominations",
    "6.01": "ABS Informational and Computational Material",
    "6.02": "Change of Servicer or Trustee",
    "6.03": "Change in Credit Enhancement or Other External Support",
    "6.04": "Failure to Make a Required Distribution",
    "6.05": "Securities Act Updating Disclosure",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}


def clean_html_to_text(raw_html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = htmllib.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _find_last(text: str, pattern: str) -> int | None:
    matches = list(re.finditer(pattern, text, re.IGNORECASE))
    return matches[-1].start() if matches else None


def _find_first(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text, re.IGNORECASE)
    return match.start() if match else None


def _trim_to_sentence(snippet: str, max_chars: int) -> str:
    if len(snippet) <= max_chars:
        return snippet.strip()
    cut = snippet[:max_chars]
    last_period = cut.rfind(". ")
    if last_period > max_chars * 0.5:
        cut = cut[: last_period + 1]
    return cut.strip() + "…"


def _extract_section(text: str, start_patterns: list[str], end_patterns: list[str], max_chars: int = 1600) -> str | None:
    start = None
    for pattern in start_patterns:
        start = _find_last(text, pattern)
        if start is not None:
            break
    if start is None:
        return None

    end = len(text)
    for pattern in end_patterns:
        candidate = _find_last(text[start + 50:], pattern)
        if candidate is not None:
            end = min(end, start + 50 + candidate)

    return _trim_to_sentence(text[start:end], max_chars)


def extract_10k_highlights(text: str) -> dict[str, str | None]:
    risk_factors = _extract_section(
        text,
        start_patterns=[r"Item\s+1A\.?\s*\n?\s*Risk Factors"],
        end_patterns=[r"Item\s+1B\.?\s*\n?\s*Unresolved Staff Comments", r"Item\s+2\.?\s*\n?\s*Properties"],
    )
    mda = _extract_section(
        text,
        start_patterns=[r"Item\s+7\.?\s*\n?\s*Management.s Discussion"],
        end_patterns=[r"Item\s+7A\.?\s*\n?\s*Quantitative", r"Item\s+8\.?\s*\n?\s*Financial Statements"],
    )
    return {"risk_factors": risk_factors, "mda": mda}


def extract_10q_highlights(text: str) -> dict[str, str | None]:
    mda = _extract_section(
        text,
        start_patterns=[r"Item\s+2\.?\s*\n?\s*Management.s Discussion"],
        end_patterns=[r"Item\s+3\.?\s*\n?\s*Quantitative", r"Item\s+4\.?\s*\n?\s*Controls"],
    )
    # Part II Item 1A risk-factor *updates* -- often "None" or a short delta
    # on top of the 10-K's full list, so cap it short.
    risk_factors = _extract_section(
        text,
        start_patterns=[r"Item\s+1A\.?\s*\n?\s*Risk Factors"],
        end_patterns=[r"Item\s+2\.?\s*\n?\s*Unregistered Sales", r"Item\s+3\.?\s*\n?\s*Defaults", r"Item\s+5\.?\s*\n?\s*Other Information", r"Item\s+6\.?\s*\n?\s*Exhibits"],
        max_chars=800,
    )
    return {"mda": mda, "risk_factors": risk_factors}


def extract_8k_summary(text: str, items: list[str]) -> dict:
    item_details = [{"code": code, "description": ITEM_8K_DESCRIPTIONS.get(code, "Other")} for code in items]

    # 8-Ks have no table of contents (unlike 10-K/10-Q), so the FIRST match of
    # the item heading is the real content -- a later match is more likely to
    # be boilerplate cross-referencing the item number in the signature block
    # or a deferred-disclosure clause.
    excerpt = None
    if items:
        first_code = re.escape(items[0])
        start = _find_first(text, rf"Item\s+{first_code}\b")
        if start is not None:
            end = len(text)
            sig_match = re.search(r"\n\s*SIGNATURE", text[start:], re.IGNORECASE)
            if sig_match:
                end = min(end, start + sig_match.start())
            if len(items) > 1:
                next_code = re.escape(items[1])
                next_match = re.search(rf"Item\s+{next_code}\b", text[start + 20:], re.IGNORECASE)
                if next_match:
                    end = min(end, start + 20 + next_match.start())
            excerpt = _trim_to_sentence(text[start:end], 1200)

    return {"items": item_details, "excerpt": excerpt}


def get_filing_highlights(document_url: str, form: str, items: list[str]) -> dict:
    if not document_url.startswith(ALLOWED_DOC_PREFIX):
        raise ValueError("document_url must be a sec.gov/Archives/edgar/ URL")

    raw_html = cached_get_text(
        namespace="filing_docs",
        url=document_url,
        ttl=7 * 24 * 3600,  # filings never change once filed
        headers={"User-Agent": settings.sec_user_agent},
    )
    text = clean_html_to_text(raw_html)

    base_form = form.replace("/A", "")
    if base_form == "10-K":
        return {"form": form, **extract_10k_highlights(text)}
    if base_form == "10-Q":
        return {"form": form, **extract_10q_highlights(text)}
    if base_form == "8-K":
        return {"form": form, **extract_8k_summary(text, items)}
    return {"form": form, "note": "Highlight extraction is only implemented for 10-K, 10-Q, and 8-K filings."}
