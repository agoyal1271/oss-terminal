const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000/api";

export interface TickerResult {
  // null for ETFs resolved via Yahoo (not an SEC operating-company filer).
  cik: number | null;
  cik_str: string;
  ticker: string;
  title: string;
}

export interface CompanyProfile {
  ticker: string;
  // null for tickers resolved via Yahoo only (typically ETFs) -- not an
  // SEC operating-company filer, so no CIK exists. See sec_coverage.
  cik: number | null;
  cik_str: string | null;
  name: string;
  sic: string | null;
  sic_description: string;
  exchanges: string[];
  ein: string | null;
  category: string;
  fiscal_year_end: string | null;
  website?: string | null;
  investor_website?: string | null;
  address: {
    street1: string;
    street2: string | null;
    city: string;
    stateOrCountry: string;
    zipCode: string;
  } | null;
  sec_coverage: boolean;
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

export interface OptionContract {
  contract_symbol: string;
  strike: number;
  last_price: number | null;
  bid: number | null;
  ask: number | null;
  change: number | null;
  percent_change: number | null;
  volume: number;
  open_interest: number;
  implied_volatility: number | null;
  in_the_money: boolean;
}

export interface OptionsSummary {
  call_volume: number;
  put_volume: number;
  call_open_interest: number;
  put_open_interest: number;
  put_call_volume_ratio: number | null;
  put_call_oi_ratio: number | null;
  atm_strike: number | null;
  atm_call_iv: number | null;
  atm_put_iv: number | null;
  expected_move_atm_straddle: number | null;
}

export interface OptionsChain {
  symbol: string;
  underlying_price: number | null;
  expiration_dates: number[];
  selected_expiration: number;
  calls: OptionContract[];
  puts: OptionContract[];
  summary: OptionsSummary;
}

export interface TermStructurePoint {
  expiration: number;
  atm_strike: number | null;
  call_iv: number | null;
  put_iv: number | null;
}

export interface TermStructure {
  symbol: string;
  underlying_price: number | null;
  points: TermStructurePoint[];
}

export interface IvRank {
  ticker: string;
  history_days: number;
  current_iv: number | null;
  iv_rank: number | null;
  iv_percentile: number | null;
  note: string;
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
  companyOptions: (ticker: string, expiration?: number) => {
    const param = expiration ? `?expiration=${expiration}` : "";
    return getJson<OptionsChain>(`/companies/${ticker}/options${param}`);
  },
  companyOptionsTermStructure: (ticker: string) => getJson<TermStructure>(`/companies/${ticker}/options/term-structure`),
  companyOptionsIvRank: (ticker: string) => getJson<IvRank>(`/companies/${ticker}/options/iv-rank`),
};
