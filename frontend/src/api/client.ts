const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000/api";

export interface TickerResult {
  cik: number;
  cik_str: string;
  ticker: string;
  title: string;
}

export interface CompanyProfile {
  ticker: string;
  cik: number;
  cik_str: string;
  name: string;
  sic: string;
  sic_description: string;
  exchanges: string[];
  ein: string;
  category: string;
  fiscal_year_end: string;
  address: {
    street1: string;
    street2: string | null;
    city: string;
    stateOrCountry: string;
    zipCode: string;
  } | null;
}

export interface AnnualRow {
  fiscal_year_end: string;
  fy: number;
  filed: string;
  form: string;
  metrics: Record<string, number | null>;
  derived: Record<string, number | null>;
}

export interface FinancialsResponse {
  ticker: string;
  entity_name: string;
  cik: number;
  annual: AnnualRow[];
  metric_sources: Record<string, string>;
  metric_units: Record<string, string>;
}

export interface PricePoint {
  t: number;
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number;
  volume: number | null;
}

export interface PriceHistory {
  symbol: string;
  currency: string;
  exchange: string;
  regular_market_price: number;
  fifty_two_week_high: number;
  fifty_two_week_low: number;
  points: PricePoint[];
}

export interface Filing {
  form: string;
  filing_date: string;
  report_date: string | null;
  accession_number: string;
  items: string[];
  document_url: string | null;
  filing_index_url: string;
}

export interface FilingHighlights {
  form: string;
  risk_factors?: string | null;
  mda?: string | null;
  items?: { code: string; description: string }[];
  excerpt?: string | null;
  note?: string;
}

export interface OwnershipData {
  ticker: string;
  quarter_end: string;
  holder_count_estimate: number;
  sample_holders: string[];
  method_note: string;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

export const api = {
  search: (q: string) => getJson<{ results: TickerResult[] }>(`/search?q=${encodeURIComponent(q)}`),
  companyProfile: (ticker: string) => getJson<CompanyProfile>(`/companies/${ticker}`),
  companyFinancials: (ticker: string) => getJson<FinancialsResponse>(`/companies/${ticker}/financials`),
  companyPrices: (ticker: string, range: string) => getJson<PriceHistory>(`/companies/${ticker}/prices?range=${range}`),
  companyFilings: (ticker: string, limit = 15, forms?: string[]) => {
    const formsParam = forms && forms.length ? `&forms=${encodeURIComponent(forms.join(","))}` : "";
    return getJson<{ filings: Filing[] }>(`/companies/${ticker}/filings?limit=${limit}${formsParam}`);
  },
  companyOwnership: (ticker: string) => getJson<OwnershipData>(`/companies/${ticker}/ownership`),
  filingHighlights: (documentUrl: string, form: string, items: string[]) => {
    const itemsParam = items.length ? `&items=${encodeURIComponent(items.join(","))}` : "";
    return getJson<FilingHighlights>(`/filings/highlights?url=${encodeURIComponent(documentUrl)}&form=${encodeURIComponent(form)}${itemsParam}`);
  },
};
